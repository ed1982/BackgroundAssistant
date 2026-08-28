"""OpenAI and any OpenAI-compatible /chat/completions endpoint.

Covers OpenAI itself, LM Studio, llama.cpp's server, Groq, and Ollama's
compatibility shim — the "Custom" provider in Preferences is this class with a
base URL you typed yourself.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Iterator, List, Optional

from bgassist.llm.base import HttpBackend, LLMError
from bgassist.llm.prompts import build_messages

log = logging.getLogger("bgassist.llm.openai")


class OpenAICompatibleBackend(HttpBackend):
    name = "openai_compatible"

    def __init__(self, base_url: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o-mini", api_key: str = "",
                 timeout_s: float = 120.0, **kwargs):
        super().__init__(base_url=base_url, model=model, api_key=api_key,
                         timeout_s=timeout_s, **kwargs)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _payload(self, context_text: str, query: str, history, marked: str,
                 stream: bool) -> dict:
        payload = {
            "model": self.model,
            "messages": build_messages(context_text, query, history=history,
                                       system_prompt=self.system_prompt,
                                       marked_utterance=marked),
            "stream": stream,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens:
            payload["max_completion_tokens"] = self.max_tokens
        return payload

    def stream(self, context_text: str = "", query: str = "", *,
               history=None, cancel: Optional[threading.Event] = None,
               marked_utterance: str = "") -> Iterator[str]:
        url = f"{self.base_url}/chat/completions"
        payload = self._payload(context_text, query, history, marked_utterance, True)
        response = self._request_adapting(url, payload, stream=True)
        for event in self._iter_sse(response, cancel):
            try:
                delta = event["choices"][0].get("delta") or {}
            except (KeyError, IndexError, TypeError):
                continue
            piece = delta.get("content")
            if piece:
                yield piece

    def ask(self, context_text: str = "", query: str = "", *,
            history=None, cancel: Optional[threading.Event] = None,
            marked_utterance: str = "") -> str:
        """Non-streaming request (used by connection tests and titling)."""
        url = f"{self.base_url}/chat/completions"
        payload = self._payload(context_text, query, history, marked_utterance, False)
        data = self._post_json(url, payload)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"Unexpected OpenAI-compatible response: {str(data)[:200]}") from exc
        return (text or "").strip()

    def list_models(self) -> List[str]:
        data = self._get_json(f"{self.base_url}/models")
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return sorted(str(item.get("id")) for item in items if item.get("id"))
