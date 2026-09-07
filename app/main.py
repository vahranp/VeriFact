"""
FastAPI app: upload PDFs, inspect extracted facts, inspect cross-document
relationships. Serves a small static UI at "/".
"""
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.cache import hash_file
from app.config import UPLOAD_DIR, BASE_DIR, MAX_UPLOAD_MB, LARGE_JOB_PAGE_WARNING
from app.pdf_extract import page_count, parse_page_spec
from app.pipeline import process_document

app = FastAPI(title="Fact Knowledge Layer")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

db.init_db()

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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

@app.post("/api/documents")
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
    if pages:
        pages_to_process = len(parse_page_spec(pages) & set(range(1, total_pages + 1)))
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


@app.get("/api/documents")
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


@app.get("/api/documents/{document_id}")
def get_document(document_id: int):
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


@app.get("/api/documents/{document_id}/pdf")
def get_document_pdf(document_id: int):
    d = db.get_document(document_id)
    if not d:
        raise HTTPException(404, "Document not found")
    return FileResponse(d["stored_path"], media_type="application/pdf", filename=d["original_name"])


# ---------------- facts ----------------

@app.get("/api/facts")
def list_facts(document_id: Optional[int] = None, q: Optional[str] = None):
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


@app.get("/api/facts/{fact_id}")
def get_fact(fact_id: int):
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

@app.get("/api/relationships")
def list_relationships(relation_type: Optional[str] = None):
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

@app.get("/api/issues")
def list_issues(document_id: Optional[int] = None):
    return db.list_issues(document_id)


@app.get("/api/stats")
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
        "relationships": len(rels),
        "relationships_by_type": by_type,
        "issues": len(issues),
    }
