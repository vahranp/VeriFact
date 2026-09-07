"""
One-off benchmark: does issuing multiple Ollama requests concurrently
actually finish a fixed batch of work faster than doing them one at a time
on this machine, or does a single CPU/GPU-split model instance just end up
serializing them anyway (with added contention overhead)? Answers this with
a real timed run instead of assuming either way -- see README > Approach >
Performance.
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import EXTRACTION_MODEL
from app.fact_extraction import SYSTEM_PROMPT
from app.llm_client import chat_json
from app.pdf_extract import extract_chunks

PDF = "data/uploads/3d7393b9d0f44c1db78123a9514168c2_02-delhivery-annual-report-fy24-excerpt.pdf"
PAGES_WANTED = {22, 24, 52}  # a mix of prose and dense-table pages, real content

chunks = [c for c in extract_chunks(PDF) if c.page_number in PAGES_WANTED][:4]
print(f"Using {len(chunks)} real chunks from pages {sorted({c.page_number for c in chunks})}")


def call_one(chunk):
    user_prompt = f"Document: bench\nPage: {chunk.page_number}\n\nPAGE TEXT:\n\"\"\"\n{chunk.text}\n\"\"\""
    t0 = time.perf_counter()
    try:
        chat_json(EXTRACTION_MODEL, SYSTEM_PROMPT, user_prompt, temperature=0.0, max_tokens=3000, timeout=180)
        ok = True
    except Exception as exc:  # noqa: BLE001 - benchmark only, want to keep going and report
        ok = False
        print(f"  chunk page {chunk.page_number} failed: {exc}")
    return time.perf_counter() - t0, ok


print("\n--- Sequential (concurrency=1) ---")
t0 = time.perf_counter()
seq_durations = [call_one(c) for c in chunks]
seq_total = time.perf_counter() - t0
print(f"per-call durations: {[round(d, 1) for d, _ in seq_durations]}")
print(f"TOTAL sequential wall time: {seq_total:.1f}s")

print("\n--- Concurrent (2 workers) ---")
t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=2) as pool:
    conc_durations = list(pool.map(call_one, chunks))
conc_total = time.perf_counter() - t0
print(f"per-call durations: {[round(d, 1) for d, _ in conc_durations]}")
print(f"TOTAL concurrent(2) wall time: {conc_total:.1f}s")

print(f"\n=== RESULT: sequential={seq_total:.1f}s  concurrent(2)={conc_total:.1f}s  "
      f"=> {'concurrency helped' if conc_total < seq_total * 0.9 else 'no meaningful benefit / worse'} ===")
