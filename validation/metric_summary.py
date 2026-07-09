"""Aggregate LLM metric results for the summary report."""

from __future__ import annotations

from collections import Counter
from typing import Any

from validation.models import ValidationResult

LLM_METRIC_NAMES = ("domain", "coherence", "grammar", "sensitivity")


def _ref(result: ValidationResult) -> dict[str, Any]:
    return {
        "line_number": result.line_number,
        "source": result.source,
    }


def _round(value: float | None, places: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, places)


def _aggregate_domain(results: list[ValidationResult]) -> dict[str, Any]:
    if not any("domain" in r.metrics for r in results):
        return {
            "method": "llm",
            "enabled": False,
            "evaluated_count": 0,
            "failed_count": 0,
            "message": "Not run — use --enable-llm",
            "category_distribution": {},
            "custom_categories": [],
            "records": [],
        }

    categories: Counter[str] = Counter()
    custom_categories: set[str] = set()
    records: list[dict[str, Any]] = []
    evaluated = 0
    failed = 0
    enabled = False
    error: str | None = None

    for result in results:
        metric = result.metrics.get("domain")
        if not metric:
            continue
        if metric.data.get("enabled") is False:
            enabled = False
            continue
        enabled = True
        if not metric.available:
            failed += 1
            error = error or metric.error
            continue
        evaluated += 1
        category = metric.data.get("category", "other")
        categories[category] += 1
        if metric.data.get("is_custom"):
            custom_categories.add(category)
        records.append(
            {
                **_ref(result),
                "category": category,
                "is_custom": metric.data.get("is_custom", False),
                "confidence": metric.data.get("confidence"),
                "secondary": metric.data.get("secondary", []),
                "rationale": metric.data.get("rationale", ""),
                "flags": metric.flags,
            }
        )

    confidences = [r["confidence"] for r in records if r.get("confidence") is not None]
    return {
        "method": "llm",
        "enabled": enabled,
        "evaluated_count": evaluated,
        "failed_count": failed,
        "error": error,
        "category_distribution": dict(categories.most_common()),
        "custom_categories": sorted(custom_categories),
        "mean_confidence": _round(sum(confidences) / len(confidences)) if confidences else None,
        "records": records,
    }


def _aggregate_coherence(results: list[ValidationResult]) -> dict[str, Any]:
    if not any("coherence" in r.metrics for r in results):
        return {
            "method": "llm",
            "enabled": False,
            "evaluated_count": 0,
            "failed_count": 0,
            "message": "Not run — use --enable-llm",
            "records": [],
        }

    records: list[dict[str, Any]] = []
    evaluated = 0
    failed = 0
    enabled = False
    error: str | None = None
    scores: list[float] = []

    for result in results:
        metric = result.metrics.get("coherence")
        if not metric:
            continue
        if metric.data.get("enabled") is False:
            enabled = False
            continue
        enabled = True
        if not metric.available:
            failed += 1
            error = error or metric.error
            continue
        evaluated += 1
        score = float(metric.data.get("score", 0.0))
        scores.append(score)
        records.append(
            {
                **_ref(result),
                "score": metric.data.get("score"),
                "issues": metric.data.get("issues", []),
                "summary": metric.data.get("summary", ""),
                "flags": metric.flags,
            }
        )

    return {
        "method": "llm",
        "enabled": enabled,
        "evaluated_count": evaluated,
        "failed_count": failed,
        "error": error,
        "mean_score": _round(sum(scores) / len(scores)) if scores else None,
        "min_score": _round(min(scores)) if scores else None,
        "max_score": _round(max(scores)) if scores else None,
        "records": records,
    }


def _aggregate_grammar(results: list[ValidationResult]) -> dict[str, Any]:
    if not any("grammar" in r.metrics for r in results):
        return {
            "method": "llm",
            "enabled": False,
            "evaluated_count": 0,
            "failed_count": 0,
            "message": "Not run — use --enable-llm",
            "records": [],
        }

    records: list[dict[str, Any]] = []
    evaluated = 0
    failed = 0
    enabled = False
    error: str | None = None
    error_rates: list[float] = []
    all_examples: list[str] = []

    for result in results:
        metric = result.metrics.get("grammar")
        if not metric:
            continue
        if metric.data.get("enabled") is False:
            enabled = False
            continue
        enabled = True
        if not metric.available:
            failed += 1
            error = error or metric.error
            continue
        evaluated += 1
        rate = float(metric.data.get("error_rate", 0.0))
        error_rates.append(rate)
        examples = metric.data.get("examples") or []
        all_examples.extend(examples)
        records.append(
            {
                **_ref(result),
                "error_rate": metric.data.get("error_rate"),
                "error_count": metric.data.get("error_count"),
                "examples": examples,
                "summary": metric.data.get("summary", ""),
                "flags": metric.flags,
            }
        )

    return {
        "method": "llm",
        "enabled": enabled,
        "evaluated_count": evaluated,
        "failed_count": failed,
        "error": error,
        "mean_error_rate": _round(sum(error_rates) / len(error_rates)) if error_rates else None,
        "max_error_rate": _round(max(error_rates)) if error_rates else None,
        "examples": list(dict.fromkeys(all_examples))[:50],
        "records": records,
    }


def _aggregate_sensitivity(results: list[ValidationResult]) -> dict[str, Any]:
    flagged_records: list[dict[str, Any]] = []
    total_evaluated = 0
    llm_evaluated = 0
    llm_failed = 0
    regex_flagged = 0
    flagged_scores: list[float] = []
    category_counts: Counter[str] = Counter()
    regex_totals: Counter[str] = Counter()
    method = "regex_only"

    for result in results:
        metric = result.metrics.get("sensitivity")
        if not metric:
            continue
        total_evaluated += 1
        data = metric.data
        if data.get("method") == "llm+regex":
            method = "llm+regex"
        regex_hits = data.get("regex_hits") or {}
        for label, count in regex_hits.items():
            regex_totals[label] += count

        score = data.get("score")
        if score is not None:
            llm_evaluated += 1
        elif data.get("llm_available") and data.get("method") == "llm+regex":
            llm_failed += 1

        if not metric.flags:
            continue

        if "potential_pii_in_text" in metric.flags:
            regex_flagged += 1

        if score is not None:
            flagged_scores.append(float(score))
            for cat in data.get("categories") or []:
                category_counts[str(cat)] += 1

        flagged_records.append(
            {
                **_ref(result),
                "score": score,
                "categories": data.get("categories", []),
                "regex_hits": regex_hits,
                "summary": data.get("llm_summary"),
                "method": data.get("method"),
                "flags": metric.flags,
            }
        )

    return {
        "method": method,
        "evaluated_count": total_evaluated,
        "flagged_count": len(flagged_records),
        "llm_evaluated_count": llm_evaluated,
        "llm_failed_count": llm_failed,
        "regex_flagged_count": regex_flagged,
        "mean_score": _round(sum(flagged_scores) / len(flagged_scores)) if flagged_scores else None,
        "max_score": _round(max(flagged_scores)) if flagged_scores else None,
        "category_distribution": dict(category_counts.most_common()),
        "regex_hit_totals": dict(regex_totals),
        "records": flagged_records,
    }


def aggregate_llm_metrics(results: list[ValidationResult]) -> dict[str, Any]:
    """Build summary section for domain, coherence, grammar, and sensitivity."""
    return {
        "domain": _aggregate_domain(results),
        "coherence": _aggregate_coherence(results),
        "grammar": _aggregate_grammar(results),
        "sensitivity": _aggregate_sensitivity(results),
    }
