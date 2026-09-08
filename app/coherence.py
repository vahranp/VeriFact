"""
Logical coherence of the relationship graph.

Every relationship in this system is judged pairwise and in isolation: two
facts go to the model, one label comes back. That is a reasonable way to
build the graph, but it throws away something real -- the graph as a whole
has logical structure that no individual judgment can see.

`corroborates` is an assertion of equality, and equality is transitive:

    A corroborates B, and B corroborates C  =>  A must corroborate C

`contradicts` and `reconciled` both assert *in*equality (the second with a
stated reason for it). So across any triangle of three facts, writing E for
an equality edge and N for an inequality edge, only some patterns are
logically possible:

    E E E   consistent -- three facts all agreeing
    E N N   consistent -- A = B, and both differ from C
    N N N   consistent -- three genuinely different values
    E E N   IMPOSSIBLE -- A = B and B = C, yet A != C

That last pattern cannot occur in a correct graph. When it appears, at
least one of those three LLM judgments is provably wrong -- and we know
this without a ground-truth label, without a human reviewer, and without
spending a single additional LLM call.

Two things fall out of it:

1. **Error detection without an answer key.** Each violating triangle
   guarantees at least one wrong edge, so the violations put a floor under
   the graph's true error rate. That's a rare thing to have: a system that
   can produce evidence of its own mistakes rather than a confidence score
   it made up.

2. **Recall for free.** If A = B and B = C but no edge exists between A and
   C, transitivity *implies* one. Candidate retrieval is a top-K embedding
   shortlist and will always miss pairs; this recovers some of them by
   deduction rather than by widening the search. That matters here
   specifically because widening retrieval was measured and rejected --
   it added 3 pairs of recall for 3x the LLM calls (see PERFORMANCE.md).
   Deduction adds edges at zero marginal cost.

Nothing in this module is domain-specific. It operates on the shape of the
graph, not on what the facts are about.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.normalize import compare_values

# Relations that assert the two facts state the same value.
EQUALITY = {"corroborates"}
# Relations that assert they differ -- "reconciled" is still a difference,
# just one with a stated reason (a period, scope or unit gap).
INEQUALITY = {"contradicts", "reconciled"}

# Evidence statuses (see app/evidence.py) that disqualify a fact's value
# from being used to adjudicate blame. A fact missing evidence_status
# entirely (not one of these three known values) is treated as usable --
# "not yet assessed", not "known bad" -- matching the identical policy in
# app/arithmetic.py.
_UNVERIFIED = {"ungrounded", "quote_grounded"}


@dataclass
class Violation:
    """A triangle whose three labels cannot all be correct."""

    fact_ids: tuple[int, int, int]
    edges: list[dict]                  # the three relationship rows
    suspect: dict                      # the edge most likely to be the wrong one
    reason: str

    def describe(self) -> str:
        labels = " + ".join(sorted(e["relation_type"] for e in self.edges))
        return (
            f"facts {self.fact_ids}: {labels} cannot all hold. "
            f"Most likely wrong: relationship {self.suspect.get('id')} "
            f"({self.suspect.get('relation_type')}, "
            f"confidence {self.suspect.get('confidence')})"
        )


@dataclass
class Inference:
    """An edge implied by transitivity that the graph does not contain."""

    fact_id_a: int
    fact_id_b: int
    relation_type: str
    via_fact_id: int
    confidence: float
    explanation: str
    # The two existing edges this deduction rests on. Kept so an inference
    # standing on an edge that later turns out to be broken can be dropped
    # rather than silently inheriting the error.
    support_edge_ids: tuple = ()


@dataclass
class CoherenceReport:
    violations: list[Violation] = field(default_factory=list)
    inferences: list[Inference] = field(default_factory=list)
    triangles_checked: int = 0
    edges: int = 0
    nodes: int = 0

    @property
    def violation_rate(self) -> float:
        """Share of closed triangles that are logically impossible."""
        return len(self.violations) / self.triangles_checked if self.triangles_checked else 0.0

    @property
    def implicated_edges(self) -> set:
        """Distinct edges appearing in at least one violating triangle. The
        true number of wrong edges is at least one per violation but no more
        than this, since one bad edge can break many triangles."""
        return {e["id"] for v in self.violations for e in v.edges}

    def summary(self) -> str:
        return (
            f"{self.nodes} facts, {self.edges} relationships, "
            f"{self.triangles_checked} closed triangles: "
            f"{len(self.violations)} logically impossible "
            f"({self.violation_rate * 100:.1f}%), "
            f"{len(self.implicated_edges)} edges implicated, "
            f"{len(self.inferences)} edges inferable by transitivity"
        )


def _kind(relation_type: str) -> Optional[str]:
    if relation_type in EQUALITY:
        return "E"
    if relation_type in INEQUALITY:
        return "N"
    return None  # "unrelated" and anything unrecognised carry no claim


def _confidence(edge: dict) -> float:
    c = edge.get("confidence")
    return float(c) if c is not None else 0.5


def _numeric_disagrees_with_label(edge: dict, facts_by_id: Optional[dict]) -> bool:
    """True when an edge's own numbers contradict the label it carries.

    Model confidence turns out to be a poor way to pick the wrong edge in a
    broken triangle -- on real data the false `corroborates` edges came back
    at confidence 1.0 while the correct `reconciled` edge sat at 0.8, so
    blaming the least confident edge accused the right answer.

    The deterministic comparison in app/normalize.py is a far better
    arbiter, because it doesn't have an opinion: if an edge claims two facts
    corroborate while their normalized values differ, that edge is wrong
    regardless of how sure the model sounded.

    That arbiter is only trustworthy if the values it's comparing are
    themselves evidenced. A fact whose quote is ungrounded, or circular
    (just restates the value), has no more claim to being "ground truth"
    than the model's own labels do -- using it to convict an edge would
    swap one unverified opinion for another instead of actually settling
    anything. Audited in alongside the identical gap in app/arithmetic.py.
    """
    if not facts_by_id:
        return False
    fa, fb = facts_by_id.get(edge.get("fact_id_a")), facts_by_id.get(edge.get("fact_id_b"))
    if not fa or not fb:
        return False
    if fa.get("evidence_status") in _UNVERIFIED or fb.get("evidence_status") in _UNVERIFIED:
        return False

    cmp = compare_values(
        fa.get("value_numeric"), fa.get("unit"),
        fb.get("value_numeric"), fb.get("unit"),
    )
    if not cmp.comparable or cmp.magnitude_suspect:
        return False  # no usable arithmetic signal; don't guess

    claims_equal = edge.get("relation_type") in EQUALITY
    return claims_equal is not bool(cmp.agree)


def _blame(edges: list[dict], facts_by_id: Optional[dict]) -> tuple[dict, str]:
    """Picks the edge most likely to be the wrong one, and says why."""
    numerically_wrong = [e for e in edges if _numeric_disagrees_with_label(e, facts_by_id)]
    if len(numerically_wrong) == 1:
        edge = numerically_wrong[0]
        return edge, (
            f"its label '{edge['relation_type']}' disagrees with the deterministic "
            f"comparison of the two values themselves"
        )
    if len(numerically_wrong) > 1:
        edge = min(numerically_wrong, key=_confidence)
        return edge, (
            f"{len(numerically_wrong)} edges in this triangle carry labels their own "
            f"numbers contradict; this is the least confident of them"
        )
    confidences = {_confidence(e) for e in edges}
    if len(confidences) == 1:
        # All three equally confident and no arithmetic to separate them.
        # Saying "least confident" here would dress an arbitrary pick up as
        # a judgment; the honest answer is that the triangle is provably
        # broken but which edge broke it is undetermined.
        return min(edges, key=lambda e: e.get("id") or 0), (
            "the triangle is provably inconsistent, but with no usable numeric "
            "comparison and all three judgments equally confident, which edge is "
            "at fault is undetermined -- all three need review"
        )
    return min(edges, key=_confidence), (
        "no edge's numbers settle it, so the least confident edge is the best guess"
    )


def check_coherence(relationships: Iterable[dict],
                     facts_by_id: Optional[dict] = None,
                     infer: bool = True) -> CoherenceReport:
    """Finds logically impossible triangles, and edges implied by
    transitivity that are missing. Deterministic -- no LLM call.

    Pass facts_by_id ({fact_id: fact_row}) to let the blame step adjudicate
    with the facts' own numbers instead of the model's confidence, which is
    substantially more accurate -- see _blame."""
    report = CoherenceReport()

    # adjacency[node][other] = edge, keeping only claim-bearing relations
    adjacency: dict[int, dict[int, dict]] = defaultdict(dict)
    for rel in relationships:
        a, b = rel.get("fact_id_a"), rel.get("fact_id_b")
        if a is None or b is None or a == b:
            continue
        if _kind(rel.get("relation_type")) is None:
            continue
        # A duplicate pair can exist from separate ingests; keep the more
        # confident judgment rather than whichever happened to be last.
        existing = adjacency[a].get(b)
        if existing is not None and _confidence(existing) >= _confidence(rel):
            continue
        adjacency[a][b] = rel
        adjacency[b][a] = rel

    report.nodes = len(adjacency)
    report.edges = len({id(e) for nbrs in adjacency.values() for e in nbrs.values()})

    seen_triangles: set[tuple[int, int, int]] = set()
    seen_inferences: set[tuple[int, int]] = set()

    for a, neighbours in adjacency.items():
        others = list(neighbours)
        for i, b in enumerate(others):
            for c in others[i + 1:]:
                triangle = tuple(sorted((a, b, c)))

                edge_ab, edge_ac = neighbours[b], neighbours[c]
                edge_bc = adjacency[b].get(c)

                if edge_bc is None:
                    if infer:
                        _maybe_infer(a, b, c, edge_ab, edge_ac, report, seen_inferences)
                    continue

                if triangle in seen_triangles:
                    continue
                seen_triangles.add(triangle)
                report.triangles_checked += 1

                edges = [edge_ab, edge_ac, edge_bc]
                kinds = [_kind(e["relation_type"]) for e in edges]

                # The one impossible pattern: two equalities and one
                # inequality. Everything else is satisfiable.
                if kinds.count("E") == 2 and kinds.count("N") == 1:
                    # The inequality edge is odd-one-out by shape, but the
                    # wrong judgment could equally be either equality, so the
                    # culprit is chosen by evidence rather than by position.
                    suspect, why = _blame(edges, facts_by_id)
                    report.violations.append(Violation(
                        fact_ids=triangle, edges=edges, suspect=suspect,
                        reason=(
                            "two facts are each said to agree with a third, "
                            f"yet are said to disagree with each other -- {why}"
                        ),
                    ))

    # A deduction is only as good as what it stands on. Any inference
    # resting on an edge that a violating triangle implicated is discarded
    # -- propagating a known-broken judgment into new edges would turn one
    # error into several, which is worse than the missing edge it fills.
    if report.violations:
        implicated = report.implicated_edges
        report.inferences = [
            inf for inf in report.inferences
            if not (set(inf.support_edge_ids) & implicated)
        ]

    return report


def _maybe_infer(a: int, b: int, c: int, edge_ab: dict, edge_ac: dict,
                  report: CoherenceReport, seen: set) -> None:
    """b and c are both linked to a but not to each other. Transitivity may
    determine what the missing b-c edge has to be."""
    kind_ab, kind_ac = _kind(edge_ab["relation_type"]), _kind(edge_ac["relation_type"])
    pair = (min(b, c), max(b, c))
    if pair in seen:
        return

    if kind_ab == "E" and kind_ac == "E":
        # b = a and a = c, so b = c.
        relation, wording = "corroborates", "both agree with the same third fact"
    elif kind_ab == "E" and kind_ac == "N":
        # b = a and a != c, so b != c. Carry the *kind* of difference: a
        # reconciled gap (period/scope/unit) propagates as reconciled, not
        # as an unexplained contradiction.
        relation = edge_ac["relation_type"]
        wording = "one agrees with a third fact that the other differs from"
    elif kind_ab == "N" and kind_ac == "E":
        relation = edge_ab["relation_type"]
        wording = "one agrees with a third fact that the other differs from"
    else:
        # N and N determines nothing: two facts that both differ from a
        # third may still equal each other.
        return

    seen.add(pair)
    # An inference is only as trustworthy as the weaker link it rests on.
    confidence = min(_confidence(edge_ab), _confidence(edge_ac))
    report.inferences.append(Inference(
        fact_id_a=b, fact_id_b=c, relation_type=relation, via_fact_id=a,
        confidence=round(confidence, 3),
        support_edge_ids=(edge_ab.get("id"), edge_ac.get("id")),
        explanation=(
            f"Inferred by transitivity through fact {a}: {wording}. "
            f"No model call was made for this pair."
        ),
    ))
