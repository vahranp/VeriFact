"""
Step 8: is llama3.1:8b (current default) appropriate, or would a smaller
model handle the (lighter) relationship-reasoning task adequately and
faster? Benchmarks llama3.2:3b head-to-head on the same real fact pair and
prompt, measuring both latency and whether it reaches the same (correct)
classification.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import SYSTEM_PROMPT, _fact_block
from app.llm_client import chat_json

db.init_db()

fact_a = db.get_fact(27)  # Sahil Barua: retire by rotation
fact_b = db.get_fact(28)  # Sahil Barua: eligible for reappointment
doc_a = db.get_document(fact_a["document_id"])["original_name"]
doc_b = db.get_document(fact_b["document_id"])["original_name"]

user_prompt = _fact_block("FACT A", fact_a, doc_a) + "\n\n" + _fact_block("FACT B", fact_b, doc_b)

for model in ["llama3.1:8b", "llama3.2:3b"]:
    t0 = time.perf_counter()
    try:
        result = chat_json(model, SYSTEM_PROMPT, user_prompt, temperature=0.0, max_tokens=500, timeout=90)
        dt = time.perf_counter() - t0
        print(f"\n=== {model} ===")
        print(f"duration: {dt:.1f}s")
        print(f"relation_type: {result.get('relation_type')}  (correct answer: unrelated)")
        print(f"explanation: {result.get('explanation', '').encode('ascii', 'replace').decode()}")
    except Exception as exc:  # noqa: BLE001
        dt = time.perf_counter() - t0
        print(f"\n=== {model} ===")
        print(f"duration: {dt:.1f}s  FAILED: {exc}")
