"""PII detection via regex patterns."""

from __future__ import annotations

import re

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import EMAIL_PATTERN

AADHAAR_PATTERN = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
PAN_PATTERN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
PHONE_IN_PATTERN = re.compile(r"(?:\+91[\s\-]?)?[6-9]\d{9}\b")

PII_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("EMAIL_ADDRESS", EMAIL_PATTERN, 1.0),
    ("IN_PHONE", PHONE_IN_PATTERN, 0.9),
    ("IN_AADHAAR", AADHAAR_PATTERN, 0.85),
    ("IN_PAN", PAN_PATTERN, 0.85),
)


class PIIValidator(BaseValidator):
    name = "pii"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        entities: list[dict[str, str | float | int]] = []

        for entity_type, pattern, score in PII_PATTERNS:
            for match in pattern.finditer(text):
                entities.append(
                    {
                        "type": entity_type,
                        "start": match.start(),
                        "end": match.end(),
                        "text": match.group(),
                        "score": score,
                    }
                )

        entities.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))  # type: ignore[arg-type, operator]
        deduped: list[dict] = []
        seen_spans: set[tuple[int, int]] = set()
        for ent in entities:
            span = (ent["start"], ent["end"])  # type: ignore[index]
            if span in seen_spans:
                continue
            seen_spans.add(span)
            deduped.append(ent)

        flags: list[str] = []
        if len(deduped) > config.thresholds.max_pii_entities:
            flags.append("pii_detected")

        return MetricResult(
            name=self.name,
            data={
                "entity_count": len(deduped),
                "entities": deduped[:20],
                "method": "regex",
            },
            flags=flags,
        )
