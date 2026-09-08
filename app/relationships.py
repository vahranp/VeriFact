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
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from app import db
from app.cache import canonical_pair_order, hash_fact_pair, hash_text
from app.config import (
    LLM_CONCURRENCY, REASONING_MODEL, REASONING_TIMEOUT_SECONDS,
    SIMILARITY_TOP_K,
)
from app.candidates import score_pair
from app.embeddings import top_k_similar
from app.llm_client import chat_json, LLMError, LLMParseError
from app.context import compare_periods, compare_scopes, format_context_for_prompt
from app.normalize import compare_values, format_comparison_for_prompt
from app.schemas import MetricMatch, RelationJudgment

from pydantic import ValidationError

# How many nearest neighbours the hybrid signals get to look at.
#
# This was originally set to 12 -- wider than SIMILARITY_TOP_K -- on the
# assumption that entity/lexical/numeric signals would rescue pairs the
# embedding ranking buries. Benchmarking that assumption on 304 real
# extracted facts (scripts/bench_candidates.py) showed it was wrong:
#
#     K   embedding-only   hybrid   rescued by the new signals
#     4        786           786          0
#     8       1573          1574          1
#    12       2339          2340          1
#    20       3776          3779          3
#
# The non-embedding signals are almost entirely subsumed by embedding
# similarity on this corpus -- MiniLM already scores same-entity,
# same-attribute fact pairs above the 0.40 threshold, so entity and
# lexical agreement correlate with it rather than adding to it. Widening
# the window tripled candidate pairs (and therefore LLM calls) while
# adding one pair of genuine recall.
#
# So the window is NOT widened. The signals are kept because they cost
# nothing measurable, can only add pairs (never drop them -- verified),
# and record *why* each pair was retrieved into relationships.candidate_reason,
# which is what makes a missing relationship diagnosable instead of a
# silent gap. See PERFORMANCE.md.
HYBRID_SCAN_K = SIMILARITY_TOP_K
# Above this pool size hybrid scoring is skipped entirely and retrieval
# falls back to embedding-only top-K.
HYBRID_MAX_POOL = 4000

# Relation types worth persisting.
#
# "uncertain" is stored deliberately. A system that can only answer
# corroborates / contradicts / reconciled has to force every judged pair
# into one of them, and the failure that produces is silent: a pair the
# evidence genuinely does not settle gets a confident label instead of an
# admission. Storing it means the UI can show "judged, not settled", which
# is a real answer and the honest one for a knowledge layer.
#
# "unrelated" stays unstored -- it is the overwhelming majority of
# candidate pairs and asserts nothing, so persisting it would bloat the
# graph with non-findings.
STORED_RELATIONS = ("corroborates", "contradicts", "reconciled", "uncertain")

# ---------------------------------------------------------------- step 1 --

SYSTEM_PROMPT_METRIC = """You are given two facts extracted from documents. Decide ONLY whether they \
are asserting something about the exact same underlying real-world metric or attribute -- NOT \
whether their values agree or disagree. That numeric comparison, if relevant, happens in a \
separate step after this one, so do not consider the values at all here.

Work through this in two stages, in order.

STAGE 1 -- name the SLICE each fact measures. A slice is the qualifier that narrows a broad \
measure down to one part of it: a business segment, a product line, a geography, a division, a \
customer type, a facility. Write it as a short phrase, or "whole" if the fact covers the entire \
measure with no narrowing qualifier. Examples: "cross border revenue" -> slice is "cross border"; \
"revenue in the North region" -> slice is "North region"; "total revenue" or "revenue from \
operations" -> slice is "whole".

CRITICAL -- reporting CONTEXT is not a slice. The reporting period (FY24, Q1, "year ended March \
31") and the reporting basis or scope (consolidated vs standalone, group vs company, gross vs net, \
actual vs forecast, continuing vs discontinued) are NOT slices and must NOT make you answer no. \
They describe how the same measure was reported, not which part of it was measured, and they are \
compared separately and precisely in a later step you are not performing here. "Standalone revenue \
from operations" and "consolidated revenue from operations" both have slice "whole" and ARE the \
same metric -- the later step decides whether their scope difference explains a gap in their \
values. Answering no here would discard exactly the comparisons this system exists to make.

STAGE 2 -- the slices must match before anything else is considered. If the two slices are \
different parts of one broad measure ("cross border" vs "PTL freight"), answer NO. If one is a \
slice and the other is "whole" ("cross border revenue" vs "total revenue"), answer NO -- a part \
is supposed to be smaller than its total, so calling them the same metric manufactures a false \
contradiction out of an expected difference. Only when the slices match do you go on to judge \
whether the underlying measure is the same.

When the slices DO match, judge the measure by real-world meaning rather than identical wording: \
business and accounting terminology uses several words for one concept depending on the \
document/author (e.g. "net worth" = "total equity" = "shareholders' equity"; "revenue" = \
"revenue from operations" = "turnover" = "sales"; "gross merchandise value" = "GMV"). When two \
whole-slice facts could plausibly be different names for the same measure, answer yes.

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

Respond with ONLY this JSON object. Fill in the slice fields FIRST -- naming them explicitly is \
what forces the stage-2 check to actually happen rather than being skipped:
{
  "slice_a": "<the slice fact A measures, or \\"whole\\">",
  "slice_b": "<the slice fact B measures, or \\"whole\\">",
  "same_metric": true | false,
  "reason": "<one sentence: what specific metric they share, or why they differ>"
}"""

# ---------------------------------------------------------------- step 2 --

SYSTEM_PROMPT_JUDGE = """Two facts have already been confirmed to describe the SAME underlying \
metric or attribute -- your job now is only to decide HOW they relate: do the stated values \
agree, disagree without explanation, or disagree for a reconcilable reason (different time \
period, scope, or unit)?

Three determinations are supplied below, all computed in code rather than by you: a normalized \
numeric comparison, a reporting-period comparison, and a scope comparison. Trust all three over \
your own reading -- do not recompute the unit conversion, and do not re-derive whether two period \
labels mean the same thing ("FY24" and "FY2023-24" are one period written two ways, and the \
comparison below already accounts for that). Where a determination reports UNKNOWN or says it was \
not possible, judge that aspect from the statements and quotes instead, and prefer lower confidence.

Treat the period and scope determinations as gating your choice:
- period or scope DIFFERENT -> a value difference is expected. That is "reconciled", not \
"contradicts", and you must name the specific difference as the reconciling context.
- period SAME, and scope SAME or UNKNOWN -> context does not explain a value difference, so a \
material unexplained difference is a genuine "contradicts".
- period OVERLAPPING (a quarter inside a year, or a fiscal year against a calendar year) -> the \
figures are not expected to match; that is "reconciled", not a contradiction.

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
- "uncertain": the facts do describe the same metric, but the evidence in front of you does not \
settle how they relate -- for example the values differ while the period or scope determination \
came back UNKNOWN, so you cannot tell whether context explains the gap. Choose this instead of \
guessing between "contradicts" and "reconciled". Reporting that the evidence is insufficient is a \
correct answer here, and is strongly preferred over a confident label the evidence does not support.

Respond with ONLY this JSON object:
{
  "relation_type": "corroborates" | "contradicts" | "reconciled" | "unrelated" | "uncertain",
  "explanation": "<1-3 sentences citing the specific values, the normalized comparison, or the reconciling context>",
  "reconciliation_context": "<required if reconciled, else null>",
  "confidence": <0.0-1.0>
}"""


def _validated(model, raw, default: dict) -> dict:
    """Puts a model response through its schema before anything else sees it.

    These schemas existed but were only applied to fact extraction, so the
    relationship path -- which writes straight to the database -- was the
    one place taking raw model output on trust. That is the opposite of the
    right way round: a bad fact is one bad row, a bad relationship is an
    assertion about two other rows.

    A response that can't be validated is downgraded to a safe default
    rather than raising, because a single unparseable judgment should cost
    one relationship, not the whole document's comparison pass. The default
    asserts nothing: step 1 falls back to "not the same metric" and step 2
    to "uncertain", so a malformed response can never manufacture a
    corroboration or a contradiction.
    """
    if not isinstance(raw, dict):
        return dict(default)
    try:
        return model.model_validate(raw).model_dump()
    except ValidationError:
        return dict(default)


# Evidence statuses (see app/evidence.py) under which a fact's value_numeric
# should not be treated as trustworthy ground truth. Matches the identical
# policy in app/arithmetic.py and app/coherence.py -- a fact missing
# evidence_status entirely is "not yet assessed", not "known bad", and is
# not included here.
_UNVERIFIED_EVIDENCE = {"ungrounded", "quote_grounded"}


def _unverified_value_caveat(fact_a: dict, fact_b: dict) -> Optional[str]:
    """A caveat for the judge when either fact's value is not itself
    evidenced -- the quote is missing, or circular (it just restates the
    number rather than supporting it, e.g. quote="35.69%" for value
    35.69%). The deterministic comparison above is only as trustworthy as
    the values feeding it; without this, a confident "these agree to
    0.006%" could be built on a number nothing ever confirmed."""
    flagged = [
        label for label, fact in (("Fact A", fact_a), ("Fact B", fact_b))
        if fact.get("evidence_status") in _UNVERIFIED_EVIDENCE
    ]
    if not flagged:
        return None
    subjects = " and ".join(f"{label}'s" for label in flagged)
    plural = len(flagged) > 1
    noun = "values are not themselves evidenced" if plural else "value is not itself evidenced"
    pronoun = "their quotes do" if plural else "its quote does"
    return (
        f"CAVEAT: {subjects} {noun} -- {pronoun} not clearly "
        f"support the stated number{'s' if plural else ''}. Treat the comparison above with "
        f"reduced confidence, and prefer \"uncertain\" over a confident "
        f"corroborates/contradicts if this unverified value is the deciding factor."
    )


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
    raw = chat_json(REASONING_MODEL, SYSTEM_PROMPT_METRIC, user_prompt, temperature=0.0,
                     max_tokens=250, timeout=REASONING_TIMEOUT_SECONDS)
    result = _validated(MetricMatch, raw, default={"same_metric": False,
                                                    "reason": "step 1 returned an unusable shape"})
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
    raw = chat_json(REASONING_MODEL, SYSTEM_PROMPT_JUDGE, user_prompt, temperature=0.0,
                     max_tokens=400, timeout=REASONING_TIMEOUT_SECONDS)
    result = _validated(RelationJudgment, raw, default={
        "relation_type": "uncertain",
        "explanation": "step 2 returned an unusable shape; no relationship is asserted",
        "reconciliation_context": None, "confidence": None,
    })
    db.set_cached_relationship(pair_hash, REASONING_MODEL, result)
    return result, False


def classify_pair(fact_a: dict, doc_a_name: str, fact_b: dict, doc_b_name: str) -> tuple[dict, bool]:
    """Public entry point, kept at the same (result, cache_hit) shape as
    the original single-call version so existing callers -- including
    scripts/test_case1.py, test_case2.py, and retest_relationships.py --
    don't need to change to serve as before/after regression checks
    across this exact refactor.

    The two facts are put into a canonical order before being shown to the
    model (see cache.canonical_pair_order): the pair cache is deliberately
    order-independent, so without this a pair first judged as (A, B) and
    later met as (B, A) would be served a cached explanation whose "FACT A"
    and "FACT B" referred to the wrong facts. `result["_meta"]["swapped"]`
    reports whether that reordering happened, so a caller storing the
    relationship can persist it in the same order the explanation describes.

    Internally runs up to two LLM calls (see module docstring for why).
    Step 2 is skipped entirely when step 1 says the facts aren't the same
    metric -- the common case (most candidate pairs are unrelated), so
    this doesn't double the call count for pairs that were never going to
    match anyway. `result["_meta"]` carries granular step/call-count
    information for the caller's stats (see build_relationships_for_document)
    without changing the public field shape relation_type/explanation/
    reconciliation_context/confidence that callers already expect."""
    swapped = canonical_pair_order(fact_a, fact_b)
    if swapped:
        fact_a, fact_b = fact_b, fact_a
        doc_a_name, doc_b_name = doc_b_name, doc_a_name

    metric_result, metric_cached = classify_metric_match(fact_a, doc_a_name, fact_b, doc_b_name)

    if not metric_result.get("same_metric"):
        result = {
            "relation_type": "unrelated",
            "explanation": metric_result.get("reason") or "The two facts do not describe the same underlying metric.",
            "reconciliation_context": None,
            "confidence": metric_result.get("confidence"),
            "_meta": {"steps_run": 1, "llm_calls": 0 if metric_cached else 1,
                       "same_metric": False, "swapped": swapped},
        }
        return result, metric_cached

    comparison = compare_values(
        fact_a.get("value_numeric"), fact_a.get("unit"),
        fact_b.get("value_numeric"), fact_b.get("unit"),
    )
    # Period and scope are determined in code for the same reason values
    # are: "FY24" and "FY2023-24" are the same period written two ways, and
    # a model comparing those strings has no reason to know it. See
    # app/context.py.
    period = compare_periods(fact_a.get("time_period"), fact_b.get("time_period"))
    scope = compare_scopes(fact_a.get("scope"), fact_b.get("scope"))

    # The comparison above is only as trustworthy as the values feeding
    # it. Audited in alongside the identical gap already fixed in
    # app/arithmetic.py and app/coherence.py: an ungrounded or
    # circular-quote fact's value_numeric has no more claim to being
    # correct than any other unverified number, so a confident "these
    # agree to 0.006%" built on one would be handing the judge a false
    # sense of certainty rather than a real computation.
    evidence_caveat = _unverified_value_caveat(fact_a, fact_b)

    # The context block needs to know whether the values actually agreed:
    # context explains a *difference*, so its guidance is only meaningful
    # when there is one. An unverified value makes that agreement itself
    # unreliable, same as magnitude_suspect already does.
    values_agree = (
        comparison.agree
        if comparison.comparable and not comparison.magnitude_suspect and not evidence_caveat
        else None
    )
    comparison_lines = [
        format_comparison_for_prompt(comparison),
        format_context_for_prompt(period, scope, values_agree),
    ]
    if evidence_caveat:
        comparison_lines.append(evidence_caveat)
    comparison_line = "\n".join(comparison_lines)
    judge_result, judge_cached = classify_relation(fact_a, doc_a_name, fact_b, doc_b_name, comparison_line)

    result = dict(judge_result)
    result["_meta"] = {
        "steps_run": 2,
        "llm_calls": (0 if metric_cached else 1) + (0 if judge_cached else 1),
        "same_metric": True,
        "comparison": comparison_line,
        "period": period.relation,
        "scope": scope.relation,
        "swapped": swapped,
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
    # Fact rows are looked up repeatedly during scoring (a fact can appear
    # in many pairs), so memoize rather than re-hitting SQLite each time.
    fact_cache: dict[int, dict] = {f["id"]: f for f in new_facts}

    def load_fact(fact_id: int):
        if fact_id not in fact_cache:
            fact_cache[fact_id] = db.get_fact(fact_id)
        return fact_cache[fact_id]

    seen_pairs: set[tuple[int, int]] = set()
    jobs = []
    hybrid_promotions = 0
    for fact, (fid, emb) in zip(new_facts, new_pool):
        candidates = [c for c in full_pool if c[0] != fid]

        # Embedding similarity narrows the field; the other signals are
        # then applied to a wider window than the embedding threshold
        # alone would admit, so a pair that embeddings rank poorly can
        # still be promoted by an entity/lexical/numeric match. Beyond
        # HYBRID_MAX_POOL the wider scan is skipped and this degrades to
        # the original embedding-only top-K -- bounded cost, no cliff.
        scan_k = SIMILARITY_TOP_K if len(candidates) > HYBRID_MAX_POOL else HYBRID_SCAN_K
        top = top_k_similar(emb, candidates, max(SIMILARITY_TOP_K, scan_k))

        for other_id, score in top:
            pair_key = (min(fid, other_id), max(fid, other_id))
            if pair_key in seen_pairs or db.relationship_exists(fid, other_id):
                continue
            other = load_fact(other_id)
            if other is None:
                continue

            reason = score_pair(fact, other, score)
            if not reason.selected:
                continue
            if "embedding" not in reason.triggers:
                hybrid_promotions += 1

            seen_pairs.add(pair_key)
            jobs.append((fact, other, score, reason))
    candidate_retrieval_seconds = time.perf_counter() - t0

    summary = {
        "candidates_checked": len(jobs), "stored": 0, "skipped_unrelated": 0, "errors": 0,
        "llm_calls": 0, "cache_hits": 0,
        "metric_mismatches": 0,  # step 1 said "no" -- resolved without ever reaching step 2
        "uncertain": 0,          # judged, but the evidence did not settle it
        # Pairs no embedding threshold would have surfaced, promoted by an
        # entity/lexical/numeric signal instead (see app/candidates.py).
        "hybrid_promotions": hybrid_promotions,
        "candidate_retrieval_seconds": round(candidate_retrieval_seconds, 3),
        "llm_reasoning_seconds": 0.0,
        # Set when a user-requested stop interrupted this stage. Pairs
        # already judged before the stop are still stored below (real LLM
        # work is never discarded); pipeline.py checks this to skip the
        # coherence stage and mark the document 'cancelled' rather than
        # 'done'.
        "cancelled": False,
    }
    if not jobs:
        return summary

    if db.is_cancel_requested(document_id):
        # Stopped between stages -- before any candidate pair in this batch
        # was even judged. Nothing to store, nothing to wait on.
        summary["cancelled"] = True
        return summary

    def run_job(job):
        fact, other, score, _reason = job
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

            if db.is_cancel_requested(document_id):
                # Drop everything not yet started; let whatever is already
                # running finish naturally (bounded by its own timeout --
                # see request_cancel). Judgments completed before this
                # point are real LLM work and are stored below exactly as
                # if the run had finished normally.
                pool.shutdown(wait=False, cancel_futures=True)
                summary["cancelled"] = True
                break
    summary["llm_reasoning_seconds"] = round(time.perf_counter() - t0, 3)

    # A cancelled run leaves trailing None entries for jobs that were
    # dropped before starting (cancel_futures=True) or never got the
    # chance to run -- entries are only ever a full 4-tuple or nothing.
    for job, result, cache_hit, exc in filter(None, results):
        fact, other, score, reason = job

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
        if relation not in STORED_RELATIONS:
            # "unrelated" is the overwhelming majority of candidate pairs
            # and carries no claim, so it stays unstored -- persisting it
            # would bloat the graph with non-findings.
            summary["skipped_unrelated"] += 1
            continue
        if relation == "uncertain":
            summary["uncertain"] += 1

        # Persist in the same order the explanation talks about, so the
        # UI's left-hand fact is the one the text calls "FACT A".
        first, second = (other, fact) if meta.get("swapped") else (fact, other)
        db.insert_relationship(
            first["id"], second["id"], relation,
            result.get("explanation", ""),
            result.get("reconciliation_context"),
            result.get("confidence"),
            score,
            reason.describe(),
        )
        summary["stored"] += 1

    return summary


def _load_embedding(fact_row: dict) -> list[float]:
    import json
    return json.loads(fact_row["embedding_json"])
