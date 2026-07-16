"""HTML parser (BeautifulSoup). Drops script/style, returns visible text."""
from __future__ import annotations

from bs4 import BeautifulSoup


def parse(data: bytes):
    from . import ParsedDocument

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    text = soup.get_text(separator="\n")
    # Collapse runs of blank lines that get_text tends to produce.
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln).strip()
    meta = {"title": title} if title else {}
    return ParsedDocument(text=text, metadata=meta)
