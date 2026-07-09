"""Aggregate deterministic (regex/script) metric results for the summary report."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from validation.flag_details import extract_flag_detail
from validation.models import ValidationResult

DETERMINISTIC_METRIC_NAMES = (
    "words",
    "links",
    "symbols",
    "abbreviations",
    "nsw",
    "encoding",
    "punctuation",
    "emoji",
    "readability",
    "pii",
)

_FLAG_ITEM_KEYS = {
    "high_nsw_density": "nsw_items",
    "contains_raw_urls": "urls",
    "high_link_density": "urls",
    "pii_detected": "entities",
    "contains_emoji": "emoji_samples",
    "mojibake_detected": "mojibake_markers",
    "html_entities_present": "html_entities",
    "repeated_punctuation": "repeated_punctuation",
    "high_abbreviation_density": "abbreviations",
}


def _dedupe_items(records: list[dict[str, Any]], item_key: str) -> list[Any]:
    seen: set[str] = set()
    items: list[Any] = []
    for record in records:
        for item in record.get(item_key) or []:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            items.append(item)
    return items


def _aggregate_metric(results: list[ValidationResult], metric_name: str) -> dict[str, Any]:
    evaluated = 0
    flags: dict[str, dict[str, Any]] = {}

    for result in results:
        metric = result.metrics.get(metric_name)
        if not metric:
            continue
        evaluated += 1
        if not metric.flags:
            continue
        for flag in metric.flags:
            entry = flags.setdefault(flag, {"count": 0, "records": []})
            entry["count"] += 1
            entry["records"].append(
                extract_flag_detail(metric_name, flag, metric.data, result)
            )

    for flag, entry in flags.items():
        item_key = _FLAG_ITEM_KEYS.get(flag)
        if item_key:
            entry["items"] = _dedupe_items(entry["records"], item_key)

    flagged_records = sum(entry["count"] for entry in flags.values())
    return {
        "evaluated_count": evaluated,
        "flagged_count": flagged_records,
        "flags": flags,
    }


def aggregate_deterministic_metrics(results: list[ValidationResult]) -> dict[str, Any]:
    """Build summary for regex/script metrics with flagged record details."""
    return {
        name: _aggregate_metric(results, name)
        for name in DETERMINISTIC_METRIC_NAMES
    }
