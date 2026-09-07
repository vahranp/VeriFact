"""
Follow-up to benchmark_concurrency.py, run fresh (so it actually imports the
now-fixed llm_client.py, which no longer retries a plain timeout) to answer
two questions cleanly:
  1. Does a call that's going to time out now cost ~1x the timeout instead
     of ~2x (the old retry-on-timeout behavior)?
  2. On a set of chunks that mostly succeed, does bounded concurrency (2
     workers) actually reduce wall-clock time, or does it just trade a
     lower total for a higher failure rate (as the first benchmark hinted)?
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import EXTRACTION_MODEL, EXTRACTION_TIMEOUT_SECONDS
from app.fact_extraction import SYSTEM_PROMPT
from app.llm_client import chat_json
from app.pdf_extract import extract_chunks

PDF = "data/uploads/3d7393b9d0f44c1db78123a9514168c2_02-delhivery-annual-report-fy24-excerpt.pdf"

all_chunks = extract_chunks(PDF)
page22 = [c for c in all_chunks if c.page_number == 22]
page24 = [c for c in all_chunks if c.page_number == 24]
print(f"page 22 has {len(page22)} chunks, page 24 has {len(page24)} chunks")
print(f"EXTRACTION_TIMEOUT_SECONDS = {EXTRACTION_TIMEOUT_SECONDS}")


def call_one(chunk):
    user_prompt = f"Document: bench\nPage: {chunk.page_number}\n\nPAGE TEXT:\n\"\"\"\n{chunk.text}\n\"\"\""
    t0 = time.perf_counter()
    try:
        chat_json(EXTRACTION_MODEL, SYSTEM_PROMPT, user_prompt, temperature=0.0, max_tokens=3000,
                  timeout=EXTRACTION_TIMEOUT_SECONDS)
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  chunk page {chunk.page_number} failed: {exc}")
    return time.perf_counter() - t0, ok


print("\n--- Q1: cost of the known-slow page-22 chunk[0] alone, under the fixed (no-retry-on-timeout) client ---")
d, ok = call_one(page22[0])
print(f"duration={d:.1f}s ok={ok}  (old behavior would have been ~2x the {EXTRACTION_TIMEOUT_SECONDS}s timeout if it fails)")

print("\n--- Q2: sequential vs concurrent(2) on page 24's chunks (shorter, prose, expected mostly-fast) ---")
chunks = page24[:4] if len(page24) >= 2 else (page24 + page22[1:3])
print(f"using {len(chunks)} chunks from pages {sorted({c.page_number for c in chunks})}")

t0 = time.perf_counter()
seq = [call_one(c) for c in chunks]
seq_total = time.perf_counter() - t0
print(f"sequential durations: {[round(d,1) for d,_ in seq]}  ok={[ok for _,ok in seq]}")
print(f"TOTAL sequential: {seq_total:.1f}s")

t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=2) as pool:
    conc = list(pool.map(call_one, chunks))
conc_total = time.perf_counter() - t0
print(f"concurrent(2) durations: {[round(d,1) for d,_ in conc]}  ok={[ok for _,ok in conc]}")
print(f"TOTAL concurrent(2): {conc_total:.1f}s")

seq_failures = sum(1 for _, ok in seq if not ok)
conc_failures = sum(1 for _, ok in conc if not ok)
print(f"\n=== sequential: {seq_total:.1f}s, {seq_failures} failures | concurrent(2): {conc_total:.1f}s, {conc_failures} failures ===")
