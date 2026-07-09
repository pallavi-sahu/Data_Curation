#!/usr/bin/env python3
"""
Run LLM coherence checks on validated JSONL and split rows by score.

Input (expected name): ``{language}_validated_cleaned_2.jsonl``
Outputs (same directory as input by default):
  ``{language}_noncoherent.jsonl`` — rows below --coherence-threshold
  input file rewritten in place — coherent rows only

Rows blocked by Gemini with ``prompt_feedback.block_reason: PROHIBITED_CONTENT``
are dropped from the input file (not written to the noncoherent file).

Example:
  python scripts/filter_coherence.py --input validated_scripts/gujarati_validated_cleaned_2.jsonl
  python scripts/filter_coherence.py --input validated_scripts/marathi_validated_cleaned_2.jsonl \\
      --coherence-threshold 0.65 --llm-provider gemini --limit 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.config import ValidationThresholds
from validation.env_loader import load_project_env
from validation.llm.client import (
    configure_llm_provider,
    evaluate_coherence,
    get_last_llm_error,
    get_llm_provider,
    is_prohibited_content_block,
    llm_api_key_hint,
    llm_available,
    normalize_provider,
)

load_project_env()

logger = logging.getLogger(__name__)

_INPUT_SUFFIX = "_validated_cleaned_2"


def language_stem_from_input(input_path: Path) -> str:
    stem = input_path.stem
    if stem.endswith(_INPUT_SUFFIX):
        return stem[: -len(_INPUT_SUFFIX)]
    return stem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Keep coherent JSONL rows in the input file; write low-coherence rows "
            "to {language}_noncoherent.jsonl. Drop PROHIBITED_CONTENT rows from input."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input JSONL (e.g. validated_scripts/gujarati_validated_cleaned_2.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for noncoherent output and stats (default: same as input).",
    )
    parser.add_argument(
        "--coherence-threshold",
        type=float,
        default=ValidationThresholds().min_coherence_score,
        help="Drop rows when LLM coherence score is below this (default: 0.60).",
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
        "--no-rewrite-input",
        action="store_true",
        help="Do not rewrite the input file; only write noncoherent rows and stats.",
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

    if not llm_available():
        logger.error("LLM API key missing: %s", llm_api_key_hint())
        return 1

    output_dir = args.output_dir or input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    lang_stem = language_stem_from_input(input_path)
    noncoherent_path = output_dir / f"{lang_stem}_noncoherent.jsonl"
    stats_path = output_dir / f"{lang_stem}_coherence_stats.json"
    rewrite_path = input_path.with_suffix(input_path.suffix + ".tmp")

    stats: dict[str, Any] = {
        "input": str(input_path),
        "output_noncoherent": str(noncoherent_path),
        "coherence_threshold": args.coherence_threshold,
        "llm_provider": get_llm_provider(),
        "records_in": 0,
        "records_kept": 0,
        "records_noncoherent": 0,
        "records_prohibited": 0,
        "records_llm_failed": 0,
        "input_rewritten": not args.no_rewrite_input,
    }

    logger.info(
        "Coherence filter (provider=%s, threshold=%.2f) on %s",
        get_llm_provider(),
        args.coherence_threshold,
        input_path,
    )

    coherent_rows: list[str] = []

    with input_path.open(encoding="utf-8") as handle_in, noncoherent_path.open(
        "w", encoding="utf-8"
    ) as handle_noncoherent:
        for line_number, line in enumerate(handle_in, start=1):
            if args.limit is not None and stats["records_in"] >= args.limit:
                break

            line = line.strip()
            if not line:
                continue

            stats["records_in"] += 1
            record = json.loads(line)
            paragraph = str(record.get("paragraph") or "")
            language_code = str(record.get("language_code") or "")

            if stats["records_in"] == 1 or stats["records_in"] % 10 == 0:
                logger.info("LLM coherence check on record %d (line %d)...", stats["records_in"], line_number)

            result = evaluate_coherence(paragraph, language_code)

            if is_prohibited_content_block():
                stats["records_prohibited"] += 1
                logger.warning(
                    "Line %d blocked as PROHIBITED_CONTENT — removing from input: %s",
                    line_number,
                    get_last_llm_error(),
                )
                continue

            if result is None:
                stats["records_llm_failed"] += 1
                logger.warning(
                    "LLM coherence failed for line %d — keeping record: %s",
                    line_number,
                    get_last_llm_error(),
                )
                row = json.dumps(record, ensure_ascii=False)
                coherent_rows.append(row)
                stats["records_kept"] += 1
                continue

            score = float(result.get("score", 0.0))
            if score < args.coherence_threshold:
                stats["records_noncoherent"] += 1
                removed = {
                    **record,
                    "_coherence": {
                        "score": round(score, 4),
                        "issues": result.get("issues", []),
                        "summary": result.get("summary", ""),
                        "provider": get_llm_provider(),
                    },
                    "_input_line_number": line_number,
                }
                handle_noncoherent.write(json.dumps(removed, ensure_ascii=False) + "\n")
                continue

            stats["records_kept"] += 1
            coherent_rows.append(json.dumps(record, ensure_ascii=False))

            if stats["records_in"] % 25 == 0:
                logger.info(
                    "Progress: %d checked, %d kept, %d noncoherent, %d prohibited",
                    stats["records_in"],
                    stats["records_kept"],
                    stats["records_noncoherent"],
                    stats["records_prohibited"],
                )

    if not args.no_rewrite_input:
        with rewrite_path.open("w", encoding="utf-8") as handle_out:
            for row in coherent_rows:
                handle_out.write(row + "\n")
        rewrite_path.replace(input_path)
        stats["output_kept"] = str(input_path)
        logger.info("Rewrote input with %d coherent rows: %s", stats["records_kept"], input_path)
    else:
        stats["output_kept"] = None

    stats["keep_rate"] = round(stats["records_kept"] / stats["records_in"], 4) if stats["records_in"] else 0.0

    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)

    logger.info(
        "Done: %d kept, %d noncoherent -> %s, %d prohibited removed, %d LLM failures kept",
        stats["records_kept"],
        stats["records_noncoherent"],
        noncoherent_path,
        stats["records_prohibited"],
        stats["records_llm_failed"],
    )
    logger.info("Stats: %s", stats_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
