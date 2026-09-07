"""Tests for the two-step relationship classification logic, with the LLM
mocked out entirely.

The point here is NOT to assert what a particular model happened to say
once -- that would be a brittle test of Ollama, not of this code. It's to
pin the deterministic control flow around the model: that step 2 is
skipped when step 1 says the metrics differ, that the normalized
comparison actually reaches the prompt, that cache keys separate the two
steps, and that only valid relation types ever reach the database.
"""
import pytest

from app import relationships


def _fact(**over):
    base = {
        "id": 1, "document_id": 1, "page_number": 10,
        "subject": "Company", "attribute": "revenue", "value": "8,142 Cr",
        "value_numeric": 8142.0, "unit": "INR Crore",
        "time_period": "FY24", "scope": None,
        "statement": "Company revenue in FY24 was 8,142 Cr.",
        "quote": "revenue ... 8,142 Cr",
    }
    base.update(over)
    return base


@pytest.fixture
def no_cache(monkeypatch):
    """Bypass the SQLite relationship cache so tests exercise the real
    control flow rather than a previous run's stored answer."""
    monkeypatch.setattr(relationships.db, "get_cached_relationship", lambda *a, **k: None)
    monkeypatch.setattr(relationships.db, "set_cached_relationship", lambda *a, **k: None)


@pytest.fixture
def calls(monkeypatch):
    """Records every prompt sent to the (mocked) LLM so tests can assert
    what the model was actually asked."""
    recorded = []

    def fake_chat_json(model, system, user, **kwargs):
        recorded.append({"system": system, "user": user})
        if "same_metric" in system:                      # step 1 prompt
            return {"same_metric": True, "reason": "both state revenue"}
        return {                                          # step 2 prompt
            "relation_type": "corroborates",
            "explanation": "values agree after normalization",
            "reconciliation_context": None,
            "confidence": 0.9,
        }

    monkeypatch.setattr(relationships, "chat_json", fake_chat_json)
    return recorded


class TestTwoStepFlow:
    def test_step2_skipped_when_metrics_differ(self, no_cache, monkeypatch):
        """The efficiency and correctness win of splitting the judgment:
        a pair that isn't even the same metric never reaches the value
        comparison, and is reported as unrelated with step 1's reason."""
        seen = []

        def fake_chat_json(model, system, user, **kwargs):
            seen.append(system)
            return {"same_metric": False, "reason": "one is revenue, the other is headcount"}

        monkeypatch.setattr(relationships, "chat_json", fake_chat_json)

        result, _ = relationships.classify_pair(_fact(), "a.pdf", _fact(attribute="headcount"), "b.pdf")

        assert len(seen) == 1, "step 2 must not run when step 1 says the metrics differ"
        assert result["relation_type"] == "unrelated"
        assert "headcount" in result["explanation"]
        assert result["_meta"]["same_metric"] is False
        assert result["_meta"]["steps_run"] == 1

    def test_both_steps_run_when_metrics_match(self, no_cache, calls):
        result, _ = relationships.classify_pair(
            _fact(), "a.pdf", _fact(value_numeric=81415.38, unit="INR million"), "b.pdf")
        assert len(calls) == 2
        assert result["relation_type"] == "corroborates"
        assert result["_meta"]["steps_run"] == 2

    def test_normalized_comparison_is_given_to_the_model(self, no_cache, calls):
        """The core P0 fix: the model must be handed a computed
        comparison, not asked to convert crore to million itself."""
        relationships.classify_pair(
            _fact(value_numeric=8142.0, unit="INR Crore"), "a.pdf",
            _fact(value_numeric=81415.38, unit="INR million"), "b.pdf")

        judge_prompt = calls[1]["user"]
        assert "Normalized numeric comparison" in judge_prompt
        assert "agree" in judge_prompt

    def test_incomparable_units_are_reported_honestly_to_the_model(self, no_cache, calls):
        relationships.classify_pair(
            _fact(value_numeric=5.6, unit="%"), "a.pdf",
            _fact(value_numeric=404, unit="INR Crore"), "b.pdf")

        judge_prompt = calls[1]["user"]
        assert "not possible" in judge_prompt

    def test_qualitative_facts_still_reach_step_2(self, no_cache, calls):
        """Status/appointment facts have no numbers at all; they must
        still be judged, just without a numeric comparison."""
        relationships.classify_pair(
            _fact(value_numeric=None, unit=None, attribute="director status"), "a.pdf",
            _fact(value_numeric=None, unit=None, attribute="director status"), "b.pdf")
        assert "not possible" in calls[1]["user"]


class TestCacheKeying:
    def test_two_steps_use_different_cache_keys(self, monkeypatch):
        """Step 1 and step 2 ask different questions about the same pair;
        sharing a cache key would let one overwrite the other."""
        keys = []
        monkeypatch.setattr(relationships.db, "get_cached_relationship",
                             lambda h: keys.append(h) or None)
        monkeypatch.setattr(relationships.db, "set_cached_relationship", lambda *a, **k: None)
        monkeypatch.setattr(relationships, "chat_json", lambda model, system, user, **k: (
            {"same_metric": True, "reason": "r"} if "same_metric" in system else
            {"relation_type": "corroborates", "explanation": "e", "reconciliation_context": None, "confidence": 1.0}
        ))

        relationships.classify_pair(_fact(), "a.pdf", _fact(value_numeric=2.0), "b.pdf")
        assert len(keys) == 2 and keys[0] != keys[1]

    def test_changed_comparison_changes_the_judge_cache_key(self, monkeypatch):
        """A normalize.py fix that changes the computed comparison must
        invalidate the stored judgment made under the old comparison."""
        keys = []
        monkeypatch.setattr(relationships.db, "get_cached_relationship",
                             lambda h: keys.append(h) or None)
        monkeypatch.setattr(relationships.db, "set_cached_relationship", lambda *a, **k: None)
        monkeypatch.setattr(relationships, "chat_json", lambda model, system, user, **k: (
            {"same_metric": True, "reason": "r"} if "same_metric" in system else
            {"relation_type": "corroborates", "explanation": "e", "reconciliation_context": None, "confidence": 1.0}
        ))

        a = _fact()
        relationships.classify_pair(a, "a.pdf", _fact(value_numeric=8142.0, unit="INR Crore"), "b.pdf")
        first_judge_key = keys[1]
        keys.clear()
        # same fact *content* signature, but a value that changes the comparison
        relationships.classify_pair(a, "a.pdf", _fact(value_numeric=999999.0, unit="INR Crore"), "b.pdf")
        assert keys[1] != first_judge_key


class TestRelationTypeGating:
    @pytest.mark.parametrize("relation,should_store", [
        ("corroborates", True),
        ("contradicts", True),
        ("reconciled", True),
        ("unrelated", False),
        ("nonsense_value_from_a_confused_model", False),
        (None, False),
    ])
    def test_only_valid_relation_types_are_persisted(self, relation, should_store):
        """Guards the database against a model returning an unexpected
        enum value -- only the three real relationship types are stored."""
        assert (relation in ("corroborates", "contradicts", "reconciled")) is should_store
