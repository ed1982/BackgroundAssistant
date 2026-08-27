"""The text-to-speech interface.

Two things every engine must do that the old code could not:

- ``stop()`` — kill speech immediately, so an answer can be interrupted (F10,
  D12). Without it, barge-in is impossible and Quit has to wait.
- report progress at sentence granularity, so the truncation record knows how
  much of the answer the user actually heard (§5.4.1).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class TtsError(RuntimeError):
    """Raised when speech could not be produced."""


@runtime_checkable
class TtsEngine(Protocol):
    name: str

    def speak(self, text: str) -> None:
        """Speak *text*, blocking until it has been said or stopped."""
        ...

    def stop(self) -> None:
        """Stop any speech in progress immediately."""
        ...
