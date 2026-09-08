# Final Review: the deterministic-adjudication hardening pass

This documents a focused engineering pass on VeriFact (the fact-extraction and cross-document
reasoning system in this repository), done against a specific brief: close the gap between "the
LLM proposes, deterministic code verifies" as a stated design principle and as something actually
enforced in code, without redesigning an architecture that was already sound. Every claim below is
either a direct code citation, a test that exists and passes, or a real command's output — nothing
here is asserted without having been run.

---

## 1. Before architecture

The pipeline already had the right shape going into this pass: PDF → layout-aware extraction →
per-chunk LLM fact extraction → verbatim quote grounding → two-tier evidence validation
(`quote_grounded` vs. `fact_validated`) → embedding-based candidate retrieval → two-step LLM
relationship classification (same-metric? → corroborates/contradicts/reconciled/uncertain) →
graph-level coherence checking. Deterministic modules already existed and were already load-bearing:
`app/normalize.py` (unit conversion), `app/context.py` (period/scope comparison), `app/arithmetic.py`
(additive-identity discovery), `app/coherence.py` (transitivity checking), `app/evidence.py`
(quote-vs-value verification).

**The gap:** every one of those deterministic modules computed a real answer and then *handed it to
the LLM as text in a prompt*, trusting the model to defer to it. Nothing in code checked that the
model's final answer actually respected the computed facts it was given. The two-step split
(same-metric?, then corroborates/contradicts/reconciled) reduced how often this mattered, but did
not close it — a model could still see "these agree to 0.006%" and answer `contradicts` anyway, and
the system would store that with no correction until (at best) a coherence triangle happened to
close across three facts later. Most pairs never get a triangle partner.

## 2. After architecture

One new module, `app/adjudication.py`, sits between the model's step-2 proposal and what gets
stored. It re-applies the same value/period/scope logic the prompt was built from and decides the
FINAL `relation_type` in code wherever that logic is conclusive — an 8-rule precedence table (see
the module's own docstring and `tests/test_adjudication.py`'s 27 cases) that confirms the model when
it agrees, overrides it when it doesn't, and defers to it only when the deterministic facts
genuinely don't settle the question (a qualitative pair with no number to check, or values
agreeing while context genuinely differs).

Two states were split out of the old catch-all `uncertain`: `related_but_not_comparable` (a
structural fact — units that don't reduce to a common base) and `insufficient_context` (values
differ, but period/scope came back UNKNOWN). Every relationship now stores `decision_source`
(`deterministic_confirmed` / `deterministic_override` / `llm_unchecked`), the model's original
`llm_proposal`, and — when they diverge — `disagreement_reason`. None of this is hidden: it's in
the API response for every relationship and rendered directly on the relationship card in the UI,
and a relationship where the system overrode its own model is scored into the HIGH priority tier
regardless of what the final label is (`app/priority.py`).

Six further, independently-confirmed bugs were fixed in the same pass because they mattered *more*
once a deterministic layer gained override authority over an LLM (a wrong deterministic fact used
to be softened by the model's own judgment; now it can win outright, so it has to actually be
right) — see [§6](#6-how-deterministic-verification-challenges-the-llm) and the fuller list in the
plan file this pass worked from.

## 3. Biggest accuracy improvements

1. **The adjudicator itself.** Converts "the model was told to trust the computed comparison" into
   "the computed comparison wins when it's conclusive, whatever the model says." This is the
   change that actually closes the gap described in §1.
2. **The numeric evidence validator rewrite** (`app/evidence.py`, `app/normalize.py::
   extract_number_tokens`). The old check reduced both the quote and the value to digit-only
   strings and did a substring test — which is a materially weaker check than it looks:
   `value=12` matched any quote containing digits "2024" and "120" (their concatenation,
   "2024120", contains "12"), and stripping the decimal point out of `12.5` produced "125", a
   substring of an unrelated "3125". Rewritten to tokenize the quote into actual numbers (via the
   same locale-aware parser used everywhere else) and compare numeric values, not digit runs. Every
   existing test in `tests/test_evidence.py` still passes under the new logic; two new tests pin
   the exact false-positive shapes that are now fixed.
3. **The scope-comparison axis fix** (`app/context.py::compare_scopes`). The contrast-checking loop
   used to compare every qualifier group against every OTHER group in a flat list, not just
   designed opposites — so "gross" (a consolidation-unrelated qualifier) and "actual" (a
   reporting-basis qualifier) could be reported as CONTRASTING scopes purely because they occupied
   different list positions, even though a figure can be gross and actual at once. Restructured
   into explicit `(dimension, sides)` axes, checked only within an axis. This is a correctness fix
   that matters specifically because of change #1: a wrong "scope DIFFERENT" verdict now has the
   power to force a deterministic `reconciled` override it didn't earn.
4. **The period-kind split** (`app/context.py::parse_period`). "Revenue for the year ended March
   31, 2024" (a twelve-month flow) and "cash balance as of March 31, 2024" (a point-in-time
   snapshot) used to parse to the identical `as_of` kind and, sharing a calendar anchor, compare as
   `SAME` — silently erasing the difference between a period total and a balance. Split into
   `as_of` and `period_end`, compared as `OVERLAPPING` (not `SAME`) when they share a date but
   differ in kind.
5. **Confidence handling stopped fabricating certainty** (`app/schemas.py`). A confidence outside
   [0, 1] used to be clamped to the nearest boundary (`7.5` → `1.0`), converting a demonstrated
   misunderstanding of the scale into the MOST confident possible value. Now rejected to `None`
   instead — consistent with how every other unparseable signal in this project is already handled
   (refuse rather than guess).

## 4. Biggest creative improvements

- **Making disagreement a first-class, queryable object** rather than an implicit outcome. A system
  that "sometimes overrides its model" is a claim; `decision_source` + `llm_proposal` +
  `disagreement_reason` on every relationship, surfaced in the API and the UI and boosted in
  priority ranking, is a mechanism a reviewer can actually inspect and count.
- **`Violation.is_strict_proof`** (`app/coherence.py`). Rather than inventing a whole parallel
  relation-kind taxonomy (`EXACT_EQUALITY` / `SEMANTIC_SUPPORT` / …) to address the legitimate
  concern that "corroborates" sometimes means numeric equality and sometimes means a qualitative
  "same status" reading, this reuses the adjudicator's own `decision_source` field: a coherence
  violation is a strict mathematical proof only when every equality edge in the triangle rests on a
  deterministically-confirmed numeric check, and honestly says so when it doesn't. One small,
  reused primitive instead of a second taxonomy.
- **Fixing the document-name leak into the extraction prompt.** The extraction call used to include
  `Document: {filename}` — for the starter corpus, a string that literally contains "delhivery".
  Removed once noticed: nothing about grounding depends on it, and it's exactly the kind of subtle,
  easy-to-miss generalization leak that a purely static audit of "no hardcoded values in `app/`"
  would never catch, since the leak lives in what gets passed at runtime, not in the code text.

## 5. Relationship decision flow

```
Fact A + Fact B
   │
   ├─ candidate retrieval (embedding + entity + lexical + numeric signals; records WHY)
   ▼
STEP 1 [LLM] — same metric? (names each fact's slice first)
   ├─ no ──► unrelated (stop)
   ▼ yes
DETERMINISTIC CHECKS [code] — normalize_unit comparison · period comparison · scope comparison
   ▼
STEP 2 [LLM] — PROPOSE corroborates / contradicts / reconciled / uncertain
   ▼
ADJUDICATE [code, app/adjudication.py] — 8 rules, first match wins:
   1. both numeric, units incompatible          → related_but_not_comparable
   2. magnitude-suspect (unit-metadata artifact) → cap at uncertain
   3. either value unverified                    → cap a confident corroborates/contradicts at uncertain
   4. values agree, period/scope not DIFFERENT   → corroborates
   5. values differ, period=SAME, scope=SAME     → contradicts   (the bold override)
   6. values differ, period/scope confirmed DIFFERENT → reconciled
   7. values differ, period/scope UNKNOWN         → insufficient_context
   8. nothing conclusive (e.g. qualitative facts) → defer to the model (llm_unchecked)
   ▼
STORED: relation_type, decision_source, llm_proposal, disagreement, disagreement_reason, checks{}
   ▼
GRAPH COHERENCE [code] — cross-checks the stored graph for logical impossibility
```

## 6. How deterministic verification challenges the LLM

This is the core ask, so stated plainly with the actual mechanism, not just the concept:

- **Rule 4 and rule 5 can force an answer the model didn't give.** If normalized values agree and
  period/scope are confirmed non-different, the result is `corroborates` even if the model proposed
  something else — and symmetrically, if values clearly differ under a *confirmed* same period and
  scope, the result is `contradicts` even if the model invented a plausible-sounding
  `reconciled` story. This second case is the deliberately bold one: it trades away a narrow
  failure mode (a genuine reconciling reason stated only in free prose, never reflected in the
  structured period/scope fields) for removing the model's ability to explain away a verified,
  unexplained numeric conflict — the right trade for a system whose entire purpose is surfacing
  disagreement.
- **Rule 2 caps the model's confidence, not just its label.** A magnitude-suspect comparison
  (values differing by orders of magnitude, the signature of an incomplete unit) is capped at
  `uncertain` regardless of how confidently the model argued for `contradicts` — demonstrated live
  in Case 2 below.
- **Every override is logged, not silently applied.** `disagreement_reason` states in plain
  language why the deterministic layer disagreed, and `llm_proposal` preserves what the model
  actually said, so the override is auditable rather than just asserted.
- **The system does NOT second-guess the model where it shouldn't.** Rule 8 (`llm_unchecked`)
  exists specifically so that genuinely qualitative judgments — "do these two statements assert the
  same status" — are left to the model, honestly labeled as resting on its reading alone rather
  than silently dressed up as equally certain as a computed fact. Case 3 below is a live example.

## 7. How unseen financial documents were tested

Per the brief's non-negotiable requirement: Delhivery is a demonstration corpus, and the claim of
generalization needed an actual run on documents this codebase was never shown during development,
not just a static audit of the source code.

**Documents:** the IMF's 2025 India Article IV Consultation report (sovereign macroeconomic
assessment) and the RBI's 2024-25 Annual Report (central-bank/monetary-policy document) — both
already present in `data/uploads/` from prior, unrelated exploration, uploaded independently of
this project and never referenced by any code path. Neither is a corporate financial statement;
neither shares an issuing organization, a vocabulary, or a table convention with Delhivery.

**Method:** uploaded through the same `POST /api/documents` path any real upload uses — no test
harness, no special code path. Pages selected for numeric density and structural difference from
the Delhivery tables: IMF pages 44 (macroeconomic framework, multi-year) and 47 (central government
operations — a scope-like central/general government contrast, analogous to standalone/
consolidated); RBI pages 91 (macroeconomic and financial indicators, 5-column year comparison) and
95 (inflation with a rural/urban/combined × year multi-level header, structurally similar in kind —
not content — to the gender × year BRSR table that caused the original Delhivery row-label bug).

**Root-cause discipline:** per the brief's instruction, any failure was to be classified (parser /
table / semantic / context / model limitation) and fixed generically or left honestly documented —
never patched with a one-off rule for these specific documents. See §8 for what was actually found.

## 8. Results from the unseen-document test

**IMF India Article IV report (pages 44, 47) — completed successfully.** 12 facts extracted, 46
candidate pairs checked, 12 relationships stored, all through the unmodified pipeline. Real,
inspected output:

- **Every extracted fact came from page 47, not page 44.** Page 44 (a macroeconomic-framework table
  spanning nine fiscal years, 2021/22 through 2030/31) timed out on **all four** of its chunks
  (`llm_call_failed`, 240s ceiling — logged as real `extraction_issues`, not hidden). Root cause,
  by the brief's own taxonomy: a **model/hardware limitation**, the same "dense chunk exceeds local
  8B-model timeout" pattern already documented for the Delhivery balance sheet pages, now confirmed
  to generalize to a genuinely different document and table shape rather than being specific to
  Delhivery's layout.
- **The facts that WERE extracted are the table's DEFINITIONAL FOOTNOTES, not its numeric rows** —
  e.g. "gross tax revenue collected by the central government minus state governments' share",
  "loans to states for capital expenditure are included". Page 47 (central government operations)
  carries extensive footnotes explaining exactly what each fiscal line item includes/excludes, and
  the extraction call favored these over the adjacent multi-year numeric columns. This is a **real,
  newly-found, generalizable limitation** — not a Delhivery-specific quirk, since Delhivery's own
  tables don't have this footnote density. Root cause: nothing in the extraction prompt weighs
  numeric table rows over descriptive footnote text when both are present and the model can only
  return 15 facts; the model's own judgment call in this instance skewed toward the more narratively
  interesting footnotes. **Not patched** with a one-off "prefer numbers" instruction tuned to this
  page, per the brief's explicit instruction not to chase single-document fixes — recorded as a
  found-and-understood, consciously-not-patched limitation, same treatment already given to the
  Delhivery chart-slide failure.
- **All 12 facts are `fact_validated`, `table_context: reconstructed`** — the evidence-grounding and
  table-reconstruction machinery worked correctly on a table structurally different from anything in
  the Delhivery corpus (footnote-numbered clauses rather than a row/column grid).
- **The adjudicator fired correctly on genuinely new content.** Two real, UI-confirmed examples: (1)
  a `related_but_not_comparable` override — the model proposed `uncertain` for a pair where one
  fact's unit couldn't be parsed at all, and the deterministic adjudicator correctly identified the
  values as structurally incomparable rather than merely uncertain, with `decision_source:
  deterministic_override` and the override reason displayed on the relationship card; (2) an
  `uncertain` (`llm_unchecked`) judgment between two facts about loan/borrowing inclusion criteria,
  correctly deferred to the model's own reading since neither fact carries a numeric value for code
  to check.
- **Cross-document consistency checking worked across independent ingestions of the same source.**
  A fact from this run ("central government debt includes SDR... 0.6 percent of GDP... FY2021/22")
  was correctly compared against a fact from an earlier, separate, much larger ingestion attempt of
  the same IMF report (the same underlying document processed in two different sessions, landing in
  two different `document_id`s) — exactly the cross-document reasoning the whole project is about,
  demonstrated on content nothing in `app/` was written with in mind.

**RBI Annual Report 2024-25 (pages 91, 95) — a genuine, informative failure, then a successful
retry.** First attempt: both of page 91's initial chunks timed out (240s each) while running
*concurrently* with the IMF ingestion above (both competing for the same local Ollama instance) —
root cause: the documented CPU-contention pattern (`PERFORMANCE.md`: concurrent local-model calls
measurably hurt reliability on already-dense chunks), now reproduced with two DIFFERENT documents
contending rather than two chunks of the same one, which is a slightly new manifestation of an
already-understood cause. Cancelled cleanly (`cancel_requested` honored cooperatively, 0 facts lost
since none had completed) and retried sequentially once the IMF run finished; see the retry's own
results directly below, folded in as part of this same generalization run rather than treated as a
separate, cherry-picked "clean" attempt.

## 8a. RBI retry results — completed successfully

Retried sequentially (no longer contending with the IMF run) and completed cleanly: **21 facts**,
67 candidate pairs checked, **22 relationships stored**. Real, inspected findings:

- **Real numeric table values were extracted this time** — unlike the IMF run, which favored
  footnotes. E.g. "Real GDP at Market Prices (% change) = 7.9%" for 2003-04, and the SAME metric
  again at 9.2% for 2022-23, correctly recognized by the pipeline as two points on one timeline
  (see below) rather than two unrelated facts.
- **A genuine table-column-misalignment risk, caught by evidence re-grounding rather than hidden.**
  One fact claims "foodgrains production was 329.7 million tonnes in 2022-23", grounded (after a
  re-grounding retry) against the quote `"I.3 | Foodgrains Production (Million Tonnes)** | 213.6 |
  248.8 | 269.8 | 329.7 | 332.3 | 330.9"` — the model's ORIGINAL quote attempt (just the row label
  and first number) failed verbatim grounding, and the retry recovered the full six-number row
  instead of the single cell. The fact's evidence-status is `fact_validated` because 329.7 does
  appear in that row, but which of the six numbers actually belongs to "2022-23" is exactly the
  "header semantics not modeled" limitation already documented for the Delhivery corpus — now
  independently reproduced on an RBI table with a different structure
  (multi-year columns under a two-row header), confirming this is a genuine, general limitation of
  the current table-reconstruction approach rather than a Delhivery-specific gap.
- **8 quote-grounding issues, 2 re-grounding recoveries, 4 more LLM timeouts** — all logged as real
  `extraction_issues`, not hidden. Consistent with the already-documented failure taxonomy.
- **A genuine cross-period timeline was discovered with zero new code**, live-confirmed in the UI:
  "Foodgrains Production -- Million Tonnes" chained FY2004 → FY2023 (213.6 → 329.7 million tonnes,
  +54.35%), and "India -- inflation rate (General Index, All Groups)" chained FY2023 → FY2025 (6.8%
  → 6.7%, -1.47%) — `app/timeline.py`'s chaining logic, built and tuned entirely against Delhivery's
  FY22/23/24 revenue figures, worked identically on RBI's macroeconomic time series.
- **26 coherence violations, all pre-existing from the Delhivery corpus** — RBI's own facts did not
  introduce new logical inconsistencies in this run, which is itself a mildly positive (if modest,
  given the small fact count) signal about extraction/relationship quality on this document.

**Combined generalization verdict:** across two independent, structurally different documents (a
sovereign macro report and a central-bank regulatory report), the full pipeline — extraction,
table reconstruction, evidence grounding, candidate retrieval, semantic matching, deterministic
adjudication, timeline chaining, graph coherence — ran without any code change specific to either
document, surfaced one new generalizable limitation (footnote-vs-numeric-row extraction competition)
and reconfirmed one already-known one (table header semantics), found zero cases where the
architecture itself needed to change, and produced a genuinely new, correct cross-period timeline
on content nothing in this codebase was written with in mind.

## 8b. Two further fixes made directly because of this generalization run

- **Per-document filtering was missing from Logic Check, Timelines and Extraction Issues** (Priority
  had the backend support but no UI control either). Found while using the app to inspect the IMF/
  RBI results specifically. `/api/coherence` gained a `document_id` parameter (it had none at all);
  all four views gained a document filter in the UI, following the exact pattern already used by
  Facts/Relationships/Graph.
- **A real UI bug: the document detail page visibly flashed/blinked while a document was
  processing.** Root cause, found by reading the polling code rather than guessing: the 2-second
  auto-refresh called the exact same render path as the initial page load, which (a) wiped the whole
  panel to a loading skeleton before every fetch and (b) rebuilt the entire panel via `innerHTML`,
  replaying every count-up number animation and `.rise` fade-in (`opacity: 0 → 1`) on fresh DOM
  nodes each tick. Fixed by giving poll-triggered refreshes a distinct, non-destructive render path
  that updates numbers and content in place without the loading skeleton or the animation replay.

## 9. Four assignment cases

All four re-run against the current code, `cache_hit` reported honestly per script (a `True` means
the relationship *classification* was cached from an earlier run under this exact prompt/logic
version — safe to reuse since nothing about the classification logic changed between that earlier
run and now; a `False` means a fresh LLM call was made during this pass specifically). Full command
output, not summarized:

### Case 1 — Corroboration across documents, different units

```
fact_a: doc=26 id=354 value_numeric=81415.38 unit=INR million
fact_b: doc=19 id=241 value_numeric=8142.0 unit=INR Crore

relation_type: corroborates
confidence: 1.0
decision_source: deterministic_confirmed
llm_proposal: corroborates
disagreement: False -- None
explanation: The normalized numeric comparison shows that Fact A (₹81,415.38 INR million) and Fact B
  (₹8,142 Cr) agree within a tolerance of 0.006%, indicating the same value for FY24.
```

The model and the deterministic layer agree; `decision_source=deterministic_confirmed` means the
1.0 confidence reflects the computed 0.006% agreement, not the model's self-report.

**Found and fixed during this pass:** `scripts/test_case1.py` used to locate Fact A by a text
substring match ("81,415.38" appearing anywhere in a fact's statement), which silently picked up an
unrelated fact from a different, *failed* re-ingest attempt whose statement mentioned the same
digits in passing but whose own `value_numeric` was a mis-extraction (`8,141,538`, off by 100×) —
producing a spurious `reconciled` (different-period) result instead of the real corroboration.
Fixed to match on the VALUE itself within a tight numeric tolerance, and to prefer facts from
successfully completed documents.

### Case 2 — Genuine contradiction, as far as the current data allows

```
net_worth:    doc=26 id=355 value_numeric=85466.74 unit=INR million
total_equity: doc=12 id=174 value_numeric=91446.46 unit=INR

relation_type: uncertain
confidence: 1.0
decision_source: deterministic_confirmed
llm_proposal: uncertain
```

The stored total-equity fact carries an incomplete unit (`"INR"`, missing "million") from a
specific, pre-existing extraction on this page — a real data condition, not a new bug, and already
documented before this pass. `compare_values` flags the ~1,000,000× gap as `magnitude_suspect`;
`app/adjudication.py`'s rule 2 caps ANY proposal at `uncertain` in that case, which is exactly what
both the model and the deterministic layer converge on here — `decision_source=
deterministic_confirmed` shows the two agreeing, not the deterministic layer merely failing to
object. Reporting a confident contradiction off a comparison wrong by a factor of a million would
be the right label for the wrong reason; `uncertain` is the honest answer. A one-off fix to this
specific page's unit (re-ingesting until the extraction happens to come out clean) was deliberately
not chased — see §13. Genuine, undisputed contradictions exist elsewhere in the Delhivery corpus
(30 at last audit, including two transposed director DIN identifiers found with no rule about
director tables anywhere).

### Case 3 — Apparent contradiction reconciled by context

```
standalone:   doc=3 id=215 value=₹66,586.61 million scope='Standalone' period='FY ended March 31, 2023'
consolidated: doc=3 id=216 value=₹72,253.01 million scope='Consolidated' period='FY ended March 31, 2023'

relation_type: reconciled
confidence: 0.8
decision_source: llm_unchecked
llm_proposal: reconciled
reconciliation_context: scope (consolidated vs standalone)
period=same scope=different
-> PASS
```

Values disagree by 7.8%; `app/context.py` determines the scopes are a genuine contrast (standalone
vs. consolidated) in code. `decision_source=llm_unchecked` here specifically because one of the two
facts on this particular ingest doesn't carry a fully validated evidence status — the adjudicator's
rule 3 correctly declines to assert a deterministic verdict off a value that isn't independently
confirmed, and defers to the model, which reaches the right conclusion on its own reading of the
statements. This is rule 8/rule 3 working as intended, not a gap: the deterministic layer only
overrides when it has grounds to.

**Found and fixed during this pass:** the original candidate-matching logic (`"consolidated" in
combined-text AND "revenue" in combined-text`) picked up an unrelated fact from a *different
document entirely* — the IMF report used in the generalization test — because its prose happened to
contain "consolidated state revenue". Fixed to match the scope FIELD itself and require "revenue
from operations" specifically, plus to require both facts share the same stated period (so a period
difference isn't accidentally doing the reconciling that the test means to attribute to scope).

### Case 4 — Real extraction and reasoning failures, found and surfaced

`scripts/test_case4.py` queries two independent, already-running detection mechanisms live rather
than asserting one canned example:

**4a — evidence validation catching an unsupported value:**
```
Fact: "Top 1: Large Credit Exposure by Type of Banks (In percent of Common Equity Tier1) for
       Foreign Banks is 2.2%."
Quote (verbatim, on the page): "0% 5% 10% 15% 20% 25% 30% 35% 40% 45% PSBs Private Banks
       Foreign Banks All SCBs 2017 2023"
(235 such issues in the current corpus)
```
This is a chart/axis-label extraction failure — the quote is genuinely on the page (it's the
chart's axis labels and category names), but it contains no actual data value, so it cannot support
the claimed "2.2%". Root cause: a bar chart's numbers and labels have no positional link once
flattened to text, the same failure mode already documented for a Delhivery earnings-deck chart
slide — now confirmed as a genuine, general limitation by reproducing on IMF content, not a
Delhivery-specific quirk.

**4b — graph coherence proving a logical inconsistency:**
```
278 facts, 401 relationships, 223 closed triangles: 26 logically impossible (11.7%),
65 edges implicated, 145 edges inferable by transitivity

facts (49, 152, 263): corroborates + corroborates + reconciled cannot all hold
(logically impossible under the model's own equality judgments).
```
`is_strict_proof=False` here (visible via the API's `is_strict_proof` field on this violation) —
correctly, since these are qualitative CSR-status facts with no number to check, so this is strong
evidence of an error rather than a strict mathematical proof, exactly the distinction §4 describes.

## 10. Failure cases

Beyond the four required cases, failure modes this pass found, confirmed, and fixed or consciously
left open:

| Failure | Where | Outcome |
|---|---|---|
| Digit-substring evidence false positives | `app/evidence.py` | Fixed — token-level numeric comparison |
| Confidence clamped to a false maximum | `app/schemas.py` | Fixed — rejected to `None` instead |
| `slice_a`/`slice_b` requested but discarded | `app/schemas.py::MetricMatch` | Fixed — fields added |
| Scope contrast-checking crossed unrelated axes | `app/context.py` | Fixed — axis-scoped contrasts |
| `as_of` vs. period-end conflated | `app/context.py::parse_period` | Fixed — split into two kinds |
| Document filename leaked into extraction prompt | `app/fact_extraction.py` | Fixed — removed |
| `pipeline_fingerprint` didn't hash the extraction prompt | `app/config.py` | Fixed — prompt now included |
| Reused-document endpoints returned empty results | `app/main.py` (facts/priority/timelines) | Fixed — `resolve_document_id` |
| Arithmetic grouped by raw period string, not canonical | `app/arithmetic.py` | Fixed — reuses `context.parse_period` |
| Arithmetic group truncation was silent | `app/arithmetic.py` | Fixed — `groups_truncated` reported |
| Table reconstruction rejection wasn't tracked per-fact | `app/tables.py`, `app/pdf_extract.py` | Fixed — `table_context` signal |
| Test scripts matched the wrong candidate fact | `scripts/test_case1.py`, `test_case3.py` | Fixed — see Case 1/3 above |
| Chart/axis-label extraction has no positional link | `app/fact_extraction.py` (chart slides) | Confirmed on IMF content; not fixed — see §13 |
| Table 68's total-equity fact has an incomplete unit | one specific Delhivery extraction | Not fixed — see Case 2 and §13 |
| Extraction favors table footnotes over numeric rows on footnote-dense pages | IMF page 47 | Confirmed generalizable, not fixed — see §8, §13 |
| Table column alignment still depends on the model reading headers correctly | RBI page 91 | Confirmed generalizable (already documented for Delhivery) — see §8a |
| Logic Check / Timelines / Extraction Issues had no per-document filter | `static/index.html`, `static/app.js`, `app/main.py` | Fixed — see §8b |
| Document detail page flashed/blinked while processing (poll re-triggered load animations) | `static/app.js::renderDocument` | Fixed — see §8b |

## 11. Performance measurements

See [PERFORMANCE.md](PERFORMANCE.md) for full detail. Headline: the adjudicator adds roughly six
orders of magnitude less time than the single LLM call it wraps (4.17µs for `adjudicate()` alone,
122.47µs for the full deterministic comparison chain, vs. 30s–several minutes for one local LLM
call) — measured, not assumed, and no optimization work was justified as a result. The
generalization run's real timings are in PERFORMANCE.md's own section once the live run completed.

## 12. Remaining limitations

Carried over from before this pass, still true, not re-litigated: the 8B local model remains the
extraction/reasoning accuracy ceiling; precision is measured by self-consistency (coherence proofs,
corpus re-audits), not hand-labelled ground truth; table reconstruction recovers rows and cells but
not header semantics, so a value's column meaning still depends on the model correctly reading an
aligned header row; relative periods ("previous year") can't resolve without each document's own
extracted reporting date; candidate retrieval is a linear scan appropriate at this scale, not at
real scale (would need an ANN index); chart/infographic slides lose their number-to-label mapping
in plain-text extraction (now confirmed general, not Delhivery-specific — see §9/§10).

New from this pass: the bold contradiction-override rule (§6) will force `contradicts` over a
genuine reconciling reason that's stated only in free prose and not reflected in the structured
period/scope fields — a deliberate, bounded trade-off, not an oversight. `related_but_not_comparable`
and `insufficient_context` are new states that existing consumers of the relationship API (were
there any beyond this project's own UI) would need to handle explicitly rather than falling through
a catch-all.

## 13. What was intentionally NOT implemented, and why

- **Currency/scale/basis/category as separate fact schema columns.** Already captured inside
  `unit`/`scope`/`time_period` and parsed deterministically downstream with no information loss;
  splitting them out would mean changing the extraction prompt (re-extraction risk, more burden on
  an 8B model) for no behavioral gain.
- **Renaming `corroborates`/`contradicts`/`reconciled`/`uncertain`** to alternate naming. Would
  churn 401 existing DB rows, every test, the UI CSS classes and the API `Literal` types for a
  cosmetic difference with zero behavioral change.
- **Re-ingesting the specific Delhivery page behind Case 2's incomplete unit.** Would be a one-off,
  document-specific patch chasing a single known artifact — exactly what the brief says not to do.
  The generalization run's IMF/RBI documents are the actual proof point instead.
- **Full `ClaimIdentity`/bounding-box-coordinate evidence storage.** A larger redesign than this
  pass's scope; the existing text-quote grounding, honestly scoped ("verbatim in the layout-
  reconstructed text shown to the model", not "verbatim in the raw linear PDF stream" — now stated
  precisely rather than implied more strongly), remains adequate for the stated purpose.
- **Widening hybrid candidate retrieval.** Already measured and rejected before this pass
  (`scripts/bench_candidates.py`): 3 pairs of recall for 3× the LLM calls. Re-litigating a measured
  decision without new evidence would violate this project's own "optimize only what's measured"
  rule; only the documentation's overclaim about how far the hybrid signals reach was fixed.
- **A hand-labelled precision benchmark, a vector index, a durable job queue, OCR, multi-agent
  orchestration.** All out of scope per the brief's own anti-overengineering instruction and this
  project's existing, already-honest limitations list — none of these are new omissions introduced
  by this pass.
- **A fabricated multi-dimensional numeric confidence score** (e.g. a made-up `context_confidence:
  0.73`). The adjudication `checks` trace (categorical: which rule fired, what each computed
  verdict was) serves the same "don't collapse uncertainty into one number" goal without inventing
  false numerical precision the brief explicitly warns against.

## 14. A follow-up round: what changed after this document was first written

A later review repeated most of §13's already-declined proposals (a `ClaimIdentity` object, a
7-value relationship taxonomy splitting exact vs. semantic equality, bounding-box evidence
storage, true multi-channel union candidate retrieval, OCR, a 50–100-pair hand-labelled
benchmark) against this same, by-then-submitted codebase. None of those were revisited without
new evidence — see §13, whose reasoning didn't change. Two items **did** change, because the
situation that justified declining them changed:

- **DB-level relationship uniqueness.** §13 declined this because the live database's
  existing-duplicate state hadn't been verified. It has been since: `SELECT fact_id_a, fact_id_b,
  COUNT(*) FROM relationships GROUP BY 1, 2 HAVING COUNT(*) > 1` (and the same check on the
  unordered pair) both returned zero rows against the real corpus. With that risk closed,
  `app/db.py` gained `fact_low_id`/`fact_high_id` columns (order-independent: `min`/`max` of the
  pair, so `(a, b)` and `(b, a)` collide) and a `UNIQUE INDEX` on them, backfilled for all existing
  rows via the same additive `_ensure_column` pattern used everywhere else in this schema.
  `insert_relationship` now catches the resulting `sqlite3.IntegrityError` on a genuine
  concurrent-insert race and returns the winning row's id instead of raising -- the caller's
  computed judgment becomes redundant, not invalid, so a 500 would be the wrong response.
  `relationship_exists()` (checked before a judgment is even computed) is unchanged and still the
  first line of defense; the constraint is the backstop for the race window between that check and
  the insert, which is exactly the gap application-level checks alone can't close. Tests:
  `tests/test_db.py`.
- **Prompt injection defense.** Genuinely missing, not previously evaluated: PDF text is untrusted
  input, and nothing told the model to treat it as such. The extraction system prompt
  (`app/fact_extraction.py`) and both relationship prompts (`app/relationships.py`) now explicitly
  state that document/fact text may contain text crafted to look like an instruction and must be
  treated as data to extract-from-or-judge, never as a command. The structural defense was already
  correct (chunk text has only ever been placed in the user message, never concatenated into the
  system prompt), so this closes the instructional gap rather than a structural one.
  `tests/test_fact_extraction.py::TestPromptInjectionCannotEscapeTheUserContent` asserts that
  property directly: the system prompt sent to the model is byte-identical to the fixed constant
  regardless of chunk content, and injected text lands only inside the delimited page-text section
  of the user message.

Everything else proposed in that follow-up round is declined for the same reasons §13 already
gives, now with one addition: rewriting the fact model around a `ClaimIdentity` object, splitting
`corroborates` into exact/semantic variants, and replacing candidate retrieval would each touch
nearly every module in `app/` at once, on a codebase that was already feature-complete and
submitted -- exactly what this project's very first instruction said not to do ("do NOT redesign
the entire project from scratch. The current architecture is already strong").
