#!/usr/bin/env python3
"""
Drop JSONL rows whose ``paragraph`` has fewer than ``--min-words`` words.

Paragraph sizing:
  - <= ``--max-words`` (default 800): kept as-is when above ``--min-words``
  - ``--max-words`` + 1 … ``--split-threshold`` (default 1000): pruned to
    ``--max-words`` at sentence boundaries
  - > ``--split-threshold``: split into multiple chunks aiming for
    ``--target-words`` (default: same as ``--min-words``), capped at
    ``--max-words`` at sentence boundaries

Before sizing, consecutive rows from the same ``source`` are consolidated into
one paragraph (exact duplicate paragraphs within a source are kept once). Exact
duplicate output paragraphs can be dropped with ``--dedupe-paragraphs``.

Decorative runs of 3+ dots (e.g. ``. . .``), 3+ dashes (e.g. ``---``), or 3+
angle brackets (e.g. ``>>>``, ``<<<``) are stripped from each paragraph before
word counting; rows are not dropped solely for containing those patterns.

Uses whitespace word count by default (``paragraph.split()``), matching curation
``--min-words`` and manual checks like ``len(paragraph.split())``. Pass
``--count-method tokenize`` for Indic-aware token boundaries instead.

Output (default: same directory as input):
  ``{stem}_min{N}w.jsonl``     — kept rows (paragraph >= min words)
  ``{stem}_removed_short.jsonl`` — dropped rows with ``_word_count`` metadata
  ``{stem}_min_words_stats.json`` — counts

Use ``--in-place`` to overwrite ``--input`` with kept rows instead.

Example:
  python scripts/filter_min_words.py --input final/urdu/urdu_validated_cleaned.jsonl --in-place
  python scripts/filter_min_words.py --input final/gujarati.jsonl --min-words 500 --output-dir validated_scripts
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.cleaning import remove_decorative_dot_dash_runs
from validation.tokenization import split_sentences

logger = logging.getLogger(__name__)

_DEFAULT_MAX_WORDS = 800
_DEFAULT_SPLIT_THRESHOLD = 1000

TOKEN_PATTERN = re.compile(
    r"[^\s\u0964\u0965\u2026\u00A0\u200B\u200C\u200D\uFEFF\.,;:!?\(\)\[\]{}\"'«»—–\-/\\|@#$%^&*+=<>~`]+"
)


def tokenize(text: str) -> list[str]:
    """Split text into word-like tokens (works across Indic scripts)."""
    return [t for t in TOKEN_PATTERN.findall(text or "") if t.strip()]


def paragraph_word_count(text: str, *, method: str = "split") -> int:
    """Count words in a paragraph.

    ``split`` (default) matches curation ``--min-words`` and whitespace checks.
    ``tokenize`` uses Indic-aware boundaries (validation ``words`` metric).
    """
    if method == "tokenize":
        return len(tokenize(text or ""))
    return len((text or "").split())


def _truncate_words(text: str, max_words: int, *, method: str) -> str:
    if method == "tokenize":
        return " ".join(tokenize(text)[:max_words])
    return " ".join((text or "").split()[:max_words])


def prune_paragraph_to_max_words(
    text: str,
    *,
    max_words: int,
    method: str = "split",
    language_code: str = "",
) -> tuple[str, bool]:
    """Trim long paragraphs to ``max_words`` at sentence boundaries when possible."""
    if max_words <= 0:
        return text, False

    if paragraph_word_count(text, method=method) <= max_words:
        return text, False

    sentences = split_sentences(text, language_code)
    if not sentences:
        return _truncate_words(text, max_words, method=method), True

    kept: list[str] = []
    kept_count = 0
    for sentence in sentences:
        sentence_count = paragraph_word_count(sentence, method=method)
        if kept_count + sentence_count <= max_words:
            kept.append(sentence)
            kept_count += sentence_count
            continue
        if not kept:
            return _truncate_words(sentence, max_words, method=method), True
        break

    if not kept:
        return _truncate_words(text, max_words, method=method), True
    return " ".join(kept), True


def _finalize_chunks(
    chunks: list[str],
    *,
    min_words: int,
    max_words: int,
    method: str,
    language_code: str = "",
) -> list[str]:
    if not chunks:
        return []

    finalized: list[str] = []
    for chunk in chunks:
        if paragraph_word_count(chunk, method=method) > max_words:
            chunk, _ = prune_paragraph_to_max_words(
                chunk,
                max_words=max_words,
                method=method,
                language_code=language_code,
            )
        finalized.append(chunk)

    if len(finalized) > 1 and paragraph_word_count(finalized[-1], method=method) < min_words:
        merged = f"{finalized[-2]} {finalized[-1]}".strip()
        if paragraph_word_count(merged, method=method) > max_words:
            merged, _ = prune_paragraph_to_max_words(
                merged,
                max_words=max_words,
                method=method,
                language_code=language_code,
            )
        finalized = finalized[:-2] + [merged]

    return finalized


def split_paragraph_into_chunks(
    text: str,
    *,
    min_words: int,
    max_words: int,
    target_words: int,
    method: str = "split",
    language_code: str = "",
) -> list[str]:
    """Split a long paragraph into chunks at sentence boundaries.

    Chunks aim for ``target_words`` (default: ``min_words``) and may grow up to
    ``max_words`` when needed for sentence boundaries or oversized sentences.
    """
    sentences = split_sentences(text, language_code)
    if not sentences:
        words = (text or "").split() if method == "split" else tokenize(text)
        chunks: list[str] = []
        index = 0
        while index < len(words):
            end = min(index + target_words, len(words))
            chunk_words = words[index:end]
            if len(chunk_words) < min_words and chunks:
                chunks[-1] = " ".join((chunks[-1].split() if method == "split" else tokenize(chunks[-1])) + chunk_words)
                break
            chunks.append(" ".join(chunk_words))
            index = end
        return _finalize_chunks(
            chunks,
            min_words=min_words,
            max_words=max_words,
            method=method,
            language_code=language_code,
        )

    chunks = []
    bucket: list[str] = []
    bucket_count = 0

    def flush_bucket() -> None:
        nonlocal bucket, bucket_count
        if bucket:
            chunks.append(" ".join(bucket))
            bucket = []
            bucket_count = 0

    for sentence in sentences:
        sentence_count = paragraph_word_count(sentence, method=method)
        if sentence_count > max_words:
            flush_bucket()
            truncated, _ = prune_paragraph_to_max_words(
                sentence,
                max_words=max_words,
                method=method,
                language_code=language_code,
            )
            chunks.append(truncated)
            continue

        if bucket_count + sentence_count > max_words:
            if bucket_count >= min_words:
                flush_bucket()
                bucket = [sentence]
                bucket_count = sentence_count
            else:
                bucket.append(sentence)
                bucket_count += sentence_count
                if bucket_count >= min_words:
                    flush_bucket()
            continue

        bucket.append(sentence)
        bucket_count += sentence_count
        if bucket_count >= min_words and bucket_count >= target_words:
            flush_bucket()

    if bucket:
        if bucket_count >= min_words or not chunks:
            chunks.append(" ".join(bucket))
        else:
            chunks[-1] = f"{chunks[-1]} {' '.join(bucket)}".strip()

    return _finalize_chunks(
        chunks,
        min_words=min_words,
        max_words=max_words,
        method=method,
        language_code=language_code,
    )


_CHUNK_FIELDS = ("_chunk_index", "_chunk_total")


def _strip_chunk_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in _CHUNK_FIELDS}


def consolidate_records_by_source(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive rows from the same source into one paragraph.

    Exact duplicate paragraphs within a source are kept once. This matches
    curation ``pack_records`` output where one article appears as several
    sequential fragments that should be sized/split as a single unit.
    """
    if not records:
        return []

    consolidated: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []

    def flush() -> None:
        if not buffer:
            return
        seen: set[str] = set()
        parts: list[str] = []
        for record in buffer:
            paragraph = str(record.get("paragraph") or "").strip()
            if not paragraph or paragraph in seen:
                continue
            seen.add(paragraph)
            parts.append(paragraph)

        if not parts:
            buffer.clear()
            return

        base = _strip_chunk_metadata(buffer[0])
        consolidated.append({**base, "paragraph": " ".join(parts)})
        buffer.clear()

    for record in records:
        source = str(record.get("source") or "")
        if buffer and source != str(buffer[0].get("source") or ""):
            flush()
        buffer.append(record)

    flush()
    return consolidated


def reconstruct_records_by_source(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge all rows sharing a source into one record (for re-chunking prior output)."""
    if not records:
        return []

    by_source: dict[str, list[dict[str, Any]]] = {}
    source_order: list[str] = []
    for record in records:
        source = str(record.get("source") or "")
        if source not in by_source:
            by_source[source] = []
            source_order.append(source)
        by_source[source].append(record)

    reconstructed: list[dict[str, Any]] = []
    for source in source_order:
        group = by_source[source]
        group.sort(
            key=lambda row: (
                row.get("_chunk_index") is None,
                int(row.get("_chunk_index") or 0),
            )
        )
        seen: set[str] = set()
        parts: list[str] = []
        for record in group:
            paragraph = str(record.get("paragraph") or "").strip()
            if not paragraph or paragraph in seen:
                continue
            seen.add(paragraph)
            parts.append(paragraph)
        if not parts:
            continue
        base = _strip_chunk_metadata(group[0])
        reconstructed.append({**base, "paragraph": " ".join(parts)})
    return reconstructed


def dedupe_records_by_paragraph(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop rows whose paragraph text was already written (keep first occurrence)."""
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        paragraph = str(record.get("paragraph") or "")
        if paragraph in seen:
            continue
        seen.add(paragraph)
        deduped.append(record)
    return deduped


def process_paragraph_chunks(
    text: str,
    *,
    min_words: int,
    max_words: int,
    target_words: int,
    split_threshold: int,
    method: str = "split",
    language_code: str = "",
) -> tuple[list[str], str]:
    """Return paragraph chunk(s) and an action label: kept, pruned, or split."""
    count = paragraph_word_count(text, method=method)
    if count <= max_words:
        return [text], "kept"
    if count <= split_threshold:
        pruned, _ = prune_paragraph_to_max_words(
            text,
            max_words=max_words,
            method=method,
            language_code=language_code,
        )
        return [pruned], "pruned"
    return split_paragraph_into_chunks(
        text,
        min_words=min_words,
        max_words=max_words,
        target_words=target_words,
        method=method,
        language_code=language_code,
    ), "split"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Keep JSONL rows with at least N words in paragraph; drop shorter rows.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input JSONL (same schema as final/*.jsonl: source, paragraph, language_code, …).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for kept rows (overrides --output-dir naming).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output files (default: same directory as input).",
    )
    parser.add_argument(
        "--min-words",
        type=int,
        default=500,
        help="Minimum words required to keep a row (default: 500).",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=_DEFAULT_MAX_WORDS,
        help=(
            "Hard maximum words per paragraph chunk "
            f"(default: {_DEFAULT_MAX_WORDS}; set 0 to disable sizing)."
        ),
    )
    parser.add_argument(
        "--target-words",
        type=int,
        default=None,
        help=(
            "Preferred chunk size when splitting long paragraphs "
            "(default: same as --min-words). Chunks flush near this size, "
            "not at --max-words."
        ),
    )
    parser.add_argument(
        "--split-threshold",
        type=int,
        default=_DEFAULT_SPLIT_THRESHOLD,
        help=(
            "Split paragraphs above this into multiple chunks; between max-words and "
            f"this threshold they are only pruned (default: {_DEFAULT_SPLIT_THRESHOLD})."
        ),
    )
    parser.add_argument(
        "--count-method",
        choices=["split", "tokenize"],
        default="split",
        help="Word counting: split=whitespace (default, matches curation); tokenize=Indic-aware.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite --input with kept (pruned) rows instead of writing a separate output file.",
    )
    parser.add_argument(
        "--rechunk",
        action="store_true",
        help=(
            "Merge all rows by source first (restores prior split/chunk output), "
            "then re-apply sizing with --target-words."
        ),
    )
    parser.add_argument(
        "--consolidate-by-source",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Merge consecutive rows from the same source before sizing/splitting "
            "(default: enabled; skipped when input already has _chunk_index metadata)."
        ),
    )
    parser.add_argument(
        "--dedupe-paragraphs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Drop exact duplicate paragraph text at the end (default: enabled).",
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

    input_path: Path = args.input
    if not input_path.is_file():
        logger.error("Input file not found: %s", input_path)
        return 1

    if args.min_words < 1:
        logger.error("--min-words must be at least 1")
        return 1

    if args.max_words < 0:
        logger.error("--max-words must be 0 or greater")
        return 1

    if args.max_words > 0 and args.split_threshold < args.max_words:
        logger.error("--split-threshold must be >= --max-words")
        return 1

    min_w = args.min_words
    target_w = args.target_words if args.target_words is not None else min_w
    if target_w < min_w:
        logger.error("--target-words must be >= --min-words")
        return 1
    if args.max_words > 0 and target_w > args.max_words:
        logger.error("--target-words must be <= --max-words")
        return 1

    if args.in_place and args.output:
        logger.error("Cannot use --output together with --in-place")
        return 1

    output_dir = args.output_dir or input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    kept_path = input_path if args.in_place else (args.output or output_dir / f"{input_path.stem}_min{min_w}w.jsonl")
    write_kept_path = input_path.with_suffix(f"{input_path.suffix}.tmp") if args.in_place else kept_path
    removed_path = output_dir / f"{input_path.stem}_removed_short.jsonl"
    stats_path = output_dir / f"{input_path.stem}_min_words_stats.json"

    records_in = 0
    records_consolidated = 0
    records_kept = 0
    records_removed = 0
    records_pruned = 0
    records_split = 0
    records_deduplicated = 0
    output_rows = 0
    word_counts_removed: list[int] = []
    pruned_word_counts_before: list[int] = []
    pruned_word_counts_after: list[int] = []

    input_records: list[dict[str, Any]] = []
    with input_path.open(encoding="utf-8") as handle_in:
        for line in handle_in:
            line = line.strip()
            if not line:
                continue
            records_in += 1
            input_records.append(json.loads(line))

    already_chunked = any(record.get("_chunk_index") is not None for record in input_records)
    working_records = input_records
    if args.rechunk:
        working_records = reconstruct_records_by_source(input_records)
        records_consolidated = len(input_records) - len(working_records)
        logger.info(
            "Reconstructed %d input rows into %d source rows for re-chunking",
            len(input_records),
            len(working_records),
        )
        already_chunked = False
    elif args.consolidate_by_source and not already_chunked:
        working_records = consolidate_records_by_source(input_records)
        records_consolidated = len(input_records) - len(working_records)
        if records_consolidated:
            logger.info(
                "Consolidated %d input rows into %d source rows",
                len(input_records),
                len(working_records),
            )
    elif args.consolidate_by_source and already_chunked:
        logger.info("Skipping consolidate-by-source: input already has _chunk_index metadata")

    kept_records: list[dict[str, Any]] = []

    with write_kept_path.open("w", encoding="utf-8") as handle_kept, removed_path.open(
        "w", encoding="utf-8"
    ) as handle_removed:
        for line_number, record in enumerate(working_records, start=1):
            paragraph = str(record.get("paragraph") or "")
            paragraph, _ = remove_decorative_dot_dash_runs(paragraph)
            original_count = paragraph_word_count(paragraph, method=args.count_method)

            if args.max_words <= 0:
                chunks, action = [paragraph], "kept"
            else:
                chunks, action = process_paragraph_chunks(
                    paragraph,
                    min_words=min_w,
                    max_words=args.max_words,
                    target_words=target_w,
                    split_threshold=args.split_threshold,
                    method=args.count_method,
                    language_code=str(record.get("language_code") or ""),
                )

            if action == "pruned":
                records_pruned += 1
                pruned_word_counts_before.append(original_count)
                pruned_word_counts_after.append(
                    paragraph_word_count(chunks[0], method=args.count_method)
                )
            elif action == "split":
                records_split += 1

            for chunk_index, chunk in enumerate(chunks, start=1):
                count = paragraph_word_count(chunk, method=args.count_method)
                chunk_record = {**_strip_chunk_metadata(record), "paragraph": chunk}
                if action == "split" and len(chunks) > 1:
                    chunk_record["_chunk_index"] = chunk_index
                    chunk_record["_chunk_total"] = len(chunks)

                if count >= min_w:
                    kept_records.append(chunk_record)
                else:
                    records_removed += 1
                    word_counts_removed.append(count)
                    removed = {
                        **chunk_record,
                        "_word_count": count,
                        "_input_line_number": line_number,
                        "_min_words_required": min_w,
                    }
                    handle_removed.write(json.dumps(removed, ensure_ascii=False) + "\n")

        if args.dedupe_paragraphs:
            before_dedupe = len(kept_records)
            kept_records = dedupe_records_by_paragraph(kept_records)
            records_deduplicated = before_dedupe - len(kept_records)
            if records_deduplicated:
                logger.info("Removed %d exact duplicate paragraphs", records_deduplicated)

        for chunk_record in kept_records:
            handle_kept.write(json.dumps(chunk_record, ensure_ascii=False) + "\n")
            records_kept += 1
            output_rows += 1

    if args.in_place:
        write_kept_path.replace(input_path)

    stats = {
        "input": str(input_path),
        "output_kept": str(kept_path),
        "in_place": args.in_place,
        "output_removed": str(removed_path),
        "min_words": min_w,
        "max_words": args.max_words,
        "target_words": target_w,
        "split_threshold": args.split_threshold,
        "count_method": args.count_method,
        "records_in": records_in,
        "records_consolidated": records_consolidated,
        "records_kept": records_kept,
        "records_removed": records_removed,
        "records_pruned": records_pruned,
        "records_split": records_split,
        "records_deduplicated": records_deduplicated,
        "output_rows": output_rows,
        "consolidate_by_source": args.consolidate_by_source and not already_chunked,
        "rechunk": args.rechunk,
        "dedupe_paragraphs": args.dedupe_paragraphs,
        "keep_rate": round(records_kept / records_in, 4) if records_in else 0.0,
        "removed_word_count_min": min(word_counts_removed) if word_counts_removed else None,
        "removed_word_count_max": max(word_counts_removed) if word_counts_removed else None,
        "pruned_word_count_before_min": (
            min(pruned_word_counts_before) if pruned_word_counts_before else None
        ),
        "pruned_word_count_before_max": (
            max(pruned_word_counts_before) if pruned_word_counts_before else None
        ),
        "pruned_word_count_after_max": (
            max(pruned_word_counts_after) if pruned_word_counts_after else None
        ),
    }

    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)

    logger.info(
        "Kept %d output rows from %d input rows (>= %d words) -> %s",
        output_rows,
        records_in,
        min_w,
        kept_path,
    )
    if records_pruned:
        logger.info(
            "Pruned %d rows to <= %d words (sentence boundaries)",
            records_pruned,
            args.max_words,
        )
    if records_split:
        logger.info(
            "Split %d rows above %d words into %d-word chunks",
            records_split,
            args.split_threshold,
            args.max_words,
        )
    if records_removed:
        logger.info("Removed %d short rows -> %s", records_removed, removed_path)
    logger.info("Stats: %s", stats_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
