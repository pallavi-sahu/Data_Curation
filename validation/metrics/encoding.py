"""Encoding, mojibake, HTML entities, and smart-quote checks."""

from __future__ import annotations

import unicodedata

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import HTML_ENTITY_PATTERN, MOJIBAKE_MARKERS, SMART_QUOTE_CHARS

try:
    import ftfy

    FTFY_AVAILABLE = True
except ImportError:
    FTFY_AVAILABLE = False


class EncodingValidator(BaseValidator):
    name = "encoding"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        flags: list[str] = []

        mojibake_hits = [m for m in MOJIBAKE_MARKERS if m in text]
        html_entities = HTML_ENTITY_PATTERN.findall(text)
        smart_quotes = sum(1 for c in text if c in SMART_QUOTE_CHARS)
        nbsp_count = text.count("\u00A0") + text.count("&nbsp;")

        fixed_text = text
        changed_by_ftfy = False
        if FTFY_AVAILABLE:
            fixed_text = ftfy.fix_text(text, normalization="NFC")
            changed_by_ftfy = fixed_text != text

        normalized = unicodedata.normalize("NFC", text)
        needs_normalization = normalized != text

        if mojibake_hits:
            flags.append("mojibake_detected")
        if html_entities:
            flags.append("html_entities_present")
        if nbsp_count:
            flags.append("non_breaking_spaces")
        if changed_by_ftfy:
            flags.append("ftfy_would_modify")
        if needs_normalization:
            flags.append("needs_unicode_normalization")

        return MetricResult(
            name=self.name,
            data={
                "mojibake_markers": mojibake_hits,
                "html_entity_count": len(html_entities),
                "html_entities_sample": html_entities[:10],
                "smart_quote_count": smart_quotes,
                "nbsp_count": nbsp_count,
                "ftfy_available": FTFY_AVAILABLE,
                "ftfy_would_modify": changed_by_ftfy,
                "needs_unicode_normalization": needs_normalization,
            },
            flags=flags,
            available=True,
        )
