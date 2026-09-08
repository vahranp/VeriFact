"""Pure metric functions: confusion matrix, precision/recall/F1. No I/O, no
app/ imports -- takes (expected, predicted) label pairs and returns numbers.
Kept separate so it can be unit-tested on its own (see
tests/test_evaluation_metrics.py) independent of anything that calls a
live LLM.
"""
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ClassMetrics:
    label: str
    support: int          # how many ground-truth examples of this label exist
    predicted_count: int  # how many times the system predicted this label
    true_positives: int
    precision: float | None   # None when predicted_count == 0 (undefined, not zero)
    recall: float | None      # None when support == 0 (no ground-truth examples)
    f1: float | None


def confusion_matrix(pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """pairs: [(expected_label, predicted_label), ...].
    Returns {expected_label: {predicted_label: count}}."""
    matrix: dict[str, dict[str, int]] = {}
    for expected, predicted in pairs:
        matrix.setdefault(expected, Counter())[predicted] += 1
    return {k: dict(v) for k, v in matrix.items()}


def confusion_matrix_markdown(pairs: list[tuple[str, str]]) -> str:
    labels = sorted({e for e, _ in pairs} | {p for _, p in pairs})
    matrix = confusion_matrix(pairs)
    header = "| expected \\ predicted | " + " | ".join(labels) + " |"
    sep = "|---|" + "|".join(["---:"] * len(labels)) + "|"
    lines = [header, sep]
    for e in labels:
        row = matrix.get(e, {})
        lines.append("| **" + e + "** | " + " | ".join(str(row.get(p, 0)) for p in labels) + " |")
    return "\n".join(lines)


def per_class_metrics(pairs: list[tuple[str, str]]) -> list[ClassMetrics]:
    labels = sorted({e for e, _ in pairs} | {p for _, p in pairs})
    support = Counter(e for e, _ in pairs)
    predicted_count = Counter(p for _, p in pairs)
    tp = Counter(e for e, p in pairs if e == p)

    out = []
    for label in labels:
        sup = support.get(label, 0)
        pred = predicted_count.get(label, 0)
        t = tp.get(label, 0)
        precision = (t / pred) if pred > 0 else None
        recall = (t / sup) if sup > 0 else None
        if precision is not None and recall is not None and (precision + recall) > 0:
            f1 = 2 * precision * recall / (precision + recall)
        elif precision == 0 or recall == 0:
            f1 = 0.0
        else:
            f1 = None
        out.append(ClassMetrics(label, sup, pred, t, precision, recall, f1))
    return out


@dataclass
class OverallMetrics:
    n: int
    accuracy: float
    macro_precision: float
    macro_recall: float
    macro_f1: float
    per_class: list[ClassMetrics] = field(default_factory=list)
    low_support_labels: list[str] = field(default_factory=list)  # support < min_support


def evaluate(pairs: list[tuple[str, str]], min_support_for_macro: int = 3) -> OverallMetrics:
    """min_support_for_macro: labels with fewer ground-truth examples than
    this are still scored individually (returned in per_class) but excluded
    from the macro average and listed in low_support_labels, since a
    precision/recall computed from 1-2 examples isn't statistically
    meaningful -- reporting it unlabeled would be misleading, not
    reporting it at all would hide a real class exists."""
    if not pairs:
        return OverallMetrics(0, 0.0, 0.0, 0.0, 0.0, [], [])

    n = len(pairs)
    correct = sum(1 for e, p in pairs if e == p)
    accuracy = correct / n

    per_class = per_class_metrics(pairs)
    eligible = [c for c in per_class if c.support >= min_support_for_macro]
    low_support = [c.label for c in per_class if 0 < c.support < min_support_for_macro]

    def _avg(values):
        vals = [v for v in values if v is not None]
        return sum(vals) / len(vals) if vals else 0.0

    macro_precision = _avg(c.precision for c in eligible)
    macro_recall = _avg(c.recall for c in eligible)
    macro_f1 = _avg(c.f1 for c in eligible)

    return OverallMetrics(n, accuracy, macro_precision, macro_recall, macro_f1, per_class, low_support)


def recall_at_k(hits: list[bool]) -> float:
    """hits: for each labelled pair, whether the correct candidate was
    present in the top-K retrieval result. A plain mean, pulled out as a
    named function so callers/tests don't reimplement it slightly
    differently each time."""
    if not hits:
        return 0.0
    return sum(1 for h in hits if h) / len(hits)
