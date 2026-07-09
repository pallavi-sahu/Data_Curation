"""Symbols and special character analysis."""

from __future__ import annotations

import unicodedata

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import count_char_categories


class SymbolsValidator(BaseValidator):
    name = "symbols"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        total_chars = len(text) or 1
        categories = count_char_categories(text)

        # Exclude Indic combining marks (matras) — normal in script text, not artifacts.
        COMBINING_CATEGORIES = frozenset({"Mn", "Mc", "Me"})

        special_chars: list[str] = []
        special_count = 0
        for char in text:
            if char.isspace() or char.isdigit():
                continue
            category = unicodedata.category(char)
            if category in COMBINING_CATEGORIES:
                continue
            if char.isalpha():
                continue
            special_count += 1
            if char not in special_chars:
                special_chars.append(char)

        special_ratio = special_count / total_chars
        flags: list[str] = []
        if special_ratio > config.thresholds.max_special_char_ratio:
            flags.append("high_special_char_ratio")

        # Control / format characters often indicate scraping damage.
        format_chars = sum(
            1 for c in text if unicodedata.category(c) in {"Cf", "Cc", "Co"}
        )
        if format_chars:
            flags.append("contains_format_characters")

        return MetricResult(
            name=self.name,
            data={
                "total_characters": total_chars,
                "special_char_count": special_count,
                "special_char_ratio": round(special_ratio, 4),
                "unicode_categories": categories,
                "distinct_special_chars": special_chars[:30],
                "format_char_count": format_chars,
            },
            flags=flags,
        )
