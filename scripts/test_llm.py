"""Quick smoke test: confirms the OpenRouter key + model names actually
work before we build the rest of the pipeline on top of them."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import EXTRACTION_MODEL, REASONING_MODEL
from app.llm_client import chat_json

if __name__ == "__main__":
    print(f"Testing EXTRACTION_MODEL = {EXTRACTION_MODEL}")
    out = chat_json(
        EXTRACTION_MODEL,
        system="Respond only with JSON.",
        user='Return this exact JSON: {"ok": true, "model_role": "extraction"}',
    )
    print("  ->", out)

    print(f"Testing REASONING_MODEL = {REASONING_MODEL}")
    out = chat_json(
        REASONING_MODEL,
        system="Respond only with JSON.",
        user='Return this exact JSON: {"ok": true, "model_role": "reasoning"}',
        max_tokens=200,
    )
    print("  ->", out)

    print("\nBoth models responded successfully.")
