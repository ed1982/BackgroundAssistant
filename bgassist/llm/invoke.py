"""Call whatever kind of backend we were given, streaming when possible.

Backends in this package all stream. Test doubles (and anything a user might
plug in) may only offer a two-argument ``ask``. Rather than guessing with a
``try/except TypeError`` — which would swallow real errors raised *inside* the
call — we inspect the signature once and adapt.
"""
from __future__ import annotations

import inspect
import threading
from typing import Iterator, Optional


def _accepts(func, name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - builtins / C callables
        return False
    parameters = signature.parameters
    if name in parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


def _kwargs(func, *, history=None, cancel=None, marked_utterance: str = "") -> dict:
    out = {}
    if history is not None and _accepts(func, "history"):
        out["history"] = history
    if cancel is not None and _accepts(func, "cancel"):
        out["cancel"] = cancel
    if marked_utterance and _accepts(func, "marked_utterance"):
        out["marked_utterance"] = marked_utterance
    return out


def stream_response(llm, context_text: str, query: str, *,
                    history=None, cancel: Optional[threading.Event] = None,
                    marked_utterance: str = "") -> Iterator[str]:
    """Yield answer chunks from *llm*, streaming if it knows how."""
    streamer = getattr(llm, "stream", None)
    if callable(streamer):
        yield from streamer(context_text, query,
                            **_kwargs(streamer, history=history, cancel=cancel,
                                      marked_utterance=marked_utterance))
        return
    ask = getattr(llm, "ask", None)
    if not callable(ask):
        raise TypeError(f"{llm!r} has neither stream() nor ask()")
    text = ask(context_text, query,
               **_kwargs(ask, history=history, cancel=cancel,
                         marked_utterance=marked_utterance))
    if text:
        yield text


def ask_once(llm, context_text: str, query: str, *, history=None,
             cancel: Optional[threading.Event] = None,
             marked_utterance: str = "") -> str:
    """Collect a whole answer (used for connection tests and auto-titling)."""
    ask = getattr(llm, "ask", None)
    if callable(ask):
        return (ask(context_text, query,
                    **_kwargs(ask, history=history, cancel=cancel,
                              marked_utterance=marked_utterance)) or "").strip()
    return "".join(stream_response(llm, context_text, query, history=history,
                                   cancel=cancel,
                                   marked_utterance=marked_utterance)).strip()
