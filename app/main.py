"""
FastAPI app: upload PDFs, inspect extracted facts, inspect cross-document
relationships. Serves a small static UI at "/".
"""
import json
import shutil
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Path as PathParam, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api_models import (
    CoherenceOut, DocumentOut, FactOut, IssueOut, RelationshipOut, StatsOut,
    UploadAccepted,
)

from app import db
from app.cache import hash_file
from app.coherence import check_coherence
from app.config import UPLOAD_DIR, BASE_DIR, MAX_UPLOAD_MB, LARGE_JOB_PAGE_WARNING
from app.pdf_extract import PageSpecError, has_extractable_text, is_encrypted, page_count, parse_page_spec
from app.pipeline import process_document

app = FastAPI(title="Fact Knowledge Layer")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

db.init_db()


@app.on_event("startup")
def _recover_interrupted_jobs():
    """Ingestion runs in this process, so anything mid-flight when it last
    stopped is unrecoverable. Swept here rather than in db.init_db()
    because only the server owns those jobs -- a script or test calling
    init_db() must never mark the running server's work as failed."""
    db.fail_orphaned_jobs()


STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last line of defence so an unexpected error returns clean JSON
    rather than a stack trace.

    A traceback in an HTTP response leaks file paths and internal
    structure, and tells the caller nothing they can act on. The detail
    stays server-side in the log; the client gets the request path and a
    statement that it failed.
    """
    import traceback
    print(f"Unhandled error on {request.method} {request.url.path}:")
    print(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal error handling {request.method} {request.url.path}. "
                            f"The server logged the cause."},
    )


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


def _doc_name(document_id: int, cache: dict) -> str:
    if document_id not in cache:
        d = db.get_document(document_id)
        cache[document_id] = d["original_name"] if d else f"document {document_id}"
    return cache[document_id]


def _enrich_fact(fact: dict, cache: dict) -> dict:
    out = dict(fact)
    out.pop("embedding_json", None)
    out["document_name"] = _doc_name(fact["document_id"], cache)
    return out


# ---------------- documents ----------------

@app.post("/api/documents", response_model=UploadAccepted,
          summary="Upload a PDF for ingestion",
          responses={400: {"description": "Not a PDF, corrupt, empty, or a malformed page selector"},
                     413: {"description": "File exceeds MAX_UPLOAD_MB"}})
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    max_pages: Optional[int] = Query(
        None, description="Optional cap: process only pages 1..N (bounds LLM spend on a big PDF)."
    ),
    pages: Optional[str] = Query(
        None, description='Optional explicit page selector, e.g. "1,3,7-10" (bounds spend, targets specific content).'
    ),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported.")

    # Validate the page selector BEFORE writing a file, creating a document
    # row, or queueing a job. Previously this was only parsed further down,
    # after the background task had already been scheduled -- so a selector
    # like "24-24-24" returned a 500 *and* left a job running that died
    # asynchronously with an opaque int() error. Cheapest check first.
    selected_pages: Optional[set[int]] = None
    if pages:
        try:
            selected_pages = parse_page_spec(pages)
        except PageSpecError as exc:
            raise HTTPException(400, str(exc))
    if max_pages is not None and max_pages < 1:
        raise HTTPException(400, f"max_pages must be at least 1, got {max_pages}.")

    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"
    dest = UPLOAD_DIR / safe_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    size_mb = dest.stat().st_size / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        dest.unlink(missing_ok=True)
        raise HTTPException(413, f"PDF is {size_mb:.1f} MB; the limit is {MAX_UPLOAD_MB} MB.")

    # Reject a file that isn't really a PDF (or is corrupt) here, with a
    # clean 400, rather than letting the background job discover it later
    # and fail the document asynchronously with a stack trace.
    try:
        total_pages = page_count(str(dest))
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Could not read that file as a PDF -- it may be corrupt or not a real PDF.")
    if total_pages == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "That PDF has no pages.")

    # page_count succeeding does NOT mean the PDF is readable -- an
    # encrypted file reports a real page count without a password, and
    # only fails once something tries to read actual page content. Caught
    # here for the same reason corruption is: a clean 400 now, not a raw
    # traceback on the document after a background job has already started.
    if is_encrypted(str(dest)):
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "That PDF is password-protected -- please remove the password and re-upload.")

    # A scanned/image-only PDF (no embedded text layer) would otherwise
    # pass every check above, run the whole pipeline, and complete "done"
    # with zero facts -- looking exactly like a quiet failure rather than
    # the honest, expected outcome for a document this system cannot read.
    if not has_extractable_text(str(dest)):
        dest.unlink(missing_ok=True)
        raise HTTPException(
            400,
            "No extractable text was found on any page. This PDF may be a scanned image with "
            "no text layer -- OCR is not currently supported.",
        )

    content_hash = hash_file(str(dest))
    page_selector = pages if pages else (f"max:{max_pages}" if max_pages is not None else None)

    # Same bytes, same page selector as a prior completed run -> nothing
    # new to do. Cheap (one hash + one indexed lookup) and fully safe: it
    # only fires on an exact match, never on a merely-similar upload.
    prior = db.find_done_document_by_hash(content_hash, page_selector)
    if prior:
        document_id = db.insert_document(file.filename, str(dest), content_hash, page_selector)
        db.update_document(
            document_id, status="done",
            num_pages=prior["num_pages"],
            reused_from_document_id=prior["id"],
            error_message=f"Identical content + page selection as document {prior['id']} -- reused its facts and relationships, no LLM calls made.",
            stats_json=prior.get("stats_json"),
        )
        return {"id": document_id, "status": "done", "reused_document_id": prior["id"]}

    document_id = db.insert_document(file.filename, str(dest), content_hash, page_selector)
    background_tasks.add_task(process_document, document_id, str(dest), max_pages, pages)

    # Non-blocking heads-up rather than a refusal: a big unrestricted run is
    # a legitimate thing to ask for, but on a local model it can take hours,
    # and previously nothing said so until you noticed it still running.
    pages_to_process = total_pages
    if selected_pages is not None:
        pages_to_process = len(selected_pages & set(range(1, total_pages + 1)))
    elif max_pages is not None:
        pages_to_process = min(max_pages, total_pages)

    response = {"id": document_id, "status": "pending", "pages_to_process": pages_to_process}
    if pages_to_process > LARGE_JOB_PAGE_WARNING:
        response["warning"] = (
            f"This will process {pages_to_process} pages on a local model, which can take hours. "
            f"Consider re-uploading with a page selector (e.g. pages=1,3,7-10) to target specific content."
        )
    return response


def _pop_progress(d: dict) -> dict:
    progress_json = d.pop("progress_json", None)
    d["progress"] = json.loads(progress_json) if progress_json else None
    return d


@app.get("/api/documents", response_model=list[DocumentOut],
         summary="All ingested documents with status and pipeline stats")
def list_documents():
    docs = db.list_documents()
    facts = db.list_facts()
    counts: dict[int, int] = {}
    for f in facts:
        counts[f["document_id"]] = counts.get(f["document_id"], 0) + 1
    for d in docs:
        # A reused (duplicate-content) document has no fact rows of its own
        # -- attribute its source document's count so it doesn't look empty.
        source_id = d.get("reused_from_document_id") or d["id"]
        d["fact_count"] = counts.get(source_id, 0)
        _pop_progress(d)
        # Parsed stats are included here (not just on the detail endpoint) so
        # the overview dashboard can aggregate pipeline totals from one request
        # instead of fetching every document individually.
        stats_json = d.pop("stats_json", None)
        d["stats"] = json.loads(stats_json) if stats_json else None
    return docs


@app.get("/api/documents/{document_id}", response_model=DocumentOut,
         summary="One document with its facts and extraction issues",
         responses={404: {"description": "No such document"}})
def get_document(document_id: int = PathParam(ge=1)):
    d = db.get_document(document_id)
    if not d:
        raise HTTPException(404, "Document not found")
    # Reused documents store no facts of their own (see upload_document's
    # dedup short-circuit) -- read through to the source so the API still
    # returns real data rather than an empty-looking "done" document.
    source_id = d.get("reused_from_document_id") or document_id
    d["facts"] = db.list_facts(source_id)
    d["issues"] = db.list_issues(source_id)
    for f in d["facts"]:
        f.pop("embedding_json", None)
    stats_json = d.pop("stats_json", None)
    d["stats"] = json.loads(stats_json) if stats_json else None
    _pop_progress(d)
    return d


@app.get("/api/documents/{document_id}/pdf",
         summary="The original PDF, for citation links",
         responses={404: {"description": "No such document"}})
def get_document_pdf(document_id: int = PathParam(ge=1)):
    d = db.get_document(document_id)
    if not d:
        raise HTTPException(404, "Document not found")
    return FileResponse(d["stored_path"], media_type="application/pdf", filename=d["original_name"])


@app.post("/api/documents/{document_id}/cancel", response_model=DocumentOut,
          summary="Stop a pending or in-progress document",
          responses={404: {"description": "No such document"},
                     409: {"description": "Document is not pending or processing -- nothing to cancel"}})
def cancel_document(document_id: int = PathParam(ge=1)):
    """Requests that a running or queued document stop.

    This only sets a flag -- app/pipeline.py checks it cooperatively
    between chunks and candidate pairs, so the response reflects the
    request being accepted, not the job having already stopped. The
    document's status stays 'processing' (with cancel_requested now true)
    until the pipeline notices and flips it to 'cancelled', which normally
    happens within one chunk or one candidate-pair judgment.
    """
    d = db.get_document(document_id)
    if not d:
        raise HTTPException(404, "Document not found")
    if d["status"] not in ("pending", "processing"):
        raise HTTPException(409, f"Document is '{d['status']}' -- nothing to cancel.")

    db.request_cancel(document_id)

    updated = db.get_document(document_id)
    stats_json = updated.pop("stats_json", None)
    updated["stats"] = json.loads(stats_json) if stats_json else None
    _pop_progress(updated)
    return updated


# ---------------- facts ----------------

@app.get("/api/facts", response_model=list[FactOut],
         summary="Extracted facts, each with its evidence and grounding status")
def list_facts(
    document_id: Optional[int] = Query(None, ge=1),
    q: Optional[str] = Query(None, max_length=200, description="Case-insensitive substring match on statement, subject or attribute."),
):
    facts = db.list_facts(document_id)
    if q:
        ql = q.lower()
        facts = [
            f for f in facts
            if ql in (f.get("statement") or "").lower()
            or ql in (f.get("subject") or "").lower()
            or ql in (f.get("attribute") or "").lower()
        ]
    cache: dict = {}
    return [_enrich_fact(f, cache) for f in facts]


@app.get("/api/facts/{fact_id}", response_model=FactOut,
         summary="One fact plus every relationship it participates in",
         responses={404: {"description": "No such fact"}})
def get_fact(fact_id: int = PathParam(ge=1)):
    fact = db.get_fact(fact_id)
    if not fact:
        raise HTTPException(404, "Fact not found")
    cache: dict = {}
    enriched = _enrich_fact(fact, cache)

    rels = [r for r in db.list_relationships() if r["fact_id_a"] == fact_id or r["fact_id_b"] == fact_id]
    rel_out = []
    for r in rels:
        other_id = r["fact_id_b"] if r["fact_id_a"] == fact_id else r["fact_id_a"]
        other = db.get_fact(other_id)
        rel_out.append({
            **r,
            "other_fact": _enrich_fact(other, cache) if other else None,
        })
    enriched["relationships"] = rel_out
    return enriched


# ---------------- relationships ----------------

@app.get("/api/relationships", response_model=list[RelationshipOut],
         summary="Judged relationships between facts, with both sides inlined")
def list_relationships(
    relation_type: Optional[Literal["corroborates", "contradicts", "reconciled", "uncertain"]] = Query(
        None, description="Filter by type. An unrecognised value is rejected with 422 rather than silently returning nothing.",
    ),
):
    rels = db.list_relationships(relation_type)
    cache: dict = {}
    out = []
    for r in rels:
        fa = db.get_fact(r["fact_id_a"])
        fb = db.get_fact(r["fact_id_b"])
        out.append({
            **r,
            "fact_a": _enrich_fact(fa, cache) if fa else None,
            "fact_b": _enrich_fact(fb, cache) if fb else None,
        })
    return out


# ---------------- issues / stats ----------------

@app.get("/api/issues", response_model=list[IssueOut],
         summary="Extraction and reasoning failures, recorded rather than swallowed")
def list_issues(document_id: Optional[int] = Query(None, ge=1)):
    return db.list_issues(document_id)


@app.get("/api/stats", response_model=StatsOut, summary="Corpus-level counts")
def stats():
    docs = db.list_documents()
    facts = db.list_facts()
    rels = db.list_relationships()
    issues = db.list_issues()
    by_type: dict[str, int] = {}
    for r in rels:
        by_type[r["relation_type"]] = by_type.get(r["relation_type"], 0) + 1
    return {
        "documents": len(docs),
        "documents_done": len([d for d in docs if d["status"] == "done"]),
        "facts": len(facts),
        "ungrounded_facts": len([f for f in facts if not f["quote_grounded"]]),
        # The stronger of the two grounding questions: not just "is this
        # quote real" but "does it support the value". See app/evidence.py.
        "fact_validated": len([f for f in facts if f.get("evidence_status") == "fact_validated"]),
        "quote_grounded_only": len([f for f in facts if f.get("evidence_status") == "quote_grounded"]),
        "relationships": len(rels),
        "relationships_by_type": by_type,
        "issues": len(issues),
    }


@app.get("/api/coherence", response_model=CoherenceOut,
         summary="Logically impossible relationship triangles, and edges implied by transitivity")
def graph_coherence(limit: int = Query(25, ge=1, le=200)):
    """Logical coherence of the relationship graph.

    Relationships are judged pairwise and in isolation, so the graph they
    form can be internally impossible: if A corroborates B and B
    corroborates C, then A cannot contradict C. Triangles like that prove
    at least one of those judgments is wrong -- with no ground truth, no
    reviewer, and no extra model call. The same transitivity also implies
    edges that candidate retrieval never shortlisted.

    See app/coherence.py.
    """
    relationships = db.list_relationships()
    facts_by_id = {f["id"]: f for f in db.list_facts()}
    report = check_coherence(relationships, facts_by_id=facts_by_id)

    cache: dict = {}

    def describe(fact_id: int) -> dict:
        fact = facts_by_id.get(fact_id)
        if not fact:
            return {"id": fact_id}
        return {
            "id": fact_id,
            "statement": fact.get("statement"),
            "value": fact.get("value"),
            "unit": fact.get("unit"),
            "document_name": _doc_name(fact["document_id"], cache),
            "page_number": fact.get("page_number"),
        }

    return {
        "summary": report.summary(),
        "nodes": report.nodes,
        "edges": report.edges,
        "triangles_checked": report.triangles_checked,
        "violation_rate": round(report.violation_rate, 4),
        "violations_total": len(report.violations),
        "implicated_edges": len(report.implicated_edges),
        "inferences_total": len(report.inferences),
        "violations": [
            {
                "facts": [describe(fid) for fid in v.fact_ids],
                "description": v.describe(),
                "reason": v.reason,
                "suspect_relationship_id": v.suspect.get("id"),
                "edges": [
                    {
                        "id": e.get("id"),
                        "relation_type": e.get("relation_type"),
                        "confidence": e.get("confidence"),
                        "fact_id_a": e.get("fact_id_a"),
                        "fact_id_b": e.get("fact_id_b"),
                        "is_suspect": e.get("id") == v.suspect.get("id"),
                    }
                    for e in v.edges
                ],
            }
            for v in report.violations[:limit]
        ],
        "inferences": [
            {
                "fact_a": describe(i.fact_id_a),
                "fact_b": describe(i.fact_id_b),
                "relation_type": i.relation_type,
                "via_fact_id": i.via_fact_id,
                "confidence": i.confidence,
                "explanation": i.explanation,
            }
            for i in report.inferences[:limit]
        ],
    }
