"""Worker thread: mic frames -> VAD segmentation -> pipeline.

Single consumer of the audio queue, so no locks are needed on the
segmenter or pipeline. While the pipeline is THINKING or SPEAKING, frames
are drained and discarded so the computer never transcribes its own voice.
"""
from __future__ import annotations

import logging
import queue as _queue
import threading

log = logging.getLogger("starcop.runner")


class Runner(threading.Thread):
    def __init__(self, capture, segmenter, pipeline, poll_timeout: float = 0.2):
        super().__init__(name="starcop-worker", daemon=True)
        self.capture = capture
        self.segmenter = segmenter
        self.pipeline = pipeline
        self.poll_timeout = poll_timeout
        self._stop = threading.Event()

    def run(self) -> None:
        from .pipeline import State  # local import keeps module graph tidy

        log.info("worker started")
        while not self._stop.is_set():
            try:
                frame = self.capture.queue.get(timeout=self.poll_timeout)
            except _queue.Empty:
                # No audio (yet): still advance deadline logic.
                self.pipeline.tick()
                continue

            if self.pipeline.state in (State.THINKING, State.SPEAKING):
                continue  # drain: never transcribe the computer's own voice

            utterance = self.segmenter.process_frame(frame)
            if utterance is not None:
                self.pipeline.feed_utterance(utterance)
            self.pipeline.tick()
        log.info("worker stopped")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if threading.current_thread() is not self:
            self.join(timeout)
