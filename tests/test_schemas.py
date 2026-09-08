"""Tests for the pydantic validation boundary between LLM output and
the rest of the pipeline (app/schemas.py).

Every fact, metric-match judgment and relationship judgment this project
stores originates as untrusted JSON from a model. These schemas are the
one place that JSON is checked before anything downstream (the database,
the deterministic adjudicator, the UI) gets to assume a shape -- so this
file tests the boundary directly, rather than only indirectly through
app/relationships.py's and app/fact_extraction.py's own tests.
"""
import pytest
from pydantic import ValidationError

from app.schemas import ExtractedFact, MetricMatch, RelationJudgment, validate_facts


class TestExtractedFactRequiredFields:
    def test_a_quote_is_required(self):
        with pytest.raises(ValidationError):
            ExtractedFact(statement="Revenue was 100")

    def test_a_statement_is_required(self):
        with pytest.raises(ValidationError):
            ExtractedFact(quote="Revenue was 100")

    def test_a_null_quote_is_rejected_not_coerced_to_empty(self):
        with pytest.raises(ValidationError):
            ExtractedFact(statement="s", quote=None)

    def test_an_empty_quote_is_rejected(self):
        """A fact with no evidence is exactly what this project exists not
        to store."""
        with pytest.raises(ValidationError):
            ExtractedFact(statement="s", quote="   ")

    def test_the_minimal_valid_fact_is_accepted(self):
        f = ExtractedFact(statement="Revenue was 100 million", quote="Revenue was 100 million")
        assert f.statement == "Revenue was 100 million"
        assert f.subject is None and f.value_numeric is None


class TestExtractedFactOptionalFieldCleaning:
    @pytest.mark.parametrize("nullish", ["null", "None", "N/A", "na", "-", "", "  "])
    def test_nullish_strings_become_none(self, nullish):
        f = ExtractedFact(statement="s", quote="q", subject=nullish, unit=nullish, scope=nullish)
        assert f.subject is None and f.unit is None and f.scope is None

    def test_real_optional_values_are_stripped_and_kept(self):
        f = ExtractedFact(statement="s", quote="q", subject="  Delhivery Limited  ")
        assert f.subject == "Delhivery Limited"

    def test_non_string_optional_values_are_stringified(self):
        """A model occasionally returns a bare number for a field the
        prompt describes as text (e.g. time_period: 2024) -- coerced to a
        string rather than rejected, since the field genuinely has a
        value, just not in the expected type."""
        f = ExtractedFact(statement="s", quote="q", time_period=2024)
        assert f.time_period == "2024"


class TestExtractedFactValueNumericCoercion:
    def test_a_locale_formatted_string_is_parsed(self):
        """Routed through parse_locale_number, not a bare float() cast --
        see app/normalize.py for why a plain comma-strip misreads a
        European-formatted number."""
        f = ExtractedFact(statement="s", quote="q", value_numeric="1.234,56")
        assert f.value_numeric == pytest.approx(1234.56)

    def test_a_real_number_passes_through(self):
        f = ExtractedFact(statement="s", quote="q", value_numeric=81415.38)
        assert f.value_numeric == 81415.38

    def test_an_unparseable_string_becomes_none_not_an_error(self):
        """fact_extraction.py falls back to parsing `value` when this is
        None -- a bad value_numeric must not fail the whole fact."""
        f = ExtractedFact(statement="s", quote="q", value_numeric="not a number")
        assert f.value_numeric is None

    def test_nullish_value_numeric_becomes_none(self):
        f = ExtractedFact(statement="s", quote="q", value_numeric="null")
        assert f.value_numeric is None


class TestExtractedFactConfidence:
    """Same policy as RelationJudgment.confidence -- see
    TestRelationJudgmentConfidence below for why clamping was replaced
    with rejection."""

    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
    def test_in_range_values_pass_through(self, value):
        assert ExtractedFact(statement="s", quote="q", confidence=value).confidence == value

    @pytest.mark.parametrize("value", [-0.1, 1.5, 7.5, 100])
    def test_out_of_range_values_become_none(self, value):
        assert ExtractedFact(statement="s", quote="q", confidence=value).confidence is None

    def test_a_non_numeric_confidence_becomes_none(self):
        assert ExtractedFact(statement="s", quote="q", confidence="high").confidence is None


class TestMetricMatch:
    def test_the_minimal_valid_response_is_accepted(self):
        m = MetricMatch(same_metric=True)
        assert m.same_metric is True and m.reason == "" and m.slice_a is None

    @pytest.mark.parametrize("value,expected", [
        (True, True), (False, False), ("true", True), ("YES", True), ("1", True),
        ("false", False), ("no", False), ("0", False), (1, True), (0, False),
    ])
    def test_same_metric_accepts_common_boolean_spellings(self, value, expected):
        assert MetricMatch(same_metric=value).same_metric is expected

    def test_an_uninterpretable_same_metric_value_is_rejected(self):
        """The system is allowed to not know -- but this field asserting
        SOMETHING that isn't yes or no must not silently become False."""
        with pytest.raises(ValidationError):
            MetricMatch(same_metric="maybe")

    def test_slices_are_preserved(self):
        """Regression test for a real bug: SYSTEM_PROMPT_METRIC asks the
        model to fill slice_a/slice_b FIRST, but the schema used to have
        no fields for them at all, so pydantic silently dropped the
        model's own reasoning on the way through."""
        m = MetricMatch(same_metric=True, slice_a="whole", slice_b="cross border", reason="r")
        assert m.slice_a == "whole" and m.slice_b == "cross border"

    def test_nullish_slices_become_none(self):
        m = MetricMatch(same_metric=True, slice_a="null", slice_b="")
        assert m.slice_a is None and m.slice_b is None

    def test_nullish_reason_becomes_empty_string_not_none(self):
        """reason has a non-Optional str type -- None must resolve to ''
        rather than fail validation."""
        m = MetricMatch(same_metric=True, reason=None)
        assert m.reason == ""


class TestRelationJudgmentRelationTypeAliasing:
    """Local models drift on enum wording -- this is the layer that
    absorbs it so app/adjudication.py always sees one of the five real
    values."""

    @pytest.mark.parametrize("raw,expected", [
        ("corroborates", "corroborates"), ("CORROBORATES", "corroborates"),
        ("corroborate", "corroborates"), ("corroboration", "corroborates"),
        ("agrees", "corroborates"),
        ("contradicts", "contradicts"), ("CONTRADICTION", "contradicts"),
        ("conflicts", "contradicts"),
        ("reconciled", "reconciled"), ("contextually_reconciled", "reconciled"),
        ("contextual reconciliation", "reconciled"),
        ("unrelated", "unrelated"), ("not_related", "unrelated"), ("none", "unrelated"),
        ("uncertain", "uncertain"), ("unknown", "uncertain"), ("insufficient_evidence", "uncertain"),
    ])
    def test_known_aliases_normalise_to_a_real_type(self, raw, expected):
        assert RelationJudgment(relation_type=raw).relation_type == expected

    def test_a_genuinely_unrecognised_value_becomes_uncertain(self):
        """Never coerced into a real relationship the model didn't
        actually assert -- "the system doesn't know" is a real answer."""
        assert RelationJudgment(relation_type="sort_of_agrees_i_guess").relation_type == "uncertain"

    def test_a_null_relation_type_becomes_uncertain(self):
        assert RelationJudgment(relation_type=None).relation_type == "uncertain"


class TestRelationJudgmentConfidence:
    """A confidence outside 0..1 is a model error. This used to be
    clamped to the nearest boundary (7.5 -> 1.0), which converts a
    demonstrated misunderstanding of the scale into the MOST confident
    possible value -- backwards. Rejecting to None instead matches how
    every other unparseable/out-of-range signal in this project is
    handled (refuse rather than guess)."""

    def test_a_value_above_one_becomes_none(self):
        assert RelationJudgment(relation_type="corroborates", confidence=7.5).confidence is None

    def test_a_negative_value_becomes_none(self):
        assert RelationJudgment(relation_type="corroborates", confidence=-0.3).confidence is None

    def test_a_valid_value_passes_through_unchanged(self):
        assert RelationJudgment(relation_type="corroborates", confidence=0.85).confidence == 0.85

    def test_boundary_values_are_accepted(self):
        assert RelationJudgment(relation_type="corroborates", confidence=0.0).confidence == 0.0
        assert RelationJudgment(relation_type="corroborates", confidence=1.0).confidence == 1.0


class TestRelationJudgmentTextFields:
    def test_nullish_explanation_becomes_empty_string(self):
        assert RelationJudgment(relation_type="uncertain", explanation="null").explanation == ""

    def test_nullish_reconciliation_context_becomes_none(self):
        assert RelationJudgment(relation_type="reconciled", reconciliation_context="N/A").reconciliation_context is None

    def test_a_real_reconciliation_context_is_stripped_and_kept(self):
        j = RelationJudgment(relation_type="reconciled", reconciliation_context="  different scopes  ")
        assert j.reconciliation_context == "different scopes"


class TestValidateFacts:
    def _valid_fact(self, **over):
        base = {"statement": "Revenue was 100 million", "quote": "Revenue was 100 million"}
        base.update(over)
        return base

    def test_a_valid_fact_passes_through(self):
        facts, issues = validate_facts([self._valid_fact()])
        assert len(facts) == 1 and issues == []

    def test_a_non_dict_item_becomes_an_issue_not_a_crash(self):
        facts, issues = validate_facts(["just a string", 42, None])
        assert facts == []
        assert len(issues) == 3
        assert all(i["issue_type"] == "malformed_fact" for i in issues)

    def test_a_fact_missing_its_quote_becomes_an_issue(self):
        facts, issues = validate_facts([{"statement": "Revenue was 100"}])
        assert facts == []
        assert len(issues) == 1
        assert issues[0]["issue_type"] == "malformed_fact"

    def test_a_mixed_batch_keeps_the_good_ones_and_reports_the_bad(self):
        raw = [self._valid_fact(subject="A"), {"statement": "no quote here"}, self._valid_fact(subject="B")]
        facts, issues = validate_facts(raw)
        assert len(facts) == 2 and len(issues) == 1
        assert {f["subject"] for f in facts} == {"A", "B"}

    def test_an_empty_batch_is_safe(self):
        assert validate_facts([]) == ([], [])

    def test_the_issue_carries_a_raw_excerpt_for_debugging(self):
        _facts, issues = validate_facts([{"unexpected": "shape"}])
        assert issues[0]["raw_excerpt"]
