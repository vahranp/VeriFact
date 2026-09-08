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

from app.normalize import normalize_unit

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


def _digits(text) -> str:
    """Digit-only projection, so 1,234.50 / 1234.5 / 1 234,50 all compare."""
    return re.sub(r"[^0-9]", "", str(text or ""))


def _number_forms(value_numeric: Optional[float], raw_value) -> set[str]:
    """The digit strings a number could legitimately appear as in prose.

    A value of 8142.0 may be written "8,142" or "8142" or "8142.00"; a
    value parsed from "(452)" appears as "452". Comparing digit-only
    projections sidesteps separators, currency symbols and sign
    conventions without needing to model any of them.
    """
    forms: set[str] = set()
    if raw_value is not None:
        d = _digits(raw_value)
        if d:
            forms.add(d)
    if value_numeric is not None:
        forms.add(_digits(value_numeric))
        # 8142.0 is written "8142", not "81420"
        if float(value_numeric).is_integer():
            forms.add(str(int(abs(value_numeric))))
        else:
            forms.add(_digits(f"{abs(value_numeric):.10g}"))
        # Trailing zeros from float formatting shouldn't cause a miss.
        forms.add(_digits(f"{abs(value_numeric):g}"))
    return {f for f in forms if f}


def _value_appears_in(quote: str, value_numeric: Optional[float], raw_value) -> Optional[bool]:
    """Is the fact's number actually present in its quote?

    Returns None for facts with no number at all (a status or qualitative
    fact) -- those simply have nothing numeric to verify, which is not the
    same as failing verification.
    """
    if value_numeric is None and not _digits(raw_value):
        return None
    quote_digits = _digits(quote)
    if not quote_digits:
        return False
    return any(form in quote_digits for form in _number_forms(value_numeric, raw_value))


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
