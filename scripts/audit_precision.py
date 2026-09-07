"""Re-judges already-stored relationships under the current prompts and
reports how many survive. Used to measure the effect of a prompt change
on precision without re-ingesting the document."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.relationships import classify_pair


def out(s):
    sys.stdout.write(str(s).encode("ascii", "replace").decode() + "\n")
    sys.stdout.flush()


def main(document_id: int):
    db.init_db()
    with db.get_conn() as c:
        rels = [dict(r) for r in c.execute(
            "select r.* from relationships r join facts f on f.id=r.fact_id_a "
            "where f.document_id=?", (document_id,)).fetchall()]
    out(f"document {document_id}: {len(rels)} stored relationships to re-judge")

    kept, dropped, changed = 0, 0, 0
    for i, r in enumerate(rels, 1):
        fa, fb = db.get_fact(r["fact_id_a"]), db.get_fact(r["fact_id_b"])
        if not fa or not fb:
            continue
        res, _ = classify_pair(fa, "a", fb, "b")
        new = res.get("relation_type")
        old = r["relation_type"]
        if new not in ("corroborates", "contradicts", "reconciled"):
            dropped += 1
            out(f"  [{i}/{len(rels)}] DROPPED (was {old}): "
                f"{str(fa['attribute'])[:34]} vs {str(fb['attribute'])[:34]}")
        else:
            kept += 1
            if new != old:
                changed += 1
                out(f"  [{i}/{len(rels)}] {old} -> {new}")

    out("")
    out(f"kept {kept} | dropped {dropped} | relabelled {changed} of {len(rels)}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 23)
