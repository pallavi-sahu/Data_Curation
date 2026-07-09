#!/usr/bin/env python3
"""
End-to-end JSONL pipeline: clean/validate, then filter and split paragraphs by word count.

Step 1 — ``process_validated_jsonl.py``
  Clean paragraphs (PII, links, Latin words, …), LLM coherence/sensitivity/domain.

Step 2 — ``filter_min_words.py`` (in-place on validated_cleaned output)
  Drop rows under ``--min-words``; prune 801–1000 word rows; split rows above
  ``--split-threshold`` into 500–800 word chunks.

Example:
  python3 scripts/run_validation_pipeline.py --input final/gujarati/gujarati.jsonl --workers 10
  python3 scripts/run_validation_pipeline.py --input final/urdu/urdu.jsonl --min-words 500 --max-words 800
  python3 scripts/run_validation_pipeline.py --all --workers 8
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_FINAL_DIR = _REPO_ROOT / "final"

logger = logging.getLogger(__name__)

_STEM_SUFFIXES = ("_validated_cleaned_2", "_validated_cleaned", "_validated")
_PROCESS_SCRIPT = _SCRIPTS_DIR / "process_validated_jsonl.py"
_FILTER_SCRIPT = _SCRIPTS_DIR / "filter_min_words.py"


def language_stem_from_input(input_path: Path) -> str:
    stem = input_path.stem
    for suffix in _STEM_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def validated_cleaned_path(input_path: Path, output_dir: Path) -> Path:
    return output_dir / f"{language_stem_from_input(input_path)}_validated_cleaned.jsonl"


def discover_language_jsonl_files() -> list[Path]:
    paths: list[Path] = []
    for path in sorted(_FINAL_DIR.glob("*/*.jsonl")):
        name = path.name
        if name.endswith("_validated_cleaned.jsonl"):
            continue
        if any(
            name.endswith(suffix)
            for suffix in (
                "_noncoherent.jsonl",
                "_removed_sensitive.jsonl",
                "_removed_short.jsonl",
                "_removed_latin.jsonl",
                "_min_words_stats.json",
                "_processing_stats.json",
                "_latin_removal_stats.json",
            )
        ):
            continue
        if "_min" in name and name.endswith("w.jsonl"):
            continue
        paths.append(path)
    return paths


def _run_step(label: str, command: list[str], *, verbose: bool) -> int:
    logger.info("=== %s ===", label)
    logger.info("Command: %s", " ".join(command))
    result = subprocess.run(command, cwd=_REPO_ROOT, check=False)
    if result.returncode != 0:
        logger.error("%s failed with exit code %d", label, result.returncode)
    return result.returncode


def run_pipeline_for_input(input_path: Path, args: argparse.Namespace) -> dict:
    output_dir = args.output_dir or input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned_path = validated_cleaned_path(input_path, output_dir)

    started = time.monotonic()
    summary: dict = {
        "input": str(input_path),
        "output_dir": str(output_dir),
        "validated_cleaned": str(cleaned_path),
        "steps": {},
    }

    if not args.skip_validate:
        process_cmd = [
            sys.executable,
            str(_PROCESS_SCRIPT),
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--workers",
            str(args.workers),
            "--coherence-threshold",
            str(args.coherence_threshold),
            "--sensitivity-threshold",
            str(args.sensitivity_threshold),
        ]
        if args.skip_sensitivity:
            process_cmd.append("--skip-sensitivity")
        if args.remove_latin_words:
            process_cmd.append("--remove-latin-words")
        else:
            process_cmd.append("--no-remove-latin-words")
        if args.sequential:
            process_cmd.append("--sequential")
        if args.llm_provider:
            process_cmd.extend(["--llm-provider", args.llm_provider])
        if args.limit is not None:
            process_cmd.extend(["--limit", str(args.limit)])
        if args.verbose:
            process_cmd.append("--verbose")

        code = _run_step("Step 1: process_validated_jsonl", process_cmd, verbose=args.verbose)
        summary["steps"]["process_validated_jsonl"] = {"exit_code": code}
        if code != 0:
            summary["status"] = "failed"
            summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
            return summary

        if not cleaned_path.is_file():
            logger.error("Expected output not found: %s", cleaned_path)
            summary["status"] = "failed"
            summary["error"] = f"missing output: {cleaned_path}"
            summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
            return summary
    elif not cleaned_path.is_file():
        logger.error("--skip-validate set but %s does not exist", cleaned_path)
        summary["status"] = "failed"
        summary["error"] = f"missing validated_cleaned: {cleaned_path}"
        summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
        return summary

    if not args.skip_filter:
        filter_cmd = [
            sys.executable,
            str(_FILTER_SCRIPT),
            "--input",
            str(cleaned_path),
            "--in-place",
            "--min-words",
            str(args.min_words),
            "--max-words",
            str(args.max_words),
            "--split-threshold",
            str(args.split_threshold),
            "--count-method",
            args.count_method,
        ]
        if args.target_words is not None:
            filter_cmd.extend(["--target-words", str(args.target_words)])
        if args.verbose:
            filter_cmd.append("--verbose")

        code = _run_step("Step 2: filter_min_words", filter_cmd, verbose=args.verbose)
        summary["steps"]["filter_min_words"] = {"exit_code": code}
        if code != 0:
            summary["status"] = "failed"
            summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
            return summary

        stats_path = output_dir / f"{cleaned_path.stem}_min_words_stats.json"
        if stats_path.is_file():
            summary["steps"]["filter_min_words"]["stats"] = json.loads(
                stats_path.read_text(encoding="utf-8")
            )

    summary["status"] = "ok"
    summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run process_validated_jsonl.py then filter_min_words.py on one JSONL file."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        type=Path,
        help="Input curated JSONL (e.g. final/gujarati/gujarati.jsonl).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process every final/*/*.jsonl file (excluding validated/sidecar outputs).",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for step 1 (default: same directory as input).",
    )

    validate = parser.add_argument_group("process_validated_jsonl options")
    validate.add_argument("--workers", type=int, default=8)
    validate.add_argument("--coherence-threshold", type=float, default=0.60)
    validate.add_argument("--sensitivity-threshold", type=float, default=0.30)
    validate.add_argument("--skip-sensitivity", action="store_true")
    validate.add_argument(
        "--remove-latin-words",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    validate.add_argument("--sequential", action="store_true")
    validate.add_argument("--llm-provider", choices=["gemini", "openai", "gpt"], default=None)
    validate.add_argument("--limit", type=int, default=None)

    filter_group = parser.add_argument_group("filter_min_words options")
    filter_group.add_argument("--min-words", type=int, default=300)
    filter_group.add_argument("--max-words", type=int, default=450)
    filter_group.add_argument(
        "--target-words",
        type=int,
        default=None,
        help="Preferred chunk size when splitting (default: same as --min-words).",
    )
    filter_group.add_argument("--split-threshold", type=int, default=1000)
    filter_group.add_argument(
        "--count-method",
        choices=["split", "tokenize"],
        default="split",
    )

    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip step 1; run filter_min_words on existing *_validated_cleaned.jsonl.",
    )
    parser.add_argument(
        "--skip-filter",
        action="store_true",
        help="Run only step 1 (process_validated_jsonl).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.skip_validate and args.skip_filter:
        logger.error("Cannot set both --skip-validate and --skip-filter")
        return 1

    inputs = discover_language_jsonl_files() if args.all else [args.input]
    if not inputs:
        logger.error("No input JSONL files found")
        return 1

    results: list[dict] = []
    failures = 0
    for input_path in inputs:
        input_path = input_path.resolve()
        if not input_path.is_file():
            logger.error("Input file not found: %s", input_path)
            failures += 1
            continue

        logger.info("Starting pipeline for %s", input_path)
        summary = run_pipeline_for_input(input_path, args)
        results.append(summary)
        if summary.get("status") != "ok":
            failures += 1
            continue
        logger.info(
            "Finished %s in %.1fs -> %s",
            input_path.name,
            summary["elapsed_seconds"],
            summary["validated_cleaned"],
        )

    if len(results) == 1:
        report_path = Path(results[0]["output_dir"]) / f"{language_stem_from_input(inputs[0])}_pipeline_summary.json"
    else:
        report_path = _FINAL_DIR / "pipeline_summary.json"

    report = {
        "inputs_processed": len(results),
        "failures": failures,
        "runs": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("Pipeline summary: %s", report_path)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
