#!/usr/bin/env python3
"""
Bucket / merge / split cleaned TTS JSONL scripts by word count.

Rules:
  1. Paragraphs with 300–450 words are kept unchanged.
  2. Paragraphs with <300 words are pairwise-merged with another short script
     that shares the same ``domain`` and ``source``, aiming for 300–450 words.
     If a merge exceeds 450 words, it is purged down to at most 450 words at a
     sentence boundary (context-preserving prefix).
  3. Paragraphs with 451–599 words are purged down to at most 450 words the
     same way.
  4. Paragraphs with >600 words are split into 2+ scripts of 300–450 words
     with ids ``{script_id}.1``, ``{script_id}.2``, ...
  5. Any script still under 300 words after merging is written to
     ``{lang}_short_script.jsonl``.

Outputs (default: same directory as input):
  ``{lang}_tts_merged.jsonl`` — kept / merged / split scripts in range
  ``{lang}_short_script.jsonl`` — leftover scripts with <300 words
  ``{lang}_tts_merge_stats.json`` — counts

Example:
  python3 scripts/merge_short_tts_jsonl.py \\
    --input raw_data/maithili/final_maithili_cleaned_tts_new.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TTS_MIN_WORDS = 300
TTS_MAX_WORDS = 450
TTS_SPLIT_THRESHOLD = 600
TTS_TARGET_WORDS = (TTS_MIN_WORDS + TTS_MAX_WORDS) // 2  # 375

# Terminal punctuation across the 15-language set (plus Western fallbacks).
_SENTENCE_END = re.compile(r'[.!?।॥۔؟꯫᱾]["\')\]]*(?:\s+|$)')

_STEM_SUFFIXES = (
    "_cleaned_tts_new_reviewed",
    "_cleaned_tts_new",
    "_validated_cleaned",
    "_validated",
)


def language_stem_from_input(input_path: Path) -> str:
    stem = input_path.stem
    if stem.startswith("final_"):
        stem = stem[len("final_") :]
    for suffix in _STEM_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    match = re.match(r"^(?:final_)?([a-zA-Z]+)(?:_|$)", input_path.stem)
    if match:
        return match.group(1)
    return stem


def word_count(text: str) -> int:
    return len((text or "").split())


def merge_key(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("domain") or ""), str(record.get("source") or ""))


def _sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    script_id = str(record.get("id") or "")
    parts = script_id.split(".")
    keys: list[Any] = []
    for part in parts:
        if part.isdigit():
            keys.append((0, int(part)))
        else:
            keys.append((1, part))
    return tuple(keys)


def _split_at_sentence_boundary(
    text: str,
    target_words: int,
    *,
    min_words: int,
    max_words: int,
) -> tuple[str, str]:
    """Split *text* near *target_words* on a sentence boundary when possible."""
    words = text.split()
    total = len(words)
    if total <= max_words:
        return text, ""

    lo = max(min_words, 1)
    hi = min(total - 1, max_words)
    if lo > hi:
        split_at = min(target_words, total - 1)
        return " ".join(words[:split_at]), " ".join(words[split_at:])

    best_idx: int | None = None
    best_distance = total + 1
    for idx in range(lo, hi + 1):
        prefix = " ".join(words[:idx])
        if not _SENTENCE_END.search(prefix):
            continue
        distance = abs(idx - target_words)
        if distance < best_distance:
            best_distance = distance
            best_idx = idx

    if best_idx is None:
        split_at = min(max(target_words, min_words), total - 1)
    else:
        split_at = best_idx

    return " ".join(words[:split_at]), " ".join(words[split_at:])


def purge_to_max_words(text: str, max_words: int = TTS_MAX_WORDS) -> str:
    """
    Keep a context-preserving prefix of at most *max_words*.

    Prefers ending on a sentence boundary within [min(300, max_words), max_words].
    """
    text = (text or "").strip()
    if not text:
        return ""
    wc = word_count(text)
    if wc <= max_words:
        return text

    kept, _discarded = _split_at_sentence_boundary(
        text,
        target_words=min(TTS_TARGET_WORDS, max_words),
        min_words=min(TTS_MIN_WORDS, max_words),
        max_words=max_words,
    )
    return kept.strip()


def split_long_script(text: str) -> list[str]:
    """
    Split a long paragraph into chunks of 300–450 words.

    Used for scripts with more than 600 words. Chooses the smallest number of
    chunks such that each lands near an even split within 300–450, cutting on
    sentence boundaries when possible.
    """
    text = (text or "").strip()
    if not text:
        return []

    total = word_count(text)
    if total <= TTS_MAX_WORDS:
        return [text]

    # Smallest n with total/n <= 450, and prefer total/n >= 300.
    n_chunks = max(2, (total + TTS_MAX_WORDS - 1) // TTS_MAX_WORDS)
    while n_chunks > 2 and total / n_chunks < TTS_MIN_WORDS:
        n_chunks -= 1

    chunks: list[str] = []
    remaining = text
    for index in range(n_chunks):
        left_chunks = n_chunks - index
        rem_wc = word_count(remaining)
        if left_chunks == 1:
            chunks.append(remaining.strip())
            break

        # Target size for this chunk so the remainder can still fill the rest.
        max_here = min(TTS_MAX_WORDS, rem_wc - TTS_MIN_WORDS * (left_chunks - 1))
        min_here = max(TTS_MIN_WORDS, rem_wc - TTS_MAX_WORDS * (left_chunks - 1))
        if max_here < min_here:
            max_here = min(TTS_MAX_WORDS, rem_wc - 1)
            min_here = max(1, min(TTS_MIN_WORDS, max_here))
        target = min(max_here, max(min_here, rem_wc // left_chunks))

        head, rest = _split_at_sentence_boundary(
            remaining,
            target_words=target,
            min_words=min_here,
            max_words=max_here,
        )
        if not head.strip() or not rest.strip():
            words = remaining.split()
            cut = min(max(target, min_here), max_here, len(words) - 1)
            head = " ".join(words[:cut])
            rest = " ".join(words[cut:])
        chunks.append(head.strip())
        remaining = rest.strip()
        if not remaining:
            break

    # Final clamp: every chunk must be ≤450; drop empty.
    normalized: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        if word_count(chunk) > TTS_MAX_WORDS:
            chunk = purge_to_max_words(chunk)
        normalized.append(chunk)
    return normalized


def merge_records(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge two short scripts; keep left metadata and concatenate paragraphs."""
    left_text = str(left.get("paragraph") or "").strip()
    right_text = str(right.get("paragraph") or "").strip()
    merged = dict(left)
    merged["paragraph"] = f"{left_text} {right_text}".strip()

    merged_from: list[str] = []
    for record in (left, right):
        existing = record.get("_merged_from")
        if isinstance(existing, list) and existing:
            merged_from.extend(str(x) for x in existing)
        else:
            merged_from.append(str(record.get("id") or ""))
    deduped: list[str] = []
    for item in merged_from:
        if item and (not deduped or deduped[-1] != item):
            deduped.append(item)
    merged["_merged_from"] = deduped
    merged["_merged_count"] = len(deduped)
    return merged


def _pair_score(total: int) -> tuple[int, int]:
    if TTS_MIN_WORDS <= total <= TTS_MAX_WORDS:
        return (0, abs(total - TTS_TARGET_WORDS))
    if total < TTS_MIN_WORDS:
        return (1, TTS_MIN_WORDS - total)
    # Overshoot — will be purged to 450 after merge.
    return (2, total - TTS_MAX_WORDS)


def _choose_pairs(
    pool: list[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]]]:
    """Greedily choose disjoint pairs from *pool*; return (pairs, leftovers)."""
    records = list(pool)
    used: set[int] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for i in range(len(records)):
        if i in used:
            continue
        wc_i = word_count(str(records[i].get("paragraph") or ""))
        best_j: int | None = None
        best_score: tuple[int, int] | None = None
        for j in range(i + 1, len(records)):
            if j in used:
                continue
            total = wc_i + word_count(str(records[j].get("paragraph") or ""))
            score = _pair_score(total)
            if best_score is None or score < best_score:
                best_score = score
                best_j = j
        if best_j is None:
            continue
        used.add(i)
        used.add(best_j)
        left, right = records[i], records[best_j]
        if _sort_key(right) < _sort_key(left):
            left, right = right, left
        pairs.append((left, right))

    leftovers = [record for index, record in enumerate(records) if index not in used]
    return pairs, leftovers


def merge_short_groups(
    shorts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """
    Pairwise-merge shorts that share domain+source.

    Merges aiming for 300–450; overshoots are purged to ≤450.

    Returns (promoted_in_range, remaining_lt_300, counters).
    """
    remaining = list(shorts)
    promoted: list[dict[str, Any]] = []
    counters = {
        "merge_operations": 0,
        "merges_purged_to_450": 0,
    }

    while True:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in remaining:
            groups[merge_key(record)].append(record)

        next_remaining: list[dict[str, Any]] = []
        merged_this_round = 0

        for _key, group in groups.items():
            if len(group) < 2:
                next_remaining.extend(group)
                continue

            pairs, leftovers = _choose_pairs(group)
            for left, right in pairs:
                merged = merge_records(left, right)
                counters["merge_operations"] += 1
                merged_this_round += 1
                wc = word_count(str(merged.get("paragraph") or ""))
                if wc > TTS_MAX_WORDS:
                    merged["paragraph"] = purge_to_max_words(
                        str(merged["paragraph"])
                    )
                    merged["_purged_to_max"] = TTS_MAX_WORDS
                    counters["merges_purged_to_450"] += 1
                    wc = word_count(str(merged.get("paragraph") or ""))
                if TTS_MIN_WORDS <= wc <= TTS_MAX_WORDS:
                    promoted.append(merged)
                elif wc < TTS_MIN_WORDS:
                    next_remaining.append(merged)
                else:
                    # Still somehow over max after purge — force purge again.
                    merged["paragraph"] = purge_to_max_words(
                        str(merged["paragraph"])
                    )
                    promoted.append(merged)
            next_remaining.extend(leftovers)

        remaining = next_remaining
        if merged_this_round == 0:
            break

    return promoted, remaining, counters


def expand_record_by_length(record: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Apply purge / split rules to a single non-short record.

    Returns (output_records, counters).
    """
    counters = {
        "purged_451_599": 0,
        "split_gt_600": 0,
        "split_chunks": 0,
    }
    paragraph = str(record.get("paragraph") or "")
    wc = word_count(paragraph)

    if TTS_MIN_WORDS <= wc <= TTS_MAX_WORDS:
        return [record], counters

    if wc <= TTS_SPLIT_THRESHOLD:
        # 451–600 (inclusive of 600? user said greater than 600 for split)
        # 451–600: purge to 450. Exactly 600 → purge. >600 → split.
        if wc > TTS_MAX_WORDS:
            updated = dict(record)
            updated["paragraph"] = purge_to_max_words(paragraph)
            updated["_purged_to_max"] = TTS_MAX_WORDS
            counters["purged_451_599"] += 1
            return [updated], counters
        return [record], counters

    # > 600 → split into id.1, id.2, ...
    chunks = split_long_script(paragraph)
    base_id = str(record.get("id") or "")
    counters["split_gt_600"] += 1
    counters["split_chunks"] += len(chunks)
    out: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        row = dict(record)
        row["id"] = f"{base_id}.{index}"
        row["paragraph"] = chunk
        row["_split_index"] = index
        row["_split_from"] = len(chunks)
        row["_split_parent_id"] = base_id
        out.append(row)
    return out, counters


def process_file(
    input_path: Path,
    *,
    output_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    lang = language_stem_from_input(input_path)
    main_path = output_dir / f"{lang}_tts_merged.jsonl"
    short_path = output_dir / f"{lang}_short_script.jsonl"
    stats_path = output_dir / f"{lang}_tts_merge_stats.json"

    untouched: list[dict[str, Any]] = []
    shorts: list[dict[str, Any]] = []
    long_records: list[dict[str, Any]] = []

    records_in = 0
    with input_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records_in += 1
            record = json.loads(line)
            wc = word_count(str(record.get("paragraph") or ""))
            if TTS_MIN_WORDS <= wc <= TTS_MAX_WORDS:
                untouched.append(record)
            elif wc < TTS_MIN_WORDS:
                shorts.append(record)
            else:
                long_records.append(record)

    promoted, leftover_shorts, merge_counters = merge_short_groups(shorts)

    expanded_long: list[dict[str, Any]] = []
    length_counters = {
        "purged_451_599": 0,
        "split_gt_600": 0,
        "split_chunks": 0,
    }
    for record in long_records:
        rows, counters = expand_record_by_length(record)
        for key, value in counters.items():
            length_counters[key] += value
        # After purge/split, keep only in-range rows in main; rare <300 tails → short.
        for row in rows:
            wc = word_count(str(row.get("paragraph") or ""))
            if wc < TTS_MIN_WORDS:
                leftover_shorts.append(row)
            else:
                if wc > TTS_MAX_WORDS:
                    row = dict(row)
                    row["paragraph"] = purge_to_max_words(str(row["paragraph"]))
                    row["_purged_to_max"] = TTS_MAX_WORDS
                expanded_long.append(row)

    main_records = sorted(
        untouched + promoted + expanded_long,
        key=_sort_key,
    )
    short_records = sorted(leftover_shorts, key=_sort_key)

    # Final safety: main should be 300–450 only.
    verified_main: list[dict[str, Any]] = []
    for row in main_records:
        wc = word_count(str(row.get("paragraph") or ""))
        if wc < TTS_MIN_WORDS:
            short_records.append(row)
        elif wc > TTS_MAX_WORDS:
            fixed = dict(row)
            fixed["paragraph"] = purge_to_max_words(str(row["paragraph"]))
            fixed["_purged_to_max"] = TTS_MAX_WORDS
            verified_main.append(fixed)
        else:
            verified_main.append(row)
    main_records = sorted(verified_main, key=_sort_key)
    short_records = sorted(short_records, key=_sort_key)

    summary = {
        "input": str(input_path),
        "output": str(main_path),
        "short_output": str(short_path),
        "language": lang,
        "word_range_kept": f"{TTS_MIN_WORDS}-{TTS_MAX_WORDS}",
        "split_threshold": TTS_SPLIT_THRESHOLD,
        "records_in": records_in,
        "records_untouched_in_range": len(untouched),
        "records_short_before_merge": len(shorts),
        "records_long_before_expand": len(long_records),
        **merge_counters,
        **length_counters,
        "records_promoted_by_merge": len(promoted),
        "records_short_remaining": len(short_records),
        "records_main_out": len(main_records),
    }

    if dry_run:
        logger.info("Dry run: %s", json.dumps(summary, ensure_ascii=False))
        return summary

    output_dir.mkdir(parents=True, exist_ok=True)
    main_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in main_records),
        encoding="utf-8",
    )
    short_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in short_records),
        encoding="utf-8",
    )
    stats_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    logger.info(
        "Done %s: in=%d main=%d short=%d merges=%d purged_merges=%d "
        "purged_long=%d split=%d -> %s",
        input_path.name,
        records_in,
        len(main_records),
        len(short_records),
        merge_counters["merge_operations"],
        merge_counters["merges_purged_to_450"],
        length_counters["purged_451_599"],
        length_counters["split_gt_600"],
        main_path,
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge short same-domain/source scripts into 300–450 words, purge "
            "overshoots to 450, split scripts >600 into id.1/id.2/… chunks, and "
            "write leftovers under 300 words to {lang}_short_script.jsonl."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input JSONL (e.g. final_maithili_cleaned_tts_new.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: same directory as --input).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print stats only; do not write files.",
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

    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        logger.error("Input not found: %s", input_path)
        return 1

    output_dir = (args.output_dir or input_path.parent).expanduser().resolve()
    try:
        process_file(input_path, output_dir=output_dir, dry_run=args.dry_run)
    except Exception:
        logger.exception("Failed processing %s", input_path)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
