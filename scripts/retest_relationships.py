"""
One-off script: re-runs relationship classification on three fact pairs that
were misclassified under the original prompt (see README, case 4), using the
tightened SYSTEM_PROMPT in app/relationships.py, to confirm the fix actually
changes the outcome rather than just sounding more careful.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

db.init_db()

PAIRS = [
    (27, 28, "Sahil Barua: retire-by-rotation vs eligible-for-reappointment (was: contradicts)"),
    (29, 31, "Independent Directors: declaration vs registration status (was: reconciled)"),
    (12, 13, "Bhiwandi vs Bengaluru gateway sizes (was: reconciled)"),
    (16, 15, "Falcon Autotech 39.34% stake vs Vinculum 10.94% stake -- two different companies, two different deals (was: corroborates)"),
    (20, 12, "'quickly expanded to geographically dispersed locations' vs Bhiwandi gateway size (was: corroborates)"),
    (11, 18, "Sort capacity 7.1M shipments/day vs 'large number of network partners' (was: corroborates)"),
]

for fid_a, fid_b, label in PAIRS:
    fact_a = db.get_fact(fid_a)
    fact_b = db.get_fact(fid_b)
    doc_a = db.get_document(fact_a["document_id"])["original_name"]
    doc_b = db.get_document(fact_b["document_id"])["original_name"]
    result = classify_pair(fact_a, doc_a, fact_b, doc_b)
    print(f"\n=== {label} ===")
    print(f"  new relation_type: {result.get('relation_type')}")
    print(f"  explanation: {result.get('explanation')}")
