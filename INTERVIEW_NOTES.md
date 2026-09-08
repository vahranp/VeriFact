# Interview Notes

Answers to the questions I expect about VeriFact. Every claim with a number behind it comes from a
real measured run — see [PERFORMANCE.md](PERFORMANCE.md). Nothing here describes functionality
that doesn't exist.

---

## 1. What problem does VeriFact solve?

Organisations hold the same fact in many documents, stated differently, and nobody knows when two
of those statements disagree. An annual report says revenue was ₹81,415.38 million; an earnings
deck says ₹8,142 crore. Are those the same number or a discrepancy? A balance sheet says total
equity is ₹91,446.46M while a sustainability report says net worth is ₹85,466.74M — same concept,
different figures, no explanation.

VeriFact ingests PDFs, extracts facts each tied to a verbatim quote, and classifies pairs of facts
as **corroborating**, **contradicting**, **reconcilable by context**, or **uncertain**. The output
is an auditable chain: relationship → fact → evidence → page.

## 2. Why is this not just RAG?

RAG retrieves passages to answer a question. It has no opinion about whether the passages it
retrieved agree with each other — if two documents conflict, a RAG system will happily quote one
and never notice the other exists.

VeriFact's entire product *is* the disagreement. There's no query. The system compares facts
against each other and reports conflicts nobody asked about. Retrieval here is an internal cost
optimisation — a way to avoid O(n²) LLM calls — not the feature.

## 3. Why is it not just a knowledge graph?

It is a graph, but a knowledge graph normally stores asserted truth: an edge means "this holds".
Here an edge is a *judgment about two claims*, carrying its own evidence, confidence, and the
reason the pair was compared at all.

And the graph is treated as fallible rather than authoritative. `app/coherence.py` looks for
triangles that cannot all be true and reports its own edges as suspect. A knowledge graph asserts;
this one argues with itself.

I also deliberately didn't reach for Neo4j. The data is a few hundred facts and a few hundred
edges. Triangle enumeration over that in Python takes milliseconds. A graph database would have
added an install step and operational surface for no measurable benefit.

## 4. Why Ollama?

Three reasons, in order of how much they mattered.

It runs locally with no API key and no cost, so a reviewer can clone the repo and run it in five
minutes. Financial and internal documents are exactly the kind of data people are unwilling to
send to a third party. And it forced the architecture to be good: an 8B model gets things wrong
often enough that I had to build deterministic verification around it, and that scaffolding —
normalization, evidence checking, coherence — is what makes the project interesting.

It's configuration, not architecture. `LLM_PROVIDER=openai_compatible` plus a base URL and key
switches to any hosted provider; only `app/llm_client.py` knows the difference.

## 5. Why embeddings?

To avoid comparing every fact against every other fact. With 336 facts, exhaustive comparison is
~56,000 pairs at 1–2 LLM calls each. At local inference speed that never finishes.

Embedding similarity shortlists ~4 neighbours per fact, which is what makes ingestion feasible.
`all-MiniLM-L6-v2` runs locally on CPU, and cosine similarity over a few hundred vectors in numpy
is faster than a network round trip to a vector database — which is why there isn't one.

## 6. Why hybrid retrieval, and did it work?

The suspicion was a blind spot: "net worth" and "total equity" are the same concept but share no
vocabulary, so embeddings might rank them poorly. I built three additional signals — entity
agreement, attribute overlap, normalized numeric agreement — plus a wider scan window.

**Benchmarked on 304 real facts, the new signals rescued at most 3 pairs at any window size**,
while widening the window from K=4 to K=12 tripled candidate pairs from 786 to 2,339 — each
costing 1–2 LLM calls. The hypothesis was wrong: MiniLM already scores same-entity, same-attribute
pairs above threshold, so my signals correlated with the embedding score rather than adding to it.

I reverted the window and kept only the part that pays for itself: the signals record *why* each
pair was retrieved into `relationships.candidate_reason`, which turns a missing relationship into
something diagnosable instead of a silent gap.

One signal was removed outright — attribute overlap alone added **1,235 junk candidates**, because
annual reports label dozens of different people with the identical attribute "Director status".

## 7. Why deterministic normalization?

Because it has a right answer, and the model doesn't reliably produce it. `app/normalize.py`
reduces values to a common base — crore, lakh, million, billion, currency symbols, percentages,
basis points, parenthesised negatives — and hands the reasoning step a finished comparison:
"these agree to 0.006%".

`original_unit` is preserved alongside, so the source representation is never destroyed.

It refuses rather than guesses. Units that don't reduce to a common base return "not comparable"
instead of a fabricated percentage difference. The same applies to periods: `app/context.py` knows
"FY24" and "FY2023-24" are one period, and reports UNKNOWN rather than guessing when it can't tell.

## 8. Why doesn't the LLM have final authority over numerical comparison?

It doesn't, and as of the hardening pass that's enforced in code, not just requested in a prompt.

The original architecture computed the comparison in `app/normalize.py`, handed it to the model as
text ("these agree to 0.006%"), and asked the model to respect it. Nothing checked that it had.
That's a real gap, not a hypothetical one: it's exactly how a prompt regression once turned a clean
0.006%-agreement into a stored `contradicts` (see the "three things I got wrong" section below) —
the model was *told* the values agreed and still got to override that into a contradiction, and
nothing in the code would have stopped a worse version of the same failure from shipping.

`app/adjudication.py` closes it. The model's step-2 answer is now a *proposal*; a deterministic
adjudicator re-applies the same value/period/scope logic and only accepts the proposal when it
either agrees with what code independently concludes, or code has nothing conclusive to say. Where
code IS conclusive, it wins even over a confidently-worded model explanation — values equal under
confirmed-same context is `corroborates`, full stop, regardless of what the model proposed; values
different under confirmed-same context is `contradicts`, even if the model invents a plausible-
sounding reconciling story. Every relationship stores `decision_source` (did a check apply, did it
agree with the model) and, on override, the model's original proposal and the disagreement reason
— so the two opinions are always both visible, not just the one that won.

I also have a second, independent reason not to trust model confidence specifically: in a broken
coherence triangle, the false `corroborates` edge came back at **confidence 1.0** while the correct
`reconciled` edge sat at **0.8**. My first blame heuristic picked the least-confident edge — and
therefore accused the right answer. Confidence was not just uninformative, it was actively
inverted. That's why a deterministically confirmed or overridden relationship is now stored with
`confidence: 1.0` regardless of what the model said — the label is a computed fact at that point,
not an estimate — and the model's own stated confidence survives only on `llm_unchecked`
relationships, where it's genuinely the best signal available.

## 9. How does evidence grounding work?

Every fact must carry a verbatim quote. After the model responds, code checks whether that string
appears on the source page — exact match first, then whitespace-normalized. A failure gets one
re-grounding retry, which runs through the same verification and can therefore only ever *upgrade*
a fact, never fabricate one.

That's necessary but not sufficient, which is the more interesting half. See the next answer.

## 10. How do you detect hallucinated or inadequate evidence?

Splitting grounding into two questions:

- **QUOTE_GROUNDED** — is this text really in the source?
- **FACT_VALIDATED** — does that text actually support the claimed value?

A fact can pass the first and fail the second, and 36% of real extractions did. Two shapes:

```
value = 779     quote = "Number of complaints filed during the year"   ← a table ROW LABEL
value = 35.69%  quote = "35.69%"                                        ← circular; the quote IS the value
```

The first isn't hallucination — the quote is genuinely on the page. It's a row label, and the
number came from a cell the PDF flattening separated from it. The second is circular: a quote that
restates the value evidences nothing.

Corpus-wide at the time of writing: 49.3% fact_validated, 33.0% quote-grounded only, 17.7%
ungrounded (373 facts). The validated share rises as pages are re-ingested with table
reconstruction — it was 44.3% before that landed. The middle band used to be invisible and
showed a green tick.

Only the value can downgrade a fact. Unit, subject and period are reported but never used to
reject, because all three are routinely stated once in a table header rather than in the quoted
sentence — rejecting on their absence would flag most correct facts.

**No LLM call is made to check the LLM.** Asking the model whether it was right about its own
output inherits the very error being looked for.

## 11. How do you handle tables?

Reading-order PDF text collapses a grid into a vertical token stream, destroying every column
relationship — this is the root cause of the row-label failures above. `app/tables.py` recovers
rows by clustering word coordinates on their vertical midpoint, and cells by splitting on
horizontal gaps wide relative to the row's own character width.

Multi-column pages are split at their gutters first. Without that, a page with two side-by-side
tables produces rows splicing cells from both — worse than the flattened text, because it invents
adjacency the page never had. The starter page has a gutter at x=594 and previously produced rows
like `Permanent Employees | 35.69% ... | Stakeholder | Grievance`.

**Measured on page 52: evidence-validated facts went from 2/17 (12%) to 11/13 (85%).**

I first tried PyMuPDF's `find_tables()` and **rejected it after testing**: the `lines` strategy
found only header rows (these documents have no ruling lines), and `strategy="text"` returned a
fragmented 69×21 grid splitting words across cells (`'Bu'`, `'siness'`). A smaller, predictable
transformation beat a richer one that was wrong.

It runs only on pages that look tabular and falls back if reconstruction loses content, so prose
pages are untouched.

## 12. How do you distinguish contradiction from contextual reconciliation?

This is the crux of the assignment, and it's answered in four separated stages rather than one
classification — one more than before the hardening pass, and the new one is the load-bearing one.

**Stage 1 — same metric?** The model names each fact's *slice* (segment, geography, or "whole")
before giving a verdict. A part compared against its own total manufactures a false contradiction
out of an expected difference — the smaller figure is *supposed* to be smaller.

**Stage 2 — deterministic checks.** Values, period and scope are compared in code. Period returns
same / different / **overlapping** / unknown; overlapping is its own answer because a quarter
inside its year isn't expected to match and isn't a conflict. Scope now also reports WHICH axis
differed (`scope` vs. `basis`), and only checks contrasts within one axis — a fix made during this
pass after finding the old code compared every qualifier against every other regardless of axis,
so "gross" and "actual" (orthogonal, not opposites) could be reported as a contrasting scope.

**Stage 3 — the model proposes, given those verdicts.** Different period or scope → the difference
is expected, so `reconciled`, and it must name the reconciling context. Same period and scope →
unexplained, so `contradicts`. Period UNKNOWN with differing values → `uncertain`.

**Stage 4 — deterministic adjudication (new).** Stage 3 used to be the final answer. Now
`app/adjudication.py` checks it: where stages 1-2 are conclusive (values equal + context confirmed
equal; values different + context confirmed equal; values different + context confirmed different;
values different + context UNKNOWN), the deterministic verdict wins even over a different model
proposal, and the two extra states below get produced deterministically rather than left to the
model's discretion between "unrelated" and "uncertain":

- `related_but_not_comparable` — both facts are numeric, but the units don't reduce to a common
  base (e.g. a percentage against an absolute count). A structural fact, not a judgment call.
- `insufficient_context` — values differ, but period or scope came back UNKNOWN. Previously
  indistinguishable from a shaky semantic judgment; now its own label naming the actual reason.

The measured impact of stage 1: a corpus-wide re-audit removed **142 false positives** (revenue
segments being reported as contradicting each other) with **0 legitimate relabels**. The measured
impact of stage 4 is qualitative rather than a single number (see FINAL_REVIEW.md for the specific
before/after cases) — it doesn't change how often the pipeline reaches for `contradicts` vs.
`reconciled`, it changes whether the model is actually held to the computed answer it was given, or
merely asked nicely to use it.

## 13. How does caching work?

Three layers, all keyed on content rather than filenames:

| layer | key |
|---|---|
| document reuse | content hash + page selector + **pipeline fingerprint** |
| chunk extraction | model + prompt + chunk text + page context |
| relationship judgment | model + prompt + both facts' content (order-independent) |

Because prompts are in the keys, changing a prompt invalidates affected entries automatically —
there's no manual cache-busting.

The pipeline fingerprint was added after a real failure. Document-level reuse short-circuits a
byte-identical upload, and after PDF extraction changed to reconstruct tables, re-uploading a page
to *measure the improvement* returned the old facts — the bytes hadn't changed. The chunk cache had
it right; the document short-circuit ran first and never gave it the chance. The fingerprint covers
what determines extraction output and deliberately excludes what doesn't, so editing a timeout
doesn't force re-ingesting everything.

## 14. Why can Ollama concurrency hurt performance?

Because the bottleneck is compute, not waiting. `llama3.1:8b` runs roughly 64% on CPU on this
machine, so parallel requests contend for an already-saturated resource instead of overlapping.
Concurrency helps when you're blocked on network I/O; local inference isn't.

It's implemented and configurable via `LLM_CONCURRENCY` — it would help against a hosted API — but
the default is 1 because that's what the benchmark showed on the default backend. The default
reflects the measurement, not the theoretically nicer number.

## 15. How does incremental ingestion work?

Only new facts are compared. Each new fact is matched against the embedding pool of existing facts
plus the other new facts in its own batch — so ingesting document N+1 never re-compares documents
1..N against each other.

Combined with the caches, re-uploading identical content costs one hash and one indexed lookup.

## 16. What happens when the LLM is wrong?

Four independent mechanisms, none of which involve asking the model to check itself:

1. **Evidence verification** catches a value its own quote doesn't support — 10.5% of numeric facts.
2. **Arithmetic self-validation** finds `a + b ≈ c` identities among extracted numbers, and a
   near-miss resolving only by a power of ten flags a denomination misread.
3. **Graph coherence** proves errors logically: two `corroborates` and one `contradicts` in a
   triangle cannot all be true. 25 of 196 closed triangles are impossible.
4. **Schema validation** degrades a malformed response to a safe default that asserts nothing, so
   one bad judgment costs one relationship rather than manufacturing a false one.

And failures are recorded rather than swallowed. A chunk whose LLM call fails becomes an extraction
issue; the rest of the document continues.

## 17. What is the biggest remaining limitation?

Precision is measured by **self-consistency, not against human labels**. The corpus audit removed
142 false positives; coherence proves 25 errors remain. Both are strong evidence and neither is
ground truth — a systematically wrong prompt would pass a self-consistency check perfectly.

Converting that into a real precision number needs a hand-labelled sample of ~40 relationships. I
haven't done it, and I'd rather say so than quote a figure I can't support.

The 8B model is the underlying ceiling. Most of the deterministic scaffolding exists to work around
it.

## 18. How would this scale?

The extraction side scales fine — it's embarrassingly parallel and cached by content hash.

The comparison side wouldn't survive 10,000 documents. Each new fact is compared against a linear
scan of the whole embedding pool, so ingestion cost grows with corpus size. At that scale I'd need
an ANN index (FAISS) instead of the linear scan, and I'd shard comparison by entity so facts about
different companies never enter each other's candidate pools.

Coherence checking is O(edges × degree) for triangle enumeration, which is fine into the tens of
thousands of edges and would need capping beyond that.

SQLite is right for this size and would need replacing well before those limits — but not before
the retrieval problem bites, which comes first.

## 19. What would you build next?

In order:

1. **Hand-label a precision sample.** It's the one claim I can't currently support, and it's cheap.
2. **Auto-repair coherence violations.** The mechanism already identifies which edge is wrong using
   arithmetic; it currently reports rather than fixes. I stopped short deliberately — I don't want
   it silently rewriting judgments before someone has watched it work.
3. **Extract each document's reporting date**, which would let relative periods ("previous year")
   resolve instead of reporting UNKNOWN.
4. **Header-aware table extraction** — currently rows and cells are recovered but header semantics
   aren't, so a value's column meaning still depends on the model reading the aligned header row.

---

## Three things I got wrong, and what they taught me

**The hybrid retriever.** I was confident embeddings had a blind spot. I built the fix, benchmarked
it, and it rescued 3 pairs while tripling cost. I reverted it and kept only the explainability.
The lesson is that the instinct to add a component is usually wrong, and the benchmark is cheap.

**Blaming low confidence.** In a broken triangle I assumed the least-confident edge was the culprit.
Real data showed the wrong edge had confidence 1.0 and the correct one 0.8 — I was accusing the
right answer. Model confidence isn't a reliable signal about model correctness.

**The context block I added, that broke Case 1.** I added period/scope determinations to the judge
prompt, and it appended "a material difference would not be explained by context" whenever periods
matched — including when the values *agreed to 0.006%*. Case 1 went from `corroborates` to
`contradicts`. I only caught it because I re-ran the required cases against the current
implementation instead of trusting they still passed. Context explains a *difference*; when there
isn't one, saying anything about explaining it is worse than saying nothing.

That third one is the most useful. The regression was in a prompt, introduced by an improvement,
and invisible to a test suite that mocks the LLM.

---

## Honest self-assessment

Scored as I'd score someone else's submission, after the final hardening pass. Every category
below 9.5 carries the reason.

Re-scored after the deterministic-adjudication pass. Rows that moved carry a one-line reason tied
to something that actually changed (a test, a measurement, a live run) — not a general sense of
"this got better."

| Category | Score | Remaining weakness |
|---|:--:|---|
| Assignment compliance | 9.5 | All required capabilities implemented and demonstrable through the running system. |
| Fact extraction | 8.5 | Open-vocabulary schema, structured output validated, per-chunk failures isolated. Still misreads which cell a value belongs to on dense tables — caught by the evidence check rather than prevented; confirmed again on RBI content (§8a of FINAL_REVIEW.md), so this is a real, general limitation, not a Delhivery artifact. |
| Evidence grounding | 9.5 → **9.7** | The numeric check itself was weaker than it looked (digit-substring matching let `value=12` match a quote containing "2024" and "120") — rewritten to compare actual numeric tokens; every existing test still passes, two new tests pin the fixed false-positive shapes. Subject/period support remains computed-but-advisory, a deliberate choice, not an oversight. |
| Numeric normalization | 9.5 | Deterministic, refuses rather than guesses, preserves the original representation, 100× guard against unit-artefact contradictions. |
| Semantic equivalence | 8.5 → **8.8** | Slice-first gating removed 142 false positives with 0 legitimate relabels. Two further, additive prompt safeguards added this pass (distinct financial-statement line items aren't the same metric merely for sharing a currency; subject/entity identity must match, not just wording) — not yet independently measured the way the slice fix was, so the bump is modest. Still one 8B judgment with no second opinion for the concept-equivalence question itself. |
| Candidate retrieval | 9 | Not exhaustive, not a single brittle threshold, records why each pair was selected. The extra signals were measured as near-redundant and kept only for explainability — a deliberate, still-current trade-off, not revisited this pass without new evidence. |
| Corroboration | 9.5 | Case 1 verified live, freshly re-run: ₹81,415.38M ≡ ₹8,142 Cr at 0.006%, `decision_source: deterministic_confirmed` — the deterministic layer now independently confirms this rather than only the model asserting it. |
| Contradiction | 8 → **8.5** | Genuine unexplained conflicts are found and surfaced. The canonical Case 2 pair still resolves to `uncertain` — correctly, given a real incomplete-unit artifact on that one extraction, deliberately not chased with a one-off re-ingest (see FINAL_REVIEW.md §13). The bump is for the new mechanism, not this specific case: `app/adjudication.py` now FORCES `contradicts` when values differ under confirmed-same period and scope, even over a model's invented reconciling story — previously the model had the last word here. |
| Contextual reconciliation | 9 → **9.3** | Period and scope computed in code, with `overlapping` as its own answer. A real bug fixed this pass: the scope-contrast checker compared every qualifier axis against every other, so orthogonal qualifiers ("gross" and "actual") could be reported as a false contrast — now axis-scoped. `as_of` and `period_end` (a snapshot vs. a period-flow sharing a calendar date) were also conflated before this pass; now distinguished. Relative periods still stay UNKNOWN. |
| Failure handling | 9 | Per-chunk isolation, orphaned-job recovery, safe defaults on malformed output, everything recorded as a visible issue. One new issue type this pass (`table_alignment_uncertain`, for a page that looked tabular but whose reconstruction was rejected). |
| Generalization | 8.5 → **9.5** | Previously a design argument, not an empirical result. Now run end-to-end, live, on two genuinely unseen, non-corporate-financial documents (an IMF Article IV report, an RBI annual report) — see FINAL_REVIEW.md §7-8a. Both completed successfully; found one new generalizable limitation (footnote-vs-numeric-row extraction competition on a footnote-dense table) and reconfirmed one already-documented one (table header semantics), with zero document-specific code changes. Not 10: two documents, four pages, isn't a claim of universal robustness. |
| Table extraction | 8 | 12% → 85% evidence validation on a table page. Recovers rows, cells and gutters; does not model header semantics or spanning cells — reconfirmed as a real, general gap (not Delhivery-specific) on an RBI table with a different structure this pass. |
| Performance | 8.5 | Profiled before optimizing, 20–30 min → ~4 min on a representative page, three changes measured and rejected. The new deterministic adjudicator was also measured, not assumed: ~4µs/call, six orders of magnitude under a single LLM call, so no optimization work was justified. Dense pages remain slow on local inference — reconfirmed by real IMF/RBI timeouts this pass, not just Delhivery ones. |
| Caching | 9.5 | Three content-keyed layers; prompt and pipeline changes invalidate automatically. A real gap fixed this pass: `pipeline_fingerprint()` hashed the model name but not the extraction prompt TEXT, so the document-level dedup short-circuit could serve stale facts after a prompt change even though the chunk-level cache already handled it correctly. |
| Testing | 9 → **9.4** | 664 tests (up from 368), still LLM-mocked, no Ollama needed, fresh clone passes. New: `tests/test_adjudication.py` (27 cases pinning the deterministic override rules), `tests/test_schemas.py` (the validation boundary had no dedicated tests at all before this pass), `tests/test_document_reuse.py`. Still missing: fixture-based end-to-end tests of the four cases without a live model. |
| API | 9 | Typed responses, constrained queries, validation before side effects, no stack traces. Response models are permissive by design. `/api/coherence` gained document-scoping this pass (previously the only per-document view without it). |
| UI | 9 → **9.3** | Evidence, values, period, grounding status and retrieval provenance all visible per relationship — now also decision_source, the model's original proposal, and why they differ when they do. A real, found-and-fixed bug this pass: the document detail page visibly flashed while processing, because the 2-second poll rebuilt the whole panel and replayed every entrance animation; fixed to update in place. |
| Documentation | 9.5 | README matches the implementation, PERFORMANCE carries the numbers including the failures — including, now, real generalization-run numbers rather than a "not yet validated" caveat. |
| Explainability | 9.5 | Every relationship exposes why the pair was retrieved, what was computed, and what the model concluded — and now, when the deterministic layer disagreed with the model, both opinions and the reason, not just the winner. |
| Code quality | 9 | Small modules with a clear boundary between LLM and deterministic work. New deterministic logic went into its own module (`app/adjudication.py`) rather than growing `app/relationships.py` further. `app/main.py` is getting longer, not shorter. |
| **Overall** | 9 → **9.4** | Strong architecture, measured decisions, honest reporting. The LLM-controlled-final-decision gap — the single biggest architectural weakness identified in an external review of this project — is closed and tested. Held back by unlabelled precision, an 8B ceiling, and table header semantics still not modeled. |

### Remaining issues

**BLOCKER** — none.

**HIGH** — none outstanding.

**MEDIUM**
- Precision is measured by self-consistency, not human labels. ~40 hand-labelled relationships
  would convert the coherence proofs into a real number.
- Table extraction recovers structure but not header semantics, so a value's column meaning still
  depends on the model reading the aligned header row correctly.
- Relative periods ("previous year") cannot resolve because each document's own reporting date
  isn't extracted.
- New this pass, deliberate: `app/adjudication.py`'s rule 5 forces `contradicts` whenever values
  differ under a period/scope confirmed the same, even over a model-proposed `reconciled` with a
  plausible-sounding story — a genuine reconciling reason stated only in free prose, never
  reflected in the structured period/scope fields, will be overridden into a contradiction. Chosen
  deliberately (a false-negative reconciliation is safer than a false-negative contradiction for a
  system whose purpose is surfacing disagreement), but it is a real, bounded trade-off, not a free
  improvement.

**LOW**
- Coherence violations are reported, not repaired. The mechanism identifies the wrong edge using
  arithmetic; I stopped short of auto-rewriting judgments deliberately.
- `app/main.py` mixes routing and response shaping and would benefit from splitting.
- No authentication or multi-tenancy — correct for a local prototype, not for shipping.
