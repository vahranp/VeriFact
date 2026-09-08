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


class TestMetricPromptGuidance:
    """The metric-match prompt is the component that decides whether a pair
    is even eligible to be called a contradiction, so the instructions it
    depends on are asserted here. These exist because a real regression
    slipped through: the prompt listed "revenue from services" as a synonym
    for "revenue", which directly contradicted the segment rule added later
    and caused three false contradictions between different revenue
    segments on a live document.
    """

    def test_prompt_requires_naming_each_slice_before_judging(self):
        from app.relationships import SYSTEM_PROMPT_METRIC
        assert "slice_a" in SYSTEM_PROMPT_METRIC
        assert "slice_b" in SYSTEM_PROMPT_METRIC
        # the ordering matters: naming the slice must come before the verdict
        assert SYSTEM_PROMPT_METRIC.index("STAGE 1") < SYSTEM_PROMPT_METRIC.index("STAGE 2")

    def test_prompt_covers_part_versus_total(self):
        """A segment compared against its own total is the specific shape
        that manufactures false contradictions -- the smaller figure is
        supposed to be smaller."""
        from app.relationships import SYSTEM_PROMPT_METRIC
        assert "whole" in SYSTEM_PROMPT_METRIC
        assert "false contradiction" in SYSTEM_PROMPT_METRIC.lower()

    def test_synonym_guidance_is_scoped_to_matching_slices(self):
        """Synonym guidance must not be stated unconditionally, or it
        overrides the slice check -- which is exactly the bug that
        occurred."""
        from app.relationships import SYSTEM_PROMPT_METRIC
        synonym_pos = SYSTEM_PROMPT_METRIC.index("net worth")
        scoping_pos = SYSTEM_PROMPT_METRIC.index("When the slices DO match")
        assert scoping_pos < synonym_pos, (
            "synonym examples must appear after the clause that scopes them "
            "to slice-matching pairs"
        )

    def test_revenue_from_services_is_not_listed_as_an_unconditional_synonym(self):
        """The specific line that caused the regression."""
        from app.relationships import SYSTEM_PROMPT_METRIC
        assert '"revenue from services"' not in SYSTEM_PROMPT_METRIC


class TestCanonicalPairOrdering:
    """The pair cache is order-independent by design, but the prompts label
    the facts positionally and the model's explanation refers to those
    labels. Without a canonical presentation order, a pair first judged as
    (A, B) and later met as (B, A) is served a cached explanation whose
    "FACT A" points at the wrong fact.
    """

    def test_swapping_the_arguments_does_not_change_the_cache_key(self):
        from app.cache import hash_fact_pair
        a = {"subject": "X", "attribute": "revenue", "statement": "X revenue was 10"}
        b = {"subject": "X", "attribute": "revenue", "statement": "X revenue was 20"}
        assert hash_fact_pair(a, b) == hash_fact_pair(b, a)

    def test_canonical_order_is_stable_and_opposite_for_swapped_inputs(self):
        from app.cache import canonical_pair_order
        a = {"subject": "X", "attribute": "revenue", "statement": "aaa"}
        b = {"subject": "X", "attribute": "revenue", "statement": "bbb"}
        assert canonical_pair_order(a, b) != canonical_pair_order(b, a)
        # and it is deterministic, not dependent on call order
        assert canonical_pair_order(a, b) == canonical_pair_order(a, b)

    def test_identical_facts_are_never_reported_as_needing_a_swap(self):
        from app.cache import canonical_pair_order
        f = {"subject": "X", "attribute": "revenue", "statement": "same"}
        assert canonical_pair_order(f, dict(f)) is False

    def test_classify_pair_reports_whether_it_swapped(self, no_cache, monkeypatch):
        """The caller needs this to persist the relationship in the order
        the explanation describes."""
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: {
            "same_metric": True, "relation_type": "corroborates",
            "explanation": "x", "confidence": 0.9,
        })
        low = {"subject": "X", "attribute": "r", "statement": "aaa", "page_number": 1, "quote": "q"}
        high = {"subject": "X", "attribute": "r", "statement": "bbb", "page_number": 1, "quote": "q"}

        _, _ = R.classify_pair(low, "d", high, "d")
        forward = R.classify_pair(low, "d", high, "d")[0]["_meta"]["swapped"]
        reverse = R.classify_pair(high, "d", low, "d")[0]["_meta"]["swapped"]
        assert forward != reverse, "exactly one presentation order must be swapped"


def _f(statement):
    return {"statement": statement, "page_number": 1, "quote": "q",
            "subject": "X", "attribute": "revenue"}


class TestModelOutputIsValidated:
    """The schemas in app/schemas.py were written for exactly this and were
    only ever applied to fact extraction, leaving the relationship path --
    which writes assertions about two other rows -- taking raw model output
    on trust. A bad fact is one bad row; a bad relationship is a claim about
    two of them."""

    def test_unrecognised_relation_type_becomes_uncertain_not_a_real_type(self, no_cache, monkeypatch):
        """The system is allowed to not know. What it must never do is
        coerce an unrecognised label into a real relationship."""
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: {
            "same_metric": True, "relation_type": "sort_of_agrees",
            "explanation": "x", "confidence": 0.9,
        })
        result, _ = R.classify_pair(_f("a"), "d", _f("b"), "d")
        assert result["relation_type"] == "uncertain"

    def test_known_alias_is_normalised(self, no_cache, monkeypatch):
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: {
            "same_metric": True, "relation_type": "CONTRADICTION",
            "explanation": "x", "confidence": 0.9,
        })
        assert R.classify_pair(_f("a"), "d", _f("b"), "d")[0]["relation_type"] == "contradicts"

    def test_out_of_range_confidence_becomes_none_not_a_false_maximum(self, no_cache, monkeypatch):
        """A confidence of 7.5 is a model error, not evidence of maximum
        confidence -- clamping it to 1.0 would display as MORE certain
        than a well-behaved 0.9 response, which is backwards."""
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: {
            "same_metric": True, "relation_type": "corroborates",
            "explanation": "x", "confidence": 7.5,
        })
        assert R.classify_pair(_f("a"), "d", _f("b"), "d")[0]["confidence"] is None

    def test_a_list_response_cannot_crash_the_pair(self, no_cache, monkeypatch):
        """A malformed response that recovers to a list used to reach
        result.get(...) and raise AttributeError. It must degrade to a
        safe default instead."""
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: [1, 2, 3])
        result, _ = R.classify_pair(_f("a"), "d", _f("b"), "d")
        assert result["relation_type"] == "unrelated"

    def test_a_malformed_response_never_manufactures_a_relationship(self, no_cache, monkeypatch):
        """The fallback must assert nothing -- a broken judgment should
        cost one relationship, not invent one."""
        import app.relationships as R
        for junk in ({}, {"nonsense": True}, {"same_metric": "maybe"}):
            monkeypatch.setattr(R, "chat_json", lambda *a, **k: junk)
            result, _ = R.classify_pair(_f("a"), "d", _f("b"), "d")
            assert result["relation_type"] not in ("corroborates", "contradicts", "reconciled")

    def test_step2_garbage_yields_uncertain_not_a_stored_relation(self, no_cache, monkeypatch):
        import app.relationships as R
        calls = {"n": 0}

        def fake(*a, **k):
            calls["n"] += 1
            return {"same_metric": True} if calls["n"] == 1 else {"junk": "yes"}

        monkeypatch.setattr(R, "chat_json", fake)
        result, _ = R.classify_pair(_f("a"), "d", _f("b"), "d")
        assert result["relation_type"] == "uncertain"


class TestUncertaintyIsExpressible:
    """A system that can only answer corroborates / contradicts /
    reconciled must force every judged pair into one of them, and the
    resulting failure is silent: a pair the evidence does not settle gets
    a confident label instead of an admission."""

    def test_uncertain_is_a_stored_relation(self):
        from app.relationships import STORED_RELATIONS
        assert "uncertain" in STORED_RELATIONS

    def test_unrelated_is_still_not_stored(self):
        """It asserts nothing and is the majority of candidate pairs."""
        from app.relationships import STORED_RELATIONS
        assert "unrelated" not in STORED_RELATIONS

    def test_the_judge_prompt_offers_uncertain(self):
        from app.relationships import SYSTEM_PROMPT_JUDGE
        assert '"uncertain"' in SYSTEM_PROMPT_JUDGE
        assert "insufficient" in SYSTEM_PROMPT_JUDGE.lower()

    def test_classify_pair_can_return_uncertain(self, no_cache, monkeypatch):
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: {
            "same_metric": True, "relation_type": "uncertain",
            "explanation": "period unknown, cannot tell", "confidence": 0.3,
        })
        result, _ = R.classify_pair(_f("a"), "d", _f("b"), "d")
        assert result["relation_type"] == "uncertain"


class TestContextReachesTheJudge:
    def test_period_and_scope_determinations_are_in_the_prompt(self, no_cache, monkeypatch):
        import app.relationships as R
        seen = {}

        def fake(model, system, user, **kw):
            seen["user"] = user
            return {"same_metric": True, "relation_type": "reconciled",
                    "explanation": "x", "confidence": 0.9}

        monkeypatch.setattr(R, "chat_json", fake)
        a = dict(_f("a"), time_period="FY24", scope="consolidated")
        b = dict(_f("b"), time_period="FY23", scope="consolidated")
        R.classify_pair(a, "d", b, "d")
        assert "Reporting period: DIFFERENT" in seen["user"]
        assert "Scope: SAME" in seen["user"]

    def test_meta_reports_the_context_determinations(self, no_cache, monkeypatch):
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: {
            "same_metric": True, "relation_type": "corroborates",
            "explanation": "x", "confidence": 0.9,
        })
        a = dict(_f("a"), time_period="FY24")
        b = dict(_f("b"), time_period="FY2023-24")
        result, _ = R.classify_pair(a, "d", b, "d")
        assert result["_meta"]["period"] == "same"


class TestDeterministicAdjudicationIsWiredIn:
    """Integration-level checks that classify_pair actually runs its
    output through app/adjudication.py rather than trusting the model's
    step-2 answer outright -- the unit tests for the adjudication rules
    themselves live in tests/test_adjudication.py."""

    def test_a_wrong_llm_proposal_is_overridden_end_to_end(self, no_cache, monkeypatch):
        """The model insists these contradict; the values agree to
        0.006% for the same period. The final answer must not be the
        model's -- exactly the failure app/adjudication.py exists to catch."""
        import app.relationships as R

        def fake(model, system, user, **kw):
            if "same_metric" in system:
                return {"same_metric": True, "reason": "both state revenue"}
            return {"relation_type": "contradicts", "explanation": "the numbers just differ",
                    "confidence": 0.95}

        monkeypatch.setattr(R, "chat_json", fake)
        result, _ = R.classify_pair(
            _fact(value_numeric=8142.0, unit="INR Crore", time_period="FY24"), "a.pdf",
            _fact(value_numeric=81415.38, unit="INR million", time_period="FY2023-24"), "b.pdf",
        )
        assert result["relation_type"] == "corroborates"
        assert result["decision_source"] == "deterministic_override"
        assert result["disagreement"] is True
        assert result["llm_proposal"] == "contradicts"
        assert result["disagreement_reason"]

    def test_an_agreeing_llm_proposal_is_confirmed_not_silently_replaced(self, no_cache, calls):
        result, _ = relationships.classify_pair(
            _fact(value_numeric=8142.0, unit="INR Crore"), "a.pdf",
            _fact(value_numeric=81415.38, unit="INR million"), "b.pdf",
        )
        assert result["decision_source"] == "deterministic_confirmed"
        assert result["disagreement"] is False
        assert result["llm_proposal"] == "corroborates"

    def test_qualitative_facts_are_unchecked_not_forced(self, no_cache, monkeypatch):
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: {
            "same_metric": True, "relation_type": "corroborates",
            "explanation": "both assert resignation", "confidence": 0.8,
        })
        result, _ = R.classify_pair(
            _fact(value_numeric=None, unit=None, attribute="director status"), "a.pdf",
            _fact(value_numeric=None, unit=None, attribute="director status"), "b.pdf",
        )
        assert result["decision_source"] == "llm_unchecked"
        assert result["disagreement"] is False

    def test_the_unrelated_early_return_carries_the_same_field_shape(self, no_cache, monkeypatch):
        """Callers should be able to rely on these keys existing regardless
        of which path produced the result."""
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda *a, **k: {
            "same_metric": False, "reason": "different measures",
        })
        result, _ = R.classify_pair(_fact(), "a.pdf", _fact(attribute="headcount"), "b.pdf")
        for key in ("decision_source", "llm_proposal", "disagreement", "disagreement_reason", "adjudication_checks"):
            assert key in result

    def test_incompatible_units_become_related_but_not_comparable(self, no_cache, monkeypatch):
        import app.relationships as R
        monkeypatch.setattr(R, "chat_json", lambda model, system, user, **k: (
            {"same_metric": True, "reason": "both are figures"} if "same_metric" in system else
            {"relation_type": "uncertain", "explanation": "not sure", "confidence": 0.5}
        ))
        result, _ = R.classify_pair(
            _fact(value_numeric=5.6, unit="%"), "a.pdf",
            _fact(value_numeric=404.0, unit="INR Crore"), "b.pdf",
        )
        assert result["relation_type"] == "related_but_not_comparable"


class TestUnverifiedEvidenceCaveat:
    """Audited in alongside the identical gap already fixed in
    app/arithmetic.py and app/coherence.py: the deterministic comparison
    handed to the judge is only as trustworthy as the values feeding it.
    An ungrounded or circular-quote fact's value_numeric has no more claim
    to being correct than any other unverified number would."""

    def test_unverified_evidence_reaches_the_prompt_as_a_caveat(self, no_cache, monkeypatch):
        import app.relationships as R
        seen = {}

        def fake(model, system, user, **kw):
            seen["user"] = user
            return {"same_metric": True, "relation_type": "uncertain",
                    "explanation": "x", "confidence": 0.5}

        monkeypatch.setattr(R, "chat_json", fake)
        a = dict(_f("a"), evidence_status="ungrounded")
        b = dict(_f("b"), evidence_status="fact_validated")
        R.classify_pair(a, "d", b, "d")
        assert "CAVEAT" in seen["user"]
        assert "not itself evidenced" in seen["user"]

    def test_fully_verified_facts_carry_no_caveat(self, no_cache, monkeypatch):
        import app.relationships as R
        seen = {}

        def fake(model, system, user, **kw):
            seen["user"] = user
            return {"same_metric": True, "relation_type": "corroborates",
                    "explanation": "x", "confidence": 0.9}

        monkeypatch.setattr(R, "chat_json", fake)
        a = dict(_f("a"), evidence_status="fact_validated")
        b = dict(_f("b"), evidence_status="fact_validated")
        R.classify_pair(a, "d", b, "d")
        assert "CAVEAT" not in seen["user"]

    def test_missing_evidence_status_carries_no_caveat(self, no_cache, monkeypatch):
        """No evidence_status field at all is "not yet assessed", not
        "known bad" -- matching every other fixture in this file, none of
        which set the field."""
        import app.relationships as R
        seen = {}

        def fake(model, system, user, **kw):
            seen["user"] = user
            return {"same_metric": True, "relation_type": "corroborates",
                    "explanation": "x", "confidence": 0.9}

        monkeypatch.setattr(R, "chat_json", fake)
        R.classify_pair(_f("a"), "d", _f("b"), "d")
        assert "CAVEAT" not in seen["user"]

    def test_an_unverified_agreement_does_not_assert_context_explains_nothing(self, no_cache, monkeypatch):
        """values_agree must become unreliable (None) when evidence is
        unverified, the same way magnitude_suspect already disables it --
        otherwise the context block could confidently tell the judge "no
        discrepancy to explain" based on an unconfirmed number."""
        import app.relationships as R
        seen = {}

        def fake(model, system, user, **kw):
            seen["user"] = user
            return {"same_metric": True, "relation_type": "uncertain",
                    "explanation": "x", "confidence": 0.5}

        monkeypatch.setattr(R, "chat_json", fake)
        a = dict(_f("a"), value_numeric=100.0, unit="INR million", evidence_status="ungrounded")
        b = dict(_f("b"), value_numeric=100.0, unit="INR million", evidence_status="fact_validated")
        R.classify_pair(a, "d", b, "d")
        assert "no discrepancy for context to explain" not in seen["user"]

    def test_the_caveat_changes_the_effective_comparison_line(self, no_cache, monkeypatch):
        """comparison_line is part of classify_relation's cache key
        (hash_text(..., comparison_line)), so a real difference in that
        string is what stops a stale judgment made before evidence_status
        existed from being silently served for a now-flagged pair."""
        import app.relationships as R
        seen = {}

        def fake(model, system, user, **kw):
            seen["user"] = user
            return {"same_metric": True, "relation_type": "uncertain",
                    "explanation": "x", "confidence": 0.5}

        monkeypatch.setattr(R, "chat_json", fake)

        R.classify_pair(dict(_f("a"), evidence_status="ungrounded"), "d",
                         dict(_f("b"), evidence_status="fact_validated"), "d")
        flagged_prompt = seen["user"]

        R.classify_pair(dict(_f("a"), evidence_status="fact_validated"), "d",
                         dict(_f("b"), evidence_status="fact_validated"), "d")
        clean_prompt = seen["user"]

        assert flagged_prompt != clean_prompt
        assert "CAVEAT" in flagged_prompt and "CAVEAT" not in clean_prompt
