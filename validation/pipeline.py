"""Validation pipeline orchestrator."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from validation.config import ValidationConfig
from validation.io import aggregate_results, load_jsonl, save_aggregate, save_validation_results
from validation.llm.client import configure_llm_provider
from validation.metrics import ALL_VALIDATORS, DETERMINISTIC_VALIDATORS
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, ValidationResult

logger = logging.getLogger(__name__)

LLM_VALIDATORS = frozenset({"domain", "coherence", "grammar"})


class ValidationPipeline:
    """Run configured validators over curated records."""

    def __init__(
        self,
        config: ValidationConfig | None = None,
        validators: Iterable[BaseValidator] | None = None,
    ) -> None:
        self.config = config or ValidationConfig()
        self.validators = list(validators) if validators is not None else list(ALL_VALIDATORS)

    def validate_record(self, record: CuratedRecord) -> ValidationResult:
        result = ValidationResult(
            source=record.source,
            language_code=record.language_code,
            line_number=record.line_number,
        )
        all_flags: list[str] = []

        for validator in self.validators:
            if validator.name in LLM_VALIDATORS and not self.config.enable_llm:
                continue
            try:
                metric = validator.validate(record, self.config)
            except Exception as exc:
                logger.exception("Validator %s failed", validator.name)
                from validation.models import MetricResult

                metric = MetricResult(
                    name=validator.name,
                    data={},
                    flags=["validator_error"],
                    available=False,
                    error=str(exc),
                )

            result.metrics[validator.name] = metric
            all_flags.extend(metric.flags)

        result.flags = sorted(set(all_flags))
        result.pass_validation = len(result.flags) == 0
        return result

    def validate_file(
        self,
        input_path: Path,
        limit: int | None = None,
    ) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        if self.config.enable_llm:
            logger.info(
                "LLM validators enabled — 4 API calls per record "
                "(domain, coherence, grammar, sensitivity). Large files take a long time."
            )
        for index, record in enumerate(load_jsonl(input_path, limit=limit), start=1):
            if self.config.enable_llm and (index == 1 or index % 10 == 0):
                logger.info("Processing record %d (line %s)...", index, record.line_number)
            results.append(self.validate_record(record))
        return results


def run_validation(
    input_path: Path,
    output_path: Path | None = None,
    summary_path: Path | None = None,
    *,
    config: ValidationConfig | None = None,
    deterministic_only: bool = False,
    limit: int | None = None,
) -> tuple[list[ValidationResult], dict]:
    """Validate one JSONL file and optionally write reports."""
    if config:
        configure_llm_provider(config.llm_provider)
    pipeline = ValidationPipeline(
        config=config,
        validators=DETERMINISTIC_VALIDATORS if deterministic_only else None,
    )
    results = pipeline.validate_file(input_path, limit=limit)
    summary = aggregate_results(results)

    if config.write_per_record_jsonl and output_path:
        save_validation_results(results, output_path)
    if summary_path:
        save_aggregate(summary, summary_path)

    return results, summary
