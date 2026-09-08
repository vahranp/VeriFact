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
        "/api/priority", "/api/timelines",
    ])
    def test_endpoint_returns_200(self, client, path):
        assert client.get(path).status_code == 200

    def test_openapi_schema_is_generated(self, client):
        """The response models exist so /docs describes the API."""
        schema = client.get("/openapi.json").json()
        assert "/api/facts" in schema["paths"]
        assert "FactOut" in schema["components"]["schemas"]


class TestPriorityEndpoint:
    def test_response_shape(self, client):
        body = client.get("/api/priority").json()
        assert "facts" in body and "relationships" in body and "level_counts" in body

    def test_facts_are_sorted_most_urgent_first(self, client):
        facts = client.get("/api/priority").json()["facts"]
        levels = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        ordered = [levels[f["priority_level"]] for f in facts]
        assert ordered == sorted(ordered)

    def test_every_ranked_fact_carries_reasons(self, client):
        facts = client.get("/api/priority").json()["facts"]
        assert all(f["priority_reasons"] for f in facts)

    def test_a_limit_is_respected(self, client):
        body = client.get("/api/priority", params={"limit": 3}).json()
        assert len(body["facts"]) <= 3
        assert len(body["relationships"]) <= 3

    def test_scoping_to_a_nonexistent_document_is_404(self, client):
        assert client.get("/api/priority", params={"document_id": 99999999}).status_code == 404

    def test_an_invalid_document_id_is_422(self, client):
        assert client.get("/api/priority", params={"document_id": 0}).status_code == 422


class TestTimelinesEndpoint:
    def test_response_shape(self, client):
        body = client.get("/api/timelines").json()
        assert "timelines" in body and "count" in body
        assert body["count"] == len(body["timelines"])

    def test_every_timeline_has_at_least_two_points(self, client):
        for t in client.get("/api/timelines").json()["timelines"]:
            assert len(t["points"]) >= 2

    def test_points_carry_original_and_normalized_values(self, client):
        timelines = client.get("/api/timelines").json()["timelines"]
        if timelines:
            point = timelines[0]["points"][0]
            assert "value" in point and "normalized_value" in point and "period_label" in point

    def test_the_first_point_has_no_pct_change(self, client):
        timelines = client.get("/api/timelines").json()["timelines"]
        if timelines:
            assert timelines[0]["points"][0]["pct_change_from_previous"] is None

    def test_scoping_to_a_nonexistent_document_is_404(self, client):
        assert client.get("/api/timelines", params={"document_id": 99999999}).status_code == 404

    def test_stats_reports_both_grounding_levels(self, client):
        stats = client.get("/api/stats").json()
        assert "fact_validated" in stats and "quote_grounded_only" in stats


class TestRelationshipComparisonEnrichment:
    """GET /api/relationships attaches a normalized_comparison computed fresh
    from app.normalize.compare_values, independent of the relation_type
    judgment. This is what lets the UI explain a case like two facts with the
    same digits but "INR" vs "million" units -- the old client-side check
    only compared unit strings for exact equality and rendered nothing at
    all when they differed, silently dropping a signal the backend already
    had (see app/main.py list_relationships and static/app.js comparisonStrip)."""

    def test_every_relationship_carries_the_field(self, client):
        rels = client.get("/api/relationships").json()
        assert rels, "expected at least one relationship in the dev database this suite runs against"
        assert all("normalized_comparison" in r for r in rels)

    def test_a_comparable_entry_has_the_expected_shape(self, client):
        rels = client.get("/api/relationships").json()
        comparable = [r["normalized_comparison"] for r in rels
                      if r["normalized_comparison"] and r["normalized_comparison"]["comparable"]]
        assert comparable, "expected at least one comparable pair to exercise the shape"
        c = comparable[0]
        assert isinstance(c["diff_pct"], (int, float))
        assert isinstance(c["value_a"], (int, float)) and isinstance(c["value_b"], (int, float))
        assert isinstance(c["common_unit"], str)
        assert isinstance(c["magnitude_suspect"], bool)

    def test_a_magnitude_suspect_pair_is_not_reported_as_agreeing(self, client):
        """The exact regression from the user report: same digits, unit
        recorded at two different scales -- must never be shown as a clean
        agreement, since that reads as a real corroboration."""
        rels = client.get("/api/relationships").json()
        suspects = [r["normalized_comparison"] for r in rels
                    if r["normalized_comparison"] and r["normalized_comparison"]["magnitude_suspect"]]
        for c in suspects:
            assert c["agree"] is not True

    def test_a_pair_missing_a_numeric_value_is_not_comparable(self, client):
        """compare_values() still runs and returns a result even when one
        side has no number -- it just reports comparable=False rather than
        the whole field going missing, so the UI can distinguish "checked,
        not comparable" from "never checked" (e.g. a fact lookup failure)."""
        facts = {f["id"]: f for f in client.get("/api/facts").json()}
        rels = client.get("/api/relationships").json()
        for r in rels:
            fa, fb = facts.get(r["fact_id_a"]), facts.get(r["fact_id_b"])
            if not fa or not fb:
                assert r["normalized_comparison"] is None
            elif fa.get("value_numeric") is None or fb.get("value_numeric") is None:
                assert r["normalized_comparison"]["comparable"] is False


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
