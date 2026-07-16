"""Swappable document-parsing layer.

Each format has a parser exposing `parse(data: bytes) -> ParsedDocument`. A registry
maps file extension -> parser. `parse_document(filename, data)` dispatches on the
extension. Module 10 swaps registry entries for Docling without touching callers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import docx as _docx
from . import html as _html
from . import pdf as _pdf
from . import text as _text


@dataclass
class ParsedDocument:
    text: str
    metadata: dict = field(default_factory=dict)


class DocumentParser(Protocol):
    def __call__(self, data: bytes) -> ParsedDocument: ...


# Extension -> parser. Keys are lowercase, without the leading dot.
_REGISTRY: dict[str, Callable[[bytes], ParsedDocument]] = {
    "pdf": _pdf.parse,
    "docx": _docx.parse,
    "html": _html.parse,
    "htm": _html.parse,
    "md": _text.parse,
    "markdown": _text.parse,
    "txt": _text.parse,
}

SUPPORTED_EXTENSIONS = frozenset(_REGISTRY)


class UnsupportedFormatError(ValueError):
    pass


def _extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lstrip(".").lower()


def parse_document(filename: str, data: bytes) -> ParsedDocument:
    ext = _extension(filename)
    parser = _REGISTRY.get(ext)
    if parser is None:
        raise UnsupportedFormatError(
            f"Unsupported file type '.{ext}'. Supported: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    parsed = parser(data)
    parsed.metadata.setdefault("filename", filename)
    parsed.metadata.setdefault("extension", ext)
    return parsed
