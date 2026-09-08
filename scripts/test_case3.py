"""Directly verify the Case 3 contextual reconciliation: two revenue
figures for the same period that differ only because one is standalone and
the other consolidated.

This is the case that distinguishes "contradicts" from "reconciled". The
numbers genuinely disagree; what makes it a reconciliation rather than a
conflict is that a stated scope difference explains the gap.

Facts are located by content, never by a hardcoded document or fact id, so
the script stays a meaningful regression check across re-ingests rather
than pinning itself to rows that may go stale.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

db.init_db()


def out(s):
    sys.stdout.write(str(s).encode("ascii", "replace").decode() + "\n")


def _find(predicate):
    """Most recent fact matching a predicate, preferring one with a
    populated value_numeric -- several ingests of the same page may
    coexist and only the classification is under test here."""
    matches = [f for f in db.list_facts() if predicate(f)]
    with_value = [f for f in matches if f.get("value_numeric") is not None]
    return (with_value or matches)[-1] if (with_value or matches) else None


def _text(fact, *fields):
    return " ".join(str(fact.get(f) or "") for f in fields).lower()


def main():
    standalone = _find(lambda f: "standalone" in _text(f, "attribute", "scope", "statement")
                       and "revenue" in _text(f, "attribute", "statement"))
    consolidated = _find(lambda f: "consolidated" in _text(f, "attribute", "scope", "statement")
                         and "revenue" in _text(f, "attribute", "statement"))

    if not standalone or not consolidated:
        out("Could not locate a standalone/consolidated revenue pair in the database.")
        out("Ingest a page stating both figures, then re-run.")
        return 1

    for label, fact in (("standalone  ", standalone), ("consolidated", consolidated)):
        out(f"{label}: doc={fact['document_id']} id={fact['id']} "
            f"value={fact.get('value')} unit={fact.get('unit')} "
            f"scope={fact.get('scope')!r} period={fact.get('time_period')!r}")

    result, cache_hit = classify_pair(
        standalone, f"document {standalone['document_id']}",
        consolidated, f"document {consolidated['document_id']}",
    )
    meta = result.pop("_meta", {})

    out("")
    out(f"cache_hit={cache_hit}")
    out(f"relation_type: {result.get('relation_type')}")
    out(f"confidence: {result.get('confidence')}")
    out(f"explanation: {result.get('explanation')}")
    out(f"reconciliation_context: {result.get('reconciliation_context')}")
    out(f"period={meta.get('period')} scope={meta.get('scope')}")

    expected = "reconciled"
    actual = result.get("relation_type")
    out("")
    out(f"EXPECTED {expected} / ACTUAL {actual} -> {'PASS' if actual == expected else 'FAIL'}")
    return 0 if actual == expected else 1


if __name__ == "__main__":
    sys.exit(main())
