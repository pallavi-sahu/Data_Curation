#!/usr/bin/env python3
"""
Strip TTS-hostile patterns from ``*_validated_cleaned.jsonl`` rows.

Patterns follow validation_regex.txt (coordinates, citations, numbers, units,
times, etc.). Implemented in ``validation/tts_cleanup.py``.

Outputs:
  ``{stem}_tts_cleaned.jsonl`` — chunks with 300–450 words
  ``{stem}_tts_cleaned_short.jsonl`` — chunks with fewer than 300 words
  ``{stem}_tts_cleaned_overflow.jsonl`` — remainder tails from 451–599 splits
  ``{stem}_tts_cleaned_dropped.jsonl`` — dropped rows with ``_tts_drop_reason``
  ``{stem}_tts_cleanup_stats.json`` — aggregate removal counts

Example:
  python3 scripts/clean_tts_nsw_jsonl.py --input final/tamil/tamil_validated_cleaned.jsonl
  python3 scripts/clean_tts_nsw_jsonl.py --input final/tamil/tamil_validated_cleaned.jsonl --in-place
  python3 scripts/clean_tts_nsw_jsonl.py --all --in-place
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import importlib.util


def _load_tts_cleanup():
    path = _REPO_ROOT / "validation" / "tts_cleanup.py"
    spec = importlib.util.spec_from_file_location("tts_cleanup", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tts_cleanup"] = module
    spec.loader.exec_module(module)
    return module


_tts = _load_tts_cleanup()
TTSCleanupStats = _tts.TTSCleanupStats
TTSChunk = _tts.TTSChunk
TTS_MAX_WORDS = _tts.TTS_MAX_WORDS
TTS_MIN_WORDS = _tts.TTS_MIN_WORDS
clean_tts_record = _tts.clean_tts_record
paragraph_word_count = _tts.paragraph_word_count
split_paragraph_for_tts = _tts.split_paragraph_for_tts

_FINAL_DIR = _REPO_ROOT / "final"
_STEM_SUFFIXES = ("_validated_cleaned_tts", "_validated_cleaned_2", "_validated_cleaned", "_validated")

logger = logging.getLogger(__name__)


def language_stem_from_input(input_path: Path) -> str:
    stem = input_path.stem
    for suffix in _STEM_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def discover_validated_cleaned_files() -> list[Path]:
    paths: list[Path] = []
    for path in sorted(_FINAL_DIR.glob("*/*_validated_cleaned.jsonl")):
        paths.append(path)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove TTS-hostile regex patterns from validated_cleaned JSONL paragraphs "
            "and split long paragraphs into 300–450 word chunks."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        type=Path,
        help="Input JSONL (e.g. final/tamil/tamil_validated_cleaned.jsonl).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process every final/*/*_validated_cleaned.jsonl file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: same directory as input).",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite --input with kept rows instead of writing *_tts_cleaned.jsonl.",
    )
    parser.add_argument(
        "--keep-list-heavy",
        action="store_true",
        help="Keep list/table-heavy paragraphs instead of dropping them.",
    )
    parser.add_argument(
        "--list-heavy-min-markers",
        type=int,
        default=5,
        help="Drop paragraphs with at least this many list/table markers (default: 5).",
    )
    parser.add_argument(
        "--no-split",
        action="store_true",
        help="Skip paragraph splitting; only apply NSW cleanup.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N records (for testing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print stats only; do not write output files.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def _route_chunk(
    record: dict,
    *,
    line_number: int,
    chunk_index: int,
    split_from: int,
) -> dict:
    paragraph = str(record.get("paragraph") or "")
    word_count = paragraph_word_count(paragraph)
    row = {
        **record,
        "paragraph": paragraph,
        "_word_count": word_count,
        "_input_line_number": line_number,
    }
    if split_from > 1:
        row["_split_index"] = chunk_index
        row["_split_from"] = split_from
    return row


def process_file(
    input_path: Path,
    *,
    output_dir: Path,
    in_place: bool,
    drop_list_heavy: bool,
    list_heavy_min_markers: int,
    split_paragraphs: bool,
    limit: int | None,
    dry_run: bool,
) -> dict:
    lang_stem = language_stem_from_input(input_path)
    if in_place:
        output_path = input_path
    else:
        output_path = output_dir / f"{lang_stem}_tts_cleaned.jsonl"
    short_path = output_dir / f"{lang_stem}_tts_cleaned_short.jsonl"
    overflow_path = output_dir / f"{lang_stem}_tts_cleaned_overflow.jsonl"
    dropped_path = output_dir / f"{lang_stem}_tts_cleaned_dropped.jsonl"
    stats_path = output_dir / f"{lang_stem}_tts_cleanup_stats.json"

    stats = TTSCleanupStats()
    kept_records: list[dict] = []
    short_records: list[dict] = []
    overflow_records: list[dict] = []
    dropped_records: list[dict] = []

    with input_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if limit is not None and stats.records_in >= limit:
                break
            line = line.strip()
            if not line:
                continue

            stats.records_in += 1
            record = json.loads(line)
            cleaned, meta = clean_tts_record(
                record,
                stats=stats,
                drop_list_heavy=drop_list_heavy,
                list_heavy_min_markers=list_heavy_min_markers,
            )

            if cleaned is None:
                dropped = dict(record)
                dropped["_tts_drop_reason"] = meta["drop_reason"]
                dropped["_input_line_number"] = line_number
                dropped_records.append(dropped)
                continue

            paragraph = str(cleaned.get("paragraph") or "")
            if split_paragraphs:
                chunks = split_paragraph_for_tts(paragraph)
                if len(chunks) > 1:
                    stats.records_split += 1
            else:
                chunks = [TTSChunk(paragraph)]

            for chunk_index, chunk in enumerate(chunks, start=1):
                chunk_record = dict(cleaned)
                chunk_record["paragraph"] = chunk.text
                row = _route_chunk(
                    chunk_record,
                    line_number=line_number,
                    chunk_index=chunk_index,
                    split_from=len(chunks),
                )
                word_count = row["_word_count"]

                if chunk.is_overflow:
                    overflow_records.append(row)
                    stats.records_overflow += 1
                    continue

                if word_count < TTS_MIN_WORDS:
                    short_records.append(row)
                elif word_count <= TTS_MAX_WORDS:
                    kept_records.append(row)
                else:
                    # No sentence boundary in range — keep long chunk in main output.
                    kept_records.append(row)

            stats.records_out += 1

    summary = {
        "input": str(input_path),
        "output": str(output_path),
        "short_output": str(short_path),
        "overflow_output": str(overflow_path),
        "word_range_kept": f"{TTS_MIN_WORDS}-{TTS_MAX_WORDS}",
        "records_kept": len(kept_records),
        "records_short": len(short_records),
        "records_overflow": len(overflow_records),
        "dropped_output": str(dropped_path),
        **stats.to_dict(),
    }

    if dry_run:
        logger.info("Dry run for %s: %s", input_path, json.dumps(summary, ensure_ascii=False))
        return summary

    if not in_place or dropped_records:
        dropped_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in dropped_records),
            encoding="utf-8",
        )

    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept_records),
        encoding="utf-8",
    )
    short_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in short_records),
        encoding="utf-8",
    )
    overflow_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in overflow_records),
        encoding="utf-8",
    )
    stats_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    logger.info(
        "Done %s: %d kept (%d-%dw), %d short (<%dw), %d overflow, %d dropped "
        "(empty=%d, list_heavy=%d, split=%d) -> %s",
        input_path.name,
        len(kept_records),
        TTS_MIN_WORDS,
        TTS_MAX_WORDS,
        len(short_records),
        TTS_MIN_WORDS,
        len(overflow_records),
        stats.records_in - stats.records_out,
        stats.records_dropped_empty,
        stats.records_dropped_list_heavy,
        stats.records_split,
        output_path,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    inputs = discover_validated_cleaned_files() if args.all else [args.input]
    if not inputs:
        logger.error("No input JSONL files found")
        return 1

    failures = 0
    for input_path in inputs:
        input_path = input_path.resolve()
        if not input_path.is_file():
            logger.error("Input file not found: %s", input_path)
            failures += 1
            continue

        output_dir = args.output_dir or input_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            process_file(
                input_path,
                output_dir=output_dir,
                in_place=args.in_place,
                drop_list_heavy=not args.keep_list_heavy,
                list_heavy_min_markers=args.list_heavy_min_markers,
                split_paragraphs=not args.no_split,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        except Exception:
            logger.exception("Failed processing %s", input_path)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
