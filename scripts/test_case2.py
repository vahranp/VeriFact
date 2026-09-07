"""
Directly verify the Case 2 contradiction (net worth vs total equity) gets
classified correctly by the real relationship-classification path, without
waiting on the full slow page-68 pipeline run to reach this exact fact.
Net worth is already a real fact in the DB (document 11); total equity is
built from the already-verified targeted_extract2.py result (same
grounding check, same model call path -- just not yet persisted to a
document since the full-page run is still in flight).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

db.init_db()

net_worth = None
for f in db.list_facts(11):
    if "net worth" in f["statement"].lower():
        net_worth = f
        break
assert net_worth is not None, "net worth fact not found in document 11"

total_equity = {
    "page_number": 68,
    "subject": "Delhivery Limited",
    "attribute": "total equity",
    "value": "91,446.46",
    "unit": "INR million",
    "time_period": "as at March 31, 2024",
    "scope": "consolidated balance sheet",
    "statement": "The total equity of Delhivery Limited as at March 31, 2024 is INR 91,446.46 million.",
    "quote": "Other equity 15 (a) 90,709.67 91,042.65 Total Equity 91,446.46 91,771.37",
    "document_id": 9,
}

result, cache_hit = classify_pair(net_worth, "annual_report.pdf", total_equity, "annual_report.pdf")
print(f"cache_hit={cache_hit}")
print("relation_type:", result.get("relation_type"))
print("confidence:", result.get("confidence"))
print("explanation:", result.get("explanation", "").encode("ascii", "replace").decode())
print("reconciliation_context:", result.get("reconciliation_context"))
