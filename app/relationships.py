"""
Cross-document (and cross-page, same-document) reasoning. Candidate pairs
come from embedding similarity (cheap, local); only candidates above the
threshold get an actual LLM call, and only *new* facts get compared at
all -- this is what makes ingesting document N+1 not re-scan documents
1..N against each other.
"""
from app import db
from app.config import REASONING_MODEL, SIMILARITY_TOP_K, SIMILARITY_THRESHOLD
from app.embeddings import top_k_similar
from app.llm_client import chat_json, LLMError, LLMParseError

SYSTEM_PROMPT = """You compare two facts, each extracted from a document, to decide how they \
relate to each other. Both facts are given with their supporting quote, source document, \
page, time period and scope (any of which may be unknown/null).

Decide exactly one relation_type:
- "corroborates": both facts state the same real-world fact. They may be worded \
differently, use different units, or come from different documents, but they agree.
- "contradicts": the facts appear to be about the same real-world subject, attribute, \
time period AND scope, but state incompatible values or statuses, and there is no clear \
explanation (different time / scope / unit / definition) that would reconcile them. Flag \
it even if you are only fairly confident, not certain -- a likely contradiction still \
counts, say so via a lower confidence score rather than silently downgrading it to \
"unrelated".
- "reconciled": the facts appear to differ on the surface, but the difference is fully \
explained by a different time period, different scope (e.g. standalone vs consolidated, \
including vs excluding a subsidiary, before vs after an acquisition), different units, or \
different definitions of a similarly-named metric. You MUST name the specific reconciling \
context.
- "unrelated": the two facts are not actually about the same underlying real-world fact \
(e.g. same company but a genuinely different metric, or a similar-sounding but different \
entity) -- they were only paired because the wording looked superficially similar.

Respond with ONLY this JSON object, nothing else:
{
  "relation_type": "corroborates" | "contradicts" | "reconciled" | "unrelated",
  "explanation": "<1-3 sentences citing the specific values/wording that drove your decision>",
  "reconciliation_context": "<required if reconciled, else null>",
  "confidence": <0.0-1.0>
}"""


def _fact_block(label: str, fact: dict, document_name: str) -> str:
    return (
        f"{label}:\n"
        f"  document: {document_name}, page {fact['page_number']}\n"
        f"  statement: {fact['statement']}\n"
        f"  subject: {fact.get('subject')}\n"
        f"  attribute: {fact.get('attribute')}\n"
        f"  value: {fact.get('value')} {fact.get('unit') or ''}\n"
        f"  time_period: {fact.get('time_period')}\n"
        f"  scope: {fact.get('scope')}\n"
        f"  supporting quote: \"{fact['quote']}\""
    )


def classify_pair(fact_a: dict, doc_a_name: str, fact_b: dict, doc_b_name: str) -> dict:
    user_prompt = (
        _fact_block("FACT A", fact_a, doc_a_name) + "\n\n" +
        _fact_block("FACT B", fact_b, doc_b_name)
    )
    return chat_json(REASONING_MODEL, SYSTEM_PROMPT, user_prompt, temperature=0.0, max_tokens=500)


def build_relationships_for_document(document_id: int, new_fact_ids: list[int]) -> dict:
    """Compares every newly-inserted fact against (a) the embedding pool of
    all facts from *other* documents already in the DB, and (b) the other
    new facts in this same batch (so within-document tensions like
    standalone-vs-consolidated numbers in one filing are still caught).
    Returns a small summary dict for logging/response purposes."""
    prior_pool = db.get_all_embeddings(exclude_document_id=document_id)
    new_facts = [db.get_fact(fid) for fid in new_fact_ids]
    new_pool = [(f["id"], _load_embedding(f)) for f in new_facts]
    full_pool = prior_pool + new_pool

    doc_name_cache: dict[int, str] = {}

    def doc_name(doc_id: int) -> str:
        if doc_id not in doc_name_cache:
            d = db.get_document(doc_id)
            doc_name_cache[doc_id] = d["original_name"] if d else f"document {doc_id}"
        return doc_name_cache[doc_id]

    summary = {"candidates_checked": 0, "stored": 0, "skipped_unrelated": 0, "errors": 0}

    for fact, (fid, emb) in zip(new_facts, new_pool):
        candidates = [c for c in full_pool if c[0] != fid]
        top = top_k_similar(emb, candidates, SIMILARITY_TOP_K)
        for other_id, score in top:
            if score < SIMILARITY_THRESHOLD:
                continue
            if db.relationship_exists(fid, other_id):
                continue
            other = db.get_fact(other_id)
            if other is None:
                continue

            summary["candidates_checked"] += 1
            try:
                result = classify_pair(
                    fact, doc_name(fact["document_id"]),
                    other, doc_name(other["document_id"]),
                )
            except (LLMError, LLMParseError) as exc:
                db.insert_issue(document_id, fact["page_number"], "relationship_classification_failed",
                                 f"Comparing fact {fid} vs {other_id}: {exc}", "")
                summary["errors"] += 1
                continue

            relation = result.get("relation_type")
            if relation not in ("corroborates", "contradicts", "reconciled"):
                summary["skipped_unrelated"] += 1
                continue

            db.insert_relationship(
                fid, other_id, relation,
                result.get("explanation", ""),
                result.get("reconciliation_context"),
                result.get("confidence"),
                score,
            )
            summary["stored"] += 1

    return summary


def _load_embedding(fact_row: dict) -> list[float]:
    import json
    return json.loads(fact_row["embedding_json"])
