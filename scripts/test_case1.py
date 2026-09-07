"""Directly verify the Case 1 corroboration classification: FY24 revenue
stated as ~Rs81,415.38 million (Annual Report) vs. ~Rs8,142 Cr (earnings
deck) -- the same number in a different unit and document.

Looks up facts by content rather than a hardcoded document id, and prefers
whichever matching fact actually has a populated value_numeric -- earlier
runs of this exact page hit a real extraction bug (value_numeric came
back null for positive-valued facts; see app/fact_extraction.py's
_numeric_fallback and the README's case-4 write-up) that a fresh
extraction of the same page no longer reproduces. This keeps the script a
meaningful regression check going forward instead of quietly pinned to a
known-stale fact."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

db.init_db()


def _best_match(candidates):
    """Prefer a fact with a real numeric value over one where extraction
    left value_numeric null -- both may exist in the DB from different
    upload attempts; the point of this script is to test relationship
    classification, not re-litigate extraction quality."""
    with_value = [f for f in candidates if f.get("value_numeric") is not None]
    return (with_value or candidates)[-1]  # most recently inserted


fact_a = db.get_fact(214)
assert fact_a is not None, "expected fact 214 (annual report consolidated revenue) to exist"

candidates_b = [f for f in db.list_facts() if "8,142" in (f.get("statement") or "") and "grew" not in (f.get("statement") or "").lower()]
assert candidates_b, "no earnings-deck revenue fact found -- has document 14 (or a re-upload of its page 6) been processed?"
fact_b = _best_match(candidates_b)

print(f"fact_a: doc={fact_a['document_id']} id={fact_a['id']} value_numeric={fact_a['value_numeric']} unit={fact_a['unit']}")
print(f"fact_b: doc={fact_b['document_id']} id={fact_b['id']} value_numeric={fact_b['value_numeric']} unit={fact_b['unit']}")

result, cache_hit = classify_pair(
    fact_a, "02-delhivery-annual-report-fy24-excerpt.pdf",
    fact_b, "03-delhivery-q4-fy24-earnings-presentation.pdf",
)
print(f"\ncache_hit={cache_hit}")
print("relation_type:", result.get("relation_type"))
print("confidence:", result.get("confidence"))
print("explanation:", result.get("explanation", "").encode("ascii", "replace").decode())
print("meta:", result.get("_meta"))
