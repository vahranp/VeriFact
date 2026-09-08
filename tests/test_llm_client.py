"""Tests for JSON recovery from model responses.

Local models wrap JSON in prose or markdown fences often enough that the
recovery path is load-bearing, and a mistake here does not fail loudly --
it hands the caller a value of the wrong shape.
"""
import json

import pytest

from app.llm_client import _extract_json_block


def _parse(text):
    return json.loads(_extract_json_block(text), strict=False)


class TestDelimiterSelection:
    def test_object_containing_brackets_in_a_string_stays_an_object(self):
        """Real crash path: preferring "[" unconditionally carved this into
        "[1]", which parses cleanly to a list -- so the caller died on
        result.get(...) with an AttributeError instead of getting the
        LLMParseError it is written to handle."""
        raw = '{"same_metric": true, "reason": "values [1] and [2] differ"}'
        assert _parse(raw) == {"same_metric": True, "reason": "values [1] and [2] differ"}

    def test_plain_array_is_still_returned_as_an_array(self):
        assert _parse('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]

    def test_object_wrapping_an_array_returns_the_object(self):
        """The caller (fact_extraction._recover_non_list_shape) unwraps
        this itself, and needs the key to do so."""
        assert _parse('{"facts": [{"a": 1}]}') == {"facts": [{"a": 1}]}

    def test_whichever_delimiter_opens_first_wins(self):
        assert isinstance(_parse('[{"a": 1}]'), list)
        assert isinstance(_parse('{"a": [1]}'), dict)


class TestStringAwareness:
    def test_brackets_inside_strings_do_not_close_the_structure(self):
        assert _parse('{"q": "a ] b } c"}') == {"q": "a ] b } c"}

    def test_escaped_quotes_do_not_end_the_string(self):
        raw = '{"q": "he said \\"[hi]\\" loudly"}'
        assert _parse(raw) == {"q": 'he said "[hi]" loudly'}

    def test_a_brace_inside_a_quote_is_not_treated_as_nesting(self):
        raw = '[{"quote": "revenue {net} was 5"}]'
        assert _parse(raw) == [{"quote": "revenue {net} was 5"}]


class TestProseAndFences:
    def test_markdown_fenced_json_is_unwrapped(self):
        assert _parse('Here you go:\n```json\n[{"a": 1}]\n```\nhope that helps') == [{"a": 1}]

    def test_unlabelled_fence_is_unwrapped(self):
        assert _parse('```\n{"a": 1}\n```') == {"a": 1}

    def test_json_embedded_in_prose_is_found(self):
        assert _parse('Sure! {"k": "v"} Let me know.') == {"k": "v"}


class TestDegenerateInput:
    @pytest.mark.parametrize("raw", ["", "no json here at all", "{unclosed"])
    def test_unrecoverable_input_is_returned_for_the_caller_to_reject(self, raw):
        """Returning the text unchanged lets json.loads raise, which
        chat_json turns into LLMParseError -- the handled path."""
        with pytest.raises(json.JSONDecodeError):
            _parse(raw)
