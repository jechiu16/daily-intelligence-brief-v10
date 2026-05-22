from __future__ import annotations
"""Shared DeepSeek Chat Completions client."""

import json
import logging
import re
from typing import Any

import httpx

from src.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_REASONING_EFFORT,
    DEEPSEEK_THINKING,
)

logger = logging.getLogger(__name__)


class DeepSeekError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not DEEPSEEK_API_KEY:
        raise DeepSeekError("DEEPSEEK_API_KEY is not configured")
    return {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }


def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") or {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
    }


def extract_json(raw_text: str) -> dict[str, Any]:
    raw_text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text)
    if match:
        raw_text = match.group(1).strip()

    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1:
        raw_text = raw_text[start:end + 1]
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            repaired = re.sub(r",\s*([}\]])", r"\1", raw_text)
            repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", repaired)
            return json.loads(repaired)
    raise json.JSONDecodeError("No JSON object found", raw_text, 0)


def chat(
    *,
    messages: list[dict[str, str]],
    system: str | None = None,
    model: str = DEEPSEEK_MODEL,
    max_tokens: int = 8000,
    response_format: dict[str, str] | None = None,
    timeout_s: float = 120.0,
) -> tuple[str, dict[str, int]]:
    full_messages: list[dict[str, str]] = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    payload: dict[str, Any] = {
        "model": model,
        "messages": full_messages,
        "max_tokens": max_tokens,
        "thinking": {"type": DEEPSEEK_THINKING},
        "reasoning_effort": DEEPSEEK_REASONING_EFFORT,
    }
    if response_format:
        payload["response_format"] = response_format

    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.post(
                f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
                headers=_headers(),
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:1000]
        raise DeepSeekError(f"DeepSeek HTTP {exc.response.status_code}: {body}") from exc
    except httpx.HTTPError as exc:
        raise DeepSeekError(f"DeepSeek request failed: {exc}") from exc

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise DeepSeekError("DeepSeek returned no choices")

    content = (choices[0].get("message") or {}).get("content") or ""
    return content.strip(), _extract_usage(data)


def chat_json(
    *,
    messages: list[dict[str, str]],
    system: str | None = None,
    model: str = DEEPSEEK_MODEL,
    max_tokens: int = 8000,
    timeout_s: float = 120.0,
) -> tuple[dict[str, Any], dict[str, int]]:
    text, usage = chat(
        messages=messages,
        system=system,
        model=model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        timeout_s=timeout_s,
    )
    return extract_json(text), usage
