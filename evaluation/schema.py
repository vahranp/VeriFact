"""Shared vocabulary and helpers for the evaluation package.

Kept separate from app/schemas.py deliberately: this describes the
BENCHMARK's ground-truth categories, which are a superset/relabeling of
the production Literal types for evaluation purposes (e.g. distinguishing
EXACT_CORROBORATION -- a deterministically-verified equality -- from a
merely LLM-judged "corroborates"), not a schema the pipeline itself uses.
"""
from dataclasses import dataclass, field
from typing import Optional

# The benchmark's target categories (brief's Phase 3/9 taxonomy).
EXACT_CORROBORATION = "EXACT_CORROBORATION"
CONTRADICTION = "CONTRADICTION"
CONTEXT_RECONCILIATION = "CONTEXT_RECONCILIATION"
UNRELATED = "UNRELATED"
INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
RELATED_NOT_COMPARABLE = "RELATED_NOT_COMPARABLE"
UNCERTAIN = "UNCERTAIN"

RELATIONSHIP_CATEGORIES = [
    EXACT_CORROBORATION, CONTRADICTION, CONTEXT_RECONCILIATION,
    UNRELATED, INSUFFICIENT_CONTEXT, RELATED_NOT_COMPARABLE, UNCERTAIN,
]

# Maps a live app.relationships.classify_pair() result onto the benchmark's
# category names. "corroborates" only counts as the strict EXACT_CORROBORATION
# category when a deterministic check actually confirmed it (decision_source
# in the *_confirmed/*_override family) -- an llm_unchecked "corroborates"
# (a qualitative status match with no numeric check possible) is real
# corroboration but not a *verified numeric equality*, so it's kept in its
# own bucket rather than conflated with the deterministically-proven case.
LLM_UNCHECKED_CORROBORATES = "SEMANTIC_CORROBORATION"
RELATIONSHIP_CATEGORIES.append(LLM_UNCHECKED_CORROBORATES)

_RELATION_TYPE_TO_CATEGORY = {
    "contradicts": CONTRADICTION,
    "reconciled": CONTEXT_RECONCILIATION,
    "unrelated": UNRELATED,
    "insufficient_context": INSUFFICIENT_CONTEXT,
    "related_but_not_comparable": RELATED_NOT_COMPARABLE,
    "uncertain": UNCERTAIN,
}


def category_for_result(relation_type: str, decision_source: Optional[str]) -> str:
    """The one place this mapping happens, so benchmark.py and any report
    that reads results agree on it."""
    if relation_type == "corroborates":
        if decision_source in ("deterministic_confirmed", "deterministic_override"):
            return EXACT_CORROBORATION
        return LLM_UNCHECKED_CORROBORATES
    return _RELATION_TYPE_TO_CATEGORY.get(relation_type, relation_type.upper())


# ---------------------------------------------------------------- facts --

_DOC_COUNTER = {"n": -1000}


def make_fact(
    subject: str, attribute: str, value: str, value_numeric: Optional[float] = None,
    unit: Optional[str] = None, time_period: Optional[str] = None, scope: Optional[str] = None,
    statement: Optional[str] = None, quote: Optional[str] = None,
    evidence_status: str = "fact_validated", page_number: int = 1,
) -> dict:
    """Fills in the boilerplate app.relationships.classify_pair() actually
    reads (page_number, id, evidence_status) so benchmark case definitions
    only have to state what's semantically relevant to that case. Every
    call gets a distinct negative id -- negative so it can never collide
    with a real facts.id from the live database."""
    _DOC_COUNTER["n"] -= 1
    return {
        "id": _DOC_COUNTER["n"],
        "page_number": page_number,
        "subject": subject,
        "attribute": attribute,
        "value": value,
        "value_numeric": value_numeric,
        "unit": unit,
        "time_period": time_period,
        "scope": scope,
        "statement": statement or f"{subject} {attribute} was {value} {unit or ''} ({time_period or 'period unstated'}).".replace("  ", " "),
        "quote": quote if quote is not None else f"{value} {unit or ''}".strip(),
        "evidence_status": evidence_status,
    }


@dataclass
class BenchmarkCase:
    id: str
    category: str                    # expected ground-truth category (RELATIONSHIP_CATEGORIES)
    provenance: str                  # "constructed" | "real_mined" | "deterministic_only"
    fact_a: dict
    fact_b: dict
    notes: str = ""
    trap: Optional[str] = None       # which named failure-mode this targets, if any
    source_relationship_id: Optional[int] = None  # for real_mined: the live DB row it came from
    run_live: bool = True            # whether run_benchmark.py should call the real LLM
    recorded: Optional[dict] = None  # for real_mined: the already-computed real result

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "provenance": self.provenance,
            "trap": self.trap,
            "notes": self.notes,
            "source_relationship_id": self.source_relationship_id,
            "run_live": self.run_live,
            "fact_a": self.fact_a,
            "fact_b": self.fact_b,
            "recorded": self.recorded,
        }
