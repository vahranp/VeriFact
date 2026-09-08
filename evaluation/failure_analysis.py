"""Groups benchmark mismatches by root cause, from the actual trace each
CaseResult already carries (decision_source, adjudication_checks,
llm_proposal, disagreement) -- no re-classification heuristics, just
reading the fields the real pipeline already produced.
"""
from collections import defaultdict

from evaluation.benchmark import CaseResult

ROOT_CAUSE_UNIT_MISMATCH = "unit_normalization"
ROOT_CAUSE_LLM_SEMANTIC = "llm_semantic_mismatch"
ROOT_CAUSE_CONTEXT_AMBIGUITY = "context_ambiguity"
ROOT_CAUSE_CANDIDATE_OR_METRIC = "metric_matching"
ROOT_CAUSE_SAFE_HEDGE = "safe_but_imprecise_hedge"
ROOT_CAUSE_OTHER = "other"


def _root_cause(r: CaseResult) -> str:
    checks = r.adjudication_checks or {}
    if checks.get("comparable") is False and checks.get("both_numeric"):
        return ROOT_CAUSE_UNIT_MISMATCH
    if checks.get("period_relation") == "unknown" or checks.get("scope_relation") == "unknown":
        return ROOT_CAUSE_CONTEXT_AMBIGUITY
    if r.predicted_category in ("UNCERTAIN", "INSUFFICIENT_CONTEXT") and r.expected_category not in (
        "UNCERTAIN", "INSUFFICIENT_CONTEXT",
    ):
        return ROOT_CAUSE_SAFE_HEDGE
    if r.decision_source == "llm_unchecked":
        return ROOT_CAUSE_LLM_SEMANTIC
    if r.expected_category == "UNRELATED":
        return ROOT_CAUSE_CANDIDATE_OR_METRIC
    return ROOT_CAUSE_OTHER


def analyze(results: list[CaseResult]) -> dict:
    mismatches = [r for r in results if not r.correct]
    by_cause: dict[str, list[CaseResult]] = defaultdict(list)
    for r in mismatches:
        by_cause[_root_cause(r)].append(r)

    severity = {
        "safe_but_imprecise_hedge": "low (a hedged/uncertain answer, not a confident wrong one)",
        "context_ambiguity": "low-medium (system correctly flagged context as unresolved)",
        "unit_normalization": "medium",
        "metric_matching": "medium-high (a retrieval/step-1 gap, not an adjudication gap)",
        "llm_semantic_mismatch": "high (no deterministic check exists for this case; the model's "
                                  "own semantic reading was wrong and nothing caught it)",
        "other": "unclassified",
    }

    return {
        "n_total": len(results),
        "n_correct": len(results) - len(mismatches),
        "n_mismatches": len(mismatches),
        "by_root_cause": {
            cause: {
                "count": len(items),
                "severity": severity.get(cause, "unclassified"),
                "cases": [
                    {"id": r.id, "expected": r.expected_category, "predicted": r.predicted_category,
                     "llm_proposal": r.llm_proposal, "decision_source": r.decision_source,
                     "trap": r.trap, "explanation": r.explanation[:200]}
                    for r in items
                ],
            }
            for cause, items in sorted(by_cause.items(), key=lambda kv: -len(kv[1]))
        },
    }


def report(analysis: dict) -> str:
    lines = ["Failure Analysis", "-" * 40,
             f"{analysis['n_correct']}/{analysis['n_total']} correct, {analysis['n_mismatches']} mismatches"]
    if not analysis["by_root_cause"]:
        lines.append("(no mismatches)")
        return "\n".join(lines)
    for cause, info in analysis["by_root_cause"].items():
        lines.append(f"\n{cause} -- {info['count']} case(s), severity: {info['severity']}")
        for c in info["cases"]:
            lines.append(f"  [{c['id']}] expected={c['expected']} predicted={c['predicted']} "
                         f"(llm said {c['llm_proposal']}, decision_source={c['decision_source']})")
    return "\n".join(lines)
