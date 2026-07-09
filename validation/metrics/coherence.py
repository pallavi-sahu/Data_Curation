"""Sentence coherence via optional LLM."""

from __future__ import annotations

from validation.config import ValidationConfig
from validation.llm.client import (
    evaluate_coherence,
    get_last_llm_error,
    get_llm_provider,
    llm_api_key_hint,
    llm_available,
)
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult


class CoherenceValidator(BaseValidator):
    name = "coherence"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        if not config.enable_llm:
            return MetricResult(
                name=self.name,
                data={"method": "llm", "enabled": False, "provider": get_llm_provider()},
                available=False,
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

        result = evaluate_coherence(record.text, record.language_code)
        if result is None:
            return MetricResult(
                name=self.name,
                data={"method": "llm", "provider": get_llm_provider()},
                available=False,
                error=f"LLM coherence check failed. {get_last_llm_error() or ''}".strip(),
            )

        score = float(result.get("score", 0.0))
        flags: list[str] = []
        if score < config.thresholds.min_coherence_score:
            flags.append("low_coherence")

        return MetricResult(
            name=self.name,
            data={
                "method": "llm",
                "provider": get_llm_provider(),
                "score": round(score, 4),
                "issues": result.get("issues", []),
                "summary": result.get("summary", ""),
            },
            flags=flags,
        )
