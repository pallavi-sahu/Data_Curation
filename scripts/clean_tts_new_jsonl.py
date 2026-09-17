#!/usr/bin/env python3
"""
Index, remove noise, and flag review-worthy sentences from
``*_validated_cleaned.jsonl`` rows.

This is a variant of ``scripts/clean_tts_nsw_jsonl.py``. It never overwrites the
original inputs and always writes brand new files. It splits the
``validation_regex.txt`` catalogue into two groups:

  REMOVAL group  — stripped out of the paragraph text (junk / IDs / markup):
      citation brackets, page/chapter/lesson IDs, figure/table refs,
      ISBN/ISSN/DOI, IP addresses, hex/color codes, math operators,
      chemical/scientific notation, hashtags/@mentions, copyright/trademark,
      author-year citations, "See also"/References blocks, list/table markup,
      bank/card numbers, PIN/postal codes, license plates, file paths,
      HTML entities, kaomoji/ASCII emoticons.

  CHECK group    — kept in the text but flagged for review (numeric / measured):
      lat/long & DMS, temperature, measurement units, timezones, clock times,
      durations, roman numerals, footnote markers, dates, percentages,
      currency, long numeric statistics, decimals, ratios, version/model
      numbers, ordinals.

Outputs (derived from the language stem of the input):
  1. ``{lang}_cleaned_tts_new.jsonl`` — every kept script with an ``id``
       (indexed 1, 2, 3, ...).
  2. ``{lang}_cleaned_tts_new_check.csv`` — one row per script that still
       contains at least one CHECK pattern. Columns: id, script, sentence1,
       sentence2, ... (only the sentences that contain a CHECK pattern).
  3. ``{lang}_cleaned_tts_new_dropped.jsonl`` — rows dropped (empty after
       removal, or list-heavy when --drop-list-heavy is set).
  4. ``{lang}_cleaned_tts_new_stats.json`` — aggregate counts.

Example:
  python3 scripts/clean_tts_new_jsonl.py --input raw_data/maithili_validated_cleaned.jsonl
  python3 scripts/clean_tts_new_jsonl.py --all
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import unicodedata
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
PATTERNS = _tts.PATTERNS
_BLOCK = _tts._BLOCK
BOX_PLACEHOLDER = _tts.BOX_PLACEHOLDER
ftfy = _tts.ftfy
_strip_wiki_markup = _tts._strip_wiki_markup
_strip_parens = _tts._strip_parens
_cosmetic_cleanup = _tts._cosmetic_cleanup
_normalize_whitespace = _tts._normalize_whitespace
paragraph_word_count = _tts.paragraph_word_count
is_list_heavy_paragraph = _tts.is_list_heavy_paragraph

from validation.config import FILENAME_TO_LANG
from validation.tokenization import split_sentences as split_sentences

logger = logging.getLogger(__name__)

_FINAL_DIR = _REPO_ROOT / "final"
_STEM_SUFFIXES = (
    "_validated_cleaned_tts",
    "_validated_cleaned_2",
    "_validated_cleaned",
    "_validated",
)

# --------------------------------------------------------------------------- #
# Pattern classification (edit here to move a label between groups).
# Labels correspond to the registry in validation/tts_cleanup.py.
# --------------------------------------------------------------------------- #
REMOVAL_LABELS = {
    "isbn_issn_doi",
    "ip_address",
    "hex_color",
    "file_paths",
    "html_entities",
    "scientific_notation",
    "chemical_formula",
    "subscript_superscript",
    "bank_card_numbers",
    "page_chapter",
    "fig_table_ref",
    "citation_brackets",
    "pin_postal",
    "license_plate",
    "author_year_citation",
    "hashtag_mention",
    "copyright_trademark",
    "math_operators",
    "kaomoji_ascii",
    "list_bullets_inline",
    "pipe_char",
    "backtick",
}

CHECK_LABELS = {
    "latlon_block",
    "dms_notation",
    "temperature",
    "measurement_units",
    "timezone",
    "clock_time",
    "duration",
    "roman_numerals",
    "footnote_markers",
    "dates",
    "percentage",
    "currency",
    "numeric_commas",
    "ratio_scale",
    "version_model",
    "ordinals_english",
    "ordinals_indic",
    "decimals",
    "bare_integers",
    "degree_symbol",
}

REMOVAL_PATTERNS = [(label, pat) for label, pat in PATTERNS if label in REMOVAL_LABELS]
CHECK_PATTERNS = [(label, pat) for label, pat in PATTERNS if label in CHECK_LABELS]

def language_stem_from_input(input_path: Path) -> str:
    stem = input_path.stem
    for suffix in _STEM_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def discover_validated_cleaned_files() -> list[Path]:
    return sorted(_FINAL_DIR.glob("*/*_validated_cleaned.jsonl"))


def apply_removals(text: str, counts: dict[str, int] | None = None) -> str:
    """Strip only the REMOVAL-group patterns; keep CHECK-group content intact."""
    if ftfy is not None:
        text = ftfy.fix_text(text)

    text = (
        text.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
    )
    text = BOX_PLACEHOLDER.sub(" ", text)
    text = unicodedata.normalize("NFC", text)
    text = _strip_wiki_markup(text)
    text = _strip_parens(text)

    for blk in _BLOCK:
        matches = blk.findall(text)
        if matches and counts is not None:
            counts["block_patterns"] = counts.get("block_patterns", 0) + len(matches)
        text = blk.sub(" ", text)

    for label, pattern in REMOVAL_PATTERNS:
        matches = pattern.findall(text)
        if matches and counts is not None:
            counts[label] = counts.get(label, 0) + len(matches)
        text = pattern.sub(" ", text)

    text = _cosmetic_cleanup(text)
    text = _normalize_whitespace(text)
    return text


def check_labels_in_text(text: str) -> list[str]:
    return [label for label, pat in CHECK_PATTERNS if pat.search(text)]


def flagged_sentences(text: str, language_code: str = "") -> list[str]:
    """Return the sentences that contain at least one CHECK-group pattern."""
    return [s for s in split_sentences(text, language_code) if check_labels_in_text(s)]


def process_file(
    input_path: Path,
    *,
    output_dir: Path,
    drop_list_heavy: bool,
    list_heavy_min_markers: int,
    limit: int | None,
    dry_run: bool,
) -> dict:
    lang = language_stem_from_input(input_path)
    output_path = output_dir / f"{lang}_cleaned_tts_new.jsonl"
    csv_path = output_dir / f"{lang}_cleaned_tts_new_check.csv"
    dropped_path = output_dir / f"{lang}_cleaned_tts_new_dropped.jsonl"
    stats_path = output_dir / f"{lang}_cleaned_tts_new_stats.json"

    kept_records: list[dict] = []
    dropped_records: list[dict] = []
    removal_counts: dict[str, int] = {}

    records_in = 0
    script_index = 0

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

            if drop_list_heavy and is_list_heavy_paragraph(
                paragraph, min_markers=list_heavy_min_markers
            ):
                dropped = dict(record)
                dropped["_drop_reason"] = "list_heavy"
                dropped["_input_line_number"] = line_number
                dropped_records.append(dropped)
                continue

            cleaned = apply_removals(paragraph, removal_counts)
            if not cleaned.strip():
                dropped = dict(record)
                dropped["_drop_reason"] = "empty_after_removal"
                dropped["_input_line_number"] = line_number
                dropped_records.append(dropped)
                continue

            script_index += 1
            row = {"id": str(script_index)}
            row.update(record)
            row["paragraph"] = cleaned
            row["_word_count"] = paragraph_word_count(cleaned)
            row["_input_line_number"] = line_number
            kept_records.append(row)

    # Build the CHECK csv rows from the kept (removal-cleaned) scripts.
    csv_rows: list[tuple[str, str, list[str]]] = []
    max_sentences = 0
    for row in kept_records:
        script_text = str(row["paragraph"])
        language_code = str(row.get("language_code") or FILENAME_TO_LANG.get(lang.lower()) or "")
        sentences = flagged_sentences(script_text, language_code)
        if not sentences:
            continue
        max_sentences = max(max_sentences, len(sentences))
        csv_rows.append((row["id"], script_text, sentences))

    summary = {
        "input": str(input_path),
        "output": str(output_path),
        "check_csv": str(csv_path),
        "dropped_output": str(dropped_path),
        "records_in": records_in,
        "scripts_indexed": script_index,
        "records_dropped": len(dropped_records),
        "check_rows": len(csv_rows),
        "max_check_sentences": max_sentences,
        "removals_by_label": dict(sorted(removal_counts.items())),
        "total_removals": sum(removal_counts.values()),
    }

    if dry_run:
        logger.info("Dry run for %s: %s", input_path, json.dumps(summary, ensure_ascii=False))
        return summary

    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept_records),
        encoding="utf-8",
    )
    dropped_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in dropped_records),
        encoding="utf-8",
    )

    header = ["id", "script"] + [f"sentence{i}" for i in range(1, max_sentences + 1)]
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        for script_id, script_text, sentences in csv_rows:
            padded = sentences + [""] * (max_sentences - len(sentences))
            writer.writerow([script_id, script_text, *padded])

    stats_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    logger.info(
        "Done %s: %d scripts, %d dropped, %d check rows (max %d sentences) -> %s",
        input_path.name,
        script_index,
        len(dropped_records),
        len(csv_rows),
        max_sentences,
        output_path,
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Index scripts, remove noise patterns, and flag CHECK-pattern "
            "sentences into a CSV. Always writes new files; inputs untouched."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        type=Path,
        help="Input JSONL (e.g. raw_data/maithili_validated_cleaned.jsonl).",
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
        "--drop-list-heavy",
        action="store_true",
        help="Drop list/table-heavy paragraphs instead of keeping them.",
    )
    parser.add_argument(
        "--list-heavy-min-markers",
        type=int,
        default=5,
        help="Marker threshold for list-heavy detection (default: 5).",
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
                drop_list_heavy=args.drop_list_heavy,
                list_heavy_min_markers=args.list_heavy_min_markers,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        except Exception:
            logger.exception("Failed processing %s", input_path)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
