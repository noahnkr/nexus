"""Chunking tests. Pure functions, no env needed."""
from app.services.chunking import chunk_text


def test_empty_input_returns_empty():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \t ") == []


def test_short_text_single_chunk():
    chunks = chunk_text("A short document.")
    assert len(chunks) == 1
    assert chunks[0].text == "A short document."
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len("A short document.")


def test_indices_are_sequential_and_stable():
    text = "\n\n".join(f"Paragraph number {i}. " + ("word " * 50) for i in range(20))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # char_start strictly increases (stable, forward-only ordering).
    starts = [c.char_start for c in chunks]
    assert starts == sorted(starts)


def test_overlap_present_between_consecutive_chunks():
    text = "word " * 2000  # ~10k chars, no paragraph breaks
    chunks = chunk_text(text)
    assert len(chunks) > 1
    # Each chunk after the first should start before the previous one ended.
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur.char_start < prev.char_end, "expected overlapping spans"


def test_oversized_paragraph_is_hard_split():
    # A single 8000-char paragraph with no sentence/paragraph breaks.
    text = "x" * 8000
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c.text) <= 1500 for c in chunks)


def test_offsets_map_back_to_source():
    text = "\n\n".join(f"Section {i} " + ("alpha " * 40) for i in range(30))
    for c in chunk_text(text):
        assert text[c.char_start : c.char_end] == c.text
