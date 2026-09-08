"""Runs the labelled datasets against the REAL production functions and
returns structured results. This module is the only place evaluation/
touches app/ -- it calls production code to observe its output, exactly
like a user of the API would, and never the reverse (app/ never imports
this package).
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.adjudication import adjudicate
from app.context import compare_periods, compare_scopes
from app.normalize import compare_values
from app.relationships import _unverified_value_caveat, classify_pair
from evaluation.schema import category_for_result

DATASETS_DIR = Path(__file__).parent / "datasets"


@dataclass
class CaseResult:
    id: str
    provenance: str
    trap: Optional[str]
    notes: str
    expected_category: str
    predicted_category: str
    correct: bool
    llm_proposal: Optional[str]
    decision_source: Optional[str]
    disagreement: bool
    disagreement_reason: Optional[str]
    explanation: str
    reconciliation_context: Optional[str]
    cache_hit: bool
    latency_seconds: float
    adjudication_checks: dict = field(default_factory=dict)


def load_relationship_cases() -> list[dict]:
    return json.loads((DATASETS_DIR / "relationship_benchmark.json").read_text(encoding="utf-8"))


def load_override_cases() -> list[dict]:
    return json.loads((DATASETS_DIR / "deterministic_override_cases.json").read_text(encoding="utf-8"))


def run_classify_pair_case(case: dict) -> CaseResult:
    """Runs one relationship_benchmark.json case through the real,
    unmodified app.relationships.classify_pair() -- the exact function
    build_relationships_for_document calls during real document
    ingestion. A live Ollama call happens here unless the relationship
    cache already has this exact prompt text cached."""
    t0 = time.perf_counter()
    result, cache_hit = classify_pair(case["fact_a"], "evaluation/fact_a", case["fact_b"], "evaluation/fact_b")
    latency = time.perf_counter() - t0

    predicted = category_for_result(result["relation_type"], result.get("decision_source"))
    return CaseResult(
        id=case["id"], provenance=case["provenance"], trap=case.get("trap"), notes=case.get("notes", ""),
        expected_category=case["category"], predicted_category=predicted,
        correct=(predicted == case["category"]),
        llm_proposal=result.get("llm_proposal"), decision_source=result.get("decision_source"),
        disagreement=bool(result.get("disagreement")), disagreement_reason=result.get("disagreement_reason"),
        explanation=result.get("explanation", ""), reconciliation_context=result.get("reconciliation_context"),
        cache_hit=cache_hit, latency_seconds=latency,
        adjudication_checks=result.get("adjudication_checks") or {},
    )


def run_override_case(case: dict) -> CaseResult:
    """Runs one deterministic_override_cases.json case through the REAL
    app.adjudication.adjudicate(), fed the exact same comparison/period/
    scope/evidence_caveat/both_numeric computation classify_pair itself
    uses (app/normalize.py, app/context.py, app/relationships.py's own
    _unverified_value_caveat) -- only the LLM's step-2 proposal is
    supplied directly instead of coming from a live call, since the point
    of this case is to test the override precisely, not to hope a live
    call reproduces a specific mistake."""
    fact_a, fact_b = case["fact_a"], case["fact_b"]
    comparison = compare_values(fact_a.get("value_numeric"), fact_a.get("unit"),
                                 fact_b.get("value_numeric"), fact_b.get("unit"))
    period = compare_periods(fact_a.get("time_period"), fact_b.get("time_period"))
    scope = compare_scopes(fact_a.get("scope"), fact_b.get("scope"))
    evidence_caveat = _unverified_value_caveat(fact_a, fact_b)
    both_numeric = fact_a.get("value_numeric") is not None and fact_b.get("value_numeric") is not None

    t0 = time.perf_counter()
    adjudication = adjudicate(case["llm_proposal"], comparison, period, scope, evidence_caveat, both_numeric)
    latency = time.perf_counter() - t0

    predicted = category_for_result(adjudication.relation_type, adjudication.decision_source)
    return CaseResult(
        id=case["id"], provenance="deterministic_override", trap=case.get("trap"), notes=case.get("notes", ""),
        expected_category=case["category"], predicted_category=predicted,
        correct=(predicted == case["category"]),
        llm_proposal=adjudication.llm_proposal, decision_source=adjudication.decision_source,
        disagreement=adjudication.disagreement, disagreement_reason=adjudication.disagreement_reason,
        explanation=adjudication.explanation, reconciliation_context=adjudication.reconciliation_context,
        cache_hit=False, latency_seconds=latency, adjudication_checks=adjudication.checks,
    )


def run_all(progress=print) -> list[CaseResult]:
    results = []
    rel_cases = load_relationship_cases()
    for i, case in enumerate(rel_cases, 1):
        progress(f"[{i}/{len(rel_cases)}] classify_pair: {case['id']} ({case['category']})...")
        results.append(run_classify_pair_case(case))
    override_cases = load_override_cases()
    for i, case in enumerate(override_cases, 1):
        progress(f"[{i}/{len(override_cases)}] adjudicate-override: {case['id']} ({case['category']})...")
        results.append(run_override_case(case))
    return results
