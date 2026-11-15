"""OpenAI-compatible chat client (Gemini by default; Groq, xAI, OpenRouter, OpenAI also work).

Handles free-tier realities: 429 rate limits (honours Retry-After), transient
5xx, timeouts, and models that wrap JSON in prose or markdown fences.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time

import httpx

from ..config import settings
from .prompts import SYSTEM_PROMPT, USER_TEMPLATE

log = logging.getLogger(__name__)
_sem = threading.BoundedSemaphore(max(1, settings.llm_concurrency))


class LLMError(Exception):
    pass


def _clip(text: str, limit: int) -> str:
    """Keep the head (parties, numbers, dates) and the tail (totals) of long text."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    return text[:head] + "\n[... middle of document omitted ...]\n" + text[-(limit - head):]


def _parse_json(content: str) -> dict:
    content = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S)
    if fence:
        content = fence.group(1)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise


def chat_extract(text: str, client: httpx.Client | None = None) -> tuple[dict, str]:
    """Returns (parsed JSON, model name). Raises LLMError after retries."""
    if not settings.llm_enabled:
        raise LLMError("LLM is not configured (set LLM_API_KEY).")
    base, model = settings.llm_endpoint
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(text=_clip(text, settings.llm_max_input_chars))},
        ],
    }
    if settings.llm_provider == "gemini" and settings.llm_reasoning_effort:
        body["reasoning_effort"] = settings.llm_reasoning_effort
    headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
    own = client is None
    client = client or httpx.Client(timeout=settings.llm_timeout_s)
    last: Exception | None = None
    try:
        for attempt in range(settings.llm_max_retries + 1):
            try:
                with _sem:
                    r = client.post(f"{base}/chat/completions", json=body, headers=headers)
                if r.status_code == 429 or r.status_code >= 500:
                    hinted = r.headers.get("retry-after") or (re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', r.text) or [None, 0])[1]
                    wait = min(60.0, float(hinted or 0)) or min(30, 2 ** attempt + random.random())
                    last = LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
                    log.warning("LLM %s, retrying in %.1fs", r.status_code, wait)
                    time.sleep(wait)
                    continue
                if r.status_code == 400 and "response_format" in r.text and "response_format" in body:
                    body.pop("response_format")  # some models reject JSON mode; prompt still demands JSON
                    continue
                if r.status_code == 400 and "reasoning_effort" in body:
                    body.pop("reasoning_effort")  # some models / values are rejected with a generic 400; retry without it
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                content = r.json()["choices"][0]["message"]["content"] or ""
                return _parse_json(content), model
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
                time.sleep(min(20, 2 ** attempt))
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                last = e  # malformed output: retry once more, then give up
        raise LLMError(f"LLM extraction failed after retries: {last}")
    finally:
        if own:
            client.close()
