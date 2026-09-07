"""
FastAPI app: upload PDFs, inspect extracted facts, inspect cross-document
relationships. Serves a small static UI at "/".
"""
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
from app.config import UPLOAD_DIR, BASE_DIR
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
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported.")

    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"
    dest = UPLOAD_DIR / safe_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    document_id = db.insert_document(file.filename, str(dest))
    background_tasks.add_task(process_document, document_id, str(dest), max_pages, pages)
    return {"id": document_id, "status": "pending"}


@app.get("/api/documents")
def list_documents():
    docs = db.list_documents()
    facts = db.list_facts()
    counts: dict[int, int] = {}
    for f in facts:
        counts[f["document_id"]] = counts.get(f["document_id"], 0) + 1
    for d in docs:
        d["fact_count"] = counts.get(d["id"], 0)
    return docs


@app.get("/api/documents/{document_id}")
def get_document(document_id: int):
    d = db.get_document(document_id)
    if not d:
        raise HTTPException(404, "Document not found")
    d["facts"] = db.list_facts(document_id)
    d["issues"] = db.list_issues(document_id)
    for f in d["facts"]:
        f.pop("embedding_json", None)
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
