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

#: A reasoning model spends ``max_completion_tokens`` on hidden reasoning
#: *before* it writes anything, so a budget sized for a spoken sentence can be
#: gone before the answer starts. The symptom is a completion with empty
#: content and ``finish_reason: "length"`` — which the app then reports as "I
#: have nothing to report", because as far as it can tell there was nothing.
#: When that happens we raise the ceiling and ask once more.
REASONING_BUDGET = 4000

#: Asked of models that understand it. A voice assistant is waited on out
#: loud, so the cheapest reasoning that answers the question is the right
#: setting. Models that have never heard of it refuse it once, and the
#: adaptation in HttpBackend stops sending it.
REASONING_EFFORT = "low"


class OpenAICompatibleBackend(HttpBackend):
    name = "openai_compatible"

    def __init__(self, base_url: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o-mini", api_key: str = "",
                 timeout_s: float = 120.0, reasoning_effort: str = REASONING_EFFORT,
                 **kwargs):
        super().__init__(base_url=base_url, model=model, api_key=api_key,
                         timeout_s=timeout_s, **kwargs)
        self.reasoning_effort = reasoning_effort
        #: Raised once we have seen this model exhaust its budget on reasoning.
        self._budget_floor = 0

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    # -- token budget ------------------------------------------------------
    @property
    def completion_budget(self) -> int:
        return max(self.max_tokens, self._budget_floor)

    def _grow_budget(self) -> bool:
        """Raise the ceiling after a model spent it all on reasoning."""
        target = max(REASONING_BUDGET, self.max_tokens * 8)
        if self._budget_floor >= target:
            return False
        self._budget_floor = target
        log.info("%s returned nothing and hit the token limit; retrying with a "
                 "budget of %d", self.model, target)
        return True

    @staticmethod
    def _ran_out_of_room(finish_reason: Optional[str]) -> bool:
        return finish_reason in ("length", "max_tokens", "max_output_tokens")

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
            payload["max_completion_tokens"] = self.completion_budget
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def stream(self, context_text: str = "", query: str = "", *,
               history=None, cancel: Optional[threading.Event] = None,
               marked_utterance: str = "") -> Iterator[str]:
        url = f"{self.base_url}/chat/completions"
        # Two attempts at most, and the retry only ever happens before a single
        # token has been yielded — so nothing is ever spoken twice.
        for _attempt in (0, 1):
            payload = self._payload(context_text, query, history,
                                    marked_utterance, True)
            spoke = False
            finish_reason = None
            response = self._request_adapting(url, payload, stream=True)
            for event in self._iter_sse(response, cancel):
                try:
                    choice = event["choices"][0]
                except (KeyError, IndexError, TypeError):
                    continue
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    spoke = True
                    yield piece
            if spoke or not self._ran_out_of_room(finish_reason):
                return
            if not self._grow_budget():
                return

    def ask(self, context_text: str = "", query: str = "", *,
            history=None, cancel: Optional[threading.Event] = None,
            marked_utterance: str = "") -> str:
        """Non-streaming request (used by connection tests and titling)."""
        url = f"{self.base_url}/chat/completions"
        for _attempt in (0, 1):
            payload = self._payload(context_text, query, history,
                                    marked_utterance, False)
            data = self._post_json(url, payload)
            try:
                choice = data["choices"][0]
                text = (choice["message"]["content"] or "").strip()
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError(
                    f"Unexpected OpenAI-compatible response: {str(data)[:200]}"
                ) from exc
            if text:
                return text
            if not self._ran_out_of_room(choice.get("finish_reason")):
                return text
            if not self._grow_budget():
                return text
        return ""

    def list_models(self) -> List[str]:
        data = self._get_json(f"{self.base_url}/models")
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return sorted(str(item.get("id")) for item in items if item.get("id"))
