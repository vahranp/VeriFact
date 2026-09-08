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
                             [LLM] step 2: given that computed comparison,
                                   corroborates / contradicts / reconciled
                                                        │
                                                        ▼
                          ┌──────── logic check on the whole graph ────────┐
                          │  equality is transitive, so A=B and B=C means  │
                          │  A=C. A triangle saying otherwise PROVES one   │
                          │  of its three judgments is wrong — no ground   │
                          │  truth, no reviewer, no extra model call. The  │
                          │  same rule deduces edges retrieval never       │
                          │  shortlisted. (app/coherence.py)               │
                          └────────────────────────────────────────────────┘
```

**The three design decisions that matter most:**

1. **The model never does arithmetic.** Unit conversion is computed deterministically in
   `app/normalize.py` and handed to the model as a finished comparison. Asking an 8B model to
   recognise "net worth = total equity" *and* convert crore to million *and* classify the
   relationship in one call reliably failed at the point where those sub-tasks interact.
2. **Every quote is verified in code**, not trusted from the model. A fact whose quote isn't
   found verbatim in the source page is re-grounded once, and dropped if that fails.
3. **The graph checks itself.** Relationships are judged pairwise and in isolation, so the graph
   they form can be internally impossible. Those impossible triangles are *proofs* of error —
   a stronger thing than a confidence score, because the system produces evidence of its own
   mistakes rather than an opinion about them.

### The design principle

**The LLM proposes. Deterministic code verifies, normalizes and challenges it.**

The model is used only where language understanding is genuinely required — deciding whether
"net worth" and "total equity" name the same concept, or whether a difference has a stated
reason. It is not used for anything with a computable answer, and it is not given final authority
over the things it is bad at.

| Question | Answered by | Why |
|---|---|---|
| What facts does this page state? | LLM | Genuinely open-ended |
| Is this quote really in the source? | Code | Exact string search; no judgment needed |
| Does the quote support the value? | Code | Digit comparison; the model checking itself would inherit its own error |
| Is ₹8,142 Cr the same as ₹81,415.38M? | Code | Arithmetic |
| Are FY24 and FY2023-24 the same period? | Code | Calendar logic |
| Is "net worth" the same as "total equity"? | **LLM** | Requires world knowledge |
| Do these figures add up? | Code | Arithmetic |
| Is this set of judgments self-consistent? | Code | Logic |

Every deterministic result is handed *to* the model as a finished answer rather than left for it
to infer. The judge prompt receives "these agree to 0.006%", "period: SAME", "scope: UNKNOWN" —
it does not receive two numbers and a hope.

### Two ideas the project actually contributes

**Arithmetic self-validation** (`app/arithmetic.py`). Documents are full of internal invariants —
a total equals the sum of its parts. Nothing tells the system those identities exist; it searches
for `a + b ≈ c` among facts sharing a unit, period and scope. On the real balance sheet it
discovered `liabilities + equity = assets` and `share capital + other equity = total equity`,
both at 0.00% error, with no accounting rules encoded anywhere. That is extraction validation with
no answer key. A near-miss resolving only if one value is rescaled by a power of ten is a specific
signal: a denomination misread, not a data conflict.

**Graph coherence** (`app/coherence.py`). `corroborates` asserts equality, and equality is
transitive, so a triangle with two `corroborates` and one `contradicts` cannot occur in a correct
graph. Its existence is a *proof* that one of those three judgments is wrong — with no ground
truth, no reviewer and no extra model call. On the live graph: 196 closed triangles, 25 logically
impossible, 62 edges implicated, and 126 further edges deducible for free.

Both are instances of the same principle: arithmetic and logic checking the model's work.

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

Scope uses contrastive pairs (consolidated/standalone, gross/net, continuing/discontinued,
actual/forecast) rather than an accounting ontology. Words from one side are synonyms —
"consolidated" and "group" name one scope — and an unfamiliar qualifier is compared as a plain
token rather than dropped, so another domain's vocabulary still works.

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
STEP 2 — how do they relate?  [LLM]
   given those three computed verdicts, not asked to derive them
   ▼
corroborates · contradicts · reconciled · uncertain
   ▼
GRAPH COHERENCE  [code] — challenges the result against every other judgment
```

Splitting into two calls was not free, and was done because a single call reliably failed at the
point where its sub-questions interact: the model could recognise two terms as synonymous and then
fail to notice the values disagreed. Step 2 is skipped entirely when step 1 says no, which is the
common case, so the second call is only paid on pairs that were going to produce a real answer.

**`uncertain` is a real verdict.** A system that can only answer corroborates / contradicts /
reconciled must force every judged pair into one of them, and that failure is silent. When the
values differ but the period determination came back UNKNOWN, "the evidence does not settle this"
is the correct answer, and it is stored and shown.

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
- **Test scripts were themselves de-hardcoded.** `scripts/test_case1.py` and `test_case2.py`
  originally looked facts up by document id; they now search by content, so they keep working
  against any re-ingest and can't accidentally pass by pointing at a known row.

**What this does not prove:** the pipeline has been run end-to-end on the three starter documents
and behaves correctly on them. It has not been validated against a document from a genuinely
different domain at full scale, so "works on any financial document" remains a design argument
supported by targeted tests, not an empirical result.

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
