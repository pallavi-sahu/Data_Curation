"""CLI for the validation pipeline."""

from __future__ import annotations

from validation.env_loader import load_project_env

load_project_env()

import argparse
import logging
import os
import sys
from pathlib import Path

from validation.config import DEFAULT_OUTPUT_DIR, ValidationConfig
from validation.io import infer_language_from_filename
from validation.llm.client import configure_llm_provider, get_llm_provider, normalize_provider
from validation.pipeline import run_validation

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate curated JSONL text data for Indic languages.",
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--input",
        type=Path,
        help="Path to a curated JSONL file (e.g. final/gujarati.jsonl).",
    )
    input_group.add_argument(
        "--input-dir",
        type=Path,
        help="Directory of curated JSONL files (e.g. final/).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for validation reports (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N records per file (for testing).",
    )
    parser.add_argument(
        "--deterministic-only",
        action="store_true",
        help="Skip LLM validators (domain, coherence, grammar); run regex/script metrics only.",
    )
    parser.add_argument(
        "--enable-llm",
        action="store_true",
        help="Enable LLM checks: domain, coherence, grammar, sensitivity.",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["gemini", "openai", "gpt"],
        default=None,
        help=(
            "LLM backend: gemini (Google) or openai/gpt (OpenAI). "
            "Default: VALIDATION_LLM_PROVIDER env var, then gemini."
        ),
    )
    parser.add_argument(
        "--write-records",
        action="store_true",
        help="Also write per-record {language}_validation.jsonl (disabled by default).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _run_single(
    input_path: Path,
    output_dir: Path,
    config: ValidationConfig,
    limit: int | None,
    deterministic_only: bool,
) -> None:
    stem = input_path.stem
    output_path = output_dir / f"{stem}_validation.jsonl" if config.write_per_record_jsonl else None
    summary_path = output_dir / f"{stem}_summary.json"
    lang = infer_language_from_filename(input_path)
    logger.info("Validating %s (language=%s)", input_path, lang or "unknown")

    results, summary = run_validation(
        input_path,
        output_path=output_path,
        summary_path=summary_path,
        config=config,
        deterministic_only=deterministic_only,
        limit=limit,
    )
    logger.info(
        "Done: %s — %d records, pass_rate=%.1f%%",
        input_path.name,
        summary["total_records"],
        100 * summary["pass_rate"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    provider = normalize_provider(
        args.llm_provider or os.environ.get("VALIDATION_LLM_PROVIDER")
    )
    configure_llm_provider(provider)

    config = ValidationConfig(
        enable_llm=args.enable_llm,
        llm_provider=provider,
        write_per_record_jsonl=args.write_records,
    )
    if args.deterministic_only:
        config.enable_llm = False

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    deterministic_only = args.deterministic_only or not config.enable_llm

    if config.enable_llm:
        logger.info("LLM provider: %s", get_llm_provider())

    if args.input:
        if not args.input.is_file():
            logger.error("Input file not found: %s", args.input)
            return 1
        _run_single(args.input, output_dir, config, args.limit, deterministic_only)
        return 0

    if not args.input_dir.is_dir():
        logger.error("Input directory not found: %s", args.input_dir)
        return 1

    jsonl_files = sorted(args.input_dir.glob("*.jsonl"))
    if not jsonl_files:
        logger.error("No JSONL files in %s", args.input_dir)
        return 1

    for path in jsonl_files:
        _run_single(path, output_dir, config, args.limit, deterministic_only)

    return 0


if __name__ == "__main__":
    sys.exit(main())
