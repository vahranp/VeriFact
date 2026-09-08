# Interview Notes

Questions I expect to be asked about this project, with honest answers. Where a claim has a
number behind it, the number is from a real measured run — see [PERFORMANCE.md](PERFORMANCE.md).

---

### 1. What is the hardest problem in this project?

Not extraction — deciding whether two facts actually conflict. `₹8,142 Cr` and `₹81,415.38 million`
are the same number written two ways, and `net worth` and `total equity` are the same concept
written two ways. A naive system reports the first as a contradiction and misses the second
entirely. Both failures look identical from the outside: a wrong label on a pair of facts.

### 2. Why did you split classification into two LLM calls? Doesn't that double cost?

Because a single call reliably failed at the point where its sub-tasks interact. An 8B model can
recognise "net worth = total equity" when asked directly, and can convert crore to million when
asked directly, but asked to do both *inside one judgment* it would recognise the concepts as
equivalent and then fail to notice the values disagreed.

It doesn't double cost in practice: step 2 is skipped entirely when step 1 says the facts aren't
the same metric, which is the common case — most candidate pairs are unrelated. So the extra call
is only paid on pairs that were going to produce a real answer anyway.

### 3. Why does unit conversion happen in Python instead of in the prompt?

Because it's deterministic and the model is not. `app/normalize.py` reduces both values to a common
base and hands the model a finished comparison — "these agree to 0.006%" — rather than asking it to
do arithmetic. This is the single change that fixed the `₹8,142 Cr` vs `₹81,415.38M` case, which
went from `unrelated` to `corroborates` at confidence 1.0.

The general principle: use the LLM for the part that genuinely needs language understanding
(is "net worth" the same thing as "total equity"?) and use code for everything that has a right
answer.

### 4. What happens when the model hallucinates a quote?

It gets caught. Every fact must carry a verbatim quote, and after the model responds the code
checks whether that string actually appears on the source page — exact match first, then
whitespace-normalized. A quote that fails gets one re-grounding retry, which can only ever
*upgrade* a fact (the retry runs through the same verification), never fabricate one. If it still
fails, the fact is stored but flagged `quote_grounded: false` and surfaced in the UI with a
warning.

The grounding check runs in code. Asking the model to grade its own homework would be worthless.

### 5. Tell me about something you built and then threw away.

A hybrid candidate retriever. Candidate selection was pure embedding similarity, and I suspected a
blind spot: "net worth" and "total equity" share no vocabulary, so embeddings might rank them
poorly. I built three additional signals — entity agreement, attribute token overlap, normalized
numeric agreement — plus a wider scan window.

Benchmarked on 304 real facts, the new signals rescued **at most 3 pairs** across every window
size, while widening the window from K=4 to K=12 tripled candidate pairs from 786 to 2,339 — each
costing 1–2 LLM calls. The hypothesis was wrong: MiniLM already scores same-entity, same-attribute
pairs above threshold, so my new signals correlated with the embedding score rather than adding
to it.

I reverted the window to K=4 (identical cost to the original, zero pairs dropped) and kept only
the part that pays for itself: the signals now record *why* each pair was retrieved into
`relationships.candidate_reason`, which makes a missing relationship diagnosable instead of a
silent gap.

### 6. What did testing on real data catch that unit tests didn't?

The arithmetic self-validation module passed a clean 5-number synthetic balance sheet. Run over 41
real extracted facts it reported 5 identities and 9 scale anomalies — **3 identities and all 9
anomalies were false**.

Two defects, neither visible synthetically:

- "Other equity" (90,709.67) sits 0.81% from "total equity" (91,446.46), so with a 0.5% tolerance
  *on the total*, any second addend between ~280 and ~1,190 completed a passing identity. Three
  unrelated figures did. Fixed by also requiring the miss to be small relative to the **smaller**
  addend.
- With 13 figures spanning three orders of magnitude, some pair always sums to within 2% of 10×
  another. Fixed by tightening to 0.5% and requiring the three facts to actually disagree on their
  stated unit — a scale anomaly *is* a unit inconsistency.

After: 2 identities, both genuine, both at 0.00% error; 0 anomalies. The lesson is that a
synthetic fixture small enough to reason about is too small to expose density-driven failures.

### 7. The arithmetic module — isn't that just hardcoded accounting rules?

No, and that's the point. There is no table of equations anywhere in `app/arithmetic.py`. It
searches for value triples where a + b ≈ c among facts sharing a comparable unit, time period and
scope, and reports what it finds.

On the real balance sheet it independently discovered `liabilities + equity = assets` and
`share capital + other equity = total equity`, both at 0.00%. The same code finds
`engineering headcount + sales headcount = total headcount` with no changes, which is the test that
proves it isn't secretly domain-specific.

### 8. Why does that matter? What does it buy you?

It's extraction validation that doesn't need a ground-truth answer key. If numbers pulled out of a
document satisfy an arithmetic identity they didn't have to satisfy, that's independent evidence
they were read correctly. And a near-miss that resolves only if one value is rescaled by a power
of ten is a very specific signal: that's a denomination misread, not a data conflict.

### 9. What's the biggest limitation of that approach?

A document whose figures are **uniformly** mis-denominated is invisible to it. If every number on a
page is off by the same factor, the identities still close perfectly — internal consistency is
blind to a constant. That's tested explicitly rather than left as an assumption.

### 10. Describe a bug that came from your own architecture, not the model.

Page 68 of the annual report says "All amounts in Indian Rupees in million" once, at the top.
Chunking put that sentence in chunk 0 while "Total Equity" landed in chunk 1 — which therefore had
no denomination anywhere in it. The model did the only reasonable thing and recorded the unit as
bare `INR`, making every comparison against it wrong by a factor of a million.

The fix was to propagate a page-level context header into subsequent chunks of that page,
deliberately kept *outside* the chunk's `text` field so quote grounding still verifies against
real chunk content. After the fix those facts extract as `INR million`.

### 11. How do you avoid confidently reporting a contradiction that's really a unit error?

`compare_values` sets a `magnitude_suspect` flag when two values for the same metric differ by
more than 100×. When set, the prompt explicitly tells the model this comparison is unreliable and
must not be treated as evidence of a contradiction.

This is deliberately choosing a *less* impressive output. Without it the system would have called
the net-worth/equity pair a contradiction with high confidence — the right label for entirely the
wrong reason, which is worse than a hedge, because it would have hidden a real data bug behind a
correct-looking answer.

### 12. Where does the time actually go?

Fact extraction — the LLM call — is ~93% of wall-clock. PDF parsing and embeddings together are
under 2%. I profiled before optimizing, which is why I didn't spend effort on the PDF layer.

### 13. What made the biggest performance difference?

Asking for every fact on a chunk in **one** structured response instead of one call per fact, and
caching on content hash so re-processing a seen chunk costs ~0s. A representative page went from
20–30+ minutes to ~4 minutes.

### 14. Why is `LLM_CONCURRENCY` set to 1?

Because I benchmarked it and concurrency didn't help. `llama3.1:8b` runs ~64% on CPU on this
machine, so parallel requests contend for the same saturated resource rather than overlapping.
Concurrency is implemented and configurable — it would help against a hosted API — but the default
reflects the measured result on the default backend, not the theoretically nicer number.

### 15. Why SQLite and no ORM? Isn't that a toy choice?

It's the right size for the problem. The data is a few hundred facts and a few thousand
relationships; an ORM would add indirection without removing any real work, and a server database
would add an install step to a project someone has to run in five minutes. All writes happen on the
main thread — only the LLM calls are threaded — which keeps concurrency reasoning trivial.

I'd change this if facts reached the millions, or if multiple writers needed it.

### 16. Why not a vector database?

304 facts. A numpy cosine similarity over an in-memory array is faster than a network round trip to
a vector DB and has no operational cost. Reaching for Pinecone here would be resume-driven
development.

### 17. How would this scale to 10,000 documents?

The honest answer is that the current comparison strategy wouldn't survive it. Each new fact is
compared against the top-K of the whole existing pool, so ingestion cost grows with corpus size.
At that scale I'd need an ANN index (FAISS) instead of a linear scan, and I'd want to shard
comparison by entity so facts about different companies never enter each other's candidate pools.

The extraction side scales fine — it's embarrassingly parallel and already cached by content hash.

### 18. What would you do next if you had another week?

Table-aware extraction. I tried PyMuPDF's table API and **rejected it after testing**: the default
`lines` strategy found only header rows on these documents (no ruling lines to detect), and the
`text` strategy returned a fragmented 69×21 grid that split words across cells
(`'Bu'`, `'siness'`, `'Responsibility'`). Adopting it naively would have made extraction worse.
Doing it properly means reconstructing cell geometry from word positions, which is a real piece of
work rather than an API call.

### 19. What's the weakest part of the system right now?

Relationship precision. On one document, 92 of 115 candidate pairs were stored as relationships —
an 80% hit rate that is almost certainly too high, meaning some pairs are being labelled as
corroborating when they're merely related. I have the mechanism to investigate it now
(`candidate_reason` records why each pair was retrieved) but I have not yet done the manual
audit to quantify the false-positive rate, and I'd rather say that than quote a precision number
I haven't measured.

### 20. What did you learn?

That the instinct to add a component is usually wrong. Three separate times here, the measurement
said "don't": hybrid retrieval added 3 pairs, concurrency didn't help on local inference, and
deduplication had literally zero duplicates to remove across 304 facts. Each of those would have
been a plausible-sounding paragraph in a writeup. Building them and measuring them was the only
way to find out they were unnecessary — and the projects I'd trust are the ones that ran that
experiment rather than the ones that assumed the answer.

### 21. What's the worst bug you shipped, and how did you find it?

Confident false contradictions between different revenue segments:

> "Cross Border revenue FY23 was ₹4,552 crore" **contradicts** "revenue from services FY23 was
> ₹663 crore" — confidence 1.00

Those are different segments, not conflicting reports of one number. I found it by **looking at
the UI**, not from a test. That's the uncomfortable part: every individual explanation read as
sound, and the failure is invisible to a test suite that mocks the LLM.

The root cause was a contradiction inside my own prompt. It listed "revenue from services" as an
unconditional synonym for "revenue". So when I added a rule saying "different segments are
different metrics", nothing changed — the two instructions conflicted, and the model reasonably
followed the more specific synonym example over the general rule. My first fix did nothing, and
that told me more than the bug did.

The real fix makes the model emit `slice_a` / `slice_b` **before** its verdict, so the
aggregation-level check can't be skipped, and scopes the synonym guidance to pairs whose slices
already match.

The check I care about is the pair that *didn't* change: cross-border revenue FY24 vs FY23 still
resolves as `reconciled`, and Case 1 still corroborates at 1.0. A "fix" that labelled everything
`unrelated` would have looked identical on the failing cases and been worthless.


### 22. How do you know your system is wrong, without a ground-truth dataset?

This is the question I'd most want to be asked, because the usual answer — "the model returns a
confidence score" — is worthless. A model that is confidently wrong reports high confidence.

The relationship graph can prove its own errors. `corroborates` asserts equality, and equality is
transitive. So if A corroborates B and B corroborates C, then A *cannot* contradict C. A triangle
of that shape is not suspicious, it is **impossible** — its existence is a proof that at least one
of those three judgments is wrong.

On the real graph: 196 closed triangles, **25 logically impossible (12.8%)**, 62 edges implicated.
No ground truth, no reviewer, no extra model call. And it gives a real floor on the error rate:
at least one edge per violating triangle is wrong, at most 62 are.

This is categorically stronger than the precision audit I did earlier. That measured
self-consistency — re-judge under a corrected prompt, see what moves — which a systematically wrong
prompt would pass perfectly. A violated transitivity constraint is not an opinion about the graph;
it's a contradiction inside it.

### 23. That sounds neat, but does it actually find anything useful?

Yes, and it also corrected me. My first version blamed the least-confident edge in each broken
triangle. The real data killed that immediately:

```
corroborates conf=1.00   revenue 81,415.38  ↔  revenue 72,253.01   ← the actual error
corroborates conf=0.90   revenue 81,415.38  ↔  revenue 81,415.38   ← correct
reconciled   conf=0.80   revenue 81,415.38  ↔  revenue 72,253.01   ← correct
```

The wrong edge was the *most* confident one. Blaming low confidence accused the right answer.

So blame now goes to the deterministic comparison in `app/normalize.py`, which has no opinion: an
edge claiming two facts corroborate while their normalized values differ is wrong no matter how
sure the model sounded. After that change the suspect was correct in every case I sampled. Where
the numbers can't settle it and confidences tie, it reports the culprit as undetermined rather
than dressing an arbitrary pick up as a judgment.

### 24. You said widening retrieval didn't work. Did you ever fix the recall problem?

Yes — by deduction instead of search, which is the part I'm happiest with.

Widening the candidate window tripled LLM calls to buy 3 pairs of recall, so I reverted it. But
transitivity means that if A = B and B = C and no A–C edge exists, one is *implied*. That found
**126 edges at zero marginal cost**, because they're derived from the graph rather than retrieved
from the corpus.

I discard any deduction resting on an edge that a violating triangle implicated (183 → 126).
Propagating a judgment already known to be broken would turn one error into several, which is
worse than the missing edge it fills.

### 25. What's the single idea holding this project together?

Use the LLM only for what genuinely needs language understanding, and let arithmetic and logic
check its work.

Every component follows it. Unit conversion is code, not a prompt. Quote grounding is verified in
code, not self-reported. Arithmetic identities are discovered structurally, with no accounting
rules encoded. And the graph's own transitivity audits the model's judgments. The model decides
whether "net worth" and "total equity" mean the same thing — that genuinely needs language. It
does not decide whether 81,415.38 equals 72,253.01.


---

## Honest self-assessment

Scored as I'd score someone else's submission. This section is for my own preparation — it is
deliberately harsher than the README, which states limitations plainly but doesn't editorialize.

| Category | Score | Reasoning |
|---|:--:|---|
| Meets the stated requirements | 9 | All four required cases are implemented and demonstrable; facts are grounded in verified quotes; relationships are classified across and within documents. |
| Correctness of the hard part | 8 | Case 1 (corroboration across units) is verified fixed end-to-end. Case 2's root cause was found and fixed at the extraction layer, but the specific pair is still blocked by a stale fact from before that fix. |
| Engineering judgment | 9 | Three components were built, measured, and rejected or reverted. Profiling preceded optimization. The measured negative results are documented as prominently as the wins. |
| Generalization | 7 | Audited clean of document-specific logic, and the mechanisms are structural rather than rule-based. But it hasn't been run end-to-end on a genuinely different document, so this rests on design argument plus targeted tests. |
| Testing | 9 | 226 tests, LLM mocked, no Ollama needed. Real-data testing caught defects synthetic fixtures missed, and each became a regression test. Missing: fixture-based end-to-end tests of the four required cases. |
| Performance | 8 | 20–30 min → ~4 min on a representative page, with the profile that justified each change. Full-document ingestion on dense tables is still slow on local inference. |
| Precision of results | 8 | Audited corpus-wide: 490 → 348, 142 false positives removed, 33 relabelled — all toward the more careful label. Graph coherence then *proved* 25 remaining errors exist (12.8% of closed triangles) without any ground truth. Known-imperfect and measurably so, which is the honest position. |
| Documentation | 9 | README leads with a diagram and the two decisions that matter; PERFORMANCE.md carries the numbers and the failures; this file covers the questions. |
| **Overall** | **8.5** | Strong process, honest reporting, and two mechanisms (arithmetic identities, graph transitivity) that validate the model's work without a ground-truth set. The remaining gap is that no human has labelled a sample to convert those self-proofs into a true precision number. |

### Open issues, by severity

**BLOCKER** — none. The system runs end to end and produces all four required case types.

**PROVEN, QUANTIFIED** — graph coherence establishes that 25 of 196 closed triangles are
logically impossible, implicating 62 edges. This is not a suspicion; at least one judgment per
violating triangle is wrong. The mechanism now reports them; it does not yet repair them.

**HIGH**
- Precision is measured by *self-consistency* — re-judging under the corrected prompt — not
  against human labels. The corpus-wide audit removed 142 false positives and relabelled 33, all
  in the more careful direction, which is strong evidence but not ground truth. Fix: hand-label a
  sample of 40 surviving relationships and report true precision.

**MEDIUM**
- `reconciled` can absorb extraction errors: a fact whose quote is the bare string `"8"` was
  reconciled against `1.4 Mn Tons` on a fabricated unit explanation. A quote carrying no context
  is not evidence and should be ineligible for reconciliation, checked at extraction time.
- Case 2's canonical pair still resolves against a fact extracted before the page-context fix, so
  it demonstrates the `magnitude_suspect` guard rather than the contradiction itself. Fix:
  re-extract that page with the extraction cache cleared for the affected chunk.
- No end-to-end regression test pinning the four required cases with fixture documents; the
  current case scripts need a live model.
- `UNCERTAIN` / `INSUFFICIENT_EVIDENCE` is supported in the schema but not surfaced through the
  pipeline or UI, so a low-confidence judgment currently reads the same as a confident one.

**LOW**
- Table extraction is still linearized text; PyMuPDF's table API was tested and rejected as worse.
- Candidate retrieval is a linear numpy scan — fine at this scale, needs an ANN index well before
  10k documents.
- `candidate_reason` is recorded but not yet displayed in the UI.
