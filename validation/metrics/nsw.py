"""Non-standard word (NSW) density: numbers, dates, URLs, etc."""

from __future__ import annotations

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import NSW_PATTERNS, tokenize


class NSWValidator(BaseValidator):
    name = "nsw"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        tokens = tokenize(text)
        word_count = max(len(tokens), 1)

        counts: dict[str, int] = {}
        spans: dict[str, list[str]] = {}
        total_nsw = 0

        for label, pattern in NSW_PATTERNS.items():
            found = pattern.findall(text)
            counts[label] = len(found)
            spans[label] = found[:10]
            total_nsw += len(found)

        density = total_nsw / word_count
        flags: list[str] = []
        if density > config.thresholds.max_nsw_density:
            flags.append("high_nsw_density")

        return MetricResult(
            name=self.name,
            data={
                "total_nsw_count": total_nsw,
                "density": round(density, 4),
                "counts_by_type": counts,
                "samples": spans,
                "word_count": len(tokens),
            },
            flags=flags,
        )
