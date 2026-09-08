"""API contract tests.

Runs against a TestClient with no Ollama and no network, so these assert
the parts of the API that must hold regardless of whether a model is
available: validation, status codes, error shape, and the documented
response schema.

Ingestion itself needs a model and is not exercised here -- what is
exercised is that a bad request is rejected *before* anything is written,
which is the property that was actually broken (a malformed page selector
created a document row, queued a job, and returned a 500).
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _fake_pdf(content=b"%PDF-1.4 not really a pdf"):
    return {"file": ("test.pdf", io.BytesIO(content), "application/pdf")}


class TestReadEndpointsRespond:
    @pytest.mark.parametrize("path", [
        "/api/documents", "/api/facts", "/api/relationships",
        "/api/issues", "/api/stats", "/api/coherence",
    ])
    def test_endpoint_returns_200(self, client, path):
        assert client.get(path).status_code == 200

    def test_openapi_schema_is_generated(self, client):
        """The response models exist so /docs describes the API."""
        schema = client.get("/openapi.json").json()
        assert "/api/facts" in schema["paths"]
        assert "FactOut" in schema["components"]["schemas"]

    def test_stats_reports_both_grounding_levels(self, client):
        stats = client.get("/api/stats").json()
        assert "fact_validated" in stats and "quote_grounded_only" in stats


class TestQueryValidation:
    def test_an_unrecognised_relation_type_is_rejected(self, client):
        """Previously a typo silently returned an empty list, which reads
        as 'no such relationships' rather than 'no such filter'."""
        r = client.get("/api/relationships", params={"relation_type": "corroberates"})
        assert r.status_code == 422

    @pytest.mark.parametrize("relation", ["corroborates", "contradicts", "reconciled", "uncertain"])
    def test_valid_relation_types_are_accepted(self, client, relation):
        assert client.get("/api/relationships", params={"relation_type": relation}).status_code == 200

    def test_a_non_positive_document_id_is_rejected(self, client):
        assert client.get("/api/facts", params={"document_id": 0}).status_code == 422

    def test_a_non_integer_path_id_is_rejected(self, client):
        assert client.get("/api/documents/not-a-number").status_code == 422


class TestNotFound:
    def test_a_missing_document_is_404_not_500(self, client):
        r = client.get("/api/documents/99999999")
        assert r.status_code == 404
        assert "detail" in r.json()

    def test_a_missing_fact_is_404(self, client):
        assert client.get("/api/facts/99999999").status_code == 404

    def test_a_missing_pdf_is_404(self, client):
        assert client.get("/api/documents/99999999/pdf").status_code == 404


class TestUploadValidation:
    """Every one of these must be rejected before a file is written, a
    document row created, or a background job queued."""

    def test_a_non_pdf_filename_is_rejected(self, client):
        r = client.post("/api/documents", files={"file": ("notes.txt", io.BytesIO(b"hi"), "text/plain")})
        assert r.status_code == 400
        assert "PDF" in r.json()["detail"]

    def test_a_missing_file_is_rejected(self, client):
        assert client.post("/api/documents").status_code == 422

    def test_a_corrupt_pdf_is_rejected_with_400(self, client):
        r = client.post("/api/documents", files=_fake_pdf(b"this is not a pdf at all"))
        assert r.status_code == 400
        assert "corrupt" in r.json()["detail"].lower()

    def test_a_password_protected_pdf_is_rejected_with_400(self, client, tmp_path):
        """The real gap this closes: page_count() succeeds on an encrypted
        PDF without a password (page count is metadata, often readable
        unauthenticated), so this used to pass validation, start a
        background job, and fail later with a raw traceback the first
        time something tried to actually read a page."""
        import fitz
        path = str(tmp_path / "encrypted.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "secret content")
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw123", owner_pw="pw123")
        doc.close()

        with open(path, "rb") as f:
            r = client.post("/api/documents", files={"file": ("encrypted.pdf", f, "application/pdf")})
        assert r.status_code == 400
        assert "password" in r.json()["detail"].lower()

    def test_a_password_protected_upload_creates_no_document_row(self, client, tmp_path):
        import fitz
        path = str(tmp_path / "encrypted2.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "secret content")
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw123", owner_pw="pw123")
        doc.close()

        before = len(client.get("/api/documents").json())
        with open(path, "rb") as f:
            client.post("/api/documents", files={"file": ("encrypted2.pdf", f, "application/pdf")})
        assert len(client.get("/api/documents").json()) == before

    def test_a_scanned_pdf_with_no_text_layer_is_rejected_with_400(self, client, tmp_path):
        """The other half of the same class of gap: a scanned PDF passes
        every existing check (real page count, not encrypted) and used to
        run the whole pipeline to completion with zero facts -- a quiet
        failure dressed up as success."""
        import fitz
        path = str(tmp_path / "scanned.pdf")
        doc = fitz.open()
        doc.new_page()  # no text layer
        doc.save(path)
        doc.close()

        with open(path, "rb") as f:
            r = client.post("/api/documents", files={"file": ("scanned.pdf", f, "application/pdf")})
        assert r.status_code == 400
        assert "extractable text" in r.json()["detail"].lower()

    @pytest.mark.parametrize("spec,fragment", [
        ("24-24-24", "start-end"),
        ("10-3", "backwards"),
        ("abc", "not a page number"),
        ("0", "start at 1"),
    ])
    def test_a_malformed_page_selector_is_rejected_with_400(self, client, spec, fragment):
        """The real failure this guards: pages=24-24-24 passed validation,
        created a row, queued a job, and died asynchronously with an
        opaque int() error while returning a 500."""
        r = client.post("/api/documents", params={"pages": spec}, files=_fake_pdf())
        assert r.status_code == 400
        assert fragment in r.json()["detail"]

    def test_a_bad_selector_is_rejected_before_any_document_is_created(self, client):
        before = len(client.get("/api/documents").json())
        client.post("/api/documents", params={"pages": "24-24-24"}, files=_fake_pdf())
        assert len(client.get("/api/documents").json()) == before

    def test_a_non_positive_max_pages_is_rejected(self, client):
        r = client.post("/api/documents", params={"max_pages": 0}, files=_fake_pdf())
        assert r.status_code == 400


class TestErrorsAreClean:
    def test_no_stack_trace_leaks_in_an_error_body(self, client):
        r = client.get("/api/documents/99999999")
        body = r.text
        assert "Traceback" not in body
        assert "superjoin-fact-layer" not in body

    def test_error_bodies_are_json_with_a_detail_field(self, client):
        r = client.post("/api/documents", files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")})
        assert r.headers["content-type"].startswith("application/json")
        assert isinstance(r.json()["detail"], str)
