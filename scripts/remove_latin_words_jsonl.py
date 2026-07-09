#!/usr/bin/env python3
"""
Remove pure Latin-script words from validated JSONL paragraphs (no LLM calls).

Use this on ``*_validated_cleaned.jsonl`` files that were processed before
``--remove-latin-words`` was added to ``process_validated_jsonl.py``.

Example:
  python3 scripts/remove_latin_words_jsonl.py --input final/gujarati/gujarati_validated_cleaned.jsonl --in-place
  python3 scripts/remove_latin_words_jsonl.py --all --in-place
  python3 scripts/remove_latin_words_jsonl.py --input final/tamil/tamil_validated_cleaned.jsonl --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FINAL_DIR = _REPO_ROOT / "final"

logger = logging.getLogger(__name__)


def _load_remove_latin_words() -> Callable[[str], tuple[str, int]]:
    """Import ``remove_latin_words`` without pulling in curation PDF / LLM deps."""
    sys.path.insert(0, str(_REPO_ROOT))
    sys.modules.setdefault("curation", types.ModuleType("curation"))
    sys.modules.setdefault("curation.processing", types.ModuleType("curation.processing"))

    for module_name, rel_path in (
        ("curation.config", "curation/config.py"),
        ("curation.processing.script", "curation/processing/script.py"),
    ):
        path = _REPO_ROOT / rel_path
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    tokenization_path = _REPO_ROOT / "validation/tokenization.py"
    spec = importlib.util.spec_from_file_location("validation.tokenization", tokenization_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {tokenization_path}")
    tokenization = importlib.util.module_from_spec(spec)
    sys.modules["validation.tokenization"] = tokenization
    spec.loader.exec_module(tokenization)
    return tokenization.remove_latin_words


def discover_validated_cleaned_files() -> list[Path]:
    paths: list[Path] = []
    for path in sorted(_FINAL_DIR.glob("*/*_validated_cleaned.jsonl")):
        paths.append(path)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove pure Latin-script words from validated JSONL paragraphs.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        type=Path,
        help="Input JSONL (e.g. final/gujarati/gujarati_validated_cleaned.jsonl).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process every final/*/*_validated_cleaned.jsonl file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: overwrite input with --in-place).",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite --input with cleaned rows.",
    )
    parser.add_argument(
        "--min-words",
        type=int,
        default=None,
        help=(
            "After Latin removal, move rows with fewer than N words to the "
            "removed sidecar (default: keep all non-empty rows)."
        ),
    )
    parser.add_argument(
        "--drop-empty",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Drop rows whose paragraph is empty after Latin removal (default: enabled).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report stats only; do not write output files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N records (for testing).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def process_file(
    input_path: Path,
    *,
    remove_latin_words: Callable[[str], tuple[str, int]],
    output_path: Path | None,
    in_place: bool,
    min_words: int | None,
    drop_empty: bool,
    dry_run: bool,
    limit: int | None,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    if in_place and output_path is not None:
        raise ValueError("Cannot use --output together with --in-place")
    if not in_place and output_path is None and not dry_run:
        output_path = input_path.with_name(f"{input_path.stem}_no_latin.jsonl")

    kept_path = input_path if in_place else output_path
    write_kept_path = (
        input_path.with_suffix(f"{input_path.suffix}.tmp") if in_place and not dry_run else kept_path
    )
    removed_path = input_path.with_name(f"{input_path.stem}_removed_latin.jsonl")

    records_in = 0
    records_changed = 0
    records_kept = 0
    records_removed_empty = 0
    records_removed_short = 0
    latin_words_removed = 0

    kept_rows: list[dict[str, Any]] = []
    removed_rows: list[dict[str, Any]] = []

    with input_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if limit is not None and records_in >= limit:
                break
            line = line.strip()
            if not line:
                continue

            records_in += 1
            record = json.loads(line)
            paragraph = str(record.get("paragraph") or "")
            cleaned, removed_count = remove_latin_words(paragraph)
            if removed_count:
                records_changed += 1
                latin_words_removed += removed_count

            updated = {**record, "paragraph": cleaned}
            word_count = len(cleaned.split())

            if drop_empty and not cleaned.strip():
                records_removed_empty += 1
                removed_rows.append(
                    {
                        **updated,
                        "_latin_words_removed": removed_count,
                        "_removal_reason": "empty_after_latin_removal",
                        "_input_line_number": line_number,
                    }
                )
                continue

            if min_words is not None and word_count < min_words:
                records_removed_short += 1
                removed_rows.append(
                    {
                        **updated,
                        "_latin_words_removed": removed_count,
                        "_word_count": word_count,
                        "_min_words_required": min_words,
                        "_removal_reason": "below_min_words",
                        "_input_line_number": line_number,
                    }
                )
                continue

            records_kept += 1
            kept_rows.append(updated)

    stats: dict[str, Any] = {
        "input": str(input_path),
        "output_kept": str(kept_path) if kept_path else None,
        "output_removed": str(removed_path),
        "in_place": in_place,
        "dry_run": dry_run,
        "drop_empty": drop_empty,
        "min_words": min_words,
        "records_in": records_in,
        "records_changed": records_changed,
        "records_kept": records_kept,
        "records_removed_empty": records_removed_empty,
        "records_removed_short": records_removed_short,
        "latin_words_removed": latin_words_removed,
    }

    if dry_run:
        logger.info(
            "Dry run %s: %d/%d rows changed, %d Latin tokens removed, would keep %d rows",
            input_path.name,
            records_changed,
            records_in,
            latin_words_removed,
            records_kept,
        )
        return stats

    assert write_kept_path is not None
    write_kept_path.parent.mkdir(parents=True, exist_ok=True)
    with write_kept_path.open("w", encoding="utf-8") as handle:
        for row in kept_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    with removed_path.open("w", encoding="utf-8") as handle:
        for row in removed_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    if in_place:
        write_kept_path.replace(input_path)

    stats_path = input_path.with_name(f"{input_path.stem}_latin_removal_stats.json")
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stats["stats_path"] = str(stats_path)

    logger.info(
        "Processed %s: kept %d rows, removed %d empty / %d short, %d Latin tokens stripped",
        input_path.name,
        records_kept,
        records_removed_empty,
        records_removed_short,
        latin_words_removed,
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.in_place and args.output is None and not args.dry_run:
        logger.error("Specify --in-place, --output, or --dry-run")
        return 1

    if args.min_words is not None and args.min_words < 1:
        logger.error("--min-words must be at least 1")
        return 1

    remove_latin_words = _load_remove_latin_words()
    inputs = discover_validated_cleaned_files() if args.all else [args.input.resolve()]

    if not inputs:
        logger.error("No input files found")
        return 1

    failures = 0
    for input_path in inputs:
        if not input_path.is_file():
            logger.error("Input file not found: %s", input_path)
            failures += 1
            continue
        try:
            process_file(
                input_path,
                remove_latin_words=remove_latin_words,
                output_path=args.output.resolve() if args.output else None,
                in_place=args.in_place,
                min_words=args.min_words,
                drop_empty=args.drop_empty,
                dry_run=args.dry_run,
                limit=args.limit,
            )
        except Exception:
            logger.exception("Failed to process %s", input_path)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
