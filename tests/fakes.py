"""Shared fakes for unit tests (no heavy dependencies)."""
from __future__ import annotations

import threading
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

    name = "fake"

    def __init__(self, texts=None):
        self.texts = list(texts or [])
        self.transcribed: list[bytes] = []

    def transcribe(self, audio: bytes) -> str:
        self.transcribed.append(audio)
        return self.texts.pop(0) if self.texts else ""


class RaisingTranscriber:
    name = "raising"

    def __init__(self, error):
        self.error = error

    def transcribe(self, audio: bytes) -> str:
        raise self.error


class RecordingLlm:
    """Old-style backend: a plain two-argument ask(), no streaming."""

    def __init__(self, response: str = "All systems nominal."):
        self.response = response
        self.calls: list[tuple] = []

    def ask(self, context_text: str, query: str) -> str:
        self.calls.append((context_text or "", query or ""))
        return self.response


class StreamingLlm:
    """Yields its response in pieces and honours the cancel token."""

    def __init__(self, chunks=("First sentence. ", "Second sentence. ",
                               "Third sentence.")):
        self.chunks = list(chunks)
        self.calls: list[tuple] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.pause_after = None  # index to block on, for cancellation tests

    def stream(self, context_text: str, query: str, *, history=None, cancel=None,
               marked_utterance: str = ""):
        self.calls.append((context_text or "", query or "", tuple(history or ())))
        self.started.set()
        for i, chunk in enumerate(self.chunks):
            if cancel is not None and cancel.is_set():
                from bgassist.llm.base import LLMCancelled

                raise LLMCancelled("cancelled")
            if self.pause_after is not None and i == self.pause_after:
                self.release.wait(timeout=5)
            yield chunk

    def ask(self, context_text: str, query: str, *, history=None, cancel=None,
            marked_utterance: str = "") -> str:
        return "".join(self.stream(context_text, query, history=history,
                                   cancel=cancel,
                                   marked_utterance=marked_utterance))


class FailingLlm:
    def __init__(self, error: str = "boom"):
        self.error = RuntimeError(error)
        self.calls: list[tuple] = []

    def ask(self, context_text: str, query: str):
        self.calls.append((context_text or "", query or ""))
        raise self.error


class RecordingTts:
    name = "recording"

    def __init__(self, fail: bool = False, block: bool = False):
        self.spoken: list[str] = []
        self.fail = fail
        self.block = block
        self.stopped = 0
        self._release = threading.Event()

    def speak(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("tts boom")
        self.spoken.append(text)
        if self.block:
            self._release.wait(timeout=5)
            self._release.clear()

    def stop(self) -> None:
        self.stopped += 1
        self._release.set()


def engine_config(**overrides) -> SimpleNamespace:
    defaults = dict(
        command_end_silence_ms=1500,
        max_command_wait_ms=12000,
        context_seconds=120.0,
        barge_in=True,
        auto_title=False,
        speak_typed_answers=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_orchestrator(clock=None, texts=None, llm=None, tts=None,
                      triggers=("computer",), sensitivity="balanced",
                      conversations=None, bus=None, threaded=False,
                      retro_transcribe=None, **cfg_overrides):
    """Build an Orchestrator with fakes and a minimal config namespace."""
    from bgassist.core.orchestrator import Orchestrator
    from bgassist.core.transcript import TranscriptBuffer
    from bgassist.core.trigger import TriggerParser

    clock = clock or FakeClock()
    cfg = engine_config(**cfg_overrides)
    return Orchestrator(
        llm=llm if llm is not None else RecordingLlm(),
        tts=tts if tts is not None else RecordingTts(),
        trigger=TriggerParser(list(triggers), sensitivity=sensitivity),
        buffer=TranscriptBuffer(max_seconds=cfg.context_seconds),
        cfg=cfg,
        clock=clock,
        transcriber=FakeTranscriber(texts),
        bus=bus,
        conversations=conversations,
        threaded_responder=threaded,
        retro_transcribe=retro_transcribe,
    )
