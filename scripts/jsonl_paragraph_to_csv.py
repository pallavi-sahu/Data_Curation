#!/usr/bin/env python3
"""
Export JSONL ``paragraph`` fields to CSV with a ``word_count`` column.

Word count uses whitespace splitting (``paragraph.split()``), matching other
TTS curation scripts.

Example:
  python3 scripts/jsonl_paragraph_to_csv.py \\
    --input raw_data/santali/santali_tts_merged.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def word_count(text: str) -> int:
    return len((text or "").split())


def jsonl_to_csv(input_path: Path, output_path: Path) -> int:
    written = 0
    with (
        input_path.open("r", encoding="utf-8") as fin,
        output_path.open("w", encoding="utf-8", newline="") as fout,
    ):
        writer = csv.DictWriter(fout, fieldnames=["paragraph", "word_count"])
        writer.writeheader()
        for line_no, line in enumerate(fin, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"Skipping line {line_no}: invalid JSON ({exc})", file=sys.stderr)
                continue
            paragraph = row.get("paragraph", "")
            if not isinstance(paragraph, str):
                paragraph = "" if paragraph is None else str(paragraph)
            writer.writerow(
                {
                    "paragraph": paragraph,
                    "word_count": word_count(paragraph),
                }
            )
            written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a CSV of paragraph + word_count from a JSONL file."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        required=True,
        help="Input JSONL file with a paragraph field.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output CSV path (default: {input_stem}_paragraphs.csv next to input).",
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.is_file():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = (
        args.output.resolve()
        if args.output is not None
        else input_path.with_name(f"{input_path.stem}_paragraphs.csv")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n = jsonl_to_csv(input_path, output_path)
    print(f"Wrote {n} rows to {output_path}")


if __name__ == "__main__":
    main()
