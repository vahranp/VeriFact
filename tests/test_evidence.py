"""Tests for independent fact-vs-evidence verification.

Checking that a quote exists is not the same as checking that it supports
the fact. Measured on 336 real extracted facts, splitting those two
questions moved 121 facts (36%) out of "grounded" and into "grounded but
unverified" -- so these tests pin both that the real failure shapes are
caught and that correct facts are not swept up with them.
"""
import pytest

from app.evidence import (
    FACT_VALIDATED, QUOTE_GROUNDED, UNGROUNDED, check_evidence,
)


def _fact(**kw):
    base = {"statement": "s", "quote": "", "value": None, "value_numeric": None,
            "unit": None, "subject": None, "time_period": None}
    base.update(kw)
    return base


class TestUngrounded:
    def test_a_quote_not_in_the_source_is_ungrounded(self):
        check = check_evidence(_fact(quote="anything"), quote_grounded=False)
        assert check.status == UNGROUNDED
        assert not check.validated

    def test_ungrounded_short_circuits_before_field_checks(self):
        """No point asking whether text supports a value when the text
        isn't in the document."""
        check = check_evidence(_fact(quote="x", value_numeric=5.0), quote_grounded=False)
        assert check.value_supported is None


class TestValueMustAppearInItsQuote:
    def test_the_real_table_row_label_failure(self):
        """The failure that motivated this module. The quote is verbatim
        on the page -- it is a table *row label* -- and the number lives in
        a cell the text flattening separated from it."""
        check = check_evidence(
            _fact(quote="Number of complaints filed during the year",
                  value="779", value_numeric=779.0),
            quote_grounded=True,
        )
        assert check.status == QUOTE_GROUNDED
        assert check.value_supported is False
        assert "does not appear in the quote" in check.describe()

    def test_a_value_present_in_the_quote_validates(self):
        check = check_evidence(
            _fact(quote="Revenue from operations was 81,415.38 million for the year",
                  value="81,415.38", value_numeric=81415.38, unit="INR million"),
            quote_grounded=True,
        )
        assert check.status == FACT_VALIDATED

    def test_thousands_separators_do_not_cause_a_false_mismatch(self):
        check = check_evidence(
            _fact(quote="Total was 1,234,567 units", value_numeric=1234567.0),
            quote_grounded=True,
        )
        assert check.value_supported is True

    def test_an_integer_float_matches_its_written_form(self):
        """8142.0 is written "8,142" in prose, not "81420"."""
        check = check_evidence(
            _fact(quote="Revenue stood at 8,142 crore this year", value_numeric=8142.0),
            quote_grounded=True,
        )
        assert check.value_supported is True

    def test_a_parenthesised_negative_matches_its_digits(self):
        check = check_evidence(
            _fact(quote="Loss for the year was (2,307.33) million",
                  value="(2,307.33)", value_numeric=-2307.33),
            quote_grounded=True,
        )
        assert check.value_supported is True

    def test_the_transposed_digit_failure_is_caught(self):
        """Source says 81,415.38; the model reports 91,415.38. The quote is
        real, so the old check passed this."""
        check = check_evidence(
            _fact(quote="Revenue was 81,415.38 million for the period",
                  value="91,415.38", value_numeric=91415.38),
            quote_grounded=True,
        )
        assert check.value_supported is False
        assert check.status == QUOTE_GROUNDED


class TestQualitativeFacts:
    def test_a_fact_with_no_number_has_nothing_numeric_to_verify(self):
        """Absence of a number is not a verification failure."""
        check = check_evidence(
            _fact(quote="The director is liable to retire by rotation at the next meeting",
                  value="liable to retire by rotation"),
            quote_grounded=True,
        )
        assert check.value_supported is None
        assert check.status == FACT_VALIDATED

    def test_a_status_value_is_not_treated_as_a_missing_number(self):
        check = check_evidence(
            _fact(quote="Mr. Sharma resigned from the Board with effect from March 2024",
                  value="Resigned"),
            quote_grounded=True,
        )
        assert check.status == FACT_VALIDATED


class TestCircularEvidence:
    """A quote that is only the number proves nothing -- it restates the
    fact rather than evidencing it. 105 of 336 real facts had quotes this
    short, which is why this is reported rather than ignored."""

    @pytest.mark.parametrize("quote,value", [
        ("35.69%", "35.69%"),
        ("(2,307.33)", "(2,307.33)"),
        ("779", "779"),
    ])
    def test_bare_value_quotes_are_not_fully_validated(self, quote, value):
        check = check_evidence(_fact(quote=quote, value=value), quote_grounded=True)
        assert check.status == QUOTE_GROUNDED
        assert "too short to carry context" in check.describe()

    def test_a_quote_with_real_context_validates(self):
        check = check_evidence(
            _fact(quote="Equity stake held by the company was 35.69% as at year end",
                  value="35.69%", value_numeric=35.69, unit="%"),
            quote_grounded=True,
        )
        assert check.status == FACT_VALIDATED


class TestSupportingSignalsNeverReject:
    """Unit, subject and period are reported but must never downgrade a
    fact: all three are routinely stated once in a table header or page
    preamble rather than inside the quoted sentence, so treating their
    absence as failure would flag most correct facts."""

    def test_a_unit_absent_from_the_quote_does_not_reject(self):
        check = check_evidence(
            _fact(quote="Revenue from operations was 81,415.38 for the year",
                  value_numeric=81415.38, unit="INR million"),
            quote_grounded=True,
        )
        assert check.status == FACT_VALIDATED

    def test_a_subject_absent_from_the_quote_does_not_reject(self):
        check = check_evidence(
            _fact(quote="Revenue from operations was 81,415.38 million",
                  value_numeric=81415.38, subject="Delhivery Limited"),
            quote_grounded=True,
        )
        assert check.status == FACT_VALIDATED

    def test_a_unit_visible_in_the_quote_is_reported_as_supported(self):
        check = check_evidence(
            _fact(quote="Revenue was 81,415.38 million during the year",
                  value_numeric=81415.38, unit="INR million"),
            quote_grounded=True,
        )
        assert check.unit_supported is True

    def test_a_subject_visible_in_the_quote_is_reported_as_supported(self):
        check = check_evidence(
            _fact(quote="Delhivery reported revenue of 81,415.38 million",
                  value_numeric=81415.38, subject="Delhivery Limited"),
            quote_grounded=True,
        )
        assert check.subject_supported is True


class TestDomainNeutrality:
    def test_a_non_financial_fact_validates_identically(self):
        """Nothing here knows what a document is about."""
        check = check_evidence(
            _fact(quote="The species was recorded at 1,204 individuals in the 2019 survey",
                  value="1,204", value_numeric=1204.0, unit="individuals"),
            quote_grounded=True,
        )
        assert check.status == FACT_VALIDATED

    def test_a_non_financial_mismatch_is_caught_identically(self):
        check = check_evidence(
            _fact(quote="Population counts by region are shown below",
                  value="1,204", value_numeric=1204.0),
            quote_grounded=True,
        )
        assert check.value_supported is False
