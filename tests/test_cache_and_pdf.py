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
    "processing" forever, showing a progress bar that would never move.

    Two earlier versions of this fix each killed a live job. Calling the
    sweep from init_db() meant any script touching the database swept.
    A once-per-process guard did not help either -- running this very test
    suite starts a FastAPI TestClient, which fires the startup hook, which
    swept the running server's work. A guard cannot see other processes.

    Staleness is the only signal correct across processes: a live job
    writes progress after every chunk, an abandoned one never writes again.
    """

    def _insert(self, status, progress_at=None, uploaded_at=None):
        import json, time
        from app import db
        progress = json.dumps({"stage": "extracting", "current": 1, "total": 4,
                               "detail": None, "at": progress_at}) if progress_at else None
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO documents (original_name, stored_path, status, uploaded_at, progress_json) "
                "VALUES ('t.pdf', '/tmp/t.pdf', ?, ?, ?)",
                (status, uploaded_at if uploaded_at is not None else time.time(), progress),
            )
            return conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    def _cleanup(self, doc_id):
        from app import db
        with db.get_conn() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def test_a_job_that_just_reported_progress_is_left_alone(self):
        """The property that broke twice: a running job must survive a
        sweep triggered from any other process."""
        import time
        from app import db
        doc_id = self._insert("processing", progress_at=time.time())
        try:
            db.fail_orphaned_jobs()
            assert db.get_document(doc_id)["status"] == "processing"
        finally:
            self._cleanup(doc_id)

    def test_a_stale_job_is_failed(self):
        import time
        from app import db
        stale = time.time() - db.STALE_JOB_SECONDS - 60
        doc_id = self._insert("processing", progress_at=stale, uploaded_at=stale)
        try:
            db.fail_orphaned_jobs()
            row = db.get_document(doc_id)
            assert row["status"] == "failed"
            assert "Interrupted" in row["error_message"]
        finally:
            self._cleanup(doc_id)

    def test_a_pending_job_with_no_progress_yet_is_judged_on_upload_time(self):
        """A job queued seconds ago has written no progress -- that is not
        evidence it is dead."""
        import time
        from app import db
        fresh = self._insert("pending", uploaded_at=time.time())
        old = self._insert("pending", uploaded_at=time.time() - db.STALE_JOB_SECONDS - 60)
        try:
            db.fail_orphaned_jobs()
            assert db.get_document(fresh)["status"] == "pending"
            assert db.get_document(old)["status"] == "failed"
        finally:
            self._cleanup(fresh)
            self._cleanup(old)

    def test_init_db_does_not_sweep(self):
        import time
        from app import db
        doc_id = self._insert("processing", progress_at=time.time() - db.STALE_JOB_SECONDS - 60)
        try:
            db.init_db()
            assert db.get_document(doc_id)["status"] == "processing"
        finally:
            self._cleanup(doc_id)

    def test_progress_writes_a_timestamp(self):
        """Staleness detection depends on it."""
        import json
        from app import db
        doc_id = self._insert("processing")
        try:
            db.set_progress(doc_id, "extracting", 1, 4, None)
            progress = json.loads(db.get_document(doc_id)["progress_json"])
            assert progress["at"] > 0
        finally:
            self._cleanup(doc_id)


class TestEncryptedPdfHandling:
    """A password-protected PDF used to sail straight through upload
    validation -- page_count() succeeds on one without a password, since
    page count is metadata that's often readable unauthenticated -- and
    only failed later, inside the background job, the first time
    something actually tried to read a page: a raw
    "ValueError: document closed or encrypted" surfacing as a stack trace
    on the document instead of a clear message.
    """

    @pytest.fixture
    def encrypted_pdf(self, tmp_path):
        import fitz
        path = str(tmp_path / "encrypted.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "secret content")
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw123", owner_pw="pw123")
        doc.close()
        return path

    @pytest.fixture
    def plain_pdf(self, tmp_path):
        import fitz
        path = str(tmp_path / "plain.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "ordinary unprotected content well past the minimum page length")
        doc.save(path)
        doc.close()
        return path

    def test_is_encrypted_detects_a_password_protected_file(self, encrypted_pdf):
        from app.pdf_extract import is_encrypted
        assert is_encrypted(encrypted_pdf) is True

    def test_is_encrypted_is_false_for_an_ordinary_pdf(self, plain_pdf):
        from app.pdf_extract import is_encrypted
        assert is_encrypted(plain_pdf) is False

    def test_page_count_alone_does_not_reveal_encryption(self, encrypted_pdf):
        """The property that let this slip past upload validation in the
        first place: page count succeeds without a password."""
        from app.pdf_extract import page_count
        assert page_count(encrypted_pdf) == 1

    def test_extracting_an_encrypted_pdf_degrades_to_zero_chunks_not_a_crash(self, encrypted_pdf):
        from app.pdf_extract import extract_chunks
        assert extract_chunks(encrypted_pdf) == []

    def test_an_ordinary_pdf_still_extracts_normally(self, plain_pdf):
        from app.pdf_extract import extract_chunks
        chunks = extract_chunks(plain_pdf)
        assert any("ordinary unprotected content" in c.text for c in chunks)


class TestOnePageFailureDoesNotSinkTheDocument:
    """The same principle already applied to per-chunk LLM failures,
    extended one layer earlier: a real PDF can have one corrupted page
    object among hundreds of good ones, and that single page must not
    discard every other page in the document."""

    def test_a_page_that_raises_on_read_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        import fitz
        from app import pdf_extract

        path = str(tmp_path / "mixed.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "first good page has real content on it, well past the minimum length")
        doc.new_page().insert_text((72, 72), "second good page also has real content, well past the minimum length")
        doc.save(path)
        doc.close()

        real_layout_aware_text = pdf_extract.layout_aware_text
        calls = {"n": 0}

        def flaky(page):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("simulated: document closed or encrypted")
            return real_layout_aware_text(page)

        monkeypatch.setattr(pdf_extract, "layout_aware_text", flaky)

        pages = pdf_extract.extract_pages(path)
        assert len(pages) == 1, "the failing page is skipped, the good one is kept"
        assert "second good page" in pages[0][1]


class TestScannedPdfHandling:
    """A scanned/image-only PDF -- no embedded text layer -- passes every
    other upload check (a real page count, not encrypted) and would
    otherwise run the whole pipeline and complete "done" with zero facts,
    looking exactly like a quiet failure instead of the honest, expected
    outcome for a document this system cannot read."""

    @pytest.fixture
    def scanned_pdf(self, tmp_path):
        import fitz
        path = str(tmp_path / "scanned.pdf")
        doc = fitz.open()
        for _ in range(3):
            doc.new_page()  # no insert_text -- no text layer at all
        doc.save(path)
        doc.close()
        return path

    @pytest.fixture
    def real_pdf(self, tmp_path):
        import fitz
        path = str(tmp_path / "real.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "This page has a real embedded text layer, unlike a scan.")
        doc.save(path)
        doc.close()
        return path

    def test_a_scanned_pdf_has_no_extractable_text(self, scanned_pdf):
        from app.pdf_extract import has_extractable_text
        assert has_extractable_text(scanned_pdf) is False

    def test_a_scanned_pdf_still_reports_a_real_page_count(self, scanned_pdf):
        """The property that let this slip past validation before: page
        count succeeds even with zero extractable text."""
        from app.pdf_extract import page_count
        assert page_count(scanned_pdf) == 3

    def test_a_real_pdf_has_extractable_text(self, real_pdf):
        from app.pdf_extract import has_extractable_text
        assert has_extractable_text(real_pdf) is True
