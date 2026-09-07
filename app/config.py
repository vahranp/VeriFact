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

# --- Tunables (all generic, none tied to any specific document; every one
# is overridable via env var so a different machine/deployment/document mix
# doesn't require a code change) ---

# Max characters of page text sent per extraction call. Pages longer than
# this are split into overlapping windows so very dense/large PDFs don't
# blow the context window or produce truncated JSON. What actually drives
# local-model decode time isn't character count so much as *how many
# distinct facts* are packed into the chunk -- a financial/CSR table (one
# number per line, dozens of line items) is far denser per character than
# prose. Measured directly on one specific stubborn chunk (see README >
# Approach > Performance): a ~1940-char slice of it reliably timed out
# across five different timeout/concurrency configurations, while a
# ~600-char window of the *same underlying text* completed cleanly in 68s.
# 1200 (down from 2000) is chosen to sit safely under that observed
# cliff, not as a round-number guess.
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", "1200"))
CHUNK_OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", "150"))

# Candidate retrieval for cross-document comparison. Not tuned to the
# starter documents -- SIMILARITY_THRESHOLD in particular is the knob a new
# document set would most likely need to revisit (denser/sparser factual
# overlap between documents shifts what a "related" cosine score looks
# like), so it's an env var rather than a constant buried in code.
#
# 0.40 (down from an initial 0.45) is informed by a real measured gap, not
# a guess: "The company's net worth is [X] million" vs "The total equity
# of Delhivery is INR [Y]" -- two facts that ARE the same underlying
# accounting concept (net worth IS total equity) and genuinely disagree in
# value -- score 0.4096 cosine similarity with this project's embedding
# model. At 0.45 that pair is silently never shown to the reasoning model
# at all, regardless of how good the reasoning prompt is. This is a real,
# demonstrated limitation of embedding-similarity-based candidate
# shortlisting: two facts can be the *same claim* without being
# *similarly worded*, and general-purpose sentence embeddings don't always
# capture domain-specific synonymy (net worth / total equity / shareholders'
# equity) as closely as a human reader would. Lowering the threshold trades
# more (cheap, local) candidates for a better chance of catching this kind
# of pair -- it does not by itself guarantee the reasoning model then
# classifies it correctly (see README > Approach > Performance / case 4).
SIMILARITY_TOP_K = int(os.getenv("SIMILARITY_TOP_K", "4"))
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.40"))

# Skip pages with near-empty extracted text (e.g. divider pages, pure images)
MIN_PAGE_CHARS = int(os.getenv("MIN_PAGE_CHARS", "40"))

# --- Performance tunables added after profiling (see README > Approach >
# Performance) ---

# Separate timeouts for extraction (long, structured, can legitimately take
# minutes on a dense table on a CPU-bound local model) vs. relationship
# reasoning (short output, two facts of context -- should never need
# anywhere near as long; a stuck reasoning call is more likely hung than
# genuinely working).
#
# 180s was tried first (tighter than the original 240s) and measurably cut
# wasted time on chunks that were never going to finish -- but real runs
# on the two densest pages (financial tables) showed some legitimate
# extraction calls need more than 180s to actually finish, not just fail
# faster (see README > Approach > Performance): at 180s those chunks were
# cut off before finishing; at 240s (this value, matching what was already
# known to work for this content earlier in development) they complete.
# The timeout-not-retried fix from this same pass means a *genuinely*
# doomed call still only costs 1x this value instead of 2x, so raising it
# back up doesn't reintroduce the original waste -- it just gives real
# work enough room to land.
EXTRACTION_TIMEOUT_SECONDS = float(os.getenv("EXTRACTION_TIMEOUT_SECONDS", "240"))
REASONING_TIMEOUT_SECONDS = float(os.getenv("REASONING_TIMEOUT_SECONDS", "60"))

# How many LLM calls this process will have in flight at once. Benchmarked,
# not assumed, and revised once (see README > Approach > Performance,
# scripts/benchmark_*.py) after later evidence overturned the first
# conclusion. On isolated prose chunks, concurrency=2 measured ~30% faster
# than sequential with no extra failures. But on the two densest,
# closest-to-the-timeout-ceiling pages in the whole document (financial
# tables, pages 52+68 -- exactly the content Case 2's contradiction
# depends on), concurrency=2 caused ALL 6 chunks to time out (0 facts
# extracted) in a clean run with nothing else competing for the machine;
# re-running the identical pages sequentially recovered real extraction.
# Read: concurrency's safety is workload-dependent -- fine on chunks with
# comfortable timeout margin, actively harmful on chunks already close to
# it, because contention is enough to push a borderline call over the
# edge. Given the assignment's own priority order (correctness first),
# default is 1 (fully sequential); raise it via env var only on hardware
# with enough spare capacity that dense chunks aren't already near the
# timeout ceiling.
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "1"))
