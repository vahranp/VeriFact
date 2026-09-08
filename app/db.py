"""
SQLite storage layer. Deliberately un-fancy (no ORM) so the schema is easy
to read end to end. Facts are stored with generic subject/attribute/value
columns rather than one column per fact-type -- that's what lets new kinds
of facts show up without a migration (the "dynamic schema" extension).
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Optional

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    num_pages INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    uploaded_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    page_number INTEGER NOT NULL,
    subject TEXT,
    attribute TEXT,
    value TEXT,
    value_numeric REAL,
    unit TEXT,
    time_period TEXT,
    scope TEXT,
    statement TEXT NOT NULL,
    quote TEXT NOT NULL,
    quote_grounded INTEGER NOT NULL DEFAULT 1,
    confidence REAL,
    embedding_json TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id_a INTEGER NOT NULL REFERENCES facts(id),
    fact_id_b INTEGER NOT NULL REFERENCES facts(id),
    relation_type TEXT NOT NULL,           -- corroborates | contradicts | reconciled
    explanation TEXT,
    reconciliation_context TEXT,
    confidence REAL,
    similarity_score REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id),
    page_number INTEGER,
    issue_type TEXT NOT NULL,
    detail TEXT,
    raw_excerpt TEXT,
    created_at REAL NOT NULL
);

-- Keyed on hash(model + prompt + chunk text) -- see app/cache.py. A prompt
-- or model change naturally invalidates old entries (different hash),
-- no manual cache-busting needed. Reused whenever the same page text is
-- extracted again, whether from a re-upload of the same PDF or from
-- overlapping chunk windows.
CREATE TABLE IF NOT EXISTS extraction_cache (
    chunk_hash TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- Keyed on hash(model + prompt + the two facts' content) -- order
-- independent, content-based (not fact id) so the same underlying claim
-- re-extracted into a different document still hits the cache.
CREATE TABLE IF NOT EXISTS relationship_cache (
    pair_hash TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_document ON facts(document_id);
CREATE INDEX IF NOT EXISTS idx_rel_a ON relationships(fact_id_a);
CREATE INDEX IF NOT EXISTS idx_rel_b ON relationships(fact_id_b);
"""


@contextmanager
def get_conn():
    # busy_timeout matters now that fact extraction runs with a thread pool
    # (see app/pipeline.py): if two worker threads happen to write at the
    # same instant, SQLite retries for up to 30s instead of raising
    # "database is locked". Actual LLM calls (the slow part) happen outside
    # any connection, so lock hold times are always short.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn, table: str, column: str, coltype: str):
    """Additive, non-destructive migration for columns added after the
    initial schema (documents.content_hash, documents.stats_json) -- avoids
    forcing a fresh DB (and losing real ingested facts) every time the
    schema gains a field."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "documents", "content_hash", "TEXT")
        _ensure_column(conn, "documents", "stats_json", "TEXT")
        _ensure_column(conn, "documents", "page_selector", "TEXT")
        _ensure_column(conn, "documents", "reused_from_document_id", "INTEGER")
        _ensure_column(conn, "documents", "progress_json", "TEXT")
        # Which retrieval signal promoted this pair to an LLM judgment
        # (see app/candidates.py) -- makes a missed or spurious
        # relationship traceable to the signal responsible.
        _ensure_column(conn, "relationships", "candidate_reason", "TEXT")
        # Grounding has two levels: the quote being verbatim in the source,
        # and the quote actually supporting the extracted value. See
        # app/evidence.py for why the second is a separate question.
        _ensure_column(conn, "facts", "evidence_status", "TEXT")
        _ensure_column(conn, "facts", "evidence_detail", "TEXT")
        # Which extraction behaviour produced this document -- reuse of a
        # byte-identical upload is only valid while it still matches.
        _ensure_column(conn, "documents", "pipeline_fingerprint", "TEXT")


# The sweep must run only when the SERVER starts, never from init_db().
#
# It was originally called from init_db() with a once-per-process guard,
# and that guard was not enough -- it bit within minutes. A separate
# python process querying the database calls init_db(), gets its own fresh
# module state, and sweeps: it marked a document that the running server
# was actively extracting as "failed". A per-process guard cannot see
# other processes.
#
# The server owns background jobs, so only the server may declare them
# orphaned. main.py calls this from its startup hook; scripts and tests
# touching the same database no longer touch running work.
_ORPHAN_SWEEP_DONE = False


def fail_orphaned_jobs():
    """Marks documents left mid-processing by a previous run as failed.

    Ingestion runs as a FastAPI BackgroundTask inside the server process,
    so a restart -- or a crash, or Ctrl-C -- abandons whatever was in
    flight. Those rows previously sat at "processing" forever, showing a
    progress bar that would never move, with no way to tell them from a
    job that is genuinely still running.

    Since nothing can resume them, they are marked failed at startup with
    an explanation. Re-uploading the same file is cheap: the chunk-level
    extraction cache still holds every chunk that finished before the
    interruption, so only the remainder is re-run.
    """
    global _ORPHAN_SWEEP_DONE
    if _ORPHAN_SWEEP_DONE:
        return
    _ORPHAN_SWEEP_DONE = True

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM documents WHERE status IN ('processing', 'pending')"
        ).fetchall()
        if not rows:
            return
        conn.execute(
            "UPDATE documents SET status = 'failed', progress_json = NULL, "
            "error_message = 'Interrupted by a server restart -- no worker was left to finish it. "
            "Re-upload to resume; already-extracted chunks are still cached.' "
            "WHERE status IN ('processing', 'pending')"
        )
    print(f"Marked {len(rows)} interrupted document(s) as failed on startup.")


# ---------------- documents ----------------

def insert_document(original_name: str, stored_path: str, content_hash: Optional[str] = None,
                     page_selector: Optional[str] = None) -> int:
    from app.config import pipeline_fingerprint

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO documents (original_name, stored_path, status, uploaded_at, content_hash, "
            "page_selector, pipeline_fingerprint) VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (original_name, stored_path, time.time(), content_hash, page_selector,
             pipeline_fingerprint()),
        )
        return cur.lastrowid


def set_progress(document_id: int, stage: str, current: int, total: int, detail: Optional[str] = None):
    """Lightweight, frequently-written progress marker for the UI's live
    progress bar -- separate from stats_json (which is the final, complete
    summary written once at the end). Called from the main thread only,
    once per completed chunk/candidate, as futures resolve (see
    app/pipeline.py and app/relationships.py) -- never from a worker
    thread, keeping all SQLite writes single-threaded."""
    update_document(document_id, progress_json=json.dumps({
        "stage": stage, "current": current, "total": total, "detail": detail,
    }))


def clear_progress(document_id: int):
    update_document(document_id, progress_json=None)


def find_done_document_by_hash(content_hash: str, page_selector: Optional[str]) -> Optional[dict]:
    """A prior upload of byte-identical content, processed with the exact
    same page selector, that already finished successfully -- used to
    short-circuit a duplicate upload entirely rather than re-running the
    pipeline (see app/pipeline.py). Matching on page_selector too (not just
    the hash) matters: a full-document run and a pages=52,68 run of the
    same file are not interchangeable, so only an exact match
    short-circuits; anything else still benefits from the chunk-level
    extraction_cache instead.

    Requires at least one fact to exist for that document: a "done" run
    that extracted zero facts (e.g. every chunk timed out) is not a
    success worth reusing -- a retry should get a real second attempt, not
    be pointed back at the same all-failure result forever.

    Also requires the pipeline fingerprint to match. Identical bytes are
    only safe to reuse while the pipeline would still extract the same
    thing from them, and that assumption broke for real: after PDF text
    extraction changed to reconstruct table pages from word coordinates,
    re-uploading a page to test the improvement returned the OLD facts,
    because the file's bytes hadn't changed. The chunk-level cache handled
    it correctly; this short-circuit ran first and never gave it the
    chance. See config.pipeline_fingerprint."""
    from app.config import pipeline_fingerprint

    with get_conn() as conn:
        row = conn.execute(
            "SELECT d.* FROM documents d WHERE d.content_hash = ? AND d.status = 'done' "
            "AND d.page_selector IS ? AND d.pipeline_fingerprint = ? "
            "AND EXISTS (SELECT 1 FROM facts f WHERE f.document_id = d.id) "
            "ORDER BY d.id DESC LIMIT 1",
            (content_hash, page_selector, pipeline_fingerprint()),
        ).fetchone()
        return dict(row) if row else None


def update_document(document_id: int, **fields: Any):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE documents SET {cols} WHERE id = ?", (*fields.values(), document_id))


def get_document(document_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return dict(row) if row else None


def list_documents() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


# ---------------- facts ----------------

def insert_fact(document_id: int, page_number: int, fact: dict, embedding: list[float]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO facts
               (document_id, page_number, subject, attribute, value, value_numeric, unit,
                time_period, scope, statement, quote, quote_grounded, confidence,
                evidence_status, evidence_detail, embedding_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                document_id, page_number,
                fact.get("subject"), fact.get("attribute"), fact.get("value"),
                fact.get("value_numeric"), fact.get("unit"), fact.get("time_period"),
                fact.get("scope"), fact.get("statement"), fact.get("quote"),
                1 if fact.get("quote_grounded", True) else 0,
                fact.get("confidence"),
                fact.get("evidence_status"), fact.get("evidence_detail"),
                json.dumps(embedding), time.time(),
            ),
        )
        return cur.lastrowid


def get_fact(fact_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return dict(row) if row else None


def list_facts(document_id: Optional[int] = None) -> list[dict]:
    with get_conn() as conn:
        if document_id is None:
            rows = conn.execute("SELECT * FROM facts ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM facts WHERE document_id = ? ORDER BY page_number, id", (document_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_all_embeddings(exclude_document_id: Optional[int] = None) -> list[tuple[int, list[float]]]:
    """Returns (fact_id, embedding) pairs for every fact currently stored,
    optionally excluding facts belonging to one document (used when we
    compare a freshly-processed document's facts against everything else)."""
    with get_conn() as conn:
        if exclude_document_id is None:
            rows = conn.execute("SELECT id, embedding_json FROM facts").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, embedding_json FROM facts WHERE document_id != ?", (exclude_document_id,)
            ).fetchall()
        return [(r["id"], json.loads(r["embedding_json"])) for r in rows]


# ---------------- relationships ----------------

def insert_relationship(fact_id_a: int, fact_id_b: int, relation_type: str, explanation: str,
                         reconciliation_context: Optional[str], confidence: Optional[float],
                         similarity_score: float, candidate_reason: Optional[str] = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO relationships
               (fact_id_a, fact_id_b, relation_type, explanation, reconciliation_context,
                confidence, similarity_score, candidate_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fact_id_a, fact_id_b, relation_type, explanation, reconciliation_context,
             confidence, similarity_score, candidate_reason, time.time()),
        )
        return cur.lastrowid


def relationship_exists(fact_id_a: int, fact_id_b: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM relationships
               WHERE (fact_id_a = ? AND fact_id_b = ?) OR (fact_id_a = ? AND fact_id_b = ?)""",
            (fact_id_a, fact_id_b, fact_id_b, fact_id_a),
        ).fetchone()
        return row is not None


def list_relationships(relation_type: Optional[str] = None) -> list[dict]:
    with get_conn() as conn:
        if relation_type is None:
            rows = conn.execute("SELECT * FROM relationships ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM relationships WHERE relation_type = ? ORDER BY id DESC", (relation_type,)
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------- extraction issues ----------------

def insert_issue(document_id: Optional[int], page_number: Optional[int], issue_type: str,
                  detail: str, raw_excerpt: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO extraction_issues (document_id, page_number, issue_type, detail, raw_excerpt, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (document_id, page_number, issue_type, detail, raw_excerpt[:2000], time.time()),
        )


def list_issues(document_id: Optional[int] = None) -> list[dict]:
    with get_conn() as conn:
        if document_id is None:
            rows = conn.execute("SELECT * FROM extraction_issues ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM extraction_issues WHERE document_id = ? ORDER BY id DESC", (document_id,)
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------- caches (see app/cache.py for key derivation) ----------------

def get_cached_extraction(chunk_hash: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT facts_json, issues_json FROM extraction_cache WHERE chunk_hash = ?", (chunk_hash,)
        ).fetchone()
        if not row:
            return None
        return {"facts": json.loads(row["facts_json"]), "issues": json.loads(row["issues_json"])}


def set_cached_extraction(chunk_hash: str, model: str, facts: list[dict], issues: list[dict]):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO extraction_cache (chunk_hash, model, facts_json, issues_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chunk_hash, model, json.dumps(facts), json.dumps(issues), time.time()),
        )


def get_cached_relationship(pair_hash: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result_json FROM relationship_cache WHERE pair_hash = ?", (pair_hash,)
        ).fetchone()
        return json.loads(row["result_json"]) if row else None


def set_cached_relationship(pair_hash: str, model: str, result: dict):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO relationship_cache (pair_hash, model, result_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (pair_hash, model, json.dumps(result), time.time()),
        )
