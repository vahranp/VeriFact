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

    # Take whichever delimiter opens FIRST, not arrays by preference.
    # Preferring "[" unconditionally meant a malformed *object* whose
    # strings happened to contain a bracket got carved up into that inner
    # fragment instead: {"same_metric": true, "reason": "values [1] and [2]
    # differ",} yielded "[1]", which parses cleanly to a list, so the caller
    # then died on result.get(...) with an AttributeError -- a crash instead
    # of the LLMParseError callers are written to handle.
    candidates = [(text.find(ch), ch, close) for ch, close in (("[", "]"), ("{", "}"))]
    candidates = sorted((pos, ch, close) for pos, ch, close in candidates if pos != -1)

    for start, open_ch, close_ch in candidates:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            char = text[i]
            # Brackets inside string literals are data, not structure.
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == open_ch:
                depth += 1
            elif char == close_ch:
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
              max_retries: int = 2, timeout: float = 180.0, max_tokens: int = 1500):
    """Calls the configured LLM backend and returns parsed JSON (list or
    dict). Raises LLMError for transport failures and LLMParseError if the
    response can't be turned into JSON after best-effort recovery.

    A plain timeout is NOT retried: profiling showed this was the single
    biggest source of wasted time (a chunk dense enough to exhaust the
    timeout once will exhaust it again with the same payload, so the old
    "retry twice" policy just multiplied the wasted wall-clock time by
    max_retries for no benefit). Only errors that look transient --
    connection failures, malformed responses -- get retried.
    """
    caller = _call_ollama if LLM_PROVIDER == "ollama" else _call_openai_compatible

    content = None
    last_exc = None
    for attempt in range(max_retries):
        try:
            content = caller(model, system, user, temperature, max_tokens, timeout)
            break
        except httpx.TimeoutException as exc:
            raise LLMError(f"Timed out calling {model} after {timeout}s (not retried): {exc}") from exc
        except (httpx.HTTPError, LLMError, KeyError, IndexError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(f"Failed calling {model} after {max_retries} attempts: {exc}") from exc

    # strict=False allows raw control characters (literal tabs/newlines)
    # inside JSON strings. The extraction prompt asks the model to copy
    # quotes character-for-character from the source page -- and PDF table
    # text routinely contains literal tab characters used for column
    # alignment -- so a model doing exactly what was asked (copying a quote
    # verbatim, tabs included) produces a technically-invalid-per-spec but
    # completely legitimate JSON string that Python's strict-mode parser
    # rejects. Rejecting the model's correct behavior here would be a
    # self-inflicted failure, not a real one.
    try:
        return json.loads(content, strict=False)
    except json.JSONDecodeError:
        pass

    block = _extract_json_block(content)
    try:
        return json.loads(block, strict=False)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"Could not parse JSON from {model} response: {exc}", content)
