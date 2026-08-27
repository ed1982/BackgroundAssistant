"""Voice-activity detection (webrtcvad) with a lazy import.

webrtcvad operates on fixed-size frames of 16-bit mono PCM: 10, 20 or
30 ms at the given sample rate (we use 30 ms @ 16 kHz = 960 bytes).
"""
from __future__ import annotations

import logging

log = logging.getLogger("bgassist.audio.vad")


def frame_bytes(samplerate: int, frame_ms: int) -> int:
    """Bytes of 16-bit mono PCM in one VAD frame."""
    return samplerate * 2 * frame_ms // 1000


class WebrtcVad:
    """Thin wrapper around webrtcvad.Vad.

    aggressiveness 0..3: higher values filter out more non-speech (at the
    cost of clipping quiet speech). 2 is a good default for ambient use.
    """

    def __init__(self, aggressiveness: int = 2, samplerate: int = 16000):
        if aggressiveness not in (0, 1, 2, 3):
            raise ValueError("aggressiveness must be 0..3")
        try:
            import webrtcvad
        except ImportError as exc:  # pragma: no cover - environment issue
            raise RuntimeError(
                "webrtcvad is not installed; run `pip install webrtcvad-wheels`"
            ) from exc
        self._vad = webrtcvad.Vad(aggressiveness)
        self.samplerate = samplerate

    def is_speech(self, frame: bytes) -> bool:
        """True when *frame* (one VAD frame of int16 mono PCM) is speech."""
        return self._vad.is_speech(frame, self.samplerate)
