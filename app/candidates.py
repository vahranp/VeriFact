"""
Hybrid candidate retrieval.

Deciding which fact pairs are worth an expensive LLM judgment is the step
that makes this system affordable -- comparing every pair is O(n^2) LLM
calls and never finishes. Embedding similarity alone did that job, and
mostly did it well, but it has a measured blind spot: "net worth" and
"total equity" are the same accounting concept and score only 0.41 cosine
similarity, because they share almost no words. Lowering the global
threshold to catch pairs like that admits noise for everyone else.

So instead of one threshold on one signal, this combines several cheap,
local, deterministic signals, any of which can independently promote a
pair to "worth asking the model about":

  embedding    -- semantic similarity of the two statements (as before)
  entity       -- do they describe the same subject?
  lexical      -- token overlap on the attribute, which catches
                  "revenue from operations" vs "revenue from services"
                  where embeddings of the full statement may drift apart
  numeric      -- do the values coincide after unit normalization? Two
                  facts stating the same number in the same unit are worth
                  looking at even if they're worded completely differently.
                  This is the signal that rescues the net-worth/total-equity
                  case without loosening anything globally.

Every candidate carries the reason it was selected, so a missed or
spurious relationship can be traced back to the signal responsible
instead of being an unexplained gap.

No LLM calls happen here. This is all numpy, string ops and arithmetic.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.normalize import compare_values

# A pair is promoted if ANY signal fires. Thresholds are per-signal and
# deliberately independent -- the point is that a strong signal on one
# axis shouldn't need corroboration from a weak one.
EMBEDDING_STRONG = 0.40      # matches the previously tuned global threshold
# One shared content word out of three ("revenue from operations" vs
# "revenue from services" -> 1/3) is the canonical shape of the pairs this
# signal exists to rescue, so the bar sits just below it.
ENTITY_LEXICAL_MIN = 0.30
NUMERIC_TOLERANCE_PCT = 2.0  # values agreeing this closely is itself a signal

# Attribute overlap NEVER promotes on its own. Benchmarked on 304 real
# facts, a standalone lexical trigger at 0.60 added 1235 candidates that
# were almost entirely junk: annual reports label dozens of *different*
# people with the identical attribute "Director status", so lexical
# scored 1.00 while entity scored 0.00-0.17. Attribute similarity only
# means anything once the subject already matches, which is what the
# entity+lexical path requires.

_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or", "is", "was",
    "were", "as", "by", "with", "from", "its", "their", "this", "that", "total",
}


def _tokens(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(text))
    return {t for t in cleaned.split() if t and t not in _STOPWORDS and len(t) > 1}


def jaccard(a: Optional[str], b: Optional[str]) -> float:
    """Token overlap, stopwords removed. Cheap proxy for 'these phrases
    are talking about the same thing' that behaves differently from
    embeddings -- it rewards literal shared vocabulary, which is exactly
    what embeddings of long statements can wash out."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def entity_similarity(fact_a: dict, fact_b: dict) -> float:
    """How likely the two facts are about the same subject. Kept as a
    similarity rather than a boolean because real documents refer to one
    entity many ways ('Delhivery', 'Delhivery Limited', 'the Company',
    'your Company')."""
    a, b = (fact_a.get("subject") or "").strip().lower(), (fact_b.get("subject") or "").strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:          # 'delhivery' vs 'delhivery limited'
        return 0.85
    return jaccard(a, b)


@dataclass
class CandidateReason:
    """Why a pair was selected -- attached to every candidate so a
    relationship (or its absence) is explainable after the fact."""

    embedding_score: float = 0.0
    entity_score: float = 0.0
    lexical_score: float = 0.0
    numeric_match: bool = False
    numeric_detail: str = ""
    triggers: list[str] = field(default_factory=list)

    @property
    def selected(self) -> bool:
        return bool(self.triggers)

    def describe(self) -> str:
        parts = [
            f"embedding={self.embedding_score:.2f}",
            f"entity={self.entity_score:.2f}",
            f"lexical={self.lexical_score:.2f}",
        ]
        if self.numeric_match:
            parts.append(f"numeric_match({self.numeric_detail})")
        return f"{'+'.join(self.triggers) or 'none'} [{', '.join(parts)}]"


def score_pair(fact_a: dict, fact_b: dict, embedding_score: float) -> CandidateReason:
    """Scores one pair across all signals. Pure function -- no IO."""
    reason = CandidateReason(embedding_score=embedding_score)

    reason.entity_score = entity_similarity(fact_a, fact_b)
    reason.lexical_score = jaccard(fact_a.get("attribute"), fact_b.get("attribute"))

    cmp = compare_values(
        fact_a.get("value_numeric"), fact_a.get("unit"),
        fact_b.get("value_numeric"), fact_b.get("unit"),
        tolerance_pct=NUMERIC_TOLERANCE_PCT,
    )
    # A coincidental numeric match between two facts about different
    # entities isn't interesting, so this signal requires some entity
    # agreement before it fires -- otherwise every "1.4" in a document
    # would pair with every other "1.4".
    if cmp.comparable and cmp.agree and not cmp.magnitude_suspect and reason.entity_score >= 0.5:
        reason.numeric_match = True
        reason.numeric_detail = f"{cmp.value_a:g}~{cmp.value_b:g} {cmp.common_unit}"

    if embedding_score >= EMBEDDING_STRONG:
        reason.triggers.append("embedding")
    if reason.entity_score >= 0.5 and reason.lexical_score >= ENTITY_LEXICAL_MIN:
        reason.triggers.append("entity+lexical")
    if reason.numeric_match:
        reason.triggers.append("numeric")

    return reason
