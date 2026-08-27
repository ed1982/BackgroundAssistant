"""Microphone capture via sounddevice (PortAudio).

The PortAudio callback only copies samples into an internal buffer and pushes
exact fixed-size frames onto a bounded queue — it never blocks and never does
heavy work, so audio timing is unaffected by whatever the rest of the app is
doing. Every frame also goes into the retro-transcription ring (§5.4.1).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from bgassist.audio.ring import AudioRing, BoundedFrameQueue

log = logging.getLogger("bgassist.audio.capture")

#: How much audio may sit unprocessed before we start dropping frames (§4.3).
QUEUE_SECONDS = 10.0
#: How much audio is kept for retro-transcription on barge-in.
RING_SECONDS = 8.0


class AudioCaptureError(RuntimeError):
    """The microphone could not be opened (missing permission, no device…)."""


class AudioCapture:
    """Streams 16-bit mono PCM frames from the default (or chosen) input."""

    def __init__(self, samplerate: int = 16000, frame_ms: int = 30, device=None,
                 queue_seconds: float = QUEUE_SECONDS,
                 ring_seconds: float = RING_SECONDS):
        self.samplerate = samplerate
        self.frame_ms = frame_ms
        self.device = device
        # int16 mono: 2 bytes per sample.
        self.frame_size = samplerate * 2 * frame_ms // 1000
        frames_per_second = 1000 // frame_ms
        self.queue = BoundedFrameQueue(maxlen=int(queue_seconds * frames_per_second))
        self.ring = AudioRing(seconds=ring_seconds, frame_ms=frame_ms)
        self._stream = None
        self._pending = bytearray()

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            import sounddevice as sd  # lazy: only needed at runtime
        except ImportError as exc:
            raise AudioCaptureError(f"sounddevice is not installed: {exc}") from exc

        log.info("opening microphone (device=%s, %d Hz, %d ms frames)",
                 self.device, self.samplerate, self.frame_ms)
        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="int16",
                blocksize=self.samplerate * self.frame_ms // 1000,
                device=self.device,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - PortAudio raises many types
            self._stream = None
            raise AudioCaptureError(
                f"could not open the microphone: {exc}. Check System Settings "
                "→ Privacy & Security → Microphone.") from exc

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("PortAudio status: %s", status)
        self._pending.extend(indata[:, 0].tobytes())
        while len(self._pending) >= self.frame_size:
            chunk = bytes(self._pending[:self.frame_size])
            del self._pending[:self.frame_size]
            self.queue.put(chunk)
            self.ring.add(chunk)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self.queue.clear()

    def __enter__(self) -> "AudioCapture":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def list_devices() -> List[str]:
    """List input-capable audio devices (for --list-devices and Preferences)."""
    import sounddevice as sd

    lines: List[str] = []
    for i, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0)) > 0:
            lines.append(f"{i}: {dev['name']} (inputs={dev['max_input_channels']})")
    return lines


def list_output_devices() -> List[str]:
    """List output-capable devices (Preferences → Voice → output device)."""
    import sounddevice as sd

    lines: List[str] = []
    for i, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_output_channels", 0)) > 0:
            lines.append(f"{i}: {dev['name']}")
    return lines


def output_is_builtin_speaker() -> Optional[bool]:
    """True when audio is going to the built-in speaker (barge-in layer 4).

    Returns None when it cannot be determined, which callers treat as "assume
    the acoustic path exists" — the conservative choice.
    """
    try:
        import sounddevice as sd

        info = sd.query_devices(kind="output")
        name = str(info.get("name", "")).lower()
    except Exception:  # noqa: BLE001 - no audio stack, or headless
        return None
    if not name:
        return None
    headphone_hints = ("headphone", "airpod", "buds", "usb", "bluetooth", "display")
    if any(hint in name for hint in headphone_hints):
        return False
    return "built-in" in name or "macbook" in name or "speaker" in name
