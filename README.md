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

## Video Demo

`[ADD LINK HERE AFTER RECORDING]`

Shows: uploading a PDF, the four required cases (corroboration / contradiction / reconciled
contradiction / extraction failure) with their source evidence, and the Extraction Issues view.

## Approach

### What counts as a "fact," and why the schema is generic

A fact is stored as `{subject, attribute, value, value_numeric, unit, time_period, scope,
statement, quote, confidence}` rather than as fixed columns like `revenue` or `director_name`.
`attribute` is an open vocabulary the model fills in per page — that's what the assignment asks
for ("the documents should guide what counts as a fact") and it's also what lets a brand-new PDF
about a completely different subject introduce new kinds of facts without a schema migration
(one of the suggested extensions).

Every fact must carry a `quote`: an exact, verbatim excerpt from the page it came from. After the
model responds, the code independently checks whether that quote actually appears on the page
(exact match, then a whitespace-normalized fuzzy match) — a quote that doesn't check out gets
stored anyway but flagged `quote_grounded: false` and surfaced in the UI with a warning, rather
than silently trusted. This is the core of "linking every fact to evidence": the grounding check
runs in code, not by asking the model to grade its own homework.

### Pipeline

```
PDF --(PyMuPDF)--> page text --(chunk if >MAX_CHUNK_CHARS)--> per-chunk LLM call (bounded
  concurrency, content-hash cache) --> facts (grounded in quotes) --> batched local embeddings
  --> SQLite
                                          |
                                          v
                          compare new facts against existing corpus
                          (embedding similarity shortlist -> LLM judges, cached, bounded
                           concurrency -> corroborates / contradicts / reconciled / unrelated)
                                          |
                                          v
                                   relationships table
```

**Extraction** is one LLM call per chunk (pages are chunked with overlap if unusually long), asking
the model to decide what's checkable on that page and ground each claim in a quote. No per-document
prompts, no regex tuned to a specific filing format. A content-hash cache means the same chunk
text is never sent to the model twice, and a bounded thread pool (`LLM_CONCURRENCY`) lets
independent chunks run concurrently when the hardware supports it — see Performance below for how
both were actually tuned.

**Cross-document comparison** is the part the assignment calls out as the interesting bit. Doing
an LLM call for every pair of facts is O(n²) and gets expensive fast. Instead:
1. Every fact's `statement` is embedded locally (`sentence-transformers`, free, no API call).
2. For each *new* fact, the top-K most similar facts already in the database (cosine similarity,
   local `numpy` dot product — no vector DB needed at this scale) are shortlisted as candidates.
3. Only those candidates get an actual LLM call, which returns one of `corroborates` /
   `contradicts` / `reconciled` (with the specific reconciling context) / `unrelated`, plus a
   one-to-three-sentence explanation citing what drove the decision.

This is also what makes ingestion **incremental**: uploading document N+1 only compares its new
facts against the pool of facts already stored (from any prior document) plus its own new facts —
it never re-scans documents 1..N against each other. Uploading a 4th, 5th, 100th document doesn't
get more expensive per-document.

### The four required cases

All four are demoed against the actual starter PDFs, not synthetic examples — see the video and
the Relationships / Extraction Issues tabs after running the app.

All four have real facts, live in the running system (ids given below refer to actual `fact`/
`relationship` rows, inspectable via the API or the UI), not hand-picked or synthetic numbers.

1. **Corroborated across documents, expressed differently:** the Annual Report states FY24
   "consolidated revenue from operations" as ₹81,415.38 million (fact 214, p.22); the Q4 FY24
   earnings deck states FY24 "revenue from services" as ₹8,142 Cr (document 14, p.6) —
   ₹8,142 Cr × 10 = ₹81,420 million, matching to within normal rounding. Same underlying number,
   different unit (crore vs. million), different document, different attribute label. **Honestly
   reported:** the *live automated pipeline does not currently classify this specific pair
   correctly* — tested directly (`scripts/test_case1.py`), the reasoning model says `unrelated`,
   incorrectly asserting fact B specifies "standalone" scope (it doesn't) and treating "revenue
   from operations" vs. "revenue from services" as necessarily different metrics even after two
   rounds of prompt tightening explicitly aimed at this kind of terminology synonymy. The
   corroboration itself is genuine and evidenced (both facts are real, grounded, quoted, and the
   arithmetic checks out); the system's current inability to *automatically* detect this one is
   folded into case 4 below rather than hidden.
2. **Genuine/likely contradiction:** the Business Responsibility & Sustainability Report states
   net worth as ₹85,466.74 million (fact in document 11, p.52); the Consolidated Balance Sheet in
   the same filing states Total Equity as ₹91,446.46 million (fact in document 12, p.68) — for a
   company, net worth *is* total equity by definition, so these should be the same number; the
   ~₹6,000 million gap has no stated explanation anywhere in the document. **Also honestly
   reported:** getting the *automated* pipeline to surface this pair at all took two real fixes
   (lowering `SIMILARITY_THRESHOLD` after discovering these two facts scored 0.41 similarity,
   just under the original 0.45 cutoff — see Performance section below — and a prompt change so
   the model recognizes "net worth" and "total equity" as the same concept), and even after both
   fixes the model's classification of the *value* comparison has been unreliable in direct
   testing (recognizing the concepts as the same but calling the differing numbers `corroborates`
   rather than `contradicts` in one run). The two facts and their gap are real and clearly
   evidenced; whether the live relationship-classification step lands on `contradicts` for this
   pair depends on exactly this still-imperfect numeric-comparison step — see case 4.
3. **Apparent contradiction reconciled by context — this one works cleanly, live, end to end.**
   The Annual Report states standalone revenue from operations as ₹74,540.82 million *and*
   consolidated revenue as ₹81,415.38 million, both for FY24, in the same table (p.22). The system
   correctly classifies this pair as **reconciled** (confidence 1.0), with reconciliation_context
   "difference in scope (standalone vs. consolidated)" — verified live in the Relationships tab,
   not just tested in isolation. Directly relevant to case 2's stubbornness: the *only* difference
   between this pair and the net-worth/total-equity pair is that "standalone vs. consolidated" is
   an explicit label sitting right next to both numbers in the source table, while "net worth" vs.
   "total equity" requires recognizing two different named metrics are the same thing — the model
   handles the former far more reliably than the latter.
4. **Extraction/reasoning failures, found and handled (or found and honestly left open):**
   several distinct ones, documented in detail below — this ended up being the richest part of
   the whole exercise.

### Extraction/reasoning failures actually found

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
- **No de-duplication of near-identical facts** extracted from overlapping page chunks on very
  long pages. Would add a cheap embedding-similarity self-check within a single document's own
  new facts before insert.
- **Schema evolves at the field level (attribute is open-vocabulary), not the table level.** A
  fact needing a genuinely new *column* (not just a new `attribute` value) — e.g. a geographic
  coordinate pair — isn't supported yet. Next step: an optional `extra_json` column for
  fact-type-specific structured data beyond the generic fields.
- **No authentication/multi-tenancy** — fine for a local prototype, not for shipping.
- **Numeric unit conversion and cross-terminology value comparison is left entirely to the LLM's
  judgment** during relationship classification, rather than normalized programmatically. This is
  a real, demonstrated weak point, not a hypothetical one: direct testing found the local model
  can correctly recognize "net worth" and "total equity" as the same concept but then fail to
  notice their values actually disagree (calling it `corroborates`), and separately fails to
  recognize "revenue from operations" and "revenue from services" as comparable at all (see case
  4). Next step: a small programmatic layer that normalizes common unit pairs (crore ↔ million)
  and flags a same-magnitude numeric mismatch explicitly in the prompt, rather than asking the
  model to both recognize terminology synonymy *and* do the arithmetic unassisted in one call.
- **Embedding-based candidate shortlisting can silently miss a real relationship** if the two
  facts' wording isn't close enough in the embedding model's vector space, even when they're
  the same underlying claim — measured directly: "net worth" vs. "total equity" facts scored
  0.41 cosine similarity, under the original 0.45 threshold (now 0.40, informed by this exact
  measurement — see Performance above). Lowering the threshold is a partial mitigation, not a
  fix: it surfaces more true candidates but also more noise, and doesn't eliminate the
  underlying gap. Next step worth trying: expand each fact's embedding input with a short
  LLM-generated list of synonym metric names before embedding, so semantically-equivalent but
  differently-worded facts land closer together without lowering the threshold for everyone.

## Additional Notes

- AI tools used: built interactively with Claude (Claude Code) as a pair-programming/build agent —
  it wrote the scaffolding, pipeline, prompts, and this README under direction, and did the actual
  debugging (diagnosing the OpenRouter credit issue, the Ollama JSON-shape bug, the timeout/retry
  pathology, the table column-misalignment failure, the embedding-threshold gap, and everything
  else documented above) with results verified against the running system — real uploads, real
  timing, real `ollama ps`/benchmark output — rather than assumed or described hypothetically.
  Several conclusions in this README (the concurrency default, the chunk-size cliff, the model
  recommendation) were revised at least once after new evidence contradicted an earlier
  measurement; those reversals are kept visible in the write-up rather than smoothed over.
- The `india-macroeconomy` folder from the broader starter dataset (Economic Survey / RBI Annual
  Report / IMF Article IV) was *not* used to build or tune anything in this pipeline — it exists
  as an independent, never-touched dataset that could be used to sanity-check generalization
  beyond Delhivery, since nothing here was written with it in mind.
