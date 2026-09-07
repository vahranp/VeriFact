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

CREATE INDEX IF NOT EXISTS idx_facts_document ON facts(document_id);
CREATE INDEX IF NOT EXISTS idx_rel_a ON relationships(fact_id_a);
CREATE INDEX IF NOT EXISTS idx_rel_b ON relationships(fact_id_b);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------- documents ----------------

def insert_document(original_name: str, stored_path: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO documents (original_name, stored_path, status, uploaded_at) VALUES (?, ?, 'pending', ?)",
            (original_name, stored_path, time.time()),
        )
        return cur.lastrowid


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
                time_period, scope, statement, quote, quote_grounded, confidence, embedding_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                document_id, page_number,
                fact.get("subject"), fact.get("attribute"), fact.get("value"),
                fact.get("value_numeric"), fact.get("unit"), fact.get("time_period"),
                fact.get("scope"), fact.get("statement"), fact.get("quote"),
                1 if fact.get("quote_grounded", True) else 0,
                fact.get("confidence"), json.dumps(embedding), time.time(),
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
                         similarity_score: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO relationships
               (fact_id_a, fact_id_b, relation_type, explanation, reconciliation_context,
                confidence, similarity_score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (fact_id_a, fact_id_b, relation_type, explanation, reconciliation_context,
             confidence, similarity_score, time.time()),
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
