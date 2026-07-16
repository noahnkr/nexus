"""Plain-text / Markdown parser. UTF-8 decode; raw Markdown embeds fine."""
from __future__ import annotations


def parse(data: bytes):
    from . import ParsedDocument

    text = data.decode("utf-8", errors="replace").strip()
    return ParsedDocument(text=text, metadata={})
