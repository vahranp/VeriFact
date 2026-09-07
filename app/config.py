"""
Central configuration, loaded from environment variables / .env.
Nothing here is document-specific -- all knobs are generic so the
pipeline works on any PDF, not just the three starter documents.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# LLM backend. Defaults to a local Ollama server (no API key, no cost, no
# network dependency) -- but this is just config: pointing LLM_BASE_URL at
# OpenRouter/OpenAI/anything else that speaks a compatible chat API and
# setting LLM_API_KEY is the only change needed to swap providers, nothing
# in app/llm_client.py or below is Ollama-specific except the request shape,
# which is isolated in llm_client.py's single call site.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # "ollama" | "openai_compatible"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "llama3.1:8b")
REASONING_MODEL = os.getenv("REASONING_MODEL", "llama3.1:8b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Local model context window / output cap (Ollama-specific tuning; ignored
# by the openai_compatible provider path).
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "storage/facts.db"))
UPLOAD_DIR = BASE_DIR / os.getenv("UPLOAD_DIR", "data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# --- Tunables (all generic, none tied to any specific document) ---

# Max characters of page text sent per extraction call. Pages longer than
# this are split into overlapping windows so very dense/large PDFs don't
# blow the context window or produce truncated JSON.
MAX_CHUNK_CHARS = 6000
CHUNK_OVERLAP_CHARS = 400

# Candidate retrieval for cross-document comparison
SIMILARITY_TOP_K = 4
SIMILARITY_THRESHOLD = 0.45

# Skip pages with near-empty extracted text (e.g. divider pages, pure images)
MIN_PAGE_CHARS = 40
