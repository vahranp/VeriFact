"""
Response models for the HTTP API.

These exist for the OpenAPI documentation at /docs, so a reviewer can see
the shape of every endpoint without reading the handlers. They are
deliberately permissive (`extra="allow"`): the UI consumes fields the
models don't enumerate, and a strict model would silently strip them,
turning a documentation improvement into a functional regression. Each
model names the fields a client can rely on, not an exhaustive schema.
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Permissive(BaseModel):
    model_config = ConfigDict(extra="allow")


class UploadAccepted(_Permissive):
    id: int
    status: str = Field(description="pending | done -- 'done' when identical content was reused")
    pages_to_process: Optional[int] = None
    reused_document_id: Optional[int] = Field(
        default=None, description="Set when byte-identical content processed by the same pipeline version already existed."
    )
    warning: Optional[str] = None


class DocumentOut(_Permissive):
    id: int
    original_name: str
    status: Literal["pending", "processing", "done", "failed", "cancelled"]
    num_pages: Optional[int] = None
    error_message: Optional[str] = None
    page_selector: Optional[str] = None
    progress: Optional[dict] = None
    stats: Optional[dict] = None
    cancel_requested: Optional[bool] = Field(
        default=None,
        description="True once a stop has been requested but the pipeline hasn't yet noticed -- "
                    "cancellation is cooperative, checked between chunks/candidate pairs.",
    )


class FactOut(_Permissive):
    id: int
    document_id: int
    page_number: int
    statement: str
    quote: str = Field(description="Verbatim excerpt from the source page.")
    quote_grounded: bool = Field(description="The quote was found verbatim in the source.")
    evidence_status: Optional[Literal["fact_validated", "quote_grounded", "ungrounded"]] = Field(
        default=None,
        description=(
            "Two-level grounding. 'quote_grounded' means the text is real but does not "
            "support the extracted value; 'fact_validated' means it does. See app/evidence.py."
        ),
    )
    evidence_detail: Optional[str] = None
    subject: Optional[str] = None
    attribute: Optional[str] = None
    value: Optional[str] = None
    value_numeric: Optional[float] = None
    unit: Optional[str] = None
    time_period: Optional[str] = None
    scope: Optional[str] = None
    confidence: Optional[float] = None


class NormalizedComparisonOut(_Permissive):
    comparable: bool = Field(description="Both facts had a numeric value and a unit that could be reduced to a common base.")
    agree: Optional[bool] = Field(default=None, description="Within tolerance once normalized. None when not comparable.")
    diff_pct: Optional[float] = None
    common_unit: Optional[str] = None
    magnitude_suspect: bool = Field(
        default=False,
        description="Values differ by an order-of-magnitude ratio typical of an incompletely recorded unit "
                    "(e.g. one side in 'million', the other missing that scale) rather than a genuine disagreement.",
    )
    value_a: Optional[float] = None
    value_b: Optional[float] = None


class RelationshipOut(_Permissive):
    id: int
    fact_id_a: int
    fact_id_b: int
    relation_type: Literal["corroborates", "contradicts", "reconciled", "uncertain"]
    explanation: Optional[str] = None
    reconciliation_context: Optional[str] = None
    confidence: Optional[float] = None
    similarity_score: Optional[float] = None
    candidate_reason: Optional[str] = Field(
        default=None, description="Which retrieval signal promoted this pair for judgment."
    )
    fact_a: Optional[FactOut] = None
    fact_b: Optional[FactOut] = None
    normalized_comparison: Optional[NormalizedComparisonOut] = Field(
        default=None,
        description="Deterministic numeric comparison recomputed at read time from app/normalize.py, "
                    "independent of the relation_type judgment -- surfaces cases like magnitude_suspect "
                    "that the UI should explain rather than silently drop.",
    )


class IssueOut(_Permissive):
    id: int
    document_id: Optional[int] = None
    page_number: Optional[int] = None
    issue_type: str
    detail: Optional[str] = None
    raw_excerpt: Optional[str] = None


class StatsOut(_Permissive):
    documents: int
    documents_done: int
    facts: int
    ungrounded_facts: int
    fact_validated: int
    quote_grounded_only: int
    relationships: int
    relationships_by_type: dict[str, int]
    issues: int


class CoherenceOut(_Permissive):
    summary: str
    nodes: int
    edges: int
    triangles_checked: int
    violation_rate: float
    violations_total: int
    implicated_edges: int
    inferences_total: int
    violations: list[Any]
    inferences: list[Any]


class PriorityOut(_Permissive):
    facts: list[Any] = Field(
        description="Facts ranked most-urgent-first: level (critical/high/medium/low), "
                    "score, and the specific reasons behind it."
    )
    relationships: list[Any] = Field(
        description="Relationships ranked the same way -- a coherence-proven error always "
                    "outranks a mere disagreement, which outranks an already-explained one."
    )
    level_counts: dict = Field(description="How many facts fall into each priority level.")


class TimelineOut(_Permissive):
    timelines: list[Any] = Field(
        description="Metric families discovered by following corroborates/reconciled "
                    "relationships across distinct periods -- e.g. revenue FY22->FY23->FY24. "
                    "No new extraction; derived entirely from relationships already stored."
    )
    count: int
