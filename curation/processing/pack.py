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
    Combine consecutive records from the same source into output chunks.

    Scraping keeps every language-matching fragment; this step builds JSONL rows
    that contain at least ``min_words`` words each by joining adjacent pieces.
    A trailing fragment shorter than ``min_words`` is appended to the previous
    row when possible, otherwise emitted on its own so no text is lost.
    """
    if min_words <= 0 or not records:
        return records

    packed: list[dict[str, Any]] = []
    buffer_parts: list[str] = []
    buffer_source = ""
    buffer_lang = ""
    buffer_confidences: list[float] = []
    buffer_methods: list[str] = []

    def _flush() -> None:
        nonlocal buffer_parts, buffer_source, buffer_lang
        nonlocal buffer_confidences, buffer_methods
        if not buffer_parts:
            return
        packed.append(
            {
                "source": buffer_source,
                "paragraph": " ".join(buffer_parts).strip(),
                "language_code": buffer_lang,
                "confidence": round(min(buffer_confidences), 6),
                "detection_method": _combined_method(buffer_methods),
            }
        )
        buffer_parts = []
        buffer_confidences = []
        buffer_methods = []

    for record in records:
        source = record["source"]
        text = record["paragraph"].strip()
        if not text:
            continue

        if buffer_parts and source != buffer_source:
            _flush()

        if not buffer_parts:
            buffer_source = source
            buffer_lang = record["language_code"]

        buffer_parts.append(text)
        buffer_confidences.append(float(record["confidence"]))
        buffer_methods.append(str(record.get("detection_method", "")))

        if _word_count(" ".join(buffer_parts)) >= min_words:
            _flush()

    if not buffer_parts:
        return packed

    combined = " ".join(buffer_parts).strip()
    if packed and packed[-1]["source"] == buffer_source:
        packed[-1]["paragraph"] = f"{packed[-1]['paragraph']} {combined}".strip()
        packed[-1]["confidence"] = round(
            min(packed[-1]["confidence"], min(buffer_confidences)),
            6,
        )
        packed[-1]["detection_method"] = _combined_method(
            [packed[-1]["detection_method"], *buffer_methods]
        )
    else:
        packed.append(
            {
                "source": buffer_source,
                "paragraph": combined,
                "language_code": buffer_lang,
                "confidence": round(min(buffer_confidences), 6),
                "detection_method": _combined_method(buffer_methods),
            }
        )

    return packed


def _combined_method(methods: list[str]) -> str:
    unique = {m for m in methods if m}
    if len(unique) == 1:
        return unique.pop()
    if not unique:
        return ""
    return "+".join(sorted(unique))
