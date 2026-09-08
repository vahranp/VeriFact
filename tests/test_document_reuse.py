"""Tests for a real bug found during review: a document reused via
content-hash dedup (see db.find_done_document_by_hash / the upload
short-circuit in app/main.py) owns no fact rows of its own -- its facts
live under the ORIGINAL document_id, reachable only through
reused_from_document_id. GET /api/documents/{id} already read through
that pointer; /api/facts, /api/priority and /api/timelines did not, so
each one silently returned an empty result for a document the UI reports
as "done" with a positive fact_count.

Runs against the real (non-isolated) dev DB, like tests/test_cancellation.py
-- inserts and explicitly cleans up its own rows rather than assuming an
isolated fixture database.
"""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


def _insert_document(reused_from_document_id=None):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (original_name, stored_path, status, uploaded_at, "
            "reused_from_document_id) VALUES ('t.pdf', '/tmp/t.pdf', 'done', 0, ?)",
            (reused_from_document_id,),
        )
        return conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]


def _delete_document(doc_id):
    with db.get_conn() as conn:
        conn.execute(
            "DELETE FROM relationships WHERE fact_id_a IN (SELECT id FROM facts WHERE document_id = ?) "
            "OR fact_id_b IN (SELECT id FROM facts WHERE document_id = ?)", (doc_id, doc_id),
        )
        conn.execute("DELETE FROM facts WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


@pytest.fixture
def source_and_reused_document():
    source_id = _insert_document()
    fact = {
        "subject": "Test Co", "attribute": "revenue", "value": "100", "value_numeric": 100.0,
        "unit": "INR million", "time_period": "FY24", "scope": None,
        "statement": "Test Co revenue was 100 million in FY24.", "quote": "revenue was 100 million",
        "quote_grounded": True, "confidence": 0.9,
        "evidence_status": "fact_validated", "evidence_detail": "ok",
    }
    fact_id = db.insert_fact(source_id, 1, fact, embedding=[0.1, 0.2])
    reused_id = _insert_document(reused_from_document_id=source_id)
    try:
        yield source_id, reused_id, fact_id
    finally:
        _delete_document(source_id)
        _delete_document(reused_id)


class TestResolveDocumentId:
    def test_none_passes_through(self):
        assert db.resolve_document_id(None) is None

    def test_a_document_with_no_reuse_pointer_resolves_to_itself(self, source_and_reused_document):
        source_id, _reused_id, _fact_id = source_and_reused_document
        assert db.resolve_document_id(source_id) == source_id

    def test_a_reused_document_resolves_to_its_source(self, source_and_reused_document):
        source_id, reused_id, _fact_id = source_and_reused_document
        assert db.resolve_document_id(reused_id) == source_id

    def test_a_nonexistent_document_id_passes_through(self):
        assert db.resolve_document_id(999_999_999) == 999_999_999


class TestReusedDocumentEndpointsSeeTheSourcesFacts:
    """The actual bug: each of these returned an empty result for a
    reused document before resolve_document_id was wired in."""

    @pytest.fixture(scope="class")
    def client(self):
        with TestClient(app) as c:
            yield c

    def test_facts_endpoint_resolves_through_reuse(self, client, source_and_reused_document):
        _source_id, reused_id, fact_id = source_and_reused_document
        resp = client.get(f"/api/facts?document_id={reused_id}")
        assert resp.status_code == 200
        assert any(f["id"] == fact_id for f in resp.json())

    def test_priority_endpoint_resolves_through_reuse(self, client, source_and_reused_document):
        _source_id, reused_id, fact_id = source_and_reused_document
        resp = client.get(f"/api/priority?document_id={reused_id}")
        assert resp.status_code == 200
        assert any(f["id"] == fact_id for f in resp.json()["facts"])

    def test_timelines_endpoint_does_not_error_on_a_reused_document(self, client, source_and_reused_document):
        """A single fact can't form a timeline (needs >=2 chained points),
        so this only asserts the resolved document_id is accepted cleanly
        -- the meaningful regression coverage is the 404 NOT firing for a
        document that genuinely exists via its reuse pointer."""
        _source_id, reused_id, _fact_id = source_and_reused_document
        resp = client.get(f"/api/timelines?document_id={reused_id}")
        assert resp.status_code == 200
