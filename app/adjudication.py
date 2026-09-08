"""
Deterministic final adjudication between an LLM's proposed relationship
judgment and the mechanically verifiable facts computed elsewhere in the
pipeline (app/normalize.py's numeric comparison, app/context.py's period
and scope comparison).

This is the piece that was missing from the two-step classification in
app/relationships.py. Before this module existed, step 2 handed the model
a computed comparison as TEXT in a prompt ("these agree to 0.006%", "period:
DIFFERENT") and asked it to decide corroborates/contradicts/reconciled/
uncertain -- but nothing in code checked the answer against the facts it
had just been given. The model was told to trust them; nothing made it.
That gap is exactly how a broken prompt could once turn a clean 0.006%
agreement into a stored "contradicts", and it is why an unexplained
same-period, same-scope disagreement could just as easily come back
"reconciled" if the model felt like inventing a reason.

adjudicate() closes it: given the model's proposal and the SAME
deterministic facts the prompt already contains, it decides the FINAL
relation_type in code whenever those facts are conclusive, and only
defers to the model's own proposal when they aren't (genuinely
qualitative facts with no numeric dimension at all; values that happen to
agree while period or scope actively differ). "Conclusive" is deliberately
narrow -- see the numbered rules below -- so this never claims certainty
it doesn't have.

Every path preserves the model's original proposal and explanation in the
returned Adjudication, whether or not it was overridden. Disagreement is
recorded, never hidden: a reviewer can always see what the model said, what
the deterministic checks found, and which one the stored relation_type
actually followed.

One override is deliberately bold and is documented here rather than
softened: when period and scope are both POSITIVELY DETERMINED to be the
same (not merely unknown) and normalized values clearly differ, the result
is forced to "contradicts" even if the model proposed "reconciled" with a
plausible-sounding reason. This trades away a narrow case -- a genuine
reconciling reason stated only in free prose, never reflected in the
structured period/scope fields -- in exchange for removing the model's
ability to explain away a verified, unexplained numeric conflict. Given
this system exists specifically to surface disagreements between
documents, a false-negative reconciliation is a safer failure than a
false-negative contradiction.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.context import Comparison as ContextComparison, DIFFERENT, OVERLAPPING, SAME, UNKNOWN
from app.normalize import Comparison as ValueComparison

# A check fired and the model's proposal already matched it.
DETERMINISTIC_CONFIRMED = "deterministic_confirmed"
# A check fired and OVERRODE a different proposal from the model.
DETERMINISTIC_OVERRIDE = "deterministic_override"
# No deterministic check was conclusive; the final answer is the model's
# own, unchecked reading -- this is itself a meaningful, honestly-labeled
# uncertainty signal (see app/priority.py and the API), not a fallback to
# be ashamed of: it is exactly where the model's semantic judgment SHOULD
# be the final word (e.g. two qualitative, non-numeric facts).
LLM_UNCHECKED = "llm_unchecked"


@dataclass
class Adjudication:
    """The final, structured outcome of comparing an LLM's proposal against
    deterministic checks. Stored alongside a relationship so the reasoning
    behind relation_type is inspectable after the fact, not just asserted."""

    relation_type: str
    decision_source: str                     # one of the three constants above
    disagreement: bool
    disagreement_reason: Optional[str]
    llm_proposal: Optional[str]
    llm_explanation: str
    explanation: str
    reconciliation_context: Optional[str]
    confidence: Optional[float]
    checks: dict = field(default_factory=dict)

    def describe(self) -> str:
        if self.disagreement:
            return (
                f"{self.relation_type} [{self.decision_source}] -- overrides the model's "
                f"proposal of '{self.llm_proposal}': {self.disagreement_reason}"
            )
        return f"{self.relation_type} [{self.decision_source}]"


def _confidence_for(source: str, llm_confidence: Optional[float]) -> Optional[float]:
    """A deterministic verdict's confidence reflects certainty in a
    COMPUTED fact, not a model's self-report -- "100 crore equals 1
    billion" is not a 0.9-confidence statement, it is arithmetic. Only a
    decision resting entirely on the model's own reading (LLM_UNCHECKED)
    keeps the model's own confidence number, which is a stated opinion,
    not a calibrated probability -- a false corroborates edge was once seen
    at confidence 1.0 while the correct edge sat at 0.8, i.e. model
    confidence is not a reliable arbiter."""
    return 1.0 if source in (DETERMINISTIC_OVERRIDE, DETERMINISTIC_CONFIRMED) else llm_confidence


def _compose_explanation(deterministic_reason: str, llm_relation: Optional[str],
                          llm_explanation: str, disagreement: bool) -> str:
    if disagreement:
        if llm_explanation:
            return (
                f'{deterministic_reason} The model had proposed "{llm_relation}" '
                f'("{llm_explanation}"), but the deterministic check above is more reliable here.'
            )
        return deterministic_reason
    # Confirmed, or nothing to disagree with: the model's own explanation is
    # usually better-written (it cites the actual statement text) and,
    # having already been checked and found consistent, has nothing to
    # correct -- fall back to the deterministic reason only if the model
    # gave no explanation at all.
    return llm_explanation or deterministic_reason


def _synthesize_reconciliation(period: ContextComparison, scope: ContextComparison) -> str:
    """A fallback reconciling-context string for when a deterministic
    override lands on "reconciled" but the model didn't propose it (so
    never supplied its own reconciliation_context)."""
    parts = []
    if period.relation in (DIFFERENT, OVERLAPPING):
        parts.append(period.detail)
    if scope.relation == DIFFERENT:
        parts.append(scope.detail)
    return "; ".join(parts) if parts else "a difference in reporting period or scope"


def adjudicate(llm_result: dict, comparison: ValueComparison, period: ContextComparison,
               scope: ContextComparison, evidence_caveat: Optional[str], both_numeric: bool,
               slice_a: Optional[str] = None, slice_b: Optional[str] = None) -> Adjudication:
    """Decides the final relation_type for a pair already confirmed (by
    step 1's semantic match) to describe the same underlying metric.

    llm_result: the validated RelationJudgment dict from step 2 (or the
    safe default produced when step 2's response couldn't be validated --
    see app/relationships.py::_validated).
    comparison / period / scope: the SAME deterministic objects already
    formatted into the step-2 prompt (app/normalize.py, app/context.py).
    evidence_caveat: set when either fact's value isn't itself evidenced
    (app/relationships.py::_unverified_value_caveat) -- the deterministic
    comparison is only as trustworthy as the numbers feeding it.
    both_numeric: whether BOTH facts carry a value_numeric at all. This is
    what distinguishes "two numbers that don't reduce to a common unit"
    (related_but_not_comparable -- a structural fact) from "at least one
    fact has no number at all" (a qualitative claim, deferred to the
    model) -- both show up as comparison.comparable=False, but they call
    for different final answers.
    slice_a / slice_b: step 1's own stated slice for each fact (see
    SYSTEM_PROMPT_METRIC) -- carried through into the trace purely for
    inspectability; not used in any rule below.

    Rules are checked in order and the first that applies wins. Anything
    not covered defers entirely to the model's own proposal (LLM_UNCHECKED)
    -- deliberately: forcing an answer where the deterministic facts are
    genuinely inconclusive would be exactly the "confident answer the
    evidence doesn't support" this project exists to avoid.
    """
    llm_relation = llm_result.get("relation_type")
    llm_explanation = (llm_result.get("explanation") or "").strip()
    llm_confidence = llm_result.get("confidence")
    llm_reconciliation = llm_result.get("reconciliation_context")

    # A comparison is only a reliable "do these agree?" signal when it's
    # both computable AND not flagged as a probable unit-metadata artifact
    # AND not resting on an unevidenced value. Any of those three missing
    # makes agreement/disagreement itself unknown, not merely unconfirmed.
    reliable_agree = (
        comparison.agree
        if comparison.comparable and not comparison.magnitude_suspect and not evidence_caveat
        else None
    )

    checks = {
        "both_numeric": both_numeric,
        "comparable": comparison.comparable,
        "value_a": comparison.value_a, "value_b": comparison.value_b,
        "common_unit": comparison.common_unit, "diff_pct": comparison.diff_pct,
        "values_agree": comparison.agree, "reliable_agree": reliable_agree,
        "magnitude_suspect": comparison.magnitude_suspect,
        "period_relation": period.relation, "period_detail": period.detail,
        "scope_relation": scope.relation, "scope_detail": scope.detail,
        "scope_dimension": scope.dimension,
        "evidence_caveat": evidence_caveat,
        "slice_a": slice_a, "slice_b": slice_b,
    }

    def made(relation: str, source: str, reason: str, reconciliation: Optional[str] = None) -> Adjudication:
        disagreement = llm_relation is not None and relation != llm_relation
        return Adjudication(
            relation_type=relation,
            decision_source=source,
            disagreement=disagreement,
            disagreement_reason=reason if disagreement else None,
            llm_proposal=llm_relation,
            llm_explanation=llm_explanation,
            explanation=_compose_explanation(reason, llm_relation, llm_explanation, disagreement),
            reconciliation_context=reconciliation or llm_reconciliation,
            confidence=_confidence_for(source, llm_confidence),
            checks=checks,
        )

    # Rule 1 -- both facts state a number, but the units don't reduce to a
    # common base (or one is unparseable): a structural fact, independent
    # of what either side's number actually is.
    if both_numeric and not comparison.comparable:
        reason = (
            f"the two values cannot be safely compared ({comparison.reason}), so no "
            f"corroboration or contradiction can be asserted between them"
        )
        source = DETERMINISTIC_CONFIRMED if llm_relation == "related_but_not_comparable" else DETERMINISTIC_OVERRIDE
        return made("related_but_not_comparable", source, reason)

    # Rule 2 -- normalized values differ by orders of magnitude, the
    # signature of an incompletely recorded unit rather than a real
    # discrepancy (app/normalize.py). A confident contradiction here would
    # be a right-shaped answer for the wrong reason.
    if comparison.comparable and comparison.magnitude_suspect:
        reason = (
            f"the normalized values differ by orders of magnitude ({comparison.reason}), which "
            f"for the same metric usually means a unit was recorded incompletely rather than the "
            f"figures genuinely disagreeing -- not treated as a confident contradiction"
        )
        source = DETERMINISTIC_CONFIRMED if llm_relation == "uncertain" else DETERMINISTIC_OVERRIDE
        return made("uncertain", source, reason)

    # Rule 3 -- either value isn't itself evidenced. The comparison above is
    # only as trustworthy as the numbers feeding it, so a confident
    # corroborates/contradicts proposal resting on an unverified value is
    # capped at uncertain. A "reconciled" or "uncertain" proposal is left
    # alone here (already appropriately hedged); rules 4-7 below can't fire
    # regardless, since reliable_agree is None whenever evidence_caveat is set.
    if comparison.comparable and not comparison.magnitude_suspect and evidence_caveat and llm_relation in ("corroborates", "contradicts"):
        reason = f"{evidence_caveat} -- a confident '{llm_relation}' should not rest on an unverified value"
        return made("uncertain", DETERMINISTIC_OVERRIDE, reason)

    # Rule 4 -- values agree, and neither period nor scope actively
    # distinguishes the two facts (UNKNOWN doesn't count against
    # agreement the way it does against a disagreement -- see rule 7's
    # docstring note below).
    if reliable_agree is True and period.relation not in (DIFFERENT, OVERLAPPING) and scope.relation not in (DIFFERENT, OVERLAPPING):
        reason = (
            f"normalized values agree ({comparison.reason}, {comparison.diff_pct:g}% apart) for "
            f"the same reporting period and scope"
        )
        source = DETERMINISTIC_CONFIRMED if llm_relation == "corroborates" else DETERMINISTIC_OVERRIDE
        return made("corroborates", source, reason)

    # Rule 5 -- values clearly differ, and period and scope are both
    # POSITIVELY confirmed the same (not merely unknown). See the module
    # docstring for why this override is deliberately bold.
    if reliable_agree is False and period.relation == SAME and scope.relation == SAME:
        reason = (
            f"normalized values disagree by {comparison.diff_pct:g}% while period and scope are "
            f"both confirmed the same, so no context explains the gap"
        )
        source = DETERMINISTIC_CONFIRMED if llm_relation == "contradicts" else DETERMINISTIC_OVERRIDE
        return made("contradicts", source, reason)

    # Rule 6 -- values differ, and a period or scope difference is
    # positively confirmed (DIFFERENT), or the period is a well-defined
    # subdivision of the other (OVERLAPPING -- a quarter is not expected
    # to equal its own year). Both are confident, not merely absent,
    # explanations for a gap.
    if reliable_agree is False and (period.relation in (DIFFERENT, OVERLAPPING) or scope.relation == DIFFERENT):
        reconciliation = llm_reconciliation or _synthesize_reconciliation(period, scope)
        reason = f"values differ by {comparison.diff_pct:g}%, but {reconciliation}"
        source = DETERMINISTIC_CONFIRMED if llm_relation == "reconciled" else DETERMINISTIC_OVERRIDE
        return made("reconciled", source, reason, reconciliation=reconciliation)

    # Rule 7 -- values differ, but the evidence doesn't establish WHY:
    # period is unknown, or scope is unknown or only partially overlapping
    # (a weaker signal than a clean contrast -- see app/context.py). Unlike
    # rule 4, an UNKNOWN dimension here DOES count: a difference has to be
    # explained by something, and "we don't know" is not an explanation.
    if reliable_agree is False and (period.relation == UNKNOWN or scope.relation in (UNKNOWN, OVERLAPPING)):
        gap = period.detail if period.relation == UNKNOWN else scope.detail
        reason = (
            f"values differ by {comparison.diff_pct:g}%, but {gap} -- not enough context to tell "
            f"a contradiction from a reconciliation"
        )
        source = (
            DETERMINISTIC_CONFIRMED if llm_relation in ("uncertain", "insufficient_context")
            else DETERMINISTIC_OVERRIDE
        )
        return made("insufficient_context", source, reason)

    # Rule 8 -- nothing above applies: typically a qualitative fact pair
    # with no numeric dimension at all (comparable=False, both_numeric=
    # False), or values that agree while period or scope actively differs
    # (agreement across a confirmed difference is neither a clean
    # corroboration nor a clean anything-else). This is exactly where
    # language understanding, not arithmetic, has to make the call.
    final_relation = llm_relation or "uncertain"
    return Adjudication(
        relation_type=final_relation,
        decision_source=LLM_UNCHECKED,
        disagreement=False,
        disagreement_reason=None,
        llm_proposal=llm_relation,
        llm_explanation=llm_explanation,
        explanation=llm_explanation or "No deterministic check applies to this pair; relying on the model's own reading.",
        reconciliation_context=llm_reconciliation,
        confidence=llm_confidence,
        checks=checks,
    )
