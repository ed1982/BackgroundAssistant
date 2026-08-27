"""Claude via the Anthropic Messages API (D7).

Different enough from the OpenAI shape to deserve its own backend: the system
prompt is a top-level field rather than a message, authentication is
``x-api-key``, and the stream is a typed event sequence.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Iterator, List, Optional

from bgassist.llm.base import HttpBackend, LLMError
from bgassist.llm.prompts import build_messages

log = logging.getLogger("bgassist.llm.anthropic")

API_VERSION = "2023-06-01"


class AnthropicBackend(HttpBackend):
    name = "anthropic"

    def __init__(self, base_url: str = "https://api.anthropic.com/v1",
                 model: str = "claude-sonnet-4-5", api_key: str = "",
                 timeout_s: float = 120.0, **kwargs):
        super().__init__(base_url=base_url, model=model, api_key=api_key,
                         timeout_s=timeout_s, **kwargs)

    def _headers(self) -> Dict[str, str]:
        headers = {"anthropic-version": API_VERSION}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _payload(self, context_text: str, query: str, history, marked: str,
                 stream: bool) -> dict:
        messages = build_messages(context_text, query, history=history,
                                  system_prompt=self.system_prompt,
                                  marked_utterance=marked)
        system = messages[0]["content"]
        return {
            "model": self.model,
            "system": system,
            "messages": [m for m in messages[1:]],
            "max_tokens": self.max_tokens or 400,
            "temperature": self.temperature,
            "stream": stream,
        }

    def stream(self, context_text: str = "", query: str = "", *,
               history=None, cancel: Optional[threading.Event] = None,
               marked_utterance: str = "") -> Iterator[str]:
        url = f"{self.base_url}/messages"
        payload = self._payload(context_text, query, history, marked_utterance, True)
        response = self._request(url, payload, stream=True)
        for event in self._iter_sse(response, cancel):
            if event.get("type") != "content_block_delta":
                continue
            delta = event.get("delta") or {}
            piece = delta.get("text")
            if piece:
                yield piece

    def ask(self, context_text: str = "", query: str = "", *,
            history=None, cancel: Optional[threading.Event] = None,
            marked_utterance: str = "") -> str:
        url = f"{self.base_url}/messages"
        payload = self._payload(context_text, query, history, marked_utterance, False)
        data = self._post_json(url, payload)
        try:
            blocks = data["content"]
            text = "".join(block.get("text", "") for block in blocks
                           if block.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Anthropic response: {str(data)[:200]}") from exc
        return (text or "").strip()

    def list_models(self) -> List[str]:
        try:
            data = self._get_json(f"{self.base_url}/models")
        except LLMError:
            return []
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return sorted(str(item.get("id")) for item in items if item.get("id"))
