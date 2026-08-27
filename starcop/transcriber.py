"""Speech-to-text with faster-whisper (local, CPU-friendly).

The model is loaded lazily on first use so importing this module — and
running the unit test suite — never downloads or loads anything.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("starcop.transcriber")


class WhisperTranscriber:
    """Wraps faster-whisper.WhisperModel for short utterances."""

    def __init__(self, model_size: str = "base.en", compute_type: str = "int8",
                 language: Optional[str] = "en"):
        self.model_size = model_size
        self.compute_type = compute_type
        self.language = language  # None lets whisper auto-detect
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # lazy heavy import

            log.info("Loading whisper model %r (compute_type=%s) — "
                     "the first run downloads it", self.model_size, self.compute_type)
            self._model = WhisperModel(self.model_size, device="cpu",
                                       compute_type=self.compute_type)
            log.info("Whisper model ready")
        return self._model

    def transcribe(self, audio: bytes) -> str:
        """Transcribe 16 kHz mono int16 PCM; returns trimmed text (may be empty)."""
        import numpy as np

        if len(audio) < 2:
            return ""
        audio = audio[: len(audio) // 2 * 2]  # drop a trailing odd byte, if any
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        model = self._ensure_model()
        segments, _info = model.transcribe(
            samples,
            language=self.language,
            beam_size=1,  # greedy: fastest, good enough for short utterances
            vad_filter=False,  # endpointing is done upstream by webrtcvad
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return re.sub(r"\s+", " ", text)
