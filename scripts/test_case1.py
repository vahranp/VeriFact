"""Directly verify the Case 1 corroboration classification: FY24 revenue
stated as ~Rs81,415.38 million (Annual Report) vs. ~Rs8,142 Cr (earnings
deck) -- the same number in a different unit and document.

Looks up BOTH facts by content rather than a hardcoded document/fact id
(this used to hardcode fact_a to id 214, which contradicted the project's
own claim that these scripts were de-hardcoded -- fixed so this script
means what the README says it means), and prefers whichever matching fact
actually has a populated value_numeric -- earlier runs of this exact page
hit a real extraction bug (value_numeric came back null for positive-valued
facts; see app/fact_extraction.py's _numeric_fallback and the README's
case-4 write-up) that a fresh extraction of the same page no longer
reproduces. This keeps the script a meaningful regression check going
forward instead of quietly pinned to a known-stale fact."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

db.init_db()


def _best_match(candidates):
    """Prefer a fact belonging to a successfully completed document, and
    among those, one with a real numeric value -- both may exist in the DB
    from different upload attempts (including failed/partial re-ingests),
    and the point of this script is to test relationship classification,
    not re-litigate extraction quality on a run that didn't finish."""
    done = [f for f in candidates if (db.get_document(f["document_id"]) or {}).get("status") == "done"]
    pool = done or candidates
    with_value = [f for f in pool if f.get("value_numeric") is not None]
    return (with_value or pool)[-1]  # most recently inserted


# Matched on the NUMBER itself (within a tight tolerance), not a text
# substring of the statement -- a substring match once picked up a
# document-44 fact whose STATEMENT mentioned "81,415.38" as a comparative
# aside but whose own value_numeric was a mis-extracted 8,141,538 from a
# failed/partial re-ingest, silently testing the wrong pair.
candidates_a = [
    f for f in db.list_facts()
    if f.get("value_numeric") is not None and abs(f["value_numeric"] - 81415.38) < 1.0
]
assert candidates_a, "no annual-report consolidated revenue fact found (value_numeric ~81415.38) -- has the annual report excerpt been processed?"
fact_a = _best_match(candidates_a)

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
print("decision_source:", result.get("decision_source"))
print("llm_proposal:", result.get("llm_proposal"))
print("disagreement:", result.get("disagreement"), "--", result.get("disagreement_reason"))
print("explanation:", result.get("explanation", "").encode("ascii", "replace").decode())
print("meta:", result.get("_meta"))
