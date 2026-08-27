"""The event bus every component talks through.

Threads publish; the Qt main thread (and the tests) subscribe. Publication is
synchronous and each subscriber is isolated: a listener that raises is logged
and the others still run, because a broken UI callback must never take the
microphone down (the failure philosophy the old code got right).

Events carrying speech text exist only in memory. Nothing here is ever logged
by the bus itself — see :mod:`bgassist.logging_setup`.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, DefaultDict, List, Optional, Type

log = logging.getLogger("bgassist.core.events")


# -- event types ---------------------------------------------------------

@dataclass(frozen=True)
class Event:
    """Base class for everything published on the bus."""


@dataclass(frozen=True)
class StateChanged(Event):
    state: str          # State.value
    previous: str = ""


@dataclass(frozen=True)
class TriggerSpotted(Event):
    """The trigger word was heard. Published before transcription finishes.

    *source* is ``"spotter"`` (acoustic, ~100 ms) or ``"transcript"`` (words,
    ~1 s). The instant chime and the tray icon change hang off this, which is
    what fixes the silence of F6.
    """

    source: str = "transcript"
    confidence: float = 1.0


@dataclass(frozen=True)
class UtteranceHeard(Event):
    """One completed utterance was transcribed. In-memory only."""

    text: str
    ts: float
    speaker: str = "user"


@dataclass(frozen=True)
class Dispatching(Event):
    query: str
    conversation_id: Optional[int] = None
    position: str = ""


@dataclass(frozen=True)
class TokenStreamed(Event):
    text: str
    message_id: Optional[int] = None


@dataclass(frozen=True)
class AnswerFinished(Event):
    text: str
    spoken_upto: int
    interrupted: bool = False
    conversation_id: Optional[int] = None
    message_id: Optional[int] = None


@dataclass(frozen=True)
class ErrorOccurred(Event):
    message: str
    detail: str = ""
    fatal: bool = False


@dataclass(frozen=True)
class AudioBacklog(Event):
    dropped_frames: int


@dataclass(frozen=True)
class ConversationsChanged(Event):
    conversation_id: Optional[int] = None
    reason: str = "updated"


@dataclass(frozen=True)
class SettingsChanged(Event):
    keys: List[str] = field(default_factory=list)


# -- the bus -------------------------------------------------------------

Listener = Callable[[Event], None]


class EventBus:
    """A tiny thread-safe publish/subscribe hub.

    Subscribe to a concrete event class, or to :class:`Event` for everything.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._listeners: DefaultDict[Type[Event], List[Listener]] = _defaultdict_list()

    def subscribe(self, event_type: Type[Event], listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners[event_type].append(listener)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._listeners[event_type].remove(listener)
                except ValueError:  # pragma: no cover - already gone
                    pass

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            listeners: List[Listener] = []
            for event_type, registered in self._listeners.items():
                if isinstance(event, event_type):
                    listeners.extend(registered)
        for listener in listeners:
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - one bad listener must not stop the rest
                log.exception("event listener failed for %s", type(event).__name__)

    def clear(self) -> None:
        with self._lock:
            self._listeners.clear()


def _defaultdict_list():
    from collections import defaultdict

    return defaultdict(list)


class RecordingBus(EventBus):
    """An EventBus that also keeps everything it published (tests)."""

    def __init__(self) -> None:
        super().__init__()
        self.events: List[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)
        super().publish(event)

    def of(self, event_type: Type[Event]) -> List[Any]:
        return [e for e in self.events if isinstance(e, event_type)]
