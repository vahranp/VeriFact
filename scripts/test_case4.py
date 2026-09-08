"""Directly surface Case 4: real extraction and reasoning failures, found
and reported rather than hidden.

Unlike cases 1-3, "success" here is not one relationship_type -- it's that
the failure is actually recorded and inspectable. Two independent
mechanisms are demonstrated, both already running as part of the normal
pipeline (nothing here is a special code path):

  1. Evidence validation (app/evidence.py) catching a fact whose quote is
     genuinely on the page but does not itself support the claimed value
     -- typically a table row label separated from its number by PDF text
     flattening. Queried from extraction_issues, not asserted.
  2. Graph coherence (app/coherence.py) proving a triangle of relationship
     judgments cannot all be correct -- a logical inconsistency found with
     no ground truth, no human reviewer, and no extra model call.

Like the other three scripts, everything here is queried by content/
structure from whatever is actually in the database, never hardcoded to a
specific fact or document id.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.coherence import check_coherence


def out(s):
    sys.stdout.write(str(s).encode("ascii", "replace").decode() + "\n")


def main():
    found_anything = False

    # --- 1. Evidence validation catching an unsupported value ----------
    out("=" * 70)
    out("CASE 4a -- evidence validation: quote real, value not supported")
    out("=" * 70)
    value_issues = [i for i in db.list_issues() if i["issue_type"] == "value_not_evidenced"]
    if value_issues:
        found_anything = True
        issue = value_issues[0]
        doc = db.get_document(issue["document_id"]) if issue.get("document_id") else None
        out(f"document: {doc['original_name'] if doc else issue.get('document_id')}, page {issue.get('page_number')}")
        out(f"detail: {issue['detail']}")
        out(f"quote on the page (verbatim, but doesn't support the value): \"{issue.get('raw_excerpt', '')[:200]}\"")
        out(f"({len(value_issues)} such issues total in the current corpus)")
    else:
        out("No value_not_evidenced issues currently recorded -- ingest a document with dense")
        out("tabular content (e.g. the BRSR/annual-report pages) to reproduce this failure class.")

    # --- 2. Graph coherence proving a logical inconsistency -------------
    out("")
    out("=" * 70)
    out("CASE 4b -- graph coherence: a logically impossible triangle")
    out("=" * 70)
    relationships = db.list_relationships()
    facts_by_id = {f["id"]: f for f in db.list_facts()}
    report = check_coherence(relationships, facts_by_id=facts_by_id)
    if report.violations:
        found_anything = True
        v = report.violations[0]
        out(report.summary())
        out("")
        out(v.describe())
        out(f"reason: {v.reason}")
        for fid in v.fact_ids:
            f = facts_by_id.get(fid)
            if f:
                out(f"  fact {fid}: {f.get('statement')}")
    else:
        out("No coherence violations in the current corpus -- ingest more documents to increase")
        out("the chance of a closed, inconsistent triangle (see PERFORMANCE.md for a measured example).")

    out("")
    out("=" * 70)
    out(f"CASE 4 -- {'PASS' if found_anything else 'FAIL'} "
        f"(at least one real, surfaced failure {'was' if found_anything else 'was NOT'} found)")
    out("=" * 70)
    return 0 if found_anything else 1


if __name__ == "__main__":
    sys.exit(main())
