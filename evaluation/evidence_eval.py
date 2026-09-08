"""Evidence precision/recall, measured two ways:

1. A random real sample (evaluation/datasets/evidence_benchmark.json's
   real_sample + known_documented_cases) -- ground truth assigned by
   reading each fact's own quote directly, independent of what the
   system already recorded. Every fact is re-run through the REAL,
   current app.evidence.check_evidence() rather than trusting the
   stored evidence_status column, so the result reflects current code,
   not whatever code produced the original extraction.

2. Adversarial constructed cases targeting a specific, already-suspected
   failure mode (a value from the wrong table column/row), run through
   the same real function to confirm or refute the suspicion with an
   actual result rather than a claim.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from app import db
from app.evidence import FACT_VALIDATED, check_evidence

DATASETS_DIR = Path(__file__).parent / "datasets"


@dataclass
class EvidenceCaseResult:
    fact_id: int
    should_validate: bool
    predicted_validate: bool
    correct: bool
    stored_status: str
    fresh_status: str
    note: str


def _quote_grounded_from_stored_status(stored_status: str) -> bool:
    return stored_status != "ungrounded"


def evaluate_real_sample() -> tuple[list[EvidenceCaseResult], list[dict]]:
    data = json.loads((DATASETS_DIR / "evidence_benchmark.json").read_text(encoding="utf-8"))
    items = data["real_sample"] + data["known_documented_cases"]

    results = []
    excluded = []
    for item in items:
        if item["should_validate"] is None:
            excluded.append(item)
            continue
        fact = db.get_fact(item["fact_id"])
        if fact is None:
            excluded.append({**item, "note": item["note"] + " [fact no longer in DB]"})
            continue
        quote_grounded = _quote_grounded_from_stored_status(fact["evidence_status"])
        fresh = check_evidence(fact, quote_grounded)
        predicted = fresh.status == FACT_VALIDATED
        should = bool(item["should_validate"])
        results.append(EvidenceCaseResult(
            fact_id=item["fact_id"], should_validate=should, predicted_validate=predicted,
            correct=(predicted == should), stored_status=fact["evidence_status"],
            fresh_status=fresh.status, note=item["note"],
        ))
    return results, excluded


def precision_recall_f1(results: list[EvidenceCaseResult]) -> dict:
    tp = sum(1 for r in results if r.should_validate and r.predicted_validate)
    fp = sum(1 for r in results if not r.should_validate and r.predicted_validate)
    fn = sum(1 for r in results if r.should_validate and not r.predicted_validate)
    tn = sum(1 for r in results if not r.should_validate and not r.predicted_validate)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
    return {"n": len(results), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1}


# ------------------------------------------------ adversarial (no LLM) --

def run_adversarial_cases() -> list[dict]:
    """Each case states its own expectation (does_evidence_currently_pass)
    -- i.e. this section doesn't score pass/fail against a single
    universal "should validate" answer, it reports what the real function
    actually does on a case engineered to probe one specific limitation,
    so the report can say precisely which known gaps are real vs closed."""
    cases = [
        {
            "id": "adv-01-wrong-table-column",
            "description": "Value is genuinely present in the quote, but at the wrong position -- "
                            "the quote is a flattened 3-column table row and the claimed period's "
                            "true value is 100, but 200 (a DIFFERENT period's column) is what's "
                            "actually being claimed. This is the real, already-documented "
                            "'header semantics not modeled' limitation (see README/FINAL_REVIEW's "
                            "BRSR turnover-rate case) reproduced as a minimal, reproducible fixture.",
            "fact": {"value": "200", "value_numeric": 200.0, "unit": "INR million", "quote": "FY24 100 | FY23 200 | FY22 300"},
            "known_limitation": True,
        },
        {
            "id": "adv-02-footnote-marker-as-value",
            "description": "A footnote marker digit ('1/') gets read as a numeric value -- the "
                            "check technically passes (1 does appear) but the extraction itself "
                            "shouldn't have produced a numeric fact here at all. Confirms the real "
                            "fact_id=2159 finding from the real-sample review as a minimal fixture.",
            "fact": {"value": "1", "value_numeric": 1.0, "unit": None, "quote": "Exports\nOutward FDI Stock\nSouth Asia 1/"},
            "known_limitation": True,
        },
        {
            "id": "adv-03-genuine-row-label",
            "description": "Control case: the original documented bug, reproduced as a minimal "
                            "fixture rather than only cited from the live DB -- confirms the fix "
                            "still holds under the current code.",
            "fact": {"value": "779", "value_numeric": 779.0, "unit": None, "quote": "Number of complaints filed during the year"},
            "known_limitation": False,
            "expected_status_not_fact_validated": True,
        },
    ]
    out = []
    for case in cases:
        result = check_evidence(case["fact"], quote_grounded=True)
        out.append({**case, "actual_status": result.status, "actual_notes": result.notes})
    return out


def report() -> str:
    results, excluded = evaluate_real_sample()
    metrics = precision_recall_f1(results)
    adversarial = run_adversarial_cases()

    lines = ["Evidence Validation", "-" * 40]
    lines.append(f"Real-sample scored: {metrics['n']} facts ({len(excluded)} excluded -- null ground "
                 f"truth, e.g. ungrounded/no-numeric-claim, see evaluation/datasets/evidence_benchmark.json)")
    lines.append(f"  TP={metrics['tp']}  FP={metrics['fp']}  FN={metrics['fn']}  TN={metrics['tn']}")
    prec = f"{metrics['precision']:.1%}" if metrics['precision'] is not None else "n/a"
    rec = f"{metrics['recall']:.1%}" if metrics['recall'] is not None else "n/a"
    f1 = f"{metrics['f1']:.1%}" if metrics['f1'] is not None else "n/a"
    lines.append(f"  Precision={prec}  Recall={rec}  F1={f1}")
    mismatches = [r for r in results if not r.correct]
    if mismatches:
        lines.append("  Mismatches:")
        for r in mismatches:
            lines.append(f"    fact_id={r.fact_id}: expected should_validate={r.should_validate}, "
                         f"got status={r.fresh_status} -- {r.note}")
    else:
        lines.append("  No mismatches against independently-assigned ground truth.")

    lines.append("\nAdversarial probes (not scored pass/fail -- report what the real function does):")
    for a in adversarial:
        flag = "[KNOWN LIMITATION]" if a["known_limitation"] else "[CONTROL]"
        lines.append(f"  {flag} {a['id']}: status={a['actual_status']}")
        lines.append(f"    {a['description']}")
    return "\n".join(lines)


if __name__ == "__main__":
    db.init_db()
    print(report())
