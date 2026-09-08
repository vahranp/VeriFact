"""Unit tests for the deterministic parts of fact extraction: evidence
grounding, numeric recovery, and malformed-response recovery.

The LLM call itself is never made here -- these cover the code that
decides whether to trust what the model returned.
"""
import pytest

from app import fact_extraction
from app.fact_extraction import (
    _clean,
    _find_quote_offset,
    _numeric_fallback,
    _recover_non_list_shape,
    extract_facts_from_chunk,
)
from app.pdf_extract import Chunk
from app.tables import PLAIN_NOT_TABULAR, PLAIN_RECONSTRUCTION_REJECTED, RECONSTRUCTED


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

    @pytest.mark.parametrize("value", ["FY2024", "FY 2024", "Q1 2025", "fiscal 2024"])
    def test_a_period_label_in_the_value_field_is_not_read_as_a_number(self, value):
        """A model occasionally puts a period label where a value belongs.
        Falling back to the digits in "FY2024" would fabricate
        value_numeric=2024 for a fact that isn't stating the number 2024 at
        all -- worse than leaving it null."""
        assert _numeric_fallback(value) is None

    def test_a_bare_number_that_looks_like_a_year_is_still_read(self):
        """The period-label guard is scoped to an explicit fiscal/quarter
        prefix, not to anything that could theoretically be a year --
        otherwise a genuine count of 2024 (e.g. "2024 units") would be
        wrongly suppressed too."""
        assert _numeric_fallback("2024") == 2024.0


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


def _page_text(marker: str) -> str:
    # Each test needs genuinely distinct chunk text: extract_facts_from_chunk's
    # cache key is hash(model, prompt, chunk.text, chunk.page_context) --
    # NOT table_context or page_number -- so two tests sharing identical
    # text would have the second silently served the first's cached
    # result (including its table_context-derived issue/evidence_detail)
    # regardless of what its own mock intended to return.
    return f"Revenue from operations was 8,142 crore for the year ({marker})"


def _mock_extraction_llm(monkeypatch, quote, facts=None):
    facts = facts if facts is not None else [{
        "subject": "Company", "attribute": "revenue", "value": "8,142 crore",
        "value_numeric": 8142.0, "unit": "INR Crore", "time_period": "FY24",
        "statement": "Company revenue from operations was 8,142 crore in FY24.",
        "quote": quote, "confidence": 0.9,
    }]
    monkeypatch.setattr(fact_extraction, "chat_json", lambda *a, **k: facts)


class TestTableContextIsStampedOntoFacts:
    """table_context (see app/tables.py) has to survive from the chunk it
    came from onto every fact extracted from it -- that's what lets a
    reviewer (or a future automated check) tell a fact grounded in
    confidently-reconstructed table text from one grounded in a page that
    looked tabular but whose reconstruction was rejected."""

    def test_a_reconstructed_chunks_facts_carry_that_context(self, monkeypatch):
        text = _page_text("reconstructed")
        _mock_extraction_llm(monkeypatch, quote=text)
        chunk = Chunk(page_number=1, text=text, table_context=RECONSTRUCTED)
        facts, _issues, _cache_hit = extract_facts_from_chunk(chunk)
        assert facts[0]["table_context"] == RECONSTRUCTED

    def test_a_plain_prose_chunks_facts_carry_that_context(self, monkeypatch):
        text = _page_text("plain")
        _mock_extraction_llm(monkeypatch, quote=text)
        chunk = Chunk(page_number=1, text=text, table_context=PLAIN_NOT_TABULAR)
        facts, _issues, _cache_hit = extract_facts_from_chunk(chunk)
        assert facts[0]["table_context"] == PLAIN_NOT_TABULAR

    def test_a_rejected_reconstruction_flags_an_issue_and_the_facts_evidence_detail(self, monkeypatch):
        text = _page_text("rejected")
        _mock_extraction_llm(monkeypatch, quote=text)
        chunk = Chunk(page_number=7, text=text, table_context=PLAIN_RECONSTRUCTION_REJECTED)
        facts, issues, _cache_hit = extract_facts_from_chunk(chunk)

        assert facts[0]["table_context"] == PLAIN_RECONSTRUCTION_REJECTED
        assert "reconstruction was rejected" in facts[0]["evidence_detail"]
        assert any(i["issue_type"] == "table_alignment_uncertain" for i in issues)

    def test_a_confident_reconstruction_raises_no_such_issue(self, monkeypatch):
        text = _page_text("confident")
        _mock_extraction_llm(monkeypatch, quote=text)
        chunk = Chunk(page_number=1, text=text, table_context=RECONSTRUCTED)
        _facts, issues, _cache_hit = extract_facts_from_chunk(chunk)
        assert not any(i["issue_type"] == "table_alignment_uncertain" for i in issues)

    def test_the_issue_is_recorded_once_per_chunk_not_once_per_fact(self, monkeypatch):
        text = _page_text("many facts")
        many_facts = [{
            "subject": "Company", "attribute": f"metric {i}", "value": "8,142",
            "value_numeric": 8142.0, "statement": f"metric {i} was 8,142",
            "quote": text, "confidence": 0.9,
        } for i in range(3)]
        _mock_extraction_llm(monkeypatch, quote=text, facts=many_facts)
        chunk = Chunk(page_number=1, text=text, table_context=PLAIN_RECONSTRUCTION_REJECTED)
        _facts, issues, _cache_hit = extract_facts_from_chunk(chunk)
        assert sum(1 for i in issues if i["issue_type"] == "table_alignment_uncertain") == 1


class TestClean:
    @pytest.mark.parametrize("value", ["null", "None", "N/A", "na", "", "  "])
    def test_nullish_strings_become_none(self, value):
        assert _clean(value) is None

    def test_real_values_pass_through(self):
        assert _clean("consolidated") == "consolidated"
        assert _clean(42) == 42
