"""A recording TTS engine for tests and offline self-tests."""
from __future__ import annotations

import threading
from typing import List


class MockTts:
    name = "mock"

    def __init__(self, fail: bool = False, block: bool = False) -> None:
        self.spoken: List[str] = []
        self.fail = fail
        self.block = block
        self.stopped = 0
        self._release = threading.Event()

    def speak(self, text: str) -> None:
        if self.fail:
            from bgassist.tts.base import TtsError

            raise TtsError("mock tts failure")
        self.spoken.append(text)
        if self.block:
            # Simulates a long utterance: returns only when stop() is called.
            self._release.wait(timeout=5)
            self._release.clear()

    def stop(self) -> None:
        self.stopped += 1
        self._release.set()
