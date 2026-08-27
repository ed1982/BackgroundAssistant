"""Star Trek Computer — a background wake-word voice assistant.

Continuously transcribes speech locally, listens for a configurable trigger
word (default: "computer"), waits for the user to finish speaking, asks an
LLM (local or cloud) about the recent conversation plus the command, and
speaks the answer back.
"""

__version__ = "0.1.0"
