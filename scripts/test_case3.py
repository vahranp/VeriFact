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


def _text(fact, *fields):
    return " ".join(str(fact.get(f) or "") for f in fields).lower()


def _candidates(predicate):
    """Facts matching a predicate, from successfully completed documents
    when any exist (several ingests of the same page, including
    failed/partial ones, may coexist in the database)."""
    done_docs = {d["id"] for d in db.list_documents() if d["status"] == "done"}
    matches = [f for f in db.list_facts() if predicate(f) and f.get("value_numeric") is not None]
    in_done = [f for f in matches if f["document_id"] in done_docs]
    return in_done or matches


def _is_revenue_from_operations(f):
    """Deliberately narrower than a bare "revenue" substring match, which
    once picked up an unrelated fact from a different document entirely
    ("consolidated STATE revenue", from a macroeconomic report also in the
    database) purely because both words appeared somewhere in its text."""
    return "revenue from operations" in _text(f, "attribute", "statement")


def main():
    # Matched on the scope FIELD itself, not scope-or-statement-or-attribute
    # combined -- the combined-text version is what let an unrelated fact's
    # prose ("...consolidated state revenue...") masquerade as a scope match.
    standalone_candidates = _candidates(
        lambda f: "standalone" in (f.get("scope") or "").lower() and _is_revenue_from_operations(f)
    )
    consolidated_candidates = _candidates(
        lambda f: "consolidated" in (f.get("scope") or "").lower() and _is_revenue_from_operations(f)
    )

    if not standalone_candidates or not consolidated_candidates:
        out("Could not locate a standalone/consolidated revenue-from-operations pair in the database.")
        out("Ingest a page stating both figures, then re-run.")
        return 1

    # Prefer a pair that states the SAME period -- without this, "standalone
    # FY24" could be compared against "consolidated FY23" purely because
    # both happened to be the most-recently-inserted match for their own
    # predicate, which is a period difference doing the reconciling instead
    # of the scope difference this case is actually meant to demonstrate.
    standalone = consolidated = None
    for s in reversed(standalone_candidates):
        same_period = [c for c in consolidated_candidates if c.get("time_period") == s.get("time_period")]
        if same_period:
            standalone, consolidated = s, same_period[-1]
            break
    if standalone is None:
        standalone, consolidated = standalone_candidates[-1], consolidated_candidates[-1]

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
    out(f"decision_source: {result.get('decision_source')}")
    out(f"llm_proposal: {result.get('llm_proposal')}")
    out(f"disagreement: {result.get('disagreement')} -- {result.get('disagreement_reason')}")
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
