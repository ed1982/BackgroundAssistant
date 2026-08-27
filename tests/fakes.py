"""Shared fakes for unit tests (no heavy dependencies)."""
from __future__ import annotations

from types import SimpleNamespace


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeVad:
    """Scripted VAD: is_speech() pops from the script, then repeats the last."""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def is_speech(self, frame: bytes) -> bool:
        if self.i < len(self.script):
            value = self.script[self.i]
        else:
            value = self.script[-1] if self.script else False
        self.i += 1
        return value


class FakeTranscriber:
    """Returns scripted text per utterance; empty string once exhausted."""

    def __init__(self, texts=None):
        self.texts = list(texts or [])
        self.transcribed: list[bytes] = []

    def transcribe(self, audio: bytes) -> str:
        self.transcribed.append(audio)
        return self.texts.pop(0) if self.texts else ""


class RecordingLlm:
    def __init__(self, response: str = "All systems nominal."):
        self.response = response
        self.calls: list[tuple] = []

    def ask(self, context_text: str, query: str) -> str:
        self.calls.append((context_text or "", query or ""))
        return self.response


class FailingLlm:
    def __init__(self, error: str = "boom"):
        self.error = RuntimeError(error)

    def ask(self, context_text: str, query: str):
        raise self.error


class RecordingTts:
    def __init__(self, fail: bool = False):
        self.spoken: list[str] = []
        self.fail = fail

    def speak(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("tts boom")
        self.spoken.append(text)


def make_pipeline(clock=None, texts=None, llm=None, tts=None,
                  triggers=("computer",), **cfg_overrides):
    """Build a Pipeline with fakes and a minimal cfg namespace."""
    from starcop.pipeline import Pipeline
    from starcop.transcript import TranscriptBuffer
    from starcop.wakeword import WakeWordMatcher

    clock = clock or FakeClock()
    cfg = SimpleNamespace(
        command_end_silence_ms=1500,
        max_command_wait_ms=12000,
        context_seconds=120.0,
        **cfg_overrides,
    )
    return Pipeline(
        transcriber=FakeTranscriber(texts),
        llm=llm if llm is not None else RecordingLlm(),
        tts=tts if tts is not None else RecordingTts(),
        wakeword=WakeWordMatcher(list(triggers)),
        buffer=TranscriptBuffer(max_seconds=cfg.context_seconds),
        cfg=cfg,
        clock=clock,
    )
