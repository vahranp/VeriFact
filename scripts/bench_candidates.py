"""Measures what hybrid retrieval actually changes on real ingested facts.

Two distinct changes are easy to conflate, so this separates them:

  A. baseline  -- embedding top-K (SIMILARITY_TOP_K) above SIMILARITY_THRESHOLD
  B. wider     -- the same embedding rule over a wider scan window
  C. hybrid    -- the wider window plus the entity/lexical/numeric signals

The number that decides whether the new signals earn their cost is C - B,
not C - A: widening the window is a separate (and much larger) effect.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.candidates import score_pair
from app.config import SIMILARITY_THRESHOLD, SIMILARITY_TOP_K
from app.embeddings import top_k_similar
from app.relationships import HYBRID_SCAN_K


def ascii(text):
    return str(text).encode("ascii", "replace").decode()


def main():
    db.init_db()
    pool = db.get_all_embeddings()
    if not pool:
        print("No facts in the database -- ingest a document first.")
        return
    print(f"Fact pool: {len(pool)} facts")
    print(f"Baseline top-K={SIMILARITY_TOP_K} threshold={SIMILARITY_THRESHOLD}; "
          f"hybrid scan window={HYBRID_SCAN_K}\n")

    baseline: set = set()
    wider: set = set()
    hybrid: set = set()
    trigger_counts: dict[str, int] = {}
    rescued: list = []

    fact_cache: dict = {}

    def load(fact_id):
        if fact_id not in fact_cache:
            fact_cache[fact_id] = db.get_fact(fact_id)
        return fact_cache[fact_id]

    for fid, emb in pool:
        fact = load(fid)
        if fact is None:
            continue
        others = [c for c in pool if c[0] != fid]
        window = top_k_similar(emb, others, HYBRID_SCAN_K)

        # A: only the first SIMILARITY_TOP_K neighbours count for baseline.
        for other_id, score in window[:SIMILARITY_TOP_K]:
            if score >= SIMILARITY_THRESHOLD:
                baseline.add((min(fid, other_id), max(fid, other_id)))

        for other_id, score in window:
            key = (min(fid, other_id), max(fid, other_id))
            if score >= SIMILARITY_THRESHOLD:
                wider.add(key)

            other = load(other_id)
            if other is None:
                continue
            reason = score_pair(fact, other, score)
            if not reason.selected:
                continue
            hybrid.add(key)
            for trigger in reason.triggers:
                trigger_counts[trigger] = trigger_counts.get(trigger, 0) + 1
            if "embedding" not in reason.triggers and key not in wider and len(rescued) < 12:
                rescued.append((fact, other, reason))

    # Sweep the window so the two effects can be read apart at every size.
    print("Window sweep -- embedding-only vs hybrid at each scan depth:")
    print(f"  {'K':>3}  {'embedding-only':>14}  {'hybrid':>8}  {'rescued by signals':>18}")
    for k in (2, 4, 6, 8, 12, 20):
        emb_pairs: set = set()
        hyb_pairs: set = set()
        for fid, emb in pool:
            fact = load(fid)
            if fact is None:
                continue
            others = [c for c in pool if c[0] != fid]
            for other_id, score in top_k_similar(emb, others, k):
                key = (min(fid, other_id), max(fid, other_id))
                if score >= SIMILARITY_THRESHOLD:
                    emb_pairs.add(key)
                other = load(other_id)
                if other is not None and score_pair(fact, other, score).selected:
                    hyb_pairs.add(key)
        print(f"  {k:>3}  {len(emb_pairs):>14}  {len(hyb_pairs):>8}  "
              f"{len(hyb_pairs - emb_pairs):>18}")
    print()

    print(f"A. baseline (top-{SIMILARITY_TOP_K}, embedding only) : {len(baseline)} pairs")
    print(f"B. wider window, embedding only          : {len(wider)} pairs "
          f"(+{len(wider) - len(baseline)} from the window alone)")
    print(f"C. wider window + hybrid signals         : {len(hybrid)} pairs "
          f"(+{len(hybrid - wider)} from the new signals)")
    print(f"   dropped vs B                          : {len(wider - hybrid)}")
    print("\nUnique pairs promoted per signal (a pair may fire several):")
    for trigger, count in sorted(trigger_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {trigger:16s} {count}")

    if rescued:
        print("\nPairs ONLY the non-embedding signals found:")
        for fact, other, reason in rescued:
            print(f"\n  A: {ascii(fact.get('attribute'))} = {ascii(fact.get('value'))} "
                  f"{ascii(fact.get('unit') or '')}")
            print(f"  B: {ascii(other.get('attribute'))} = {ascii(other.get('value'))} "
                  f"{ascii(other.get('unit') or '')}")
            print(f"     -> {ascii(reason.describe())}")
    else:
        print("\nNo pairs were found by the non-embedding signals alone.")


if __name__ == "__main__":
    main()
