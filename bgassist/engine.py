"""The worker threads and how they are wired together (fixes F1, F5, F7).

One thread used to do four jobs: it read the microphone queue, ran Whisper,
called the LLM and blocked on TTS — while frames piled up on an unbounded
queue behind it. That is the twenty-one-second "utterance" in the log, and it
is why the lag compounded instead of degrading.

Now each stage has its own thread and every queue between them is bounded:

    PortAudio callback -> frame queue -> [audio thread] -> utterance queue
                                              |                  |
                                          spotter           [stt thread]
                                                                 |
                                                          orchestrator
                                                                 |
                                                     [responder thread] -> speech

The ``Runner`` that used to *be* a ``Thread`` and shadowed its private
``_stop`` attribute — crashing every Stop and every Quit (F1) — is replaced by
this object, which *holds* its threads and owns a plainly named
``_stop_event``.
"""
from __future__ import annotations

import logging
import queue as _queue
import threading
from typing import Optional

from bgassist.core import events

log = logging.getLogger("bgassist.engine")

UTTERANCE_QUEUE = 8


class Engine:
    """Owns the audio and transcription threads for one listening session."""

    def __init__(self, capture, segmenter, transcriber, orchestrator,
                 spotter=None, poll_timeout: float = 0.2,
                 utterance_queue: int = UTTERANCE_QUEUE, bus=None):
        self.capture = capture
        self.segmenter = segmenter
        self.transcriber = transcriber
        self.orchestrator = orchestrator
        self.spotter = spotter
        # So the orchestrator can raise the spotter's bar while it is speaking.
        orchestrator.spotter = spotter
        self.poll_timeout = float(poll_timeout)
        self.bus = bus or getattr(orchestrator, "bus", None) or events.EventBus()

        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._utterances: "_queue.Queue" = _queue.Queue(maxsize=utterance_queue)
        self.dropped_utterances = 0

    # -- lifecycle -------------------------------------------------------
    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        drain(self._utterances)
        if hasattr(self.capture, "queue") and hasattr(self.capture.queue, "set_drop_callback"):
            self.capture.queue.set_drop_callback(self._on_frames_dropped)
        self._threads = [
            threading.Thread(target=self._audio_loop, name="bgassist-audio",
                             daemon=True),
            threading.Thread(target=self._stt_loop, name="bgassist-stt",
                             daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        log.info("engine started")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop every thread. Safe to call twice, and safe from any thread."""
        self._stop_event.set()
        try:
            self._utterances.put_nowait(None)  # wake the stt thread
        except _queue.Full:  # pragma: no cover - it will time out instead
            pass
        for thread in self._threads:
            if thread.is_alive() and threading.current_thread() is not thread:
                thread.join(timeout)
        self._threads = []
        cancel = getattr(self.orchestrator, "cancel", None)
        if callable(cancel):
            try:
                cancel(reason="stopped")
            except Exception:  # noqa: BLE001 - best effort shutdown
                log.exception("cancelling the orchestrator failed")
        log.info("engine stopped")

    # -- audio thread ----------------------------------------------------
    def _audio_loop(self) -> None:
        from bgassist.core.orchestrator import State

        log.info("audio thread started")
        while not self._stop_event.is_set():
            frame = self._next_frame()
            if frame is None:
                # No audio (yet): still advance the deadline logic (F7).
                self.orchestrator.tick()
                continue

            if self.spotter is not None:
                self._feed_spotter(frame)

            if self.orchestrator.state in (State.THINKING, State.SPEAKING):
                # Discard: we never transcribe our own voice. Resetting is what
                # the old code forgot, which glued half an utterance from
                # before the answer onto whatever was said after it (F7).
                self.segmenter.reset()
                self.orchestrator.tick()
                continue

            try:
                utterance = self.segmenter.process_frame(frame)
            except Exception:  # noqa: BLE001 - a bad frame must not stop listening
                log.exception("segmentation failed")
                utterance = None
            if utterance is not None:
                self._offer(utterance)
            self.orchestrator.tick()
        log.info("audio thread stopped")

    def _next_frame(self) -> Optional[bytes]:
        source = self.capture.queue
        getter = getattr(source, "get", None)
        try:
            if isinstance(source, _queue.Queue):
                return source.get(timeout=self.poll_timeout)
            return getter(timeout=self.poll_timeout)
        except _queue.Empty:
            return None

    def _feed_spotter(self, frame: bytes) -> None:
        try:
            hit = self.spotter.process_frame(frame)
        except Exception:  # noqa: BLE001 - the spotter is optional polish
            log.exception("wake-word spotter failed; disabling it")
            self.spotter = None
            return
        if hit:
            self.orchestrator.on_spotter_trigger(
                confidence=getattr(hit, "confidence", 1.0))

    def _offer(self, utterance: bytes) -> None:
        """Queue an utterance, dropping the oldest when we are behind (§4.3)."""
        try:
            self._utterances.put_nowait(utterance)
            return
        except _queue.Full:
            pass
        try:
            self._utterances.get_nowait()
            self.dropped_utterances += 1
        except _queue.Empty:  # pragma: no cover - race with the stt thread
            pass
        try:
            self._utterances.put_nowait(utterance)
        except _queue.Full:  # pragma: no cover
            pass
        log.warning("transcription backlog: dropped %d utterance(s)",
                    self.dropped_utterances)
        self.bus.publish(events.AudioBacklog(dropped_frames=self.dropped_utterances))

    # -- transcription thread --------------------------------------------
    def _stt_loop(self) -> None:
        from bgassist.stt.base import TranscriberError

        log.info("transcription thread started")
        while not self._stop_event.is_set():
            try:
                utterance = self._utterances.get(timeout=self.poll_timeout)
            except _queue.Empty:
                continue
            if utterance is None:  # shutdown sentinel
                break
            try:
                text = (self.transcriber.transcribe(utterance) or "").strip()
            except TranscriberError as exc:
                self.orchestrator.report_error(exc.user_message, str(exc),
                                               fatal=not exc.transient)
                if not exc.transient:
                    break
                continue
            except Exception as exc:  # noqa: BLE001
                self.orchestrator.report_error("Speech recognition failed.",
                                               str(exc))
                continue
            if text:
                self.orchestrator.on_transcript(text)
        log.info("transcription thread stopped")

    def _on_frames_dropped(self, total: int) -> None:
        self.bus.publish(events.AudioBacklog(dropped_frames=total))

    # -- retro-transcription (§5.4.1) ------------------------------------
    def retro_transcribe(self, seconds: float = 5.0) -> str:
        """Transcribe the last *seconds* from the capture ring buffer.

        Called once, on an interruption, to recover the words the user spoke
        before the trigger — the audio was captured, it simply was not being
        transcribed while we were speaking.
        """
        ring = getattr(self.capture, "ring", None)
        if ring is None:
            return ""
        audio = ring.tail(seconds)
        if not audio:
            return ""
        try:
            return (self.transcriber.transcribe(audio) or "").strip()
        except Exception:  # noqa: BLE001 - recovery is best effort
            log.exception("retro-transcription failed")
            return ""


def drain(q: "_queue.Queue") -> None:
    while True:
        try:
            q.get_nowait()
        except _queue.Empty:
            return
