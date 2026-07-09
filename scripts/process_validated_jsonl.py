#!/usr/bin/env python3
"""
Clean, LLM-filter, and enrich curated JSONL in one pass.

Steps per record:
  1. Text cleaning (PII redaction, links/emoji/format chars, ftfy, punctuation,
     optional Latin-word removal)
  2. One combined LLM call: sensitivity + coherence + domain (parallel across rows)

Rows blocked with ``prompt_feedback.block_reason: PROHIBITED_CONTENT`` are dropped.

Outputs (default: same directory as input, ``{language}`` stem from filename):
  ``{language}_validated_cleaned.jsonl`` — kept rows with domain field
  ``{language}_noncoherent.jsonl`` — low-coherence rows
  ``{language}_removed_sensitive.jsonl`` — sensitive rows
  ``{language}_processing_stats.json`` — run summary

Example (fish/bash — single line, no ``\\`` before flags):
  python3 scripts/process_validated_jsonl.py --input final/gujarati.jsonl --workers 10
  python3 scripts/process_validated_jsonl.py --input final/urdu.jsonl --output-dir validated_scripts --coherence-threshold 0.55 --workers 8 --limit 20
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.cleaning import FTFY_AVAILABLE, CleanStats, clean_record
from validation.config import ValidationThresholds
from validation.env_loader import load_project_env
from validation.llm.client import (
    RecordQualityResult,
    configure_llm_provider,
    evaluate_record_quality,
    get_llm_provider,
    llm_api_key_hint,
    llm_available,
    normalize_provider,
)

load_project_env()

logger = logging.getLogger(__name__)

_STEM_SUFFIXES = ("_validated_cleaned_2", "_validated_cleaned", "_validated")
RowStatus = Literal["kept", "noncoherent", "sensitive", "prohibited", "empty", "llm_failed"]


@dataclass
class PreparedRow:
    line_number: int
    cleaned: dict[str, Any]
    changes: dict[str, Any]
    paragraph: str
    language_code: str


@dataclass
class ProcessedRow:
    prepared: PreparedRow
    status: RowStatus
    quality: RecordQualityResult | None = None
    sidecar: dict[str, Any] | None = None


def language_stem_from_input(input_path: Path) -> str:
    stem = input_path.stem
    for suffix in _STEM_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clean JSONL, drop sensitive/non-coherent/prohibited rows, "
            "and add an LLM-assigned domain field to kept records."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input JSONL (e.g. final/gujarati.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for outputs (default: same directory as input).",
    )
    parser.add_argument(
        "--coherence-threshold",
        type=float,
        default=ValidationThresholds().min_coherence_score,
        help="Drop rows when LLM coherence score is below this (default: 0.60).",
    )
    parser.add_argument(
        "--sensitivity-threshold",
        type=float,
        default=ValidationThresholds().max_sensitivity_score,
        help="Drop rows when LLM sensitivity score exceeds this (default: 0.30).",
    )
    parser.add_argument(
        "--skip-sensitivity",
        action="store_true",
        help="Skip LLM sensitivity filtering.",
    )
    parser.add_argument(
        "--remove-latin-words",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Remove pure Latin-script words from paragraphs (default: enabled).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel LLM requests (default: 8). Lower if you hit rate limits.",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Process one row at a time (no parallelism; for debugging).",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["gemini", "openai", "gpt"],
        default=None,
        help="LLM backend (default: VALIDATION_LLM_PROVIDER env, then gemini).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N records (for testing).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def _accumulate_clean_stats(stats: CleanStats, changes: dict[str, Any]) -> None:
    if changes["ftfy_applied"]:
        stats.ftfy_applied += 1
    stats.pii_redactions += changes["pii_redactions"]
    stats.links_removed += changes["links_removed"]
    stats.emoji_removed += changes["emoji_removed"]
    stats.format_chars_removed += changes["format_chars_removed"]
    stats.foreign_script_words_removed += changes["foreign_script_words_removed"]
    stats.latin_words_removed += changes.get("latin_words_removed", 0)
    if changes["punctuation_fixed"]:
        stats.punctuation_fixed += 1
    if changes["brackets_fixed"]:
        stats.brackets_fixed += 1
    stats.decorative_dot_dash_removed += changes.get("decorative_dot_dash_removed", 0)


def _load_prepared_rows(
    input_path: Path,
    limit: int | None,
    *,
    remove_latin_words: bool,
) -> tuple[list[PreparedRow], CleanStats]:
    stats = CleanStats()
    prepared: list[PreparedRow] = []

    with input_path.open(encoding="utf-8") as handle_in:
        for line_number, line in enumerate(handle_in, start=1):
            if limit is not None and stats.records_in >= limit:
                break

            line = line.strip()
            if not line:
                continue

            stats.records_in += 1
            record = json.loads(line)
            cleaned, changes = clean_record(record, remove_latin=remove_latin_words)
            paragraph = str(cleaned.get("paragraph") or "")

            if not paragraph.strip():
                stats.empty_after_clean += 1
                continue

            _accumulate_clean_stats(stats, changes)
            prepared.append(
                PreparedRow(
                    line_number=line_number,
                    cleaned=cleaned,
                    changes=changes,
                    paragraph=paragraph,
                    language_code=str(cleaned.get("language_code") or ""),
                )
            )

    return prepared, stats


def _classify_row(
    prepared: PreparedRow,
    *,
    include_sensitivity: bool,
    sensitivity_threshold: float,
    coherence_threshold: float,
) -> ProcessedRow:
    quality = evaluate_record_quality(
        prepared.paragraph,
        prepared.language_code,
        include_sensitivity=include_sensitivity,
    )

    if quality.prohibited:
        return ProcessedRow(prepared=prepared, status="prohibited", quality=quality)

    if not quality.ok:
        return ProcessedRow(prepared=prepared, status="llm_failed", quality=quality)

    if include_sensitivity and quality.sensitivity_score is not None:
        if quality.sensitivity_score > sensitivity_threshold:
            return ProcessedRow(
                prepared=prepared,
                status="sensitive",
                quality=quality,
                sidecar={
                    "_sensitivity": {
                        "score": round(quality.sensitivity_score, 4),
                        "threshold": sensitivity_threshold,
                    },
                    "_input_line_number": prepared.line_number,
                },
            )

    if quality.coherence_score is not None and quality.coherence_score < coherence_threshold:
        return ProcessedRow(
            prepared=prepared,
            status="noncoherent",
            quality=quality,
            sidecar={
                "_coherence": {
                    "score": round(quality.coherence_score, 4),
                    "issues": list(quality.coherence_issues),
                },
                "_input_line_number": prepared.line_number,
            },
        )

    return ProcessedRow(prepared=prepared, status="kept", quality=quality)


def _run_llm_pass(
    prepared_rows: list[PreparedRow],
    *,
    include_sensitivity: bool,
    sensitivity_threshold: float,
    coherence_threshold: float,
    workers: int,
    sequential: bool,
) -> list[ProcessedRow]:
    if not prepared_rows:
        return []

    classify_kwargs = {
        "include_sensitivity": include_sensitivity,
        "sensitivity_threshold": sensitivity_threshold,
        "coherence_threshold": coherence_threshold,
    }

    if sequential or workers <= 1:
        results: list[ProcessedRow] = []
        for index, row in enumerate(prepared_rows, start=1):
            results.append(_classify_row(row, **classify_kwargs))
            if index == 1 or index % 10 == 0:
                logger.info("LLM progress: %d / %d", index, len(prepared_rows))
        return results

    results: list[ProcessedRow] = []
    total = len(prepared_rows)
    logger.info("Running LLM on %d rows with %d workers (1 API call per row)...", total, workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_classify_row, row, **classify_kwargs): row.line_number
            for row in prepared_rows
        }
        done = 0
        for future in as_completed(futures):
            results.append(future.result())
            done += 1
            if done == 1 or done % 10 == 0 or done == total:
                logger.info("LLM progress: %d / %d", done, total)

    results.sort(key=lambda item: item.prepared.line_number)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.workers < 1:
        logger.error("--workers must be at least 1")
        return 1

    input_path: Path = args.input
    if not input_path.is_file():
        logger.error("Input file not found: %s", input_path)
        return 1

    provider = normalize_provider(args.llm_provider or os.environ.get("VALIDATION_LLM_PROVIDER"))
    configure_llm_provider(provider)

    if not llm_available():
        logger.error("LLM API key missing: %s", llm_api_key_hint())
        return 1

    output_dir = args.output_dir or input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    lang_stem = language_stem_from_input(input_path)
    output_path = output_dir / f"{lang_stem}_validated_cleaned.jsonl"
    noncoherent_path = output_dir / f"{lang_stem}_noncoherent.jsonl"
    sensitive_path = output_dir / f"{lang_stem}_removed_sensitive.jsonl"
    stats_path = output_dir / f"{lang_stem}_processing_stats.json"

    if not FTFY_AVAILABLE:
        logger.warning("ftfy not installed — skipping ftfy.fix_text (pip install ftfy)")

    include_sensitivity = not args.skip_sensitivity
    workers = 1 if args.sequential else args.workers

    logger.info(
        "Processing %s (provider=%s, coherence>=%.2f, sensitivity<=%.2f, workers=%d, remove_latin=%s)",
        input_path,
        get_llm_provider(),
        args.coherence_threshold,
        args.sensitivity_threshold,
        workers,
        args.remove_latin_words,
    )

    started = time.monotonic()
    prepared_rows, clean_stats = _load_prepared_rows(
        input_path,
        args.limit,
        remove_latin_words=args.remove_latin_words,
    )
    logger.info("Cleaned %d non-empty rows locally (%d empty skipped)", len(prepared_rows), clean_stats.empty_after_clean)

    processed_rows = _run_llm_pass(
        prepared_rows,
        include_sensitivity=include_sensitivity,
        sensitivity_threshold=args.sensitivity_threshold,
        coherence_threshold=args.coherence_threshold,
        workers=workers,
        sequential=args.sequential,
    )

    extra_stats: dict[str, Any] = {
        "input": str(input_path),
        "output_kept": str(output_path),
        "output_noncoherent": str(noncoherent_path),
        "output_sensitive": str(sensitive_path),
        "coherence_threshold": args.coherence_threshold,
        "sensitivity_threshold": args.sensitivity_threshold,
        "sensitivity_enabled": include_sensitivity,
        "remove_latin_words": args.remove_latin_words,
        "llm_provider": get_llm_provider(),
        "workers": workers,
        "llm_calls_per_row": 1,
        "records_noncoherent": 0,
        "records_prohibited": 0,
        "records_llm_failed": 0,
        "records_domain_failed": 0,
        "domains_assigned": {},
    }

    with output_path.open("w", encoding="utf-8") as handle_out, noncoherent_path.open(
        "w", encoding="utf-8"
    ) as handle_noncoherent, sensitive_path.open("w", encoding="utf-8") as handle_sensitive:
        for item in processed_rows:
            prepared = item.prepared
            quality = item.quality

            if item.status == "prohibited":
                extra_stats["records_prohibited"] += 1
                logger.warning(
                    "Line %d blocked as PROHIBITED_CONTENT: %s",
                    prepared.line_number,
                    quality.error if quality else "",
                )
                continue

            if item.status == "sensitive":
                clean_stats.sensitive_removed += 1
                clean_stats.llm_sensitivity_checked += 1
                removed = {**prepared.cleaned, **(item.sidecar or {})}
                handle_sensitive.write(json.dumps(removed, ensure_ascii=False) + "\n")
                continue

            if item.status == "noncoherent":
                extra_stats["records_noncoherent"] += 1
                removed = {**prepared.cleaned, **(item.sidecar or {})}
                handle_noncoherent.write(json.dumps(removed, ensure_ascii=False) + "\n")
                continue

            if item.status == "llm_failed":
                extra_stats["records_llm_failed"] += 1
                logger.warning(
                    "LLM failed for line %d — keeping record: %s",
                    prepared.line_number,
                    quality.error if quality else "unknown error",
                )
                domain = "other"
                extra_stats["records_domain_failed"] += 1
            else:
                domain = quality.domain if quality and quality.domain else "other"
                if not quality or not quality.domain:
                    extra_stats["records_domain_failed"] += 1

            if include_sensitivity:
                clean_stats.llm_sensitivity_checked += 1

            kept = dict(prepared.cleaned)
            kept["domain"] = domain
            extra_stats["domains_assigned"][domain] = extra_stats["domains_assigned"].get(domain, 0) + 1
            handle_out.write(json.dumps(kept, ensure_ascii=False) + "\n")
            clean_stats.records_out += 1

    elapsed = time.monotonic() - started
    stats = {**clean_stats.to_dict(), **extra_stats}
    stats["keep_rate"] = (
        round(clean_stats.records_out / clean_stats.records_in, 4) if clean_stats.records_in else 0.0
    )
    stats["elapsed_seconds"] = round(elapsed, 1)
    stats["rows_per_second"] = (
        round(len(prepared_rows) / elapsed, 2) if elapsed > 0 and prepared_rows else 0.0
    )

    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)

    logger.info(
        "Done in %.1fs (%.2f rows/s): %d kept -> %s (%d noncoherent, %d sensitive, %d prohibited)",
        elapsed,
        stats["rows_per_second"],
        clean_stats.records_out,
        output_path,
        extra_stats["records_noncoherent"],
        clean_stats.sensitive_removed,
        extra_stats["records_prohibited"],
    )
    if extra_stats["records_noncoherent"]:
        logger.info("Noncoherent rows: %s", noncoherent_path)
    if clean_stats.sensitive_removed:
        logger.info("Sensitive rows: %s", sensitive_path)
    logger.info("Stats: %s", stats_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
