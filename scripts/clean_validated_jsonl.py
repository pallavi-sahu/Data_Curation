#!/usr/bin/env python3
"""
Clean curated JSONL text: redact PII, strip links/emoji/format chars, ftfy, punctuation, brackets.

Optional LLM sensitivity filter (domain/coherence/grammar are NOT run):
  --enable-llm-sensitivity drops whole records flagged as sensitive content.

Output:
  ``{stem}_validated_cleaned.jsonl`` — kept records
  ``{stem}_removed_sensitive.jsonl`` — dropped sensitive records (when LLM sensitivity enabled)
  ``{stem}_cleaning_stats.json`` — aggregate stats

Example:
  python scripts/clean_validated_jsonl.py --input final/gujarati.jsonl
  python scripts/clean_validated_jsonl.py --input final/gujarati.jsonl --output-dir validated_scripts \\
      --enable-llm-sensitivity --llm-provider gemini
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.cleaning import (
    FTFY_AVAILABLE,
    CleanStats,
    CleaningConfig,
    assess_llm_sensitivity,
    clean_record,
)
from validation.env_loader import load_project_env
from validation.llm.client import configure_llm_provider, get_llm_provider, llm_api_key_hint, llm_available, normalize_provider

load_project_env()

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clean curated JSONL: redact PII, remove links/emoji/format chars, ftfy, "
            "fix punctuation/brackets. Optionally drop LLM-flagged sensitive records."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input JSONL file (e.g. final/gujarati.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output (default: same directory as input).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N records (for testing).",
    )
    parser.add_argument(
        "--enable-llm-sensitivity",
        action="store_true",
        help="Use LLM for sensitivity only; drop records above --sensitivity-threshold.",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["gemini", "openai", "gpt"],
        default=None,
        help="LLM backend for sensitivity (default: VALIDATION_LLM_PROVIDER env, then gemini).",
    )
    parser.add_argument(
        "--sensitivity-threshold",
        type=float,
        default=0.30,
        help="Drop records when LLM sensitivity score exceeds this (default: 0.30).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    input_path: Path = args.input
    if not input_path.is_file():
        logger.error("Input file not found: %s", input_path)
        return 1

    provider = normalize_provider(args.llm_provider or os.environ.get("VALIDATION_LLM_PROVIDER"))
    configure_llm_provider(provider)

    config = CleaningConfig(
        enable_llm_sensitivity=args.enable_llm_sensitivity,
        llm_provider=provider,
        max_sensitivity_score=args.sensitivity_threshold,
        drop_sensitive_records=True,
    )

    if config.enable_llm_sensitivity:
        if not llm_available():
            logger.error("LLM sensitivity enabled but API key missing: %s", llm_api_key_hint())
            return 1
        logger.info(
            "LLM sensitivity filter enabled (provider=%s, threshold=%.2f) — "
            "domain/coherence/grammar are NOT run",
            get_llm_provider(),
            config.max_sensitivity_score,
        )

    output_dir = args.output_dir or input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}_validated_cleaned_2.jsonl"
    removed_path = output_dir / f"{input_path.stem}_removed_sensitive.jsonl"
    stats_path = output_dir / f"{input_path.stem}_cleaning_stats.json"

    if not FTFY_AVAILABLE:
        logger.warning("ftfy not installed — skipping ftfy.fix_text (pip install ftfy)")

    stats = CleanStats()

    removed_handle = (
        removed_path.open("w", encoding="utf-8") if config.enable_llm_sensitivity else None
    )

    try:
        with input_path.open(encoding="utf-8") as handle_in, output_path.open(
            "w", encoding="utf-8"
        ) as handle_out:
            for line_number, line in enumerate(handle_in, start=1):
                if args.limit is not None and line_number > args.limit:
                    break
                line = line.strip()
                if not line:
                    continue

                stats.records_in += 1
                record = json.loads(line)
                cleaned, changes = clean_record(record)

                if config.enable_llm_sensitivity:
                    stats.llm_sensitivity_checked += 1
                    if stats.llm_sensitivity_checked == 1 or stats.llm_sensitivity_checked % 10 == 0:
                        logger.info(
                            "LLM sensitivity check on record %d (line %d)...",
                            stats.llm_sensitivity_checked,
                            line_number,
                        )

                    sensitive, sensitivity_details = assess_llm_sensitivity(
                        cleaned.get("paragraph") or "",
                        str(cleaned.get("language_code") or ""),
                        config,
                    )

                    if sensitivity_details.get("method") == "llm_failed":
                        stats.llm_sensitivity_failed += 1
                        logger.warning(
                            "LLM sensitivity failed for line %d — keeping record: %s",
                            line_number,
                            sensitivity_details.get("error"),
                        )

                    if sensitive and config.drop_sensitive_records:
                        stats.sensitive_removed += 1
                        removed_record = {
                            **cleaned,
                            "_cleaning": changes,
                            "_sensitivity": sensitivity_details,
                            "_input_line_number": line_number,
                        }
                        if removed_handle is not None:
                            removed_handle.write(
                                json.dumps(removed_record, ensure_ascii=False) + "\n"
                            )
                        continue

                if changes["ftfy_applied"]:
                    stats.ftfy_applied += 1
                stats.pii_redactions += changes["pii_redactions"]
                stats.links_removed += changes["links_removed"]
                stats.emoji_removed += changes["emoji_removed"]
                stats.format_chars_removed += changes["format_chars_removed"]
                stats.foreign_script_words_removed += changes["foreign_script_words_removed"]
                if changes["punctuation_fixed"]:
                    stats.punctuation_fixed += 1
                if changes["brackets_fixed"]:
                    stats.brackets_fixed += 1
                stats.decorative_dot_dash_removed += changes.get("decorative_dot_dash_removed", 0)
                if not cleaned.get("paragraph", "").strip():
                    stats.empty_after_clean += 1

                handle_out.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
                stats.records_out += 1

                if line_number % 100 == 0:
                    logger.info("Cleaned %d records (%d removed as sensitive)...", stats.records_out, stats.sensitive_removed)
    finally:
        if removed_handle is not None:
            removed_handle.close()

    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats.to_dict(), handle, ensure_ascii=False, indent=2)

    logger.info(
        "Wrote %s (%d records kept, %d removed as sensitive). Stats: %s",
        output_path,
        stats.records_out,
        stats.sensitive_removed,
        stats.to_dict(),
    )
    if config.enable_llm_sensitivity and stats.sensitive_removed:
        logger.info("Removed sensitive records: %s", removed_path)
    logger.info("Cleaning summary: %s", stats_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
