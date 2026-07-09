#!/usr/bin/env python3
"""
Assign ``"domain"`` to already-processed validated JSONL rows (LLM only).

Use this when cleaning / sensitivity / coherence are done but domain tagging
failed or was skipped. Does not re-run any other pipeline step.

Input example: ``final/Assamese/assamese_validated_cleaned.jsonl``

By default rewrites the input file in place with domains added.
Use ``--output`` to write to a separate file instead.

Example (single line — no ``\\`` before flags in fish):
  python3 scripts/tag_domains.py --input final/Assamese/assamese_validated_cleaned.jsonl
  python3 scripts/tag_domains.py --input final/malayalam/malayalam_validated_cleaned.jsonl --workers 10 --llm-provider gemini
  python3 scripts/tag_domains.py --input final/marathi/marathi_validated_cleaned.jsonl --retag-other --workers 10
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
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.env_loader import load_project_env
from validation.llm.client import (
    RecordQualityResult,
    classify_paragraph_domain,
    configure_llm_provider,
    evaluate_record_quality,
    get_llm_provider,
    llm_api_key_hint,
    llm_available,
    normalize_provider,
)

load_project_env()

logger = logging.getLogger(__name__)


@dataclass
class LoadedRow:
    line_number: int
    record: dict[str, Any]
    paragraph: str
    language_code: str
    needs_tag: bool


@dataclass
class TaggedRow:
    loaded: LoadedRow
    domain: str | None
    error: str | None = None
    block_reason: str | None = None
    skipped: bool = False

    @property
    def prohibited(self) -> bool:
        return self.block_reason == "PROHIBITED_CONTENT"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add or fix the domain field on validated JSONL rows using LLM.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input JSONL (e.g. final/Assamese/assamese_validated_cleaned.jsonl).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSONL path (default: rewrite --input in place).",
    )
    parser.add_argument(
        "--retag-all",
        action="store_true",
        help="Re-tag every row, even if domain is already set.",
    )
    parser.add_argument(
        "--retag-other",
        action="store_true",
        help="Also re-tag rows where domain is 'other' (typical LLM-failure fallback).",
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
        help="Process one row at a time (no parallelism).",
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


def _needs_domain_tag(
    record: dict[str, Any],
    *,
    retag_all: bool,
    retag_other: bool,
) -> bool:
    if retag_all:
        return True

    domain = record.get("domain")
    if domain is None or (isinstance(domain, str) and not domain.strip()):
        return True
    if retag_other and str(domain).strip().lower() == "other":
        return True
    return False


def _load_rows(
    input_path: Path,
    *,
    limit: int | None,
    retag_all: bool,
    retag_other: bool,
) -> list[LoadedRow]:
    rows: list[LoadedRow] = []

    with input_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if limit is not None and len(rows) >= limit:
                break

            line = line.strip()
            if not line:
                continue

            record = json.loads(line)
            needs_tag = _needs_domain_tag(record, retag_all=retag_all, retag_other=retag_other)
            rows.append(
                LoadedRow(
                    line_number=line_number,
                    record=record,
                    paragraph=str(record.get("paragraph") or ""),
                    language_code=str(record.get("language_code") or ""),
                    needs_tag=needs_tag,
                )
            )

    return rows


def _tag_domain(row: LoadedRow) -> TaggedRow:
    if not row.needs_tag:
        return TaggedRow(loaded=row, domain=str(row.record.get("domain") or ""), skipped=True)

    if not row.paragraph.strip():
        return TaggedRow(loaded=row, domain="other", error="empty paragraph")

    domain = classify_paragraph_domain(row.paragraph, row.language_code)
    if domain is not None:
        return TaggedRow(loaded=row, domain=domain)

    # classify_paragraph_domain uses globals; use domain-only combined call as fallback
    quality: RecordQualityResult = evaluate_record_quality(
        row.paragraph,
        row.language_code,
        include_sensitivity=False,
    )
    if quality.prohibited:
        return TaggedRow(
            loaded=row,
            domain=None,
            error=quality.error,
            block_reason=quality.block_reason,
        )
    if quality.domain:
        return TaggedRow(loaded=row, domain=quality.domain)

    return TaggedRow(loaded=row, domain="other", error=quality.error or "LLM domain failed")


def _run_tagging(rows: list[LoadedRow], workers: int, sequential: bool) -> list[TaggedRow]:
    to_tag = [row for row in rows if row.needs_tag]
    if not to_tag:
        return [TaggedRow(loaded=row, domain=str(row.record.get("domain") or ""), skipped=True) for row in rows]

    if sequential or workers <= 1:
        tagged: list[TaggedRow] = []
        for index, row in enumerate(to_tag, start=1):
            tagged.append(_tag_domain(row))
            if index == 1 or index % 10 == 0:
                logger.info("Domain tagging: %d / %d", index, len(to_tag))
        tagged_by_line = {item.loaded.line_number: item for item in tagged}
        return [
            tagged_by_line.get(row.line_number, TaggedRow(loaded=row, domain=str(row.record.get("domain") or ""), skipped=True))
            for row in rows
        ]

    logger.info("Tagging domains for %d rows with %d workers...", len(to_tag), workers)
    tagged = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_tag_domain, row): row.line_number for row in to_tag}
        done = 0
        for future in as_completed(futures):
            tagged.append(future.result())
            done += 1
            if done == 1 or done % 10 == 0 or done == len(to_tag):
                logger.info("Domain tagging: %d / %d", done, len(to_tag))

    tagged_by_line = {item.loaded.line_number: item for item in tagged}
    return [
        tagged_by_line.get(row.line_number, TaggedRow(loaded=row, domain=str(row.record.get("domain") or ""), skipped=True))
        for row in rows
    ]


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

    output_path = args.output or input_path
    rewrite_in_place = output_path.resolve() == input_path.resolve()
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp") if rewrite_in_place else output_path
    stats_path = output_path.parent / f"{output_path.stem}_domain_stats.json"

    workers = 1 if args.sequential else args.workers
    started = time.monotonic()

    rows = _load_rows(
        input_path,
        limit=args.limit,
        retag_all=args.retag_all,
        retag_other=args.retag_other,
    )
    need_count = sum(1 for row in rows if row.needs_tag)
    logger.info(
        "Loaded %d rows from %s (%d need domain tagging, workers=%d)",
        len(rows),
        input_path,
        need_count,
        workers,
    )

    tagged_rows = _run_tagging(rows, workers=workers, sequential=args.sequential)

    stats: dict[str, Any] = {
        "input": str(input_path),
        "output": str(output_path),
        "llm_provider": get_llm_provider(),
        "workers": workers,
        "only_missing": not args.retag_all,
        "retag_all": args.retag_all,
        "retag_other": args.retag_other,
        "records_in": len(rows),
        "records_tagged": 0,
        "records_skipped": 0,
        "records_failed": 0,
        "records_prohibited": 0,
        "domains_assigned": {},
    }

    with temp_path.open("w", encoding="utf-8") as handle_out:
        for item in tagged_rows:
            record = dict(item.loaded.record)

            if item.skipped:
                stats["records_skipped"] += 1
            elif item.prohibited:
                stats["records_prohibited"] += 1
                logger.warning(
                    "Line %d blocked as PROHIBITED_CONTENT — keeping row without domain: %s",
                    item.loaded.line_number,
                    item.error,
                )
            else:
                domain = item.domain or "other"
                record["domain"] = domain
                stats["records_tagged"] += 1
                stats["domains_assigned"][domain] = stats["domains_assigned"].get(domain, 0) + 1
                if item.error and domain == "other":
                    stats["records_failed"] += 1

            handle_out.write(json.dumps(record, ensure_ascii=False) + "\n")

    if rewrite_in_place:
        temp_path.replace(output_path)

    elapsed = time.monotonic() - started
    stats["elapsed_seconds"] = round(elapsed, 1)
    stats["tagged_per_second"] = round(stats["records_tagged"] / elapsed, 2) if elapsed > 0 else 0.0

    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)

    logger.info(
        "Done in %.1fs: %d tagged, %d skipped, %d failed, %d prohibited -> %s",
        elapsed,
        stats["records_tagged"],
        stats["records_skipped"],
        stats["records_failed"],
        stats["records_prohibited"],
        output_path,
    )
    logger.info("Stats: %s", stats_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
