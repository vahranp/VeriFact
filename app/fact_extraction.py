"""
Per-page fact extraction. Nothing here is specific to Delhivery, financial
filings, or any fixed fact taxonomy -- the prompt asks the model to decide
what counts as a "fact" on each page, which is what lets this generalize
to unseen PDFs (per the assignment: "the documents should guide what
counts as a fact").
"""
import re

from app import db
from app.cache import hash_text
from app.schemas import validate_facts
from app.config import EXTRACTION_MODEL, EXTRACTION_TIMEOUT_SECONDS
from app.llm_client import chat_json, LLMError, LLMParseError
from app.pdf_extract import Chunk

SYSTEM_PROMPT = """You are a precise fact-extraction engine. You read one page (or page \
fragment) from an arbitrary document -- it could be a financial filing, a report, a \
contract, anything -- and extract every meaningful, checkable fact stated on it.

A "fact" is any discrete, checkable claim: a number (revenue, a count, a percentage, a \
date, a dimension), a status (a person's role, whether something is active/resigned/\
approved), a relationship (X owns Y, X is located at Y), or a definition. Ignore page \
furniture: headers, footers, page numbers, table-of-contents entries, boilerplate legal \
disclaimers with no checkable content.

For EVERY fact you extract, you must ground it in an exact verbatim quote copied \
character-for-character from the page text you were given -- never paraphrase the quote \
itself (you can paraphrase in the "statement" field, just not in "quote"). If you cannot \
find exact supporting text on the page, do not invent the fact.

Respond with ONLY a JSON array (no prose, no markdown fences). Each element:
{
  "subject": "<entity/thing the fact is about, e.g. a company, person, or product>",
  "attribute": "<what aspect is being stated, e.g. revenue, pin-code reach, director status>",
  "value": "<the stated value as text, keep original formatting/currency symbols>",
  "value_numeric": <number, or null if not numeric>,
  "unit": "<unit if applicable, e.g. INR million, %, count, or null>",
  "time_period": "<period/date this fact applies to if stated, e.g. FY24, as of March 31 2024, or null>",
  "scope": "<qualifying scope if stated, e.g. consolidated, standalone, excluding a subsidiary, or null>",
  "statement": "<one self-contained natural-language sentence stating the fact, including subject+value+period+scope so it makes sense out of context>",
  "quote": "<exact verbatim snippet, under 300 characters, copied from the page>",
  "confidence": <0.0-1.0, how explicitly/directly the page states this>
}

Extract at most 15 of the most meaningful facts per chunk -- prioritize quality and variety \
over exhaustive coverage. If the text is a dense uniform table (e.g. a full balance sheet or a \
multi-year line-item schedule), do NOT try to transcribe every row: pick the handful of rows \
most likely to matter on their own (totals, subtotals, headline figures) rather than every \
minor line item. Skip trivial repetition. If the page has no extractable facts, return an \
empty array []."""


_NULLISH = {"null", "none", "n/a", "na", ""}

# Found via direct testing (not a hypothetical): on one real page, the
# model reliably filled value_numeric for negative/parenthesized figures
# ("(452)" -> -452.0) but left it null for plain positive ones ("8,142",
# "12.7%", "1.4") even though `value` clearly states a number and `unit`
# separately carries the scale word. Rather than trust the model's own
# value_numeric field when it's missing, fall back to parsing the number
# straight out of `value` -- this is exactly the kind of deterministic
# parsing that shouldn't be left to chance in an unassisted LLM field.
#
# Checked as a parenthesized accounting-negative first ("(452)", "(6.3%)"
# -- note the number sits *before* a trailing "%" and *then* the closing
# paren, so the parenthesis check can't just look for the number
# immediately followed by ")"), falling back to a plain signed number.
_PAREN_NUMBER = re.compile(r"\(\s*-?[\d,]*\.?\d+\s*%?\s*\)")
_PLAIN_NUMBER = re.compile(r"-?[\d,]*\.?\d+")


def _numeric_fallback(value) -> float | None:
    if value is None:
        return None
    s = str(value)

    paren_match = _PAREN_NUMBER.search(s)
    if paren_match:
        inner = _PLAIN_NUMBER.search(paren_match.group(0))
        if inner:
            try:
                return -abs(float(inner.group(0).replace(",", "")))
            except ValueError:
                pass

    plain_match = _PLAIN_NUMBER.search(s)
    if not plain_match:
        return None
    try:
        return float(plain_match.group(0).replace(",", ""))
    except ValueError:
        return None


def _clean(value):
    """Some models emit the literal string "null" instead of JSON null for
    optional fields. Normalize those to real None so downstream filtering
    (e.g. `if fact.get('unit')`) behaves as expected."""
    if isinstance(value, str) and value.strip().lower() in _NULLISH:
        return None
    return value


def _find_quote_offset(page_text: str, quote: str) -> int:
    """Returns the char offset of `quote` in `page_text`, or -1. Tries an
    exact match first, then a whitespace-normalized fuzzy match, since
    models occasionally collapse/expand whitespace when "copying"."""
    idx = page_text.find(quote)
    if idx != -1:
        return idx

    norm_page = re.sub(r"\s+", " ", page_text)
    norm_quote = re.sub(r"\s+", " ", quote).strip()
    if not norm_quote:
        return -1
    return norm_page.find(norm_quote)


def _recover_non_list_shape(raw: dict) -> tuple[list | None, str | None]:
    """Smaller/local models occasionally emit a single JSON object instead
    of the array we asked for -- either one fact directly, or the array
    wrapped under some key like {"facts": [...]}. (We saw this consistently
    when Ollama's grammar-constrained format="json" mode was enabled, which
    is why extraction now runs unconstrained -- but this recovery stays as
    a safety net since it can still happen occasionally either way.) Try
    both recoveries before giving up."""
    if not isinstance(raw, dict):
        return None, None

    if "quote" in raw and "statement" in raw:
        return [raw], "treated the single object as a one-element fact list"

    for key, value in raw.items():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            return value, f"unwrapped array found under key '{key}'"

    return None, None


REGROUND_PROMPT = """A fact was extracted from a page, but the quote given as its supporting \
evidence could not be found verbatim in the page text -- the model that produced it appears to \
have paraphrased rather than copied.

Your job: find the EXACT span of the page text below that actually supports the stated fact, and \
return it copied character-for-character. Do not paraphrase, summarise, correct spelling, fix \
spacing, or add anything. If no span of the page genuinely supports the fact, say so instead of \
inventing one -- a wrong quote is worse than an admitted gap.

Respond with ONLY this JSON object:
{
  "quote": "<exact verbatim span copied from the page, or null if the page does not support the fact>",
  "found": true | false
}"""


def _reground_quote(fact_statement: str, page_text: str) -> str | None:
    """One focused retry when a quote fails the grounding check: ask the
    model to locate the real supporting span instead of accepting a
    paraphrase. Returns a verified-grounded quote, or None if the retry
    also fails -- the caller then keeps the original and flags it
    ungrounded, exactly as before. This can only ever upgrade a fact's
    evidence, never downgrade or fabricate it: the returned quote is put
    through the same _find_quote_offset check as the original."""
    user_prompt = (
        f"STATED FACT: {fact_statement}\n\n"
        f"PAGE TEXT:\n\"\"\"\n{page_text}\n\"\"\""
    )
    try:
        raw = chat_json(EXTRACTION_MODEL, REGROUND_PROMPT, user_prompt, temperature=0.0,
                         max_tokens=400, timeout=EXTRACTION_TIMEOUT_SECONDS)
    except (LLMError, LLMParseError):
        return None
    if not isinstance(raw, dict) or not raw.get("found"):
        return None
    candidate = raw.get("quote")
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    candidate = candidate.strip()
    return candidate if _find_quote_offset(page_text, candidate) != -1 else None


def extract_facts_from_chunk(chunk: Chunk, document_name: str) -> tuple[list[dict], list[dict], bool]:
    """Returns (facts, issues, cache_hit). `facts` have a 'quote_grounded'
    bool added. `issues` are dicts ready to hand to
    db.insert_issue (issue_type/detail/raw_excerpt).

    Cache key is hash(model, prompt, chunk text) -- NOT document name or
    page number, so identical page text hits the cache whether it arrives
    via a re-upload of the same PDF, an overlapping chunk window, or a
    different file that happens to contain identical text. Changing
    EXTRACTION_MODEL or SYSTEM_PROMPT changes the hash, so a prompt/model
    change can never silently serve a stale cached answer."""
    # page_context is part of the key: it changes the prompt, so two chunks
    # with identical text but different surrounding page context are
    # genuinely different questions and must not share a cached answer.
    chunk_hash = hash_text(EXTRACTION_MODEL, SYSTEM_PROMPT, chunk.text, chunk.page_context)
    cached = db.get_cached_extraction(chunk_hash)
    if cached is not None:
        return cached["facts"], cached["issues"], True

    issues: list[dict] = []
    context_block = ""
    if chunk.page_context:
        context_block = (
            "PAGE HEADER CONTEXT -- this is the top of the same page, provided ONLY so you can "
            "interpret units, currency, denominations and reporting periods correctly (e.g. a table "
            "stating 'all amounts in millions'). Do NOT extract facts from this section, and do NOT "
            "quote from it:\n"
            f"\"\"\"\n{chunk.page_context}\n\"\"\"\n\n"
        )
    user_prompt = (
        f"Document: {document_name}\nPage: {chunk.page_number}\n\n"
        f"{context_block}"
        f"PAGE TEXT (extract facts from this section only):\n\"\"\"\n{chunk.text}\n\"\"\""
    )

    try:
        raw = chat_json(EXTRACTION_MODEL, SYSTEM_PROMPT, user_prompt, temperature=0.0,
                         max_tokens=3000, timeout=EXTRACTION_TIMEOUT_SECONDS)
    except LLMError as exc:
        issues.append({"issue_type": "llm_call_failed", "detail": str(exc), "raw_excerpt": ""})
        return [], issues, False
    except LLMParseError as exc:
        issues.append({"issue_type": "unparseable_response", "detail": str(exc), "raw_excerpt": exc.raw_content})
        return [], issues, False

    if not isinstance(raw, list):
        original_raw = raw
        raw, recovered_how = _recover_non_list_shape(raw)
        if raw is None:
            issues.append({
                "issue_type": "unexpected_shape",
                "detail": f"Expected a JSON array of facts, got {type(original_raw).__name__} that we couldn't recover",
                "raw_excerpt": str(original_raw)[:1000],
            })
            return [], issues, False
        issues.append({
            "issue_type": "unexpected_shape_recovered",
            "detail": (
                "Model returned a JSON object instead of an array (a known behavior of "
                f"grammar-constrained local JSON decoding); recovered via: {recovered_how}."
            ),
            "raw_excerpt": "",
        })

    # Schema-validate before anything touches the database: a fact with no
    # quote, or a confidence of 7.5, is rejected here rather than persisted
    # and dealt with later (see app/schemas.py).
    validated, schema_issues = validate_facts(raw)
    issues.extend(schema_issues)

    facts = []
    for item in validated:
        quote = str(item.get("quote", "")).strip()
        offset = _find_quote_offset(chunk.text, quote)
        grounded = offset != -1

        if not grounded:
            # One focused retry before giving up on the evidence link.
            recovered = _reground_quote(str(item.get("statement", "")), chunk.text)
            if recovered:
                quote, grounded = recovered, True
                issues.append({
                    "issue_type": "quote_regrounded",
                    "detail": (
                        f"Initial quote for fact '{item.get('statement', '')[:120]}' was not verbatim on "
                        f"page {chunk.page_number}; a re-grounding retry located the real supporting text."
                    ),
                    "raw_excerpt": recovered[:300],
                })
            else:
                issues.append({
                    "issue_type": "quote_not_grounded",
                    "detail": (
                        f"Model's quote for fact '{item.get('statement', '')[:120]}' was not found "
                        f"verbatim on page {chunk.page_number}; a re-grounding retry also failed to "
                        f"locate supporting text, so the fact is kept but flagged unverified."
                    ),
                    "raw_excerpt": quote,
                })

        # Schema validation already coerced/cleaned these; the only work
        # left is recovering a number the model declined to give us.
        value_numeric = item.get("value_numeric")
        if value_numeric is None:
            value_numeric = _numeric_fallback(item.get("value"))

        facts.append({
            "subject": item.get("subject"),
            "attribute": item.get("attribute"),
            "value": item.get("value"),
            "value_numeric": value_numeric,
            "unit": item.get("unit"),
            "time_period": item.get("time_period"),
            "scope": item.get("scope"),
            "statement": item.get("statement"),
            "quote": quote,
            "quote_grounded": grounded,
            "confidence": item.get("confidence"),
        })

    # Cache the outcome even if it contains issues (e.g. an ungrounded
    # quote) -- the cache stores "what the model actually said for this
    # exact input", not "a verified-correct answer"; re-asking would
    # deterministically waste a call to get the same imperfect answer
    # again, not a better one.
    db.set_cached_extraction(chunk_hash, EXTRACTION_MODEL, facts, issues)
    return facts, issues, False
