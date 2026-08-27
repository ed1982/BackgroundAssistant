"""Text-to-speech engines.

- ``say``: macOS ``say(1)`` subprocess — offline, zero extra dependencies.
- ``pyttsx3``: SAPI5 on Windows (default there); the engine lives on a
  dedicated thread with an internal queue because pyttsx3 is not
  thread-safe.
- ``mock``: records spoken text instead of speaking (tests).

All engines implement ``speak(text) -> None`` and raise TtsError on failure.
"""
from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
from typing import List, Optional

log = logging.getLogger("starcop.tts")


class TtsError(RuntimeError):
    pass


class MockTts:
    name = "mock"

    def __init__(self) -> None:
        self.spoken: List[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        log.info("[mock tts] %s", text)


class SayTts:
    """macOS ``say(1)`` — offline, no extra dependencies."""

    name = "say"

    def __init__(self, rate: int = 185, voice: Optional[str] = None):
        self._base_cmd = ["say", "-r", str(int(rate))]
        if voice:
            self._base_cmd += ["-v", voice]
        self._proc: Optional[subprocess.Popen] = None

    def speak(self, text: str) -> None:
        try:
            self._proc = subprocess.Popen([*self._base_cmd, text])
            rc = self._proc.wait(timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            raise TtsError(f"`say` failed: {exc}") from exc
        finally:
            self._proc = None
        if rc != 0:
            raise TtsError(f"`say` exited with code {rc}")

    def stop(self) -> None:
        """Kill an in-flight `say` (used on shutdown)."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:  # pragma: no cover - already gone
                pass


class Pyttsx3Tts:
    """pyttsx3 on a dedicated thread (the engine is not thread-safe)."""

    name = "pyttsx3"

    def __init__(self, rate: int = 185, voice: Optional[str] = None):
        self._rate = int(rate)
        self._voice = voice
        self._queue: "queue.Queue" = queue.Queue()
        self._ready = threading.Event()
        self._init_error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, name="tts-engine",
                                        daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise TtsError("pyttsx3 engine did not start in time")
        if self._init_error is not None:
            raise TtsError(f"pyttsx3 init failed: {self._init_error}")

    def _run(self) -> None:
        engine = None
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", self._rate)
            if self._voice:
                for v in engine.getProperty("voices") or []:
                    if self._voice.lower() in (v.name or "").lower():
                        engine.setProperty("voice", v.id)
                        break
        except BaseException as exc:  # noqa: BLE001 - report any init failure
            self._init_error = exc
        finally:
            self._ready.set()

        while True:
            item = self._queue.get()
            if item is None:  # shutdown sentinel
                break
            text, done = item
            try:
                engine.say(text)
                engine.runAndWait()
            except BaseException as exc:  # noqa: BLE001 - keep the loop alive
                log.error("pyttsx3 speak failed: %s", exc)
            finally:
                done.set()

    def speak(self, text: str) -> None:
        if self._init_error is not None:
            raise TtsError(f"pyttsx3 unavailable: {self._init_error}")
        done = threading.Event()
        self._queue.put((text, done))
        if not done.wait(timeout=180):
            raise TtsError("TTS timed out")

    def stop(self) -> None:
        try:
            self._queue.put(None)
        except Exception:  # noqa: BLE001 - best effort shutdown
            pass


def make_tts(tts_cfg) -> object:
    """Instantiate the configured TTS engine.

    ``engine="auto"`` picks ``say`` on macOS and ``pyttsx3`` elsewhere.
    """
    engine = (getattr(tts_cfg, "engine", "") or "auto").lower()
    rate = int(getattr(tts_cfg, "rate", 185) or 185)
    voice = getattr(tts_cfg, "voice", None)

    if engine == "auto":
        engine = "say" if sys.platform == "darwin" else "pyttsx3"
    if engine == "mock":
        return MockTts()
    if engine == "say":
        if sys.platform != "darwin":
            log.warning("tts engine 'say' is macOS-only; falling back to pyttsx3")
            return Pyttsx3Tts(rate=rate, voice=voice)
        return SayTts(rate=rate, voice=voice)
    if engine == "pyttsx3":
        return Pyttsx3Tts(rate=rate, voice=voice)
    raise ValueError(f"Unknown TTS engine: {engine!r}")
