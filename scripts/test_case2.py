"""Directly verify the Case 2 contradiction classification: BRSR net worth
(Rs85,466.74 million) vs. Consolidated Balance Sheet total equity
(Rs91,446.46 million) -- for a company these are the same accounting
concept and should agree exactly; they don't, by ~6.5%.

Both facts are now real, DB-backed extractions (document 11's net worth,
document 12's total equity) -- an earlier version of this script used a
hand-built stand-in dict for total equity because that fact hadn't been
extracted yet at the time; now that it has, this tests the real stored
data instead of a synthetic substitute."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

db.init_db()

nw_candidates = [
    f for f in db.list_facts()
    if "net worth" in (f.get("statement") or "").lower() and f.get("value_numeric") is not None
]
assert nw_candidates, "no net worth fact with a populated value_numeric found"
net_worth = nw_candidates[-1]  # most recently inserted

candidates = [
    f for f in db.list_facts()
    if (f.get("attribute") or "").strip().lower() == "total equity" and f.get("value_numeric") is not None
]
assert candidates, "no fact with attribute exactly 'total equity' and a populated value_numeric found"
total_equity = candidates[-1]  # most recently inserted, in case of re-extractions

def _safe(line):
    print(line.encode("ascii", "replace").decode())


_safe(f"net_worth:    doc={net_worth['document_id']} id={net_worth['id']} value_numeric={net_worth['value_numeric']} unit={net_worth['unit']}")
_safe(f"total_equity: doc={total_equity['document_id']} id={total_equity['id']} value_numeric={total_equity['value_numeric']} unit={total_equity['unit']}")

result, cache_hit = classify_pair(
    net_worth, "annual_report.pdf",
    total_equity, "annual_report.pdf",
)
print(f"\ncache_hit={cache_hit}")
print("relation_type:", result.get("relation_type"))
print("confidence:", result.get("confidence"))
print("explanation:", result.get("explanation", "").encode("ascii", "replace").decode())
print("reconciliation_context:", result.get("reconciliation_context"))
print("meta:", result.get("_meta"))
