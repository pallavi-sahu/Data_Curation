"""Split raw text into paragraph units (no minimum length at scrape time)."""

from __future__ import annotations

import re

# Common page-number patterns (metadata, not article body).
_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:page\s+)?\d{1,4}\s*(?:of\s+\d{1,4})?\s*$",
    re.IGNORECASE,
)
_ROMAN_PAGE_RE = re.compile(r"^\s*[ivxlcdm]+\s*$", re.IGNORECASE)

# Sentence boundary for splitting oversized web blocks (Latin + Indic danda).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।\u0964\u0965])\s+")

# Blocks larger than this are re-split further (common with web extractors).
_OVERSIZED_BLOCK_CHARS = 1_500


def segment_paragraphs(raw_text: str) -> list[str]:
    """
    Split extracted text into paragraph-sized units.

    All non-junk fragments are kept regardless of length. Minimum word count
    is applied later when packing JSONL output rows (see ``pack_records``).

    Split order:
    1. Blank lines (``\\n\\n``)
    2. Single newlines for oversized blocks
    3. Sentence boundaries for blocks still too large
    """
    if not raw_text or not raw_text.strip():
        return []

    blocks = _initial_blocks(raw_text)
    paragraphs: list[str] = []

    for block in blocks:
        for piece in _split_oversized_block(block):
            cleaned = _normalize_block(piece)
            if not cleaned:
                continue
            if _is_junk_fragment(cleaned):
                continue
            paragraphs.append(cleaned)

    return paragraphs


def _initial_blocks(raw_text: str) -> list[str]:
    return [b for b in re.split(r"\n\s*\n+", raw_text) if b.strip()]


def _split_oversized_block(block: str) -> list[str]:
    """Break large web/PDF blocks into natural paragraph-sized pieces."""
    if len(block) <= _OVERSIZED_BLOCK_CHARS and "\n" not in block:
        return [block]

    pieces: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) > _OVERSIZED_BLOCK_CHARS:
            pieces.extend(s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip())
        else:
            pieces.append(line)

    return pieces or [block]


def _normalize_block(text: str) -> str:
    """Collapse internal whitespace and strip ends."""
    return re.sub(r"\s+", " ", text).strip()


def _is_junk_fragment(text: str) -> bool:
    """Drop bare page numbers and non-text noise only."""
    if _PAGE_NUMBER_RE.match(text):
        return True
    if _ROMAN_PAGE_RE.match(text):
        return True
    alpha = sum(1 for c in text if c.isalpha())
    if alpha < len(text) * 0.3:
        return True
    return False
