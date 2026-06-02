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
    Combine consecutive language-filtered fragments into output chunks.

    Every emitted row has at least ``min_words`` words in ``paragraph``, except
    when the entire input has fewer than ``min_words`` words total (one row is
    still written so nothing is lost). Scraping keeps all fragments; only this
    step merges them for export.
    """
    if min_words <= 0 or not records:
        return records

    packed: list[dict[str, Any]] = []
    buffer_parts: list[str] = []
    buffer_source = ""
    buffer_lang = ""
    buffer_confidences: list[float] = []
    buffer_methods: list[str] = []

    def _flush_buffer() -> None:
        nonlocal buffer_parts, buffer_source, buffer_lang
        nonlocal buffer_confidences, buffer_methods
        if not buffer_parts:
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
        buffer_parts = []
        buffer_confidences = []
        buffer_methods = []

    for record in records:
        text = record["paragraph"].strip()
        if not text:
            continue

        if not buffer_parts:
            buffer_source = record["source"]
            buffer_lang = record["language_code"]
        elif record["source"] != buffer_source:
            buffer_source = _join_sources(buffer_source, record["source"])

        buffer_parts.append(text)
        buffer_confidences.append(float(record["confidence"]))
        buffer_methods.append(str(record.get("detection_method", "")))

        if _word_count(" ".join(buffer_parts)) >= min_words:
            _flush_buffer()

    if buffer_parts:
        combined_wc = _word_count(" ".join(buffer_parts))
        if combined_wc >= min_words:
            _flush_buffer()
        elif packed:
            _merge_into_row(
                packed[-1],
                buffer_source,
                buffer_lang,
                buffer_parts,
                buffer_confidences,
                buffer_methods,
            )
            buffer_parts = []
        else:
            _flush_buffer()

    return _enforce_min_words(packed, min_words=min_words)


def _enforce_min_words(
    rows: list[dict[str, Any]],
    *,
    min_words: int,
) -> list[dict[str, Any]]:
    """Merge any row shorter than ``min_words`` into a neighbor."""
    if min_words <= 0 or len(rows) <= 1:
        return rows

    index = 0
    while index < len(rows):
        if _word_count(rows[index]["paragraph"]) >= min_words:
            index += 1
            continue
        if index > 0:
            _merge_rows(rows[index - 1], rows[index])
            del rows[index]
            continue
        _merge_rows(rows[0], rows[1])
        del rows[0]
    return rows


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


def _merge_into_row(
    target: dict[str, Any],
    source: str,
    language_code: str,
    parts: list[str],
    confidences: list[float],
    methods: list[str],
) -> None:
    extra = " ".join(parts).strip()
    if extra:
        target["paragraph"] = f"{target['paragraph']} {extra}".strip()
    target["confidence"] = round(min(target["confidence"], min(confidences)), 6)
    target["detection_method"] = _combined_method(
        _method_tokens(target) + methods
    )
    if source and source != target["source"]:
        target["source"] = _join_sources(target["source"], source)
    if language_code:
        target["language_code"] = language_code


def _merge_rows(keep: dict[str, Any], drop: dict[str, Any]) -> None:
    extra = drop["paragraph"].strip()
    if extra:
        keep["paragraph"] = f"{keep['paragraph']} {extra}".strip()
    keep["confidence"] = round(min(keep["confidence"], drop["confidence"]), 6)
    keep["detection_method"] = _combined_method(
        _method_tokens(keep) + _method_tokens(drop)
    )
    if drop["source"] and drop["source"] != keep["source"]:
        keep["source"] = _join_sources(keep["source"], drop["source"])


def _join_sources(existing: str, new: str) -> str:
    parts = [p for p in existing.split(" | ") if p]
    if new not in parts:
        parts.append(new)
    return " | ".join(parts)


def _method_tokens(row: dict[str, Any]) -> list[str]:
    raw = str(row.get("detection_method", ""))
    if not raw:
        return []
    return [m for m in raw.split("+") if m]


def _combined_method(methods: list[str]) -> str:
    unique = sorted({m for m in methods if m})
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return ""
    return "+".join(unique)
