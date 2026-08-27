"""Subtracting our own voice from what the microphone heard (§5.4.1).

When the user interrupts, we re-read the last few seconds of audio from the
ring buffer to recover the words they spoke *before* the trigger. That audio
also contains the tail of our own playback — but we know exactly what we were
saying, so it can be removed rather than injected into the ambient buffer as
though a person in the room had said it.

Word-level rather than character-level because the recogniser will not have
produced our text verbatim: punctuation and casing differ, and a word or two
may be wrong.
"""
from __future__ import annotations

import re
from typing import List

MIN_RUN = 4  # consecutive matching words before we call it our own voice


def _tokens(text: str) -> List[str]:
    return re.findall(r"[0-9a-z']+", (text or "").lower())


def subtract_playback(heard: str, spoken: str, min_run: int = MIN_RUN) -> str:
    """Remove runs of *min_run*+ words from *heard* that we know we said.

    Returns what is left, which is the user's own speech (possibly empty).
    """
    heard_words = re.findall(r"\S+", heard or "")
    if not heard_words or not (spoken or "").strip():
        return (heard or "").strip()

    spoken_tokens = _tokens(spoken)
    spoken_set = {tuple(spoken_tokens[i:i + min_run])
                  for i in range(max(0, len(spoken_tokens) - min_run + 1))}
    if not spoken_set:
        return (heard or "").strip()

    normalised = [(_tokens(w) or [""])[0] for w in heard_words]
    drop = [False] * len(heard_words)
    for i in range(len(normalised) - min_run + 1):
        window = tuple(normalised[i:i + min_run])
        if window in spoken_set:
            for j in range(i, i + min_run):
                drop[j] = True
    # Extend each dropped run word by word while the words keep matching.
    kept = [w for w, d in zip(heard_words, drop, strict=False) if not d]
    return re.sub(r"\s+", " ", " ".join(kept)).strip()
