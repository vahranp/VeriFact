"""Directly verify the Case 2 classification: BRSR net worth
(Rs85,466.74 million) vs. Consolidated Balance Sheet total equity
(Rs91,446.46 million) -- for a company these name the same underlying
concept, and the ~6.5% gap has no explanation stated in either source.

Both facts are real, DB-backed extractions. The total-equity fact
(facts.id=174) originally carried two real extraction gaps: unit stored
as "INR" instead of "INR million", and time_period unset. Both were
corrected as a one-time, VERIFIED data fix (not a guess): the balance
sheet's own page header states "(All amounts in Indian Rupees in
million, unless otherwise stated)", and the column this figure sits
under is explicitly headed "As at March 31, 2024" (see
data page 68, "Consolidated Balance Sheet as at March 31, 2024"). Fixing
these two, and only these two, moved the comparison from a ~1,000,000x
magnitude-suspect artifact (the previous, correctly-cautious `uncertain`
result) to a real, materially different comparison.

What no source states, for either fact, is an explicit scope
(standalone vs. consolidated) -- so app/adjudication.py's rule 5
(a confirmed CONTRADICTS) cannot fire; that rule specifically requires
scope to be POSITIVELY confirmed the same, not merely unstated on both
sides, precisely so a stated-but-unverifiable assumption is never
substituted for a fact neither document actually asserts. The real,
reproducible, deterministic result is INSUFFICIENT_CONTEXT -- and it is
the more interesting result anyway, because it is a genuine, live
disagreement: the model confidently proposes "contradicts" off a real
6.5% gap at the same reported period, but the deterministic layer knows
it cannot confirm whether "net worth" and "total equity" were reported
on the same basis here, and overrides rather than asserting a conflict
it cannot back up.

This is a hard regression assertion, not merely printed output: the
script exits non-zero if the real, live classification stops matching
this documented, reproducible behavior."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

db.init_db()

nw_candidates = [
    f for f in db.list_facts()
    if "net worth" in (f.get("statement") or "").lower() and f.get("value_numeric") is not None
]
assert nw_candidates, "no net worth fact with a populated value_numeric found"
net_worth = nw_candidates[-1]  # most recently inserted

candidates = [
    f for f in db.list_facts()
    if (f.get("attribute") or "").strip().lower() == "total equity" and f.get("value_numeric") is not None
]
assert candidates, "no fact with attribute exactly 'total equity' and a populated value_numeric found"
total_equity = candidates[-1]  # most recently inserted, in case of re-extractions


def _safe(line):
    print(line.encode("ascii", "replace").decode())


_safe(f"net_worth:    doc={net_worth['document_id']} id={net_worth['id']} value_numeric={net_worth['value_numeric']} "
      f"unit={net_worth['unit']} period={net_worth['time_period']} scope={net_worth['scope']}")
_safe(f"total_equity: doc={total_equity['document_id']} id={total_equity['id']} value_numeric={total_equity['value_numeric']} "
      f"unit={total_equity['unit']} period={total_equity['time_period']} scope={total_equity['scope']}")

result, cache_hit = classify_pair(
    net_worth, "annual_report.pdf",
    total_equity, "annual_report.pdf",
)
print(f"\ncache_hit={cache_hit}")
print("relation_type:", result.get("relation_type"))
print("confidence:", result.get("confidence"))
print("decision_source:", result.get("decision_source"))
print("llm_proposal:", result.get("llm_proposal"))
print("disagreement:", result.get("disagreement"), "--", result.get("disagreement_reason"))
print("explanation:", result.get("explanation", "").encode("ascii", "replace").decode())
print("reconciliation_context:", result.get("reconciliation_context"))
print("checks:", result.get("adjudication_checks"))
print("meta:", result.get("_meta"))

# --- Hard regression assertions -------------------------------------------
# Both values are now verified-comparable (same unit, same period) and
# genuinely differ by ~6.5% -- not a magnitude/unit artifact. Scope is
# unstated on both sides, so the deterministic layer must not assert a
# confirmed contradiction it cannot back up.
checks = result.get("adjudication_checks") or {}
assert checks.get("magnitude_suspect") is False, (
    f"expected the unit fix to remove the magnitude-suspect flag, got {checks.get('magnitude_suspect')!r}"
)
assert checks.get("period_relation") == "same", (
    f"expected both facts' FY24 period to resolve as SAME, got {checks.get('period_relation')!r}"
)
assert 5.0 < (checks.get("diff_pct") or 0) < 10.0, (
    f"expected a real ~6.5% gap (not near-zero, not magnitude-suspect), got diff_pct={checks.get('diff_pct')!r}"
)
assert result.get("relation_type") == "insufficient_context", (
    f"expected insufficient_context (scope unstated on both sides blocks a confirmed contradiction), "
    f"got {result.get('relation_type')!r}"
)
assert result.get("decision_source") == "deterministic_override", (
    f"expected the deterministic layer to override the LLM's proposal, got decision_source={result.get('decision_source')!r}"
)
assert result.get("llm_proposal") == "contradicts", (
    f"expected the model to have confidently proposed 'contradicts' (the interesting disagreement case), "
    f"got llm_proposal={result.get('llm_proposal')!r}"
)
assert result.get("disagreement") is True, "expected disagreement=True: the model and the system land on different answers here"

print("\nPASS -- Case 2 is deterministic and reproducible: a real ~6.5% gap, same period, "
      "the model confidently says 'contradicts', and the system correctly overrides it to "
      "'insufficient_context' because neither source states a scope.")
