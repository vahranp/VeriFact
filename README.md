# Fact Knowledge Layer

A system that reads PDFs, extracts facts grounded in exact quotes from the source, and figures
out — across documents, or across pages of the same document — which facts **corroborate** each
other, which **contradict**, and which look like a contradiction but are actually **reconciled**
by different time periods, scope, or units. Built for the Superjoin VIT 2026 Engineering Intern
assignment.

Tested against the three Delhivery documents provided as the starter dataset (a 2022 IPO
prospectus, the FY24 annual report, and the Q4 FY24 earnings deck), but nothing in the pipeline
is specific to Delhivery, financial documents, or any fixed fact taxonomy — see [Approach](#approach)
for how that's enforced.

**Delhivery is a demonstration/test corpus, not a production dependency.** Production logic in
`app/` does not depend on Delhivery-specific facts, filenames, pages, or schemas — stated
explicitly here because it's a claim worth being able to check, not just assert. See
[Generalization](#generalization-how-not-specific-to-delhivery-is-actually-enforced) for how it's
verified, including an end-to-end run on genuinely unseen, non-corporate-financial documents (an
IMF Article IV macroeconomic report and an RBI annual report) that this codebase was never written
with in mind.

---

## How it works, in one diagram

```
PDF ──► page text ──► overlapping chunks ──► [LLM] extract facts ──► verify quote
        (PyMuPDF)     (+ page context:          one call per chunk,   is verbatim
                       carries "all amounts     structured JSON       in the source
                       in millions" headers                           (checked in code,
                       into later chunks)                              not by the model)
                                                        │
                                                        ▼
                                        ┌──── arithmetic self-validation ────┐
                                        │  discovers a + b = c among the     │
                                        │  extracted numbers. No accounting  │
                                        │  rules encoded — if the identities │
                                        │  close, the figures were read      │
                                        │  correctly; near-misses off by a    │
                                        │  power of ten flag a unit misread. │
                                        └────────────────────────────────────┘
                                                        │
                                                        ▼
                        embed facts ──► candidate pairs ──► deterministic unit
                        (MiniLM)        (top-4 cosine,      normalization computes
                                         + why each pair    the comparison IN CODE
                                         was retrieved)     (₹8,142 Cr vs ₹81,415.38M)
                                                        │
                                                        ▼
                             [LLM] step 1: same metric?  ──no──► unrelated (stop, no 2nd call)
                                                        │yes
                                                        ▼
                             [LLM] step 2: PROPOSES corroborates / contradicts /
                                   reconciled / uncertain, given that computed comparison
                                                        │
                                                        ▼
                          ┌──── deterministic adjudicator (app/adjudication.py) ────┐
                          │  checks the proposal against the SAME computed values,   │
                          │  period and scope the prompt was given. Confirms it,     │
                          │  OVERRIDES it when the facts are conclusive, or defers   │
                          │  to it when they aren't (e.g. a genuinely qualitative    │
                          │  pair). Disagreement is stored, never hidden.            │
                          └──────────────────────────────────────────────────────────┘
                                                        │
                                                        ▼
                          ┌──────── logic check on the whole graph ────────┐
                          │  equality is transitive, so A=B and B=C means  │
                          │  A=C. A triangle saying otherwise means one    │
                          │  of its three judgments is wrong — no ground   │
                          │  truth, no reviewer, no extra model call. The  │
                          │  same rule deduces edges retrieval never       │
                          │  shortlisted. (app/coherence.py)               │
                          └────────────────────────────────────────────────┘
```

**The four design decisions that matter most:**

1. **The model never has the final word on a computable answer.** Unit, period and scope
   comparisons are computed deterministically (`app/normalize.py`, `app/context.py`) and handed to
   the model as a finished comparison — but the model's step-2 answer is still only a *proposal*.
   `app/adjudication.py` checks it against those same computed facts and overrides it when they're
   conclusive (see [Relationship reasoning](#relationship-reasoning) below). Asking an 8B model to
   recognise "net worth = total equity" *and* convert crore to million *and* classify the
   relationship *and reliably follow an instruction to defer to a computed number* in one call
   reliably failed at exactly the points where those sub-tasks interact.
2. **Every quote is verified in code**, not trusted from the model. A fact whose quote isn't
   found verbatim in the source page is re-grounded once, and flagged unverified if that fails.
3. **Disagreement between the model and the deterministic layer is stored, not hidden.** Every
   relationship carries `decision_source` (did a check apply, and did it agree with the model?),
   the model's original proposal, and — when they differ — why. A reviewer can always see both
   opinions, not just the one that won.
4. **The graph checks itself.** Relationships are judged pairwise and in isolation, so the graph
   they form can be internally impossible. Those impossible triangles are strong evidence of
   error — stronger than a confidence score, because the system produces evidence of its own
   mistakes rather than an opinion about them (see [Graph coherence](#graph-coherence-as-a-consistency-check-not-ground-truth)
   for exactly how strong, which depends on what's actually being asserted).

### The design principle

**The LLM proposes. Deterministic code verifies, normalizes, adjudicates and challenges it.**

The model is used only where language understanding is genuinely required — deciding whether
"net worth" and "total equity" name the same concept, or whether a stated reason explains a
difference. It is not used for anything with a computable answer, and — as of this pass — it is
no longer trusted to police itself on the instruction to defer to one: code checks that too.

| Question | Answered by | Why |
|---|---|---|
| What facts does this page state? | LLM | Genuinely open-ended |
| Is this quote really in the source? | Code | Exact string search; no judgment needed |
| Does the quote support the value? | Code | Numeric-token comparison; the model checking itself would inherit its own error |
| Is ₹8,142 Cr the same as ₹81,415.38M? | Code | Arithmetic |
| Are FY24 and FY2023-24 the same period? | Code | Calendar logic |
| Is "net worth" the same as "total equity"? | **LLM** | Requires world knowledge |
| Do these figures add up? | Code | Arithmetic |
| Given matching values and matching context, is this a corroboration? | **Code** | A computed fact once semantic equivalence is settled — the model proposes, code confirms or overrides |
| Is this set of judgments self-consistent? | Code | Logic |

Every deterministic result is handed *to* the model as a finished answer rather than left for it
to infer. The judge prompt receives "these agree to 0.006%", "period: SAME", "scope: UNKNOWN" —
it does not receive two numbers and a hope. And where that computed answer is conclusive, the
model's own response to it is checked afterward, not merely requested beforehand.

### Three ideas the project actually contributes

**Deterministic adjudication** (`app/adjudication.py`, new in this pass). The two-step
classification below still asks the model to propose a relationship — but the proposal is now
checked against the exact same computed comparison it was given, and overridden when that
comparison is conclusive (values equal + context equal → `corroborates`, full stop, regardless of
what the model says; values different + context confirmed equal → `contradicts`, even if the model
invents a reconciling story). Every relationship stores `decision_source`
(`deterministic_confirmed` / `deterministic_override` / `llm_unchecked`) and, on an override, the
model's original proposal and why it was overruled. This is what turns "the LLM proposes,
deterministic code verifies" from a design aspiration into something enforced in code rather than
requested in a prompt.

**Arithmetic self-validation** (`app/arithmetic.py`). Documents are full of internal invariants —
a total equals the sum of its parts. Nothing tells the system those identities exist; it searches
for `a + b ≈ c` among facts sharing a unit, period and scope. On the real balance sheet it
discovered `liabilities + equity = assets` and `share capital + other equity = total equity`,
both at 0.00% error, with no accounting rules encoded anywhere. That is an independent consistency
signal, not proof the extraction is correct — an identity can hold by coincidence, which is exactly
why real, unrelated numbers are excluded by construction in the tests (see `test_arithmetic.py`).
A near-miss resolving only if one value is rescaled by a power of ten is a specific signal: a
denomination misread, not a data conflict.

**Graph coherence** (`app/coherence.py`). `corroborates` asserts equality, and equality is
transitive, so a triangle with two `corroborates` and one `contradicts` cannot occur in a correct
graph. Where every `corroborates` edge in that triangle rests on a deterministically-confirmed
numeric equality, that is a strict proof one judgment is wrong — no ground truth, no reviewer, no
extra model call. Where a `corroborates` edge instead rests on the model's own qualitative "same
status" reading (two non-numeric facts, no number to check), the triangle is still strong evidence
of an error, but calling it a mathematical proof would overstate what a same-status *judgment*
establishes versus a same-*value* computation — `Violation.is_strict_proof` reports which case
applies, rather than treating every violation the same way. On the live graph: 196 closed
triangles, 25 logically impossible, 62 edges implicated, and 126 further edges deducible for free.

All three are instances of one principle: arithmetic, logic, and now direct numeric/context
comparison, checking the model's work rather than trusting its report of having checked its own.

## Setup and Run Instructions

**Requirements:** Python 3.11+, and either [Ollama](https://ollama.com) installed locally (default,
free, no API key) or an API key for an OpenAI-compatible provider (OpenRouter, OpenAI, etc.).

```bash
# 1. Clone and enter the repo
git clone <this-repo-url>
cd superjoin-fact-layer

# 2. Create a virtual environment and install dependencies
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# 3. Pull a local model (only needed if using the default Ollama backend)
ollama pull llama3.1:8b

# 4. Configure
copy .env.example .env         # Windows
# cp .env.example .env         # macOS/Linux
# Defaults already point at local Ollama — no edits needed to just run it.
# To use a hosted provider instead, see the commented block at the bottom of .env.example.

# 5. Run
uvicorn app.main:app --reload --port 8000
```

Open **http://127.0.0.1:8000** — upload a PDF, watch it process (status polls automatically),
then browse Facts / Relationships / Extraction Issues.

For a large PDF, the upload form accepts an optional page limit (`max pages`) or an explicit page
selector (e.g. `1,3,7-10`) so you can target specific content instead of paying to process every
page. The API supports the same via `POST /api/documents?pages=1,3,7-10`.

## Approach

### What counts as a "fact," and why the schema is generic

A fact is stored as `{subject, attribute, value, value_numeric, unit, time_period, scope,
statement, quote, evidence_status, confidence}` rather than as fixed columns like `revenue` or
`director_name`. `attribute` is an open vocabulary the model fills in per page — that's what the
assignment asks for ("the documents should guide what counts as a fact") and it's what lets a
brand-new PDF about a completely different subject introduce new kinds of facts without a schema
migration.

### Evidence grounding: two questions, not one

Every fact carries a `quote`: a verbatim excerpt from its page. But checking that a quote *exists*
is much weaker than checking that it *supports the fact*, and treating the two as equivalent hid
real errors. Measured across 336 extracted facts, two failure classes were sitting behind a green
"quote verified" badge:

```
value = 779    quote = "Number of complaints filed during the year"     ← a table ROW LABEL
value = 35.69% quote = "35.69%"                                          ← circular: the quote IS the value
```

The first is not the model inventing text — the quote is genuinely on the page. It's a row label,
and the number lives in a cell the PDF flattening separated from it.

So grounding is two independent verdicts (`app/evidence.py`):

| status | meaning |
|---|---|
| `ungrounded` | the quote is not verbatim in the source |
| `quote_grounded` | the quote is real, but does not support the extracted value |
| `fact_validated` | the quote is real **and** supports the value |

Only the value can downgrade a fact. Unit, subject and period are computed and reported but never
used to reject, because all three are routinely stated once in a table header rather than inside
the quoted sentence — rejecting on their absence would flag most correct facts.

No LLM call is made to check the LLM. Asking the model whether it was right about its own output
would inherit the very error being looked for.

### Table structure: recovering what flattening destroys

The row-label failure above traces to PDF text extraction. Reading-order text collapses a grid
into a vertical token stream, and every column relationship is gone:

```
FY24 / FY23 / FY22 / Male / Female / Total / Male / ... / Permanent Employees / 35.69% / 45.15% / ...
```

`app/tables.py` recovers rows by clustering word coordinates on their vertical midpoint and cells
by splitting on horizontal gaps, giving:

```
FY24 | FY23 | FY22
Male | Female | Total | Male | Female | Total | Male | Female | Total
Permanent Employees | 35.69% | 45.15% | 36.36% | 41.93% | 43.26% | 42.02% | ...
```

Multi-column pages are split at their gutters first — without that, a page holding two side-by-side
tables produces rows splicing cells from both, which is worse than the flattened text because it
invents adjacency the page never had.

**Measured effect on page 52:** evidence-validated facts went from **2 of 17 (12%) to 11 of 13
(85%)**. Fewer facts, and that is the point — the flattened run was producing facts it could not
evidence.

It runs *only* on pages that look tabular, and falls back to plain text if reconstruction loses
content, so prose pages are untouched.

### Numeric normalization

`app/normalize.py` reduces `(value, unit)` pairs to a common base deterministically — Indian and
international scale words (crore, lakh, million, billion), currency symbols and codes, percentages
and basis points, commas, parenthesised negatives. `original_unit` is preserved alongside the
normalized value; nothing about the source representation is destroyed.

It refuses rather than guesses: an unparseable unit, or two units that don't reduce to a common
base, returns "not comparable" instead of a fabricated percentage difference. And when two values
for the same metric differ by more than 100×, it flags the comparison as a probable unit-metadata
error rather than reporting a confident contradiction — a right answer for the wrong reason is
worse than an admitted gap.

### Temporal and scope normalization

"FY24", "FY2023-24" and "fiscal year ended March 31, 2024" are one period written three ways, and
a model comparing those strings has no reason to know it. `app/context.py` computes period and
scope compatibility in code and hands the verdict to the reasoning step:

| relation | example |
|---|---|
| `same` | FY24 vs FY2023-24 |
| `different` | FY24 vs FY23 |
| `overlapping` | Q1 FY25 inside FY25; a fiscal year against a calendar year |
| `unknown` | "previous year" — unresolvable without the document's own reporting date |

**Unknown is a real answer and never guessed.** Inventing precision here is worse than admitting
the gap: a wrong "different period" reading turns a genuine contradiction into a false
reconciliation, so the system would explain away a real conflict.

A point-in-time snapshot and a period ending on the same date are also kept distinct: "cash
balance as of March 31, 2024" and "revenue for the year ended March 31, 2024" share a calendar
anchor but report fundamentally different things (an instant vs. a twelve-month flow) — conflating
them used to be possible (both parsed to the same internal period kind); they're now separate kinds
that compare as `overlapping`, not `same`.

Scope uses contrastive *axes* (consolidated/standalone, gross/net, continuing/discontinued on one
axis; actual/forecast, current/restated on another — labeled `scope` vs. `basis` respectively)
rather than an accounting ontology. Words from one side of an axis are synonyms — "consolidated"
and "group" name one scope — and a contrast is only checked *within* an axis, never across two
unrelated ones (a fact recorded as "gross" and another as "actual" are not opposites; a figure can
be both at once). An unfamiliar qualifier is compared as a plain token rather than dropped, so
another domain's vocabulary still works.

### Relationship reasoning

```
Fact A + Fact B
   │
   ├─ candidate retrieval (embedding + entity + lexical + numeric signals)
   │     records WHY the pair was selected
   ▼
STEP 1 — same metric?  [LLM]
   │   names each fact's SLICE first (segment / geography / "whole"),
   │   because a part compared against its own total manufactures a
   │   false contradiction out of an expected difference
   ├─ no ──► unrelated (stop — step 2 is never called)
   ▼ yes
DETERMINISTIC CHECKS  [code]
   normalized value comparison · period comparison · scope comparison
   ▼
STEP 2 — PROPOSE how they relate  [LLM]
   given those three computed verdicts, not asked to derive them
   ▼
ADJUDICATE  [code, app/adjudication.py] — checks the proposal against
   the same computed verdicts. 8 precedence rules, first match wins:
   comparable? → related_but_not_comparable if not (both numeric)
   magnitude-suspect? → cap at uncertain
   evidence unverified? → cap a confident corroborates/contradicts at uncertain
   values agree + period/scope not DIFFERENT → corroborates (confirm or OVERRIDE)
   values differ + period=SAME + scope=SAME → contradicts (confirm or OVERRIDE)
   values differ + period/scope confirmed DIFFERENT → reconciled (confirm or OVERRIDE)
   values differ + period/scope UNKNOWN → insufficient_context (confirm or OVERRIDE)
   nothing conclusive (e.g. qualitative facts) → defer to the model entirely
   ▼
corroborates · contradicts · reconciled · uncertain ·
related_but_not_comparable · insufficient_context
   (+ decision_source, the model's original proposal, and why they differ if they do)
   ▼
GRAPH COHERENCE  [code] — challenges the result against every other judgment
```

Splitting classification into two calls was not free, and was done because a single call reliably
failed at the point where its sub-questions interact: the model could recognise two terms as
synonymous and then fail to notice the values disagreed. Step 2 is skipped entirely when step 1
says no, which is the common case, so the second call is only paid on pairs that were going to
produce a real answer.

**The step-2 model call is a proposal, not a verdict.** Before this pass, the model's step-2 answer
*was* the stored relationship — the deterministic comparisons above were shown to it as text, and
it was trusted to actually defer to them. Nothing checked that it had. `app/adjudication.py` closes
that gap: it re-applies the same period/scope/value logic in code and only accepts the model's
answer when either (a) it agrees with what code independently concludes, or (b) code has nothing
conclusive to say (a genuinely qualitative pair, or values that agree while context genuinely
differs). Everywhere the deterministic facts *are* conclusive, code has the final word — including
overriding a confidently-worded model explanation. See
[LLM vs. system disagreement](#llm-vs-system-disagreement-a-signature-feature-not-a-footnote) below.

**Two states beyond the original four.** `related_but_not_comparable` (same metric, but the units
structurally can't be reduced to one base — e.g. a percentage against an absolute count) and
`insufficient_context` (values differ, but period or scope didn't resolve, so contradiction vs.
reconciliation can't be told apart) used to both collapse into `uncertain`. Splitting them out means
"the numbers structurally can't be compared" is no longer indistinguishable from "the model
wasn't sure" or "we don't have enough context to judge a real difference" — three different
findings that call for different follow-up.

**`uncertain` is still a real verdict**, now specifically for the residual case: a genuinely shaky
semantic judgment, or a magnitude-suspect comparison that's more likely a unit-metadata artifact
than a real conflict. A system that can only answer corroborates / contradicts / reconciled must
force every judged pair into one of them, and that failure is silent; naming the specific reason
something is unsettled is more useful than one catch-all bucket for everything unsettled.

### LLM vs. system disagreement: a signature feature, not a footnote

Every relationship stores three things a reviewer can compare directly: what the model proposed in
step 2, what the deterministic adjudicator decided, and — when they differ — why. This is
deliberately not hidden in a log line; `disagreement`, `llm_proposal` and `disagreement_reason` are
first-class API/UI fields (`GET /api/relationships`, the relationship card in the UI). A relationship
where the system overrode its own model is treated as high-priority for review (`app/priority.py`)
regardless of what the final label turned out to be — the fact that a disagreement happened is
itself the interesting signal, independent of which side was right.

`decision_source` records which of three things happened:

| value | meaning |
|---|---|
| `deterministic_confirmed` | a computed check applied, and the model's proposal already agreed with it |
| `deterministic_override` | a computed check applied and DISAGREED with the model's proposal — code's answer is what's stored |
| `llm_unchecked` | no deterministic check was conclusive (typically a qualitative, non-numeric pair) — the model's own reading stands, honestly labeled as unchecked rather than implicitly treated as equally certain |

A deterministically confirmed or overridden relationship is stored with `confidence: 1.0` —
not because the model was certain, but because the label is a computed fact, not an estimate. Only
`llm_unchecked` relationships keep the model's own stated confidence, and that number is exactly
that: the model's *stated* confidence, not a calibrated probability (see
[Limitations](#limitations-and-next-steps)).

### The four required cases

All four are produced by the generic pipeline against the real starter PDFs. No expected
relationship is hard-coded anywhere in `app/`; the regression scripts locate facts by content
(matched on the actual VALUE for case 1, not a text substring — see the case-1 note below), never
by document or fact id, so they stay meaningful across re-ingests.

Run them yourself: `python scripts/test_case1.py`, `test_case2.py`, `test_case3.py`, `test_case4.py`.
Every result below is copied from an actual run against the current code, made after clearing
nothing but with the relationship-classification cache naturally invalidated by this pass's prompt
changes (the cache key includes the literal prompt text, so a changed prompt can never silently
serve a stale judgment) — `cache_hit=False` in each script's own output confirms the judgment shown
was actually computed by the code being submitted, not replayed from before.

---

**CASE 1 — Corroboration across documents, expressed in different units.** ✅ verified live, fresh LLM call

| | |
|---|---|
| Fact A | Annual Report, FY24 revenue from operations = **₹81,415.38 million** |
| Fact B | Q4 FY24 earnings deck, revenue from services = **₹8,142 crore** |
| Result | **`corroborates`, confidence 1.0** |
| `decision_source` | `deterministic_confirmed` — the model also proposed `corroborates`; code confirmed it from the numbers, not merely trusted the model's word |
| Reasoning | Normalized comparison: 8.14154e10 vs 8.142e10 INR — agree within 0.006% |

Nothing about crore is special-cased. `app/normalize.py` reduces both to a common base through the
same scale-word table that handles lakh, million and billion, and the judge receives the finished
comparison rather than two numbers. `scripts/test_case1.py` locates Fact A by its VALUE (within a
tight numeric tolerance of 81,415.38), not a text substring — a substring match once picked up an
unrelated fact from a *different, failed* re-ingest attempt whose statement happened to mention the
same digits in passing, silently testing the wrong pair. Matching on the number itself is what the
test is actually supposed to pin down anyway.

---

**CASE 2 — Genuine contradiction, as far as the data currently allows.** Net worth (₹85,466.74M,
sustainability report) against total equity (₹91,446.46M, balance sheet). For a company these name
the same thing, and the ~₹6bn gap has no stated explanation.

The stored total-equity fact currently carries an incomplete unit (`"INR"` rather than `"INR
million"`), a real, pre-existing extraction artifact on this specific ingested page. Fresh, live
run against the current code:

| | |
|---|---|
| Result | **`uncertain`** |
| `decision_source` | `deterministic_confirmed` — the model also proposed `uncertain`; `compare_values` independently flagged the ~1,000,000× gap as `magnitude_suspect` |

**That is the correct behaviour, not a failure to detect.** `app/adjudication.py`'s rule 2 caps
*any* proposal at `uncertain` when the comparison is magnitude-suspect, regardless of what the model
says — reporting a contradiction off a comparison that is wrong by a factor of a million would be
the right label for entirely the wrong reason, and it would hide a real extraction bug behind a
plausible-looking finding. Re-ingesting this specific page to pick up a corrected unit is exactly
the kind of one-off, document-specific fix this pass deliberately did not chase (see
[What was intentionally not implemented](FINAL_REVIEW.md)) — the fresh IMF/RBI generalization run
below produces genuinely fresh contradiction/reconciliation examples on data this pass never
touched. Genuine unexplained contradictions *are* found and stored elsewhere in the corpus — 30 in
the Delhivery corpus, including two directors whose DIN identifiers are transposed between
extractions, found by comparing facts against each other with no rule about director tables
anywhere.

---

**CASE 3 — Apparent contradiction reconciled by context.** ✅ verified live, fresh LLM call

| | |
|---|---|
| Fact A | Standalone revenue from operations, FY ended March 2023 = ₹66,586.61 million |
| Fact B | Consolidated revenue from operations, FY ended March 2023 = ₹72,253.01 million |
| Result | **`reconciled`** |
| `decision_source` | `llm_unchecked` — one of the two facts on this specific ingest doesn't carry a fully validated evidence status, so the adjudicator (correctly) declines to assert a deterministic verdict off an unverified value and defers to the model's own reading, which independently reached the same, correct conclusion |
| Reconciling context | scope difference — standalone vs consolidated |

The values genuinely disagree (7.8%). What makes this a reconciliation rather than a conflict is
that `app/context.py` determines the scopes are contrastive *in code* and tells the judge that a
difference is therefore expected. `scripts/test_case3.py` matches facts on the *scope field itself*
and requires "revenue from operations" specifically (a looser match once paired an unrelated fact
from a completely different, non-Delhivery document merely because its prose happened to contain
the words "consolidated" and "revenue"), and pairs a standalone/consolidated fact sharing the SAME
stated period so period isn't accidentally doing the reconciling instead of scope. The same
machinery reconciles FY24-vs-FY23 pairs on period, and a `1.4 Mn Tons` vs `8 thousand tons` pair on
unit.

---

**CASE 4 — Real extraction and reasoning failures, found and surfaced.** `scripts/test_case4.py`
queries the live database for two independent, already-running failure-detection mechanisms rather
than asserting a single hardcoded example: an evidence-validation catch (a quote genuinely on the
page that doesn't support its claimed value) and a graph-coherence violation (a logically
impossible triangle of relationship judgments). Both are described in full below.

### Extraction/reasoning failures actually found

#### Found during the final hardening pass

- **Evidence that was verified but didn't evidence anything.** The system reported facts as
  "quote verified" whenever the quote existed in the source. Measured across 336 facts, **16
  carried a value absent from their own quote** (`value = 779`, `quote = "Number of complaints
  filed during the year"` — a table row label) and **105 had a quote that was just the value**
  (`quote = "35.69%"`), which restates a fact rather than evidencing it. **Handling:** grounding
  split into `quote_grounded` and `fact_validated` (`app/evidence.py`); both classes are now
  visible in the UI instead of showing a green tick.

- **Table flattening as the root cause.** The row-label failures traced to PDF text extraction
  collapsing grids into vertical token streams. **Handling:** row/cell reconstruction from word
  coordinates plus gutter detection (`app/tables.py`). Evidence validation on page 52 went from
  **2/17 (12%) to 11/13 (85%)**.

- **A cache that served results the pipeline would no longer produce.** After extraction changed,
  re-uploading a page *to measure the improvement* returned the old facts, because the file's bytes
  hadn't changed and document-level dedup runs before chunking. **Handling:** a pipeline
  fingerprint now participates in reuse, covering what determines extraction output and excluding
  what doesn't.

- **A regression I introduced, caught by re-testing rather than by tests.** Adding period/scope
  determinations to the judge prompt broke Case 1: the block appended "a material difference would
  not be explained by context" whenever periods matched — *including when the values agreed to
  0.006%*. Case 1 went from `corroborates` to `contradicts`. **Handling:** the block now takes the
  value verdict and says nothing about explaining a difference when there isn't one. This was
  invisible to a test suite that mocks the LLM, and only surfaced because the required cases were
  re-run against the current implementation instead of assumed still passing.

- **A recovery mechanism that destroyed what it was meant to recover — twice.** Orphaned-job
  cleanup was first called from `db.init_db()`, so a shell query killed a running extraction. Adding
  a once-per-process guard didn't help: running the test suite starts a FastAPI `TestClient`, which
  fires the startup hook, which swept the live job again. **Handling:** orphan detection is now
  based on staleness of the last progress write, which is the only signal correct across processes.

- **JSON recovery that produced the wrong type instead of failing.** `_extract_json_block`
  preferred `[` over `{`, so a malformed object whose strings contained a bracket was carved into
  that fragment — `{"reason": "values [1] and [2] differ",}` became `[1]`, which parses cleanly to
  a list, so callers died on `.get()` with an `AttributeError` rather than the `LLMParseError` they
  handle. **Handling:** take whichever delimiter opens first, and track string literals so brackets
  inside quotes are data.

#### Found earlier

- **Relationship-classification false positives on same-page, same-subject facts.** The clearest
  failure found: the reasoning model classified "Mr. Sahil Barua... is liable to retire by
  rotation at the ensuing AGM" (Annual Report, p.24) and "the Board recommends re-appointment of
  Mr. Sahil Barua at the ensuing AGM" (same page) as **contradicts**, with 0.9 confidence and the
  explanation "liable to retire by rotation" vs. "eligible for re-appointment" are incompatible.
  They are not — retiring by rotation and then being recommended for re-appointment is standard,
  sequential Indian corporate-governance practice (Companies Act s.152(6)), not a conflict. The
  same run also mis-labeled two clearly unrelated fact pairs as **reconciled** (an Independent
  Director's *declaration* status vs. their separate *registration* status; Bhiwandi vs. Bengaluru
  gateway warehouse sizes, two different locations) — both are cases of "these facts share a
  subject but aren't actually in tension," which is `unrelated`, not something needing
  reconciliation. **Handling:** rewrote `SYSTEM_PROMPT` in `app/relationships.py` to default to
  `unrelated` unless two facts assert the *same* real-world claim, to explicitly name the
  retire-by-rotation/re-appointment pattern as a non-conflict example, and to require `contradicts`
  to first rule out "could both statements simply be true at once." Re-running the exact same
  three fact pairs through the new prompt (`scripts/retest_relationships.py`) now correctly returns
  `unrelated` for all three. This is kept in the repo as a live before/after: the flawed
  classifications are still visible by relation_type in the Relationships tab (they predate the
  prompt fix and weren't deleted), and the fixed prompt is what a fresh upload now uses.
- **JSON shape collapse under grammar-constrained decoding.** Ollama's `format="json"` mode
  (which grammar-constrains output to valid JSON) measurably biased the local 8B model toward
  emitting a single JSON *object* instead of the array of facts the prompt asked for — tested
  head-to-head on the same page/prompt: 0 recoverable facts with `format="json"` vs. a full
  multi-fact array without it. **Handling:** extraction now runs without the format constraint,
  relying on prompt instructions plus a brace-matching JSON recovery pass. As a safety net for
  when a single-object response still slips through, `_recover_non_list_shape` detects it and
  either treats a lone fact-shaped object as a one-element list or unwraps a `{"facts": [...]}`
  wrapper — logged as `unexpected_shape_recovered` in the Issues tab so it's visible, not silent.
- **Paraphrased-instead-of-copied quotes.** The model sometimes states a real fact but "quotes" a
  loosely paraphrased version rather than the exact page text (caught via the grounding check
  described above). These are kept (the fact itself may still be correct) but flagged
  `quote_grounded: false` in the UI, so a human reviewing results knows which evidence links to
  double-check rather than trust blindly.
- **Table column-misalignment during extraction (found via the system's own contradiction
  detection, then root-caused by hand).** A BRSR table on p.52 lists employee turnover rate for
  three fiscal years (FY24/FY23/FY22), each split into Male/Female/Total sub-columns, flattened
  by PDF text extraction into one run of 9 numbers following the year/gender headers. One
  extraction run correctly read FY24 = {Male 35.69%, Female 45.15%, Total 36.36%}; a later
  extraction run (different chunk boundaries, same underlying text) misread the *same table* as
  FY24 = {Total 42.02%, Male 41.93%, Female 43.26%} — actually the FY23 column, shifted one
  year-group to the left. The system's own contradiction-detection flagged the mismatch between
  runs (3 `contradicts` relationships, e.g. relationship ids 99/102/103); tracing *why* two
  extractions of identical text disagreed led to this root cause, hand-verified against the raw
  table text (`repr()`-dumped to see the literal `\n`/`\t` structure). **Why it matters:** a table
  with a header hierarchy (year → gender) loses that structure once flattened to plain text, and
  an LLM reading the flattened sequence has no visual/positional signal to keep the mapping
  correct — a real, generalizable limitation of plain-text PDF extraction for anything but simple
  single-header tables, not specific to this one page. **Not fully "handled"**: no code change
  fixes this (it would need layout-aware extraction, e.g. PyMuPDF's table-detection APIs, or an
  extraction prompt that's shown the table's 2D structure instead of a flat token stream) — logged
  here as a found, understood, and consciously *not* patched failure, with the fix path named
  rather than faked.
- **Chart/infographic slides lose their number-to-label mapping even more severely.** The Q4
  earnings deck's bar-chart slides (e.g. p.9) extract as a bare sequence of numbers and category
  labels with no positional link between them (`7,054 7,224 8,142 FY22 FY23 FY24 Express Parcel
  PTL TL SCS Cross Border Revenue from services...`) — correctly attributing each number to its
  year and segment requires the chart's visual layout, which plain-text extraction discards
  entirely. Confirmed by hand: even careful manual reading of the flattened text left genuine
  ambiguity about which numbers were revenue vs. tonnage. The extraction call on this slide also
  reliably timed out (dense, ambiguous content, same density-not-length pattern as the financial
  tables). **Handling:** rather than fight this page, a different, cleaner slide in the same deck
  (p.6, a "key highlights" slide with short labelled callouts like "₹8,142 Cr — FY24 revenue from
  services") was used instead — it extracted cleanly on the first try. Documented as a real,
  legitimate limitation of plain-text extraction for chart-derived numbers, with a working
  alternative rather than a forced fix.
- **A prompt fix that didn't generalize (found through direct, repeatable testing).** After
  teaching the reasoning prompt that "net worth" and "total equity" are the same accounting
  concept (fixing that one specific pair — see case 2), the *same kind* of terminology gap
  immediately reappeared on a different pair: "revenue from operations" (Annual Report) vs.
  "revenue from services" (earnings deck) was still classified `unrelated`, with the model
  incorrectly asserting one fact specified a "standalone" scope it never stated. A second prompt
  revision generalized the instruction (business/accounting terms routinely have several names
  for one concept: revenue = turnover = sales = revenue from operations/services; net worth =
  total equity = shareholders' equity — compare values instead of defaulting to unrelated on
  wording alone) and was retested on the same pair (`scripts/test_case1.py`) — it *still* returned
  `unrelated` with the same hallucinated "standalone" claim. **Left honestly unresolved**: this is
  a real ceiling of what a general instruction can teach an 8B local model through prompting
  alone within a single classification call; a more reliable fix would likely need either a larger
  reasoning model (see Model considerations) or splitting the judgment into two explicit LLM
  steps (first: "could these labels denote the same metric?", then: "given they might, do the
  values agree?") rather than asking for both judgments at once.
- **Literal control characters break strict JSON parsing.** PDF table text often contains literal
  tab characters (used for column alignment); the extraction prompt asks the model to copy quotes
  character-for-character, so a model doing exactly that can embed a raw tab inside a JSON string
  — which Python's default strict-mode `json.loads` rejects as invalid, even though the model did
  precisely what was asked. Found while debugging a page-52 chunk that kept timing out: once a
  smaller window of the *same* text finally got a fast response, that response failed to parse
  with `Invalid control character at: line 47 column 19`. **Handling:** `app/llm_client.py` now
  parses with `json.loads(content, strict=False)`, which accepts literal control characters in
  strings — a one-line fix, but one that required first ruling out the timeout as the only
  problem on that chunk.
- **What I'd improve:** a second, cheap verification pass that re-checks each fact's quote against
  the page and asks the model to fix ungrounded ones before they're stored, instead of just
  flagging them after the fact; stricter numeric-value validation (e.g. cross-checking
  `value_numeric` actually appears in `quote`); layout-aware table extraction (see above) for
  documents that are mostly tables; and splitting relationship classification into the two-step
  "could these be the same metric, then do the values agree" judgment named above.

### Key engineering decisions & trade-offs

- **Local Ollama over a hosted API, discovered mid-build, not planned upfront.** The original plan
  used OpenRouter (`gpt-4o-mini` for extraction, `claude-sonnet-4.5` for reasoning). Once wired up,
  the exact same extraction request against `gpt-4o-mini` returned real results 1 out of 7 times
  and empty results the other 6 — traced to the OpenRouter account's `$0` credit balance
  (confirmed via its `/credits` endpoint) routing requests through an unstable path. A genuinely
  free-tier model was tried as a zero-cost alternative and hit hard rate limits (HTTP 429) almost
  immediately. Given a local Ollama install with `llama3.1:8b` was already available, the whole
  LLM layer was switched to it — the `chat_json()` abstraction in `app/llm_client.py` made this a
  config change plus one new request-shape function, not a rewrite. Trade-off: a local 8B model on
  a 4GB laptop GPU is slow (roughly 30s–6min per page depending on content density) and somewhat
  weaker at extraction/reasoning than a frontier hosted model, which is why the demo run targets a
  curated set of pages rather than full ~90-100 page documents (the pipeline itself has no such
  limit — `pages`/`max_pages` are there specifically so a real run can be scoped to what time/
  hardware allows).
- **Embeddings run locally regardless of which LLM backend is active.** Even when pointed at a
  paid API, candidate retrieval never spends an API call — only the actual relationship judgment
  does, and only on shortlisted pairs.
- **SQLite, not a vector DB or graph DB.** The assignment explicitly says a graph DB isn't the
  point. At this scale (a handful of documents, low thousands of facts) a plain table plus
  in-memory cosine similarity is simpler to read end-to-end and fast enough; see Limitations for
  what would change at real scale.
- **No hardcoded fact types, filenames, or per-document rules anywhere in the pipeline** — the
  extraction prompt is generic, and the only document-specific text in this repo is the *demo
  page selection* used to keep the local-model run fast for this specific submission, which is a
  runtime argument (`pages=22,24,52,68`), not code.

### Performance, and the experiments that failed

Processing the starter PDFs originally took 20-30+ minutes and, on the densest pages, didn't
reliably finish at all. It was profiled before anything was optimized: fact extraction (the LLM
call) is ~93% of wall-clock time, and PDF parsing and embeddings together are under 2%.

Headline results, all measured:

| | before | after |
|---|---:|---:|
| representative page (6 chunks, 11 facts) | 20-30+ min | **~4 min** |
| re-processing an already-seen document | full cost | **~0s** (content-hash cache) |
| LLM calls per chunk | 1 per fact | **1 per chunk** |

Three changes were built, benchmarked, and then **rejected or reverted** because the measurement
contradicted the hypothesis — including a hybrid candidate retriever that turned out to add at most
3 pairs of recall while tripling cost, and an arithmetic checker whose false-positive rate only
became visible on real data (5 identities reported, 3 of them coincidences).

Full profiling data, the concurrency benchmarks, the model comparison, and write-ups of what
didn't work are in **[PERFORMANCE.md](PERFORMANCE.md)**.

### Generalization: how "not specific to Delhivery" is actually enforced

The claim that this isn't tuned to the starter documents is easy to make and easy to violate
accidentally, so it's checked rather than asserted:

- **No fact values, entity names, filenames, or expected relationships appear anywhere in
  `app/`.** Auditing the production package for domain terms returns only comments and docstrings
  that *explain* a design decision using a real example — no branch, threshold, or lookup keys off
  them. The one genuine finding was a prompt that illustrated `time_period` with "e.g. FY24",
  which could nudge extraction toward one fiscal-year convention; it now describes the shape of
  the field instead of naming a format.
- **No alias dictionary.** "Net worth = total equity" is not a hardcoded synonym pair — it's
  decided by the model in a step whose only job is that question. Adding a lookup table would
  have fixed the two known cases and generalized to nothing.
- **No accounting rules in the arithmetic checker.** It discovers `a + b = c` structurally. The
  test suite includes a non-financial case (`engineering headcount + sales headcount = total
  headcount`) that passes with no code changes, which is what demonstrates the mechanism isn't
  secretly domain-specific.
- **Unit normalization is table-driven over scale words** (crore, lakh, million, bn, …) rather
  than over metric names, so it extends by adding a scale word, not by adding a document type.
- **Test scripts were themselves de-hardcoded.** `scripts/test_case1.py`, `test_case2.py` and
  `test_case3.py` originally looked at least one fact up by document or fact id, or matched
  candidates loosely enough to occasionally grab the wrong one; all three now search by content
  (case 1 by numeric value, case 3 by scope field + attribute + matching period) precisely enough
  to keep working against any re-ingest and not silently pass by pointing at the wrong row — both
  failure shapes were found and fixed *during this pass*, not hypothesized.
- **The extraction prompt never sees the document's filename.** It used to (`Document: {name}`,
  purely for the model's own context) — for the starter corpus, that name literally contains the
  word "delhivery", a real if subtle prior leaking into what's supposed to be a generic per-page
  extraction call. Removed; nothing about grounding or extraction depended on it.

### Generalization, proven empirically: an IMF Article IV report and an RBI annual report

The bullet points above are static-analysis arguments — real, but not the same thing as running the
unmodified pipeline on a document it has never seen. This is that run.

**Documents used** (already present in `data/uploads/`, uploaded independently of this project and
never referenced by any code path): the IMF's 2025 India Article IV Consultation report (a
sovereign macroeconomic assessment — GDP growth, inflation, fiscal and external-sector tables
spanning multiple fiscal years) and the RBI's 2024-25 Annual Report (a central-bank/monetary-policy
document — appendix tables of macroeconomic and financial indicators with multi-level column
headers: category × year, and separately rural/urban/combined × year). Neither is a corporate
financial statement; neither shares a table layout, a vocabulary, or an issuing organization with
the Delhivery corpus this pipeline was built and tuned against.

**Pages selected**: IMF pages 44 and 47 (a macroeconomic-framework table and a central-government-
operations table, chosen for internal multi-year numeric density and a scope-like contrast —
central vs. general government — analogous to standalone vs. consolidated); RBI pages 91 and 95
(a macroeconomic-and-financial-indicators appendix table, and an inflation table with a rural/
urban/combined multi-level header structurally similar in spirit — though not content — to the
gender × year BRSR table that caused the original row-label bug in the Delhivery corpus).

**Run through the unmodified pipeline** — the same `POST /api/documents` upload path, the same
extraction prompt, the same table reconstruction, the same candidate retrieval and relationship
adjudication as any other document. No code changed to accommodate either document.

**Results (IMF report):** 12 facts extracted (page 47 only — page 44's every chunk timed out on the
local model, a real, root-caused, already-documented "dense chunk exceeds the timeout ceiling"
limitation, not hidden), all `fact_validated` with `table_context: reconstructed`, 46 candidate
pairs checked, 12 relationships stored. The adjudicator overrode a model proposal on genuinely new
content (a `related_but_not_comparable` correction where the model said `uncertain` but one fact's
unit couldn't be parsed at all), and correctly deferred to the model (`llm_unchecked`) on a
qualitative pair with no numbers to check. One real, new, generalizable finding: the extraction
favored a table's *definitional footnotes* ("loans to states for capital expenditure are included")
over its numeric rows on this specific page — a genuine limitation of the generic 15-fact cap
interacting with a footnote-heavy table, found, root-caused, and consciously left unpatched rather
than special-cased for this one document (see [FINAL_REVIEW.md](FINAL_REVIEW.md) for the full
reasoning). A fact from this run was also correctly cross-checked against a fact from a completely
separate, earlier ingestion of the same source document — real cross-document reasoning, not a
demo-only path.

**Results (RBI report):** 21 facts, 67 candidate pairs, 22 relationships stored — and, unlike the
IMF run, real numeric table values this time (e.g. "Real GDP at Market Prices (% change) = 7.9%"
for 2003-04, restated at 9.2% for 2022-23). Two genuine, live-confirmed timelines were discovered
with zero new code: Foodgrains Production FY2004→FY2023 (213.6→329.7 million tonnes) and India's
CPI inflation FY2023→FY2025 — `app/timeline.py`'s chaining logic, tuned entirely against
Delhivery's own FY22/23/24 revenue figures, worked identically on RBI's macroeconomic series. One
fact's evidence trail also reconfirmed an already-documented limitation (table header semantics
aren't modeled) on a genuinely different table structure, rather than surfacing something new.
First attempt at this document contended for the local Ollama instance with the IMF run above
(both processing concurrently) and both of its in-flight chunks timed out; cancelled cleanly
(`cancel_requested` honored, no partial work lost) and retried sequentially once IMF finished.

Full narrative, every number, and what was and wasn't fixed, is in
[FINAL_REVIEW.md](FINAL_REVIEW.md).

**What this still does not prove:** two documents, four pages, is not a claim of universal
robustness — a genuinely adversarial or exotic layout could still defeat the table reconstruction
or the extraction prompt. What it does establish is that the pipeline's generalization is not just
an absence-of-hardcoding argument: it produces sensible, evidence-grounded output on real content
from two organizations, two domains, and two table conventions this codebase was never shown
during development.

## Limitations and Next Steps

- **Full-document ingestion is still slow with the local model**, even after this optimization
  pass — dense financial tables can take 15-45 minutes for a single page. `LLM_CONCURRENCY` exists
  and helps on typical content, but defaults to 1 because it measurably hurts reliability on the
  densest pages (see Performance above); a machine with more VRAM headroom (so the model doesn't
  spill onto CPU at all) would likely see concurrency help without that downside. Next step:
  default to a hosted API when one is configured, falling back to local only when no key is
  present, so a real large-document run isn't gated on local hardware limits at all.
- **Relationship candidate retrieval is a linear scan over all embeddings in Python/numpy.** Fine
  for low thousands of facts; would need an actual vector index (FAISS/pgvector/etc.) for many
  documents at real scale — noted in the code as the specific place this would need to change.
- **De-duplication of near-identical facts** from overlapping chunks was planned, then measured
  and not built: across all 304 extracted facts there were **0 exact duplicates and 0
  near-duplicates** within a document. The 150-character overlap region rarely contains a
  complete, self-contained fact statement, so the extractor doesn't in practice emit one twice.
  If a document type appears where it does, the check is a cheap embedding self-comparison over
  one document's own new facts before insert.
- **Relationship precision is verified on one document, not across the corpus.** Reviewing the
  Relationships view surfaced a real error class — different revenue *segments* being reported as
  contradicting each other — which was traced to conflicting instructions inside the metric
  prompt and fixed (see [PERFORMANCE.md](PERFORMANCE.md)). Re-judging that document's 30 stored
  relationships dropped 15, all individually verified as false positives, with 0 legitimate
  relationships relabelled. `scripts/audit_precision.py` runs this audit for any document, but it
  has only been run on one, so the fix is validated rather than proven general.
- **Schema evolves at the field level (attribute is open-vocabulary), not the table level.** A
  fact needing a genuinely new *column* (not just a new `attribute` value) — e.g. a geographic
  coordinate pair — isn't supported yet. Next step: an optional `extra_json` column for
  fact-type-specific structured data beyond the generic fields.
- **No authentication/multi-tenancy** — fine for a local prototype, not for shipping.
- **Relationship precision is measured by self-consistency, not against human labels.** A
  corpus-wide audit removed 142 false positives and relabelled 33, all toward the more careful
  label; graph coherence then *proved* that 25 errors remain (12.8% of closed triangles). Both are
  strong evidence, but neither is ground truth. Converting them into a true precision figure needs
  a hand-labelled sample, which has not been done.
- **Table reconstruction handles grids, not every layout.** Rows and columns are recovered from
  word coordinates and multi-column pages are split at their gutters, which took evidence
  validation on a table page from 12% to 85%. It does not merge spanning cells, identify header
  rows semantically, or handle nested tables. A page whose columns are separated by less than
  normal word spacing will still under-split.
- **Period parsing covers common forms, not all of them.** Fiscal years, spans, quarters and
  as-of dates parse; anything else reports UNKNOWN rather than being guessed, which is the safe
  direction but still a gap. Relative periods ("previous year") cannot be resolved at all without
  tracking each document's own reporting date, which is not currently extracted.
- **The 8B local model remains the accuracy ceiling.** Most of the deterministic scaffolding in
  this project exists to work around it -- normalization, evidence checking, coherence. A larger
  model would need less of that, and the architecture supports swapping one in by changing
  configuration alone.

### What was intentionally not implemented

Deliberate scope decisions, not oversights — each one measured or reasoned through rather than
assumed:

- **Currency/scale/basis as separate fact-schema columns.** Already captured inside
  `unit`/`scope`/`time_period` and parsed deterministically downstream with no information loss;
  splitting them out would mean changing the extraction prompt (re-extraction risk, more burden on
  an 8B model) for no behavioral gain.
- **Full bounding-box/coordinate evidence storage.** The existing text-quote grounding — verbatim
  in the layout-reconstructed text shown to the model, stated precisely as that rather than implied
  more strongly — is adequate for the stated purpose; per-word PDF coordinates would be a much
  larger evidence model for no corresponding gain here.
- **Widening candidate retrieval beyond embeddings + entity/lexical/numeric signals.** A hybrid
  retriever was built and measured, then reverted: at most 3 additional pairs of recall for 3× the
  LLM calls. Re-litigating a measured decision without new evidence would violate this project's
  own "optimize only what's measured" rule.
- **A DB-level unique constraint on relationships.** A real, narrow gap (a possible concurrent
  duplicate insert) that isn't currently manifesting as a bug; adding a migration against a live
  database whose existing-duplicate state wasn't verified was judged riskier than the gap it would
  close.
- **A hand-labelled precision benchmark, a vector index, a durable job queue, OCR, multi-agent
  orchestration.** All out of scope for a prototype at this stage — none of the assignment's
  "brownie point" extensions require them, and the existing limitations above are already honestly
  stated rather than hidden behind added machinery.
- **A fabricated multi-dimensional numeric confidence score** (e.g. a made-up
  `context_confidence: 0.73`). The adjudication `checks` trace — categorical, recording which rule
  fired and what each computed verdict was — represents uncertainty without inventing false
  numerical precision.
