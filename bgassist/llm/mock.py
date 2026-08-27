"""Canned LLM responses for tests and offline self-tests."""
from __future__ import annotations

import threading
from typing import Iterator, List, Optional


class MockBackend:
    """Records every call and streams its canned response word by word."""

    name = "mock"
    model = "mock"

    def __init__(self, response: str = "Mock response.", chunk_words: int = 3,
                 delay_s: float = 0.0):
        self.response = response
        self.chunk_words = max(1, int(chunk_words))
        self.delay_s = float(delay_s)
        self.calls: List[tuple] = []

    def _text(self, query: str) -> str:
        if query and query.strip():
            return f"{self.response} (you said: {query.strip()})"
        return self.response

    def stream(self, context_text: str = "", query: str = "", *,
               history=None, cancel: Optional[threading.Event] = None,
               marked_utterance: str = "") -> Iterator[str]:
        self.calls.append((context_text or "", query or ""))
        words = self._text(query).split(" ")
        for i in range(0, len(words), self.chunk_words):
            if cancel is not None and cancel.is_set():
                from bgassist.llm.base import LLMCancelled

                raise LLMCancelled("cancelled")
            if self.delay_s:
                import time

                time.sleep(self.delay_s)
            chunk = " ".join(words[i:i + self.chunk_words])
            yield chunk if i == 0 else " " + chunk

    def ask(self, context_text: str = "", query: str = "", *,
            history=None, cancel: Optional[threading.Event] = None,
            marked_utterance: str = "") -> str:
        self.calls.append((context_text or "", query or ""))
        return self._text(query)

    def test_connection(self) -> dict:
        return {"ok": True, "latency_ms": 0, "model": "mock", "reply": "ready"}

    def list_models(self) -> List[str]:
        return ["mock"]
