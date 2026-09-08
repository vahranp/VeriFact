"""Recomputes evidence_status/evidence_detail for facts already in the DB.

The evidence check (app/evidence.py) is deterministic, so it can be applied
retroactively to facts extracted before it existed -- no LLM calls, no
re-ingest. Re-runnable: running it again after changing the evidence rules
brings stored facts back in line with the current definition.

    python scripts/backfill_evidence.py            # report only
    python scripts/backfill_evidence.py --apply    # write the results
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.evidence import check_evidence


def out(s):
    sys.stdout.write(str(s).encode("ascii", "replace").decode() + "\n")
    sys.stdout.flush()


def main(apply_changes: bool):
    db.init_db()
    with db.get_conn() as conn:
        facts = [dict(r) for r in conn.execute("SELECT * FROM facts").fetchall()]

    out(f"{len(facts)} facts{' (writing)' if apply_changes else ' (report only)'}")

    counts, changed, updates = Counter(), 0, []
    for fact in facts:
        check = check_evidence(fact, bool(fact.get("quote_grounded", True)))
        counts[check.status] += 1
        if fact.get("evidence_status") != check.status:
            changed += 1
        updates.append((check.status, check.describe(), fact["id"]))

    if apply_changes:
        with db.get_conn() as conn:
            conn.executemany(
                "UPDATE facts SET evidence_status = ?, evidence_detail = ? WHERE id = ?",
                updates,
            )

    total = len(facts) or 1
    out("")
    for status, n in counts.most_common():
        out(f"  {status:<16} {n:>4}  ({n / total * 100:.1f}%)")
    out("")
    out(f"{changed} facts changed status")


if __name__ == "__main__":
    main("--apply" in sys.argv)
