"""LLM backends: Ollama (local), OpenAI-compatible (cloud or local server),
and a mock for tests/self-tests.

HTTP uses only the standard library (urllib) so no extra dependency is
required. All backends implement ``ask(context_text, query) -> str``.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Dict, List

log = logging.getLogger("starcop.llm")

SYSTEM_PROMPT = (
    "You are the ship's computer aboard a starship, in the style of Star Trek. "
    "You are calm, precise and helpful. Answer in one to three short sentences "
    "that sound natural when spoken aloud. Use plain text only: no markdown, "
    "no bullet points, no code blocks and no emoji. You are given the recent "
    "conversation transcript for context, followed by the user's command — the "
    "words they said after calling your name. If the command is empty, respond "
    "helpfully to the most recent conversation."
)


class LLMError(RuntimeError):
    """Raised when an LLM backend fails (network, HTTP error, bad response)."""


def build_messages(context_text: str, query: str) -> List[Dict[str, str]]:
    """Build the chat message list from a transcript window and a command."""
    parts: List[str] = []
    if context_text and context_text.strip():
        parts.append(f"Recent conversation (oldest first):\n{context_text.strip()}")
    if query and query.strip():
        parts.append(f"The user just said: {query.strip()}")
    else:
        parts.append(
            "The user only called your name. Respond helpfully to the most "
            "recent conversation."
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _post_json(url: str, payload: dict, headers: Dict[str, str],
               timeout_s: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 - best effort detail extraction
            detail = ""
        raise LLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise LLMError(f"Could not reach {url}: {exc}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise LLMError(f"Invalid JSON from {url}: {body[:200]!r}") from exc


class OllamaBackend:
    """Local LLM via an Ollama server (default http://localhost:11434)."""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "llama3.2", timeout_s: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def ask(self, context_text: str, query: str) -> str:
        payload = {
            "model": self.model,
            "messages": build_messages(context_text, query),
            "stream": False,
        }
        data = _post_json(f"{self.base_url}/api/chat", payload, {}, self.timeout_s)
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Ollama response: {str(data)[:200]}") from exc
        return (text or "").strip()


class OpenAICompatibleBackend:
    """Any OpenAI-compatible /chat/completions endpoint (OpenAI, Groq,
    LM Studio, llama.cpp server, …)."""

    name = "openai_compatible"

    def __init__(self, base_url: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o-mini", api_key: str = "",
                 timeout_s: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    def ask(self, context_text: str, query: str) -> str:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model, "messages": build_messages(context_text, query)}
        data = _post_json(f"{self.base_url}/chat/completions", payload, headers,
                          self.timeout_s)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"Unexpected OpenAI-compatible response: {str(data)[:200]}") from exc
        return (text or "").strip()


class MockBackend:
    """Canned responses for tests and offline self-tests. Records all calls."""

    name = "mock"

    def __init__(self, response: str = "Mock response."):
        self.response = response
        self.calls: List[tuple] = []

    def ask(self, context_text: str, query: str) -> str:
        self.calls.append((context_text or "", query or ""))
        if query and query.strip():
            return f"{self.response} (you said: {query.strip()})"
        return self.response


def make_llm(llm_cfg) -> object:
    """Instantiate the configured LLM backend.

    *llm_cfg* is anything with backend/base_url/model/api_key/timeout_s
    attributes (starcop.config.LlmConfig in production).
    """
    backend = (getattr(llm_cfg, "backend", "") or "ollama").lower()
    if backend == "mock":
        return MockBackend()
    if backend == "ollama":
        return OllamaBackend(base_url=llm_cfg.base_url, model=llm_cfg.model,
                             timeout_s=llm_cfg.timeout_s)
    if backend in ("openai_compatible", "openai"):
        return OpenAICompatibleBackend(
            base_url=llm_cfg.base_url, model=llm_cfg.model,
            api_key=getattr(llm_cfg, "api_key", ""), timeout_s=llm_cfg.timeout_s)
    raise ValueError(f"Unknown LLM backend: {backend!r}")
