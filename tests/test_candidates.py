"""Tests for hybrid candidate retrieval.

The point of this module is that a pair can be promoted by any one of
several independent signals, so these tests check each signal fires on
its own AND that the cheap signals don't fire indiscriminately -- a
retrieval layer that promotes everything is just as useless as one that
promotes nothing, since every promotion costs an LLM call.
"""
from app.candidates import (
    EMBEDDING_STRONG, entity_similarity, jaccard, score_pair,
)


def _f(subject, attribute, value=None, unit=None, statement=""):
    return {
        "subject": subject, "attribute": attribute, "value_numeric": value,
        "unit": unit, "statement": statement or f"{subject} {attribute}",
    }


class TestLexicalSignals:
    def test_jaccard_ignores_stopwords(self):
        # "total" is a stopword, so these reduce to {revenue} vs {revenue}
        assert jaccard("total revenue", "revenue") == 1.0

    def test_jaccard_is_zero_for_disjoint_phrases(self):
        assert jaccard("net worth", "warehouse count") == 0.0

    def test_jaccard_handles_missing_values(self):
        assert jaccard(None, "revenue") == 0.0
        assert jaccard("", "") == 0.0

    def test_punctuation_does_not_break_matching(self):
        assert jaccard("shareholders' equity", "shareholders equity") == 1.0


class TestEntitySimilarity:
    def test_exact_subject_match(self):
        assert entity_similarity(_f("Delhivery", "a"), _f("Delhivery", "b")) == 1.0

    def test_substring_match_scores_high(self):
        """Real documents name one entity several ways."""
        score = entity_similarity(_f("Delhivery", "a"), _f("Delhivery Limited", "b"))
        assert score >= 0.8

    def test_case_insensitive(self):
        assert entity_similarity(_f("DELHIVERY", "a"), _f("delhivery", "b")) == 1.0

    def test_missing_subject_scores_zero(self):
        assert entity_similarity(_f(None, "a"), _f("Delhivery", "b")) == 0.0


class TestPromotionSignals:
    def test_strong_embedding_alone_promotes(self):
        reason = score_pair(_f("A", "x"), _f("B", "y"), embedding_score=0.85)
        assert reason.selected and "embedding" in reason.triggers

    def test_weak_embedding_alone_does_not_promote(self):
        reason = score_pair(_f("A", "x"), _f("B", "y"), embedding_score=0.10)
        assert not reason.selected

    def test_entity_plus_lexical_rescues_a_weak_embedding_pair(self):
        """Same company, overlapping metric wording, but the full
        statements embed poorly -- exactly the gap this layer exists for."""
        reason = score_pair(
            _f("Delhivery", "revenue from operations"),
            _f("Delhivery Limited", "revenue from services"),
            embedding_score=0.20,
        )
        assert reason.selected
        assert "entity+lexical" in reason.triggers

    def test_numeric_agreement_rescues_a_synonym_pair(self):
        """'Net worth' and 'total equity' share no vocabulary at all, so
        neither embeddings nor lexical overlap help. Identical normalized
        values are the signal that saves the pair."""
        reason = score_pair(
            _f("Delhivery", "net worth", 85466.74, "INR million"),
            _f("Delhivery", "total equity", 85466.74, "INR million"),
            embedding_score=0.15,
        )
        assert reason.selected
        assert "numeric" in reason.triggers
        assert reason.numeric_match

    def test_numeric_match_requires_entity_agreement(self):
        """Two unrelated companies both reporting 500 is a coincidence,
        not a candidate -- otherwise every repeated number in a document
        pairs with every other."""
        reason = score_pair(
            _f("Delhivery", "revenue", 500.0, "INR million"),
            _f("Blue Dart Express", "revenue", 500.0, "INR million"),
            embedding_score=0.10,
        )
        assert not reason.numeric_match

    def test_different_measurements_are_not_numerically_matched(self):
        """A headcount and a revenue figure sharing the number 500 aren't
        comparable, because their units don't reduce to a common base."""
        reason = score_pair(
            _f("Delhivery", "revenue", 500.0, "INR million"),
            _f("Delhivery", "headcount", 500.0, "people"),
            embedding_score=0.10,
        )
        assert not reason.numeric_match

    def test_differing_values_do_not_trigger_the_numeric_signal(self):
        reason = score_pair(
            _f("Delhivery", "net worth", 85466.74, "INR million"),
            _f("Delhivery", "total equity", 12000.0, "INR million"),
            embedding_score=0.10,
        )
        assert not reason.numeric_match

    def test_incomparable_units_do_not_trigger_the_numeric_signal(self):
        reason = score_pair(
            _f("Delhivery", "margin", 500.0, "%"),
            _f("Delhivery", "revenue", 500.0, "INR million"),
            embedding_score=0.10,
        )
        assert not reason.numeric_match

    def test_magnitude_suspect_pairs_do_not_trigger_numeric(self):
        """A value that agrees only because one side's unit was misread
        should not be treated as a confident numeric match."""
        reason = score_pair(
            _f("Delhivery", "total equity", 85466.74, "INR"),
            _f("Delhivery", "net worth", 85466.74, "INR million"),
            embedding_score=0.10,
        )
        # units differ by 1e6, so values do NOT agree once normalized
        assert not reason.numeric_match

    def test_facts_without_values_fall_back_to_other_signals(self):
        """Qualitative facts have no numbers; retrieval must still work."""
        reason = score_pair(
            _f("Mr. Sharma", "director independence declaration"),
            _f("Mr. Sharma", "director independence status"),
            embedding_score=0.30,
        )
        assert reason.selected  # entity+lexical carries it
        assert not reason.numeric_match


class TestReasonMetadata:
    def test_describe_names_the_triggering_signal(self):
        reason = score_pair(_f("A", "x"), _f("A", "x"), embedding_score=0.9)
        described = reason.describe()
        assert "embedding" in described
        assert "embedding=0.90" in described

    def test_unselected_pair_describes_as_none(self):
        reason = score_pair(_f("A", "x"), _f("B", "y"), embedding_score=0.0)
        assert reason.describe().startswith("none")

    def test_all_scores_are_recorded_even_when_not_triggering(self):
        """Scores are kept regardless of promotion so a *missed* pair can
        be diagnosed, not just a stored one."""
        reason = score_pair(_f("Delhivery", "revenue"), _f("Delhivery", "revenue"),
                            embedding_score=0.05)
        assert reason.entity_score == 1.0
        assert reason.lexical_score == 1.0
        assert reason.embedding_score == 0.05

    def test_threshold_boundary_is_inclusive(self):
        reason = score_pair(_f("A", "x"), _f("B", "y"), embedding_score=EMBEDDING_STRONG)
        assert "embedding" in reason.triggers
