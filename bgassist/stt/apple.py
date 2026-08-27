"""Apple's on-device speech recognition (optional, macOS only) — D6.

Offered in Preferences as an alternative to Whisper and clearly labelled as
*may conflict with Voice Control*, because both want the same recogniser.
Whisper stays the default on every platform: it reads the microphone like any
other recorder and coexists with Voice Control (spike S1).

Requires ``pyobjc-framework-Speech``; if that is missing, constructing this
class raises :class:`ModelUnavailable` and the caller falls back to Whisper.
"""
from __future__ import annotations

import logging
import sys
import wave
from typing import Optional

from bgassist.stt.base import ModelUnavailable, TranscriptionFailed

log = logging.getLogger("bgassist.stt.apple")


class AppleTranscriber:
    name = "apple"

    def __init__(self, language: Optional[str] = "en-US", samplerate: int = 16000):
        if sys.platform != "darwin":
            raise ModelUnavailable("Apple speech recognition is macOS only")
        self.language = language or "en-US"
        self.samplerate = samplerate
        self._recognizer = None

    def _ensure(self):
        if self._recognizer is None:
            try:
                import Speech  # type: ignore  # noqa: N813 (pyobjc module name)
                from Foundation import NSLocale  # type: ignore
            except ImportError as exc:
                raise ModelUnavailable(
                    "Apple speech recognition needs pyobjc-framework-Speech; "
                    "install the 'macos' extra or use Whisper") from exc
            locale = NSLocale.localeWithLocaleIdentifier_(self.language)
            recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)
            if recognizer is None or not recognizer.isAvailable():
                raise ModelUnavailable(
                    "Apple speech recognition is unavailable for "
                    f"{self.language!r}")
            recognizer.setSupportsOnDeviceRecognition_(True)
            self._recognizer = recognizer
        return self._recognizer

    def preload(self) -> None:
        self._ensure()

    def transcribe(self, audio: bytes) -> str:
        """Recognise one utterance. Blocking; used from the STT thread only."""
        import tempfile

        import Speech  # type: ignore
        from Foundation import NSURL  # type: ignore

        recognizer = self._ensure()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            path = fh.name
        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.samplerate)
                wf.writeframes(audio)
            request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(
                NSURL.fileURLWithPath_(path))
            request.setRequiresOnDeviceRecognition_(True)
            request.setShouldReportPartialResults_(False)

            import threading

            done = threading.Event()
            result: dict = {}

            def handler(recognition, error) -> None:
                if error is not None:
                    result["error"] = str(error)
                elif recognition is not None and recognition.isFinal():
                    result["text"] = str(
                        recognition.bestTranscription().formattedString())
                else:
                    return
                done.set()

            recognizer.recognitionTaskWithRequest_resultHandler_(request, handler)
            if not done.wait(timeout=30):
                raise TranscriptionFailed("Apple speech recognition timed out")
            if "error" in result:
                raise TranscriptionFailed(result["error"])
            return (result.get("text") or "").strip()
        finally:
            import os

            try:
                os.unlink(path)
            except OSError:  # pragma: no cover
                pass
