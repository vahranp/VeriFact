"""
Deterministic attention-priority ranking for facts and relationships.

A corpus grows to hundreds of facts and thousands of relationships, and
nobody can review all of them. The obvious next move is to ask an LLM
"which of these matter most?" -- and that would be exactly wrong for this
project: it would spend a call producing one more unverified opinion,
on top of all the unverified opinions this system already exists to
check.

Instead, priority is computed the same way everything else in this
project prefers to work: deterministically, from signals that are
already proven rather than guessed.

    PROVEN WRONG        a relationship inside a logically impossible
                         triangle (app/coherence.py) -- not a suspicion,
                         a proof. Always the highest priority.
    FLAGGED BY ARITHMETIC a fact singled out as the probable source of a
                         scale/denomination mismatch (app/arithmetic.py).
    UNEXPLAINED CONFLICT a stored `contradicts` relationship.
    UNSETTLED            a stored `uncertain` relationship, or a fact
                         whose own evidence didn't validate.
    CENTRAL              a fact many other facts have been compared
                         against -- an error here has a wide blast radius.

None of this requires a new model call, a new database table, or a new
extraction pass: every signal already exists somewhere in this project,
scattered across coherence, arithmetic, and evidence checks. This module
is purely a combination step, run on demand over whatever is already in
the database.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.arithmetic import check_arithmetic_consistency
from app.coherence import check_coherence

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

_LEVEL_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}


@dataclass
class PriorityScore:
    level: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.level.upper()} ({self.score:g}): {'; '.join(self.reasons)}"


def _level_for(score: float) -> str:
    if score >= 80:
        return CRITICAL
    if score >= 35:
        return HIGH
    if score >= 15:
        return MEDIUM
    return LOW


def score_relationship(rel: dict, implicated_edge_ids: set) -> PriorityScore:
    """Priority for one relationship row."""
    reasons: list[str] = []
    score = 0.0

    if rel.get("id") in implicated_edge_ids:
        reasons.append("part of a logically impossible triangle -- proven, not suspected")
        score += 100

    relation = rel.get("relation_type")
    if relation == "contradicts":
        score += 40
        confidence = rel.get("confidence")
        if confidence is not None and confidence >= 0.85:
            reasons.append(f"unexplained contradiction at high confidence ({confidence:.2f})")
            score += 10
        else:
            reasons.append("unexplained contradiction")
    elif relation == "uncertain":
        reasons.append("evidence did not settle how these facts relate")
        score += 25
    elif relation == "insufficient_context":
        reasons.append("values differ, but period or scope didn't establish whether that explains it")
        score += 20
    elif relation == "reconciled":
        score += 4  # already explained; lowest live priority, not noise
    elif relation == "related_but_not_comparable":
        score += 2  # a finding, but neither an agreement nor a conflict

    # A relationship where app/adjudication.py overrode the model's own
    # proposal is exactly the case a reviewer should see -- the system
    # caught its own LLM disagreeing, independent of what the final label
    # turned out to be. Always pushes at least into HIGH.
    if rel.get("disagreement"):
        reasons.append(
            f"the deterministic adjudicator overrode the model's proposal of "
            f"'{rel.get('llm_proposal')}' -- worth a second look regardless of the final label"
        )
        score += 35

    if not reasons:
        reasons.append("no elevated signal")
    return PriorityScore(level=_level_for(score), score=round(score, 1), reasons=reasons)


def score_fact(fact: dict, relationships_for_fact: list[dict],
               implicated_edge_ids: set, scale_anomaly_fact_ids: set) -> PriorityScore:
    """Priority for one fact, from its own evidence status and everything
    it's been compared against."""
    reasons: list[str] = []
    score = 0.0

    evidence_status = fact.get("evidence_status")
    if evidence_status == "ungrounded":
        reasons.append("quote not found verbatim in the source")
        score += 30
    elif evidence_status == "quote_grounded":
        reasons.append("quote does not clearly support the extracted value")
        score += 15

    if fact.get("id") in scale_anomaly_fact_ids:
        reasons.append("flagged by arithmetic self-validation as a probable unit/denomination misread")
        score += 35

    implicated = sum(1 for r in relationships_for_fact if r.get("id") in implicated_edge_ids)
    if implicated:
        reasons.append(f"involved in {implicated} logically impossible triangle(s)")
        # A single implicated edge is already a PROVEN error, the same
        # tier as score_relationship gives it directly -- one occurrence
        # must already clear the critical threshold on its own.
        score += 80 * min(implicated, 2)

    contradicts = sum(1 for r in relationships_for_fact if r.get("relation_type") == "contradicts")
    if contradicts:
        reasons.append(f"involved in {contradicts} unexplained contradiction(s)")
        score += 20 * min(contradicts, 3)

    degree = len(relationships_for_fact)
    if degree >= 5:
        reasons.append(f"high graph centrality ({degree} relationships) -- an error here is widely felt")
        score += 5

    if not reasons:
        reasons.append("no elevated signal")
    return PriorityScore(level=_level_for(score), score=round(score, 1), reasons=reasons)


def _scale_anomaly_fact_ids(facts: list[dict]) -> set:
    """Re-runs arithmetic validation fresh, over facts that carry a real
    database id, so a scale anomaly's suspect fact can be traced back to
    a specific row -- the pipeline's own run of this (app/pipeline.py)
    only ever persists a text description, not a fact_id, because at the
    point it runs facts haven't been inserted yet."""
    report = check_arithmetic_consistency(facts)
    return {a.suspect_fact.get("id") for a in report.scale_anomalies if a.suspect_fact.get("id") is not None}


def rank_facts(facts: list[dict], relationships: list[dict],
               document_id: Optional[int] = None) -> list[tuple[dict, PriorityScore]]:
    """Ranks facts by priority, highest first. Pass document_id to scope
    the coherence/arithmetic re-check to one document's own facts and
    relationships (cheaper, and what a per-document view wants); omit it
    to rank across the whole corpus."""
    if document_id is not None:
        facts = [f for f in facts if f.get("document_id") == document_id]
        fact_ids = {f["id"] for f in facts}
        relationships = [r for r in relationships
                         if r.get("fact_id_a") in fact_ids or r.get("fact_id_b") in fact_ids]

    facts_by_id = {f["id"]: f for f in facts}
    coherence = check_coherence(relationships, facts_by_id=facts_by_id, infer=False)
    scale_anomaly_ids = _scale_anomaly_fact_ids(facts)

    by_fact: dict = {}
    for r in relationships:
        by_fact.setdefault(r.get("fact_id_a"), []).append(r)
        by_fact.setdefault(r.get("fact_id_b"), []).append(r)

    scored = [
        (f, score_fact(f, by_fact.get(f["id"], []), coherence.implicated_edges, scale_anomaly_ids))
        for f in facts
    ]
    scored.sort(key=lambda pair: (_LEVEL_ORDER[pair[1].level], -pair[1].score))
    return scored


def rank_relationships(relationships: list[dict], facts_by_id: Optional[dict] = None,
                        document_id: Optional[int] = None) -> list[tuple[dict, PriorityScore]]:
    """Ranks relationships by priority, highest first."""
    if document_id is not None and facts_by_id is not None:
        fact_ids = {fid for fid, f in facts_by_id.items() if f.get("document_id") == document_id}
        relationships = [r for r in relationships
                         if r.get("fact_id_a") in fact_ids or r.get("fact_id_b") in fact_ids]

    coherence = check_coherence(relationships, facts_by_id=facts_by_id, infer=False)
    scored = [(r, score_relationship(r, coherence.implicated_edges)) for r in relationships]
    scored.sort(key=lambda pair: (_LEVEL_ORDER[pair[1].level], -pair[1].score))
    return scored
