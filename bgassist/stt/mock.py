"""A scripted transcriber for tests and offline self-tests."""
from __future__ import annotations

from typing import List, Optional, Sequence


class MockTranscriber:
    name = "mock"

    def __init__(self, texts: Optional[Sequence[str]] = None):
        self.texts: List[str] = list(texts or [])
        self.transcribed: List[bytes] = []

    def transcribe(self, audio: bytes) -> str:
        self.transcribed.append(audio)
        return self.texts.pop(0) if self.texts else ""
