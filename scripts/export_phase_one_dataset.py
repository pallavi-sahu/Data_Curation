#!/usr/bin/env python3
"""
Export validated_cleaned JSONL rows to phase_one / phase_two txt files and manifests.

Phase 1 (default): first ``--max-rows`` non-empty paragraphs ->
  ``{language}_phase_one/`` and ``{language}_dataset.json``

Phase 2 (``--phase 2``): next ``--max-rows`` paragraphs after skipping phase 1 ->
  ``{language}_phase_two/`` and ``{language}_dataset_2.json``

Script filenames continue numbering from phase 1 by default (e.g. ``malayalam_script_0201.txt``
after 200 phase-one exports). Override the starting index with ``--start-script-number``.

Also writes ``final/phase_one_scripts_summary.csv`` or ``phase_two_scripts_summary.csv``.

Example:
  python3 scripts/export_phase_one_dataset.py --all --max-rows 200
  python3 scripts/export_phase_one_dataset.py --input final/bodo/bodo_validated_cleaned.jsonl
  python3 scripts/export_phase_one_dataset.py --all --phase 2 --max-rows 150
  python3 scripts/export_phase_one_dataset.py --input final/Assamese/assamese_tts_cleaned.jsonl \\
      --phase 2 --max-rows 1925 --start-script-number 201
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FINAL_DIR = _REPO_ROOT / "final"

FILENAME_TO_LANG: dict[str, str] = {
    "assamese": "as",
    "bengali": "bn",
    "bodo": "brx",
    "dogri": "doi",
    "gujarati": "gu",
    "hindi": "hi",
    "kannada": "kn",
    "kashmiri": "ks",
    "konkani": "kok",
    "malayalam": "ml",
    "marathi": "mr",
    "maithili": "mai",
    "manipuri": "mni",
    "nepali": "ne",
    "odia": "or",
    "punjabi": "pa",
    "santali": "sat",
    "sanskrit": "sa",
    "sindhi": "sd",
    "tamil": "ta",
    "telugu": "te",
    "urdu": "ur",
}

LANG_TO_LOCALE: dict[str, str] = {code: name for name, code in FILENAME_TO_LANG.items()}

_STEM_SUFFIXES = ("_tts_cleaned", "_validated_cleaned_2", "_validated_cleaned", "_validated")
_DEFAULT_PAD_WIDTH = 4
_DEFAULT_MAX_ROWS = 200


def language_stem_from_input(input_path: Path) -> str:
    stem = input_path.stem
    for suffix in _STEM_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def resolve_language_code(lang_stem: str, record: dict) -> str:
    return str(record.get("language_code") or FILENAME_TO_LANG.get(lang_stem.lower()) or lang_stem)


def resolve_locale_name(lang_stem: str, language_code: str) -> str:
    return LANG_TO_LOCALE.get(language_code) or lang_stem.lower()


def _clear_script_files(scripts_dir: Path) -> None:
    for path in scripts_dir.glob("*.txt"):
        path.unlink()


def phase_one_export_count(lang_dir: Path, language_name: str) -> int:
    """Rows already exported in phase one (manifest length, else txt count)."""
    manifest_path = lang_dir / f"{language_name}_dataset.json"
    if manifest_path.is_file():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return len(data)

    phase_one_dir = lang_dir / f"{language_name}_phase_one"
    if phase_one_dir.is_dir():
        return len(list(phase_one_dir.glob("*.txt")))

    return 0


def _phase_paths(lang_dir: Path, language_name: str, phase: int) -> tuple[Path, Path]:
    if phase == 1:
        return (
            lang_dir / f"{language_name}_phase_one",
            lang_dir / f"{language_name}_dataset.json",
        )
    if phase == 2:
        return (
            lang_dir / f"{language_name}_phase_two",
            lang_dir / f"{language_name}_dataset_2.json",
        )
    raise ValueError(f"Unsupported phase: {phase}")


def export_phase_dataset(
    input_path: Path,
    *,
    phase: int = 1,
    output_dir: Path | None = None,
    pad_width: int = _DEFAULT_PAD_WIDTH,
    max_rows: int = _DEFAULT_MAX_ROWS,
    skip_rows: int | None = None,
    start_script_number: int | None = None,
) -> dict[str, int | str]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if phase not in (1, 2):
        raise ValueError(f"phase must be 1 or 2, got {phase}")
    if max_rows < 1:
        raise ValueError("--max-rows must be at least 1")
    if start_script_number is not None and start_script_number < 1:
        raise ValueError("--start-script-number must be at least 1")

    lang_stem = language_stem_from_input(input_path)
    lang_dir = output_dir or input_path.parent
    language_name = lang_dir.name
    scripts_dir, manifest_path = _phase_paths(lang_dir, language_name, phase)

    if phase == 1:
        effective_skip = 0
    elif skip_rows is not None:
        effective_skip = skip_rows
    else:
        effective_skip = phase_one_export_count(lang_dir, language_name)
        if effective_skip == 0:
            logger.warning(
                "No phase-one export found for %s; phase 2 starts from the first row",
                language_name,
            )

    scripts_dir.mkdir(parents=True, exist_ok=True)
    _clear_script_files(scripts_dir)

    dataset: list[dict] = []
    present = 0
    skipped_empty = 0
    skipped_prior = 0
    exported = 0
    valid_seen = 0

    with input_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue

            present += 1
            record = json.loads(line)
            paragraph = str(record.get("paragraph") or "").strip()
            if not paragraph:
                skipped_empty += 1
                continue

            valid_seen += 1
            if valid_seen <= effective_skip:
                skipped_prior += 1
                continue
            if exported >= max_rows:
                continue

            exported += 1
            language_code = resolve_language_code(lang_stem, record)
            locale_name = resolve_locale_name(lang_stem, language_code)
            if start_script_number is not None:
                script_index = start_script_number + exported - 1
            else:
                script_index = effective_skip + exported
            script_name = f"{locale_name}_script_{script_index:0{pad_width}d}.txt"

            (scripts_dir / script_name).write_text(paragraph, encoding="utf-8")
            dataset.append(
                {
                    "data": {"file_name": script_name, "language": language_code},
                    "files": {"script_file": script_name},
                }
            )

    manifest_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "phase": phase,
        "language": lang_stem,
        "language_folder": language_name,
        "locale": resolve_locale_name(lang_stem, resolve_language_code(lang_stem, {})),
        "language_code": FILENAME_TO_LANG.get(lang_stem.lower(), lang_stem),
        "output_dir": str(scripts_dir),
        "present": present,
        "used": exported,
        "skipped_empty": skipped_empty,
        "skipped_prior": skipped_prior,
        "skip_rows": effective_skip,
        "start_script_number": start_script_number if start_script_number is not None else effective_skip + 1,
        "manifest": str(manifest_path),
    }


def export_phase_one(
    input_path: Path,
    *,
    output_dir: Path | None = None,
    pad_width: int = _DEFAULT_PAD_WIDTH,
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> dict[str, int | str]:
    """Backward-compatible wrapper for phase 1 export."""
    return export_phase_dataset(
        input_path,
        phase=1,
        output_dir=output_dir,
        pad_width=pad_width,
        max_rows=max_rows,
    )


def discover_validated_cleaned_files() -> list[Path]:
    return sorted(_FINAL_DIR.glob("*/*_validated_cleaned.jsonl"))


def write_summary_csv(rows: list[dict[str, int | str]], *, phase: int) -> Path:
    _FINAL_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = _FINAL_DIR / (
        "phase_one_scripts_summary.csv" if phase == 1 else "phase_two_scripts_summary.csv"
    )
    fieldnames = ["language", "present", "used", "skipped_prior", "skip_rows"]
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "language": row["locale"],
                    "present": row["present"],
                    "used": row["used"],
                    "skipped_prior": row.get("skipped_prior", 0),
                    "skip_rows": row.get("skip_rows", 0),
                }
            )
    return summary_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export validated_cleaned JSONL to phase_one/phase_two script folders "
            "and dataset manifests."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        type=Path,
        help="Input JSONL (e.g. final/bodo/bodo_validated_cleaned.jsonl).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process every final/*/*_validated_cleaned.jsonl file.",
    )
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2],
        default=1,
        help="Export phase: 1=first N rows (default), 2=next N after phase 1.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Language output directory (default: same directory as input).",
    )
    parser.add_argument(
        "--pad-width",
        type=int,
        default=_DEFAULT_PAD_WIDTH,
        help="Zero-pad width for script filenames (default: 4 -> locale_script_0001.txt).",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=_DEFAULT_MAX_ROWS,
        help="Number of datapoints to export per language (default: 200).",
    )
    parser.add_argument(
        "--skip-rows",
        type=int,
        default=None,
        help="Phase 2 only: non-empty rows to skip (default: phase-one export count).",
    )
    parser.add_argument(
        "--start-script-number",
        type=int,
        default=None,
        help=(
            "First script index in filenames (default: 1 for phase 1; phase-one count + 1 "
            "for phase 2). E.g. 201 -> assamese_script_0201.txt."
        ),
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

    if args.phase == 1 and args.skip_rows is not None:
        logger.error("--skip-rows applies only to --phase 2")
        return 1

    inputs = discover_validated_cleaned_files() if args.all else [args.input]
    if not inputs:
        logger.error("No validated_cleaned JSONL files found under %s", _FINAL_DIR)
        return 1

    summary_rows: list[dict[str, int | str]] = []
    for input_path in inputs:
        try:
            stats = export_phase_dataset(
                input_path,
                phase=args.phase,
                output_dir=args.output_dir,
                pad_width=args.pad_width,
                max_rows=args.max_rows,
                skip_rows=args.skip_rows,
                start_script_number=args.start_script_number,
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.error("%s", exc)
            return 1

        summary_rows.append(stats)
        logger.info(
            "Phase %d: exported %d / %d scripts for %s (skipped %d phase-one rows) -> %s",
            stats["phase"],
            stats["used"],
            stats["present"],
            stats["language"],
            stats["skipped_prior"],
            stats["output_dir"],
        )
        if stats["skipped_empty"]:
            logger.info("Skipped %d empty paragraphs", stats["skipped_empty"])
        logger.info("Manifest: %s", stats["manifest"])

    summary_path = write_summary_csv(summary_rows, phase=args.phase)
    logger.info("Summary CSV: %s", summary_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
