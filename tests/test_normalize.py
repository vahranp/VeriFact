"""Unit tests for the deterministic value/unit normalization layer.

These are the tests that matter most for the project's core claim: that
unit conversion is done in code, not left to an LLM's arithmetic. They
run in milliseconds and need no Ollama server.
"""
import pytest

from app.normalize import (
    Comparison,
    compare_values,
    format_comparison_for_prompt,
    is_percent_unit,
    normalize_unit,
)


class TestNormalizeUnit:
    @pytest.mark.parametrize("value,unit,expected_value", [
        (8142, "Cr", 8142 * 1e7),
        (8142, "INR Crore", 8142 * 1e7),
        (5, "lakh", 5 * 1e5),
        (81415.38, "INR million", 81415.38 * 1e6),
        (2.5, "billion", 2.5 * 1e9),
        (40, "thousand", 40 * 1e3),
        (1.4, "Mn Tons", 1.4 * 1e6),
    ])
    def test_scale_words_apply(self, value, unit, expected_value):
        result = normalize_unit(value, unit)
        assert result is not None
        assert result.value == pytest.approx(expected_value)

    def test_currency_spellings_fold_to_one_base(self):
        """'INR million', 'Rs million' and '₹ million' must be comparable
        to each other -- otherwise the same figure written three ways in
        three documents would never be compared."""
        bases = {normalize_unit(1, u).base_unit for u in ["INR million", "Rs million", "₹ million"]}
        assert len(bases) == 1

    def test_unparseable_unit_returns_none_rather_than_guessing(self):
        assert normalize_unit(5, None) is None
        assert normalize_unit(None, "INR million") is None
        assert normalize_unit(5, "") is None

    def test_percent_is_not_scaled(self):
        result = normalize_unit(12.7, "%")
        assert result.value == pytest.approx(12.7)
        assert result.base_unit == "%"

    def test_basis_points_convert_to_percentage_points(self):
        result = normalize_unit(50, "bps")
        assert result.value == pytest.approx(0.5)
        assert result.base_unit == "%"

    def test_non_currency_units_keep_their_descriptor(self):
        assert normalize_unit(750000, "sq ft").base_unit == "sq ft"
        assert normalize_unit(1.4, "Mn Tons").base_unit == "tons"


class TestIsPercentUnit:
    @pytest.mark.parametrize("unit", ["%", "percent", "percentage", "pct", "12%"])
    def test_percent_variants(self, unit):
        assert is_percent_unit(unit)

    @pytest.mark.parametrize("unit", ["INR million", "sq ft", "", None])
    def test_non_percent(self, unit):
        assert not is_percent_unit(unit)


class TestCompareValues:
    def test_crore_vs_million_agree(self):
        """The real Case 1 pair: ₹8,142 Cr vs ₹81,415.38 million is the
        same figure in two units and must compare as agreeing."""
        cmp = compare_values(8142, "INR Crore", 81415.38, "INR million")
        assert cmp.comparable
        assert cmp.agree
        assert cmp.diff_pct < 0.1

    def test_real_disagreement_is_detected(self):
        """The real Case 2 pair: net worth vs total equity, same unit,
        ~6.5% apart -- must NOT be reported as agreeing."""
        cmp = compare_values(85466.74, "INR million", 91446.46, "INR million")
        assert cmp.comparable
        assert cmp.agree is False
        assert cmp.diff_pct == pytest.approx(6.539, abs=0.01)

    def test_percent_vs_absolute_refuses_to_compare(self):
        """An EBITDA margin in % and an absolute amount in Cr are both
        numbers, but their difference is meaningless -- refusing is the
        only correct behavior."""
        cmp = compare_values(5.6, "%", 404, "INR Crore")
        assert not cmp.comparable
        assert "same base" in cmp.reason

    def test_different_physical_quantities_refuse_to_compare(self):
        cmp = compare_values(750000, "sq ft", 1.4, "Mn Tons")
        assert not cmp.comparable

    def test_missing_numeric_value_refuses_to_compare(self):
        cmp = compare_values(None, "INR million", 5, "INR million")
        assert not cmp.comparable
        assert "numeric value" in cmp.reason

    def test_identical_values_agree_exactly(self):
        cmp = compare_values(100, "INR million", 100, "INR million")
        assert cmp.comparable and cmp.agree
        assert cmp.diff_pct == 0.0

    def test_tolerance_boundary_is_respected(self):
        # 1% apart, default tolerance 2% -> agree
        assert compare_values(100, "million", 101, "million").agree
        # 5% apart -> disagree
        assert compare_values(100, "million", 105, "million").agree is False
        # explicit tighter tolerance flips the 1% case
        assert compare_values(100, "million", 101, "million", tolerance_pct=0.5).agree is False

    def test_negative_values_compare_correctly(self):
        cmp = compare_values(-452, "INR Crore", -452, "INR Crore")
        assert cmp.comparable and cmp.agree

    def test_orders_of_magnitude_gap_is_flagged_as_suspect(self):
        """Real observed case: one fact's unit was extracted as 'INR' where
        the source table was denominated in 'INR million'. Comparing them
        literally yields a ~99.9% 'disagreement' -- a contradiction verdict
        that would be right by accident and wrong in reasoning."""
        cmp = compare_values(85466.74, "INR million", 91446.46, "INR")
        assert cmp.comparable
        assert cmp.magnitude_suspect is True
        assert "unit is incomplete" in cmp.reason

    def test_normal_disagreement_is_not_flagged_suspect(self):
        cmp = compare_values(85466.74, "INR million", 91446.46, "INR million")
        assert cmp.comparable
        assert cmp.magnitude_suspect is False
        assert cmp.agree is False

    def test_suspect_comparison_warns_the_model_not_to_call_it_a_contradiction(self):
        line = format_comparison_for_prompt(compare_values(85466.74, "INR million", 91446.46, "INR"))
        assert "UNRELIABLE" in line
        assert "Do NOT treat this as evidence of a contradiction" in line

    def test_bare_scaled_numbers_are_flagged_as_such(self):
        """Two bare numbers with only a scale word (no currency/quantity
        descriptor) are numerically comparable but carry no semantic
        signal -- the reason string must say so, since the caller relies
        on a separate same-metric check to decide if comparing is valid."""
        cmp = compare_values(1, "Cr", 1, "Cr")
        assert cmp.comparable
        assert "bare scaled numbers" in cmp.reason


class TestFormatComparisonForPrompt:
    def test_incomparable_renders_reason(self):
        line = format_comparison_for_prompt(Comparison(False, "some reason"))
        assert "not possible" in line and "some reason" in line

    def test_agreement_renders_values_and_diff(self):
        line = format_comparison_for_prompt(compare_values(8142, "INR Crore", 81415.38, "INR million"))
        assert "agree" in line and "%" in line

    def test_disagreement_is_stated_emphatically(self):
        line = format_comparison_for_prompt(compare_values(85466.74, "INR million", 91446.46, "INR million"))
        assert "DISAGREE" in line


class TestCurrencyAliasBoundaries:
    """Currency aliases must not match inside longer words.

    Real bug: "rs" -> "inr" was applied with a trailing word boundary only,
    so any unit whose name ends in "rs" was silently corrupted --
    "hours" became "houinr", "years" became "yeainr", "workers" became
    "workeinr". Two facts sharing such a unit still compared equal (both
    were corrupted identically), which is why it survived unnoticed; but
    the base unit shown in explanations was garbage, and a singular/plural
    pair like "hour" vs "hours" stopped reducing to the same base.
    """

    import pytest as _pytest

    @_pytest.mark.parametrize("unit", [
        "hours", "years", "cars", "workers", "liters", "meters",
        "containers", "gears", "sensors", "tons",
    ])
    def test_non_currency_units_are_left_alone(self, unit):
        from app.normalize import normalize_unit
        assert normalize_unit(1.0, unit).base_unit == unit

    @_pytest.mark.parametrize("unit,expected", [
        ("rs", "inr"), ("Rs.", "inr"), ("INR", "inr"), ("rupees", "inr"),
        ("USD", "usd"), ("dollars", "usd"),
    ])
    def test_real_currency_aliases_still_fold(self, unit, expected):
        from app.normalize import normalize_unit
        assert normalize_unit(1.0, unit).base_unit == expected

    def test_currency_with_scale_word_still_works(self):
        from app.normalize import normalize_unit
        n = normalize_unit(2.0, "Rs. crore")
        assert n.base_unit == "inr" and n.value == 2.0 * 1e7

    def test_the_case_1_comparison_is_unaffected(self):
        """The headline corroboration must survive this fix."""
        from app.normalize import compare_values
        cmp = compare_values(8142.0, "INR Crore", 81415.38, "INR million")
        assert cmp.comparable and cmp.agree
        assert cmp.diff_pct < 0.01
