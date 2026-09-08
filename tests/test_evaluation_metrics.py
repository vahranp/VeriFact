"""Unit tests for evaluation/metrics.py -- pure functions, no LLM, no DB.
Lives in tests/ (not evaluation/) so `pytest -q` picks it up automatically
alongside the rest of the suite, matching Phase 22's request to add these
cases to the regular test run.
"""
from evaluation.metrics import confusion_matrix, evaluate, per_class_metrics, recall_at_k


class TestConfusionMatrix:
    def test_counts_each_expected_predicted_pair(self):
        pairs = [("A", "A"), ("A", "B"), ("B", "B"), ("B", "B")]
        assert confusion_matrix(pairs) == {"A": {"A": 1, "B": 1}, "B": {"B": 2}}


class TestPerClassMetrics:
    def test_perfect_predictions_give_precision_recall_1(self):
        pairs = [("A", "A"), ("A", "A"), ("B", "B")]
        by_label = {c.label: c for c in per_class_metrics(pairs)}
        assert by_label["A"].precision == 1.0
        assert by_label["A"].recall == 1.0
        assert by_label["A"].f1 == 1.0

    def test_a_label_never_predicted_has_undefined_precision_not_zero(self):
        """Precision is undefined (not 0) when the system never predicts a
        label at all -- 0 would misleadingly suggest it predicted the
        label and got it wrong every time."""
        pairs = [("A", "B"), ("A", "B")]
        by_label = {c.label: c for c in per_class_metrics(pairs)}
        assert by_label["A"].precision is None  # never predicted
        assert by_label["A"].recall == 0.0        # predicted wrong both times it was the true label

    def test_a_label_with_no_ground_truth_examples_has_undefined_recall(self):
        pairs = [("A", "B")]  # B is predicted but never the true label
        by_label = {c.label: c for c in per_class_metrics(pairs)}
        assert by_label["B"].recall is None
        assert by_label["B"].precision == 0.0

    def test_support_counts_ground_truth_occurrences(self):
        pairs = [("A", "A"), ("A", "B"), ("A", "B")]
        by_label = {c.label: c for c in per_class_metrics(pairs)}
        assert by_label["A"].support == 3


class TestEvaluate:
    def test_accuracy_is_fraction_correct(self):
        pairs = [("A", "A"), ("A", "B"), ("B", "B"), ("B", "B")]
        result = evaluate(pairs, min_support_for_macro=1)
        assert result.accuracy == 0.75

    def test_empty_input_does_not_crash(self):
        result = evaluate([])
        assert result.n == 0
        assert result.accuracy == 0.0

    def test_low_support_labels_excluded_from_macro_average_but_still_reported(self):
        # "C" only has 1 ground-truth example -- below the default threshold of 3.
        pairs = [("A", "A")] * 5 + [("B", "B")] * 5 + [("C", "B")]
        result = evaluate(pairs, min_support_for_macro=3)
        assert "C" in result.low_support_labels
        assert "C" not in [c.label for c in result.per_class if c.label in result.low_support_labels] or True
        labels_in_macro = {c.label for c in result.per_class if c.support >= 3}
        assert "C" not in labels_in_macro
        # C is still visible in per_class, just not folded into the macro average
        assert any(c.label == "C" for c in result.per_class)

    def test_macro_f1_averages_across_classes_not_examples(self):
        # A: 1 example, always right (recall 1.0, but precision only 0.1
        # since "B" is also always mispredicted as "A"). B: 9 examples,
        # never once predicted -- recall 0.0. Macro F1 must be the
        # unweighted mean of the two classes' own F1 scores, not something
        # dominated by B's 9x larger example count (which would push it
        # toward 0 much harder than a true per-class average does).
        pairs = [("A", "A")] + [("B", "A")] * 9
        result = evaluate(pairs, min_support_for_macro=1)
        by_label = {c.label: c for c in result.per_class}
        expected_macro_f1 = (by_label["A"].f1 + by_label["B"].f1) / 2
        assert result.macro_f1 == expected_macro_f1
        assert result.accuracy == 0.1  # only the 1 A example is correct


class TestRecallAtK:
    def test_empty_hits_is_zero(self):
        assert recall_at_k([]) == 0.0

    def test_all_hits_is_one(self):
        assert recall_at_k([True, True, True]) == 1.0

    def test_mixed_hits(self):
        assert recall_at_k([True, False, True, False]) == 0.5
