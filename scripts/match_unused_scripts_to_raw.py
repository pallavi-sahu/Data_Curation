#!/usr/bin/env python3
"""
Match unused script files back to unique raw JSONL paragraphs.

Inputs:
  1. TXT list of script filenames (one per line), e.g.
     ``unused_scripts/Assamese_expired_unused_scripts.txt``
  2. Folder containing those ``.txt`` script files
  3. Raw JSONL with a ``paragraph`` field

Outputs (under ``--output-dir``, default: same dir as the list file):
  - ``{stem}_matched_raw.jsonl`` — unique matched raw records (one paragraph once)
  - ``{stem}_match_report.json`` — summary match stats only

Matching order (same idea as ``find_tts_cleanup_removals.py``):
  1. Exact normalized paragraph == script text
  2. Prefix-index substring (script chunk inside a raw paragraph)
  3. Bounded fuzzy (token-overlap shortlist, then SequenceMatcher ratio)

Example:
  python3 scripts/match_unused_scripts_to_raw.py \\
    --list unused_scripts/Assamese_expired_unused_scripts.txt \\
    --scripts-dir "Apollo TTS dataset/assamese_phase_3" \\
    --raw raw_data/assamese_validated_cleaned.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX_LEN = 48
_PREFIX_STEP = 24
_FUZZY_TOP_K = 12
_FUZZY_EDGE_TOKENS = 24
_FUZZY_MIN_TOKEN_HITS = 3
_FUZZY_DEFAULT_CUTOFF = 0.88


def _normalize(text: str) -> str:
    return " ".join((text or "").split())


def _edge_tokens(norm: str, n: int = _FUZZY_EDGE_TOKENS) -> set[str]:
    tokens = norm.split()
    if not tokens:
        return set()
    return {t for t in tokens[:n] + tokens[-n:] if len(t) >= 2}


def load_filename_list(path: Path) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            name = line.strip()
            if not name or name.startswith("#"):
                continue
            # Allow either bare names or accidental paths.
            name = Path(name).name
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names


def find_script_file(scripts_dir: Path, filename: str) -> Path | None:
    direct = scripts_dir / filename
    if direct.is_file():
        return direct
    # Recursive fallback if files live in nested folders.
    matches = list(scripts_dir.rglob(filename))
    if not matches:
        return None
    return matches[0]


def load_raw_paragraphs(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            paragraph = str(record.get("paragraph") or "")
            rows.append(
                {
                    "line_number": line_no,
                    "record": record,
                    "paragraph": paragraph,
                    "norm": _normalize(paragraph),
                    "n_words": len(paragraph.split()),
                }
            )
    return rows


def build_raw_indexes(
    rows: list[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    by_norm: dict[str, list[dict[str, Any]]] = {}
    by_prefix: dict[str, list[dict[str, Any]]] = {}
    by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        norm = row["norm"]
        if not norm:
            continue
        by_norm.setdefault(norm, []).append(row)
        seen_keys: set[str] = set()
        limit = max(1, len(norm) - _PREFIX_LEN + 1)
        for i in range(0, limit, _PREFIX_STEP):
            key = norm[i : i + _PREFIX_LEN]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            by_prefix.setdefault(key, []).append(row)
        start_key = norm[:_PREFIX_LEN]
        if start_key not in seen_keys:
            by_prefix.setdefault(start_key, []).append(row)
        for token in _edge_tokens(norm):
            by_token[token].append(row)
    return by_norm, by_prefix, dict(by_token)


def match_raw_paragraph(
    script: str,
    by_norm: dict[str, list[dict[str, Any]]],
    by_prefix: dict[str, list[dict[str, Any]]],
    by_token: dict[str, list[dict[str, Any]]],
    *,
    fuzzy: bool = True,
    fuzzy_cutoff: float = _FUZZY_DEFAULT_CUTOFF,
) -> tuple[dict[str, Any] | None, str, float]:
    """Return (row, method, score). score is 1.0 for exact/substring."""
    script_norm = _normalize(script)
    if not script_norm:
        return None, "empty_script", 0.0

    exact = by_norm.get(script_norm)
    if exact:
        return exact[0], "exact", 1.0

    probe = script_norm[:_PREFIX_LEN]
    candidates = by_prefix.get(probe, [])
    substring_hits: list[tuple[int, dict[str, Any]]] = []
    seen_ids: set[int] = set()
    for row in candidates:
        row_id = id(row)
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        if script in row["paragraph"] or script_norm in row["norm"]:
            substring_hits.append((len(row["paragraph"]), row))
    if substring_hits:
        substring_hits.sort(key=lambda item: item[0])
        return substring_hits[0][1], "substring", 1.0

    if not fuzzy:
        return None, "no_match", 0.0

    script_words = len(script_norm.split())
    script_toks = _edge_tokens(script_norm)
    overlap: Counter[int] = Counter()
    id_to_row: dict[int, dict[str, Any]] = {}
    posting_budget = 5000
    for token in script_toks:
        postings = by_token.get(token, ())
        if len(postings) > 200:
            continue
        for row in postings:
            posting_budget -= 1
            if posting_budget <= 0:
                break
            n_words = row["n_words"]
            if n_words == 0:
                continue
            len_ratio = min(script_words, n_words) / max(script_words, n_words)
            if len_ratio < 0.5:
                continue
            rid = id(row)
            overlap[rid] += 1
            id_to_row[rid] = row
        if posting_budget <= 0:
            break

    if not overlap:
        return None, "no_match", 0.0

    shortlist = [
        id_to_row[rid]
        for rid, hits in overlap.most_common(_FUZZY_TOP_K)
        if hits >= _FUZZY_MIN_TOKEN_HITS
    ]
    if not shortlist:
        return None, "no_match", 0.0

    best_row: dict[str, Any] | None = None
    best_ratio = 0.0
    a = script_norm[:500]
    for row in shortlist:
        b = row["norm"][:500]
        ratio = SequenceMatcher(None, a, b).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_row = row
    if best_row is not None and best_ratio >= fuzzy_cutoff:
        return best_row, "fuzzy", best_ratio
    return None, "no_match", best_ratio


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match unused script files to unique raw JSONL paragraphs."
    )
    parser.add_argument(
        "--list",
        required=True,
        type=Path,
        help="TXT file listing script filenames (one per line).",
    )
    parser.add_argument(
        "--scripts-dir",
        required=True,
        type=Path,
        help="Folder containing the listed .txt script files.",
    )
    parser.add_argument(
        "--raw",
        required=True,
        type=Path,
        help="Raw JSONL file with paragraph fields.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: same directory as --list).",
    )
    parser.add_argument(
        "--fuzzy-cutoff",
        type=float,
        default=_FUZZY_DEFAULT_CUTOFF,
        help=f"Minimum SequenceMatcher ratio for fuzzy match (default: {_FUZZY_DEFAULT_CUTOFF}).",
    )
    parser.add_argument(
        "--no-fuzzy",
        action="store_true",
        help="Disable fuzzy matching (exact + substring only).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    list_path: Path = args.list.resolve()
    scripts_dir: Path = args.scripts_dir.resolve()
    raw_path: Path = args.raw.resolve()
    output_dir: Path = (
        args.output_dir.resolve() if args.output_dir else list_path.parent
    )

    if not list_path.is_file():
        logger.error("List file not found: %s", list_path)
        return 1
    if not scripts_dir.is_dir():
        logger.error("Scripts folder not found: %s", scripts_dir)
        return 1
    if not raw_path.is_file():
        logger.error("Raw JSONL not found: %s", raw_path)
        return 1

    filenames = load_filename_list(list_path)
    logger.info("Loaded %d unique filenames from %s", len(filenames), list_path)

    logger.info("Indexing raw paragraphs from %s …", raw_path)
    raw_rows = load_raw_paragraphs(raw_path)
    by_norm, by_prefix, by_token = build_raw_indexes(raw_rows)
    logger.info("Indexed %d raw paragraphs", len(raw_rows))

    matched_raw_by_norm: dict[str, dict[str, Any]] = {}

    counts = Counter()
    method_counts = Counter()

    for filename in filenames:
        counts["listed"] += 1
        script_path = find_script_file(scripts_dir, filename)
        if script_path is None:
            counts["missing_file"] += 1
            continue

        try:
            script_text = script_path.read_text(encoding="utf-8").strip()
        except OSError:
            counts["read_error"] += 1
            continue

        if not script_text:
            counts["empty_script"] += 1
            continue

        row, method, score = match_raw_paragraph(
            script_text,
            by_norm,
            by_prefix,
            by_token,
            fuzzy=not args.no_fuzzy,
            fuzzy_cutoff=args.fuzzy_cutoff,
        )

        if row is None:
            counts["unmatched"] += 1
            continue

        counts["matched"] += 1
        method_counts[method] += 1
        norm = row["norm"]
        if norm not in matched_raw_by_norm:
            counts["unique_raw_kept"] += 1
            out_record = dict(row["record"])
            out_record["_matched_from_files"] = [filename]
            out_record["_raw_line_number"] = row["line_number"]
            out_record["_match_method"] = method
            out_record["_match_score"] = round(score, 4)
            matched_raw_by_norm[norm] = out_record
        else:
            counts["duplicate_raw_skipped"] += 1
            matched_raw_by_norm[norm].setdefault("_matched_from_files", []).append(
                filename
            )

    summary = {
        "type": "summary",
        "list_file": str(list_path),
        "scripts_dir": str(scripts_dir),
        "raw_file": str(raw_path),
        "listed": counts["listed"],
        "missing_file": counts["missing_file"],
        "read_error": counts["read_error"],
        "empty_script": counts["empty_script"],
        "matched": counts["matched"],
        "unmatched": counts["unmatched"],
        "unique_raw_kept": counts["unique_raw_kept"],
        "duplicate_raw_skipped": counts["duplicate_raw_skipped"],
        "match_methods": dict(method_counts),
        "did_not_match_total": (
            counts["missing_file"]
            + counts["read_error"]
            + counts["empty_script"]
            + counts["unmatched"]
        ),
    }

    stem = list_path.stem
    matched_path = output_dir / f"{stem}_matched_raw.jsonl"
    report_path = output_dir / f"{stem}_match_report.json"

    write_jsonl(matched_path, list(matched_raw_by_norm.values()))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    logger.info("Wrote %d unique raw scripts → %s", len(matched_raw_by_norm), matched_path)
    logger.info("Wrote report → %s", report_path)
    logger.info(
        "Summary: listed=%d matched=%d unmatched=%d missing_file=%d unique_raw=%d",
        summary["listed"],
        summary["matched"],
        summary["unmatched"],
        summary["missing_file"],
        summary["unique_raw_kept"],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
