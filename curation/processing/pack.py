"""Merge language-filtered fragments into min-word JSONL output records."""

from __future__ import annotations

from typing import Any


def _word_count(text: str) -> int:
    return len(text.split())


def pack_records(
    records: list[dict[str, Any]],
    *,
    min_words: int,
) -> list[dict[str, Any]]:
    """
    Combine consecutive fragments from the **same** source into output chunks.

    When ``min_words`` > 0, fragments from one page/URL are merged until a row
    reaches at least ``min_words`` words, then that row is written and a new
    buffer starts for the same source. Remaining text under ``min_words`` at
    the end of a source is **discarded** (not written). Fragments from different
    sources are never merged together.
    """
    if min_words <= 0 or not records:
        return records

    packed: list[dict[str, Any]] = []
    buffer_parts: list[str] = []
    buffer_source = ""
    buffer_lang = ""
    buffer_confidences: list[float] = []
    buffer_methods: list[str] = []

    def _clear_buffer() -> None:
        nonlocal buffer_parts, buffer_source, buffer_lang
        nonlocal buffer_confidences, buffer_methods
        buffer_parts = []
        buffer_confidences = []
        buffer_methods = []

    def _emit_buffer_if_ready() -> None:
        if not buffer_parts:
            return
        if _word_count(" ".join(buffer_parts)) < min_words:
            return
        packed.append(
            _make_row(
                buffer_source,
                buffer_lang,
                buffer_parts,
                buffer_confidences,
                buffer_methods,
            )
        )
        _clear_buffer()

    def _finish_source_buffer() -> None:
        """Emit a full row if possible; drop any remainder under ``min_words``."""
        _emit_buffer_if_ready()
        _clear_buffer()

    for record in records:
        text = record["paragraph"].strip()
        if not text:
            continue

        source = record["source"]
        if buffer_parts and source != buffer_source:
            _finish_source_buffer()

        if not buffer_parts:
            buffer_source = source
            buffer_lang = record["language_code"]

        buffer_parts.append(text)
        buffer_confidences.append(float(record["confidence"]))
        buffer_methods.append(str(record.get("detection_method", "")))

        if _word_count(" ".join(buffer_parts)) >= min_words:
            _emit_buffer_if_ready()

    _finish_source_buffer()
    return packed


def _make_row(
    source: str,
    language_code: str,
    parts: list[str],
    confidences: list[float],
    methods: list[str],
) -> dict[str, Any]:
    return {
        "source": source,
        "paragraph": " ".join(parts).strip(),
        "language_code": language_code,
        "confidence": round(min(confidences), 6),
        "detection_method": _combined_method(methods),
    }


def _combined_method(methods: list[str]) -> str:
    unique = sorted({m for m in methods if m})
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return ""
    return "+".join(unique)
