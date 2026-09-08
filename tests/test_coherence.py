"""Tests for logical coherence of the relationship graph.

The claim this module makes is a strong one -- that it can prove a set of
LLM judgments contains an error without any ground truth -- so these tests
pin both directions: the impossible pattern must be caught, and every
satisfiable pattern must be left alone. A detector that flags consistent
graphs is worse than none, because it would make the error estimate
meaningless.
"""
import pytest

from app.coherence import check_coherence


def _rel(rel_id, a, b, relation, confidence=0.9):
    return {"id": rel_id, "fact_id_a": a, "fact_id_b": b,
            "relation_type": relation, "confidence": confidence}


def _fact(fact_id, value, unit="INR million"):
    return {"id": fact_id, "value_numeric": value, "unit": unit}


class TestImpossibleTriangles:
    def test_two_corroborates_and_one_contradicts_is_impossible(self):
        """A = B and B = C, yet A != C. Cannot all be true."""
        rels = [
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "corroborates"),
            _rel(3, 10, 30, "contradicts"),
        ]
        report = check_coherence(rels)
        assert len(report.violations) == 1
        assert report.violations[0].fact_ids == (10, 20, 30)

    def test_reconciled_counts_as_an_inequality(self):
        """`reconciled` still asserts the values differ -- it just explains
        why -- so it breaks transitivity exactly as `contradicts` does."""
        rels = [
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "corroborates"),
            _rel(3, 10, 30, "reconciled"),
        ]
        assert len(check_coherence(rels).violations) == 1

    def test_violation_names_all_three_edges(self):
        rels = [
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "corroborates"),
            _rel(3, 10, 30, "contradicts"),
        ]
        violation = check_coherence(rels).violations[0]
        assert {e["id"] for e in violation.edges} == {1, 2, 3}


class TestSatisfiablePatternsAreLeftAlone:
    """Only one of the four triangle patterns is impossible. Flagging any
    of the others would inflate the error estimate into noise."""

    def test_all_corroborates_is_fine(self):
        rels = [
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "corroborates"),
            _rel(3, 10, 30, "corroborates"),
        ]
        assert check_coherence(rels).violations == []

    def test_one_equality_and_two_inequalities_is_fine(self):
        """A = B, and both differ from C. Perfectly consistent."""
        rels = [
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "contradicts"),
            _rel(3, 10, 30, "contradicts"),
        ]
        assert check_coherence(rels).violations == []

    def test_three_mutually_contradicting_facts_are_fine(self):
        """Three different values disagree with each other. Nothing wrong."""
        rels = [
            _rel(1, 10, 20, "contradicts"),
            _rel(2, 20, 30, "contradicts"),
            _rel(3, 10, 30, "contradicts"),
        ]
        assert check_coherence(rels).violations == []

    def test_unrelated_edges_carry_no_claim(self):
        """`unrelated` asserts neither equality nor inequality, so it can
        never complete or break a triangle."""
        rels = [
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "corroborates"),
            _rel(3, 10, 30, "unrelated"),
        ]
        report = check_coherence(rels)
        assert report.violations == []
        assert report.triangles_checked == 0

    def test_an_open_triangle_is_not_a_violation(self):
        """Two edges with no third edge cannot contradict anything."""
        rels = [_rel(1, 10, 20, "corroborates"), _rel(2, 20, 30, "contradicts")]
        report = check_coherence(rels)
        assert report.violations == []
        assert report.triangles_checked == 0


class TestBlameAssignment:
    """Picking which of the three judgments is wrong. Model confidence is a
    poor guide -- on real data the false `corroborates` edges came back at
    confidence 1.0 while the correct `reconciled` edge sat at 0.8 -- so the
    facts' own numbers are used to adjudicate wherever they can."""

    def test_numbers_override_confidence(self):
        rels = [
            # claims agreement between two visibly different values
            _rel(1, 10, 30, "corroborates", confidence=1.0),
            _rel(2, 10, 20, "corroborates", confidence=0.9),
            _rel(3, 20, 30, "reconciled", confidence=0.8),
        ]
        facts = {10: _fact(10, 81415.38), 20: _fact(20, 81415.38), 30: _fact(30, 72253.01)}
        violation = check_coherence(rels, facts_by_id=facts).violations[0]
        assert violation.suspect["id"] == 1, "must blame the edge its own numbers refute"
        assert "deterministic comparison" in violation.reason

    def test_falls_back_to_confidence_without_facts(self):
        rels = [
            _rel(1, 10, 20, "corroborates", confidence=0.95),
            _rel(2, 20, 30, "corroborates", confidence=0.30),
            _rel(3, 10, 30, "contradicts", confidence=0.90),
        ]
        violation = check_coherence(rels).violations[0]
        assert violation.suspect["id"] == 2
        assert "least confident" in violation.reason

    def test_incomparable_units_do_not_produce_a_blame_claim(self):
        """No arithmetic signal means no arithmetic verdict."""
        rels = [
            _rel(1, 10, 20, "corroborates", confidence=0.9),
            _rel(2, 20, 30, "corroborates", confidence=0.4),
            _rel(3, 10, 30, "contradicts", confidence=0.9),
        ]
        facts = {10: _fact(10, 5.0, "%"), 20: _fact(20, 5.0, "INR million"), 30: _fact(30, 9.0, "tons")}
        violation = check_coherence(rels, facts_by_id=facts).violations[0]
        assert "least confident" in violation.reason


class TestTransitiveInference:
    def test_equality_is_transitive(self):
        """A = B and B = C implies A = C, with no model call."""
        rels = [_rel(1, 10, 20, "corroborates"), _rel(2, 20, 30, "corroborates")]
        inferences = check_coherence(rels).inferences
        assert len(inferences) == 1
        inf = inferences[0]
        assert {inf.fact_id_a, inf.fact_id_b} == {10, 30}
        assert inf.relation_type == "corroborates"
        assert inf.via_fact_id == 20

    def test_equality_propagates_the_kind_of_difference(self):
        """A = B and B differs-for-a-reason from C means A differs from C
        for that same reason -- so a reconciled gap must not be inferred as
        an unexplained contradiction."""
        rels = [_rel(1, 10, 20, "corroborates"), _rel(2, 20, 30, "reconciled")]
        assert check_coherence(rels).inferences[0].relation_type == "reconciled"

    def test_two_inequalities_determine_nothing(self):
        """Two facts that both differ from a third may still equal each
        other, so nothing may be inferred."""
        rels = [_rel(1, 10, 20, "contradicts"), _rel(2, 20, 30, "contradicts")]
        assert check_coherence(rels).inferences == []

    def test_inference_confidence_is_the_weaker_link(self):
        rels = [
            _rel(1, 10, 20, "corroborates", confidence=0.9),
            _rel(2, 20, 30, "corroborates", confidence=0.4),
        ]
        assert check_coherence(rels).inferences[0].confidence == 0.4

    def test_no_inference_where_an_edge_already_exists(self):
        rels = [
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "corroborates"),
            _rel(3, 10, 30, "corroborates"),
        ]
        assert check_coherence(rels).inferences == []

    def test_inferences_resting_on_broken_edges_are_discarded(self):
        """Propagating a judgment already known to be wrong would turn one
        error into several -- worse than the missing edge it fills."""
        rels = [
            # a violating triangle among 10/20/30
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "corroborates"),
            _rel(3, 10, 30, "contradicts"),
            # 40 hangs off edge 1, which the violation implicates
            _rel(4, 20, 40, "corroborates"),
        ]
        report = check_coherence(rels)
        assert report.violations
        assert all(not (set(i.support_edge_ids) & report.implicated_edges)
                   for i in report.inferences)

    def test_inference_can_be_turned_off(self):
        rels = [_rel(1, 10, 20, "corroborates"), _rel(2, 20, 30, "corroborates")]
        assert check_coherence(rels, infer=False).inferences == []


class TestReportShape:
    def test_empty_graph_is_safe(self):
        report = check_coherence([])
        assert report.violations == [] and report.violation_rate == 0.0

    def test_self_edges_are_ignored(self):
        assert check_coherence([_rel(1, 10, 10, "corroborates")]).edges == 0

    def test_violation_rate_is_a_share_of_closed_triangles(self):
        rels = [
            _rel(1, 10, 20, "corroborates"),
            _rel(2, 20, 30, "corroborates"),
            _rel(3, 10, 30, "contradicts"),
        ]
        report = check_coherence(rels)
        assert report.triangles_checked == 1
        assert report.violation_rate == 1.0

    def test_summary_is_human_readable(self):
        summary = check_coherence([]).summary()
        assert "logically impossible" in summary

    @pytest.mark.parametrize("bad", [
        {"id": 1, "fact_id_a": None, "fact_id_b": 2, "relation_type": "corroborates"},
        {"id": 2, "fact_id_a": 1, "fact_id_b": None, "relation_type": "corroborates"},
    ])
    def test_malformed_rows_are_skipped(self, bad):
        assert check_coherence([bad]).edges == 0


class TestUndeterminedBlame:
    """When nothing separates the three edges, saying "least confident"
    would dress an arbitrary pick up as a judgment."""

    def test_all_equal_confidence_and_no_numbers_is_reported_as_undetermined(self):
        rels = [
            _rel(1, 10, 20, "corroborates", confidence=1.0),
            _rel(2, 20, 30, "corroborates", confidence=1.0),
            _rel(3, 10, 30, "reconciled", confidence=1.0),
        ]
        violation = check_coherence(rels).violations[0]
        assert "undetermined" in violation.reason
        assert "all three need review" in violation.reason

    def test_numbers_still_win_over_a_confidence_tie(self):
        rels = [
            _rel(1, 10, 30, "corroborates", confidence=1.0),
            _rel(2, 10, 20, "corroborates", confidence=1.0),
            _rel(3, 20, 30, "reconciled", confidence=1.0),
        ]
        facts = {10: _fact(10, 100.0), 20: _fact(20, 100.0), 30: _fact(30, 250.0)}
        violation = check_coherence(rels, facts_by_id=facts).violations[0]
        assert violation.suspect["id"] == 1
        assert "deterministic comparison" in violation.reason
