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
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

from app import db
from app.arithmetic import check_arithmetic_consistency
from app.coherence import check_coherence
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
        f"  Arithmetic self-validation: {stats.get('arithmetic_identities', 0)} identities confirmed, "
        f"{stats.get('arithmetic_anomalies', 0)} scale anomalies flagged",
        f"  Graph coherence: {stats.get('coherence_violations', 0)} impossible triangles of "
        f"{stats.get('coherence_triangles', 0)} checked, "
        f"{stats.get('coherence_inferences', 0)} edges inferable by transitivity",
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
        # SYSTEM_PROMPT in fact_extraction.py. Submitted via as_completed
        # (not pool.map) specifically so progress can be reported as each
        # chunk finishes, not only once the whole batch is done; results are
        # written back into a pre-sized list by original index so downstream
        # page/chunk attribution is unaffected by completion order.
        extraction_calls = 0
        extraction_cache_hits = 0
        total_chunks = len(chunks)
        results: list = [None] * total_chunks
        db.set_progress(document_id, "extracting", 0, total_chunks, None)
        with sw.track("fact_extraction"):
            with ThreadPoolExecutor(max_workers=max(1, LLM_CONCURRENCY)) as pool:
                future_to_idx = {
                    pool.submit(extract_facts_from_chunk, c, document_name): i
                    for i, c in enumerate(chunks)
                }
                done_count = 0
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    results[idx] = future.result()
                    done_count += 1
                    db.set_progress(document_id, "extracting", done_count, total_chunks,
                                     f"page {chunks[idx].page_number}")

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
        db.set_progress(document_id, "embedding", 0, 1, None)
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

        # --- Arithmetic self-validation (deterministic, no LLM call).
        # Looks for additive identities among this document's own numbers
        # (a total equalling the sum of its parts, etc.) as independent
        # evidence the figures were read correctly, and flags near-misses
        # that only resolve if a value is rescaled by a power of ten --
        # the signature of a denomination/unit misread. See app/arithmetic.py.
        arithmetic_report = None
        with sw.track("arithmetic"):
            all_new_facts = [f for _, facts in per_chunk_facts for f in facts]
            arithmetic_report = check_arithmetic_consistency(all_new_facts)
        with sw.track("database"):
            for anomaly in arithmetic_report.scale_anomalies:
                db.insert_issue(
                    document_id, None, "arithmetic_scale_anomaly",
                    anomaly.describe(), f"suspect value: {anomaly.suspect_fact.get('value')}",
                )

        rel_summary = {
            "candidates_checked": 0, "stored": 0, "skipped_unrelated": 0, "errors": 0,
            "llm_calls": 0, "cache_hits": 0,
            "candidate_retrieval_seconds": 0.0, "llm_reasoning_seconds": 0.0,
        }
        if new_fact_ids:
            rel_summary = build_relationships_for_document(document_id, new_fact_ids)
            sw.totals["candidate_retrieval"] = sw.totals.get("candidate_retrieval", 0.0) + rel_summary["candidate_retrieval_seconds"]
            sw.totals["relationship_reasoning"] = sw.totals.get("relationship_reasoning", 0.0) + rel_summary["llm_reasoning_seconds"]

        # --- Graph coherence (deterministic, no LLM call). Pairwise
        # judgments are made in isolation, so the graph they form can be
        # internally impossible: A=B and B=C while A!=C. Those triangles
        # prove at least one judgment is wrong without any ground truth,
        # and the same transitivity fills in edges retrieval never
        # shortlisted. See app/coherence.py.
        coherence = None
        with sw.track("coherence"):
            all_rels = db.list_relationships()
            facts_by_id = {f["id"]: f for f in db.list_facts()}
            coherence = check_coherence(all_rels, facts_by_id=facts_by_id)
        with sw.track("database"):
            for violation in coherence.violations[:50]:
                db.insert_issue(
                    document_id, None, "graph_incoherence",
                    violation.describe(), violation.reason,
                )

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
            "arithmetic_identities": len(arithmetic_report.identities) if arithmetic_report else 0,
            "arithmetic_anomalies": len(arithmetic_report.scale_anomalies) if arithmetic_report else 0,
            "coherence_triangles": coherence.triangles_checked if coherence else 0,
            "coherence_violations": len(coherence.violations) if coherence else 0,
            "coherence_inferences": len(coherence.inferences) if coherence else 0,
            "timing": {
                "pdf_extraction": sw.totals.get("pdf_extraction", 0.0),
                "fact_extraction": sw.totals.get("fact_extraction", 0.0),
                "embeddings": sw.totals.get("embeddings", 0.0),
                "candidate_retrieval": sw.totals.get("candidate_retrieval", 0.0),
                "relationship_reasoning": sw.totals.get("relationship_reasoning", 0.0),
                "arithmetic": sw.totals.get("arithmetic", 0.0),
                "coherence": sw.totals.get("coherence", 0.0),
                "database": sw.totals.get("database", 0.0),
                "total": total_seconds,
            },
        }
        _log_stats(document_name, stats)

        db.clear_progress(document_id)
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
        db.clear_progress(document_id)
        db.update_document(document_id, status="failed", error_message=f"{exc}\n{traceback.format_exc()[-1500:]}")
        db.insert_issue(document_id, None, "pipeline_crashed", str(exc), traceback.format_exc()[-1500:])
