"""Tests for the deterministic adjudicator -- the piece that decides the
FINAL relation_type in code, checking (and where conclusive, overriding)
the LLM's own proposal rather than trusting it outright.

Fixtures build real Comparison objects via the actual compare_values /
compare_periods / compare_scopes functions rather than hand-constructing
dataclasses, so these tests also catch a mismatch between what those
functions actually produce and what the adjudicator assumes.
"""
from app.adjudication import (
    DETERMINISTIC_CONFIRMED, DETERMINISTIC_OVERRIDE, LLM_UNCHECKED, adjudicate,
)
from app.context import compare_periods, compare_scopes
from app.normalize import compare_values


def _llm(relation_type, explanation="model reasoning", reconciliation_context=None, confidence=0.8):
    return {
        "relation_type": relation_type, "explanation": explanation,
        "reconciliation_context": reconciliation_context, "confidence": confidence,
    }


class TestRelatedButNotComparable:
    def test_incompatible_units_override_a_confident_llm_verdict(self):
        cmp = compare_values(5.6, "%", 404.0, "INR Crore")
        result = adjudicate(_llm("contradicts"), cmp, compare_periods(None, None),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.relation_type == "related_but_not_comparable"
        assert result.decision_source == DETERMINISTIC_OVERRIDE
        assert result.disagreement is True
        assert result.llm_proposal == "contradicts"

    def test_confirmed_when_the_model_already_said_so(self):
        cmp = compare_values(5.6, "%", 404.0, "INR Crore")
        result = adjudicate(_llm("related_but_not_comparable"), cmp, compare_periods(None, None),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.decision_source == DETERMINISTIC_CONFIRMED
        assert result.disagreement is False

    def test_a_missing_value_on_either_side_defers_to_the_llm_instead(self):
        """both_numeric=False means at least one fact isn't a numeric claim
        at all (a qualitative status) -- structurally different from two
        numbers that fail to reduce to a common unit, and must not be
        forced into related_but_not_comparable."""
        cmp = compare_values(None, None, 404.0, "INR Crore")
        result = adjudicate(_llm("corroborates"), cmp, compare_periods(None, None),
                             compare_scopes(None, None), None, both_numeric=False)
        assert result.relation_type == "corroborates"
        assert result.decision_source == LLM_UNCHECKED


class TestMagnitudeSuspectCapsAtUncertain:
    def test_a_confident_contradiction_is_overridden_to_uncertain(self):
        cmp = compare_values(100.0, "INR", 100.0, "INR million")  # 1e6x apart
        assert cmp.magnitude_suspect
        result = adjudicate(_llm("contradicts", confidence=0.95), cmp, compare_periods("FY24", "FY24"),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.relation_type == "uncertain"
        assert result.decision_source == DETERMINISTIC_OVERRIDE
        assert result.disagreement is True

    def test_confirmed_when_the_model_already_said_uncertain(self):
        cmp = compare_values(100.0, "INR", 100.0, "INR million")
        result = adjudicate(_llm("uncertain"), cmp, compare_periods(None, None),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.decision_source == DETERMINISTIC_CONFIRMED


class TestEvidenceCaveatCapsStrongOutcomes:
    def test_a_confident_corroboration_is_capped_when_evidence_is_unverified(self):
        cmp = compare_values(100.0, "INR million", 100.0, "INR million")
        assert cmp.agree
        result = adjudicate(
            _llm("corroborates"), cmp, compare_periods("FY24", "FY24"), compare_scopes(None, None),
            evidence_caveat="CAVEAT: Fact A's value is not itself evidenced", both_numeric=True,
        )
        assert result.relation_type == "uncertain"
        assert result.decision_source == DETERMINISTIC_OVERRIDE

    def test_a_confident_contradiction_is_also_capped(self):
        cmp = compare_values(100.0, "INR million", 200.0, "INR million")
        result = adjudicate(
            _llm("contradicts"), cmp, compare_periods("FY24", "FY24"), compare_scopes(None, None),
            evidence_caveat="CAVEAT: unverified", both_numeric=True,
        )
        assert result.relation_type == "uncertain"
        assert result.decision_source == DETERMINISTIC_OVERRIDE

    def test_an_already_hedged_proposal_is_left_alone(self):
        """The caveat rule only intervenes on a confident corroborates/
        contradicts; a model that already said reconciled or uncertain
        isn't further second-guessed by this specific rule (rules 4-7
        can't fire anyway once evidence_caveat makes reliable_agree None)."""
        cmp = compare_values(100.0, "INR million", 200.0, "INR million")
        result = adjudicate(
            _llm("reconciled", reconciliation_context="different periods"), cmp,
            compare_periods("FY24", "FY24"), compare_scopes(None, None),
            evidence_caveat="CAVEAT: unverified", both_numeric=True,
        )
        assert result.relation_type == "reconciled"
        assert result.decision_source == LLM_UNCHECKED


class TestCorroboration:
    def test_agreeing_values_override_a_wrong_llm_proposal(self):
        cmp = compare_values(8142.0, "INR Crore", 81415.38, "INR million")  # agree ~0.006%
        assert cmp.agree
        result = adjudicate(_llm("contradicts"), cmp, compare_periods("FY24", "FY2023-24"),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.relation_type == "corroborates"
        assert result.decision_source == DETERMINISTIC_OVERRIDE
        assert result.confidence == 1.0

    def test_confirmed_when_the_model_agrees(self):
        cmp = compare_values(8142.0, "INR Crore", 81415.38, "INR million")
        result = adjudicate(_llm("corroborates"), cmp, compare_periods("FY24", "FY2023-24"),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.decision_source == DETERMINISTIC_CONFIRMED
        assert result.disagreement is False

    def test_unknown_period_does_not_block_corroboration(self):
        """Agreement across an unresolved period is still meaningful --
        unlike a mismatch, it isn't ambiguous the same way (see rule 7)."""
        cmp = compare_values(100.0, "INR million", 100.0, "INR million")
        result = adjudicate(_llm("uncertain"), cmp, compare_periods(None, None),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.relation_type == "corroborates"

    def test_overlapping_period_does_not_auto_corroborate(self):
        """A quarter coincidentally matching its own year's total is not
        confident evidence of corroboration -- deferred to the model."""
        cmp = compare_values(100.0, "INR million", 100.0, "INR million")
        result = adjudicate(_llm("uncertain"), cmp, compare_periods("Q1 FY25", "FY25"),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.decision_source == LLM_UNCHECKED


class TestContradiction:
    def test_same_period_same_scope_overrides_a_reconciled_proposal(self):
        """The bold rule: 'context is equivalent' has been VERIFIED, not
        assumed, so a stated reconciliation is overridden."""
        cmp = compare_values(36.82, "%", 43.26, "%")
        assert cmp.agree is False
        result = adjudicate(
            _llm("reconciled", reconciliation_context="a made-up reason"), cmp,
            compare_periods("FY24", "FY24"), compare_scopes("consolidated", "consolidated"),
            None, both_numeric=True,
        )
        assert result.relation_type == "contradicts"
        assert result.decision_source == DETERMINISTIC_OVERRIDE
        assert result.disagreement is True
        assert "reconciled" in result.explanation.lower()

    def test_confirmed_when_the_model_already_said_contradicts(self):
        cmp = compare_values(36.82, "%", 43.26, "%")
        result = adjudicate(_llm("contradicts"), cmp, compare_periods("FY24", "FY24"),
                             compare_scopes("consolidated", "consolidated"), None, both_numeric=True)
        assert result.decision_source == DETERMINISTIC_CONFIRMED

    def test_same_period_unknown_scope_still_contradicts_only_if_scope_is_same(self):
        """Scope UNKNOWN (not confirmed same) must NOT trigger the bold
        override -- that's rule 7's job (insufficient_context), not rule 5's."""
        cmp = compare_values(36.82, "%", 43.26, "%")
        result = adjudicate(_llm("uncertain"), cmp, compare_periods("FY24", "FY24"),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.relation_type == "insufficient_context"


class TestReconciliation:
    def test_different_period_overrides_a_contradicts_proposal(self):
        """The example this rule exists for: FY23=36.82%, FY24=43.26% must
        not be forced to contradicts when the context engine knows the
        periods differ."""
        cmp = compare_values(36.82, "%", 43.26, "%")
        result = adjudicate(_llm("contradicts", confidence=0.9), cmp,
                             compare_periods("FY23", "FY24"), compare_scopes(None, None),
                             None, both_numeric=True)
        assert result.relation_type == "reconciled"
        assert result.decision_source == DETERMINISTIC_OVERRIDE
        assert "different periods" in result.reconciliation_context

    def test_the_models_own_reconciliation_context_is_preferred_when_given(self):
        cmp = compare_values(36.82, "%", 43.26, "%")
        result = adjudicate(
            _llm("reconciled", reconciliation_context="restated after a segment realignment"),
            cmp, compare_periods("FY23", "FY24"), compare_scopes(None, None), None, both_numeric=True,
        )
        assert result.reconciliation_context == "restated after a segment realignment"

    def test_a_confirmed_scope_difference_also_reconciles(self):
        cmp = compare_values(74540.82, "INR million", 81415.38, "INR million")
        result = adjudicate(_llm("contradicts"), cmp, compare_periods("FY24", "FY24"),
                             compare_scopes("standalone", "consolidated"), None, both_numeric=True)
        assert result.relation_type == "reconciled"
        assert "scope" in result.reconciliation_context.lower() or "consolidated" in result.reconciliation_context.lower()

    def test_a_quarter_inside_its_year_also_reconciles_not_contradicts(self):
        cmp = compare_values(2000.0, "INR million", 8142.0, "INR million")
        result = adjudicate(_llm("contradicts"), cmp, compare_periods("Q1 FY25", "FY25"),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.relation_type == "reconciled"


class TestInsufficientContext:
    def test_unknown_period_with_differing_values_is_insufficient_not_uncertain_label_confusion(self):
        cmp = compare_values(85466.74, "INR million", 91446.46, "INR million")
        result = adjudicate(_llm("contradicts", confidence=0.9), cmp,
                             compare_periods(None, None), compare_scopes(None, None),
                             None, both_numeric=True)
        assert result.relation_type == "insufficient_context"
        assert result.decision_source == DETERMINISTIC_OVERRIDE

    def test_unknown_scope_with_differing_values_is_insufficient(self):
        cmp = compare_values(85466.74, "INR million", 91446.46, "INR million")
        result = adjudicate(_llm("reconciled"), cmp, compare_periods("FY24", "FY24"),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.relation_type == "insufficient_context"

    def test_overlapping_scope_with_differing_values_is_insufficient_not_reconciled(self):
        """A partial token overlap (see app/context.py OVERLAPPING) is a
        weaker signal than a clean contrast pair -- not confident enough
        to assert a reconciling scope difference."""
        cmp = compare_values(100.0, "INR million", 150.0, "INR million")
        result = adjudicate(_llm("reconciled"), cmp, compare_periods("FY24", "FY24"),
                             compare_scopes("segment: north", "segment: south"), None, both_numeric=True)
        assert result.relation_type == "insufficient_context"

    def test_confirmed_when_the_model_already_said_uncertain(self):
        cmp = compare_values(85466.74, "INR million", 91446.46, "INR million")
        result = adjudicate(_llm("uncertain"), cmp, compare_periods(None, None),
                             compare_scopes(None, None), None, both_numeric=True)
        assert result.decision_source == DETERMINISTIC_CONFIRMED


class TestDeferralToTheModel:
    def test_qualitative_facts_are_fully_deferred(self):
        cmp = compare_values(None, None, None, None)
        result = adjudicate(_llm("corroborates", explanation="both assert the same status"),
                             cmp, compare_periods(None, None), compare_scopes(None, None),
                             None, both_numeric=False)
        assert result.relation_type == "corroborates"
        assert result.decision_source == LLM_UNCHECKED
        assert result.disagreement is False
        assert result.confidence == 0.8  # the model's own stated confidence, unmodified

    def test_a_missing_llm_relation_type_defaults_to_uncertain_not_a_crash(self):
        cmp = compare_values(None, None, None, None)
        result = adjudicate({"explanation": "", "confidence": None}, cmp,
                             compare_periods(None, None), compare_scopes(None, None),
                             None, both_numeric=False)
        assert result.relation_type == "uncertain"
        assert result.llm_proposal is None
        assert result.disagreement is False  # nothing to disagree with


class TestTraceContents:
    def test_checks_dict_carries_the_deterministic_inputs(self):
        cmp = compare_values(8142.0, "INR Crore", 81415.38, "INR million")
        period = compare_periods("FY24", "FY2023-24")
        scope = compare_scopes("consolidated", "group")
        result = adjudicate(_llm("corroborates"), cmp, period, scope, None, both_numeric=True,
                             slice_a="whole", slice_b="whole")
        assert result.checks["period_relation"] == "same"
        assert result.checks["scope_relation"] == "same"
        assert result.checks["scope_dimension"] == "scope"
        assert result.checks["slice_a"] == "whole"
        assert result.checks["slice_b"] == "whole"
        assert result.checks["diff_pct"] == cmp.diff_pct

    def test_llm_proposal_and_explanation_survive_an_override(self):
        cmp = compare_values(36.82, "%", 43.26, "%")
        result = adjudicate(_llm("contradicts", explanation="the numbers just disagree"), cmp,
                             compare_periods("FY23", "FY24"), compare_scopes(None, None), None,
                             both_numeric=True)
        assert result.llm_proposal == "contradicts"
        assert result.llm_explanation == "the numbers just disagree"
