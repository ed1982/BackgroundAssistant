"""The operating system's own voices: macOS ``say`` and Windows SAPI5.

These are the fallback for Piper (D11) and the reason the app works the moment
it is installed, with no voice download.
"""
from __future__ import annotations

import logging
import queue
import subprocess
import threading
from typing import List, Optional

from bgassist.tts.base import TtsError

log = logging.getLogger("bgassist.tts.system")

#: What "Automatic" resolves to on macOS, best first. Whichever is installed
#: wins; if none is, `say` uses the system default.
#:
#: Siri's own voices (Pippa, Jamie, Nicky…) are not in this list and cannot
#: be: Apple does not expose them to `say` or to AVSpeechSynthesizer, so no
#: third-party app can use them. Nothing to work around — the fix is to
#: install an Enhanced or Premium voice in System Settings, which are the
#: good ones and are meant to be used this way.
PREFERRED_VOICES = ("Tessa", "Serena", "Fiona", "Moira", "Daniel", "Karen",
                    "Samantha", "Ava")


class SayTts:
    """macOS ``say(1)`` — offline, no extra dependencies."""

    name = "say"

    def __init__(self, rate: int = 185, voice: Optional[str] = None):
        self.voice = self.resolve_voice(voice)
        self._base_cmd = ["say", "-r", str(int(rate))]
        if self.voice:
            self._base_cmd += ["-v", self.voice]
        self._proc: Optional[subprocess.Popen] = None
        self._stopping = False

    @classmethod
    def resolve_voice(cls, voice: Optional[str]) -> Optional[str]:
        """Turn a request into a voice that actually exists on this Mac.

        `say -v Tessa` on a machine without Tessa exits non-zero, which used to
        surface as "TTS failed" and total silence — the assistant losing its
        voice over a preference. A voice that is not installed is simply not
        used.
        """
        installed = cls.available_voices()
        if not installed:  # `say -v ?` unavailable: let `say` decide
            return voice
        lookup = {name.lower(): name for name in installed}
        if voice:
            found = lookup.get(voice.strip().lower())
            if found:
                return found
            log.warning("the voice %r is not installed on this Mac; using the "
                        "best available one instead. Install more in System "
                        "Settings → Accessibility → Spoken Content → System "
                        "Voice → Manage Voices.", voice)
        for candidate in PREFERRED_VOICES:
            found = lookup.get(candidate.lower())
            if found:
                return found
        return None

    def speak(self, text: str) -> None:
        self._stopping = False
        try:
            self._proc = subprocess.Popen([*self._base_cmd, text])
            rc = self._proc.wait(timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            raise TtsError(f"`say` failed: {exc}") from exc
        finally:
            self._proc = None
        if rc != 0 and not self._stopping:
            raise TtsError(f"`say` exited with code {rc}")

    def stop(self) -> None:
        """Kill an in-flight `say` (barge-in, Stop, shutdown)."""
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:  # pragma: no cover - already gone
                pass

    #: `say -v ?` is a subprocess, and a voice is resolved every time settings
    #: change; the list of installed voices does not move underneath us often
    #: enough to pay for that each time.
    _voice_cache: Optional[List[str]] = None

    @classmethod
    def available_voices(cls, refresh: bool = False) -> List[str]:
        if cls._voice_cache is not None and not refresh:
            return cls._voice_cache
        voices: List[str] = []
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True,
                                 text=True, timeout=10)
            for line in out.stdout.splitlines():
                parts = line.split()
                if parts:
                    voices.append(parts[0])
        except Exception:  # noqa: BLE001 - not being able to list voices is
            # not a reason to be unable to speak
            log.debug("could not list system voices", exc_info=True)
            voices = []
        cls._voice_cache = voices
        return voices


class Pyttsx3Tts:
    """pyttsx3 on a dedicated thread (the engine is not thread-safe)."""

    name = "pyttsx3"

    def __init__(self, rate: int = 185, voice: Optional[str] = None):
        self._rate = int(rate)
        self._voice = voice
        self._queue: "queue.Queue" = queue.Queue()
        self._ready = threading.Event()
        self._init_error: Optional[BaseException] = None
        self._engine = None
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
            self._engine = engine
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
        engine = self._engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001 - best effort
                pass

    def shutdown(self) -> None:
        try:
            self._queue.put(None)
        except Exception:  # noqa: BLE001 - best effort shutdown
            pass
