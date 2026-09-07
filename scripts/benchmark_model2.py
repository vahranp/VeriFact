"""Second call to llama3.2:3b (now warm/loaded) to rule out cold-start as
the reason it wasn't faster than llama3.1:8b in benchmark_model.py."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import SYSTEM_PROMPT, _fact_block
from app.llm_client import chat_json

db.init_db()

fact_a = db.get_fact(12)  # Bhiwandi gateway size
fact_b = db.get_fact(13)  # Bengaluru gateway size
doc_a = db.get_document(fact_a["document_id"])["original_name"]
doc_b = db.get_document(fact_b["document_id"])["original_name"]
user_prompt = _fact_block("FACT A", fact_a, doc_a) + "\n\n" + _fact_block("FACT B", fact_b, doc_b)

t0 = time.perf_counter()
result = chat_json("llama3.2:3b", SYSTEM_PROMPT, user_prompt, temperature=0.0, max_tokens=500, timeout=90)
dt = time.perf_counter() - t0
print(f"llama3.2:3b (warm) duration: {dt:.1f}s")
print(f"relation_type: {result.get('relation_type')}  (correct answer: unrelated)")
print(f"explanation: {result.get('explanation', '').encode('ascii', 'replace').decode()}")
