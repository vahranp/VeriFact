"""
LLM call layer. Supports two backends behind one `chat_json` function:

- "ollama"           -> local Ollama server, native /api/chat, format="json"
                        (grammar-constrained JSON, no API key, no cost)
- "openai_compatible" -> any OpenAI-style /chat/completions endpoint
                        (OpenRouter, OpenAI direct, etc.)

Nothing outside this file knows which one is active -- app/config.py picks
the provider, everything else just calls chat_json(model, system, user).
"""
import json
import re
import time

import httpx

from app.config import LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, OLLAMA_NUM_CTX


class LLMError(Exception):
    """Raised when the model call itself fails (network/HTTP)."""


class LLMParseError(Exception):
    """Raised when the model responded but the content wasn't parseable
    JSON. Callers are expected to catch this and log it as an
    extraction_issue rather than crash the whole pipeline -- this is one
    of the concrete failure modes surfaced in the write-up."""

    def __init__(self, message: str, raw_content: str):
        super().__init__(message)
        self.raw_content = raw_content


def _extract_json_block(text: str) -> str:
    """Best-effort recovery when the model wraps JSON in prose or markdown
    fences instead of returning it bare."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    text = text.strip()
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text


def _call_ollama(model: str, system: str, user: str, temperature: float,
                  max_tokens: int, timeout: float) -> str:
    # NOTE: we deliberately do NOT set format="json" here. Ollama's
    # grammar-constrained JSON mode measurably pushed this 8B model toward
    # collapsing a requested JSON *array* of facts into a single JSON
    # *object* (tested empirically -- 1 fact recovered vs a full array
    # without the constraint). We rely on the prompt's explicit schema
    # instructions plus llm_client's own brace-matching recovery instead,
    # which in practice extracts far more real facts per page.
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": OLLAMA_NUM_CTX,
        },
    }
    resp = httpx.post(f"{LLM_BASE_URL}/api/chat", json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise LLMError(f"HTTP {resp.status_code} from ollama/{model}: {resp.text[:500]}")
    body = resp.json()
    return body["message"]["content"]


def _call_openai_compatible(model: str, system: str, user: str, temperature: float,
                             max_tokens: int, timeout: float) -> str:
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = httpx.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise LLMError(f"HTTP {resp.status_code} from {model}: {resp.text[:500]}")
    body = resp.json()
    return body["choices"][0]["message"]["content"]


def chat_json(model: str, system: str, user: str, temperature: float = 0.0,
              max_retries: int = 3, timeout: float = 180.0, max_tokens: int = 1500):
    """Calls the configured LLM backend and returns parsed JSON (list or
    dict). Raises LLMError for transport failures and LLMParseError if the
    response can't be turned into JSON after best-effort recovery."""
    caller = _call_ollama if LLM_PROVIDER == "ollama" else _call_openai_compatible

    content = None
    last_exc = None
    for attempt in range(max_retries):
        try:
            content = caller(model, system, user, temperature, max_tokens, timeout)
            break
        except (httpx.HTTPError, LLMError, KeyError, IndexError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(f"Failed calling {model} after {max_retries} attempts: {exc}") from exc

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    block = _extract_json_block(content)
    try:
        return json.loads(block)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"Could not parse JSON from {model} response: {exc}", content)
