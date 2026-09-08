"""
Deterministic normalization of reporting period and scope.

Two facts can state different numbers for the same metric and both be
correct, because they describe different periods or different scopes. That
distinction is the entire difference between a contradiction and a
reconcilable difference -- the assignment asks for both, separately -- and
until now it was left to the model to work out from raw strings like
"FY24" versus "FY2023-24" versus "fiscal year ended March 31, 2024".

Those are the same period written three ways, and a model comparing them
character-by-character has no reason to know that. The same problem
`app/normalize.py` solves for values applies here: the answer is
computable, so it should be computed rather than inferred.

So this module answers two questions in code, and hands the result to the
reasoning step the same way normalized values are:

    do these two facts cover the same period?   same / different /
                                                overlapping / unknown
    do they describe the same scope?            same / different / unknown

Two design commitments worth stating:

**Unknown is a real answer.** A period this cannot parse is reported as
unknown, never guessed into a year. Inventing precision here would be
worse than admitting the gap, because a wrong "different period" reading
turns a genuine contradiction into a false reconciliation -- the system
would explain away a real conflict.

**Nothing here is financial.** Fiscal years, quarters and as-of dates are
general reporting concepts used by governments, universities and
non-profits. Scope qualifiers are handled as an open vocabulary of
contrastive pairs (consolidated/standalone, gross/net) rather than an
accounting ontology, so an unfamiliar qualifier is preserved and compared
rather than dropped.
"""
import re
from dataclasses import dataclass
from typing import Optional

SAME = "same"
DIFFERENT = "different"
OVERLAPPING = "overlapping"
UNKNOWN = "unknown"

# Periods anchored to an explicit calendar date rather than a fiscal-year
# label. Two facts sharing one of these kinds are compared by exact date,
# not by year alone.
_DATE_ANCHORED_KINDS = {"as_of", "period_end"}
# "period_end" ("year ended March 31, 2024") is a flow/period measurement,
# the same kind of thing a fiscal year is -- just dated by an explicit
# end-date instead of a label. Grouped with fiscal_year for the "which
# window, exactly" ambiguity check against a bare calendar year below.
_FISCAL_LIKE_KINDS = {"fiscal_year", "period_end"}

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# Phrases that name a period only relative to an unstated "now". Without
# the document's own reporting date these cannot be resolved, and guessing
# would be worse than admitting it -- so they parse to unknown explicitly
# rather than falling through as unparseable noise.
_RELATIVE_TERMS = {
    "current period", "current year", "current quarter", "this year",
    "previous year", "prior year", "last year", "preceding year",
    "previous period", "prior period", "year ago", "corresponding period",
    "to date", "ytd", "year to date",
}


@dataclass
class Period:
    """A normalized reporting period.

    `label` keeps the original text so nothing is destroyed -- the same
    principle as preserving original_unit alongside a normalized value.
    """

    label: str
    kind: str               # "fiscal_year" | "calendar_year" | "quarter" | "half_year" | "as_of" | "relative"
    year: Optional[int] = None
    quarter: Optional[int] = None
    half: Optional[int] = None  # 1 or 2, for half-year reporting (H1/H2)
    month: Optional[int] = None
    day: Optional[int] = None
    # A fiscal year written "2023-24" spans two calendar years; kept so a
    # fiscal label can be compared against a calendar one without pretending
    # they are identical.
    spans_years: tuple[int, int] | None = None

    def describe(self) -> str:
        if self.kind == "quarter":
            return f"Q{self.quarter} {self.year}"
        if self.kind == "half_year":
            return f"H{self.half} {self.year}" if self.year else f"H{self.half}"
        if self.kind == "as_of":
            return f"as of {self.year}-{self.month:02d}-{self.day:02d}" if self.day else f"as of {self.year}-{self.month:02d}"
        if self.kind == "period_end":
            return f"year ended {self.year}-{self.month:02d}-{self.day:02d}" if self.day else f"period ended {self.year}-{self.month:02d}"
        if self.kind == "relative":
            return f"relative period ('{self.label}') -- cannot be resolved without a reporting date"
        return f"{'FY' if self.kind == 'fiscal_year' else ''}{self.year}"


def _two_digit_year(value: int) -> int:
    """FY24 means 2024. Two-digit years below 70 are read as 2000s."""
    if value >= 100:
        return value
    return 2000 + value if value < 70 else 1900 + value


def parse_period(text: Optional[str]) -> Optional[Period]:
    """Parses a reporting period, or returns None when it cannot be read.

    None means "I don't know", and callers must treat it that way rather
    than as "no period" -- the two are different, and conflating them is
    how a genuine contradiction gets explained away.
    """
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    s = raw.lower().replace("–", "-").replace("—", "-")

    if any(term in s for term in _RELATIVE_TERMS):
        return Period(label=raw, kind="relative")

    # Q1 FY25 / Q4 2024 / first quarter of 2023
    q = re.search(r"\bq([1-4])\b", s)
    if q:
        year = re.search(r"\b(?:fy\s*)?(\d{2,4})\b", s[q.end():] or s)
        if year:
            return Period(label=raw, kind="quarter", quarter=int(q.group(1)),
                          year=_two_digit_year(int(year.group(1))))
        return Period(label=raw, kind="quarter", quarter=int(q.group(1)))

    # H1 2024 / H1 FY24 / HY2 2024 / 1H24 / 2HFY25 / first half of 2024.
    # Missed entirely before this: both halves collapsed to just their
    # shared year, so H1 and H2 of the same year compared as SAME --
    # exactly the failure this module exists to prevent for quarters,
    # just one granularity coarser. A seasonal business's H1 and H2 can
    # legitimately differ a great deal; without this, that legitimate
    # difference had no way to be recognized as expected and would read
    # as an unexplained (and false) contradiction.
    #
    # Two regexes because the half marker can come before or after its
    # digit ("H1" vs "1H"), and in the compact form the year runs directly
    # against the letter with no boundary between them ("1H24") -- a
    # single \bh...\b-style pattern can't match both orderings safely.
    half_prefix = re.search(r"\bhy?([12])\s*(?:fy)?\s*(\d{2,4})\b", s)
    half_suffix = None if half_prefix else re.search(r"\b([12])h\s*(?:fy)?\s*(\d{2,4})\b", s)
    half_match = half_prefix or half_suffix
    if half_match:
        return Period(label=raw, kind="half_year", half=int(half_match.group(1)),
                      year=_two_digit_year(int(half_match.group(2))))
    for phrase, half_num in (("first half", 1), ("second half", 2),
                              ("1st half", 1), ("2nd half", 2)):
        if phrase in s:
            year = re.search(r"\b(19|20)(\d{2})\b", s)
            return Period(label=raw, kind="half_year", half=half_num,
                          year=int(year.group(0)) if year else None)

    # "as of March 31, 2024" / "as at 31 March 2024" -- a true point-in-time
    # snapshot (a balance, a headcount, a stock price at a moment).
    as_of = re.search(r"\bas\s+(?:of|at|on)\b(.*)$", s)
    if as_of:
        tail = as_of.group(1)
        month_name = re.search(r"\b(" + "|".join(_MONTHS) + r")\b", tail)
        year = re.search(r"\b(\d{4})\b", tail)
        day = re.search(r"\b(\d{1,2})\b", tail)
        if month_name and year:
            return Period(label=raw, kind="as_of", year=int(year.group(1)),
                          month=_MONTHS[month_name.group(1)],
                          day=int(day.group(1)) if day and int(day.group(1)) <= 31 else None)

    # "year ended 31 March 2024" / "ended 31.03.2024" / "ending June 30" --
    # a PERIOD whose end-date is spelled out explicitly, rather than named
    # by a fiscal-year label. This is a flow/period measurement, the same
    # kind of thing as a fiscal year, NOT a point-in-time snapshot -- kept
    # as its own kind ("period_end") rather than folded into "as_of",
    # which it used to be. "Revenue for the year ended March 31, 2024" and
    # "cash balance as of March 31, 2024" share a calendar anchor but
    # report fundamentally different things (a twelve-month total vs. a
    # balance at an instant); merging them into one kind meant
    # compare_periods could call them SAME purely because the dates
    # matched, silently erasing that distinction.
    period_end = re.search(r"\b(?:year\s+)?end(?:ed|ing)\b(.*)$", s)
    if period_end:
        tail = period_end.group(1)
        month_name = re.search(r"\b(" + "|".join(_MONTHS) + r")\b", tail)
        year = re.search(r"\b(\d{4})\b", tail)
        day = re.search(r"\b(\d{1,2})\b", tail)
        if month_name and year:
            return Period(label=raw, kind="period_end", year=int(year.group(1)),
                          month=_MONTHS[month_name.group(1)],
                          day=int(day.group(1)) if day and int(day.group(1)) <= 31 else None)

    # FY2023-24 / FY23-24 / 2023-2024 / FY 2024 / FY24 / fiscal 2024
    span = re.search(r"\b(?:fy|fiscal(?:\s+year)?)?\s*(\d{4}|\d{2})\s*-\s*(\d{4}|\d{2})\b", s)
    if span:
        start = _two_digit_year(int(span.group(1)))
        end = _two_digit_year(int(span.group(2)))
        # "2023-24" -> the fiscal year *ending* 2024, which is how such a
        # label is conventionally named.
        if end < start:
            end = start + 1
        return Period(label=raw, kind="fiscal_year", year=end, spans_years=(start, end))

    fy = re.search(r"\b(?:fy|fiscal(?:\s+year)?)\s*(\d{2,4})\b", s)
    if fy:
        year = _two_digit_year(int(fy.group(1)))
        return Period(label=raw, kind="fiscal_year", year=year, spans_years=(year - 1, year))

    bare_year = re.search(r"\b(19|20)(\d{2})\b", s)
    if bare_year:
        return Period(label=raw, kind="calendar_year", year=int(bare_year.group(0)))

    return None


@dataclass
class Comparison:
    relation: str      # SAME | DIFFERENT | OVERLAPPING | UNKNOWN
    detail: str
    # Which real-world dimension this verdict is actually about: "time" for
    # compare_periods, and for compare_scopes either "scope" (which entity/
    # slice/operations are covered) or "basis" (how the figure was measured
    # or reported -- actual vs forecast, current vs restated). Kept as a
    # label rather than a hard split into separate fields because a scope
    # string can legitimately carry both ("consolidated actual") -- this
    # only records which one a DIFFERENT/SAME verdict was decided on, for a
    # caller (the relationship adjudicator) that wants to say "these differ
    # in basis, not scope" rather than a single undifferentiated verdict.
    dimension: str = "scope"

    @property
    def distinguishing(self) -> bool:
        """True only when this genuinely separates the two facts. UNKNOWN
        and OVERLAPPING deliberately don't, because neither is evidence
        that the facts describe different things."""
        return self.relation == DIFFERENT


def _subdivision(p: Period) -> Optional[tuple]:
    """(kind, number) for a period that is a fraction of a year, or None
    for a period that covers the whole year (or isn't year-bound at all).
    The shared shape is what lets compare_periods treat quarters and
    halves -- and any future subdivision -- with one rule instead of a
    copy-pasted special case per granularity."""
    if p.kind == "quarter" and p.quarter is not None:
        return ("quarter", p.quarter)
    if p.kind == "half_year" and p.half is not None:
        return ("half_year", p.half)
    return None


def compare_periods(a: Optional[str], b: Optional[str]) -> Comparison:
    """Do these two period strings describe the same reporting period?"""
    pa, pb = parse_period(a), parse_period(b)

    if pa is None or pb is None:
        missing = "both facts" if pa is None and pb is None else ("fact A" if pa is None else "fact B")
        if not a and not b:
            return Comparison(UNKNOWN, "neither fact states a reporting period", dimension="time")
        return Comparison(UNKNOWN, f"the reporting period of {missing} could not be determined", dimension="time")

    if pa.kind == "relative" or pb.kind == "relative":
        return Comparison(
            UNKNOWN,
            f"a period is stated only relative to an unstated reporting date "
            f"('{pa.label}' vs '{pb.label}'), so they cannot be aligned",
            dimension="time",
        )

    if pa.year is None or pb.year is None:
        return Comparison(UNKNOWN, "a period names no year, so the two cannot be aligned", dimension="time")

    if pa.year != pb.year:
        return Comparison(DIFFERENT, f"different periods: {pa.describe()} vs {pb.describe()}", dimension="time")

    # Same year, but a quarter or half is not its own annual figure --
    # generalized so quarters and halves are handled by one rule rather
    # than duplicating this logic per granularity as new ones are added.
    sub_a, sub_b = _subdivision(pa), _subdivision(pb)
    if sub_a is not None or sub_b is not None:
        if sub_a is not None and sub_b is not None:
            if sub_a == sub_b:
                return Comparison(SAME, f"same period: {pa.describe()}", dimension="time")
            if sub_a[0] == sub_b[0]:
                label = sub_a[0].replace("_", "-")
                return Comparison(DIFFERENT, f"different {label}s: {pa.describe()} vs {pb.describe()}", dimension="time")
        # Either mismatched granularities (a quarter against a half) or
        # one subdivision against the whole year -- in both cases the
        # smaller window sits inside the larger one, so a numeric gap is
        # expected rather than a genuine conflict.
        return Comparison(
            OVERLAPPING,
            f"{pa.describe()} and {pb.describe()} are different-sized slices of the year "
            f"-- the smaller falls inside the larger, so the figures are not expected to match",
            dimension="time",
        )

    # A fiscal-style period (a labeled fiscal year, or a period ending on
    # an explicit date) and a bare calendar year of the same number overlap
    # but are not confirmed to be the same window.
    if (pa.kind in _FISCAL_LIKE_KINDS and pb.kind == "calendar_year") or (
        pb.kind in _FISCAL_LIKE_KINDS and pa.kind == "calendar_year"
    ):
        return Comparison(
            OVERLAPPING,
            f"a fiscal-style period and a calendar year of the same number ({pa.describe()} vs "
            f"{pb.describe()}) cover overlapping but different windows",
            dimension="time",
        )

    # A point-in-time snapshot (as_of) and an explicit period-end date
    # (period_end) can be anchored to the identical calendar date and still
    # not be the same KIND of measurement: "cash balance as of March 31,
    # 2024" is an instant, "revenue for the year ended March 31, 2024" is a
    # twelve-month total. Checked together (not just when both are as_of)
    # so the cross case -- one snapshot, one explicit period-end, same
    # date -- is caught too, not just genuine as_of-vs-as_of pairs.
    if pa.kind in _DATE_ANCHORED_KINDS and pb.kind in _DATE_ANCHORED_KINDS:
        if (pa.month, pa.day) != (pb.month, pb.day):
            return Comparison(DIFFERENT, f"different dates: {pa.describe()} vs {pb.describe()}", dimension="time")
        if pa.kind == pb.kind:
            return Comparison(SAME, f"same date: {pa.describe()}", dimension="time")
        return Comparison(
            OVERLAPPING,
            f"{pa.describe()} and {pb.describe()} share a calendar date, but one is a "
            f"point-in-time snapshot and the other is a period ending on that date -- a balance "
            f"is not expected to equal a flow accumulated over the period ending on it",
            dimension="time",
        )

    return Comparison(SAME, f"same period: {pa.describe()}", dimension="time")


# Contrastive qualifier axes. Each axis is a tuple of 2+ mutually
# contrasting sides; a qualifier from one side of an axis contradicts a
# qualifier from a DIFFERENT side of the SAME axis. Qualifiers from
# different axes are orthogonal, not contrasting -- "gross" (this axis)
# and "actual" (a different axis) do not contradict each other; a figure
# can be gross AND actual at the same time. An earlier version of this
# module checked every group in a flat list against every OTHER group
# regardless of axis, so "gross" vs "actual" (and "consolidated" vs
# "forecast", etc.) were wrongly reported as contrasting scopes. That
# mattered more once a deterministic scope verdict gained the authority to
# override an LLM's relationship judgment (see app/adjudication.py) --
# advisory-only text can survive being occasionally wrong; a hard override
# cannot.
#
# "scope" axes describe WHICH entity/operations/slice is covered. "basis"
# axes describe HOW a figure was measured or reported. The distinction is
# a labeling convenience for callers that want to say "these differ in
# basis, not scope" (see Comparison.dimension) -- not a claim that every
# qualifier cleanly belongs to exactly one or the other.
_AXES: list[tuple[str, tuple[set, ...]]] = [
    ("scope", ({"consolidated", "group"}, {"standalone", "separate", "company", "entity"})),
    ("scope", ({"gross"}, {"net"})),
    ("scope", ({"continuing"}, {"discontinued"})),
    ("scope", ({"annual", "yearly"}, {"quarterly", "interim"})),
    ("basis", ({"current"}, {"historical", "prior", "restated"})),
    ("basis", ({"actual", "reported"}, {"estimated", "forecast", "projected", "budgeted"})),
]

_SCOPE_STOP = {"the", "a", "an", "of", "for", "in", "on", "at", "to", "and",
               "or", "is", "as", "by", "with", "from", "basis", "level"}


def scope_tokens(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    words = re.split(r"\W+", str(text).lower())
    return {w for w in words if w and w not in _SCOPE_STOP and len(w) > 2}


def compare_scopes(a: Optional[str], b: Optional[str]) -> Comparison:
    """Do these two scope strings describe the same reporting scope?"""
    ta, tb = scope_tokens(a), scope_tokens(b)
    if not ta or not tb:
        return Comparison(UNKNOWN, "at least one fact does not state a scope", dimension="scope")

    if ta == tb:
        return Comparison(SAME, f"same scope: {', '.join(sorted(ta))}", dimension="scope")

    # Opposite sides of a known axis make the scopes incompatible on that
    # axis specifically -- checked axis-by-axis, never across axes.
    for dimension, sides in _AXES:
        for i, left in enumerate(sides):
            for right in sides[i + 1:]:
                if (ta & left and tb & right) or (ta & right and tb & left):
                    return Comparison(
                        DIFFERENT,
                        f"contrasting {dimension}: '{', '.join(sorted(ta & (left | right)))}' vs "
                        f"'{', '.join(sorted(tb & (left | right)))}'",
                        dimension=dimension,
                    )

    # Different words drawn from the same side of an axis are synonyms,
    # not a difference -- "consolidated" and "group" name one scope.
    for dimension, sides in _AXES:
        for side in sides:
            if ta & side and tb & side:
                return Comparison(
                    SAME,
                    f"equivalent {dimension}: '{', '.join(sorted(ta & side))}' and "
                    f"'{', '.join(sorted(tb & side))}' describe the same {dimension}",
                    dimension=dimension,
                )

    if ta & tb:
        return Comparison(
            OVERLAPPING,
            f"scopes partly agree ('{', '.join(sorted(ta & tb))}') but are not identical",
            dimension="scope",
        )
    return Comparison(DIFFERENT, f"different scopes: '{a}' vs '{b}'", dimension="scope")


def format_context_for_prompt(period: Comparison, scope: Comparison,
                               values_agree: Optional[bool] = None) -> str:
    """One block, ready to drop into the reasoning prompt, so the model is
    given these determinations rather than asked to make them.

    `values_agree` is required for the concluding guidance to be correct,
    and leaving it out caused a real regression: the block previously said
    "a material difference in values would not be explained by context"
    whenever the periods matched, even when the numeric comparison
    immediately above it reported the values agreeing to 0.006%. The model
    read that as licence to call a clean corroboration a contradiction.

    Context only ever explains a difference. When there is no difference,
    saying anything about explaining one is worse than saying nothing.
    """
    lines = [f"Reporting period: {period.relation.upper()} -- {period.detail}.",
             f"Scope: {scope.relation.upper()} -- {scope.detail}."]

    if values_agree is True:
        lines.append(
            "The values AGREE, so there is no discrepancy for context to explain. Period and "
            "scope are shown only to confirm the two facts are comparable."
        )
        return "\n".join(lines)

    if values_agree is None:
        # No usable numeric comparison; the model must judge agreement from
        # the statements, so promising it anything about differences would
        # be asserting more than is known.
        return "\n".join(lines)

    if period.relation == DIFFERENT or scope.relation == DIFFERENT:
        lines.append(
            "The values differ AND the facts differ in period or scope, so the difference is "
            "EXPECTED and is not by itself evidence of a contradiction."
        )
    elif period.relation == SAME and scope.relation in (SAME, UNKNOWN):
        lines.append(
            "The values differ, the facts cover the same period, and no scope difference "
            "separates them -- so this difference is not explained by context."
        )
    return "\n".join(lines)
