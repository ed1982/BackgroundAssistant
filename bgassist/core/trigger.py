"""Position-aware trigger grammar (replaces wakeword.py; fixes F15, F16).

Two decisions live here, and keeping them apart is what makes the feature work
(§5.1):

**When to dispatch** is mechanical, instant, and never involves the model.
It depends on *where* the trigger word fell in the sentence:

- ``TRAILING`` — "what is the answer, Computer?" The question has already been
  asked, so there is nothing to wait for: dispatch immediately. This is the
  most natural phrasing and the one the old ``split_command()`` broke on.
- ``LEADING`` / ``MEDIAL`` — wait for the command-end silence, extending on
  further speech, as before.

**What was actually asked** is not decided here at all. Rather than slicing the
string, we hand the model the transcript with the trigger position marked and
let it work out the intent (D8) — see :mod:`bgassist.llm.prompts`.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

#: The marker the model sees where the user said the assistant's name (D8).
TRIGGER_MARKER = "«ASSISTANT-NAME-SPOKEN»"

#: Words that do not count as "something was asked" on either side.
FILLER_WORDS = frozenset({
    "um", "uh", "erm", "er", "ah", "oh", "hmm", "mm", "please", "thanks",
    "thank", "you", "ok", "okay", "right", "now", "then", "hey", "hi", "hello",
    "so", "well", "just", "actually",
})
# Deliberately not filler: "yes" and "no" carry meaning ("the computer says
# no" must stay quiet, and one-word answers matter in a conversation).

_CLAUSE_PUNCTUATION = ",.;:!?—-–"


class TriggerPosition(enum.Enum):
    LEADING = "leading"    # trigger in the first few words
    MEDIAL = "medial"      # meaningful words on both sides
    TRAILING = "trailing"  # nothing meaningful after the trigger


class Sensitivity(enum.Enum):
    """How readily a trigger in the middle of a sentence counts as address.

    ``relaxed`` wakes on any occurrence (the old behaviour, F15).
    ``balanced`` (default) requires a medial trigger to sit at a clause
    boundary, so "I told him my computer is broken" is ignored while
    "so, computer, what time is it" is not.
    ``strict`` ignores medial triggers altogether.
    """

    RELAXED = "relaxed"
    BALANCED = "balanced"
    STRICT = "strict"

    @classmethod
    def parse(cls, value) -> "Sensitivity":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            return cls.BALANCED


def normalize_text(text: str) -> str:
    """Lowercase and reduce everything that is not a letter/digit to spaces."""
    text = (text or "").lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return text.strip()


def _words(text: str) -> List[str]:
    return [w for w in normalize_text(text).split() if w]


def _meaningful(words: List[str]) -> List[str]:
    return [w for w in words if w not in FILLER_WORDS]


@dataclass(frozen=True)
class TriggerMatch:
    """Everything the orchestrator needs to decide what to do."""

    trigger: str
    position: TriggerPosition
    text: str
    start: int
    end: int
    before: str
    after: str

    @property
    def dispatch_now(self) -> bool:
        """True when the user has finished asking and we should not wait."""
        return self.position is TriggerPosition.TRAILING

    @property
    def command(self) -> str:
        """A best-effort plain-text command, for logs and the chat window.

        The model is given the marked transcript instead (D8); this exists so
        a human reading the UI sees something sensible.
        """
        after = self.after.strip(" \t\r\n.,;:!?'\"()[]{}")
        if _meaningful(_words(after)):
            return after
        before = self.before.strip(" \t\r\n.,;:!?'\"()[]{}")
        return before

    def marked(self) -> str:
        """The utterance with the trigger replaced by :data:`TRIGGER_MARKER`."""
        return (self.text[:self.start] + TRIGGER_MARKER + self.text[self.end:]).strip()


class TriggerParser:
    """Matches configured trigger words and classifies where they fell."""

    def __init__(self, trigger_words, sensitivity=Sensitivity.BALANCED,
                 max_leading_filler: int = 3):
        self._triggers = [t.strip() for t in (trigger_words or []) if t and t.strip()]
        if not self._triggers:
            raise ValueError("at least one non-empty trigger word is required")
        self._patterns: List[Tuple[str, "re.Pattern[str]"]] = [
            (t, re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE)) for t in self._triggers
        ]
        self.sensitivity = Sensitivity.parse(sensitivity)
        self.max_leading_filler = int(max_leading_filler)

    # -- introspection ---------------------------------------------------
    @property
    def triggers(self) -> List[str]:
        return list(self._triggers)

    @property
    def is_easter_egg(self) -> bool:
        """🖖 — shown beside the trigger word when it is exactly "computer" (D2)."""
        return any(t.lower() == "computer" for t in self._triggers)

    # -- matching --------------------------------------------------------
    def find(self, text: str) -> Optional[TriggerMatch]:
        """Return the classified match, or None when the trigger is absent."""
        text = text or ""
        for trigger, pattern in self._patterns:
            match = pattern.search(text)
            if match is None:
                continue
            before = text[:match.start()]
            after = text[match.end():]
            position = self._classify(before, after)
            return TriggerMatch(
                trigger=trigger, position=position, text=text,
                start=match.start(), end=match.end(), before=before, after=after)
        return None

    def match(self, text: str) -> Optional[str]:
        """Backwards-compatible: the trigger word found, or None."""
        found = self.find(text)
        return found.trigger if found else None

    def accepts(self, found: TriggerMatch) -> bool:
        """Apply the sensitivity policy to a match (F15)."""
        if found.position is not TriggerPosition.MEDIAL:
            return True
        if self.sensitivity is Sensitivity.RELAXED:
            return True
        if self.sensitivity is Sensitivity.STRICT:
            return False
        return self._at_clause_boundary(found)

    def parse(self, text: str) -> Optional[TriggerMatch]:
        """find() + accepts(): the one call the orchestrator makes."""
        found = self.find(text)
        if found is None or not self.accepts(found):
            return None
        return found

    # -- internals -------------------------------------------------------
    def _classify(self, before: str, after: str) -> TriggerPosition:
        raw_before = _words(before)
        before_words = _meaningful(raw_before)
        after_words = _meaningful(_words(after))

        # "Nothing meaningful after" = only punctuation, filler, or a single
        # word. A bare "Computer" with nothing either side is a call for
        # attention rather than a question, so it waits like a LEADING trigger.
        if len(after_words) < 2 and before_words:
            return TriggerPosition.TRAILING

        # LEADING means *addressed*: nothing before the trigger but filler.
        # This is the distinction that keeps "my computer is broken" out —
        # it puts a real word in front of the trigger, so it is MEDIAL and
        # subject to the sensitivity policy (F15). "hey computer" and
        # "so, computer" are still leading, because "hey" and "so" carry no
        # content of their own.
        if not before_words and len(raw_before) <= self.max_leading_filler:
            return TriggerPosition.LEADING
        return TriggerPosition.MEDIAL

    @staticmethod
    def _at_clause_boundary(found: TriggerMatch) -> bool:
        before = found.before.rstrip()
        after = found.after.lstrip()
        if before and before[-1] in _CLAUSE_PUNCTUATION:
            return True
        raw_after = found.after
        stripped = raw_after.lstrip(" \t")
        if stripped and stripped[0] in _CLAUSE_PUNCTUATION:
            return True
        if not before or not after:
            return True
        return False
