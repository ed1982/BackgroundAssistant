"""Text-to-speech engines and the factory that picks one from settings."""
from __future__ import annotations

import logging
import sys
from typing import List, Optional

from bgassist.tts.base import TtsEngine, TtsError
from bgassist.tts.chunker import sentence_chunks, split_sentences
from bgassist.tts.mock import MockTts
from bgassist.tts.system import Pyttsx3Tts, SayTts

log = logging.getLogger("bgassist.tts")

__all__ = ["TtsEngine", "TtsError", "MockTts", "SayTts", "Pyttsx3Tts",
           "sentence_chunks", "split_sentences", "make_tts", "available_voices"]


def make_tts(cfg, platform: Optional[str] = None):
    """Instantiate the configured engine.

    ``engine="auto"`` prefers Piper when a voice is installed (D11) and falls
    back to the operating system's own voice, so the app always speaks.

    *platform* names the operating system to select for, defaulting to this
    one. It is a parameter rather than a read of ``sys.platform`` because the
    latter can only be varied in a test by mutating the real ``sys`` module —
    which is global, and which the standard library reads too.
    """
    system = platform or sys.platform
    engine = (getattr(cfg, "engine", "") or "auto").lower()
    rate = int(getattr(cfg, "rate", 185) or 185)
    voice = getattr(cfg, "voice", None) or None  # "" is "automatic" too

    if engine == "mock":
        return MockTts()

    if engine in ("auto", "piper"):
        try:
            from bgassist.tts.piper import DEFAULT_VOICE, PiperTts

            return PiperTts(voice=voice or DEFAULT_VOICE, rate=rate / 185.0)
        except Exception as exc:  # noqa: BLE001 - an optional engine must never
            # stop the app from speaking; any failure falls back to the OS voice.
            if engine == "piper":
                log.warning("piper unavailable (%s); using the system voice", exc)
            else:
                log.debug("piper unavailable (%s); using the system voice", exc)
            engine = "system"

    if engine in ("system", "auto"):
        engine = "say" if system == "darwin" else "pyttsx3"

    if engine == "say":
        if system != "darwin":
            log.warning("tts engine 'say' is macOS-only; falling back to pyttsx3")
            return Pyttsx3Tts(rate=rate, voice=voice)
        return SayTts(rate=rate, voice=voice)
    if engine == "pyttsx3":
        return Pyttsx3Tts(rate=rate, voice=voice)
    raise ValueError(f"Unknown TTS engine: {engine!r}")


def available_voices(engine: Optional[str] = None,
                     platform: Optional[str] = None) -> List[str]:
    """Voice names for the Preferences picker."""
    system = platform or sys.platform
    engine = (engine or "auto").lower()
    if engine in ("auto", "piper"):
        try:
            from bgassist.tts.piper import installed_voices

            voices = installed_voices()
            if voices:
                return voices
        except Exception:  # noqa: BLE001 - no piper, no voices
            pass
    if system == "darwin":
        return SayTts.available_voices()
    try:
        import pyttsx3

        engine_obj = pyttsx3.init()
        return [v.name for v in engine_obj.getProperty("voices") or []]
    except Exception:  # noqa: BLE001 - no SAPI5 available
        return []
