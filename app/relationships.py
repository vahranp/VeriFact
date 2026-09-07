"""
Cross-document (and cross-page, same-document) reasoning. Candidate pairs
come from embedding similarity (cheap, local); only candidates above the
threshold get an actual LLM call, and only *new* facts get compared at
all -- this is what makes ingesting document N+1 not re-scan documents
1..N against each other.

Classification is deliberately split into two LLM calls instead of one
combined judgment (see classify_pair below) after direct testing showed a
single-call approach reliably fails at exactly the point where the two
sub-questions interact: a model can correctly recognize "net worth" and
"total equity" as the same accounting concept, and separately can do
unit conversion when asked directly, but asked to do BOTH inside one
judgment it sometimes recognizes the concepts as equivalent and then
fails to notice the values actually disagree. Splitting "are these the
same metric?" from "given a deterministic value comparison, how do they
relate?" -- and handing the second step a normalized comparison computed
in code (app/normalize.py) rather than asking it to convert crore to
million itself -- removes both failure points from a single call.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app import db
from app.cache import hash_fact_pair, hash_text
from app.config import (
    LLM_CONCURRENCY, REASONING_MODEL, REASONING_TIMEOUT_SECONDS,
    SIMILARITY_TOP_K, SIMILARITY_THRESHOLD,
)
from app.embeddings import top_k_similar
from app.llm_client import chat_json, LLMError, LLMParseError
from app.normalize import compare_values, format_comparison_for_prompt

# ---------------------------------------------------------------- step 1 --

SYSTEM_PROMPT_METRIC = """You are given two facts extracted from documents. Decide ONLY whether they \
are asserting something about the exact same underlying real-world metric or attribute -- NOT \
whether their values agree or disagree. That numeric comparison, if relevant, happens in a \
separate step after this one, so do not consider the values at all here.

Judge "same metric" by real-world meaning, not by whether the attribute labels are worded \
identically: business and accounting terminology routinely uses several different words for \
one concept depending on the document/author (e.g. "net worth" = "total equity" = \
"shareholders' equity"; "revenue" = "revenue from operations" = "revenue from services" = \
"turnover" = "sales", especially for a company whose core business already IS providing a \
service; "gross merchandise value" = "GMV"). When two facts' attributes could plausibly be \
different names for the same metric, answer yes.

But two facts about the same person, company, or subject are NOT automatically the same metric \
just because they share a subject -- check whether they measure the SAME aspect of that \
subject, not merely mention it. For example, "liable to retire by rotation" and "eligible for \
re-appointment" are two DIFFERENT attributes of one person's director status (routine, \
sequential steps of the same governance process, not the same measurement) -- answer no. \
Likewise "declaration of independence" and "registration in a director databank" are two \
different regulatory requirements, not the same metric -- answer no. And two facts about \
physically different things (a warehouse in one city vs. a warehouse in another city; one \
company's stake in one investee vs. its stake in a different investee) are not the same metric \
merely because they share a similar sentence structure -- answer no.

Respond with ONLY this JSON object:
{
  "same_metric": true | false,
  "reason": "<one sentence: what specific metric they share, or why they differ>"
}"""

# ---------------------------------------------------------------- step 2 --

SYSTEM_PROMPT_JUDGE = """Two facts have already been confirmed to describe the SAME underlying \
metric or attribute -- your job now is only to decide HOW they relate: do the stated values \
agree, disagree without explanation, or disagree for a reconcilable reason (different time \
period, scope, or unit)?

A deterministic numeric comparison is provided below whenever both facts had a parseable value \
and unit -- trust it over your own arithmetic; do not recompute or second-guess the unit \
conversion it already did. If it reports the comparison was not possible (e.g. a \
qualitative/status fact, or units that don't reduce to a common base), judge agreement from the \
statements and quotes directly instead.

Decide exactly one relation_type:
- "corroborates": the values agree (directly, or per the normalized comparison below) for the \
same time period and scope -- or, for non-numeric facts, the statements assert the same status.
- "contradicts": the values disagree (directly, or per the normalized comparison below), AND \
the time period, scope, and unit are the same or not distinguishing -- there is no stated reason \
the numbers should differ. Flag this even at moderate confidence rather than defaulting away \
from it once a real, unexplained disagreement is in front of you.
- "reconciled": the values disagree, BUT a difference in time period, scope (e.g. standalone \
vs. consolidated, before vs. after an acquisition), or definition explains the gap. You MUST \
name the specific reconciling context.
- "unrelated": on reflection the two facts don't actually support a comparison after all (e.g. \
no numeric comparison was possible and the statements are too different to judge qualitatively).

Respond with ONLY this JSON object:
{
  "relation_type": "corroborates" | "contradicts" | "reconciled" | "unrelated",
  "explanation": "<1-3 sentences citing the specific values, the normalized comparison, or the reconciling context>",
  "reconciliation_context": "<required if reconciled, else null>",
  "confidence": <0.0-1.0>
}"""


def _fact_block(label: str, fact: dict, document_name: str) -> str:
    return (
        f"{label}:\n"
        f"  document: {document_name}, page {fact['page_number']}\n"
        f"  statement: {fact['statement']}\n"
        f"  subject: {fact.get('subject')}\n"
        f"  attribute: {fact.get('attribute')}\n"
        f"  value: {fact.get('value')} {fact.get('unit') or ''}\n"
        f"  time_period: {fact.get('time_period')}\n"
        f"  scope: {fact.get('scope')}\n"
        f"  supporting quote: \"{fact['quote']}\""
    )


def classify_metric_match(fact_a: dict, doc_a_name: str, fact_b: dict, doc_b_name: str) -> tuple[dict, bool]:
    """Step 1: do these two facts denote the same real-world metric? Cached
    separately from step 2 (different prompt, different question) under
    its own hash namespace, so it survives independently of whatever the
    final judge prompt does."""
    pair_hash = hash_text(REASONING_MODEL, "step1_metric", SYSTEM_PROMPT_METRIC, hash_fact_pair(fact_a, fact_b))
    cached = db.get_cached_relationship(pair_hash)
    if cached is not None:
        return cached, True

    user_prompt = _fact_block("FACT A", fact_a, doc_a_name) + "\n\n" + _fact_block("FACT B", fact_b, doc_b_name)
    result = chat_json(REASONING_MODEL, SYSTEM_PROMPT_METRIC, user_prompt, temperature=0.0,
                        max_tokens=250, timeout=REASONING_TIMEOUT_SECONDS)
    db.set_cached_relationship(pair_hash, REASONING_MODEL, result)
    return result, False


def classify_relation(fact_a: dict, doc_a_name: str, fact_b: dict, doc_b_name: str,
                       comparison_line: str) -> tuple[dict, bool]:
    """Step 2: given the two facts already share a metric, and given a
    deterministic normalized-value comparison computed in code (not by the
    model), decide corroborates / contradicts / reconciled / unrelated.
    The comparison line is part of the cache key so a normalize.py bugfix
    that changes the computed comparison can't silently serve a stale
    judgment made under the old (wrong) comparison."""
    pair_hash = hash_text(REASONING_MODEL, "step2_judge", SYSTEM_PROMPT_JUDGE,
                           hash_fact_pair(fact_a, fact_b), comparison_line)
    cached = db.get_cached_relationship(pair_hash)
    if cached is not None:
        return cached, True

    user_prompt = (
        _fact_block("FACT A", fact_a, doc_a_name) + "\n\n" +
        _fact_block("FACT B", fact_b, doc_b_name) + "\n\n" +
        comparison_line
    )
    result = chat_json(REASONING_MODEL, SYSTEM_PROMPT_JUDGE, user_prompt, temperature=0.0,
                        max_tokens=400, timeout=REASONING_TIMEOUT_SECONDS)
    db.set_cached_relationship(pair_hash, REASONING_MODEL, result)
    return result, False


def classify_pair(fact_a: dict, doc_a_name: str, fact_b: dict, doc_b_name: str) -> tuple[dict, bool]:
    """Public entry point, kept at the same (result, cache_hit) shape as
    the original single-call version so existing callers -- including
    scripts/test_case1.py, test_case2.py, and retest_relationships.py --
    don't need to change to serve as before/after regression checks
    across this exact refactor.

    Internally runs up to two LLM calls (see module docstring for why).
    Step 2 is skipped entirely when step 1 says the facts aren't the same
    metric -- the common case (most candidate pairs are unrelated), so
    this doesn't double the call count for pairs that were never going to
    match anyway. `result["_meta"]` carries granular step/call-count
    information for the caller's stats (see build_relationships_for_document)
    without changing the public field shape relation_type/explanation/
    reconciliation_context/confidence that callers already expect."""
    metric_result, metric_cached = classify_metric_match(fact_a, doc_a_name, fact_b, doc_b_name)

    if not metric_result.get("same_metric"):
        result = {
            "relation_type": "unrelated",
            "explanation": metric_result.get("reason") or "The two facts do not describe the same underlying metric.",
            "reconciliation_context": None,
            "confidence": metric_result.get("confidence"),
            "_meta": {"steps_run": 1, "llm_calls": 0 if metric_cached else 1, "same_metric": False},
        }
        return result, metric_cached

    comparison = compare_values(
        fact_a.get("value_numeric"), fact_a.get("unit"),
        fact_b.get("value_numeric"), fact_b.get("unit"),
    )
    comparison_line = format_comparison_for_prompt(comparison)
    judge_result, judge_cached = classify_relation(fact_a, doc_a_name, fact_b, doc_b_name, comparison_line)

    result = dict(judge_result)
    result["_meta"] = {
        "steps_run": 2,
        "llm_calls": (0 if metric_cached else 1) + (0 if judge_cached else 1),
        "same_metric": True,
        "comparison": comparison_line,
    }
    return result, (metric_cached and judge_cached)


def build_relationships_for_document(document_id: int, new_fact_ids: list[int]) -> dict:
    """Compares every newly-inserted fact against (a) the embedding pool of
    all facts from *other* documents already in the DB, and (b) the other
    new facts in this same batch (so within-document tensions like
    standalone-vs-consolidated numbers in one filing are still caught).

    Candidate shortlisting (cheap, local, numpy) and the DB reads/writes
    all happen on the main thread; only the actual LLM calls -- the slow,
    independent part -- run in a bounded thread pool (LLM_CONCURRENCY),
    which keeps SQLite access single-threaded and simple (see app/db.py)
    while still letting slow network calls overlap when the hardware
    supports it. Returns a summary dict used both for the API response and
    for the per-document performance report (see app/pipeline.py)."""
    t0 = time.perf_counter()
    prior_pool = db.get_all_embeddings(exclude_document_id=document_id)
    new_facts = [db.get_fact(fid) for fid in new_fact_ids]
    new_pool = [(f["id"], _load_embedding(f)) for f in new_facts]
    full_pool = prior_pool + new_pool

    doc_name_cache: dict[int, str] = {}

    def doc_name(doc_id: int) -> str:
        if doc_id not in doc_name_cache:
            d = db.get_document(doc_id)
            doc_name_cache[doc_id] = d["original_name"] if d else f"document {doc_id}"
        return doc_name_cache[doc_id]

    # Build the full candidate-pair job list up front (pure local
    # computation: embedding similarity + a relationship_exists lookup per
    # candidate) before any LLM call is made. seen_pairs guards against a
    # pair being queued twice -- e.g. two new facts in the same batch that
    # each rank each other in their own top-K -- which the old code
    # avoided for free by inserting into the DB immediately after each
    # comparison (so the second lookup would already see it); deferring all
    # inserts until after the concurrent LLM calls below means that guard
    # has to be explicit instead.
    seen_pairs: set[tuple[int, int]] = set()
    jobs = []
    for fact, (fid, emb) in zip(new_facts, new_pool):
        candidates = [c for c in full_pool if c[0] != fid]
        top = top_k_similar(emb, candidates, SIMILARITY_TOP_K)
        for other_id, score in top:
            if score < SIMILARITY_THRESHOLD:
                continue
            pair_key = (min(fid, other_id), max(fid, other_id))
            if pair_key in seen_pairs or db.relationship_exists(fid, other_id):
                continue
            other = db.get_fact(other_id)
            if other is None:
                continue
            seen_pairs.add(pair_key)
            jobs.append((fact, other, score))
    candidate_retrieval_seconds = time.perf_counter() - t0

    summary = {
        "candidates_checked": len(jobs), "stored": 0, "skipped_unrelated": 0, "errors": 0,
        "llm_calls": 0, "cache_hits": 0,
        "metric_mismatches": 0,  # step 1 said "no" -- resolved without ever reaching step 2
        "candidate_retrieval_seconds": round(candidate_retrieval_seconds, 3),
        "llm_reasoning_seconds": 0.0,
    }
    if not jobs:
        return summary

    def run_job(job):
        fact, other, score = job
        try:
            result, cache_hit = classify_pair(
                fact, doc_name(fact["document_id"]),
                other, doc_name(other["document_id"]),
            )
            return job, result, cache_hit, None
        except (LLMError, LLMParseError) as exc:
            return job, None, False, exc

    t0 = time.perf_counter()
    total_jobs = len(jobs)
    results: list = [None] * total_jobs
    db.set_progress(document_id, "comparing", 0, total_jobs, None)
    with ThreadPoolExecutor(max_workers=max(1, LLM_CONCURRENCY)) as pool:
        future_to_idx = {pool.submit(run_job, j): i for i, j in enumerate(jobs)}
        done_count = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            done_count += 1
            db.set_progress(document_id, "comparing", done_count, total_jobs, None)
    summary["llm_reasoning_seconds"] = round(time.perf_counter() - t0, 3)

    for job, result, cache_hit, exc in results:
        fact, other, score = job

        if exc is not None:
            db.insert_issue(document_id, fact["page_number"], "relationship_classification_failed",
                             f"Comparing fact {fact['id']} vs {other['id']}: {exc}", "")
            summary["errors"] += 1
            continue

        meta = result.pop("_meta", {})
        summary["llm_calls"] += meta.get("llm_calls", 0 if cache_hit else 1)
        if cache_hit:
            summary["cache_hits"] += 1
        if meta.get("same_metric") is False:
            summary["metric_mismatches"] += 1

        relation = result.get("relation_type")
        if relation not in ("corroborates", "contradicts", "reconciled"):
            summary["skipped_unrelated"] += 1
            continue

        db.insert_relationship(
            fact["id"], other["id"], relation,
            result.get("explanation", ""),
            result.get("reconciliation_context"),
            result.get("confidence"),
            score,
        )
        summary["stored"] += 1

    return summary


def _load_embedding(fact_row: dict) -> list[float]:
    import json
    return json.loads(fact_row["embedding_json"])
