"""Re-judges already-stored relationships under the current prompts.

Prompt changes invalidate the reasoning cache automatically (the cache key
includes the prompt text), but relationships already written to the
database are not revisited -- the pipeline only compares *new* facts, which
is what makes ingesting document N+1 cheap. So after a prompt fix the
stored graph can still contain judgments the current system would not make.

  python scripts/audit_precision.py            # audit everything, report only
  python scripts/audit_precision.py 23         # audit one document
  python scripts/audit_precision.py --apply    # also delete the ones that no longer hold

Reports kept / dropped / relabelled so the effect of a prompt change is a
number rather than an impression.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair

STORABLE = ("corroborates", "contradicts", "reconciled")

# Deletions are committed in batches rather than once at the end. A full
# corpus audit is hours of local inference, and an all-or-nothing write at
# the finish line means an interruption throws away every judgment made.
# Re-running after a stop is cheap regardless -- step 1 results are cached
# by prompt hash, so already-judged pairs come back without an LLM call --
# but there is no reason to discard completed work in the first place.
DELETE_BATCH = 10


def out(s):
    sys.stdout.write(str(s).encode("ascii", "replace").decode() + "\n")
    sys.stdout.flush()


def audit(document_id=None, apply_changes=False):
    db.init_db()
    with db.get_conn() as c:
        if document_id is None:
            rels = [dict(r) for r in c.execute("select * from relationships").fetchall()]
            label = "all documents"
        else:
            rels = [dict(r) for r in c.execute(
                "select r.* from relationships r join facts f on f.id=r.fact_id_a "
                "where f.document_id=?", (document_id,)).fetchall()]
            label = f"document {document_id}"

    out(f"{label}: {len(rels)} stored relationships to re-judge"
        f"{' (will delete those that no longer hold)' if apply_changes else ' (report only)'}")

    kept = dropped = relabelled = errors = 0
    deleted = 0
    doomed = []

    def flush():
        nonlocal doomed, deleted
        if not (apply_changes and doomed):
            return
        with db.get_conn() as c:
            c.executemany("delete from relationships where id=?", [(i,) for i in doomed])
        deleted += len(doomed)
        out(f"       ... deleted {len(doomed)} (running total {deleted})")
        doomed = []

    for i, r in enumerate(rels, 1):
        fa, fb = db.get_fact(r["fact_id_a"]), db.get_fact(r["fact_id_b"])
        if not fa or not fb:
            continue
        try:
            res, _ = classify_pair(fa, "a", fb, "b")
        except Exception as exc:  # noqa: BLE001 - an audit must not die on one bad pair
            errors += 1
            out(f"  [{i}/{len(rels)}] ERROR: {exc}")
            continue

        new, old = res.get("relation_type"), r["relation_type"]
        if new not in STORABLE:
            dropped += 1
            doomed.append(r["id"])
            out(f"  [{i}/{len(rels)}] DROP (was {old}): "
                f"{str(fa.get('attribute'))[:34]} vs {str(fb.get('attribute'))[:34]}")
            if len(doomed) >= DELETE_BATCH:
                flush()
        else:
            kept += 1
            if new != old:
                relabelled += 1
                out(f"  [{i}/{len(rels)}] {old} -> {new}")

    flush()

    total = kept + dropped
    pct = (dropped / total * 100) if total else 0.0
    out("")
    out(f"kept {kept} | dropped {dropped} ({pct:.0f}%) | relabelled {relabelled} | errors {errors}")
    if apply_changes:
        out(f"deleted {deleted} relationships that no longer hold")
    return dropped


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    audit(int(args[0]) if args else None, apply_changes="--apply" in sys.argv)
