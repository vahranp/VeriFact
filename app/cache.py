"""
Content-hash helpers for cache keys. Hashing on actual content (never on
filename or document id) is what makes caching safe: two uploads of the
same bytes, or two chunks with identical text, hash identically regardless
of what they're called or which document they arrived in.
"""
import hashlib


def hash_text(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def hash_fact_pair(fact_a: dict, fact_b: dict) -> str:
    """Order-independent: comparing (A, B) or (B, A) must hit the same
    cache entry. Keyed on content (statement/quote/etc), not fact id, so
    the same underlying claim re-extracted into a new document (new ids)
    still hits the cache."""
    def sig(f: dict) -> str:
        return "|".join(str(f.get(k) or "") for k in
                         ("subject", "attribute", "value", "unit", "time_period", "scope", "statement", "quote"))

    sig_a, sig_b = sig(fact_a), sig(fact_b)
    ordered = sorted((sig_a, sig_b))
    return hash_text(*ordered)


def canonical_pair_order(fact_a: dict, fact_b: dict) -> bool:
    """True when the two facts should be swapped before being shown to the
    model as "FACT A" and "FACT B".

    hash_fact_pair is deliberately order-independent so comparing (A, B)
    and (B, A) hits one cache entry. But the prompts label the two facts
    positionally and the model's explanation refers to those labels -- so
    without a canonical presentation order, a pair first judged as (A, B)
    and later encountered as (B, A) would be served a cached explanation
    with "FACT A" and "FACT B" pointing at the wrong facts.

    Ordering presentation by the same signature the cache key sorts on
    keeps both properties: one cache entry per pair, and an explanation
    whose labels always mean the same thing.
    """
    def sig(f: dict) -> str:
        return "|".join(str(f.get(k) or "") for k in
                         ("subject", "attribute", "value", "unit", "time_period", "scope", "statement", "quote"))

    return sig(fact_a) > sig(fact_b)
