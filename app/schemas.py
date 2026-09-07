"""
Pydantic schemas for everything an LLM returns.

The pipeline persists model output to SQLite, so a malformed-but-parseable
response is a data-integrity problem, not just a nuisance: JSON that parses
fine can still carry a missing quote, a confidence of 7.5, a relation_type
the code has never heard of, or a value_numeric that's actually a string.
Validating at the boundary means the database only ever sees shapes the
rest of the code can rely on, and anything rejected becomes a visible
extraction issue rather than a silent corruption.

Deliberately lenient where leniency is safe (coercing "12.7" to 12.7,
normalising the literal string "null" to None, clamping a confidence of
1.5 to 1.0) and strict where it isn't (a fact with no quote is dropped --
an unverifiable claim is exactly what this project exists not to store).
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

_NULLISH = {"null", "none", "n/a", "na", "-", ""}

RelationType = Literal["corroborates", "contradicts", "reconciled", "unrelated", "uncertain"]


def _nullish_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() in _NULLISH:
        return None
    return value


class ExtractedFact(BaseModel):
    """One fact as returned by the extraction model."""

    statement: str
    quote: str
    subject: Optional[str] = None
    attribute: Optional[str] = None
    value: Optional[str] = None
    value_numeric: Optional[float] = None
    unit: Optional[str] = None
    time_period: Optional[str] = None
    scope: Optional[str] = None
    confidence: Optional[float] = None

    @field_validator("subject", "attribute", "value", "unit", "time_period", "scope", mode="before")
    @classmethod
    def _clean_optional_text(cls, v):
        v = _nullish_to_none(v)
        if v is None:
            return None
        return str(v).strip() or None

    @field_validator("statement", "quote", mode="before")
    @classmethod
    def _require_text(cls, v):
        if v is None:
            raise ValueError("required field was null")
        return str(v).strip()

    @field_validator("value_numeric", mode="before")
    @classmethod
    def _coerce_number(cls, v):
        """Models return numbers as strings often enough that rejecting
        them outright would throw away good facts. Anything that isn't a
        real number becomes None rather than failing the whole fact --
        app/fact_extraction.py then falls back to parsing `value`."""
        v = _nullish_to_none(v)
        if v is None or isinstance(v, (int, float)):
            return v
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v):
        """A confidence outside 0..1 is a model error, not a reason to
        discard an otherwise good fact -- clamp it and move on."""
        v = _nullish_to_none(v)
        if v is None:
            return None
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return None

    @field_validator("quote")
    @classmethod
    def _quote_must_be_substantive(cls, v):
        if not v.strip():
            raise ValueError("quote was empty -- a fact with no evidence is not storable")
        return v


class MetricMatch(BaseModel):
    """Step 1 of relationship classification: same underlying metric?"""

    same_metric: bool
    reason: str = ""

    @field_validator("same_metric", mode="before")
    @classmethod
    def _coerce_bool(cls, v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"true", "yes", "y", "1"}:
                return True
            if s in {"false", "no", "n", "0"}:
                return False
        if isinstance(v, (int, float)):
            return bool(v)
        raise ValueError(f"could not interpret same_metric={v!r} as a boolean")

    @field_validator("reason", mode="before")
    @classmethod
    def _reason_text(cls, v):
        v = _nullish_to_none(v)
        return "" if v is None else str(v).strip()


class RelationJudgment(BaseModel):
    """Step 2 of relationship classification: how do they relate?"""

    relation_type: RelationType
    explanation: str = ""
    reconciliation_context: Optional[str] = None
    confidence: Optional[float] = None

    @field_validator("relation_type", mode="before")
    @classmethod
    def _normalise_relation(cls, v):
        """Local models drift on enum wording ("CORROBORATES",
        "contextually_reconciled", "contradiction"). Map the obvious
        variants; anything genuinely unrecognised becomes "uncertain"
        rather than being forced into a real relationship type -- the
        system is allowed to not know."""
        if v is None:
            return "uncertain"
        s = str(v).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "corroborate": "corroborates", "corroborated": "corroborates",
            "corroboration": "corroborates", "agrees": "corroborates", "agree": "corroborates",
            "contradict": "contradicts", "contradicted": "contradicts",
            "contradiction": "contradicts", "conflicts": "contradicts",
            "reconcile": "reconciled", "reconciles": "reconciled",
            "contextually_reconciled": "reconciled", "contextual_reconciliation": "reconciled",
            "unrelated": "unrelated", "not_related": "unrelated", "none": "unrelated",
            "uncertain": "uncertain", "unknown": "uncertain",
            "insufficient_evidence": "uncertain",
        }
        if s in {"corroborates", "contradicts", "reconciled", "unrelated", "uncertain"}:
            return s
        return aliases.get(s, "uncertain")

    @field_validator("explanation", mode="before")
    @classmethod
    def _explanation_text(cls, v):
        v = _nullish_to_none(v)
        return "" if v is None else str(v).strip()

    @field_validator("reconciliation_context", mode="before")
    @classmethod
    def _context_text(cls, v):
        v = _nullish_to_none(v)
        return None if v is None else str(v).strip() or None

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v):
        v = _nullish_to_none(v)
        if v is None:
            return None
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return None


def validate_facts(raw_items: list) -> tuple[list[dict], list[dict]]:
    """Validates a list of raw fact dicts from the extraction model.

    Returns (valid_facts, issues). A fact that fails validation is dropped
    and reported -- never silently repaired into something the model
    didn't actually say."""
    facts: list[dict] = []
    issues: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            issues.append({
                "issue_type": "malformed_fact",
                "detail": f"Expected a JSON object per fact, got {type(item).__name__}",
                "raw_excerpt": str(item)[:500],
            })
            continue
        try:
            facts.append(ExtractedFact.model_validate(item).model_dump())
        except ValidationError as exc:
            issues.append({
                "issue_type": "malformed_fact",
                "detail": f"Fact failed schema validation: {exc.errors()[0].get('msg', 'invalid')}",
                "raw_excerpt": str(item)[:500],
            })
    return facts, issues
