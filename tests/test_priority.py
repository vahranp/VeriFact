"""Tests for deterministic attention-priority ranking.

The point of this module is triage: with hundreds of facts, tell a
reviewer where to look first, using only signals already proven
elsewhere in the codebase (coherence violations, arithmetic anomalies,
evidence status) rather than spending a model call on one more opinion.
These tests pin the ordering claims -- a proven error must outrank a
mere disagreement, which must outrank an already-explained one.
"""
from app.priority import (
    CRITICAL, HIGH, LOW, MEDIUM, rank_facts, rank_relationships,
    score_fact, score_relationship,
)


def _rel(rel_id, a, b, relation, confidence=0.9):
    return {"id": rel_id, "fact_id_a": a, "fact_id_b": b,
            "relation_type": relation, "confidence": confidence}


def _fact(fact_id, document_id=1, evidence_status=None):
    return {"id": fact_id, "document_id": document_id, "evidence_status": evidence_status,
            "value_numeric": None, "unit": None, "time_period": None, "scope": None}


class TestRelationshipPriority:
    def test_an_implicated_edge_is_critical(self):
        score = score_relationship(_rel(1, 10, 20, "contradicts"), implicated_edge_ids={1})
        assert score.level == CRITICAL

    def test_a_plain_contradiction_is_high_not_critical(self):
        """Real, but not proven by logic the way a coherence violation is."""
        score = score_relationship(_rel(1, 10, 20, "contradicts"), implicated_edge_ids=set())
        assert score.level == HIGH

    def test_higher_confidence_contradictions_score_higher(self):
        low_conf = score_relationship(_rel(1, 10, 20, "contradicts", confidence=0.4), set())
        high_conf = score_relationship(_rel(2, 10, 20, "contradicts", confidence=0.95), set())
        assert high_conf.score > low_conf.score

    def test_uncertain_outranks_reconciled(self):
        uncertain = score_relationship(_rel(1, 10, 20, "uncertain"), set())
        reconciled = score_relationship(_rel(2, 10, 20, "reconciled"), set())
        assert uncertain.score > reconciled.score

    def test_a_reconciled_relationship_is_low_priority(self):
        """Already explained -- the system did its job, nothing to review."""
        score = score_relationship(_rel(1, 10, 20, "reconciled"), set())
        assert score.level == LOW

    def test_reasons_are_never_empty(self):
        score = score_relationship(_rel(1, 10, 20, "reconciled"), set())
        assert score.reasons


class TestFactPriority:
    def test_an_ungrounded_fact_is_elevated(self):
        score = score_fact(_fact(1, evidence_status="ungrounded"), [], set(), set())
        assert score.level in (MEDIUM, HIGH, CRITICAL)

    def test_a_fully_verified_isolated_fact_is_low(self):
        score = score_fact(_fact(1, evidence_status="fact_validated"), [], set(), set())
        assert score.level == LOW

    def test_a_scale_anomaly_suspect_is_elevated(self):
        score = score_fact(_fact(1), [], set(), scale_anomaly_fact_ids={1})
        assert score.level in (MEDIUM, HIGH, CRITICAL)
        assert any("arithmetic" in r for r in score.reasons)

    def test_involvement_in_an_implicated_edge_is_critical(self):
        rels = [_rel(1, 1, 2, "corroborates")]
        score = score_fact(_fact(1), rels, implicated_edge_ids={1}, scale_anomaly_fact_ids=set())
        assert score.level == CRITICAL

    def test_more_implicated_edges_score_higher_but_with_diminishing_returns(self):
        one = score_fact(_fact(1), [_rel(1, 1, 2, "corroborates")], {1}, set())
        two = score_fact(_fact(1), [_rel(1, 1, 2, "corroborates"), _rel(2, 1, 3, "corroborates")], {1, 2}, set())
        three = score_fact(
            _fact(1),
            [_rel(1, 1, 2, "corroborates"), _rel(2, 1, 3, "corroborates"), _rel(3, 1, 4, "corroborates")],
            {1, 2, 3}, set(),
        )
        assert two.score > one.score
        assert three.score == two.score, "capped after 2 -- diminishing returns, not unbounded growth"

    def test_high_degree_alone_is_a_minor_signal_not_a_dominant_one(self):
        """Centrality matters, but only as a tie-breaker -- a fact with
        many boring relationships should not outrank a fact with one
        proven error."""
        many_boring = [_rel(i, 1, i + 1, "reconciled") for i in range(10)]
        central = score_fact(_fact(1), many_boring, set(), set())
        proven_wrong = score_fact(_fact(2), [_rel(99, 2, 3, "contradicts")], {99}, set())
        assert proven_wrong.score > central.score

    def test_reasons_are_never_empty(self):
        score = score_fact(_fact(1), [], set(), set())
        assert score.reasons
        assert "no elevated signal" in score.reasons[0]


class TestRankingOrder:
    def test_facts_are_sorted_most_urgent_first(self):
        facts = [_fact(1, evidence_status="fact_validated"), _fact(2, evidence_status="ungrounded")]
        rels = [_rel(1, 2, 3, "contradicts")]
        ranked = rank_facts(facts, rels)
        assert ranked[0][0]["id"] == 2, "the ungrounded, contradicted fact must come first"

    def test_relationships_are_sorted_most_urgent_first(self):
        rels = [_rel(1, 10, 20, "reconciled"), _rel(2, 30, 40, "contradicts")]
        ranked = rank_relationships(rels)
        assert ranked[0][0]["id"] == 2

    def test_document_scoping_excludes_other_documents(self):
        facts = [_fact(1, document_id=1), _fact(2, document_id=2)]
        rels = [_rel(1, 1, 2, "contradicts")]
        ranked = rank_facts(facts, rels, document_id=1)
        assert [f["id"] for f, _ in ranked] == [1]

    def test_an_empty_corpus_is_safe(self):
        assert rank_facts([], []) == []
        assert rank_relationships([]) == []
