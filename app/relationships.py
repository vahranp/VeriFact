"""
Cross-document (and cross-page, same-document) reasoning. Candidate pairs
come from embedding similarity (cheap, local); only candidates above the
threshold get an actual LLM call, and only *new* facts get compared at
all -- this is what makes ingesting document N+1 not re-scan documents
1..N against each other.
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

SYSTEM_PROMPT = """You compare two facts, each extracted from a document, to decide how they \
relate to each other. Both facts are given with their supporting quote, source document, \
page, time period and scope (any of which may be unknown/null).

Default to "unrelated" unless the two facts are clearly asserting something about the exact \
same real-world claim (same subject, same underlying attribute/metric/status). Judge \
"same underlying attribute" by real-world meaning, not by whether the attribute labels are \
worded identically: business and accounting terminology routinely uses several different \
words for one concept depending on the document/author (e.g. "net worth" = "total equity" = \
"shareholders' equity"; "revenue" = "revenue from operations" = "revenue from services" = \
"turnover" = "sales", especially for a company whose core business already IS providing a \
service; "gross merchandise value" = "GMV"). When two facts' attributes could plausibly be \
different names for the same metric, do NOT default to "unrelated" on wording alone -- \
compare their actual values as you would for any same-metric pair, and let the comparison of \
those values (not the attribute strings) drive whether the result is corroborates, \
contradicts, or reconciled. Only fall back to "unrelated" once you've actually considered \
whether the values agree, disagree, or are explained by a scope/time/unit difference. Two \
facts about the same person or company are NOT automatically related just \
because they mention that person/company, though -- e.g. "eligible for re-appointment as \
director" and "retires by rotation at the AGM" are routine, complementary steps of the same \
standard governance process, not competing claims, and must NOT be marked contradicts or \
reconciled. Likewise, two facts that are simply about different things (different specific \
metrics, different locations, different subsidiaries) are "unrelated" even if superficially \
similar in wording -- do not strain to find a relationship between facts that merely mention \
overlapping words.

Decide exactly one relation_type:
- "corroborates": both facts state the same real-world fact. They may be worded \
differently, use different units, or come from different documents, but they agree.
- "contradicts": the facts are unambiguously about the same real-world subject, attribute, \
time period AND scope, but state incompatible values or statuses, and there is no clear \
explanation (different time / scope / unit / definition) that would reconcile them. Before \
choosing this, explicitly check: could these two statements both be true at once (e.g. as \
sequential steps of one process, or as different aspects of the same situation)? If so, it \
is not a contradiction. Flag it even if you are only fairly confident, not certain -- a \
likely contradiction still counts, say so via a lower confidence score -- but only after \
ruling out that both facts could simply be true together.
- "reconciled": the facts genuinely appear to conflict at first glance, AND the conflict is \
fully explained by a different time period, different scope (e.g. standalone vs \
consolidated, including vs excluding a subsidiary, before vs after an acquisition), \
different units, or different definitions of a similarly-named metric. You MUST name the \
specific reconciling context. Do not use this category for facts that were never really in \
tension to begin with (see "unrelated" above) -- reconciled is for resolving a real apparent \
conflict, not for connecting any two facts that share a topic.
- "unrelated": the two facts are not actually about the same underlying real-world fact \
(e.g. same company but a genuinely different metric, a similar-sounding but different \
entity, or two non-competing facts about the same subject like sequential process steps) -- \
they were only paired because the wording looked superficially similar. When in doubt, \
choose this.

Respond with ONLY this JSON object, nothing else:
{
  "relation_type": "corroborates" | "contradicts" | "reconciled" | "unrelated",
  "explanation": "<1-3 sentences citing the specific values/wording that drove your decision>",
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


def classify_pair(fact_a: dict, doc_a_name: str, fact_b: dict, doc_b_name: str) -> tuple[dict, bool]:
    """Returns (result, cache_hit). Cache key is content-based (see
    hash_fact_pair) and order-independent, so (A, B) and (B, A) -- and the
    same underlying claim re-extracted into a different document -- hit the
    same entry. Changing REASONING_MODEL or SYSTEM_PROMPT changes the hash,
    so a prompt/model change can't silently serve a stale judgment."""
    pair_hash = hash_text(REASONING_MODEL, SYSTEM_PROMPT, hash_fact_pair(fact_a, fact_b))
    cached = db.get_cached_relationship(pair_hash)
    if cached is not None:
        return cached, True

    user_prompt = (
        _fact_block("FACT A", fact_a, doc_a_name) + "\n\n" +
        _fact_block("FACT B", fact_b, doc_b_name)
    )
    result = chat_json(REASONING_MODEL, SYSTEM_PROMPT, user_prompt, temperature=0.0,
                        max_tokens=500, timeout=REASONING_TIMEOUT_SECONDS)
    db.set_cached_relationship(pair_hash, REASONING_MODEL, result)
    return result, False


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
        if cache_hit:
            summary["cache_hits"] += 1
        elif exc is None:
            summary["llm_calls"] += 1

        if exc is not None:
            db.insert_issue(document_id, fact["page_number"], "relationship_classification_failed",
                             f"Comparing fact {fact['id']} vs {other['id']}: {exc}", "")
            summary["errors"] += 1
            continue

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
