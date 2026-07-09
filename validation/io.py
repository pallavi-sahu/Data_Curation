"""Load curated JSONL records and write validation reports."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

from validation.config import FILENAME_TO_LANG
from validation.deterministic_summary import aggregate_deterministic_metrics
from validation.flag_details import extract_flag_detail
from validation.llm.client import get_llm_provider
from validation.metric_summary import aggregate_llm_metrics
from validation.models import CuratedRecord, ValidationResult


def load_jsonl(path: Path, limit: int | None = None) -> Iterator[CuratedRecord]:
    """Stream records from a curated JSONL file."""
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if limit is not None and line_number > limit:
                break
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            yield CuratedRecord.from_dict(data, line_number=line_number)


def infer_language_from_filename(path: Path) -> str | None:
    """Infer ISO 639-1 code from final/<language>.jsonl naming."""
    stem = path.stem.lower()
    return FILENAME_TO_LANG.get(stem)


def save_validation_results(
    results: list[ValidationResult],
    path: Path,
) -> None:
    """Write per-record validation results as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")


def aggregate_results(results: list[ValidationResult]) -> dict[str, Any]:
    """Summarize validation results with per-flag counts and record details."""
    total = len(results)
    passed = sum(1 for r in results if r.pass_validation)
    flag_counts: Counter[str] = Counter()
    flag_records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for result in results:
        for flag in result.flags:
            flag_counts[flag] += 1

        for metric_name, metric in result.metrics.items():
            for flag in metric.flags:
                detail = extract_flag_detail(metric_name, flag, metric.data, result)
                flag_records[flag].append(detail)

    flags_summary: dict[str, Any] = {}
    for flag, count in flag_counts.most_common():
        records = flag_records.get(flag, [])
        entry: dict[str, Any] = {"count": count, "records": records}
        # Flatten list-style payloads for quick scanning (e.g. all NSW tokens).
        aggregated = _aggregate_flag_items(flag, records)
        if aggregated is not None:
            entry["items"] = aggregated
        flags_summary[flag] = entry

    return {
        "total_records": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "llm_provider": get_llm_provider(),
        "llm_metrics": aggregate_llm_metrics(results),
        "deterministic_metrics": aggregate_deterministic_metrics(results),
        "flags": flags_summary,
    }


def _aggregate_flag_items(flag: str, records: list[dict[str, Any]]) -> list[Any] | None:
    """Build a flat deduplicated list of flagged items where applicable."""
    key_by_flag = {
        "high_nsw_density": "nsw_items",
        "contains_raw_urls": "urls",
        "high_link_density": "urls",
        "pii_detected": "entities",
        "contains_emoji": "emoji_samples",
        "mojibake_detected": "mojibake_markers",
        "html_entities_present": "html_entities",
        "repeated_punctuation": "repeated_punctuation",
        "high_abbreviation_density": "abbreviations",
        "high_grammar_error_rate": "examples",
        "low_coherence": "issues",
        "low_domain_confidence": "category",
        "uncategorized_content": "category",
        "sensitive_content": "categories",
    }
    item_key = key_by_flag.get(flag)
    if not item_key:
        return None

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


def save_aggregate(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
