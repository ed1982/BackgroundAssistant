"""The acknowledgement chime (§5.2, item 3).

The moment the trigger is spotted the user gets a sound and an icon change,
before any transcription has finished. Without it there is no feedback at all
for one to three seconds, so people repeat themselves — which re-triggers.

The chime is synthesised rather than shipped as an audio file so there is no
binary asset to package, and it is played on a throwaway thread so nothing
ever waits on it.
"""
from __future__ import annotations

import logging
import math
import struct
import threading
import wave
from pathlib import Path
from typing import Optional

log = logging.getLogger("bgassist.audio.chime")

SAMPLERATE = 44100


def _render(path: Path, notes=((880.0, 0.08), (1320.0, 0.12)),
            volume: float = 0.22) -> Path:
    """Two short sine notes with a quick fade, written once and cached."""
    frames = bytearray()
    for frequency, duration in notes:
        count = int(SAMPLERATE * duration)
        for i in range(count):
            fade = min(1.0, (count - i) / (0.35 * count))
            attack = min(1.0, i / max(1.0, 0.02 * count))
            value = math.sin(2 * math.pi * frequency * i / SAMPLERATE)
            frames += struct.pack("<h", int(32767 * volume * value * fade * attack))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLERATE)
        wf.writeframes(bytes(frames))
    return path


class Chime:
    def __init__(self, enabled: bool = True, path: Optional[Path] = None):
        self.enabled = enabled
        self._path = path
        self._lock = threading.Lock()

    def _ensure(self) -> Optional[Path]:
        if self._path is not None and Path(self._path).exists():
            return Path(self._path)
        try:
            from bgassist.platform import paths

            target = paths.data_dir() / "chime.wav"
            if not target.exists():
                _render(target)
            self._path = target
            return target
        except Exception:  # noqa: BLE001 - a missing chime is not an error
            log.debug("could not prepare the chime", exc_info=True)
            return None

    def play(self) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._play_blocking, name="bgassist-chime",
                         daemon=True).start()

    def _play_blocking(self) -> None:
        path = self._ensure()
        if path is None:
            return
        import shutil
        import subprocess
        import sys

        player = None
        if sys.platform == "darwin":
            player = shutil.which("afplay")
        elif sys.platform.startswith("win"):
            try:
                import winsound

                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except Exception:  # noqa: BLE001 - fall through to a player
                player = None
        else:
            player = shutil.which("aplay") or shutil.which("paplay")
        if not player:
            return
        try:
            subprocess.run([player, str(path)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        except Exception:  # noqa: BLE001 - never let a sound break anything
            log.debug("chime playback failed", exc_info=True)
