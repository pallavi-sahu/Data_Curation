#!/usr/bin/env python3
"""
Apply Gemini review CSV edits onto ``*_cleaned_tts_new.jsonl`` scripts.

CSV columns (header names are matched flexibly):
  Language
  script number/ID
  Segments/scripts
  verdict
  why verdict
  character/number category
  if the numerics, numbers are contextual or not
  cleaned segment (retain)

Matching / edit rules:
  1. Match CSV ``script number/ID`` to JSONL ``id``.
  2. Locate CSV ``Segments/scripts`` inside that script's ``paragraph``.
  3. If verdict is ``DELETE`` or ``Review`` → remove that sentence/segment.
  4. Otherwise → replace it with ``cleaned segment (retain)``.

The output JSONL keeps the same fields as the input JSONL; only ``paragraph``
is updated. Scripts with no matching CSV rows are copied unchanged.

Example:
  python3 scripts/apply_gemini_review_jsonl.py \\
    --csv raw_data/santali/santali_gemini_review.csv \\
    --jsonl raw_data/santali/santali_cleaned_tts_new.jsonl \\
    --output raw_data/santali/santali_cleaned_tts_new_reviewed.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MULTISPACE = re.compile(r" {2,}")
_WHITESPACE = re.compile(r"\s+")

# Canonical CSV field → accepted header aliases (normalized).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "language": ("language", "lang"),
    "script_id": (
        "script number/id",
        "script number",
        "script id",
        "script_id",
        "id",
        "scriptnumber/id",
        "scriptnumber",
    ),
    "segment": (
        "segments/scripts",
        "segments",
        "scripts",
        "segment",
        "sentence",
        "segmentscripts",
    ),
    "verdict": ("verdict",),
    "why_verdict": ("why verdict", "why_verdict", "reason"),
    "category": (
        "character/number category",
        "character number category",
        "category",
        "characternumbercategory",
    ),
    "contextual": (
        "if the numerics, numbers are contextual or not",
        "if the numerics numbers are contextual or not",
        "contextual",
        "is contextual",
    ),
    "cleaned": (
        "cleaned segment (retain)",
        "cleaned segment",
        "cleaned_segment",
        "cleanedsegmentretain",
        "retain",
    ),
}


def _normalize_header(value: str) -> str:
    text = (value or "").strip().lower().replace("\ufeff", "")
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def _compact_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_header(value))


def resolve_columns(fieldnames: list[str] | None) -> dict[str, str]:
    """Map canonical names → actual CSV header names."""
    if not fieldnames:
        raise ValueError("CSV has no header row")

    by_norm = {_normalize_header(name): name for name in fieldnames}
    by_compact = {_compact_header(name): name for name in fieldnames}
    resolved: dict[str, str] = {}

    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in by_norm:
                resolved[canonical] = by_norm[alias]
                break
            compact = _compact_header(alias)
            if compact in by_compact:
                resolved[canonical] = by_compact[compact]
                break

    missing = [key for key in ("script_id", "segment", "verdict") if key not in resolved]
    if missing:
        raise ValueError(
            "CSV is missing required columns "
            f"{missing}; found headers={list(fieldnames)}"
        )
    return resolved


def normalize_ws(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def find_segment_span(paragraph: str, segment: str) -> tuple[int, int] | None:
    """Return (start, end) of *segment* inside *paragraph*, if found."""
    if not paragraph or not segment:
        return None

    exact = paragraph.find(segment)
    if exact >= 0:
        return exact, exact + len(segment)

    segment_norm = normalize_ws(segment)
    if not segment_norm:
        return None

    # Walk paragraph with a sliding window over whitespace-normalized prefixes.
    # Build a map from normalized prefix length → original end index.
    para_chars: list[str] = []
    origin_ends: list[int] = []  # origin_ends[i] = end index in paragraph after i norm chars
    last_was_space = False
    for index, char in enumerate(paragraph):
        if char.isspace():
            if para_chars and not last_was_space:
                para_chars.append(" ")
                origin_ends.append(index + 1)
                last_was_space = True
            continue
        if last_was_space and para_chars and para_chars[-1] == " ":
            # keep single space already appended
            pass
        para_chars.append(char)
        origin_ends.append(index + 1)
        last_was_space = False

    para_norm = "".join(para_chars).strip()
    # Rebuild origin map after strip of leading/trailing spaces in norm string.
    # Simpler fallback: search normalized strings, then locate via regex of tokens.
    pos = para_norm.find(segment_norm)
    if pos < 0:
        return None

    # Token-based locate in original text for robustness.
    tokens = segment_norm.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(token) for token in tokens)
    match = re.search(pattern, paragraph)
    if not match:
        return None
    return match.start(), match.end()


def apply_edits_to_paragraph(
    paragraph: str,
    edits: list[dict[str, str]],
) -> tuple[str, dict[str, int]]:
    """
    Apply DELETE / replace edits to one paragraph.

    Edits are applied from rightmost match to leftmost so earlier offsets stay valid.
    """
    stats = {
        "applied_delete": 0,
        "applied_replace": 0,
        "segment_not_found": 0,
        "empty_cleaned_skipped": 0,
    }

    located: list[tuple[int, int, dict[str, str]]] = []
    for edit in edits:
        span = find_segment_span(paragraph, edit["segment"])
        if span is None:
            stats["segment_not_found"] += 1
            logger.warning(
                "Segment not found for id=%s verdict=%s segment=%r",
                edit.get("script_id"),
                edit.get("verdict"),
                edit.get("segment", "")[:80],
            )
            continue
        located.append((span[0], span[1], edit))

    # Prefer longer matches first when spans collide; then apply right-to-left.
    located.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, dict[str, str]]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, edit in located:
        if any(start < occ_end and end > occ_start for occ_start, occ_end in occupied):
            stats["segment_not_found"] += 1
            logger.warning(
                "Overlapping segment skipped for id=%s segment=%r",
                edit.get("script_id"),
                edit.get("segment", "")[:80],
            )
            continue
        occupied.append((start, end))
        selected.append((start, end, edit))

    selected.sort(key=lambda item: item[0], reverse=True)
    text = paragraph
    for start, end, edit in selected:
        verdict = edit["verdict"]
        if verdict == "DELETE" or verdict == "Review":
            replacement = ""
            stats["applied_delete"] += 1
        else:
            cleaned = edit.get("cleaned", "")
            if not cleaned.strip():
                stats["empty_cleaned_skipped"] += 1
                logger.warning(
                    "Empty cleaned segment for id=%s; leaving original text",
                    edit.get("script_id"),
                )
                continue
            replacement = cleaned
            stats["applied_replace"] += 1
        text = text[:start] + replacement + text[end:]

    text = _MULTISPACE.sub(" ", text)
    text = re.sub(r"\s+([।॥\.!?۔؟꯫᱾])", r"\1", text)
    text = text.strip()
    return text, stats


def load_review_csv(path: Path) -> dict[str, list[dict[str, str]]]:
    """Return script_id → list of review edits."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = resolve_columns(reader.fieldnames)
        by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row_number, row in enumerate(reader, start=2):
            script_id = str(row.get(columns["script_id"]) or "").strip()
            segment = str(row.get(columns["segment"]) or "").strip()
            verdict = str(row.get(columns["verdict"]) or "").strip().upper()
            cleaned = ""
            if "cleaned" in columns:
                cleaned = str(row.get(columns["cleaned"]) or "").strip()

            if not script_id or not segment or not verdict:
                logger.warning(
                    "%s:%d skipping incomplete row id=%r verdict=%r",
                    path.name,
                    row_number,
                    script_id,
                    verdict,
                )
                continue

            by_id[script_id].append(
                {
                    "script_id": script_id,
                    "segment": segment,
                    "verdict": verdict,
                    "cleaned": cleaned,
                    "csv_row": str(row_number),
                }
            )
    return dict(by_id)


def process(
    csv_path: Path,
    jsonl_path: Path,
    output_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    reviews = load_review_csv(csv_path)
    records_in = 0
    records_edited = 0
    records_unchanged = 0
    records_missing_id = 0
    aggregate = {
        "applied_delete": 0,
        "applied_replace": 0,
        "segment_not_found": 0,
        "empty_cleaned_skipped": 0,
    }
    output_rows: list[dict[str, Any]] = []

    with jsonl_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            records_in += 1
            record = json.loads(line)
            script_id = str(record.get("id") or "").strip()
            paragraph = str(record.get("paragraph") or "")

            edits = reviews.get(script_id)
            if not edits:
                records_unchanged += 1
                output_rows.append(record)
                continue

            new_paragraph, stats = apply_edits_to_paragraph(paragraph, edits)
            for key, value in stats.items():
                aggregate[key] += value

            updated = dict(record)
            updated["paragraph"] = new_paragraph
            if new_paragraph != paragraph:
                records_edited += 1
            else:
                records_unchanged += 1
            output_rows.append(updated)

    reviewed_ids = set(reviews)
    jsonl_ids = {str(row.get("id") or "").strip() for row in output_rows}
    orphan_csv_ids = sorted(
        reviewed_ids - jsonl_ids,
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
    )
    records_missing_id = len(orphan_csv_ids)
    if orphan_csv_ids:
        logger.warning(
            "%d CSV script ids not found in JSONL: %s",
            records_missing_id,
            orphan_csv_ids,
        )
        missing_ids_path = output_path.with_name(
            output_path.stem + "_missing_ids.txt"
        )
        if not dry_run:
            missing_ids_path.write_text(
                "\n".join(orphan_csv_ids) + ("\n" if orphan_csv_ids else ""),
                encoding="utf-8",
            )
            logger.warning("Wrote missing id list to %s", missing_ids_path)

    summary = {
        "csv": str(csv_path),
        "jsonl": str(jsonl_path),
        "output": str(output_path),
        "records_in": records_in,
        "records_edited": records_edited,
        "records_unchanged": records_unchanged,
        "csv_script_ids": len(reviews),
        "csv_rows": sum(len(v) for v in reviews.values()),
        "csv_ids_missing_in_jsonl": records_missing_id,
        "csv_ids_missing_in_jsonl_list": orphan_csv_ids,
        **aggregate,
    }

    if dry_run:
        logger.info("Dry run: %s", json.dumps(summary, ensure_ascii=False))
        return summary

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output_rows),
        encoding="utf-8",
    )
    stats_path = output_path.with_name(output_path.stem + "_stats.json")
    stats_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "Wrote %s (%d records, %d edited, delete=%d replace=%d missing_segment=%d)",
        output_path,
        records_in,
        records_edited,
        aggregate["applied_delete"],
        aggregate["applied_replace"],
        aggregate["segment_not_found"],
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply Gemini review CSV (DELETE / cleaned-segment replace) onto a "
            "cleaned TTS JSONL file."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="Gemini review CSV with script id, segment, verdict, cleaned segment.",
    )
    parser.add_argument(
        "--jsonl",
        type=Path,
        required=True,
        help="Input JSONL (e.g. santali_cleaned_tts_new.jsonl).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output JSONL path (default: "
            "{jsonl_stem}_reviewed.jsonl next to the input JSONL)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute stats only; do not write output.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    csv_path = args.csv.expanduser().resolve()
    jsonl_path = args.jsonl.expanduser().resolve()
    if not csv_path.is_file():
        logger.error("CSV not found: %s", csv_path)
        return 1
    if not jsonl_path.is_file():
        logger.error("JSONL not found: %s", jsonl_path)
        return 1

    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else jsonl_path.with_name(f"{jsonl_path.stem}_reviewed.jsonl")
    )

    try:
        process(csv_path, jsonl_path, output_path, dry_run=args.dry_run)
    except Exception:
        logger.exception("Failed applying Gemini review")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
