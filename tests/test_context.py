"""Tests for temporal and scope normalization.

The stake here is the difference between a contradiction and a reconcilable
difference. Getting "different period" wrong in one direction invents a
conflict; getting it wrong in the other explains a real conflict away. The
second is worse, which is why unparseable periods must report UNKNOWN
rather than being guessed into a year.
"""
import pytest

from app.context import (
    DIFFERENT, OVERLAPPING, SAME, UNKNOWN,
    compare_periods, compare_scopes, format_context_for_prompt, parse_period,
)


class TestPeriodParsing:
    @pytest.mark.parametrize("text,year", [
        ("FY24", 2024), ("FY2024", 2024), ("fy 24", 2024),
        ("fiscal 2024", 2024), ("fiscal year 2024", 2024),
        ("FY2023-24", 2024), ("FY23-24", 2024), ("2023-2024", 2024),
    ])
    def test_fiscal_year_forms_resolve_to_the_ending_year(self, text, year):
        """A span is conventionally named for the year it ends in."""
        assert parse_period(text).year == year

    def test_en_dash_ranges_parse(self):
        assert parse_period("2023–2024").year == 2024

    @pytest.mark.parametrize("text,q,year", [
        ("Q1 FY25", 1, 2025), ("Q4 2024", 4, 2024), ("q3 fy2023", 3, 2023),
    ])
    def test_quarters_parse(self, text, q, year):
        p = parse_period(text)
        assert p.quarter == q and p.year == year

    @pytest.mark.parametrize("text", [
        "as of March 31, 2024", "as at 31 March 2024", "year ended 31 March 2024",
    ])
    def test_as_of_dates_parse(self, text):
        p = parse_period(text)
        assert (p.year, p.month, p.day) == (2024, 3, 31)

    def test_a_bare_calendar_year_parses(self):
        p = parse_period("2024")
        assert p.kind == "calendar_year" and p.year == 2024


class TestUnknownIsARealAnswer:
    """Inventing precision is worse than admitting the gap: a wrong
    'different period' turns a genuine contradiction into a false
    reconciliation, so the system explains away a real conflict."""

    @pytest.mark.parametrize("text", ["previous year", "current period", "prior year", "YTD"])
    def test_relative_periods_are_not_guessed(self, text):
        assert parse_period(text).kind == "relative"

    @pytest.mark.parametrize("text", [None, "", "   ", "sometime", "the period"])
    def test_unparseable_periods_return_none(self, text):
        assert parse_period(text) is None

    def test_comparing_against_a_relative_period_is_unknown(self):
        assert compare_periods("FY24", "previous year").relation == UNKNOWN

    def test_a_missing_period_is_unknown_not_different(self):
        assert compare_periods("FY24", None).relation == UNKNOWN

    def test_unknown_is_not_distinguishing(self):
        """UNKNOWN must never be used as evidence the facts differ."""
        assert compare_periods("FY24", None).distinguishing is False


class TestPeriodComparison:
    def test_the_same_period_written_two_ways_is_same(self):
        """The case this module exists for: a model comparing these
        strings has no reason to know they are one period."""
        assert compare_periods("FY24", "FY2023-24").relation == SAME

    def test_different_years_are_different(self):
        result = compare_periods("FY24", "FY23")
        assert result.relation == DIFFERENT and result.distinguishing

    def test_a_quarter_inside_a_year_overlaps_rather_than_differs(self):
        """Q1 is not expected to equal the annual figure, but they are not
        contradictory either."""
        assert compare_periods("Q1 FY25", "FY25").relation == OVERLAPPING

    def test_two_different_quarters_are_different(self):
        assert compare_periods("Q1 FY25", "Q3 FY25").relation == DIFFERENT

    def test_the_same_quarter_is_same(self):
        assert compare_periods("Q1 FY25", "Q1 2025").relation == SAME

    def test_fiscal_and_calendar_years_of_one_number_overlap(self):
        assert compare_periods("FY24", "2024").relation == OVERLAPPING

    def test_identical_as_of_dates_are_same(self):
        assert compare_periods("as of March 31, 2024", "as at 31 March 2024").relation == SAME

    def test_different_as_of_dates_are_different(self):
        assert compare_periods("as of March 31, 2024", "as of June 30, 2024").relation == DIFFERENT

    def test_overlapping_is_not_distinguishing(self):
        """Overlap is not evidence the facts describe different things."""
        assert compare_periods("Q1 FY25", "FY25").distinguishing is False


class TestScopeComparison:
    def test_contrasting_scopes_are_different(self):
        result = compare_scopes("consolidated", "standalone")
        assert result.relation == DIFFERENT and result.distinguishing

    def test_synonymous_scopes_are_the_same(self):
        """'Consolidated' and 'group' name one scope."""
        assert compare_scopes("consolidated", "group").relation == SAME

    def test_identical_scopes_are_the_same(self):
        assert compare_scopes("consolidated", "consolidated").relation == SAME

    @pytest.mark.parametrize("a,b", [("gross", "net"), ("continuing", "discontinued"),
                                      ("actual", "forecast"), ("annual", "quarterly")])
    def test_other_contrast_pairs(self, a, b):
        assert compare_scopes(a, b).relation == DIFFERENT

    def test_a_missing_scope_is_unknown(self):
        assert compare_scopes("consolidated", None).relation == UNKNOWN

    def test_partially_shared_scopes_overlap(self):
        assert compare_scopes("segment: north", "segment: south").relation == OVERLAPPING

    def test_an_unfamiliar_qualifier_is_still_compared(self):
        """No accounting ontology -- an unknown domain's vocabulary is
        compared as plain tokens rather than dropped."""
        assert compare_scopes("larval stage", "adult stage").relation == OVERLAPPING
        assert compare_scopes("larval", "adult").relation == DIFFERENT


class TestPromptFormatting:
    """Context explains a *difference*. When the values agree there is no
    difference to explain, and saying anything about explaining one is
    worse than saying nothing -- that caused a real regression, where a
    clean corroboration (values agreeing to 0.006%) came back classified
    as a contradiction because the block told the model a difference
    "would not be explained by context"."""

    def test_agreeing_values_are_not_told_a_gap_is_unexplained(self):
        block = format_context_for_prompt(
            compare_periods("FY24", "FY2023-24"), compare_scopes(None, None),
            values_agree=True)
        assert "no discrepancy for context to explain" in block
        assert "not explained by context" not in block

    def test_a_context_difference_tells_the_model_a_gap_is_expected(self):
        block = format_context_for_prompt(
            compare_periods("FY24", "FY23"), compare_scopes(None, None),
            values_agree=False)
        assert "EXPECTED" in block
        assert "not by itself evidence of a contradiction" in block

    def test_matching_context_tells_the_model_a_gap_is_unexplained(self):
        block = format_context_for_prompt(
            compare_periods("FY24", "FY2023-24"), compare_scopes("consolidated", "consolidated"),
            values_agree=False)
        assert "not explained by context" in block

    def test_no_numeric_comparison_means_no_guidance_is_asserted(self):
        """Without a usable comparison the model must judge agreement from
        the statements, so promising it anything about differences would
        assert more than is known."""
        block = format_context_for_prompt(
            compare_periods("FY24", "FY24"), compare_scopes(None, None), values_agree=None)
        assert "EXPECTED" not in block and "explain" not in block

    def test_both_determinations_always_appear(self):
        block = format_context_for_prompt(
            compare_periods(None, None), compare_scopes(None, None))
        assert "Reporting period:" in block and "Scope:" in block


class TestDomainNeutrality:
    def test_a_non_financial_reporting_period_works(self):
        """Fiscal years are used by governments, universities and
        non-profits -- nothing here is finance-specific."""
        assert compare_periods("academic year 2023-2024", "FY2024").relation == SAME

    def test_a_non_financial_scope_contrast_works(self):
        assert compare_scopes("estimated", "actual").relation == DIFFERENT


class TestHalfYearParsing:
    """Real gap found in a generalization audit: H1/H2 were not parsed at
    all, so both halves of a fiscal year collapsed to just their shared
    year and compared as SAME -- the exact failure this module exists to
    prevent for quarters, one granularity coarser. A seasonal business's
    H1 and H2 can legitimately differ a great deal; without this, that
    difference would have read as an unexplained (and false) contradiction.
    """

    @pytest.mark.parametrize("text,half,year", [
        ("H1 2024", 1, 2024), ("H2 2024", 2, 2024),
        ("H1 FY24", 1, 2024), ("H2FY2024", 2, 2024),
        ("1H24", 1, 2024), ("2H2024", 2, 2024), ("2HFY25", 2, 2025),
        ("HY1 2024", 1, 2024), ("HY2 FY24", 2, 2024),
    ])
    def test_half_year_forms_parse(self, text, half, year):
        p = parse_period(text)
        assert (p.half, p.year) == (half, year)

    @pytest.mark.parametrize("text,half", [
        ("first half of 2024", 1), ("second half of 2024", 2),
        ("1st half 2024", 1), ("2nd half 2024", 2),
    ])
    def test_half_year_word_forms_parse(self, text, half):
        p = parse_period(text)
        assert p.half == half and p.year == 2024


class TestHalfYearComparison:
    def test_the_same_half_written_two_ways_is_same(self):
        assert compare_periods("H1 2024", "1H FY24").relation == SAME

    def test_different_halves_of_one_year_are_different(self):
        """The real bug: these used to compare SAME because both parsed
        down to just the year 2024, losing which half entirely."""
        result = compare_periods("H1 2024", "H2 2024")
        assert result.relation == DIFFERENT and result.distinguishing

    def test_a_half_against_its_own_year_overlaps(self):
        assert compare_periods("H1 2024", "2024").relation == OVERLAPPING

    def test_a_quarter_against_a_half_overlaps_rather_than_matching(self):
        """Different granularities of the same year -- neither same nor
        cleanly different, since Q1 sits inside H1."""
        assert compare_periods("Q1 2024", "H1 2024").relation == OVERLAPPING

    def test_quarters_are_unaffected_by_the_generalization(self):
        assert compare_periods("Q1 2024", "Q2 2024").relation == DIFFERENT
        assert compare_periods("Q1 FY25", "FY25").relation == OVERLAPPING
        assert compare_periods("Q1 FY25", "Q1 2025").relation == SAME
