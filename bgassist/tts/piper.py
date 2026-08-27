"""Piper — local neural text-to-speech (D11).

Offline, free per use, and a great deal less dated than ``say``. The voice is
a pair of files (``.onnx`` plus ``.onnx.json``) in the models directory; one
small voice ships with the app and more can be downloaded in Preferences.

Piper is optional: if the package or the voice is missing, construction raises
:class:`TtsError` and the caller falls back to the system voice, which is why
the app still speaks on a fresh install.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import wave
from pathlib import Path
from typing import List, Optional

from bgassist.tts.base import TtsError

log = logging.getLogger("bgassist.tts.piper")

DEFAULT_VOICE = "en_GB-alba-medium"


def voices_dir() -> Path:
    from bgassist.platform import paths

    directory = paths.models_dir() / "voices"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def installed_voices() -> List[str]:
    """Voice names with both files present."""
    out: List[str] = []
    for onnx in sorted(voices_dir().glob("*.onnx")):
        if onnx.with_suffix(".onnx.json").exists() or Path(
                str(onnx) + ".json").exists():
            out.append(onnx.stem)
    return out


class PiperTts:
    """Speaks via the piper CLI, writing to a temp WAV and playing it.

    The CLI is used rather than the Python API because it is what ships in the
    bundle and because killing a subprocess is a reliable ``stop()`` — the
    thing barge-in depends on.
    """

    name = "piper"

    def __init__(self, voice: str = DEFAULT_VOICE, rate: float = 1.0,
                 executable: Optional[str] = None, player: Optional[str] = None):
        self.voice = voice or DEFAULT_VOICE
        self.rate = float(rate or 1.0)
        self.executable = executable or shutil.which("piper")
        if not self.executable:
            raise TtsError("piper is not installed (pip install 'backgroundassistant[piper]')")
        self.model_path = self._resolve_voice(self.voice)
        self.player = player or self._find_player()
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._stopping = False

    @staticmethod
    def _find_player() -> str:
        for candidate in ("afplay", "aplay", "paplay", "ffplay"):
            found = shutil.which(candidate)
            if found:
                return found
        raise TtsError("no audio player found for piper output")

    @staticmethod
    def _resolve_voice(voice: str) -> Path:
        path = voices_dir() / f"{voice}.onnx"
        if not path.exists():
            raise TtsError(f"piper voice {voice!r} is not installed")
        return path

    def speak(self, text: str) -> None:
        import tempfile

        self._stopping = False
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            wav_path = fh.name
        try:
            cmd = [self.executable, "--model", str(self.model_path),
                   "--output_file", wav_path]
            if self.rate and self.rate != 1.0:
                # piper expresses speed as seconds per phoneme: lower is faster.
                cmd += ["--length_scale", f"{1.0 / self.rate:.3f}"]
            with self._lock:
                self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                              stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE)
            proc = self._proc
            _out, err = proc.communicate(text.encode("utf-8"), timeout=120)
            if proc.returncode != 0 and not self._stopping:
                raise TtsError(f"piper failed: {err.decode('utf-8', 'replace')[:200]}")
            if self._stopping:
                return
            with self._lock:
                self._proc = subprocess.Popen([self.player, wav_path],
                                              stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
            rc = self._proc.wait(timeout=300)
            if rc != 0 and not self._stopping:
                raise TtsError(f"audio playback exited with code {rc}")
        except (OSError, subprocess.SubprocessError) as exc:
            raise TtsError(f"piper failed: {exc}") from exc
        finally:
            with self._lock:
                self._proc = None
            try:
                Path(wav_path).unlink()
            except OSError:  # pragma: no cover
                pass

    def stop(self) -> None:
        self._stopping = True
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:  # pragma: no cover
                pass

    @staticmethod
    def duration_of(wav_path: str) -> float:  # pragma: no cover - helper
        with wave.open(wav_path, "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
