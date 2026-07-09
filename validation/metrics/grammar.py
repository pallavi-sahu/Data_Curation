"""Grammar error rate via optional LLM."""

from __future__ import annotations

from validation.config import ValidationConfig
from validation.llm.client import (
    evaluate_grammar,
    get_last_llm_error,
    get_llm_provider,
    llm_api_key_hint,
    llm_available,
)
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult


class GrammarValidator(BaseValidator):
    name = "grammar"

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

        result = evaluate_grammar(record.text, record.language_code)
        if result is None:
            return MetricResult(
                name=self.name,
                data={"method": "llm", "provider": get_llm_provider()},
                available=False,
                error=f"LLM grammar check failed. {get_last_llm_error() or ''}".strip(),
            )

        error_rate = float(result.get("error_rate", 0.0))
        flags: list[str] = []
        if error_rate > config.thresholds.max_grammar_error_rate:
            flags.append("high_grammar_error_rate")

        return MetricResult(
            name=self.name,
            data={
                "method": "llm",
                "provider": get_llm_provider(),
                "error_rate": round(error_rate, 4),
                "error_count": result.get("error_count"),
                "examples": result.get("examples", [])[:5],
                "summary": result.get("summary", ""),
            },
            flags=flags,
        )
