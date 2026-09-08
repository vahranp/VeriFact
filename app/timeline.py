"""
Metric timelines: turning isolated facts into a trend, deterministically.

Extraction produces a pile of individual facts. A `reconciled` or
`corroborates` relationship between two of them is already a claim that
they describe the *same underlying metric* -- step 1 of relationship
classification (app/relationships.py) exists specifically to establish
that. What nothing does yet is follow that claim through: if fact A is
"the same metric" as fact B, and B is "the same metric" as C, then
A-B-C is not three isolated numbers, it is one metric's value at three
points in time.

This module finds those chains and turns them into timelines: revenue
across FY22/FY23/FY24, a headcount over four quarters, a country's GDP
growth across a decade of an IMF report -- whatever the document
actually reports, with no metric list or accounting vocabulary hard-coded
anywhere here. The chain is discovered from relationships and periods
that already exist; nothing new is extracted or asked of the model.

Two deliberate scope limits, stated rather than silently assumed:

- Only `corroborates` and `reconciled` edges are followed. A `contradicts`
  or `uncertain` edge means the system does NOT have a settled claim that
  two facts share one coherent value, so it must not be smoothed into a
  trend line -- that would hide the disagreement instead of reporting it.
- One representative value per period. A metric family can mix scopes
  (standalone/consolidated) or units across its facts; rather than guess
  which one is "the" value for an ambiguous period, this picks the scope
  that recurs most often across the whole chain and stays with it, so the
  narrative is at least internally consistent, and says which scope that
  was.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.context import OVERLAPPING, compare_periods, parse_period
from app.normalize import normalize_unit

_CHAINABLE = {"corroborates", "reconciled"}


def _is_subdivision(period) -> bool:
    """True for a quarter or half-year -- a period that is only PART of a
    year, never a candidate to chain against a whole-year period."""
    return period is not None and period.kind in ("quarter", "half_year")


@dataclass
class TimelinePoint:
    fact_id: int
    document_id: int
    period_label: str
    sort_key: tuple
    value: float               # normalized to the timeline's common base unit
    original_value: object
    original_unit: Optional[str]
    scope: Optional[str]
    statement: str

    def pct_change_from(self, prev: "TimelinePoint") -> Optional[float]:
        if prev.value == 0:
            return None
        return round((self.value - prev.value) / abs(prev.value) * 100, 2)


@dataclass
class Timeline:
    label: str                  # e.g. "Delhivery -- revenue from operations"
    base_unit: str
    scope_used: Optional[str]
    points: list[TimelinePoint] = field(default_factory=list)
    fact_ids: set = field(default_factory=set)

    def summary(self) -> str:
        if len(self.points) < 2:
            return f"{self.label}: a single data point, no trend to report"
        parts = [f"{p.period_label} {p.original_value}" for p in self.points]
        return f"{self.label}: " + " -> ".join(parts)


def _union_find_components(fact_ids: set, edges: list[tuple]) -> dict:
    parent = {fid: fid for fid in fact_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)

    components: dict = {}
    for fid in fact_ids:
        components.setdefault(find(fid), []).append(fid)
    return components


def _representative_label(facts: list[dict]) -> str:
    """A short label for the family, drawn from whichever fact has the
    most complete subject+attribute -- there is no canonical name to pick
    from, so this just prefers the most descriptive one available."""
    best = max(facts, key=lambda f: len(str(f.get("subject") or "")) + len(str(f.get("attribute") or "")))
    subject = best.get("subject") or ""
    attribute = best.get("attribute") or "value"
    return f"{subject} -- {attribute}" if subject else attribute


def build_timelines(facts: list[dict], relationships: list[dict],
                     min_points: int = 2) -> list[Timeline]:
    """Finds chains of same-metric facts across distinct periods and
    returns them as timelines, longest first.

    Purely deterministic -- no LLM call. Reuses relationships and periods
    that already exist; this only follows the chains they imply."""
    facts_by_id = {f["id"]: f for f in facts if f.get("id") is not None}

    chain_edges = []
    for r in relationships:
        if r.get("relation_type") not in _CHAINABLE:
            continue
        fa, fb = facts_by_id.get(r.get("fact_id_a")), facts_by_id.get(r.get("fact_id_b"))
        if fa is None or fb is None:
            continue
        # A relationship can be `reconciled` because the periods
        # genuinely differ (FY23 vs FY24 -- a real second point in time)
        # or because one period sits INSIDE the other (a quarter against
        # its own year). Chaining the second kind produced a real bug:
        # "Q4 2023 -> FY2024 -> Q3 2024" as if a quarter and its parent
        # year were sequential points, when a quarter is expected to be
        # smaller than its own annual total by construction, not a trend
        # step. Only a genuinely distinguishing period difference extends
        # a timeline; OVERLAPPING must not.
        if compare_periods(fa.get("time_period"), fb.get("time_period")).relation == OVERLAPPING:
            continue
        # Belt and suspenders: the OVERLAPPING check above only fires when
        # both periods resolve to the same year, but fiscal-year start
        # month is never stated in these documents, so "Q4 2023" and
        # "FY2024" compare as DIFFERENT YEARS even when, under a
        # April-starting fiscal convention, Q4 calendar 2023 falls inside
        # FY2024 -- a real gap this exposed. Rather than guess a fiscal
        # start month that was never given, a subdivision (quarter/half)
        # is never chained against a whole year at all, independent of
        # what the year comparison concluded.
        if _is_subdivision(parse_period(fa.get("time_period"))) != _is_subdivision(parse_period(fb.get("time_period"))):
            continue
        chain_edges.append((r["fact_id_a"], r["fact_id_b"]))

    components = _union_find_components(set(facts_by_id), chain_edges)

    timelines: list[Timeline] = []
    for member_ids in components.values():
        if len(member_ids) < min_points:
            continue

        # Only facts with both a parseable period and a normalizable
        # numeric value can become a point on the timeline.
        dated: list[tuple] = []
        for fid in member_ids:
            fact = facts_by_id[fid]
            period = parse_period(fact.get("time_period"))
            if period is None or period.year is None or period.kind == "relative":
                continue
            norm = normalize_unit(fact.get("value_numeric"), fact.get("unit"))
            if norm is None:
                continue
            sort_key = (period.year, period.half or 0, period.quarter or 0)
            dated.append((sort_key, period.describe(), fact, norm))

        if len(dated) < min_points:
            continue

        # One representative value per distinct period. Where a period has
        # several facts (e.g. standalone and consolidated both stated),
        # prefer whichever scope recurs most often across the whole chain,
        # so the narrative stays internally consistent rather than jumping
        # scope from point to point.
        scope_counts: dict = {}
        for _, _, fact, _ in dated:
            scope = (fact.get("scope") or "").strip().lower()
            if scope:
                scope_counts[scope] = scope_counts.get(scope, 0) + 1
        preferred_scope = max(scope_counts, key=scope_counts.get) if scope_counts else None

        by_period: dict = {}
        for sort_key, label, fact, norm in dated:
            scope = (fact.get("scope") or "").strip().lower()
            existing = by_period.get(sort_key)
            if existing is None:
                by_period[sort_key] = (label, fact, norm)
            elif preferred_scope and scope == preferred_scope and (
                (existing[1].get("scope") or "").strip().lower() != preferred_scope
            ):
                by_period[sort_key] = (label, fact, norm)

        if len(by_period) < min_points:
            continue

        # All points must share a comparable base unit -- a "timeline"
        # mixing INR and a headcount would be meaningless. The most common
        # base unit among the candidate points wins; points that don't
        # match it are dropped rather than silently converted wrong.
        unit_counts: dict = {}
        for _, _, norm in by_period.values():
            unit_counts[norm.base_unit] = unit_counts.get(norm.base_unit, 0) + 1
        base_unit = max(unit_counts, key=unit_counts.get)

        points = []
        for sort_key in sorted(by_period):
            label, fact, norm = by_period[sort_key]
            if norm.base_unit != base_unit:
                continue
            points.append(TimelinePoint(
                fact_id=fact["id"], document_id=fact.get("document_id"),
                period_label=label, sort_key=sort_key, value=norm.value,
                original_value=fact.get("value"), original_unit=fact.get("unit"),
                scope=fact.get("scope"), statement=fact.get("statement", ""),
            ))

        if len(points) < min_points:
            continue

        timelines.append(Timeline(
            label=_representative_label([facts_by_id[i] for i in member_ids]),
            base_unit=base_unit, scope_used=preferred_scope, points=points,
            fact_ids=set(member_ids),
        ))

    timelines.sort(key=lambda t: -len(t.points))
    return timelines
