"""PDF parser (pypdf). Extracts text page by page; pages joined with blank lines."""
from __future__ import annotations

import io

from pypdf import PdfReader

# Local import guard would create a cycle; ParsedDocument is imported lazily.


def parse(data: bytes):
    from . import ParsedDocument

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    text = "\n\n".join(pages).strip()
    return ParsedDocument(text=text, metadata={"page_count": len(reader.pages)})
