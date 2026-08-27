"""Turn a stream of tokens into speakable sentences (§5.2).

Speaking sentence by sentence as the answer generates is the single biggest
perceived-latency win in the plan: time-to-first-audio drops from *whole
response* (3–8 s) to *first sentence* (well under a second). It is also what
gives the truncation record its granularity — the offset of the last completed
sentence is exactly what the user heard (§5.4.1).
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator, List

#: Sentence-final punctuation followed by whitespace, or the end of the text.
_BOUNDARY = re.compile(r"(?<=[.!?…])[\"')\]]*\s")

#: Abbreviations that end in a full stop but do not end a sentence.
_ABBREVIATIONS = ("mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "e.g.", "i.e.",
                  "etc.", "vs.", "approx.", "no.")

MIN_CHUNK_CHARS = 12


def _ends_with_abbreviation(text: str) -> bool:
    tail = text.rstrip().lower()
    return any(tail.endswith(a) for a in _ABBREVIATIONS)


def split_sentences(text: str) -> List[str]:
    """Split finished text into sentences, keeping their punctuation."""
    out: List[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        piece = text[start:match.end()]
        if _ends_with_abbreviation(piece):
            continue
        if piece.strip():
            out.append(piece)
        start = match.end()
    tail = text[start:]
    if tail.strip():
        out.append(tail)
    return out


def sentence_chunks(tokens: Iterable[str],
                    min_chars: int = MIN_CHUNK_CHARS) -> Iterator[str]:
    """Yield speakable chunks as soon as each sentence completes.

    Chunks shorter than *min_chars* are held back and merged with the next one,
    so a stray "Yes." does not become its own utterance with a gap after it.
    """
    buffer = ""
    for token in tokens:
        if not token:
            continue
        buffer += token
        while True:
            candidates = split_sentences(buffer)
            if len(candidates) < 2:
                break
            complete = candidates[0]
            if len(complete.strip()) < min_chars and len(candidates) == 2:
                break
            buffer = buffer[len(complete):]
            if complete.strip():
                yield complete
    if buffer.strip():
        yield buffer
