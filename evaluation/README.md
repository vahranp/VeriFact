# VeriFact Evaluation

A measurement harness, kept entirely separate from the production pipeline
(`app/`). **`app/` never imports anything from this package** — evaluation
observes what the real pipeline produces and compares it against
independently-assigned ground truth; nothing here feeds back into how a
document is actually processed. Confirm this yourself:

```bash
grep -rn "^from evaluation\|^import evaluation" app/   # must print nothing
```

Evaluation data is used only for measurement. No benchmark fact, filename,
company name, or label is used as a production rule — the datasets exist
under `evaluation/datasets/`, are read only by `evaluation/benchmark.py`,
and `app/` has no path that reads that directory.

## What's here

```
evaluation/
  schema.py                       category taxonomy + fact-dict helpers
  benchmark.py                    runs a labelled case through the REAL
                                   app.relationships.classify_pair() or
                                   app.adjudication.adjudicate()
  metrics.py                      pure precision/recall/F1/confusion-matrix
                                   functions (unit-tested independently,
                                   see tests/test_evaluation_metrics.py)
  candidate_recall.py             Recall@K against real corpus embeddings
  evidence_eval.py                evidence precision/recall against a
                                   real, independently-labelled sample
  failure_analysis.py             groups benchmark mismatches by root
                                   cause using the real decision trace
  run_benchmark.py                CLI: runs everything, prints + saves
                                   a report
  datasets/
    build_relationship_benchmark.py   defines the labelled cases in
                                       Python (so common fields aren't
                                       repeated by hand) and writes:
    relationship_benchmark.json       32 classify_pair() cases
    deterministic_override_cases.json 6 adjudicate()-only cases
    evidence_benchmark.json           evidence ground truth
  results/                        run_benchmark.py's output lands here
                                   (gitignored except this README's own
                                   worked example, if present)
```

## How labels were created

Two kinds, neither obtained by asking the production LLM to grade itself
(which would make the benchmark circular):

**Constructed** (26 of 32 relationship cases, all 6 override cases). Fact
pairs built with controlled fields — `value_numeric`, `unit`,
`time_period`, `scope` — so the correct category is true **by
construction** and independently checkable against the deterministic
functions themselves (`app.normalize.compare_values`,
`app.context.compare_periods`/`compare_scopes`), not asserted. They use a
fictional subject ("Northwind Logistics") specifically so nothing here
could be mistaken for a real extracted fact. This is explicitly permitted
by evaluation methodology that wants labels independent of the system
under test — see e.g. property-based/fixture testing practice — and
matches how `tests/test_adjudication.py` already validates the
deterministic layer.

**Real-mined** (6 of 32 relationship cases; all 35 scored evidence
cases). Actual fact pairs already in `storage/facts.db` from the real
Delhivery/IMF/RBI extractions this session. Ground truth was assigned by
**reading the fact's own quote and structured fields directly** — the
same evidence a human reviewer would use — not by querying
`app.relationships`'s LLM for a label. Where a case relies on prior
knowledge from this project's own documented history (the Case 1/3
examples, the BRSR turnover-rate mismatch), that's cited in the case's
`notes` field rather than left implicit. One real labeling mistake was
caught and fixed while building this: four facts with a numeric value
extracted from an incidental digit (a footnote marker, a bond-tenor
label, a date fragment) were initially marked "should validate" on the
reasoning that the evidence check technically finds a match — on review,
that conflated "the mechanical check passes" with "this is a real
quantity," which are different questions; ground truth was corrected to
reflect the latter. That correction is why the evidence numbers below
aren't a suspiciously clean 100%.

## Development vs. frozen-test split

This project didn't run a formal pre-registered train/tune/freeze
protocol from day one — that's stated plainly rather than implied. What
did happen, and is being reported as what it is:

- **Development corpus**: the three Delhivery documents (IPO prospectus,
  FY24 annual report, Q4 earnings deck). Every prompt, threshold, and
  piece of deterministic logic in `app/` was written and iterated against
  this corpus.
- **Held-out generalization set**: an IMF India Article IV report and an
  RBI Annual Report — different organizations, different domains
  (sovereign macroeconomics; central-bank/monetary policy), different
  table conventions, never referenced by any code path (see README's own
  audit: `grep` for document-specific terms in `app/` finds none). These
  were ingested through the **unmodified** pipeline only after the
  adjudication/evidence/context work for this pass was complete, making
  them a genuine (if not formally pre-registered) generalization check.
  Six of this benchmark's relationship cases (`rm-03` through `rm-06`)
  and the candidate-recall/evidence measurements below draw on real facts
  from documents in this set.

Production has no way to know which category a document belongs to —
`app/` doesn't read filenames for behavior, and there is no
"evaluation mode" flag anywhere in the pipeline.

## Multi-domain coverage

Three structurally distinct domains have been run through the identical
pipeline with zero domain-specific code: corporate financial
statements (Delhivery), sovereign macroeconomic reporting (IMF), and
central-bank/monetary policy reporting (RBI). Same schema, same
extraction prompt, same adjudicator, same table reconstruction.

## Reproducing this

```bash
# Fast path (no LLM calls, ~30s): candidate recall + evidence eval only
python -m evaluation.run_benchmark --skip-relationships

# Full run (live Ollama calls, ~15-20 minutes on a local 8B model):
python -m evaluation.run_benchmark

# Regenerate the dataset definitions (no LLM calls):
python evaluation/datasets/build_relationship_benchmark.py

# Unit tests for the evaluation package itself (fast, no LLM/DB):
python -m pytest tests/test_evaluation_metrics.py tests/test_evaluation_dataset.py -q
```

`run_benchmark.py` prints a full report and saves it to
`evaluation/results/report.txt` plus the raw per-case results to
`evaluation/results/relationship_results.json`.

## What this does and doesn't establish

**Does**: gives real, currently-measured numbers for candidate recall,
evidence precision/recall, and relationship classification accuracy —
both the raw LLM proposal and VeriFact's final (adjudicated) answer — on
a benchmark that mixes controlled fixtures covering named failure modes
(digit-substring traps, unit-scale conversion, period/scope/basis
reconciliation, evidence-failure caveats) with real extracted facts from
three different document domains.

**Doesn't**: this is not a large, professionally hand-labelled corpus —
32 relationship cases and 35 scored evidence cases is enough to see real
signal and real failure modes, not enough for tight statistical
confidence intervals on a metric like "contradiction precision" if the
contradiction class only has a handful of examples (the report states
per-class support explicitly and excludes low-support classes from the
macro average rather than presenting a misleadingly precise number). See
`evaluation/results/report.txt` after a run for exactly which classes
that applies to.
