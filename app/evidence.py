"""
Independent verification of a fact against its own evidence.

Checking that a quote exists on the page is necessary but nowhere near
sufficient, and this project had been treating it as if it were. Measured
on 152 real extracted numeric facts, **16 of them (10.5%) carried a
value that does not appear anywhere in the quote offered as its
evidence**. Every one of those was reported as `quote_grounded: true`.

The failure looks like this, and it is not the model inventing text -- the
quote is genuinely on the page:

    value = 779
    quote = "Number of complaints filed during the year"

The quote is a table *row label*. The number lives in a cell that PDF text
flattening separated from it. So the evidence is real, it is verbatim, and
it does not support the value.

That distinction matters more than it might sound. A system whose whole
premise is "every fact is linked to evidence" has to mean something
stronger by "linked" than "these two strings appeared on the same page".
So grounding is split into two independent questions:

    QUOTE_GROUNDED  -- does this text exist verbatim in the source?
    FACT_VALIDATED  -- does that text actually support the claimed value,
                       unit, subject and period?

A fact can pass the first and fail the second. Those are the interesting
ones, and previously they were invisible.

Everything here is deterministic string and number work. No LLM call is
made to check the LLM -- asking the model whether it was right about its
own output would inherit exactly the error being looked for.

Nothing in this module is domain-specific: it compares the fact's own
fields against the fact's own quote, with no notion of what the document
is about.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from app.normalize import extract_number_tokens, normalize_unit

# Verification outcomes, ordered weakest to strongest.
UNGROUNDED = "ungrounded"        # the quote isn't in the source at all
QUOTE_GROUNDED = "quote_grounded"  # quote is real, but doesn't corroborate the fields
FACT_VALIDATED = "fact_validated"  # quote is real AND supports the value

# A quote this short is usually a stray table cell or fragment rather than
# a claim. Not an error on its own, but it is reported, because a bare
# number as "evidence" carries no context to verify anything against.
MIN_SUBSTANTIVE_QUOTE_CHARS = 12


@dataclass
class EvidenceCheck:
    status: str
    value_supported: Optional[bool] = None    # None when the fact has no number
    unit_supported: Optional[bool] = None
    subject_supported: Optional[bool] = None
    period_supported: Optional[bool] = None
    notes: list[str] = field(default_factory=list)

    @property
    def validated(self) -> bool:
        return self.status == FACT_VALIDATED

    def describe(self) -> str:
        if self.status == UNGROUNDED:
            return "quote not found in the source"
        if self.validated:
            return "quote found in source and supports the extracted value"
        return "quote found in source, but " + "; ".join(self.notes or ["it does not support the extracted fields"])


def _value_appears_in(quote: str, value_numeric: Optional[float], raw_value) -> Optional[bool]:
    """Is the fact's number actually present in its quote, as its OWN
    numeric token -- not merely as a run of matching digits somewhere
    inside a longer number.

    An earlier version of this check projected both sides to digit-only
    strings and did a substring test, which is a materially weaker check
    than it looks: value=12 "matched" any quote containing "2024" and
    "120" (their concatenated digits, "2024120", contain "12" as a
    substring), and stripping the decimal point out of "12.5" left "125",
    which is a substring of an unrelated "3125". Tokenizing the quote with
    extract_number_tokens (the same locale-aware parser used everywhere
    else numbers are read in this project) and comparing actual numeric
    values closes both holes: 12 no longer "appears in" 120 or 2024, and
    12.5 no longer "appears in" 3125.

    Returns None for facts with no number at all (a status or qualitative
    fact) -- those simply have nothing numeric to verify, which is not the
    same as failing verification.
    """
    targets: list[float] = []
    if value_numeric is not None:
        targets.append(float(value_numeric))
    if raw_value is not None:
        targets.extend(extract_number_tokens(str(raw_value)))
    if not targets:
        return None

    quote_tokens = extract_number_tokens(quote)
    if not quote_tokens:
        return False
    return any(abs(t - q) < 1e-6 * max(1.0, abs(t)) for t in targets for q in quote_tokens)


def _unit_appears_in(quote: str, unit: Optional[str]) -> Optional[bool]:
    """Is the unit corroborated by the quote?

    Deliberately lenient. A unit is frequently stated once in a table
    header or page preamble rather than repeated on every row -- which is
    exactly why page context is propagated during chunking -- so a unit
    absent from the quote is unremarkable and must not be treated as an
    error. This only reports the positive case: the quote visibly agrees.
    """
    if not unit:
        return None
    lowered = quote.lower()
    norm = normalize_unit(1.0, unit)

    tokens = {unit.strip().lower()}
    if norm:
        tokens.add(norm.base_unit)
    # Currency symbols and scale words are what actually appear in text.
    for token in list(tokens):
        tokens.update(t for t in re.split(r"[^\w%₹$€£]+", token) if t)

    return any(t and t in lowered for t in tokens if len(t) > 1 or t in "%₹$€£")


def _text_overlap(quote: str, text: Optional[str]) -> Optional[bool]:
    """Shared content words between the quote and a field. Used for
    subject and period, where an exact match is too strict -- a quote
    saying "the Company" supports a subject of "Delhivery Limited" only
    contextually, so this is reported, never enforced."""
    if not text:
        return None
    stop = {"the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or", "is",
            "was", "were", "as", "by", "with", "from", "its", "their", "this", "that"}
    words = {w for w in re.split(r"\W+", str(text).lower()) if w and w not in stop and len(w) > 2}
    if not words:
        return None
    lowered = quote.lower()
    return any(w in lowered for w in words)


def check_evidence(fact: dict, quote_grounded: bool) -> EvidenceCheck:
    """Verifies a fact against its own quote.

    `quote_grounded` is the caller's existing verbatim-presence result;
    this adds the second, stronger question on top of it.

    The only field whose disagreement downgrades the status is the value.
    Unit, subject and period are reported as supporting signals but never
    used to reject, because all three are routinely stated once elsewhere
    on a page rather than inside the sentence a fact is quoted from --
    treating their absence as failure would flag most correct facts.
    """
    quote = str(fact.get("quote") or "")

    if not quote_grounded:
        return EvidenceCheck(status=UNGROUNDED, notes=["quote is not verbatim in the source"])

    check = EvidenceCheck(status=QUOTE_GROUNDED)
    check.value_supported = _value_appears_in(quote, fact.get("value_numeric"), fact.get("value"))
    check.unit_supported = _unit_appears_in(quote, fact.get("unit"))
    check.subject_supported = _text_overlap(quote, fact.get("subject"))
    check.period_supported = _text_overlap(quote, fact.get("time_period"))

    if len(quote.strip()) < MIN_SUBSTANTIVE_QUOTE_CHARS:
        check.notes.append(
            f"the quote is only {len(quote.strip())} characters -- too short to carry context"
        )

    if check.value_supported is False:
        shown = fact.get("value") if fact.get("value") is not None else fact.get("value_numeric")
        check.notes.append(
            f"the extracted value ({shown}) does not appear in the quote, so the quote does "
            f"not evidence it -- typically a table cell separated from its row label"
        )
        return check

    # No number to check means nothing can be contradicted; a qualitative
    # fact whose quote is real is as validated as it can be.
    if check.value_supported is None and not check.notes:
        check.status = FACT_VALIDATED
        return check

    if check.value_supported is True and not check.notes:
        check.status = FACT_VALIDATED
    return check
