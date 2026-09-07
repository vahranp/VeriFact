"""Tests for the re-grounding retry.

The invariant that matters: re-grounding can only ever UPGRADE a fact's
evidence. A retry's answer is put through exactly the same verbatim check
as the original quote, so a model that paraphrases again, hallucinates a
span, or errors out leaves the fact flagged ungrounded rather than
silently "fixed".
"""
import pytest

from app import fact_extraction
from app.fact_extraction import _reground_quote
from app.llm_client import LLMError

PAGE = "Total Equity 91,446.46 91,771.37\nOther equity 90,709.67 91,042.65"


def _mock_llm(monkeypatch, response):
    def fake(model, system, user, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(fact_extraction, "chat_json", fake)


def test_recovers_a_genuinely_verbatim_span(monkeypatch):
    _mock_llm(monkeypatch, {"found": True, "quote": "Total Equity 91,446.46"})
    assert _reground_quote("Total equity is 91,446.46", PAGE) == "Total Equity 91,446.46"


def test_rejects_a_retry_that_paraphrases_again(monkeypatch):
    """The whole point: a second paraphrase must not be accepted just
    because the model asserted found=true."""
    _mock_llm(monkeypatch, {"found": True, "quote": "The total equity was about 91.4 billion"})
    assert _reground_quote("Total equity is 91,446.46", PAGE) is None


def test_respects_the_model_admitting_it_cannot_find_support(monkeypatch):
    _mock_llm(monkeypatch, {"found": False, "quote": None})
    assert _reground_quote("Revenue was 500", PAGE) is None


def test_llm_failure_falls_back_to_ungrounded(monkeypatch):
    _mock_llm(monkeypatch, LLMError("ollama offline"))
    assert _reground_quote("Total equity is 91,446.46", PAGE) is None


@pytest.mark.parametrize("bad", [
    {"found": True, "quote": ""},
    {"found": True, "quote": "   "},
    {"found": True},
    {"found": True, "quote": 12345},
    ["not", "a", "dict"],
    "a bare string",
])
def test_malformed_retry_responses_are_rejected(monkeypatch, bad):
    _mock_llm(monkeypatch, bad)
    assert _reground_quote("Total equity is 91,446.46", PAGE) is None


def test_whitespace_differences_are_still_accepted(monkeypatch):
    """Consistent with the primary grounding check: a faithful copy that
    collapses PDF newlines to spaces is still genuinely grounded."""
    _mock_llm(monkeypatch, {"found": True, "quote": "Total Equity 91,446.46 91,771.37 Other equity"})
    assert _reground_quote("Total equity", PAGE) is not None
