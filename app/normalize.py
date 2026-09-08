"""
Deterministic value/unit normalization for relationship comparison.

Generic across any numeric domain -- not tied to financial reporting, any
specific document type, or the starter dataset. The goal is narrow: given
two facts' (value_numeric, unit) pairs, decide in code whether they're
expressed in a common comparable base and, if so, whether the normalized
values agree -- so the LLM in app/relationships.py is handed a computed
answer ("these are 81420.0 and 81415.38 in the same base unit, 0.006%
apart") instead of being asked to do crore-to-million arithmetic and
currency-symbol parsing unassisted inside a single reasoning call.

Numeric equality after normalization is NOT treated as proof of semantic
equivalence anywhere in this module -- it only answers "if these two
numbers are meant to be compared at all, do they match?". Whether they
SHOULD be compared (same entity, same metric, same period, same scope) is
still a judgment app/relationships.py asks the LLM to make explicitly,
before this module's output is used at all.
"""
import re
from dataclasses import dataclass
from typing import Optional


def parse_locale_number(text) -> Optional[float]:
    """Parses a numeric string that may use either the US/UK convention
    (comma = thousands separator, dot = decimal point) or the
    continental-European convention (dot = thousands, comma = decimal).

    This project's other numeric parsing used to do a bare
    `float(s.replace(",", ""))` everywhere, which is silently wrong for
    real international documents: "1.234" means 1234 in Germany and 1.234
    in the US, and a naive strip-the-comma parse gets the European case
    wrong without any error to signal it. Worse, on a number with more
    than one European thousands group ("12.345.678") the old parsing
    regex only matched up to the first dot and truncated the rest.

    Full generality is impossible from the string alone -- a single
    separator used exactly once ("1,234" or "1.234") is genuinely
    ambiguous with no surrounding context to resolve it. This resolves
    every case that IS unambiguous and only falls back to a fixed
    convention for the case that isn't:

      "1,234.56"      -> 1234.56   (US/UK, unambiguous: two different
      "1.234,56"      -> 1234.56    separators present, so whichever comes
                                     LAST is the decimal point in both
                                     conventions)
      "12,345,678"    -> 12345678.0 (repeated separator: a number has at
      "12.345.678"    -> 12345678.0  most one decimal point, so a REPEATED
                                      comma or dot can only be a thousands
                                      grouping, regardless of locale)
      "1,234"         -> 1234.0    (single separator used once: genuinely
      "1.234"         -> 1.234      ambiguous. Defaults to the US/UK
                                     reading -- comma=thousands, dot=
                                     decimal -- since that convention is
                                     what this project's target documents
                                     overwhelmingly use; this is a
                                     deliberate default, not an oversight.)

    Returns None for anything that still doesn't parse, rather than
    guessing further -- consistent with normalize_unit's refusal to guess
    an unrecognized unit.
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None

    negative = s.startswith("-")
    s = s.lstrip("+-")

    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # European: 1.234,56
        else:
            s = s.replace(",", "")                     # US/UK: 1,234.56
    elif commas > 1:
        s = s.replace(",", "")                          # 12,345,678
    elif dots > 1:
        s = s.replace(".", "")                          # 12.345.678
    else:
        s = s.replace(",", "")                          # ambiguous default

    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value

# Multiplicative scale words. Indian numbering (crore, lakh) and
# international numbering (million, billion) are both included because the
# starter documents mix both -- but a "scale word" is just a multiplier
# attached to a number, not something specific to money or to any one
# document, so this list is safe to extend for any future unit vocabulary.
_SCALE_WORDS = [
    ("crores", 1e7), ("crore", 1e7), ("cr", 1e7),
    ("lakhs", 1e5), ("lakh", 1e5), ("lac", 1e5),
    ("billion", 1e9), ("bn", 1e9),
    ("million", 1e6), ("mn", 1e6),
    ("thousand", 1e3),
]

# Percentage-like units are dimensionless and never comparable to an
# absolute quantity -- these are matched as a whole unit string, not scaled.
_PERCENT_UNITS = {"%", "percent", "percentage", "pct", "percentage points", "pp", "bps"}
_BPS_TO_PCT = 0.01  # 1 basis point = 0.01 percentage point

# Common currency spellings, folded to one canonical token so "₹", "Rs",
# "INR", and "rupees" all resolve to the same base_unit signature and can
# be compared to each other; a currency that appears nowhere in this list
# still works correctly, it's just kept as its own literal token (e.g. an
# uncommon currency stays distinguishable from every other unit instead of
# being silently treated as comparable to something it isn't).
_CURRENCY_ALIASES = {
    "₹": "inr", "rs": "inr", "rs.": "inr", "inr": "inr", "rupees": "inr", "rupee": "inr",
    "$": "usd", "usd": "usd", "dollars": "usd", "dollar": "usd",
    "€": "eur", "eur": "eur", "euros": "eur",
    "£": "gbp", "gbp": "gbp", "pounds": "gbp",
}


@dataclass
class Normalized:
    value: float
    base_unit: str          # canonical signature, e.g. "inr", "sq ft", "%", "tons"
    original_unit: str


def _alias_pattern(sym: str) -> str:
    """A currency alias must not match inside a longer word. Guarding only
    the right-hand edge is not enough, and this bit for real: "rs" -> "inr"
    with a trailing \\b alone rewrote "hours" to "houinr", "years" to
    "yeainr" and "workers" to "workeinr", quietly corrupting the base unit
    of every non-currency quantity whose name happens to end in "rs".

    Boundaries are applied per edge and only where the edge character is
    alphanumeric, so symbol aliases ("₹", "$") -- which have no word
    boundary of their own -- still match tight against digits and letters.
    """
    escaped = re.escape(sym)
    left = r"(?<![0-9a-z])" if sym[:1].isalnum() else ""
    right = r"(?![0-9a-z])" if sym[-1:].isalnum() else ""
    return left + escaped + right


# Longest first, so "rs." is considered before "rs" and can't be left as a
# stray "inr." fragment.
_ALIAS_PATTERNS = [
    (re.compile(_alias_pattern(sym)), canon)
    for sym, canon in sorted(_CURRENCY_ALIASES.items(), key=lambda kv: -len(kv[0]))
]


def _clean_token(s: str) -> str:
    s = s.strip().lower()
    for pattern, canon in _ALIAS_PATTERNS:
        s = pattern.sub(canon, s)
    s = re.sub(r"[,\.]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_percent_unit(unit: str) -> bool:
    if not unit:
        return False
    s = unit.strip().lower().rstrip(".")
    return s in _PERCENT_UNITS or s.endswith("%")


def normalize_unit(value: Optional[float], unit: Optional[str]) -> Optional[Normalized]:
    """Returns a Normalized value in a canonical base, or None if the unit
    is missing/unparseable. Returning None (rather than guessing a scale
    of 1) is the safe default -- an unrecognized unit should not silently
    participate in a numeric comparison it might not belong in."""
    if value is None or unit is None:
        return None
    raw_unit = str(unit).strip()
    if not raw_unit:
        return None

    if is_percent_unit(raw_unit):
        s = raw_unit.strip().lower().rstrip(".")
        if s in ("bps",):
            return Normalized(float(value) * _BPS_TO_PCT, "%", raw_unit)
        return Normalized(float(value), "%", raw_unit)

    cleaned = _clean_token(raw_unit)
    scale = 1.0
    for word, mult in sorted(_SCALE_WORDS, key=lambda x: -len(x[0])):
        pattern = rf"\b{re.escape(word)}\b"
        m = re.search(pattern, cleaned)
        if m:
            scale = mult
            cleaned = (cleaned[:m.start()] + cleaned[m.end():]).strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            break

    base_unit = cleaned if cleaned else "unit"
    return Normalized(float(value) * scale, base_unit, raw_unit)


@dataclass
class Comparison:
    comparable: bool
    reason: str                      # why comparable / why not, for the prompt + UI
    value_a: Optional[float] = None
    value_b: Optional[float] = None
    common_unit: Optional[str] = None
    diff_pct: Optional[float] = None
    agree: Optional[bool] = None
    # True when the two normalized values differ by orders of magnitude.
    # For two facts a semantic step has just called "the same metric",
    # a 100x+ gap is far more likely to mean one side's unit metadata is
    # incomplete (e.g. "INR" recorded where the source table was denominated
    # in "INR million") than a real 100x business discrepancy. Surfacing
    # this stops a broken comparison from being reported as a confident
    # contradiction -- a right answer reached for the wrong reason.
    magnitude_suspect: bool = False


# Two values this far apart, for facts claimed to be the same metric, are
# treated as a probable unit-metadata problem rather than a real finding.
_MAGNITUDE_SUSPECT_RATIO = 100.0


def compare_values(value_a: Optional[float], unit_a: Optional[str],
                    value_b: Optional[float], unit_b: Optional[str],
                    tolerance_pct: float = 2.0) -> Comparison:
    """Deterministic comparison of two (value, unit) pairs. Refuses to
    compare (comparable=False) rather than guess whenever either side
    can't be normalized or the two normalized units don't match -- e.g. an
    absolute amount vs. a percentage, or two genuinely different physical
    quantities (sq ft vs. tons) are correctly reported as not comparable,
    not silently treated as equal or forced into a bogus percentage
    difference."""
    if value_a is None or value_b is None:
        return Comparison(False, "one or both facts have no numeric value to compare")

    a = normalize_unit(value_a, unit_a)
    b = normalize_unit(value_b, unit_b)
    if a is None or b is None:
        missing = "A" if a is None else "B"
        return Comparison(False, f"fact {missing}'s unit ('{unit_a if a is None else unit_b}') could not be parsed")

    if a.base_unit != b.base_unit:
        return Comparison(
            False,
            f"units don't reduce to the same base ('{a.base_unit}' from '{a.original_unit}' "
            f"vs. '{b.base_unit}' from '{b.original_unit}') -- not safe to compare numerically",
        )

    bare_caveat = ""
    if a.base_unit == "unit":
        # Both sides were a scale word with no further descriptor (e.g. a
        # bare "Cr" or "thousand" with no currency/quantity word attached).
        # They're numerically comparable, but this module has no signal
        # left to confirm they're the same *kind* of bare number -- that
        # judgment is left entirely to the semantic "same metric?" step
        # that must run before this comparison is treated as meaningful.
        bare_caveat = " (both are bare scaled numbers with no unit descriptor -- numeric comparability only, not semantic confirmation)"

    if a.value == b.value:
        diff_pct = 0.0
    else:
        base = max(abs(a.value), abs(b.value)) or 1.0
        diff_pct = abs(a.value - b.value) / base * 100

    lo, hi = sorted((abs(a.value), abs(b.value)))
    magnitude_suspect = bool(lo > 0 and hi / lo >= _MAGNITUDE_SUSPECT_RATIO)
    if magnitude_suspect:
        bare_caveat += (
            f" -- WARNING: the two values differ by ~{hi / lo:.0f}x, which for facts describing "
            f"the same metric usually means one side's unit is incomplete "
            f"(e.g. '{a.original_unit}' vs '{b.original_unit}'), not a real discrepancy"
        )

    return Comparison(
        True,
        f"both normalize to the same base unit ('{a.base_unit}'){bare_caveat}",
        value_a=a.value, value_b=b.value, common_unit=a.base_unit,
        diff_pct=round(diff_pct, 3), agree=diff_pct <= tolerance_pct,
        magnitude_suspect=magnitude_suspect,
    )


def format_comparison_for_prompt(cmp: Comparison) -> str:
    """One line, ready to drop into an LLM prompt so it sees a computed
    answer instead of being asked to do the arithmetic itself."""
    if not cmp.comparable:
        return f"Normalized numeric comparison: not possible ({cmp.reason})."

    if cmp.magnitude_suspect:
        return (
            f"Normalized numeric comparison: UNRELIABLE. Fact A = {cmp.value_a:g} and "
            f"Fact B = {cmp.value_b:g} (both reduced to '{cmp.common_unit}') differ by orders of "
            f"magnitude, which for the same metric almost always means one fact's unit was "
            f"recorded incompletely rather than the figures genuinely conflicting. Do NOT treat "
            f"this as evidence of a contradiction; judge from the statements and quotes instead, "
            f"and prefer a lower confidence."
        )

    verdict = "agree (within tolerance)" if cmp.agree else "DISAGREE"
    return (
        f"Normalized numeric comparison: Fact A = {cmp.value_a:g} {cmp.common_unit}, "
        f"Fact B = {cmp.value_b:g} {cmp.common_unit} -- values {verdict}, "
        f"differing by {cmp.diff_pct:g}%."
    )
