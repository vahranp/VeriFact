"""Tests for user-requested cancellation of a running or queued document.

Cancellation is cooperative: a flag is checked between chunks and between
candidate-pair judgments, not inside a single LLM call -- there is no safe
way to abort a synchronous HTTP request from another thread without far
more machinery than a local prototype needs (see db.request_cancel). That
means exactly how much work completes after Stop is pressed is a race with
whatever a worker thread happens to be doing, and these tests are written
to respect that: they assert what must hold regardless of the race
(nothing crashes, completed work is kept, later stages are skipped), not
an exact "stopped after N of M" count that the design does not promise.

No real Ollama calls are made -- chat_json is mocked, exactly as in
test_relationships.py.
"""
import pytest

from app import db, pipeline
from app.pdf_extract import Chunk


# ---------------------------------------------------------------- helpers --

def _insert_document(status="processing"):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (original_name, stored_path, status, uploaded_at) "
            "VALUES ('t.pdf', '/tmp/t.pdf', ?, 0)", (status,),
        )
        return conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]


def _delete_document(doc_id):
    with db.get_conn() as conn:
        conn.execute(
            "DELETE FROM relationships WHERE fact_id_a IN (SELECT id FROM facts WHERE document_id = ?) "
            "OR fact_id_b IN (SELECT id FROM facts WHERE document_id = ?)", (doc_id, doc_id),
        )
        conn.execute("DELETE FROM facts WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM extraction_issues WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


# The dev DB this test suite runs against is not isolated -- it's the same
# persistent SQLite file used for manual runs, already holding real facts
# with real 384-dim (all-MiniLM-L6-v2) embeddings. build_relationships_for_
# document pools EVERY fact's embedding together (db.get_all_embeddings has
# no per-test scoping), so a fake vector of a different length breaks numpy's
# stacking across the whole pool, not just this test's own facts. Matching
# the real dimensionality avoids that, and biasing one axis away from the
# rest keeps our facts scoring highest among themselves regardless of
# whatever unrelated real data already exists in the database.
_EMBED_DIM = 384


def _vec():
    return [1.0] + [0.0] * (_EMBED_DIM - 1)


def _fact_dict(n):
    return {
        "subject": "TestCo", "attribute": "revenue", "value": str(n),
        "value_numeric": float(n), "unit": "INR million", "time_period": "FY24",
        "scope": None, "statement": f"TestCo revenue was {n} million in FY24.",
        "quote": f"TestCo revenue was {n} million in FY24.", "quote_grounded": True,
        "confidence": 0.9, "evidence_status": "fact_validated", "evidence_detail": "ok",
    }


def _insert_facts(doc_id, n):
    """n facts sharing an identical embedding vector, so every pair scores
    a perfect 1.0 cosine similarity and is reliably selected as a
    candidate regardless of the exact threshold config."""
    ids = []
    for i in range(n):
        ids.append(db.insert_fact(doc_id, 1, _fact_dict(i), _vec()))
    return ids


def _fake_corroborates(*_a, **_k):
    return {
        "same_metric": True, "relation_type": "corroborates",
        "explanation": "values agree", "confidence": 0.9,
    }


# ------------------------------------------------------------ db-level flag --

class TestRequestCancel:
    @pytest.mark.parametrize("status", ["pending", "processing"])
    def test_sets_the_flag_on_a_cancellable_document(self, status):
        doc_id = _insert_document(status)
        try:
            assert db.request_cancel(doc_id) is True
            assert db.is_cancel_requested(doc_id) is True
        finally:
            _delete_document(doc_id)

    @pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
    def test_does_nothing_on_a_terminal_document(self, status):
        """Nothing left to cancel -- must not resurrect a finished job."""
        doc_id = _insert_document(status)
        try:
            assert db.request_cancel(doc_id) is False
            assert db.is_cancel_requested(doc_id) is False
        finally:
            _delete_document(doc_id)

    def test_returns_false_for_a_nonexistent_document(self):
        assert db.request_cancel(999_999_999) is False

    def test_the_flag_is_false_by_default(self):
        doc_id = _insert_document("processing")
        try:
            assert db.is_cancel_requested(doc_id) is False
        finally:
            _delete_document(doc_id)


# --------------------------------------------------- comparison-stage cancel --

class TestComparisonStageCancellation:
    """build_relationships_for_document is the loop a Stop click interrupts
    when it arrives during the (often longest) candidate-comparison stage."""

    @pytest.fixture(autouse=True)
    def no_cache(self, monkeypatch):
        # Bypass the SQLite relationship cache so every pair actually goes
        # through the mocked chat_json instead of a previous run's answer.
        monkeypatch.setattr(db, "get_cached_relationship", lambda *a, **k: None)
        monkeypatch.setattr(db, "set_cached_relationship", lambda *a, **k: None)
        monkeypatch.setattr("app.relationships.chat_json", _fake_corroborates)

    def test_cancellation_before_any_pair_is_judged_stores_nothing(self, monkeypatch):
        """Fully deterministic case: the stop arrives before the loop even
        starts. Nothing has run yet, so nothing should be stored."""
        from app.relationships import build_relationships_for_document

        doc_id = _insert_document("processing")
        try:
            fact_ids = _insert_facts(doc_id, 4)
            monkeypatch.setattr(db, "is_cancel_requested", lambda _id: True)

            summary = build_relationships_for_document(doc_id, fact_ids)

            assert summary["cancelled"] is True
            assert summary["stored"] == 0
            assert summary["candidates_checked"] > 0, "candidates are still counted even when none run"
        finally:
            _delete_document(doc_id)

    def test_cancellation_mid_loop_keeps_completed_work_and_does_not_crash(self, monkeypatch):
        """The real regression this guards against: a cancelled run used to
        leave None entries in `results` for pairs that never started, and
        unpacking those crashed the whole comparison stage instead of
        cleanly stopping it."""
        from app.relationships import build_relationships_for_document

        doc_id = _insert_document("processing")
        try:
            fact_ids = _insert_facts(doc_id, 4)  # -> 6 unique candidate pairs

            calls = {"n": 0}

            def cancel_after_first(_id):
                calls["n"] += 1
                return calls["n"] > 1  # False once, True from then on

            monkeypatch.setattr(db, "is_cancel_requested", cancel_after_first)

            summary = build_relationships_for_document(doc_id, fact_ids)

            assert summary["cancelled"] is True
            # The pair that completed before cancellation was noticed is
            # real LLM work (mocked here, but the code path is the same)
            # and must be kept, not discarded.
            assert summary["stored"] >= 1
            assert summary["errors"] == 0
        finally:
            _delete_document(doc_id)

    def test_no_cancellation_requested_runs_to_completion(self, monkeypatch):
        """Sanity check that the cancellation checks don't interfere with
        an ordinary, uninterrupted run."""
        from app.relationships import build_relationships_for_document

        doc_id = _insert_document("processing")
        try:
            fact_ids = _insert_facts(doc_id, 4)
            monkeypatch.setattr(db, "is_cancel_requested", lambda _id: False)

            summary = build_relationships_for_document(doc_id, fact_ids)

            assert summary["cancelled"] is False
            assert summary["stored"] == summary["candidates_checked"]
        finally:
            _delete_document(doc_id)


# ------------------------------------------------------- extraction-stage --

class TestExtractionStageCancellation:
    """process_document is the top-level orchestrator; this exercises the
    branch that skips arithmetic/relationships/coherence entirely when the
    stop arrives during fact extraction, the earliest and usually longest
    stage on a large document."""

    def test_cancelling_during_extraction_skips_later_stages(self, monkeypatch):
        doc_id = _insert_document("pending")
        try:
            chunks = [Chunk(page_number=i + 1, text=f"page {i} text") for i in range(3)]
            monkeypatch.setattr(pipeline, "page_count", lambda _path: 3)
            monkeypatch.setattr(pipeline, "extract_chunks", lambda _path: chunks)

            def fake_extract(chunk):
                # Every chunk requests a stop as its first action --
                # guarantees is_cancel_requested reads True as soon as any
                # chunk completes, without depending on which one wins the
                # race to finish first.
                db.request_cancel(doc_id)
                fact = _fact_dict(chunk.page_number)
                return [fact], [], False

            monkeypatch.setattr(pipeline, "extract_facts_from_chunk", fake_extract)
            monkeypatch.setattr(pipeline, "embed", lambda texts: [_vec() for _ in texts])

            def fail_if_called(*_a, **_k):
                raise AssertionError("later stage ran despite the extraction-stage cancellation")

            monkeypatch.setattr(pipeline, "check_arithmetic_consistency", fail_if_called)
            monkeypatch.setattr(pipeline, "build_relationships_for_document", fail_if_called)
            monkeypatch.setattr(pipeline, "check_coherence", fail_if_called)

            pipeline.process_document(doc_id, "/tmp/fake.pdf")

            doc = db.get_document(doc_id)
            assert doc["status"] == "cancelled"
            assert "extraction" in doc["error_message"]
            assert "cached" in doc["error_message"], "should point at how to resume cheaply"

            # Whatever chunk(s) completed before the stop was noticed had
            # their facts saved -- real extraction work is never discarded.
            saved = db.list_facts(doc_id)
            assert len(saved) >= 1
        finally:
            _delete_document(doc_id)

    def test_cancelling_before_processing_begins_never_reaches_extraction(self, monkeypatch):
        doc_id = _insert_document("pending")
        try:
            db.request_cancel(doc_id)

            def fail_if_called(*_a, **_k):
                raise AssertionError("extraction started despite an already-pending cancellation")

            monkeypatch.setattr(pipeline, "page_count", fail_if_called)
            monkeypatch.setattr(pipeline, "extract_chunks", fail_if_called)

            pipeline.process_document(doc_id, "/tmp/fake.pdf")

            doc = db.get_document(doc_id)
            assert doc["status"] == "cancelled"
            assert "before" in doc["error_message"].lower()
        finally:
            _delete_document(doc_id)

    def test_an_uninterrupted_run_still_produces_status_done(self, monkeypatch):
        """Sanity check: the cancellation plumbing must not change the
        outcome of a normal run that nobody stopped."""
        doc_id = _insert_document("pending")
        try:
            chunks = [Chunk(page_number=1, text="page text")]
            monkeypatch.setattr(pipeline, "page_count", lambda _path: 1)
            monkeypatch.setattr(pipeline, "extract_chunks", lambda _path: chunks)
            monkeypatch.setattr(
                pipeline, "extract_facts_from_chunk",
                lambda chunk: ([_fact_dict(1)], [], False),
            )
            monkeypatch.setattr(pipeline, "embed", lambda texts: [_vec() for _ in texts])

            pipeline.process_document(doc_id, "/tmp/fake.pdf")

            doc = db.get_document(doc_id)
            assert doc["status"] == "done"
        finally:
            _delete_document(doc_id)
