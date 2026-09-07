"""
End-to-end orchestration for one document: extract -> per-chunk fact
extraction -> embed -> store -> incremental cross-document comparison.
Runs as a FastAPI BackgroundTask so the upload endpoint returns instantly
and the UI can poll document status.
"""
import traceback

from app import db
from app.embeddings import embed
from app.fact_extraction import extract_facts_from_chunk
from app.pdf_extract import extract_chunks, page_count, parse_page_spec
from app.relationships import build_relationships_for_document


def process_document(document_id: int, pdf_path: str, max_pages: int | None = None,
                      pages: str | None = None):
    """max_pages: process only pages 1..N (a prefix cap).
    pages: process only this explicit selector, e.g. "1,3,7-10" -- lets a
    large PDF be ingested a targeted slice at a time instead of end to end.
    If both are omitted, the whole document is processed."""
    try:
        db.update_document(document_id, status="processing")
        n_pages = page_count(pdf_path)
        db.update_document(document_id, num_pages=n_pages)

        chunks = extract_chunks(pdf_path)
        if pages:
            wanted = parse_page_spec(pages)
            chunks = [c for c in chunks if c.page_number in wanted]
        elif max_pages is not None:
            chunks = [c for c in chunks if c.page_number <= max_pages]

        doc = db.get_document(document_id)
        document_name = doc["original_name"]

        new_fact_ids: list[int] = []
        for chunk in chunks:
            facts, issues = extract_facts_from_chunk(chunk, document_name)

            for issue in issues:
                db.insert_issue(document_id, chunk.page_number, issue["issue_type"],
                                 issue["detail"], issue.get("raw_excerpt", ""))

            if not facts:
                continue

            statements = [f["statement"] for f in facts]
            vectors = embed(statements)
            for fact, vec in zip(facts, vectors):
                fid = db.insert_fact(document_id, chunk.page_number, fact, vec)
                new_fact_ids.append(fid)

        rel_summary = {"candidates_checked": 0, "stored": 0, "skipped_unrelated": 0, "errors": 0}
        if new_fact_ids:
            rel_summary = build_relationships_for_document(document_id, new_fact_ids)

        db.update_document(
            document_id, status="done",
            error_message=(
                f"facts={len(new_fact_ids)} "
                f"relationships_checked={rel_summary['candidates_checked']} "
                f"relationships_stored={rel_summary['stored']}"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - top-level job boundary, must not crash the server
        db.update_document(document_id, status="failed", error_message=f"{exc}\n{traceback.format_exc()[-1500:]}")
        db.insert_issue(document_id, None, "pipeline_crashed", str(exc), traceback.format_exc()[-1500:])
