"""The model the plugins think with ("agent" model), and a small client for it.

It is the privacy gateway by default: the gateway removes private values, forwards to the public provider that has a key, and
passes the caller's own `Authorization` header through. Settings, all optional:

  JARVIS_AGENT_BASE_URL   default http://127.0.0.1:48950/v1  (the privacy gateway)
  JARVIS_AGENT_MODEL      default ""                         (the plugin says the model is not set up when it is empty)
  JARVIS_AGENT_KEY_FILE   a file that holds the key (never the key itself); read at the call, never logged
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:48950/v1"


def _key_from_file() -> str:
    path = os.environ.get("JARVIS_AGENT_KEY_FILE", "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def agent_model() -> dict[str, Any]:
    """{"base_url", "model", "api_key", "provider_type"} of the model the plugins use."""
    return {"base_url": os.environ.get("JARVIS_AGENT_BASE_URL", DEFAULT_BASE_URL).strip(),
            "model": os.environ.get("JARVIS_AGENT_MODEL", "").strip(),
            "api_key": _key_from_file(), "provider_type": "openai-compatible"}


class Reply:
    def __init__(self, content: str, raw: dict[str, Any] | None = None):
        self.content = content
        self.raw = raw or {}


class ChatClient:
    """OpenAI-compatible chat completions over loopback or the tailnet. `await client.ainvoke(messages)` returns a Reply."""

    def __init__(self, *, model: str, base_url: str, api_key: str = "", max_completion_tokens: int = 1200, timeout: float = 60.0, **_: Any):
        self.model, self.base_url, self.api_key = model, base_url.rstrip("/"), api_key
        self.max_tokens, self.timeout = max_completion_tokens, timeout
        self._http = httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)

    async def ainvoke(self, messages: list[dict[str, Any]]) -> Reply:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        resp = await self._http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        return Reply(str((choice.get("message") or {}).get("content") or ""), data)

    async def aclose(self) -> None:
        await self._http.aclose()


async def create_chat_llm_async(**kwargs: Any) -> ChatClient:
    return ChatClient(**kwargs)
