"""
End-to-end orchestration for one document: extract -> per-chunk fact
extraction -> embed -> store -> incremental cross-document comparison.
Runs as a FastAPI BackgroundTask so the upload endpoint returns instantly
and the UI can poll document status.

Instrumented end to end (see _log_stats) so every run reports where the
time actually went and how many Ollama calls it took -- added after
profiling showed fact extraction, not PDF parsing or embeddings, dominates
wall-clock time on a local model (see README > Approach > Performance).
"""
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from app import db
from app.config import LLM_CONCURRENCY
from app.embeddings import embed
from app.fact_extraction import extract_facts_from_chunk
from app.pdf_extract import extract_chunks, page_count, parse_page_spec
from app.relationships import build_relationships_for_document


class Stopwatch:
    """Accumulates wall-clock time per named stage. A stage can be entered
    more than once (e.g. "database" is touched from a few places); time
    accumulates rather than being overwritten."""

    def __init__(self):
        self.totals: dict[str, float] = {}

    @contextmanager
    def track(self, stage: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.totals[stage] = self.totals.get(stage, 0.0) + (time.perf_counter() - t0)


def _fmt(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}m {s:.1f}s" if m else f"{s:.1f}s"


def _log_stats(document_name: str, stats: dict):
    lines = [
        f"Document: {document_name}",
        f"  Pages processed: {stats['pages']}",
        f"  Chunks: {stats['chunks']}",
        f"  Facts extracted: {stats['facts']}",
        f"  Embeddings generated: {stats['embeddings']}",
        f"  Candidate relationship pairs: {stats['candidate_pairs']}",
        f"  Relationships stored: {stats['relationships_stored']}",
        f"  Ollama calls: {stats['ollama_calls']} (extraction={stats['extraction_calls']}, "
        f"reasoning={stats['reasoning_calls']}; cache hits avoided "
        f"{stats['extraction_cache_hits'] + stats['reasoning_cache_hits']} more)",
        "  Stage timing:",
        f"    pdf_extraction:        {_fmt(stats['timing']['pdf_extraction'])}",
        f"    fact_extraction (LLM): {_fmt(stats['timing']['fact_extraction'])}",
        f"    embeddings:            {_fmt(stats['timing']['embeddings'])}",
        f"    candidate_retrieval:   {_fmt(stats['timing']['candidate_retrieval'])}",
        f"    relationship_reasoning (LLM): {_fmt(stats['timing']['relationship_reasoning'])}",
        f"    database:              {_fmt(stats['timing']['database'])}",
        f"  Total processing time: {_fmt(stats['timing']['total'])}",
    ]
    print("\n".join(lines))


def process_document(document_id: int, pdf_path: str, max_pages: int | None = None,
                      pages: str | None = None):
    """max_pages: process only pages 1..N (a prefix cap).
    pages: process only this explicit selector, e.g. "1,3,7-10" -- lets a
    large PDF be ingested a targeted slice at a time instead of end to end.
    If both are omitted, the whole document is processed."""
    sw = Stopwatch()
    t_total0 = time.perf_counter()
    try:
        db.update_document(document_id, status="processing")

        with sw.track("pdf_extraction"):
            n_pages = page_count(pdf_path)
            chunks = extract_chunks(pdf_path)
            if pages:
                wanted = parse_page_spec(pages)
                chunks = [c for c in chunks if c.page_number in wanted]
            elif max_pages is not None:
                chunks = [c for c in chunks if c.page_number <= max_pages]

        with sw.track("database"):
            db.update_document(document_id, num_pages=n_pages)
            doc = db.get_document(document_id)
            document_name = doc["original_name"]

        # --- Fact extraction: one LLM call per chunk, at most LLM_CONCURRENCY
        # in flight at once. Each call already asks for every fact on that
        # chunk in one structured response (not one call per fact) -- see
        # SYSTEM_PROMPT in fact_extraction.py. Order is preserved by
        # ThreadPoolExecutor.map, so downstream page/chunk attribution is
        # unaffected by which call happens to finish first.
        extraction_calls = 0
        extraction_cache_hits = 0
        with sw.track("fact_extraction"):
            with ThreadPoolExecutor(max_workers=max(1, LLM_CONCURRENCY)) as pool:
                results = list(pool.map(
                    lambda c: extract_facts_from_chunk(c, document_name), chunks
                ))

        all_statements: list[str] = []
        per_chunk_facts: list[tuple] = []  # (chunk, facts) with issues already logged
        with sw.track("database"):
            for chunk, (facts, issues, cache_hit) in zip(chunks, results):
                if cache_hit:
                    extraction_cache_hits += 1
                else:
                    extraction_calls += 1
                for issue in issues:
                    db.insert_issue(document_id, chunk.page_number, issue["issue_type"],
                                     issue["detail"], issue.get("raw_excerpt", ""))
                if facts:
                    per_chunk_facts.append((chunk, facts))
                    all_statements.extend(f["statement"] for f in facts)

        # --- Embeddings: one batched call across every fact in this
        # document rather than one call per chunk -- sentence-transformers
        # amortizes fixed per-call overhead better over a bigger batch, and
        # it means this stage's cost no longer scales with chunk count.
        with sw.track("embeddings"):
            vectors = embed(all_statements) if all_statements else []

        new_fact_ids: list[int] = []
        with sw.track("database"):
            v_idx = 0
            for chunk, facts in per_chunk_facts:
                for fact in facts:
                    fid = db.insert_fact(document_id, chunk.page_number, fact, vectors[v_idx])
                    new_fact_ids.append(fid)
                    v_idx += 1

        rel_summary = {
            "candidates_checked": 0, "stored": 0, "skipped_unrelated": 0, "errors": 0,
            "llm_calls": 0, "cache_hits": 0,
            "candidate_retrieval_seconds": 0.0, "llm_reasoning_seconds": 0.0,
        }
        if new_fact_ids:
            rel_summary = build_relationships_for_document(document_id, new_fact_ids)
            sw.totals["candidate_retrieval"] = sw.totals.get("candidate_retrieval", 0.0) + rel_summary["candidate_retrieval_seconds"]
            sw.totals["relationship_reasoning"] = sw.totals.get("relationship_reasoning", 0.0) + rel_summary["llm_reasoning_seconds"]

        total_seconds = time.perf_counter() - t_total0
        stats = {
            "pages": len({c.page_number for c in chunks}),
            "chunks": len(chunks),
            "facts": len(new_fact_ids),
            "embeddings": len(vectors),
            "candidate_pairs": rel_summary["candidates_checked"],
            "relationships_stored": rel_summary["stored"],
            "extraction_calls": extraction_calls,
            "extraction_cache_hits": extraction_cache_hits,
            "reasoning_calls": rel_summary["llm_calls"],
            "reasoning_cache_hits": rel_summary["cache_hits"],
            "ollama_calls": extraction_calls + rel_summary["llm_calls"],
            "relationship_errors": rel_summary["errors"],
            "timing": {
                "pdf_extraction": sw.totals.get("pdf_extraction", 0.0),
                "fact_extraction": sw.totals.get("fact_extraction", 0.0),
                "embeddings": sw.totals.get("embeddings", 0.0),
                "candidate_retrieval": sw.totals.get("candidate_retrieval", 0.0),
                "relationship_reasoning": sw.totals.get("relationship_reasoning", 0.0),
                "database": sw.totals.get("database", 0.0),
                "total": total_seconds,
            },
        }
        _log_stats(document_name, stats)

        db.update_document(
            document_id, status="done",
            error_message=(
                f"facts={len(new_fact_ids)} "
                f"relationships_checked={rel_summary['candidates_checked']} "
                f"relationships_stored={rel_summary['stored']}"
            ),
            stats_json=json.dumps(stats),
        )
    except Exception as exc:  # noqa: BLE001 - top-level job boundary, must not crash the server
        db.update_document(document_id, status="failed", error_message=f"{exc}\n{traceback.format_exc()[-1500:]}")
        db.insert_issue(document_id, None, "pipeline_crashed", str(exc), traceback.format_exc()[-1500:])
