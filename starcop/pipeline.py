"""The assistant's decision pipeline: a pure, testable state machine.

States::

    IDLE -> AWAITING_COMMAND -> THINKING -> SPEAKING -> IDLE

- IDLE: every completed utterance is transcribed and appended to the
  rolling transcript buffer. A trigger word moves us to AWAITING_COMMAND.
- AWAITING_COMMAND: further utterances extend the command and push out a
  silence deadline; dispatch happens on the deadline or the hard time cap.
- THINKING: the LLM is called (blocking, on the worker thread).
- SPEAKING: TTS speaks the response; audio is drained by the runner so the
  computer never transcribes its own voice.

All time comes from an injectable clock (monotonic seconds) so tests can
drive the machine deterministically. Only the worker thread calls
feed_utterance()/tick().
"""
from __future__ import annotations

import enum
import logging
import time
from typing import Callable, List, Optional

log = logging.getLogger("starcop.pipeline")


class State(enum.Enum):
    IDLE = "idle"
    AWAITING_COMMAND = "awaiting_command"
    THINKING = "thinking"
    SPEAKING = "speaking"


class Pipeline:
    def __init__(self, transcriber, llm, tts, wakeword, buffer, cfg,
                 clock: Callable[[], float] = time.monotonic,
                 on_state_change: Optional[Callable[[State], None]] = None):
        self.transcriber = transcriber
        self.llm = llm
        self.tts = tts
        self.wakeword = wakeword
        self.buffer = buffer
        self.cfg = cfg
        self.clock = clock
        self.on_state_change = on_state_change

        self.state = State.IDLE
        self.command_parts: List[str] = []
        self.trigger_ts: Optional[float] = None
        self._deadline: Optional[float] = None

    # -- helpers ----------------------------------------------------------
    def _set_state(self, new: State) -> None:
        if self.state is not new:
            log.info("state %s -> %s", self.state.value, new.value)
            self.state = new
            if self.on_state_change is not None:
                try:
                    self.on_state_change(new)
                except Exception:  # noqa: BLE001 - UI callbacks must not kill the pipeline
                    log.exception("on_state_change callback failed")

    # -- inputs (called by the worker thread) ------------------------------
    def feed_utterance(self, audio: bytes) -> None:
        """Handle one completed utterance of 16 kHz mono int16 PCM."""
        try:
            text = (self.transcriber.transcribe(audio) or "").strip()
        except Exception:  # noqa: BLE001 - a bad utterance must not kill listening
            log.exception("transcription failed; dropping utterance")
            return
        if not text:
            return

        now = self.clock()
        log.info("heard: %r", text)
        self.buffer.add(time.time(), text)

        if self.state is State.IDLE:
            split = self.wakeword.split_command(text)
            if split is not None:
                self._begin_command(split, now)
        elif self.state is State.AWAITING_COMMAND:
            split = self.wakeword.split_command(text)
            if split is not None:
                log.info("re-triggered; resetting command")
                self._begin_command(split, now)
            else:
                self.command_parts.append(text)
                self._deadline = now + self.cfg.command_end_silence_ms / 1000.0

    def tick(self, now: Optional[float] = None) -> None:
        """Advance deadline logic. Called frequently by the worker."""
        if self.state is not State.AWAITING_COMMAND:
            return
        now = self.clock() if now is None else now
        cap_hit = (now - self.trigger_ts) >= (self.cfg.max_command_wait_ms / 1000.0)
        deadline_hit = self._deadline is not None and now >= self._deadline
        if cap_hit or deadline_hit:
            self._dispatch()

    # -- internals ----------------------------------------------------------
    def _begin_command(self, split: tuple, now: float) -> None:
        trigger, command = split
        log.info("wake word %r detected", trigger)
        self.command_parts = [command] if command else []
        self.trigger_ts = now
        self._deadline = now + self.cfg.command_end_silence_ms / 1000.0
        self._set_state(State.AWAITING_COMMAND)

    def _dispatch(self) -> None:
        query = " ".join(p for p in self.command_parts if p).strip()
        context = self.buffer.recent_text(seconds=self.cfg.context_seconds)
        log.info("dispatching to LLM (query=%r, context_lines=%d)",
                 query, len(context.splitlines()) if context else 0)

        self._set_state(State.THINKING)
        try:
            response = (self.llm.ask(context, query) or "").strip()
        except Exception as exc:  # noqa: BLE001 - LLM failures must not kill listening
            log.error("LLM failed: %s", exc)
            response = "I'm sorry, I could not process that. Please try again."
        if not response:
            response = "I'm sorry, I have nothing to report."

        self._set_state(State.SPEAKING)
        try:
            self.tts.speak(response)
        except Exception as exc:  # noqa: BLE001 - TTS failures must not kill listening
            log.error("TTS failed: %s", exc)

        self._set_state(State.IDLE)

    def reset(self) -> None:
        """Back to IDLE, clearing any in-flight command (e.g. Stop pressed)."""
        self.command_parts = []
        self.trigger_ts = None
        self._deadline = None
        self._set_state(State.IDLE)
