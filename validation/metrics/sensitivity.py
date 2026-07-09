"""Sensitivity screening via regex and optional LLM."""

from __future__ import annotations

import re

from validation.config import ValidationConfig
from validation.llm.client import evaluate_sensitivity, get_llm_provider, llm_available
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult

# Lightweight regex hints — not a substitute for LLM review.
SENSITIVE_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    "phone": re.compile(r"(?:\+91[\s\-]?)?[6-9]\d{9}\b"),
}


class SensitivityValidator(BaseValidator):
    name = "sensitivity"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        regex_hits = {
            label: len(pattern.findall(text))
            for label, pattern in SENSITIVE_PATTERNS.items()
        }

        llm_data: dict | None = None
        score = 0.0
        categories: list[str] = []
        flags: list[str] = []

        if config.enable_llm and llm_available():
            llm_data = evaluate_sensitivity(text, record.language_code)
            if llm_data:
                score = float(llm_data.get("score", 0.0))
                categories = list(llm_data.get("categories", []))
                if score > config.thresholds.max_sensitivity_score:
                    flags.append("sensitive_content")

        # Personal identifiers contribute to sensitivity even without LLM.
        if any(regex_hits.values()):
            flags.append("potential_pii_in_text")

        return MetricResult(
            name=self.name,
            data={
                "score": round(score, 4) if llm_data else None,
                "categories": categories,
                "regex_hits": regex_hits,
                "llm_summary": (llm_data or {}).get("summary"),
                "method": "llm+regex" if llm_data else "regex_only",
                "provider": get_llm_provider() if config.enable_llm else None,
                "llm_available": config.enable_llm and llm_available(),
            },
            flags=flags,
            available=True,
        )
