"""Detect URLs and link density in paragraph text."""

from __future__ import annotations

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import URL_PATTERN, tokenize


class LinksValidator(BaseValidator):
    name = "links"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        urls = URL_PATTERN.findall(text)
        tokens = tokenize(text)
        word_count = max(len(tokens), 1)
        link_density = len(urls) / word_count

        flags: list[str] = []
        if link_density > config.thresholds.max_link_density:
            flags.append("high_link_density")
        if "http://" in text.lower() or "https://" in text.lower():
            flags.append("contains_raw_urls")

        return MetricResult(
            name=self.name,
            data={
                "url_count": len(urls),
                "urls": urls[:20],
                "link_density": round(link_density, 4),
                "word_count": len(tokens),
            },
            flags=flags,
        )
