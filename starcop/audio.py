"""Microphone capture via sounddevice (PortAudio).

The PortAudio callback only copies samples into an internal buffer and
pushes exact fixed-size frames onto a queue — it never blocks or does
heavy work, so audio timing is unaffected by downstream processing.
"""
from __future__ import annotations

import logging
import queue
from typing import List, Optional

log = logging.getLogger("starcop.audio")


class AudioCapture:
    """Streams 16-bit mono PCM frames from the default (or chosen) input."""

    def __init__(self, samplerate: int = 16000, frame_ms: int = 30, device=None):
        self.samplerate = samplerate
        self.frame_ms = frame_ms
        self.device = device
        # int16 mono: 2 bytes per sample.
        self.frame_size = samplerate * 2 * frame_ms // 1000
        self.queue: "queue.Queue[bytes]" = queue.Queue()
        self._stream = None
        self._pending = bytearray()

    def start(self) -> None:
        import sounddevice as sd  # lazy: only needed at runtime

        if self._stream is not None:
            return
        log.info("Opening microphone (device=%s, %d Hz, %d ms frames)",
                 self.device, self.samplerate, self.frame_ms)
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            blocksize=self.samplerate * self.frame_ms // 1000,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("PortAudio status: %s", status)
        self._pending.extend(indata[:, 0].tobytes())
        while len(self._pending) >= self.frame_size:
            chunk = bytes(self._pending[:self.frame_size])
            del self._pending[:self.frame_size]
            self.queue.put(chunk)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def __enter__(self) -> "AudioCapture":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def list_devices() -> List[str]:
    """List input-capable audio devices (for --list-devices)."""
    import sounddevice as sd

    lines: List[str] = []
    for i, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0)) > 0:
            lines.append(f"{i}: {dev['name']} (inputs={dev['max_input_channels']})")
    return lines
