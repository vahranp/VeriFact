"""Same as targeted_extract.py, for the page-68 'Total Equity' line."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fact_extraction import extract_facts_from_chunk
from app.pdf_extract import Chunk, extract_pages

PDF = "data/uploads/3d7393b9d0f44c1db78123a9514168c2_02-delhivery-annual-report-fy24-excerpt.pdf"

pages = dict(extract_pages(PDF))
text68 = pages[68]
idx = text68.lower().index("total equity\n")
window = text68[max(0, idx - 400):idx + 200]
print(f"window length: {len(window)} chars")
print(window.encode("ascii", "replace").decode())

chunk = Chunk(page_number=68, text=window)
t0 = time.perf_counter()
facts, issues, cache_hit = extract_facts_from_chunk(chunk)
dt = time.perf_counter() - t0
print(f"\nduration: {dt:.1f}s  cache_hit={cache_hit}")
print(f"facts: {len(facts)}  issues: {len(issues)}")
for f in facts:
    line = f"- {f['statement']} | grounded: {f['quote_grounded']}"
    print(line.encode("ascii", "replace").decode())
for i in issues:
    print(" issue:", i["issue_type"], i["detail"][:150])
