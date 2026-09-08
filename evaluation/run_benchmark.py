"""Evaluation CLI. Runs the full evaluation suite against the real,
unmodified production pipeline and prints/saves a report.

    python -m evaluation.run_benchmark                 # everything
    python -m evaluation.run_benchmark --skip-relationships  # fast path,
        skips the live-LLM relationship benchmark (candidate recall +
        evidence eval only, both LLM-free and fast)

Every number below comes from an actual run against app/ as it exists
right now -- nothing here is precomputed or asserted. See
evaluation/README.md for full methodology and what each section means.
"""
import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from evaluation import candidate_recall, evidence_eval, failure_analysis, metrics
from evaluation.benchmark import run_all
from evaluation.schema import category_for_result  # noqa: F401 (re-exported for convenience)

RESULTS_DIR = Path(__file__).parent / "results"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-relationships", action="store_true",
                         help="skip the live-LLM relationship benchmark (candidate recall + "
                              "evidence eval only -- both are LLM-free)")
    args = parser.parse_args()

    db.init_db()
    RESULTS_DIR.mkdir(exist_ok=True)
    report_lines = []

    def log(msg):
        print(msg)

    report_lines.append("=" * 70)
    report_lines.append("VeriFact Evaluation Report")
    report_lines.append(f"Run at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("=" * 70)

    # ---- candidate retrieval (real corpus, no LLM) ----
    log("\n--- Candidate retrieval (real corpus embeddings) ---")
    recall_result = candidate_recall.measure()
    recall_text = candidate_recall.report(recall_result)
    log(recall_text)
    report_lines.append("\n" + recall_text)

    # ---- evidence (real sample + adversarial, no LLM) ----
    log("\n--- Evidence validation ---")
    evidence_text = evidence_eval.report()
    log(evidence_text)
    report_lines.append("\n" + evidence_text)

    if args.skip_relationships:
        report_lines.append("\n(relationship benchmark skipped: --skip-relationships)")
        (RESULTS_DIR / "report.txt").write_text("\n".join(report_lines), encoding="utf-8")
        return

    # ---- relationship classification: LLM vs final VeriFact (live) ----
    log("\n--- Relationship benchmark (live LLM calls; this takes several minutes) ---")
    t0 = time.perf_counter()
    results = run_all(progress=log)
    total_time = time.perf_counter() - t0

    llm_pairs = [(r.expected_category, category_for_result(r.llm_proposal, None))
                 for r in results if r.llm_proposal is not None]
    final_pairs = [(r.expected_category, r.predicted_category) for r in results]

    llm_metrics = metrics.evaluate(llm_pairs) if llm_pairs else None
    final_metrics = metrics.evaluate(final_pairs)
    confusion_md = metrics.confusion_matrix_markdown(final_pairs)

    n_disagreements = sum(1 for r in results if r.disagreement)
    n_llm_wrong = sum(1 for e, p in llm_pairs if e != p)
    n_llm_wrong_corrected = sum(
        1 for r in results
        if r.llm_proposal is not None
        and category_for_result(r.llm_proposal, None) != r.expected_category
        and r.correct
    )
    n_llm_wrong_still_wrong = sum(
        1 for r in results
        if r.llm_proposal is not None
        and category_for_result(r.llm_proposal, None) != r.expected_category
        and not r.correct
    )
    n_final_errors_introduced_by_override = sum(
        1 for r in results
        if r.decision_source == "deterministic_override"
        and r.llm_proposal is not None
        and category_for_result(r.llm_proposal, None) == r.expected_category
        and not r.correct
    )

    section = []
    section.append(f"\nRan {len(results)} cases in {total_time:.1f}s "
                   f"({sum(1 for r in results if r.provenance == 'deterministic_override')} deterministic-only, "
                   f"{sum(1 for r in results if r.provenance != 'deterministic_override')} via classify_pair)")
    section.append(f"\nFinal VeriFact accuracy: {final_metrics.accuracy:.1%} ({final_metrics.n} cases)")
    if llm_metrics:
        section.append(f"Raw LLM proposal accuracy (same {len(llm_pairs)} cases that had a live "
                       f"proposal): {llm_metrics.accuracy:.1%}")
    section.append(f"Macro F1 (final): {final_metrics.macro_f1:.1%}"
                   + (f"  (excluding low-support labels: {final_metrics.low_support_labels})"
                      if final_metrics.low_support_labels else ""))
    section.append(f"\nDeterministic disagreement fired on {n_disagreements}/{len(results)} cases")
    section.append(f"LLM proposals that were wrong (of cases with a live proposal): {n_llm_wrong}")
    section.append(f"  -> corrected by the deterministic layer to the right answer: {n_llm_wrong_corrected}")
    section.append(f"  -> still wrong after adjudication: {n_llm_wrong_still_wrong}")
    section.append(f"New errors introduced by an override (LLM was right, override made it wrong): "
                   f"{n_final_errors_introduced_by_override}")

    section.append("\nPer-class (final VeriFact):")
    for c in final_metrics.per_class:
        p = f"{c.precision:.0%}" if c.precision is not None else "n/a"
        r_ = f"{c.recall:.0%}" if c.recall is not None else "n/a"
        f1 = f"{c.f1:.0%}" if c.f1 is not None else "n/a"
        flag = "  (low support -- not in macro avg)" if c.support < 3 else ""
        section.append(f"  {c.label:<24} support={c.support:<3} precision={p:<6} recall={r_:<6} f1={f1}{flag}")

    section.append("\nConfusion matrix (final VeriFact):")
    section.append(confusion_md)

    analysis = failure_analysis.analyze(results)
    section.append("\n" + failure_analysis.report(analysis))

    for line in section:
        log(line)
    report_lines.extend(section)

    # Save raw results + report.
    (RESULTS_DIR / "relationship_results.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False), encoding="utf-8")
    (RESULTS_DIR / "report.txt").write_text("\n".join(report_lines), encoding="utf-8")
    log(f"\nSaved: {RESULTS_DIR / 'relationship_results.json'}, {RESULTS_DIR / 'report.txt'}")


if __name__ == "__main__":
    main()
