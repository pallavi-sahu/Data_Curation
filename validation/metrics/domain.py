"""Content domain classification via LLM (education, news, stories, etc.)."""

from __future__ import annotations

from validation.config import ValidationConfig
from validation.llm.client import classify_content_domain, get_last_llm_error, get_llm_provider, llm_api_key_hint, llm_available
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult


class DomainValidator(BaseValidator):
    name = "domain"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        if not config.enable_llm:
            return MetricResult(
                name=self.name,
                data={"method": "llm", "enabled": False, "provider": get_llm_provider()},
                available=False,
                error="Enable LLM with --enable-llm to classify content domain.",
            )

        if not llm_available():
            return MetricResult(
                name=self.name,
                data={
                    "method": "llm",
                    "enabled": True,
                    "provider": get_llm_provider(),
                    "api_key_set": False,
                },
                available=False,
                error=llm_api_key_hint(),
            )

        result = classify_content_domain(record.text, record.language_code)
        if result is None:
            return MetricResult(
                name=self.name,
                data={"method": "llm", "provider": get_llm_provider()},
                available=False,
                error=f"LLM domain classification failed. {get_last_llm_error() or ''}".strip(),
            )

        confidence = float(result.get("confidence", 0.0))
        primary = result.get("primary", "other")
        primary_is_custom = bool(result.get("primary_is_custom", False))
        flags: list[str] = []
        if confidence < config.thresholds.min_domain_confidence:
            flags.append("low_domain_confidence")
        if not primary_is_custom and primary == "other":
            flags.append("uncategorized_content")

        return MetricResult(
            name=self.name,
            data={
                "method": "llm",
                "provider": get_llm_provider(),
                "category": primary,
                "is_custom": primary_is_custom,
                "secondary": result.get("secondary", []),
                "secondary_custom": result.get("secondary_custom", []),
                "confidence": round(confidence, 4),
                "rationale": result.get("rationale", ""),
            },
            flags=flags,
        )
