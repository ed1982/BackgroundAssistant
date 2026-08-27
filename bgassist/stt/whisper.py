"""Speech-to-text with faster-whisper (local, CPU-friendly).

The model is loaded lazily on first use, so importing this module — and
running the unit test suite — never downloads or loads anything. Model files
live in the app-support directory (F11), not next to the code.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from bgassist.stt.base import AudioFormatError, ModelUnavailable, TranscriptionFailed

log = logging.getLogger("bgassist.stt.whisper")

#: Sizes offered in Preferences, smallest first. ``base.en`` is bundled (D16).
MODEL_SIZES = ("tiny.en", "base.en", "small.en", "medium.en", "large-v3")


class WhisperTranscriber:
    """Wraps faster_whisper.WhisperModel for short utterances."""

    name = "whisper"

    def __init__(self, model_size: str = "base.en", compute_type: str = "int8",
                 language: Optional[str] = "en", download_root: Optional[str] = None):
        self.model_size = model_size
        self.compute_type = compute_type
        self.language = language  # None lets whisper auto-detect
        self._download_root = download_root
        self._model = None

    def _root(self) -> str:
        if self._download_root:
            return self._download_root
        from bgassist.platform import paths

        return str(paths.models_dir())

    def _ensure_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # lazy heavy import
            except ImportError as exc:
                raise ModelUnavailable(f"faster-whisper is not installed: {exc}") from exc

            log.info("loading whisper model %r (compute_type=%s); the first run "
                     "downloads it", self.model_size, self.compute_type)
            try:
                self._model = WhisperModel(self.model_size, device="cpu",
                                           compute_type=self.compute_type,
                                           download_root=self._root())
            except Exception as exc:  # noqa: BLE001 - normalised to a typed error
                raise ModelUnavailable(
                    f"could not load whisper model {self.model_size!r}: {exc}") from exc
            log.info("whisper model ready")
        return self._model

    def preload(self) -> None:
        """Load the model now (Preferences uses this to show progress)."""
        self._ensure_model()

    def transcribe(self, audio: bytes) -> str:
        """Transcribe 16 kHz mono int16 PCM; returns trimmed text (may be empty)."""
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - packaging issue
            raise ModelUnavailable(f"numpy is not installed: {exc}") from exc

        if not isinstance(audio, (bytes, bytearray, memoryview)):
            raise AudioFormatError(f"expected PCM bytes, got {type(audio).__name__}")
        if len(audio) < 2:
            return ""
        audio = bytes(audio)[: len(audio) // 2 * 2]  # drop a trailing odd byte
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0

        model = self._ensure_model()
        try:
            segments, _info = model.transcribe(
                samples,
                language=self.language,
                beam_size=1,  # greedy: fastest, good enough for short utterances
                vad_filter=False,  # endpointing is done upstream by webrtcvad
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as exc:  # noqa: BLE001 - normalised to a typed error
            raise TranscriptionFailed(f"whisper failed on this utterance: {exc}") from exc
        return re.sub(r"\s+", " ", text)
