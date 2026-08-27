"""The decision state machine (replaces pipeline.py).

States::

    IDLE -> AWAITING_COMMAND -> THINKING -> SPEAKING -> IDLE

- **IDLE** — every transcribed utterance goes into the RAM-only rolling buffer.
  A trigger word moves us on; a *trailing* trigger dispatches immediately
  because the question has already been asked (§5.1).
- **AWAITING_COMMAND** — further utterances extend the command and push out a
  silence deadline; dispatch happens on the deadline or the hard time cap.
- **THINKING / SPEAKING** — the answer streams and is spoken sentence by
  sentence on the responder, which stays cancellable throughout (§5.5).

All time comes from an injectable clock, so the machine is driven
deterministically in tests. This object holds no I/O of its own: transcription
happens upstream, speech happens in the responder.
"""
from __future__ import annotations

import enum
import logging
import time
from typing import Callable, List, Optional

from bgassist.core import events
from bgassist.core.echo import subtract_playback
from bgassist.core.responder import AnswerRequest, AnswerResult, SpeechResponder
from bgassist.core.trigger import TriggerMatch
from bgassist.logging_setup import transcript_log

log = logging.getLogger("bgassist.core.orchestrator")


class State(enum.Enum):
    IDLE = "idle"
    AWAITING_COMMAND = "awaiting_command"
    THINKING = "thinking"
    SPEAKING = "speaking"


class Orchestrator:
    def __init__(self, llm=None, tts=None, trigger=None, buffer=None, cfg=None,
                 clock: Callable[[], float] = time.monotonic,
                 transcriber=None, bus=None, conversations=None, responder=None,
                 on_state_change: Optional[Callable[[State], None]] = None,
                 retro_transcribe: Optional[Callable[[float], str]] = None,
                 threaded_responder: bool = False):
        self.llm = llm
        self.tts = tts
        self.trigger = trigger
        self.buffer = buffer
        self.cfg = cfg
        self.clock = clock
        self.transcriber = transcriber
        self.bus = bus or events.EventBus()
        self.conversations = conversations
        self.on_state_change = on_state_change
        self.retro_transcribe = retro_transcribe

        self.responder = responder or SpeechResponder(
            llm, tts, threaded=threaded_responder,
            on_speaking=self._on_speaking, on_token=self._on_token,
            on_done=self._on_answer_done)
        # A responder handed in by a caller still needs its callbacks wired.
        for name, value in (("on_speaking", self._on_speaking),
                            ("on_token", self._on_token),
                            ("on_done", self._on_answer_done)):
            if getattr(self.responder, name, None) is None:
                setattr(self.responder, name, value)

        self.state = State.IDLE
        self.command_parts: List[str] = []
        self.trigger_ts: Optional[float] = None
        self.last_error: str = ""
        self._deadline: Optional[float] = None
        self._match: Optional[TriggerMatch] = None
        self._conversation_id: Optional[int] = None
        self._user_message_id: Optional[int] = None
        self._spoke_anything = False
        #: While the chat window's push-to-talk button is held, speech is
        #: addressed to us by definition and the trigger word is bypassed (D10).
        self.push_to_talk = False
        self._ptt_parts: List[str] = []
        #: Set by the engine so the spotter can raise its threshold while we
        #: are the ones making noise (§5.4.2, layer 3).
        self.spotter = None

    # -- config helpers ---------------------------------------------------
    def _cfg(self, name: str, default):
        return getattr(self.cfg, name, default) if self.cfg is not None else default

    # -- state ------------------------------------------------------------
    def _set_state(self, new: State) -> None:
        if self.state is new:
            return
        previous, self.state = self.state, new
        self._update_spotter_sensitivity(new)
        log.info("state %s -> %s", previous.value, new.value)
        self.bus.publish(events.StateChanged(new.value, previous.value))
        if self.on_state_change is not None:
            try:
                self.on_state_change(new)
            except Exception:  # noqa: BLE001 - a UI callback must not kill us
                log.exception("on_state_change callback failed")

    # -- inputs -----------------------------------------------------------
    def feed_utterance(self, audio: bytes) -> None:
        """Transcribe one utterance and act on it (used by --selftest).

        The threaded engine transcribes on its own thread and calls
        :meth:`on_transcript` instead.
        """
        from bgassist.stt.base import TranscriberError

        if self.transcriber is None:
            return
        try:
            text = (self.transcriber.transcribe(audio) or "").strip()
        except TranscriberError as exc:
            self._report_error(exc.user_message, str(exc), fatal=not exc.transient)
            return
        except Exception as exc:  # noqa: BLE001 - an unexpected type is still not fatal
            self._report_error("Speech recognition failed.", str(exc))
            return
        if text:
            self.on_transcript(text)

    def on_transcript(self, text: str, ts: Optional[float] = None) -> None:
        """One completed utterance of speech, already transcribed."""
        text = (text or "").strip()
        if not text:
            return
        now = self.clock()
        wall = time.time() if ts is None else ts
        # Transcript text never reaches an ordinary logger (F3).
        transcript_log("heard: %r", text)
        if self.buffer is not None:
            self.buffer.add(wall, text)
        self.bus.publish(events.UtteranceHeard(text=text, ts=wall))

        if self.push_to_talk:
            # Held button: everything said is the question, no trigger needed.
            self._ptt_parts.append(text)
            return

        if self.state in (State.THINKING, State.SPEAKING):
            self._maybe_barge_in(text)
            return

        found = self.trigger.parse(text) if self.trigger is not None else None
        if self.state is State.IDLE:
            if found is not None:
                self._begin_command(found, now)
        elif self.state is State.AWAITING_COMMAND:
            if found is not None:
                log.info("re-triggered; resetting command")
                self._begin_command(found, now)
            else:
                self.command_parts.append(text)
                self._deadline = now + self._cfg("command_end_silence_ms", 1500) / 1000.0

    def tick(self, now: Optional[float] = None) -> None:
        """Advance deadline logic. Called frequently by the engine."""
        if self.state is not State.AWAITING_COMMAND:
            return
        now = self.clock() if now is None else now
        cap = self._cfg("max_command_wait_ms", 12000) / 1000.0
        cap_hit = self.trigger_ts is not None and (now - self.trigger_ts) >= cap
        deadline_hit = self._deadline is not None and now >= self._deadline
        if cap_hit or deadline_hit:
            self._dispatch("cap" if cap_hit else "silence")

    # -- typed input (chat window, D10) -----------------------------------
    def ask_text(self, text: str, speak: Optional[bool] = None) -> None:
        """A question typed into the chat window, or push-to-talk speech.

        Bypasses the wake word entirely — you are already talking to it.
        """
        text = (text or "").strip()
        if not text:
            return
        if self.responder.busy or self.state in (State.THINKING, State.SPEAKING):
            self.cancel(reason="superseded")
        self.command_parts = [text]
        self._match = None
        self.trigger_ts = self.clock()
        if speak is None:
            speak = bool(self._cfg("speak_typed_answers", False))
        self._dispatch("typed", speak=speak, source="typed")

    def begin_push_to_talk(self) -> None:
        """The push-to-talk button went down (D10)."""
        if self.responder.busy or self.state in (State.THINKING, State.SPEAKING):
            self.cancel(reason="superseded")
        self._ptt_parts = []
        self.push_to_talk = True
        self._set_state(State.AWAITING_COMMAND)

    def end_push_to_talk(self) -> None:
        """The button came up: dispatch whatever was said while it was held."""
        if not self.push_to_talk:
            return
        self.push_to_talk = False
        text = " ".join(part for part in self._ptt_parts if part).strip()
        self._ptt_parts = []
        if not text:
            self.reset()
            return
        self.command_parts = [text]
        self._match = None
        self.trigger_ts = self.clock()
        self._dispatch("push-to-talk", source="push_to_talk")

    # -- barge-in ---------------------------------------------------------
    def on_spotter_trigger(self, confidence: float = 1.0) -> None:
        """The acoustic spotter heard the trigger word (§5.3).

        In IDLE this only produces the instant chime — the transcript path
        remains the authority on grammar and scope. While speaking it is a
        barge-in, unless we are the ones saying the word (self-echo, layer 2).
        """
        self.bus.publish(events.TriggerSpotted(source="spotter",
                                               confidence=confidence))
        if self.state is not State.SPEAKING:
            return
        if self._own_voice_contains_trigger():
            log.debug("ignoring spotter hit: we are saying the trigger word")
            return
        self.interrupt(source="spotter")

    def _update_spotter_sensitivity(self, state: State) -> None:
        """Layers 3 and 4 of the barge-in stack (§5.4.2).

        While we are speaking, our own voice comes back attenuated and
        room-coloured, so the spotter's bar goes up — unless output is going to
        headphones, in which case there is no acoustic path at all and normal
        sensitivity is right.
        """
        spotter = self.spotter
        set_speaking = getattr(spotter, "set_speaking", None)
        if not callable(set_speaking):
            return
        speaking = state is State.SPEAKING
        if speaking:
            try:
                from bgassist.audio.capture import output_is_builtin_speaker

                # None (unknown) is treated as "assume there is a path".
                if output_is_builtin_speaker() is False:
                    speaking = False
            except Exception:  # noqa: BLE001 - no audio stack here
                pass
        try:
            set_speaking(speaking)
        except Exception:  # noqa: BLE001 - the spotter is optional polish
            log.debug("could not update the spotter sensitivity", exc_info=True)

    def _own_voice_contains_trigger(self) -> bool:
        chunk = getattr(self.responder, "current_chunk", "") or ""
        if not chunk or self.trigger is None:
            return False
        return self.trigger.find(chunk) is not None

    def _maybe_barge_in(self, text: str) -> None:
        """Transcript-based barge-in: ~1 s later than the spotter, still correct."""
        if not self._cfg("barge_in", True):
            return
        if self.trigger is None or self.trigger.parse(text) is None:
            return
        if self._own_voice_contains_trigger():
            return
        self.interrupt(source="transcript", text=text)

    def interrupt(self, source: str = "user", text: str = "") -> None:
        """Cut the answer off now and treat it as a conversational turn (D12a)."""
        if self.state not in (State.THINKING, State.SPEAKING):
            return
        log.info("interrupted by %s", source)
        self.bus.publish(events.TriggerSpotted(source=source))
        self.responder.cancel()
        recovered = self._recover_leading_words()
        follow_up = text or recovered
        if follow_up:
            # The interruption carries its own question: run it as a new turn.
            self.on_transcript(follow_up)

    def _recover_leading_words(self) -> str:
        """Retro-transcribe the ring buffer and subtract our own playback."""
        if self.retro_transcribe is None:
            return ""
        try:
            heard = self.retro_transcribe(5.0) or ""
        except Exception:  # noqa: BLE001 - a failed recovery must not break the cut
            log.exception("retro-transcription failed")
            return ""
        spoken = getattr(self.responder, "current_chunk", "") or ""
        last = getattr(self.responder, "last_result", None)
        if last is not None and last.text:
            spoken = f"{last.text[:last.spoken_upto]} {spoken}"
        return subtract_playback(heard, spoken)

    # -- control ----------------------------------------------------------
    def cancel(self, reason: str = "stopped") -> None:
        """Stop button / Esc / a new question arriving."""
        if self.state in (State.THINKING, State.SPEAKING):
            self.responder.cancel()
        self.reset()

    def reset(self) -> None:
        self.command_parts = []
        self.trigger_ts = None
        self._deadline = None
        self._match = None
        self._set_state(State.IDLE)

    # -- internals --------------------------------------------------------
    def _begin_command(self, found: TriggerMatch, now: float) -> None:
        log.info("trigger %r detected (%s)", found.trigger, found.position.value)
        self.bus.publish(events.TriggerSpotted(source="transcript"))
        self._match = found
        self.command_parts = [found.command] if found.command else []
        self.trigger_ts = now
        self._deadline = now + self._cfg("command_end_silence_ms", 1500) / 1000.0
        self._set_state(State.AWAITING_COMMAND)
        if found.dispatch_now:
            # Trailing trigger: they have finished asking (§5.1). No wait.
            self._dispatch("trailing")

    def _dispatch(self, reason: str, speak: bool = True,
                  source: str = "voice") -> None:
        query = " ".join(part for part in self.command_parts if part).strip()
        context = ""
        if self.buffer is not None:
            context = self.buffer.recent_text(seconds=self._cfg("context_seconds", 120.0))
        marked = self._match.marked() if self._match is not None else ""
        if marked and len(self.command_parts) > 1:
            marked = " ".join([marked, *self.command_parts[1:]])

        log.info("dispatching to the model (reason=%s, %d context line(s))",
                 reason, len(context.splitlines()) if context else 0)
        self._set_state(State.THINKING)
        self._spoke_anything = False

        history: List[dict] = []
        self._conversation_id = None
        self._user_message_id = None
        if self.conversations is not None:
            self._conversation_id = self.conversations.current_conversation()
            history = self.conversations.history(self._conversation_id)
            stored = self.conversations.add_message(
                self._conversation_id, "user", query or marked or "(name only)",
                context=context, source=source)
            self._user_message_id = stored.id

        self.bus.publish(events.Dispatching(
            query=query, conversation_id=self._conversation_id,
            position=self._match.position.value if self._match else reason))

        self.responder.submit(AnswerRequest(
            query=query, context=context, marked_utterance=marked,
            history=history, conversation_id=self._conversation_id, speak=speak))

    # -- responder callbacks ----------------------------------------------
    def _on_speaking(self, first_chunk: str) -> None:
        self._spoke_anything = True
        self._set_state(State.SPEAKING)

    def _on_token(self, token: str) -> None:
        self.bus.publish(events.TokenStreamed(text=token))

    def _on_answer_done(self, result: AnswerResult) -> None:
        message_id = None
        if self.conversations is not None and self._conversation_id is not None:
            if result.interrupted and not result.spoke_anything:
                # Cut off before a word was said: there is no assistant turn,
                # and the question the model never answered must not be
                # replayed as though it were part of the dialogue (§5.4.4).
                if self._user_message_id is not None:
                    self.conversations.update_message(self._user_message_id,
                                                      superseded=True)
            elif result.text.strip():
                stored = self.conversations.add_message(
                    self._conversation_id, "assistant", result.text,
                    spoken_upto=result.spoken_upto, interrupted=result.interrupted)
                message_id = stored.id
                self._maybe_title(self._conversation_id)

        if result.error:
            self.last_error = result.error
            self.bus.publish(events.ErrorOccurred(
                message="The language model could not be reached.",
                detail=result.error))

        self.bus.publish(events.AnswerFinished(
            text=result.text, spoken_upto=result.spoken_upto,
            interrupted=result.interrupted,
            conversation_id=self._conversation_id, message_id=message_id))
        self.bus.publish(events.ConversationsChanged(
            conversation_id=self._conversation_id))

        if self.state in (State.THINKING, State.SPEAKING):
            self.reset()

    def _maybe_title(self, conversation_id: int) -> None:
        """Auto-title a conversation after its first exchange (D15)."""
        if not self._cfg("auto_title", True) or self.conversations is None:
            return
        conversation = self.conversations.get_conversation(conversation_id)
        if conversation is None or conversation.title:
            return
        messages = self.conversations.messages(conversation_id)
        if len(messages) < 2:
            return
        try:
            from bgassist.llm.invoke import ask_once
            from bgassist.llm.prompts import TITLE_PROMPT

            exchange = "\n".join(f"{m.role}: {m.spoken_text}" for m in messages[:4])
            title = ask_once(self.llm, exchange, TITLE_PROMPT)
        except Exception as exc:  # noqa: BLE001 - titling is a nicety
            log.debug("auto-title failed: %s", exc)
            return
        title = " ".join((title or "").strip().strip('"').split())[:60]
        if title:
            self.conversations.set_title(conversation_id, title)
            self.bus.publish(events.ConversationsChanged(
                conversation_id=conversation_id, reason="titled"))

    def _report_error(self, message: str, detail: str = "", fatal: bool = False) -> None:
        self.last_error = message
        log.error("%s (%s)", message, detail)
        self.bus.publish(events.ErrorOccurred(message=message, detail=detail,
                                              fatal=fatal))
