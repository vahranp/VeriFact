# Performance and Measurement

Every number in this file is from a real instrumented run, not an estimate. The pipeline times
each stage per document (`app/pipeline.py`); the raw breakdown is available at
`GET /api/documents/{id}` → `stats`, or via the "perf" button in the Documents tab.

This file also records the changes that were **measured and then rejected**, because those were
the more useful experiments.

---

## Contents

- [Where the time actually goes](#performance-profiling-bottlenecks-and-what-actually-changed)
- [Rejected: widening hybrid candidate retrieval](#rejected-widening-hybrid-candidate-retrieval)
- [Fixed after real-data testing: arithmetic false positives](#fixed-after-real-data-testing-arithmetic-false-positives)
- [Measured and not built: fact deduplication](#measured-and-not-built-fact-deduplication)

---

### Performance: profiling, bottlenecks, and what actually changed

Processing the starter PDFs originally took 20-30+ minutes and, on the densest pages, didn't
reliably finish at all. Rather than guess at fixes, this was profiled first (`app/pipeline.py` now
times every stage and logs it per document; the numbers below are real measured runs, not
estimates — see `scripts/benchmark_concurrency.py` / `benchmark_concurrency2.py` for the raw
benchmark scripts and `GET /api/documents/{id}` → `stats` for the instrumented per-document
breakdown, also viewable via the "perf" button in the Documents tab).

**Where the time actually goes.** A representative instrumented run (document processing page 24,
6 chunks, 11 facts, 32 relationship candidates):

```
pdf_extraction:          1.1s
fact_extraction (LLM): 484.5s   <- the bottleneck, by a wide margin
embeddings:              11.1s  (one-time model load; near-zero on later documents)
candidate_retrieval:      0.1s  <- local numpy, effectively free
relationship_reasoning (LLM): 292.5s
database:                 0.1s
TOTAL:                  789.4s
```

PDF parsing, chunking, embedding generation, candidate retrieval, and all database operations
combined are under 1% of total time. **Every minute this pipeline spends is an LLM call running
on a local model** — `ollama ps` shows `llama3.1:8b` (Q4_K_M, 4.9GB) running **64% CPU / 36% GPU**:
the model doesn't fully fit in this machine's VRAM alongside its context window, so a large chunk
of every forward pass runs on CPU. That single fact explains most of what follows.

**LLM call count, calculated (Step 2 of the ask).** Neither call site was ever O(n²) or
one-call-per-fragment — both were already designed around a cheap-then-expensive funnel before
this optimization pass:
- **Extraction** is exactly 1 call per chunk (never per fact) — each call already asks for every
  fact on that chunk in one structured JSON array response (up to 15 facts/call).
- **Relationship reasoning** is bounded by `SIMILARITY_TOP_K` (default 4) per *new* fact, not
  compared against every other fact: 11 new facts against a 54-fact corpus produced 32 real
  candidate pairs (measured), not `11 × 65 = 715` or a pairwise `N×(N-1)/2` scan — cheap local
  embedding similarity (`app/embeddings.py`) does the shortlisting, and only candidates above
  `SIMILARITY_THRESHOLD` reach an LLM call at all.

So the O(n²)-avoidance and batched-extraction structure the assignment asks for (Steps 3-4) were
already correct in the original build. What was missing, and what this pass added, was: not
wasting the calls that *did* have to happen, caching so identical work is never repeated, bounded
concurrency, and making every tunable an env var instead of a constant.

**1. Fixed a real timeout/retry pathology (the single biggest win).** The original retry policy
(`max_retries=2`, blanket-retry on any `httpx.HTTPError`, including a plain timeout) meant a chunk
dense enough to exhaust its timeout once would reliably exhaust it again on retry with the
identical payload — paying up to `2 × timeout` for a call that was never going to succeed. This is
what caused document processing to run 58+ minutes and still never finish two dense
balance-sheet/table pages (every chunk on those pages timed out on both attempts). **Fix:**
`app/llm_client.py` now raises immediately on `httpx.TimeoutException` without retrying (only
genuinely transient errors — connection failures, malformed responses — still retry). Verified
directly: the same doomed chunk that cost **362.1s** under the old policy now costs exactly
**180.3s** (≈1× the timeout, not 2×) under the fix — confirmed with `scripts/benchmark_concurrency2.py`
run against the two versions of `llm_client.py`.

**2. Bounded concurrency — benchmarked, applied, then reverted when better evidence came in
(Step 6).** This one is worth walking through honestly because the conclusion changed twice:
- A clean, controlled test — identical real chunks, nothing else competing for the machine —
  comparing strictly sequential extraction against a 2-worker `ThreadPoolExecutor`: **499.0s
  sequential vs. 352.0s concurrent(2)** (~30% faster), with the *same single* genuine timeout
  occurring in both runs. Based on this, `LLM_CONCURRENCY=2` was set as the default.
- A subsequent real full-document run at concurrency=2 on the two densest pages in the whole
  corpus (financial tables, pages 52+68 — exactly the content case 2 depends on) then produced
  **zero facts**: all 6 chunks timed out. Re-running the identical pages sequentially recovered
  real extraction (17-30 facts depending on chunk size, see below).
- Conclusion: concurrency's safety is workload-dependent. On chunks with comfortable margin
  under the timeout, two requests overlapping cleanly speeds things up. On chunks *already*
  close to the timeout ceiling, contention from a second concurrent request is enough to push a
  borderline call over the edge — and this project's hardest content is disproportionately the
  content most likely to already be near that ceiling. Given the assignment's own priority order
  (correctness first, latency further down), **`LLM_CONCURRENCY` now defaults to 1** (see
  `app/config.py` for the full before/after reasoning) — the ~30% speedup was real but not worth
  trading for a workload-dependent risk of total extraction failure on exactly the pages that
  matter most for this submission's required cases. It's still one env var away from 2 for a
  document mix or machine where that trade-off looks different.
- Applied identically to relationship classification (`app/relationships.py`) as to extraction —
  same `LLM_CONCURRENCY` knob, same `ThreadPoolExecutor` pattern. DB writes were deliberately kept
  out of the worker threads regardless of the concurrency value (workers only make the network
  call and return data; the main thread does all SQLite access sequentially), specifically to
  avoid needing any locking logic in `app/db.py`. A real concurrency bug was caught and fixed
  during this: deferring all relationship inserts out of the worker threads meant two new facts
  that each ranked each other as a top-K candidate could each queue a job before either was
  inserted, double-processing one pair; fixed with an explicit `seen_pairs` set built during the
  (sequential) candidate-shortlisting pass, before any thread pool work begins.

**3. Content-hash caching, safe for new PDFs (Step 5).** Three layers, all keyed on content hashes
(`app/cache.py`), never on filename:
- **Document-level**: an upload with byte-identical content *and* the same page selector as a
  prior completed run short-circuits entirely — `POST /api/documents` returns in ~0.1s with
  `reused_document_id` set, zero Ollama calls. Implemented as a pointer (`reused_from_document_id`)
  with the API reading through to the original's facts/relationships, not by duplicating rows.
- **Extraction cache**: keyed on `hash(model, prompt, chunk text)`. Any chunk whose exact text has
  already been extracted (same model, same prompt) is served from SQLite instead of calling
  Ollama — including across different documents/uploads that happen to contain the same page text.
  A model or prompt change changes the hash, so a prompt fix (like the relationship one above)
  can never silently serve a stale cached answer.
- **Relationship cache**: keyed on `hash(model, prompt, sorted(fact_a content, fact_b content))` —
  order-independent and content-based (not fact id), so the same underlying claim re-extracted
  into a different document still hits the cache.

Measured effect: re-processing page 24 under a different (but functionally identical) page
selector — deliberately avoiding the document-level short-circuit, to isolate the chunk/pair
caches specifically — needed only **3 of 6** extraction calls (3 cache hits) and **21 of 51**
relationship calls (30 cache hits): **24 real Ollama calls instead of 57**, and **469.4s instead
of 789.4s** for equivalent output (~40% faster, ~58% fewer calls). A genuinely identical re-upload
(same file, same page selector) needed **0** calls and finished in **0.12s**.

**4. Batched embeddings.** `app/pipeline.py` now collects every new fact's statement across *all*
chunks in a document and calls `sentence-transformers` once per document instead of once per
chunk — the fixed per-call overhead (including the one-time model load, ~11s on first use, then
sub-second) is no longer paid repeatedly.

**5. Chunk size, tightened further based on a specific measured cliff.** `MAX_CHUNK_CHARS` moved
2000 → 1200 (`CHUNK_OVERLAP_CHARS` 200 → 150 proportionally) after finding the single most
stubborn chunk in the whole corpus (part of page 52's BRSR table) failed at **every** combination
of timeout (180s, 240s) and concurrency (1, 2) tried — five failures in a row — while a ~600-char
window of the *exact same underlying text* completed in 68s, and a ~200-char window (roughly one
table row) completed in 71s with clean, grounded facts. 1200 was chosen to sit safely under that
observed cliff, not as a round-number guess.

**6. Every pipeline tunable is now an env var**, not a constant: chunk size/overlap, similarity
top-k/threshold, extraction and reasoning timeouts (separate, since a reasoning call is a much
smaller ask than extraction and shouldn't wait as long before being declared stuck), and
`LLM_CONCURRENCY`. None of this is tuned to the three starter PDFs specifically — a different
document mix that needs a different similarity threshold, or different hardware that can run
higher concurrency, is a `.env` edit, not a code change.

**Before vs. after, honestly.** The "before" and "after" runs in this repo's history aren't a
single perfectly-matched pair (the config itself evolved twice more *during* optimization, as
items 2 and 5 above describe) — these are the real, recorded data points, not estimates:

| | Before optimization | After optimization |
|---|---|---|
| Pages 52+68 (the two densest pages) | Every chunk timed out on **both** attempts; document never finished after 58+ min, 0 usable facts from those two pages | All chunks eventually succeeded once chunk size was reduced to the measured-safe 1200 and a strict-JSON parsing bug was fixed; net worth (p.52) and total equity (p.68) — the exact facts case 2 needs — both extracted cleanly and grounded |
| Doomed chunk, cost of giving up | ~362s (2× timeout, retried) | 180-240s (1× timeout, not retried) |
| Fresh page, first time seen (6 chunks, 11 facts, 32 rel. candidates) | *(no equivalent "before" run exists — this pipeline had no timing instrumentation before this pass; ~20-30 min was the informal observation)* | 789.4s (~13 min), 38 Ollama calls, measured and logged |
| Same content, cache-eligible | n/a (no caching existed) | 469.4s (~8 min), 24 Ollama calls instead of 57 (~40% faster, ~58% fewer calls) for equivalent output |
| Exact duplicate re-upload | n/a | 0.12s, 0 Ollama calls |

The honest summary: this pipeline is still slow in absolute terms on this hardware (dense
financial tables can take 15-45 minutes for a single page even after every fix here), but it now
**reliably finishes and reports what happened**, instead of silently burning 58+ minutes and
producing zero usable output from the hardest content — and a second identical request is nearly
free.

**Model choice (Step 8) — benchmarked, not switched.** `EXTRACTION_MODEL` and `REASONING_MODEL`
were already separate, env-configurable settings before this pass (`app/config.py`) — both
default to `llama3.1:8b`. Given the CPU/GPU split shown above, `llama3.2:3b` (~2GB, small enough
to fit fully in this machine's VRAM) looked like a reasonable candidate specifically for
`REASONING_MODEL`, whose task (classify one pair of facts into 4 categories with a short
explanation) is a much lighter ask than extraction's "read a page, decide what's checkable,
produce a structured array." Pulled and benchmarked head-to-head on the exact same prompt/fact
pairs already used elsewhere in this write-up (`scripts/benchmark_model.py`, `benchmark_model2.py`):

| | llama3.1:8b | llama3.2:3b (cold) | llama3.2:3b (warm) |
|---|---|---|---|
| Sahil Barua pair — correct answer: `unrelated` | 10.9s, **correct** (`unrelated`) | 12.7s, **wrong** (`corroborates`) | — |
| Bhiwandi/Bengaluru pair — correct answer: `unrelated` | *(not re-tested here; see case 4 for the original 8B result on this pair)* | — | 4.0s, **wrong** (`corroborates`) |

Once warm, `llama3.2:3b` is genuinely fast for this task (4.0s vs. roughly 10-20s for `llama3.1:8b`
on a comparable call) — a real 2-5x speedup. But it got **both** test pairs wrong, reproducing
the exact same-subject-implies-related false-positive pattern that the reasoning prompt was
specifically rewritten to fix for the 8B model (see case 4) — a 3B model appears to need that
same lesson even more than the 8B one did, and two prompt-only fixes weren't enough to get the 8B
model fully reliable either. **Recommendation: keep `llama3.1:8b` as `REASONING_MODEL`.** The
speed win is real but relationship classification is precisely the step where this submission's
required cases depend on correctness (a false "corroborates" or missed "contradicts" undermines
the actual deliverable), so trading reliability for latency here is the wrong trade for this
project, even though it might be the *right* trade for a use case that tolerates more noise.
RAM/VRAM: `llama3.2:3b` fits fully in ~2GB, comfortably within this machine's GPU — the ceiling
that makes `llama3.1:8b` run 64% on CPU in the first place — so the speed difference is genuinely
about the CPU/GPU split, not a fluke of either benchmark run.


---

## Rejected: widening hybrid candidate retrieval

Candidate retrieval decides which fact pairs are worth an LLM judgment. It was originally pure
embedding similarity: each fact's top-4 neighbours, above cosine 0.40.

The suspicion was that this has a blind spot. "Net worth" and "total equity" are the same
accounting concept but share no vocabulary, so an embedding model may rank them poorly. The plan
was a hybrid retriever (`app/candidates.py`) adding three cheap deterministic signals — entity
agreement, attribute token overlap, and agreement of normalized numeric values — any of which can
promote a pair on its own, plus a wider scan window so those signals have room to work.

**Benchmarked on 304 real extracted facts** (`scripts/bench_candidates.py`):

| scan window K | embedding-only pairs | hybrid pairs | rescued by the new signals |
|---:|---:|---:|---:|
| 2 | 391 | 391 | 0 |
| 4 | 786 | 786 | 0 |
| 8 | 1,573 | 1,574 | 1 |
| 12 | 2,339 | 2,340 | 1 |
| 20 | 3,776 | 3,779 | 3 |

The hypothesis was wrong. The non-embedding signals are almost entirely **subsumed** by embedding
similarity on this corpus: `all-MiniLM-L6-v2` already scores same-entity, same-attribute fact
pairs above 0.40, so entity and lexical agreement correlate with the embedding score rather than
adding to it. Across the whole sweep the new signals contribute at most 3 pairs of genuine recall.

Widening the window, meanwhile, is expensive and indiscriminate: K=4→12 tripled candidate pairs
from 786 to 2,339, and every one of those 1,553 extra pairs costs 1–2 LLM calls.

**Decision:** the window was left at K=4 (`HYBRID_SCAN_K = SIMILARITY_TOP_K`), so retrieval cost is
identical to the original — 786 pairs, verified, with zero pairs dropped. The signals themselves
were kept, for two reasons that don't depend on the failed hypothesis: they cost nothing
measurable, and they record *why* each pair was retrieved into `relationships.candidate_reason`,
which turns a missing relationship into something diagnosable rather than a silent gap.

A third signal was removed outright. Attribute overlap alone, at a 0.60 threshold, added **1,235
junk candidates**: annual reports label dozens of *different people* with the identical attribute
"Director status", which scored lexical 1.00 against entity 0.00–0.17. Attribute similarity only
means anything once the subject already matches.

## Fixed after real-data testing: arithmetic false positives

`app/arithmetic.py` searches extracted numbers for additive identities (a + b ≈ c) as independent
evidence the figures were read correctly. It passed a clean 5-number synthetic balance sheet.
Run over **41 real extracted facts**, it reported 5 identities and 9 scale anomalies — of which
**3 identities and all 9 anomalies were false**.

Two distinct defects, neither visible in the synthetic test:

**1. A dominant addend absorbs anything.** "Other equity" (90,709.67) sits 0.81% from "total
equity" (91,446.46). With a 0.5% relative tolerance *on the total*, any second addend roughly
between 280 and 1,190 completed a passing identity — borrowings (401.84), provisions (646.61) and
other financial liabilities (1,091.14) each produced a confident-looking coincidence. Fixed by
also requiring the miss to be small relative to the **smaller addend**, so that value has to be
doing real work in the sum rather than fitting inside the slack around a much larger number.

**2. Enough numbers guarantee a spurious power-of-ten near-miss.** With 13 figures spanning three
orders of magnitude, some pair always sums to within 2% of 10× another. Fixed by tightening the
power-of-ten tolerance from 2% to 0.5% — the same tightness demanded of a genuine identity — and
by requiring the three facts to disagree on their stated unit, since a scale anomaly *is* a
unit-metadata inconsistency and there is nothing to find when all three already agree.

| on 41 real facts | before | after |
|---|---:|---:|
| identities reported | 5 (2 genuine, 3 coincidental) | **2, both genuine, both 0.00% error** |
| scale anomalies reported | 9 (all false) | **0** |

The two surviving identities are `liabilities + equity = assets` and
`share capital + other equity = total equity`, both discovered with no accounting rules encoded
anywhere in the module, and both matching to 0.00%.

**Known limitation, now tested explicitly:** a document whose figures are *uniformly*
mis-denominated cannot be caught this way. The identities still close perfectly, because internal
consistency is blind to a constant factor.

## Measured and not built: fact deduplication

Chunking uses a 150-character overlap, which in principle lets one fact be extracted twice from
two adjacent chunks. A deduplication pass was planned for this.

Measured across all 304 extracted facts:

- exact duplicates within a document (same subject, attribute and value): **0**
- near-duplicates within a document (same attribute and normalized numeric value): **0**

The overlap region rarely contains a complete, self-contained fact statement, so the extractor
does not in practice emit the same fact twice. Building a deduplication stage would have added a
component with no measured work to do. It wasn't built.

(Repeated facts *across* documents are not duplicates — that's the corroboration signal the system
exists to find. Re-uploading an identical file is already prevented by the document-level content
hash.)
