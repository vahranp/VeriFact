"""Tests for the DB-level relationship-uniqueness guarantee (see
init_db's idx_rel_pair_unique and insert_relationship in app/db.py).

Before this, the only protection against a duplicate relationship
between the same two facts was relationships.py checking
relationship_exists() before calling classify_pair -- a real
check-then-insert race between two worker threads could still produce
two rows for one pair. fact_low_id/fact_high_id plus a UNIQUE index
make the database itself reject the duplicate, and insert_relationship
turns that rejection into "return the existing row" rather than a
crash, since a benign race isn't an error the caller should see.

Runs against the real (non-isolated) dev DB, like
tests/test_document_reuse.py -- inserts and explicitly cleans up its
own rows.
"""
import sqlite3

import pytest

from app import db


def _insert_document():
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (original_name, stored_path, status, uploaded_at) "
            "VALUES ('t.pdf', '/tmp/t.pdf', 'done', 0)",
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
def two_facts():
    doc_id = _insert_document()
    base = {
        "unit": "INR million", "time_period": "FY24", "scope": None,
        "quote_grounded": True, "confidence": 0.9,
        "evidence_status": "fact_validated", "evidence_detail": "ok",
    }
    fact_a = db.insert_fact(doc_id, 1, {
        **base, "subject": "Test Co", "attribute": "revenue", "value": "100",
        "value_numeric": 100.0, "statement": "Test Co revenue was 100 million.",
        "quote": "revenue was 100 million",
    }, embedding=[0.1, 0.2])
    fact_b = db.insert_fact(doc_id, 1, {
        **base, "subject": "Test Co", "attribute": "revenue", "value": "100",
        "value_numeric": 100.0, "statement": "Test Co turnover was 100 million.",
        "quote": "turnover was 100 million",
    }, embedding=[0.1, 0.2])
    try:
        yield fact_a, fact_b
    finally:
        _delete_document(doc_id)


class TestRelationshipUniqueIndex:
    def test_the_unique_index_exists(self):
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_rel_pair_unique'"
            ).fetchone()
        assert row is not None
        assert "UNIQUE" in row["sql"].upper()

    def test_fact_low_high_id_are_order_independent(self, two_facts):
        fact_a, fact_b = two_facts
        lo, hi = min(fact_a, fact_b), max(fact_a, fact_b)

        db.insert_relationship(fact_a, fact_b, "corroborates", "explanation", None, 0.9, 0.95)
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT fact_low_id, fact_high_id FROM relationships WHERE fact_id_a = ? AND fact_id_b = ?",
                (fact_a, fact_b),
            ).fetchone()
        assert (row["fact_low_id"], row["fact_high_id"]) == (lo, hi)

    def test_inserting_the_same_pair_twice_does_not_create_two_rows(self, two_facts):
        fact_a, fact_b = two_facts
        db.insert_relationship(fact_a, fact_b, "corroborates", "first", None, 0.9, 0.95)
        db.insert_relationship(fact_a, fact_b, "corroborates", "second", None, 0.9, 0.95)

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) c FROM relationships WHERE "
                "(fact_id_a = ? AND fact_id_b = ?) OR (fact_id_a = ? AND fact_id_b = ?)",
                (fact_a, fact_b, fact_b, fact_a),
            ).fetchone()
        assert rows["c"] == 1

    def test_the_reverse_ordered_pair_is_treated_as_the_same_relationship(self, two_facts):
        """A relationship has no inherent direction: (a, b) and (b, a)
        must collide on the same DB row, since fact_low_id/fact_high_id
        are computed from min/max regardless of argument order."""
        fact_a, fact_b = two_facts
        db.insert_relationship(fact_a, fact_b, "corroborates", "forward", None, 0.9, 0.95)
        db.insert_relationship(fact_b, fact_a, "corroborates", "reversed", None, 0.9, 0.95)

        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) c FROM relationships WHERE fact_low_id = ? AND fact_high_id = ?",
                (min(fact_a, fact_b), max(fact_a, fact_b)),
            ).fetchone()
        assert rows["c"] == 1

    def test_a_direct_sqlite_integrity_error_is_impossible_to_smuggle_past_the_index(self, two_facts):
        """Confirms the index is a real constraint, not just documentation:
        a raw second INSERT bypassing insert_relationship's own
        try/except still gets rejected by SQLite itself."""
        fact_a, fact_b = two_facts
        db.insert_relationship(fact_a, fact_b, "corroborates", "first", None, 0.9, 0.95)
        with db.get_conn() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO relationships (fact_id_a, fact_id_b, fact_low_id, fact_high_id, "
                    "relation_type, created_at) VALUES (?, ?, ?, ?, 'corroborates', 0)",
                    (fact_b, fact_a, min(fact_a, fact_b), max(fact_a, fact_b)),
                )
