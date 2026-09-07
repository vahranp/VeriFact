"""
Local embeddings (no API cost, no network dependency once the model is
cached) used purely to shortlist *candidate* fact pairs before spending an
LLM call on them. This is what keeps cross-document comparison cheap and
incremental: a new document's facts only get embedded once and compared
against existing embeddings already in SQLite, not against a full re-scan.
"""
import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import EMBEDDING_MODEL

_model = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def top_k_similar(query_vec: list[float], candidates: list[tuple[int, list[float]]], k: int):
    """candidates: [(fact_id, embedding), ...]. Returns [(fact_id, score), ...]
    sorted descending, since embeddings are normalized cosine similarity is
    a plain dot product."""
    if not candidates:
        return []
    q = np.array(query_vec)
    ids = [c[0] for c in candidates]
    mat = np.array([c[1] for c in candidates])
    scores = mat @ q
    order = np.argsort(-scores)[:k]
    return [(ids[i], float(scores[i])) for i in order]
