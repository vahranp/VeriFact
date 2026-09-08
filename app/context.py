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
    kind: str               # "fiscal_year" | "calendar_year" | "quarter" | "as_of" | "relative"
    year: Optional[int] = None
    quarter: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    # A fiscal year written "2023-24" spans two calendar years; kept so a
    # fiscal label can be compared against a calendar one without pretending
    # they are identical.
    spans_years: tuple[int, int] | None = None

    def describe(self) -> str:
        if self.kind == "quarter":
            return f"Q{self.quarter} {self.year}"
        if self.kind == "as_of":
            return f"as of {self.year}-{self.month:02d}-{self.day:02d}" if self.day else f"as of {self.year}-{self.month:02d}"
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

    # "as of March 31, 2024" / "as at 31 March 2024" / "ended 31.03.2024"
    as_of = re.search(
        r"\b(?:as\s+(?:of|at|on)|ended|ending|year\s+ended)\b(.*)$", s
    )
    if as_of:
        tail = as_of.group(1)
        month_name = re.search(r"\b(" + "|".join(_MONTHS) + r")\b", tail)
        year = re.search(r"\b(\d{4})\b", tail)
        day = re.search(r"\b(\d{1,2})\b", tail)
        if month_name and year:
            return Period(label=raw, kind="as_of", year=int(year.group(1)),
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

    @property
    def distinguishing(self) -> bool:
        """True only when this genuinely separates the two facts. UNKNOWN
        and OVERLAPPING deliberately don't, because neither is evidence
        that the facts describe different things."""
        return self.relation == DIFFERENT


def compare_periods(a: Optional[str], b: Optional[str]) -> Comparison:
    """Do these two period strings describe the same reporting period?"""
    pa, pb = parse_period(a), parse_period(b)

    if pa is None or pb is None:
        missing = "both facts" if pa is None and pb is None else ("fact A" if pa is None else "fact B")
        if not a and not b:
            return Comparison(UNKNOWN, "neither fact states a reporting period")
        return Comparison(UNKNOWN, f"the reporting period of {missing} could not be determined")

    if pa.kind == "relative" or pb.kind == "relative":
        return Comparison(
            UNKNOWN,
            f"a period is stated only relative to an unstated reporting date "
            f"('{pa.label}' vs '{pb.label}'), so they cannot be aligned",
        )

    if pa.year is None or pb.year is None:
        return Comparison(UNKNOWN, "a period names no year, so the two cannot be aligned")

    if pa.year != pb.year:
        return Comparison(DIFFERENT, f"different periods: {pa.describe()} vs {pb.describe()}")

    # Same year, but a quarter is not its own annual figure.
    if pa.kind == "quarter" or pb.kind == "quarter":
        if pa.quarter is not None and pb.quarter is not None:
            if pa.quarter == pb.quarter:
                return Comparison(SAME, f"same period: {pa.describe()}")
            return Comparison(DIFFERENT, f"different quarters: {pa.describe()} vs {pb.describe()}")
        return Comparison(
            OVERLAPPING,
            f"one is a quarter and the other covers the year ({pa.describe()} vs "
            f"{pb.describe()}) -- the shorter falls inside the longer, so the "
            f"figures are not expected to match",
        )

    # A fiscal year and a calendar year sharing a label overlap but are not
    # the same window.
    if {pa.kind, pb.kind} == {"fiscal_year", "calendar_year"}:
        return Comparison(
            OVERLAPPING,
            f"a fiscal year and a calendar year of the same number ({pa.describe()} vs "
            f"{pb.describe()}) cover overlapping but different windows",
        )

    if pa.kind == "as_of" and pb.kind == "as_of":
        if (pa.month, pa.day) == (pb.month, pb.day):
            return Comparison(SAME, f"same date: {pa.describe()}")
        return Comparison(DIFFERENT, f"different dates: {pa.describe()} vs {pb.describe()}")

    return Comparison(SAME, f"same period: {pa.describe()}")


# Contrastive qualifier pairs. Membership of opposite sides is what makes
# two scopes incompatible; a qualifier appearing in neither list is still
# compared as a plain token, so an unfamiliar domain's vocabulary is not
# silently dropped.
_CONTRASTS = [
    {"consolidated", "group"}, {"standalone", "separate", "company", "entity"},
    {"gross"}, {"net"},
    {"continuing"}, {"discontinued"},
    {"current"}, {"historical", "prior", "restated"},
    {"annual", "yearly"}, {"quarterly", "interim"},
    {"actual", "reported"}, {"estimated", "forecast", "projected", "budgeted"},
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
        return Comparison(UNKNOWN, "at least one fact does not state a scope")

    if ta == tb:
        return Comparison(SAME, f"same scope: {', '.join(sorted(ta))}")

    # Opposite sides of a known contrast make the scopes incompatible.
    for i, left in enumerate(_CONTRASTS):
        for right in _CONTRASTS[i + 1:]:
            if (ta & left and tb & right) or (ta & right and tb & left):
                return Comparison(
                    DIFFERENT,
                    f"contrasting scopes: '{', '.join(sorted(ta & (left | right)))}' vs "
                    f"'{', '.join(sorted(tb & (left | right)))}'",
                )

    # Different words drawn from the same side of a contrast are synonyms,
    # not a difference -- "consolidated" and "group" name one scope.
    for group in _CONTRASTS:
        if ta & group and tb & group:
            return Comparison(
                SAME,
                f"equivalent scopes: '{', '.join(sorted(ta & group))}' and "
                f"'{', '.join(sorted(tb & group))}' describe the same scope",
            )

    if ta & tb:
        return Comparison(
            OVERLAPPING,
            f"scopes partly agree ('{', '.join(sorted(ta & tb))}') but are not identical",
        )
    return Comparison(DIFFERENT, f"different scopes: '{a}' vs '{b}'")


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
