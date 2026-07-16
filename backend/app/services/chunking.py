"""Chunking: a pure function, no tokenizer dependency.

Targets ~1500 characters per chunk with 200 characters of overlap. Chunk
boundaries prefer, in order: a paragraph break, a sentence end, then any
whitespace, falling back to a hard cut only for unbroken runs (so an oversized
paragraph is split rather than emitted whole). Each chunk carries its absolute
[char_start, char_end) offsets into the source text.
"""
from __future__ import annotations

from dataclasses import dataclass

TARGET = 1500
OVERLAP = 200
# How far back from a proposed cut we will look for a nicer boundary.
_LOOKBACK = 300


@dataclass
class Chunk:
    index: int
    text: str
    char_start: int
    char_end: int

    @property
    def metadata(self) -> dict:
        return {"char_start": self.char_start, "char_end": self.char_end}


def _find_break(text: str, start: int, end: int) -> int:
    """Move `end` back to the nicest boundary within the lookback window.
    Returns an index in (start, end]. Falls back to `end` (hard cut)."""
    floor = max(start + 1, end - _LOOKBACK)
    window = text[floor:end]

    # Prefer a paragraph break, then sentence end, then any whitespace.
    for marker in ("\n\n", ". ", ".\n", "! ", "? ", "\n"):
        pos = window.rfind(marker)
        if pos != -1:
            return floor + pos + len(marker)

    ws = max(window.rfind(" "), window.rfind("\t"))
    if ws != -1:
        return floor + ws + 1

    return end


def chunk_text(text: str, *, target: int = TARGET, overlap: int = OVERLAP) -> list[Chunk]:
    if not text or not text.strip():
        return []

    n = len(text)
    spans: list[tuple[int, int]] = []
    start = 0

    while start < n:
        # Skip leading whitespace so chunks don't begin with blank runs.
        while start < n and text[start].isspace():
            start += 1
        if start >= n:
            break

        end = min(start + target, n)
        if end < n:
            end = _find_break(text, start, end)

        # Trim trailing whitespace off the recorded span.
        real_end = end
        while real_end > start and text[real_end - 1].isspace():
            real_end -= 1
        if real_end > start:
            spans.append((start, real_end))

        if end >= n:
            break
        start = max(start + 1, end - overlap)

    return [
        Chunk(index=i, text=text[s:e], char_start=s, char_end=e)
        for i, (s, e) in enumerate(spans)
    ]
