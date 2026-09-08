"""Unit tests for cache-key derivation and PDF page/chunk handling.

Cache correctness matters more than it looks: a key that ignores the
model or prompt would silently serve stale results after a prompt fix,
and a key that depended on fact ids rather than content would miss the
re-extraction case entirely.
"""
import pytest

from app.cache import hash_fact_pair, hash_text
from app.pdf_extract import chunk_page, parse_page_spec


class TestHashText:
    def test_deterministic(self):
        assert hash_text("a", "b") == hash_text("a", "b")

    def test_order_matters(self):
        assert hash_text("a", "b") != hash_text("b", "a")

    def test_field_boundaries_are_unambiguous(self):
        """Without a separator between parts, ('ab','c') and ('a','bc')
        would collide -- which would let two different prompts share a
        cache entry."""
        assert hash_text("ab", "c") != hash_text("a", "bc")

    def test_none_is_treated_as_empty(self):
        assert hash_text(None) == hash_text("")


class TestHashFactPair:
    def _fact(self, **over):
        base = {
            "subject": "Company", "attribute": "revenue", "value": "100",
            "unit": "INR million", "time_period": "FY24", "scope": "consolidated",
            "statement": "Company revenue FY24 was INR 100 million", "quote": "revenue ... 100",
        }
        base.update(over)
        return base

    def test_order_independent(self):
        """(A,B) and (B,A) are the same comparison and must share a cache
        entry."""
        a, b = self._fact(), self._fact(subject="Other", value="200")
        assert hash_fact_pair(a, b) == hash_fact_pair(b, a)

    def test_content_based_not_id_based(self):
        """Two facts with identical content but different db ids (the
        same claim re-extracted into a new document) must hash the same."""
        a = self._fact()
        a_copy = dict(a, id=999, document_id=42)
        b = self._fact(subject="Other")
        assert hash_fact_pair(a, b) == hash_fact_pair(a_copy, b)

    def test_content_change_changes_hash(self):
        a, b = self._fact(), self._fact(subject="Other")
        assert hash_fact_pair(a, b) != hash_fact_pair(self._fact(value="999"), b)


class TestParsePageSpec:
    @pytest.mark.parametrize("spec,expected", [
        ("1", {1}),
        ("1,3", {1, 3}),
        ("7-10", {7, 8, 9, 10}),
        ("1,3,7-10", {1, 3, 7, 8, 9, 10}),
        ("52,68", {52, 68}),
        (" 4 , 6 ", {4, 6}),
        ("5-5", {5}),
        ("0052", {52}),
    ])
    def test_selector_forms(self, spec, expected):
        assert parse_page_spec(spec) == expected

    def test_empty_segments_ignored(self):
        assert parse_page_spec("1,,3,") == {1, 3}


class TestChunkPage:
    def test_short_page_is_one_chunk(self):
        chunks = chunk_page(1, "short text")
        assert len(chunks) == 1
        assert chunks[0].page_number == 1
        assert chunks[0].text == "short text"

    def test_long_page_is_split_with_overlap(self):
        from app.config import MAX_CHUNK_CHARS
        text = "x" * (MAX_CHUNK_CHARS * 3)
        chunks = chunk_page(7, text)
        assert len(chunks) > 1
        assert all(c.page_number == 7 for c in chunks)
        # every chunk stays within the configured size
        assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)
        # reassembling covers the whole page (overlap means >= original)
        assert sum(len(c.text) for c in chunks) >= len(text)

    def test_page_number_preserved_on_every_chunk(self):
        from app.config import MAX_CHUNK_CHARS
        chunks = chunk_page(52, "y" * (MAX_CHUNK_CHARS * 2))
        assert {c.page_number for c in chunks} == {52}


class TestPageSpecValidation:
    """A malformed page selector must be rejected with an explanation, not
    crash deep inside int().

    Real failure: uploading with pages="24-24-24" passed the API, created a
    document row, queued a background job, and then died asynchronously
    with "invalid literal for int() with base 10: '24-24'". The upload
    returned a 500 and left a crashed job behind.
    """

    import pytest as _pytest

    @_pytest.mark.parametrize("spec", ["1,3,7-10", "7-10", "5", " 2 , 4 "])
    def test_valid_selectors_parse(self, spec):
        from app.pdf_extract import parse_page_spec
        assert parse_page_spec(spec)

    @_pytest.mark.parametrize("spec,fragment", [
        ("24-24-24", "start-end"),
        ("10-3", "backwards"),
        ("1-100000", "maximum selectable"),
        ("abc", "not a page number"),
        ("", "does not select any pages"),
        ("0", "page numbers start at 1"),
        ("-5", "not a valid page range"),
    ])
    def test_malformed_selectors_are_rejected_with_a_message(self, spec, fragment):
        from app.pdf_extract import parse_page_spec, PageSpecError
        with self._pytest.raises(PageSpecError) as exc:
            parse_page_spec(spec)
        assert fragment in str(exc.value)

    def test_error_is_a_valueerror_subclass(self):
        """Callers that already catch ValueError keep working."""
        from app.pdf_extract import PageSpecError
        assert issubclass(PageSpecError, ValueError)

    def test_ranges_are_inclusive_of_both_ends(self):
        from app.pdf_extract import parse_page_spec
        assert parse_page_spec("7-10") == {7, 8, 9, 10}


class TestPipelineFingerprintInvalidation:
    """Document-level dedup short-circuits a byte-identical re-upload,
    skipping ingestion entirely. That is only correct while the pipeline
    would produce the same result -- and that assumption broke for real:
    after PDF text extraction changed to reconstruct table pages, a
    re-upload returned the OLD facts because the bytes hadn't changed.
    """

    def test_fingerprint_is_stable_across_calls(self):
        from app.config import pipeline_fingerprint
        assert pipeline_fingerprint() == pipeline_fingerprint()

    def test_extraction_model_change_changes_the_fingerprint(self, monkeypatch):
        from app import config
        before = config.pipeline_fingerprint()
        monkeypatch.setattr(config, "EXTRACTION_MODEL", "some-other-model")
        assert config.pipeline_fingerprint() != before

    def test_chunk_size_change_changes_the_fingerprint(self, monkeypatch):
        from app import config
        before = config.pipeline_fingerprint()
        monkeypatch.setattr(config, "MAX_CHUNK_CHARS", config.MAX_CHUNK_CHARS + 1)
        assert config.pipeline_fingerprint() != before

    def test_pipeline_version_bump_changes_the_fingerprint(self, monkeypatch):
        from app import config
        before = config.pipeline_fingerprint()
        monkeypatch.setattr(config, "PIPELINE_VERSION", "999")
        assert config.pipeline_fingerprint() != before

    def test_unrelated_config_does_not_change_the_fingerprint(self, monkeypatch):
        """A timeout or concurrency edit must not force re-ingestion of
        every document -- those don't change what gets extracted."""
        from app import config
        before = config.pipeline_fingerprint()
        monkeypatch.setattr(config, "REASONING_TIMEOUT_SECONDS", 999)
        monkeypatch.setattr(config, "LLM_CONCURRENCY", 8)
        monkeypatch.setattr(config, "SIMILARITY_THRESHOLD", 0.99)
        assert config.pipeline_fingerprint() == before


class TestOrphanedJobRecovery:
    """Ingestion runs as a BackgroundTask inside the server process, so a
    restart abandons whatever was in flight. Those rows used to sit at
    "processing" forever, showing a progress bar that would never move."""

    def test_the_sweep_runs_only_once_per_process(self):
        """init_db() is also called from tests; sweeping on a later call
        would kill a job that is genuinely running -- exactly the failure
        it exists to clean up after."""
        from app import db
        assert db._ORPHAN_SWEEP_DONE is True, "init_db should have swept at import time"

        # A second call must be a no-op even with an in-flight document.
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO documents (original_name, stored_path, status, uploaded_at) "
                "VALUES ('live.pdf', '/tmp/live.pdf', 'processing', 0)"
            )
            live_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        try:
            db._fail_orphaned_jobs()
            assert db.get_document(live_id)["status"] == "processing"
        finally:
            with db.get_conn() as conn:
                conn.execute("DELETE FROM documents WHERE id = ?", (live_id,))
