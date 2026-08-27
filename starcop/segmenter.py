"""VAD-driven utterance segmentation (endpointing).

Turns a stream of fixed-size PCM frames into complete utterances:
- pre-roll is kept so speech onsets are not clipped;
- an utterance ends after *end_silence_ms* of continuous silence, or at the
  hard *max_utterance_ms* cap;
- utterances shorter than *min_utterance_ms* are dropped (coughs, clicks).

The segmenter is pure logic: it takes any object with
``is_speech(frame: bytes) -> bool``, which keeps it unit-testable without
webrtcvad installed.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional


class UtteranceSegmenter:
    def __init__(self, vad, frame_ms: int = 30, pre_roll_ms: int = 360,
                 end_silence_ms: int = 700, min_utterance_ms: int = 300,
                 max_utterance_ms: int = 30000):
        if frame_ms not in (10, 20, 30):
            raise ValueError("frame_ms must be 10, 20 or 30 (webrtcvad)")
        self.vad = vad
        self.frame_ms = frame_ms
        self.pre_roll_frames = max(1, pre_roll_ms // frame_ms)
        self.end_silence_frames = max(1, end_silence_ms // frame_ms)
        self.min_utterance_frames = max(1, min_utterance_ms // frame_ms)
        self.max_utterance_frames = max(
            self.min_utterance_frames + 1, max_utterance_ms // frame_ms)
        self._pre: Deque[bytes] = deque(maxlen=self.pre_roll_frames)
        self._buf: Optional[bytearray] = None
        self._frames = 0
        self._silence_frames = 0

    def process_frame(self, frame: bytes) -> Optional[bytes]:
        """Feed one PCM frame; returns a completed utterance (or None)."""
        voiced = self.vad.is_speech(frame)

        if self._buf is None:
            # Not in an utterance: keep a pre-roll window of recent frames.
            self._pre.append(frame)
            if voiced:
                # Speech started: begin the utterance with the pre-roll so
                # the onset is not clipped.
                self._buf = bytearray(b"".join(self._pre))
                self._frames = len(self._pre)
                self._silence_frames = 0
            return None

        self._buf.extend(frame)
        self._frames += 1
        if voiced:
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        if (self._silence_frames >= self.end_silence_frames
                or self._frames >= self.max_utterance_frames):
            return self._flush()
        return None

    def _flush(self) -> Optional[bytes]:
        audio = bytes(self._buf) if self._buf else b""
        frames = self._frames
        self._reset()
        if frames < self.min_utterance_frames:
            return None  # too short to be meaningful speech
        return audio

    def _reset(self) -> None:
        self._buf = None
        self._frames = 0
        self._silence_frames = 0

    def flush(self) -> Optional[bytes]:
        """Return any in-progress utterance (e.g. at end of a file/stream).

        Live listening does not need this — silence always eventually ends an
        utterance — but file-based processing (self-test) must flush the tail.
        """
        if self._buf is None:
            return None
        audio = bytes(self._buf)
        frames = self._frames
        self._reset()
        if frames < self.min_utterance_frames:
            return None
        return audio

    def reset(self) -> None:
        """Discard any in-progress utterance (e.g. when listening stops)."""
        self._reset()
