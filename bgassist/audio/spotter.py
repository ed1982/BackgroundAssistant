"""The always-on acoustic wake-word spotter (§5.3).

A small keyword model running directly on the frame stream, in parallel with —
not instead of — the transcript grammar. It buys two things the transcript
path cannot:

- the **instant chime**, ~100 ms after the word rather than a second later,
  which is what fixes the silence of F6;
- **barge-in while speaking**, when full transcription is deliberately off.

It is explicitly *not* load-bearing. Under D12a a late cut is still a correct
cut, so if openWakeWord turns out to be unreliable for a custom word (spike
S2) the transcript path alone remains correct and merely less sharp. That is
why this module degrades to a no-op instead of raising.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("bgassist.audio.spotter")

#: Confidence needed for a hit. Raised while speaking, because our own voice
#: comes back attenuated and room-coloured (§5.4.2, layer 3).
THRESHOLD_IDLE = 0.5
THRESHOLD_SPEAKING = 0.75
#: Ignore repeat hits inside this window, so one word is one trigger.
REFRACTORY_S = 1.5


@dataclass
class SpotterHit:
    word: str
    confidence: float
    ts: float


class NullSpotter:
    """What you get when openWakeWord is not installed. Never fires."""

    available = False
    name = "none"

    def process_frame(self, frame: bytes) -> Optional[SpotterHit]:
        return None

    def set_speaking(self, speaking: bool) -> None:
        pass


class OpenWakeWordSpotter:
    """openWakeWord over ONNX, fed the same 30 ms frames as the VAD."""

    available = True
    name = "openwakeword"

    def __init__(self, words: List[str], model_paths: Optional[List[str]] = None,
                 threshold: float = THRESHOLD_IDLE, samplerate: int = 16000,
                 clock=time.monotonic):
        try:
            from openwakeword.model import Model  # lazy heavy import
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError(
                "openwakeword is not installed (pip install "
                "'backgroundassistant[spotter]')") from exc
        self.words = [w.lower() for w in words if w]
        self.threshold = float(threshold)
        self.samplerate = samplerate
        self.clock = clock
        self._speaking = False
        self._last_hit = 0.0
        kwargs = {"wakeword_models": model_paths} if model_paths else {}
        self._model = Model(**kwargs)

    def set_speaking(self, speaking: bool) -> None:
        self._speaking = bool(speaking)

    def _threshold(self) -> float:
        return THRESHOLD_SPEAKING if self._speaking else self.threshold

    def process_frame(self, frame: bytes) -> Optional[SpotterHit]:
        import numpy as np

        samples = np.frombuffer(frame, dtype=np.int16)
        scores = self._model.predict(samples)
        threshold = self._threshold()
        now = self.clock()
        if now - self._last_hit < REFRACTORY_S:
            return None
        for word, score in (scores or {}).items():
            if score < threshold:
                continue
            if self.words and not any(w in word.lower() for w in self.words):
                continue
            self._last_hit = now
            return SpotterHit(word=word, confidence=float(score), ts=now)
        return None


class ScriptedSpotter:
    """Fires on the frames whose index is in *hit_frames* (tests)."""

    available = True
    name = "scripted"

    def __init__(self, hit_frames=(), confidence: float = 0.9):
        self.hit_frames = set(hit_frames)
        self.confidence = confidence
        self.index = -1
        self.speaking = False

    def set_speaking(self, speaking: bool) -> None:
        self.speaking = bool(speaking)

    def process_frame(self, frame: bytes) -> Optional[SpotterHit]:
        self.index += 1
        if self.index in self.hit_frames:
            return SpotterHit(word="computer", confidence=self.confidence,
                              ts=float(self.index))
        return None


def make_spotter(settings_general, enabled: bool = False):
    """Build a spotter, falling back to the null one on any problem."""
    if not enabled:
        return NullSpotter()
    words = list(getattr(settings_general, "trigger_words", ["computer"]))
    try:
        return OpenWakeWordSpotter(words)
    except Exception as exc:  # noqa: BLE001 - optional polish, never fatal
        log.warning("wake-word spotter unavailable (%s); using transcript-based "
                    "triggering only", exc)
        return NullSpotter()
