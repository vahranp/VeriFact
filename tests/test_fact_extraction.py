"""Unit tests for the deterministic parts of fact extraction: evidence
grounding, numeric recovery, and malformed-response recovery.

The LLM call itself is never made here -- these cover the code that
decides whether to trust what the model returned.
"""
import pytest

from app.fact_extraction import (
    _clean,
    _find_quote_offset,
    _numeric_fallback,
    _recover_non_list_shape,
)


class TestFindQuoteOffset:
    """Evidence grounding is the project's core integrity guarantee: a
    quote the model claims to have copied must actually be findable in
    the source page."""

    def test_exact_match_found(self):
        page = "Revenue from operations for FY24 was INR 81,415.38 million."
        assert _find_quote_offset(page, "INR 81,415.38 million") > 0

    def test_absent_quote_returns_minus_one(self):
        page = "Revenue from operations for FY24 was INR 81,415.38 million."
        assert _find_quote_offset(page, "a quote that was never on this page") == -1

    def test_whitespace_normalized_fuzzy_match(self):
        """PDF text routinely contains newlines/tabs where the model
        reproduces single spaces -- that must still count as grounded,
        since the model did copy the text faithfully."""
        page = "Total Equity\n91,446.46\t91,771.37"
        assert _find_quote_offset(page, "Total Equity 91,446.46 91,771.37") != -1

    def test_paraphrase_is_not_accepted_as_grounded(self):
        """The failure this check exists to catch: the model states a
        real fact but 'quotes' its own paraphrase."""
        page = "Your Company has expanded the gateway infrastructure in Bhiwandi to 750,000 sq ft"
        assert _find_quote_offset(page, "Delhivery expanded Bhiwandi to 750,000 square feet") == -1

    def test_empty_quote_returns_minus_one(self):
        assert _find_quote_offset("some page text", "   ") == -1


class TestNumericFallback:
    """Regression tests for a real observed bug: the model populated
    value_numeric for negative/parenthesized figures but left it null for
    plain positive ones on the same page."""

    @pytest.mark.parametrize("value,expected", [
        ("8,142 Cr", 8142.0),
        ("12.7%", 12.7),
        ("1.4 Mn Tons", 1.4),
        ("740 Mn", 740.0),
        ("91,446.46", 91446.46),
        ("0", 0.0),
        ("-3.40", -3.4),
    ])
    def test_positive_and_plain_numbers(self, value, expected):
        assert _numeric_fallback(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value,expected", [
        ("(452) Cr", -452.0),
        ("(6.3%)", -6.3),      # number sits before the '%', then the ')'
        ("(5.6%)", -5.6),
        ("(404)", -404.0),
    ])
    def test_parenthesised_accounting_negatives(self, value, expected):
        assert _numeric_fallback(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", [None, "active", "resigned", ""])
    def test_non_numeric_returns_none(self, value):
        assert _numeric_fallback(value) is None


class TestRecoverNonListShape:
    """Local models sometimes return a single object, or wrap the array
    under a key, instead of the bare JSON array the prompt asks for."""

    def test_single_fact_object_becomes_one_element_list(self):
        raw = {"statement": "X is 5", "quote": "X is 5", "subject": "X"}
        recovered, how = _recover_non_list_shape(raw)
        assert recovered == [raw]
        assert how

    def test_wrapped_array_is_unwrapped(self):
        inner = [{"statement": "a", "quote": "a"}, {"statement": "b", "quote": "b"}]
        recovered, how = _recover_non_list_shape({"facts": inner})
        assert recovered == inner
        assert "facts" in how

    def test_unrecoverable_shape_returns_none(self):
        recovered, how = _recover_non_list_shape({"unexpected": "scalar"})
        assert recovered is None and how is None

    def test_non_dict_input_returns_none(self):
        assert _recover_non_list_shape("a string") == (None, None)


class TestClean:
    @pytest.mark.parametrize("value", ["null", "None", "N/A", "na", "", "  "])
    def test_nullish_strings_become_none(self, value):
        assert _clean(value) is None

    def test_real_values_pass_through(self):
        assert _clean("consolidated") == "consolidated"
        assert _clean(42) == 42
