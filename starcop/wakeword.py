"""Wake-word (trigger word) matching on transcript text.

Matching is done with case-insensitive, word-boundary regexes over the raw
transcript text. Word boundaries mean "computer" does not match
"computers"; known mis-transcriptions can be added as aliases in config.

Trade-off (documented): any utterance that merely *contains* the trigger
word ("the computer is broken") will wake the assistant. Choose a trigger
word you rarely say in ordinary conversation, or add aliases to narrow it.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple


def normalize_text(text: str) -> str:
    """Lowercase and reduce everything that is not a letter/digit to spaces."""
    text = (text or "").lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return text.strip()


class WakeWordMatcher:
    """Matches configured trigger words in transcript text."""

    def __init__(self, trigger_words: List[str]):
        self._triggers = [t.strip() for t in (trigger_words or []) if t and t.strip()]
        self._patterns: List[Tuple[str, "re.Pattern[str]"]] = []
        for trigger in self._triggers:
            pattern = re.compile(rf"\b{re.escape(trigger)}\b", re.IGNORECASE)
            self._patterns.append((trigger, pattern))
        if not self._patterns:
            raise ValueError("at least one non-empty trigger word is required")

    @property
    def triggers(self) -> List[str]:
        return list(self._triggers)

    def match(self, text: str) -> Optional[str]:
        """Return the first trigger word found in *text*, else None."""
        for trigger, pattern in self._patterns:
            if pattern.search(text or ""):
                return trigger
        return None

    def split_command(self, text: str) -> Optional[Tuple[str, str]]:
        """Return (trigger, command).

        *command* is the original text after the first occurrence of the
        trigger word (may be empty). Returns None when no trigger matches.
        """
        for trigger, pattern in self._patterns:
            m = pattern.search(text or "")
            if m:
                command = text[m.end():].strip()
                # Trim leftover punctuation at the edges ("Computer!" -> "").
                command = command.strip(" \t\r\n.,;:!?'\"()[]{}")
                return trigger, command
        return None
