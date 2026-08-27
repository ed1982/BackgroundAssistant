"""The speech-to-text interface and its typed failures (fixes F8).

The old code wrapped transcription in a bare ``except Exception`` and dropped
the utterance, so a missing model, a corrupt download and a bad frame all
looked identical: silence. These types let the orchestrator tell a transient
problem from one the user has to act on, and surface the latter.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class TranscriberError(RuntimeError):
    """Base class for every speech-to-text failure."""

    #: True when retrying the next utterance might work.
    transient = True
    #: Short sentence shown in the tray / chat window.
    user_message = "Speech recognition failed."


class ModelUnavailable(TranscriberError):
    """The model is missing, could not be downloaded, or failed to load."""

    transient = False
    user_message = ("The speech model could not be loaded. Open Preferences "
                    "→ Listening to download it again.")


class TranscriptionFailed(TranscriberError):
    """The model loaded but this particular utterance could not be decoded."""

    transient = True
    user_message = "That utterance could not be transcribed."


class AudioFormatError(TranscriberError):
    """The audio handed to the transcriber was not 16 kHz mono int16 PCM."""

    transient = True
    user_message = "The captured audio was in an unexpected format."


@runtime_checkable
class Transcriber(Protocol):
    """Anything that turns 16 kHz mono int16 PCM into text."""

    name: str

    def transcribe(self, audio: bytes) -> str:
        """Return the transcript of *audio* (may be empty).

        Raises a :class:`TranscriberError` subclass on failure — never a bare
        ``Exception``.
        """
        ...
