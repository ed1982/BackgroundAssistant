"""Rolling buffer of recent transcript lines used as LLM context."""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, List, Optional, Tuple


class TranscriptBuffer:
    """Keeps the last *max_seconds* of transcript (and at most *max_chars*).

    Single-writer by design: only the pipeline worker thread mutates it, so
    no locking is required. Timestamps are wall-clock seconds (time.time()).
    """

    def __init__(self, max_seconds: float = 120.0, max_chars: int = 4000):
        self.max_seconds = float(max_seconds)
        self.max_chars = int(max_chars)
        self._items: Deque[Tuple[float, str]] = deque()

    def add(self, ts: float, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._items.append((float(ts), text))
        self._prune()

    def _prune(self) -> None:
        if not self._items:
            return
        cutoff = self._items[-1][0] - self.max_seconds
        while self._items and self._items[0][0] < cutoff:
            self._items.popleft()
        total = sum(len(text) for _, text in self._items)
        while self._items and total > self.max_chars:
            _, old = self._items.popleft()
            total -= len(old)

    def recent_text(self, seconds: Optional[float] = None) -> str:
        """Render the window as timestamped lines, oldest first."""
        if not self._items:
            return ""
        window = self.max_seconds if seconds is None else float(seconds)
        cutoff = self._items[-1][0] - window
        lines: List[str] = []
        for ts, text in self._items:
            if ts < cutoff:
                continue
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            lines.append(f"{stamp}  {text}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
