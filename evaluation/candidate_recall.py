"""Recall@K for candidate retrieval, measured against real data: every
fact pair that's already a stored relationship in the live database (so
some retrieval path already found it worth judging) is real, mined
ground truth for "these two facts should be retrievable as candidates
of each other." For each such pair, this checks whether fact B is
actually inside fact A's top-K nearest neighbours by embedding
similarity -- using app.embeddings.top_k_similar(), the exact function
build_relationships_for_document calls, against the exact candidate
pool it uses (every other fact's embedding, corpuswide).

This does NOT re-derive or duplicate the hybrid entity/lexical/numeric
reranking in app/candidates.py -- it measures the
embedding-only retrieval stage specifically, which is the stage that
determines the candidate pool the hybrid signals then rerank within
(see app/relationships.py's own HYBRID_SCAN_K comment: those signals
were measured and found to rescue at most 3 pairs out of thousands).
"""
import json
import random
from dataclasses import dataclass

from app import db
from app.embeddings import top_k_similar

K_VALUES = [4, 8, 16, 32]


@dataclass
class RecallResult:
    k_values: list[int]
    recall_at_k: dict[int, float]
    n_pairs_evaluated: int
    n_relationships_in_corpus: int
    candidate_pool_size: int


def _sample_real_pairs(max_pairs: int, seed: int = 42) -> list[tuple[int, int]]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT fact_id_a, fact_id_b FROM relationships").fetchall()
    pairs = [(r["fact_id_a"], r["fact_id_b"]) for r in rows]
    if len(pairs) > max_pairs:
        random.Random(seed).shuffle(pairs)
        pairs = pairs[:max_pairs]
    return pairs


def measure(max_pairs: int = 300) -> RecallResult:
    pool = db.get_all_embeddings()  # every fact in the corpus, real embeddings
    pool_by_id = {fid: emb for fid, emb in pool}
    pairs = _sample_real_pairs(max_pairs)

    max_k = max(K_VALUES)
    hits_at_k = {k: 0 for k in K_VALUES}
    evaluated = 0

    for fact_a_id, fact_b_id in pairs:
        query_emb = pool_by_id.get(fact_a_id)
        if query_emb is None or fact_b_id not in pool_by_id:
            continue  # a fact whose embedding_json failed to parse -- skip, don't fabricate a hit/miss
        evaluated += 1
        candidates = [(fid, emb) for fid, emb in pool if fid != fact_a_id]
        top = top_k_similar(query_emb, candidates, max_k)
        top_ids_in_rank_order = [fid for fid, _score in top]
        for k in K_VALUES:
            if fact_b_id in top_ids_in_rank_order[:k]:
                hits_at_k[k] += 1

    recall_at_k = {k: (hits_at_k[k] / evaluated if evaluated else 0.0) for k in K_VALUES}
    return RecallResult(
        k_values=K_VALUES, recall_at_k=recall_at_k, n_pairs_evaluated=evaluated,
        n_relationships_in_corpus=len(pairs), candidate_pool_size=len(pool),
    )


def report(result: RecallResult) -> str:
    lines = ["Candidate Retrieval (measured on real corpus embeddings)", "-" * 55]
    for k in result.k_values:
        lines.append(f"Recall@{k:<3}: {result.recall_at_k[k]:.1%}")
    lines.append(f"\n({result.n_pairs_evaluated} real relationship pairs evaluated, "
                 f"out of a candidate pool of {result.candidate_pool_size} facts)")
    return "\n".join(lines)


if __name__ == "__main__":
    db.init_db()
    res = measure()
    print(report(res))
