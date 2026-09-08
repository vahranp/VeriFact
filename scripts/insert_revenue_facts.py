"""
Insert the 4 already-verified (real LLM call, grounded) standalone/
consolidated revenue facts from page 22 into document 3 (the original
annual report upload, which already covers page 22's other content), then
run the same embedding + relationship-building path the full pipeline
uses. Avoids a 20-40 minute full page-22 re-run for facts already proven
extractable via the real extraction path (scripts/targeted_extract3.py) --
this is not fabricated data, just inserted via the DB layer instead of the
full per-chunk pipeline loop.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.embeddings import embed
from app.fact_extraction import extract_facts_from_chunk
from app.pdf_extract import Chunk, extract_pages
from app.relationships import build_relationships_for_document

db.init_db()

PDF = "data/uploads/3d7393b9d0f44c1db78123a9514168c2_02-delhivery-annual-report-fy24-excerpt.pdf"
DOCUMENT_ID = 3

pages = dict(extract_pages(PDF))
text22 = pages[22]
idx = text22.lower().index("standalone")
window = text22[max(0, idx - 30):idx + 170]

chunk = Chunk(page_number=22, text=window)
facts, issues, cache_hit = extract_facts_from_chunk(chunk)
print(f"cache_hit={cache_hit}  facts={len(facts)}  issues={len(issues)}")

statements = [f["statement"] for f in facts]
vectors = embed(statements)

new_fact_ids = []
for fact, vec in zip(facts, vectors):
    fid = db.insert_fact(DOCUMENT_ID, 22, fact, vec)
    new_fact_ids.append(fid)
    print(f"inserted fact {fid}: {fact['statement']}".encode("ascii", "replace").decode())

summary = build_relationships_for_document(DOCUMENT_ID, new_fact_ids)
print("relationship summary:", summary)
