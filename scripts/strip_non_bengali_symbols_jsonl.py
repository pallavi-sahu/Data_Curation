#!/usr/bin/env python3
"""
Strip symbols that are not part of Bengali prose from a TTS JSONL file.

Bengali letters, Bengali and ASCII digits, the danda, and ordinary
sentence punctuation stay. Currency signs, guillemets, hashtags, at-signs,
asterisks, and similar marks are removed. A slash between words becomes a space.

Example:
  python3 scripts/strip_non_bengali_symbols_jsonl.py \\
    --input new_data_batch3/bengali/final_bengali_new.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Devanagari danda is the sentence stop used in Bengali. There is no Bengali danda.
_DANDA = "\u0964\u0965"
_PUNCT = ".,;:?!%\"'“”‘’()[]-–—"
_DELETE = set("«»‹›*$£€¥¢@#*&•><=+\\_`~^|½⅓⅔¼¾")
_SLASH = re.compile(r"[/\\]+")
_SPACE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r" +([।॥.,;:?!])")


def _keep(char: str) -> bool:
    if char in _DELETE or char in "/\\":
        return False
    if char in _DANDA or char in _PUNCT or char.isspace() or char.isdigit():
        return True
    code = ord(char)
    return 0x0980 <= code <= 0x09FF


def clean_paragraph(text: str) -> tuple[str, int]:
    """Return cleaned text and the number of characters removed."""
    removed = 0
    kept: list[str] = []
    for char in text:
        if char in "/\\":
            kept.append(" ")
            removed += 1
            continue
        if char in _DELETE:
            removed += 1
            continue
        if _keep(char):
            kept.append(char)
        else:
            removed += 1
    cleaned = "".join(kept)
    cleaned = _SLASH.sub(" ", cleaned)
    cleaned = _SPACE.sub(" ", cleaned)
    cleaned = _SPACE_BEFORE_PUNCT.sub(r"\1", cleaned)
    cleaned = cleaned.strip()
    return cleaned, removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove non-Bengali symbols from JSONL paragraphs.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Default: {input_stem}_cleaned.jsonl next to the input.",
    )
    args = parser.parse_args()
    input_path = args.input.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else input_path.with_name(f"{input_path.stem}_cleaned.jsonl")
    )

    rows = 0
    changed = 0
    chars_removed = 0
    with input_path.open(encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as dest:
        for line in source:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            paragraph = str(record.get("paragraph") or "")
            cleaned, removed = clean_paragraph(paragraph)
            if cleaned != paragraph:
                changed += 1
                chars_removed += removed
                record["paragraph"] = cleaned
                record["_word_count"] = len(cleaned.split())
            dest.write(json.dumps(record, ensure_ascii=False) + "\n")
            rows += 1

    print(
        json.dumps(
            {
                "input": str(input_path),
                "output": str(output_path),
                "rows": rows,
                "rows_changed": changed,
                "characters_removed": chars_removed,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
