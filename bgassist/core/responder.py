"""Streaming answer + sentence-by-sentence speech, cancellable (§5.2, §5.5).

This is where three of the plan's ideas meet:

- **Speak the first sentence while the rest is still generating**, so
  time-to-first-audio is under a second instead of the whole response.
- **Cancel cleanly** — a new trigger, the Stop button or Esc closes the HTTP
  response and kills speech mid-word (F10).
- **Record what was actually heard.** ``spoken_upto`` counts only sentences
  that finished speaking, which is exactly the prefix the user experienced and
  therefore exactly what the model is replayed later (D12a).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from bgassist.llm.base import LLMCancelled, LLMError
from bgassist.llm.invoke import stream_response
from bgassist.tts.chunker import sentence_chunks

log = logging.getLogger("bgassist.core.responder")

#: Spoken when the provider fails. The detail (401, unreachable, …) goes to
#: the tray and the chat window, where it can actually be acted on.
ERROR_SPEECH = "I could not reach the language model. Check Preferences."
EMPTY_SPEECH = "I have nothing to report."


@dataclass
class AnswerRequest:
    query: str = ""
    context: str = ""
    marked_utterance: str = ""
    history: List[Dict[str, str]] = field(default_factory=list)
    conversation_id: Optional[int] = None
    speak: bool = True


@dataclass
class AnswerResult:
    text: str = ""
    spoken_upto: int = 0
    interrupted: bool = False
    error: str = ""
    spoke_anything: bool = False


class SpeechResponder:
    """Runs one answer at a time; a second submit cancels the first."""

    def __init__(self, llm, tts, on_speaking: Optional[Callable[[str], None]] = None,
                 on_token: Optional[Callable[[str], None]] = None,
                 on_done: Optional[Callable[[AnswerResult], None]] = None,
                 threaded: bool = False):
        self.llm = llm
        self.tts = tts
        self.on_speaking = on_speaking
        self.on_token = on_token
        self.on_done = on_done
        self.threaded = threaded
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._busy = threading.Event()
        self._finished = threading.Event()
        self._finished.set()
        self.last_result: Optional[AnswerResult] = None
        #: What is being spoken right now, for self-echo suppression (§5.4.2).
        self.current_chunk = ""

    # -- lifecycle -------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def submit(self, request: AnswerRequest) -> Optional[AnswerResult]:
        """Answer *request*. Blocks unless the responder is threaded."""
        if self.busy:
            self.cancel()
        self._cancel = threading.Event()
        self._busy.set()
        self._finished.clear()
        if not self.threaded:
            return self._run(request)
        self._thread = threading.Thread(target=self._run, args=(request,),
                                        name="bgassist-responder", daemon=True)
        self._thread.start()
        return None

    def cancel(self, timeout: float = 3.0) -> None:
        """Stop the answer now: close the stream, kill speech."""
        self._cancel.set()
        stop = getattr(self.tts, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001 - best effort
                log.exception("tts.stop() failed")
        thread = self._thread
        if thread is not None and thread.is_alive() and \
                threading.current_thread() is not thread:
            thread.join(timeout)

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._finished.wait(timeout)

    # -- the work --------------------------------------------------------
    def _run(self, request: AnswerRequest) -> AnswerResult:
        result = AnswerResult()
        spoken_chars = 0
        produced: List[str] = []
        announced = False
        try:
            tokens = self._tokens(request, produced)
            for chunk in sentence_chunks(tokens):
                if self._cancel.is_set():
                    result.interrupted = True
                    break
                if not announced:
                    announced = True
                    self._announce(chunk)
                self.current_chunk = chunk
                if request.speak:
                    spoke = self._speak(chunk)
                else:
                    spoke = True
                self.current_chunk = ""
                if self._cancel.is_set():
                    # The chunk was cut off part-way: at sentence granularity
                    # it does not count as heard (§5.4.1).
                    result.interrupted = True
                    break
                if spoke:
                    spoken_chars += len(chunk)
                    result.spoke_anything = True
        except LLMCancelled:
            result.interrupted = True
        except LLMError as exc:
            result.error = str(exc)
            log.error("LLM failed: %s", exc)
        except Exception as exc:  # noqa: BLE001 - nothing may kill the worker
            result.error = str(exc)
            log.exception("answering failed")

        result.text = "".join(produced)
        result.spoken_upto = min(spoken_chars, len(result.text))

        if result.error and not result.spoke_anything and not result.interrupted:
            if request.speak:
                self._announce(ERROR_SPEECH)
                self._speak(ERROR_SPEECH)
        elif (not result.text.strip() and not result.interrupted
                and not result.error):
            if request.speak:
                self._announce(EMPTY_SPEECH)
                self._speak(EMPTY_SPEECH)
            result.text = EMPTY_SPEECH
            result.spoken_upto = len(EMPTY_SPEECH)

        self.last_result = result
        self._busy.clear()
        if self.on_done is not None:
            try:
                self.on_done(result)
            except Exception:  # noqa: BLE001
                log.exception("on_done callback failed")
        # Signalled last, so that waiting for the responder means the whole
        # turn is finished — including the truncation record being written.
        # Setting it first let a late callback land after the caller had moved
        # on, which is the kind of race that only shows up under load.
        self._finished.set()
        return result

    def _tokens(self, request: AnswerRequest, sink: List[str]):
        """Wrap the token stream so we keep every token we were given."""
        for token in stream_response(self.llm, request.context, request.query,
                                     history=request.history,
                                     cancel=self._cancel,
                                     marked_utterance=request.marked_utterance):
            sink.append(token)
            if self.on_token is not None:
                try:
                    self.on_token(token)
                except Exception:  # noqa: BLE001
                    log.exception("on_token callback failed")
            yield token

    def _announce(self, first_chunk: str) -> None:
        if self.on_speaking is not None:
            try:
                self.on_speaking(first_chunk)
            except Exception:  # noqa: BLE001
                log.exception("on_speaking callback failed")

    def _speak(self, chunk: str) -> bool:
        try:
            self.tts.speak(chunk)
            return True
        except Exception as exc:  # noqa: BLE001 - TTS must not kill listening
            log.error("TTS failed: %s", exc)
            return False
