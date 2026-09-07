"""Directly verify the Case 1 corroboration classification for the two
real, already-extracted facts (annual report fact 214, earnings deck fact
from document 14), independent of whether embedding top-K happened to
shortlist this exact pair automatically."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

db.init_db()

fact_a = db.get_fact(214)
fact_b = next(f for f in db.list_facts(14) if "8,142" in f["statement"] and "grew" not in f["statement"].lower())

result, cache_hit = classify_pair(
    fact_a, "02-delhivery-annual-report-fy24-excerpt.pdf",
    fact_b, "03-delhivery-q4-fy24-earnings-presentation.pdf",
)
print(f"cache_hit={cache_hit}")
print("relation_type:", result.get("relation_type"))
print("confidence:", result.get("confidence"))
print("explanation:", result.get("explanation", "").encode("ascii", "replace").decode())
