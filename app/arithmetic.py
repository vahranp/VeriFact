"""
Arithmetic self-validation of extracted numbers.

The idea: financial and statistical documents are full of *internal
invariants* -- assets = liabilities + equity, a total equals the sum of
its parts, income - expenses = profit. Nothing tells the system these
identities exist. But if they hold among the numbers we extracted, that's
independent evidence the extraction was read correctly; and if a set of
numbers *almost* satisfies one, that near-miss is a strong, specific
signal that something was misread.

This is deliberately discovery-based rather than rule-based. There is no
table of known accounting equations anywhere in this module -- it searches
for value triples (a + b ~= c) among facts that share a comparable unit,
period and scope, and reports what it finds. That's what keeps it generic
across arbitrary documents instead of being a Delhivery/balance-sheet
special case.

Two failure signals are worth more than the confirmations:

1. A triple that misses by a clean power of ten almost always means one
   of the three values had its *scale* misread -- exactly the real bug
   this project hit, where a balance sheet's "all amounts in millions"
   header was chunked away from its data rows and one figure came back
   denominated in bare rupees.

2. A triple that misses by a small amount is more likely a
   transcription/column-alignment error -- the other real failure mode
   documented here, where a multi-year table's columns were read one
   year-group out of alignment.
"""
from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

from app.normalize import normalize_unit

# Two values are "the same" for identity-discovery purposes within this
# relative tolerance. Rounding in published tables is common, so this is
# deliberately looser than an exact match but far tighter than the
# magnitudes a real mistake produces.
# Tightened from 1.0 after testing on a real balance sheet: at 1% a
# coincidental triple (liabilities + *other* equity landing 0.64% from
# total assets) was reported alongside the two genuine identities, which
# both matched at 0.00%. Published totals are rounded, not approximate,
# so a real identity sits far closer than a coincidence does.
DEFAULT_TOLERANCE_PCT = 0.5

# A relative tolerance on the TOTAL alone is not enough, and real data
# proved it. On a real balance sheet, "other equity" (90,709.67) sits
# within 0.81% of "total equity" (91,446.46), so at 0.5% tolerance on the
# total, *any* second addend between roughly 280 and 1,190 completed a
# passing "identity" -- borrowings, provisions and other financial
# liabilities all did, producing three confident-looking identities that
# are pure coincidence. When one addend dominates, the total's tolerance
# says nothing about whether the small addend is the right one.
#
# So a candidate identity must ALSO land close relative to the smaller
# addend: that value has to be doing real work in the sum, not merely
# fitting inside the slack around a much larger number.
_SMALL_ADDEND_TOLERANCE_PCT = 5.0

# A near-miss whose ratio is within this distance of a clean power of ten
# is reported as a probable scale/unit error rather than a data conflict.
# Held to the same tightness as a genuine identity: at the original 2% a
# group of 13 real figures spanning three orders of magnitude threw nine
# scale "anomalies", every one of them a false positive, because with
# enough numbers some pair always sums to within 2% of 10x another.
_POWER_OF_TEN_TOLERANCE = 0.005

# Identity search is O(n^2) in the size of a comparable group; groups are
# capped so a pathological document can't stall the pipeline.
MAX_GROUP_SIZE = 60


@dataclass
class Identity:
    """A discovered a + b ~= c relationship between three extracted facts."""

    a: dict
    b: dict
    c: dict
    a_value: float
    b_value: float
    c_value: float
    unit: str
    error_pct: float

    def describe(self) -> str:
        return (
            f"{self.a_value:g} + {self.b_value:g} = {self.a_value + self.b_value:g} "
            f"vs stated total {self.c_value:g} ({self.unit}), off by {self.error_pct:.2f}%"
        )


@dataclass
class ScaleAnomaly:
    """Three facts that would satisfy an identity if one of them were
    rescaled by a power of ten -- i.e. a probable unit-metadata error."""

    a: dict
    b: dict
    c: dict
    unit: str
    factor: float          # how far off the stated total is, e.g. 1000.0
    suspect_fact: dict     # the fact whose scale looks wrong

    def describe(self) -> str:
        return (
            f"{self.a.get('statement', '')[:60]} + {self.b.get('statement', '')[:60]} "
            f"matches the stated total only if one value is rescaled by {self.factor:g}x "
            f"-- likely a unit/denomination misread rather than a real discrepancy"
        )


@dataclass
class ArithmeticReport:
    identities: list[Identity] = field(default_factory=list)
    scale_anomalies: list[ScaleAnomaly] = field(default_factory=list)
    groups_checked: int = 0
    facts_considered: int = 0

    def summary(self) -> str:
        return (
            f"{self.facts_considered} numeric facts in {self.groups_checked} comparable groups: "
            f"{len(self.identities)} arithmetic identities confirmed, "
            f"{len(self.scale_anomalies)} probable scale/unit anomalies"
        )


def _group_key(fact: dict) -> Optional[tuple]:
    """Facts are only comparable within the same unit base, period and
    scope -- adding a FY24 figure to a FY23 one, or a consolidated figure
    to a standalone one, would manufacture nonsense identities."""
    norm = normalize_unit(fact.get("value_numeric"), fact.get("unit"))
    if norm is None or norm.base_unit == "%":
        return None  # percentages don't sum meaningfully
    return (
        norm.base_unit,
        (fact.get("time_period") or "").strip().lower(),
        (fact.get("scope") or "").strip().lower(),
    )


def _normalized_value(fact: dict) -> Optional[float]:
    norm = normalize_unit(fact.get("value_numeric"), fact.get("unit"))
    return norm.value if norm else None


def _nearest_power_of_ten_factor(ratio: float) -> Optional[float]:
    """Returns the power of ten the ratio is closest to, if it's close
    enough to be a scale error rather than a coincidence."""
    if ratio <= 0:
        return None
    for exponent in range(1, 10):          # 10x .. 1e9
        power = 10.0 ** exponent
        for candidate in (power, 1.0 / power):
            if candidate == 0:
                continue
            if abs(ratio - candidate) / candidate <= _POWER_OF_TEN_TOLERANCE:
                return candidate
    return None


# Facts whose value isn't itself evidenced must not feed this module.
# The entire argument this module makes is "these numbers satisfy an
# identity they didn't have to satisfy, which is independent evidence
# they were read correctly" -- that only holds if the inputs were
# verified to begin with. A fact whose quote never actually supports its
# value (see app/evidence.py: "ungrounded", or "quote_grounded" without
# validation -- e.g. a circular quote that just restates the number)
# would let an unverified figure pose as ground truth here, exactly
# backwards from what the module exists to establish: it could produce a
# false "scale anomaly" that's really just a wrong value, or worse, a
# coincidental identity that manufactures false confidence in a number
# nothing ever confirmed.
#
# A fact with no evidence_status at all -- the field missing entirely,
# rather than an explicit negative status -- is still included. That
# covers a fact dict built directly (tests, or any future caller not
# routed through the evidence check), which is "not yet assessed", not
# "known bad".
_EXCLUDED_EVIDENCE_STATUSES = {"ungrounded", "quote_grounded"}


def _is_evidenced(fact: dict) -> bool:
    return fact.get("evidence_status") not in _EXCLUDED_EVIDENCE_STATUSES


def check_arithmetic_consistency(facts: list[dict],
                                  tolerance_pct: float = DEFAULT_TOLERANCE_PCT) -> ArithmeticReport:
    """Searches extracted facts for additive identities and for near-misses
    that look like scale errors. Purely deterministic -- no LLM call."""
    report = ArithmeticReport()

    groups: dict[tuple, list[dict]] = {}
    for fact in facts:
        if not _is_evidenced(fact):
            continue
        key = _group_key(fact)
        if key is None:
            continue
        value = _normalized_value(fact)
        if value is None or value == 0:
            continue
        groups.setdefault(key, []).append(fact)

    report.facts_considered = sum(len(g) for g in groups.values())

    for key, group in groups.items():
        if len(group) < 3:
            continue
        group = group[:MAX_GROUP_SIZE]
        report.groups_checked += 1
        unit = key[0]

        values = {id(f): _normalized_value(f) for f in group}

        for a, b in combinations(group, 2):
            total = values[id(a)] + values[id(b)]
            if total == 0:
                continue
            for c in group:
                if c is a or c is b:
                    continue
                c_value = values[id(c)]
                error_pct = abs(total - c_value) / abs(c_value) * 100 if c_value else 100.0

                # The absolute miss must be small relative to the smaller
                # addend too -- see _SMALL_ADDEND_TOLERANCE_PCT.
                smaller = min(abs(values[id(a)]), abs(values[id(b)]))
                residual_pct = abs(total - c_value) / smaller * 100 if smaller else 100.0

                if error_pct <= tolerance_pct and residual_pct <= _SMALL_ADDEND_TOLERANCE_PCT:
                    report.identities.append(Identity(
                        a=a, b=b, c=c,
                        a_value=values[id(a)], b_value=values[id(b)], c_value=c_value,
                        unit=unit, error_pct=round(error_pct, 3),
                    ))
                    continue

                # Near-miss: would this work if one value were rescaled?
                # Only meaningful when the three facts don't already agree
                # on a unit string -- a scale anomaly IS a unit-metadata
                # inconsistency, so if all three were recorded with the
                # same unit there is no inconsistency to find. (A document
                # whose figures are *uniformly* mis-denominated cannot be
                # caught this way at all; internal consistency is blind to
                # a constant factor. See README > Known limitations.)
                stated_units = {
                    (f.get("unit") or "").strip().lower() for f in (a, b, c)
                }
                if len(stated_units) < 2:
                    continue

                factor = _nearest_power_of_ten_factor(abs(total / c_value)) if c_value else None
                if factor is not None:
                    # The suspect is the fact whose stated unit is the odd
                    # one out among the three, which is the fact whose
                    # denomination actually differs.
                    suspect = _odd_unit_out(a, b, c)
                    report.scale_anomalies.append(ScaleAnomaly(
                        a=a, b=b, c=c, unit=unit, factor=round(factor, 4), suspect_fact=suspect,
                    ))

    # Identity search yields the same identity from several orderings;
    # de-duplicate on the underlying fact triple.
    report.identities = _dedupe(report.identities)
    report.scale_anomalies = _dedupe(report.scale_anomalies)
    return report


def _odd_unit_out(a: dict, b: dict, c: dict) -> dict:
    """Of three facts, the one whose stated unit differs from the other
    two -- the fact whose denomination was most likely misread. Falls back
    to the stated total when all three differ."""
    facts = [a, b, c]
    units = [(f.get("unit") or "").strip().lower() for f in facts]
    for i, unit in enumerate(units):
        if units.count(unit) == 1:
            return facts[i]
    return c


def _dedupe(items: list) -> list:
    seen: set = set()
    out = []
    for item in items:
        key = frozenset((
            item.a.get("statement"), item.b.get("statement"), item.c.get("statement"),
        ))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
