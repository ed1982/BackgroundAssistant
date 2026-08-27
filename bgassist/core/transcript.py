"""The rolling, RAM-only buffer of recent speech (D5).

This is the whole ambient record: the last couple of minutes of what was said
near the machine, held in memory and nowhere else. It is never written to
disk, never logged, and evaporates when the buffer rolls or the app quits.
Only an exchange you actually triggered is persisted, and then only as a
snapshot of the window that was sent (§6.3).

Every line carries a *speaker*, always ``"user"`` today. The field exists so
that adding diarisation later is additive rather than a schema migration
(D17).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional


@dataclass(frozen=True)
class Line:
    ts: float
    text: str
    speaker: str = "user"


class TranscriptBuffer:
    """Keeps the last *max_seconds* of transcript (and at most *max_chars*).

    Single-writer by design: only the transcription thread appends, so no
    locking is required for correctness of the deque itself.
    """

    def __init__(self, max_seconds: float = 120.0, max_chars: int = 4000):
        self.max_seconds = float(max_seconds)
        self.max_chars = int(max_chars)
        self._items: Deque[Line] = deque()

    def add(self, ts: float, text: str, speaker: str = "user") -> None:
        text = (text or "").strip()
        if not text:
            return
        self._items.append(Line(float(ts), text, speaker))
        self._prune()

    def _prune(self) -> None:
        if not self._items:
            return
        cutoff = self._items[-1].ts - self.max_seconds
        while self._items and self._items[0].ts < cutoff:
            self._items.popleft()
        total = sum(len(line.text) for line in self._items)
        while self._items and total > self.max_chars:
            total -= len(self._items.popleft().text)

    def lines(self, seconds: Optional[float] = None) -> List[Line]:
        if not self._items:
            return []
        window = self.max_seconds if seconds is None else float(seconds)
        cutoff = self._items[-1].ts - window
        return [line for line in self._items if line.ts >= cutoff]

    def recent_text(self, seconds: Optional[float] = None) -> str:
        """Render the window as timestamped lines, oldest first."""
        out: List[str] = []
        for line in self.lines(seconds):
            stamp = time.strftime("%H:%M:%S", time.localtime(line.ts))
            out.append(f"[{stamp}]  {line.text}")
        return "\n".join(out)

    def replace_last(self, text: str) -> None:
        """Rewrite the most recent line (used by retro-transcription, §5.4.1)."""
        if not self._items:
            return
        last = self._items[-1]
        self._items[-1] = Line(last.ts, text.strip(), last.speaker)

    def drop_last(self) -> None:
        if self._items:
            self._items.pop()

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
