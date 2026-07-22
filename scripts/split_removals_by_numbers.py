#!/usr/bin/env python3
"""
Split a Removal JSONL file into accepted / review / rejected scripts.

Input format (unchanged source file)::

    {"script_id": {"0": ["token", ...], "3": ["15", "سال"]}}
    {"other_script": {}}

Outputs (same directory as input by default)::

    {language}_accepted.jsonl — scripts with no edits: {"script_id": {}}
    {language}_review.jsonl   — only segments whose removals do *not* contain numbers
    {language}_rejected.jsonl — only segments whose removals contain numbers

Routing rules:
  - No removals ``{}`` → accepted only (never review/rejected).
  - Segment with any numeric removal → that segment in rejected.
  - Segment with only non-numeric removals → that segment in review.
  - A script may appear in both review and rejected when it has mixed segments;
    each file then holds only the matching non-empty segment map.

Example::

    python3 scripts/split_removals_by_numbers.py --input Removal/urdu.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


# Roman numerals as a whole token (after stripping punctuation / Urdu marks).
_ROMAN_RE = re.compile(
    r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)

# Strip common wrappers so "(1200" / "15،" / "2015ء" still classify as numbers.
_STRIP_CHARS = (
    "()[]{}<>«»\"'`.,;:?!؟،۔·•…\\/-–—_|*%$#@+=~"
    "\u200c\u200d\u200e\u200f\u061f\u060c\u06d4"  # ZW*/bidi + Arabic punct
)


def _has_decimal_digit(text: str) -> bool:
    return any(unicodedata.category(ch) == "Nd" for ch in text)


def _strip_noise(token: str) -> str:
    return token.strip().strip(_STRIP_CHARS)


def token_is_number(token: str) -> bool:
    """True if token is / contains a number in any common script form."""
    if not token or not token.strip():
        return False
    if _has_decimal_digit(token):
        return True
    core = _strip_noise(token)
    if not core:
        return False
    if _has_decimal_digit(core):
        return True
    # Standalone roman numerals (II, XIV, …); skip single ambiguous "I".
    if len(core) >= 2 and _ROMAN_RE.fullmatch(core):
        return True
    return False


def segment_has_number_removal(tokens: list[Any]) -> bool:
    return any(token_is_number(str(t)) for t in tokens)


def language_from_input(path: Path) -> str:
    return path.stem


def split_script_removals(
    removals: dict[str, list[Any]],
) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
    review: dict[str, list[Any]] = {}
    rejected: dict[str, list[Any]] = {}
    for seg_id, tokens in removals.items():
        if not isinstance(tokens, list):
            continue
        if segment_has_number_removal(tokens):
            rejected[seg_id] = tokens
        else:
            review[seg_id] = tokens
    return review, rejected


def process_file(input_path: Path, output_dir: Path, language: str) -> dict[str, int]:
    accepted_path = output_dir / f"{language}_accepted.jsonl"
    review_path = output_dir / f"{language}_review.jsonl"
    rejected_path = output_dir / f"{language}_rejected.jsonl"

    stats = {
        "scripts_total": 0,
        "scripts_accepted": 0,
        "scripts_review": 0,
        "scripts_rejected": 0,
        "scripts_mixed_review_and_rejected": 0,
        "segments_review": 0,
        "segments_rejected": 0,
    }

    with (
        input_path.open(encoding="utf-8") as fin,
        accepted_path.open("w", encoding="utf-8") as f_accepted,
        review_path.open("w", encoding="utf-8") as f_review,
        rejected_path.open("w", encoding="utf-8") as f_rejected,
    ):
        for line_no, line in enumerate(fin, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{input_path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict) or len(row) != 1:
                raise SystemExit(
                    f"{input_path}:{line_no}: expected one script key per line, got {row!r}"
                )

            script_id, removals = next(iter(row.items()))
            if not isinstance(removals, dict):
                raise SystemExit(
                    f"{input_path}:{line_no}: expected dict of segments, got {type(removals)}"
                )

            stats["scripts_total"] += 1

            # Untouched scripts → accepted only.
            if not removals:
                f_accepted.write(json.dumps({script_id: {}}, ensure_ascii=False) + "\n")
                stats["scripts_accepted"] += 1
                continue

            review, rejected = split_script_removals(removals)
            stats["segments_review"] += len(review)
            stats["segments_rejected"] += len(rejected)

            if review and rejected:
                stats["scripts_mixed_review_and_rejected"] += 1

            if review:
                f_review.write(json.dumps({script_id: review}, ensure_ascii=False) + "\n")
                stats["scripts_review"] += 1
            if rejected:
                f_rejected.write(json.dumps({script_id: rejected}, ensure_ascii=False) + "\n")
                stats["scripts_rejected"] += 1

    stats["accepted_path"] = str(accepted_path)
    stats["review_path"] = str(review_path)
    stats["rejected_path"] = str(rejected_path)
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Split Removal JSONL into {language}_accepted.jsonl (no edits), "
            "{language}_review.jsonl (non-number removals), and "
            "{language}_rejected.jsonl (number removals)."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Removal JSONL, e.g. Removal/urdu.jsonl (left unchanged).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for outputs (default: same directory as --input).",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Language stem for output names (default: input filename stem).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 1

    output_dir = (args.output_dir or input_path.parent).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    language = args.language or language_from_input(input_path)

    stats = process_file(input_path, output_dir, language)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
