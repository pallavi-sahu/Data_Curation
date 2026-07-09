"""Abbreviation and acronym detection."""

from __future__ import annotations

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import ACRONYM_PATTERN, tokenize


class AbbreviationValidator(BaseValidator):
    name = "abbreviations"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        matches = ACRONYM_PATTERN.findall(text)
        tokens = tokenize(text)
        word_count = max(len(tokens), 1)
        density = len(matches) / word_count

        # Dotted abbreviations common in Indic Wikipedia refs: [ ૧૦ ] style footnotes
        bracket_refs = text.count("[") + text.count("]")

        flags: list[str] = []
        if density > config.thresholds.max_abbreviation_density:
            flags.append("high_abbreviation_density")

        return MetricResult(
            name=self.name,
            data={
                "count": len(matches),
                "density": round(density, 4),
                "items": matches[:30],
                "bracket_reference_markers": bracket_refs,
            },
            flags=flags,
        )
