"""LLM adapter: Anthropic Messages API or any OpenAI-compatible /chat/completions endpoint.

Mock mode returns deterministic output derived from a hash of the prompt so tests and demos
are reproducible offline.
* Anthropic: POST https://api.anthropic.com/v1/messages  headers x-api-key, anthropic-version: 2023-06-01
* OpenAI-compatible: POST {OPENAI_BASE_URL}/chat/completions  Authorization: Bearer
  (works with OpenAI, Groq, OpenRouter, Together, local vLLM/Ollama gateways, etc.)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from ..settings import get_settings
from .http import HttpClient


@dataclass
class LLMConfig:
    provider: str  # mock | anthropic | openai
    api_key: str | None
    model: str
    base_url: str = ""

    @property
    def live(self) -> bool:
        return self.provider in ("anthropic", "openai") and bool(self.api_key)


def _mock_text(system: str, prompt: str) -> str:
    digest = hashlib.sha256((system + "\n" + prompt).encode()).hexdigest()
    return f"MOCK::{digest[:12]}"


def mock_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    if request.url.path.endswith("/messages"):
        prompt = body.get("messages", [{}])[-1].get("content", "")
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": _mock_text(body.get("system", ""), prompt)}]}
        )
    msgs = body.get("messages", [])
    system = next((m["content"] for m in msgs if m.get("role") == "system"), "")
    prompt = msgs[-1].get("content", "") if msgs else ""
    return httpx.Response(200, json={"choices": [{"message": {"content": _mock_text(system, prompt)}}]})


class LLMClient:
    def __init__(self, cfg: LLMConfig, http: HttpClient | None = None) -> None:
        self.cfg = cfg
        self.mode = "live" if cfg.live else "mock"
        self.http = http or HttpClient(transport=None if cfg.live else httpx.MockTransport(mock_handler))

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> str:
        if self.cfg.provider == "anthropic" or (self.mode == "mock" and self.cfg.provider != "openai"):
            resp = self.http.request(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.cfg.api_key or "mock", "anthropic-version": "2023-06-01"},
                json={
                    "model": self.cfg.model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data: dict[str, Any] = resp.json()
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        base = (self.cfg.base_url or get_settings().openai_base_url).rstrip("/")
        resp = self.http.request(
            "POST",
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.api_key or 'mock'}"},
            json={
                "model": self.cfg.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            },
        )
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None
