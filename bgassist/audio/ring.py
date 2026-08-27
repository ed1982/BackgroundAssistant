"""Bounded audio structures: the frame queue and the retro-transcription ring.

Today's unbounded queue is why lag compounds rather than degrading (F5): while
the worker was busy in Whisper or TTS, frames piled up until it processed a
twenty-one-second "utterance" in one lump. A dropped frame is a better failure
than a growing backlog, so both structures here have a hard bound and an
explicit, counted drop policy (§4.3).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, List, Optional

log = logging.getLogger("bgassist.audio.ring")


class BoundedFrameQueue:
    """A queue of PCM frames that drops the *oldest* frame when full.

    Dropping the oldest rather than the newest keeps the audio the consumer is
    about to look at as fresh as possible, which is what matters for a live
    assistant. Drops are counted and logged at most once every
    *log_interval_s* so a busy period cannot itself flood the log.
    """

    def __init__(self, maxlen: int, log_interval_s: float = 5.0,
                 clock=time.monotonic):
        self.maxlen = int(maxlen)
        self.log_interval_s = float(log_interval_s)
        self.clock = clock
        self.dropped = 0
        self._items: Deque[bytes] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._last_log = 0.0
        self._on_drop = None  # optional callback(total_dropped)

    def set_drop_callback(self, callback) -> None:
        self._on_drop = callback

    def put(self, frame: bytes) -> None:
        """Never blocks: the PortAudio callback must never wait on us."""
        notify_drop = False
        with self._not_empty:
            self._items.append(frame)
            while len(self._items) > self.maxlen:
                self._items.popleft()
                self.dropped += 1
                now = self.clock()
                if now - self._last_log >= self.log_interval_s:
                    self._last_log = now
                    notify_drop = True
            self._not_empty.notify()
        if notify_drop:
            log.warning("audio backlog: dropped %d frame(s) so far", self.dropped)
            if self._on_drop is not None:
                try:
                    self._on_drop(self.dropped)
                except Exception:  # noqa: BLE001 - a listener must not break capture
                    log.exception("drop callback failed")

    def get(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Pop the oldest frame, or None if *timeout* elapses first."""
        with self._not_empty:
            if not self._items:
                self._not_empty.wait(timeout)
            if not self._items:
                return None
            return self._items.popleft()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class AudioRing:
    """The last *seconds* of raw audio, for retro-transcription (§5.4.1).

    When the user interrupts, the words they spoke *before* the trigger were
    never transcribed (full transcription is off while speaking). Re-reading
    them from this ring is what makes "…what did you mean, Computer?" work as
    a barge-in rather than losing the question.
    """

    def __init__(self, seconds: float = 8.0, frame_ms: int = 30):
        self.frame_ms = int(frame_ms)
        self.max_frames = max(1, int(seconds * 1000 / self.frame_ms))
        self._frames: Deque[bytes] = deque(maxlen=self.max_frames)
        self._lock = threading.Lock()

    def add(self, frame: bytes) -> None:
        with self._lock:
            self._frames.append(frame)

    def tail(self, seconds: float) -> bytes:
        """The most recent *seconds* of audio as one PCM block."""
        want = max(1, int(seconds * 1000 / self.frame_ms))
        with self._lock:
            frames: List[bytes] = list(self._frames)[-want:]
        return b"".join(frames)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)
