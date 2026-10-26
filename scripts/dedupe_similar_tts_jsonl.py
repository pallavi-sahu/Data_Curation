#!/usr/bin/env python3
"""
Drop duplicate TTS paragraphs that came in from more than one source.

This is the JSONL form of ``scripts/tts_script_similarity_check.py`` (that
file flags a Google Sheet from Colab). Run this after the native-vocabulary
LLM filter, before the review CSV.

Rules, matching the sheet script:

- Normalize to NFC, strip format characters, and ignore punctuation.
- Exact normalized copies are duplicates.
- Otherwise a pair must clear a TF-IDF cosine of 0.70, then word Jaccard
  decides: >= 0.85 duplicate, >= 0.60 near duplicate.
- Walk the file in order. The first copy is kept. A later copy is removed
  when it matches any copy already kept.

Embeddings are not used. In the sheet script they only fill a score column;
they do not decide keep versus remove.

Outputs (same directory as the input, unless ``--output-dir`` is set):

  ``{stem}_unique.jsonl``      — kept paragraphs, unchanged
  ``{stem}_duplicates.jsonl``  — removed copies, with the kept id and scores

Example:
  python3 scripts/dedupe_similar_tts_jsonl.py \\
    --input new_data_batch3/sanskrit/sanskrit_tts_merged_native.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer

logger = logging.getLogger(__name__)

_MIN_WORDS = 10
_TFIDF_MIN = 0.70
_JACCARD_DUP = 0.85
_JACCARD_NEAR = 0.60


def clean_whitespace(text: str) -> str:
    """NFC, drop format characters, collapse whitespace. Same as the sheet script."""
    text = unicodedata.normalize("NFC", str(text))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_for_lexical(text: str) -> str:
    """Lowercase and turn punctuation and symbols into spaces. Used for overlap."""
    text = clean_whitespace(text).lower()
    text = "".join(" " if unicodedata.category(ch)[0] in "PSC" else ch for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def whitespace_tokenizer(text: str) -> list[str]:
    # Splitting on word characters breaks Indic words at vowel signs.
    return text.split()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON on line %d of %s", line_number, path)
                continue
            if isinstance(record, dict):
                rows.append(record)
    return rows


def _prepare(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        lexical = normalize_for_lexical(str(record.get("paragraph") or ""))
        prepared.append(
            {
                "index": index,
                "record": record,
                "lexical": lexical,
                "n_words": len(lexical.split()),
                "digest": hashlib.md5(lexical.encode("utf-8")).hexdigest(),
            }
        )
    return prepared


def _flagged_pairs(
    compared: list[dict[str, Any]],
    *,
    tfidf_min: float,
    jaccard_dup: float,
    jaccard_near: float,
    drop_near: bool,
) -> list[tuple[int, int, str, float, float]]:
    """Return (i, j, label, tfidf, jaccard) for pairs that should not both be kept.

    i and j are positions in ``compared``, not in the original file.
    """
    count = len(compared)
    if count < 2:
        return []

    lexical = [item["lexical"] for item in compared]
    tfidf = TfidfVectorizer(
        tokenizer=whitespace_tokenizer,
        token_pattern=None,
        lowercase=False,
        ngram_range=(1, 2),
    )
    matrix = tfidf.fit_transform(lexical)
    tfidf_sim = (matrix @ matrix.T).toarray().astype(np.float32)
    del matrix

    binary = CountVectorizer(
        tokenizer=whitespace_tokenizer,
        token_pattern=None,
        lowercase=False,
        binary=True,
    ).fit_transform(lexical)
    intersection = (binary @ binary.T).toarray().astype(np.float32)
    sizes = np.asarray(binary.sum(axis=1)).ravel().astype(np.float32)
    del binary

    left, right = np.triu_indices(count, k=1)
    tf = np.clip(tfidf_sim[left, right], 0, 1)
    inter = intersection[left, right]
    del tfidf_sim, intersection
    union = sizes[left] + sizes[right] - inter
    jaccard = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)

    digests = np.array([item["digest"] for item in compared])
    exact = digests[left] == digests[right]
    gated = (tf >= tfidf_min) | exact
    duplicate = (gated & (jaccard >= jaccard_dup)) | exact
    near = gated & (jaccard >= jaccard_near) & ~duplicate
    if not drop_near:
        near = np.zeros_like(near)

    flagged: list[tuple[int, int, str, float, float]] = []
    for position in np.flatnonzero(duplicate | near):
        label = "duplicate" if duplicate[position] else "near duplicate"
        flagged.append(
            (
                int(left[position]),
                int(right[position]),
                label,
                float(tf[position]),
                float(jaccard[position]),
            )
        )
    return flagged


def _choose_removals(
    compared: list[dict[str, Any]],
    pairs: list[tuple[int, int, str, float, float]],
) -> dict[int, dict[str, Any]]:
    """Map a compared-list index to the kept row it duplicates.

    File order wins: a paragraph is removed only when it matches a paragraph
    that is already kept. Same chain rule as the sheet script.
    """
    neighbors: list[dict[int, tuple[str, float, float]]] = [{} for _ in compared]
    for left, right, label, tfidf, jaccard in pairs:
        neighbors[left][right] = (label, tfidf, jaccard)
        neighbors[right][left] = (label, tfidf, jaccard)

    kept: set[int] = set()
    removed: dict[int, dict[str, Any]] = {}
    for index in range(len(compared)):
        matches = neighbors[index]
        if not matches:
            kept.add(index)
            continue
        kept_matches = [other for other in matches if other in kept]
        if not kept_matches:
            kept.add(index)
            continue

        def _rank(other: int) -> tuple[int, float, int]:
            label, _tfidf, jaccard = matches[other]
            # Prefer a true duplicate over a near duplicate, then higher overlap,
            # then the earlier row.
            return (0 if label == "duplicate" else 1, -jaccard, other)

        best = min(kept_matches, key=_rank)
        label, tfidf, jaccard = matches[best]
        removed[index] = {
            "label": label,
            "tfidf": tfidf,
            "jaccard": jaccard,
            "kept_index": best,
        }
    return removed


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def process(
    input_path: Path,
    *,
    unique_path: Path,
    duplicate_path: Path,
    min_words: int,
    tfidf_min: float,
    jaccard_dup: float,
    jaccard_near: float,
    drop_near: bool,
) -> dict[str, Any]:
    prepared = _prepare(_load_jsonl(input_path))
    compared = [item for item in prepared if item["n_words"] >= min_words]
    skipped_short = len(prepared) - len(compared)

    logger.info(
        "Comparing %d paragraphs (skipped %d under %d words)",
        len(compared),
        skipped_short,
        min_words,
    )
    if len(compared) > 6000:
        logger.warning("%d paragraphs: the pairwise matrices will use a lot of memory.", len(compared))

    pairs = _flagged_pairs(
        compared,
        tfidf_min=tfidf_min,
        jaccard_dup=jaccard_dup,
        jaccard_near=jaccard_near,
        drop_near=drop_near,
    )
    removals = _choose_removals(compared, pairs)

    unique_rows: list[dict[str, Any]] = []
    duplicate_rows: list[dict[str, Any]] = []
    compared_pos = {item["index"]: pos for pos, item in enumerate(compared)}

    for item in prepared:
        pos = compared_pos.get(item["index"])
        if pos is None or pos not in removals:
            unique_rows.append(item["record"])
            continue
        decision = removals[pos]
        kept = compared[decision["kept_index"]]
        kept_record = kept["record"]
        row = dict(item["record"])
        row["_similarity_label"] = decision["label"]
        row["_similar_to_id"] = kept_record.get("id")
        row["_similar_to_source"] = kept_record.get("source")
        row["_tfidf_similarity"] = round(decision["tfidf"], 3)
        row["_jaccard"] = round(decision["jaccard"], 3)
        row["_similarity_reason"] = (
            f"Remove - {decision['label']} with id {kept_record.get('id')}"
        )
        duplicate_rows.append(row)

    _write_jsonl(unique_path, unique_rows)
    _write_jsonl(duplicate_path, duplicate_rows)

    duplicate_n = sum(1 for row in duplicate_rows if row["_similarity_label"] == "duplicate")
    near_n = len(duplicate_rows) - duplicate_n
    stats = {
        "input": str(input_path),
        "unique": str(unique_path),
        "duplicates": str(duplicate_path),
        "records_in": len(prepared),
        "compared": len(compared),
        "skipped_short": skipped_short,
        "flagged_pairs": len(pairs),
        "kept": len(unique_rows),
        "removed": len(duplicate_rows),
        "removed_duplicate": duplicate_n,
        "removed_near_duplicate": near_n,
        "tfidf_min": tfidf_min,
        "jaccard_duplicate": jaccard_dup,
        "jaccard_near": jaccard_near if drop_near else None,
    }
    logger.info(
        "Kept %d, removed %d (%d duplicate, %d near duplicate)",
        stats["kept"],
        stats["removed"],
        duplicate_n,
        near_n,
    )
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove duplicate and near-duplicate TTS paragraphs from a JSONL file. "
            "Same overlap rules as scripts/tts_script_similarity_check.py."
        )
    )
    parser.add_argument("--input", type=Path, required=True, help="JSONL with a paragraph field.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the two output files (default: the input's directory).",
    )
    parser.add_argument("--min-words", type=int, default=_MIN_WORDS, help="Skip shorter paragraphs (they are kept).")
    parser.add_argument("--tfidf-min", type=float, default=_TFIDF_MIN, help="TF-IDF cosine gate (default: 0.70).")
    parser.add_argument(
        "--jaccard-duplicate",
        type=float,
        default=_JACCARD_DUP,
        help="Word Jaccard at or above this, after the TF-IDF gate, is a duplicate (default: 0.85).",
    )
    parser.add_argument(
        "--jaccard-near",
        type=float,
        default=_JACCARD_NEAR,
        help="Word Jaccard at or above this, after the TF-IDF gate, is a near duplicate (default: 0.60).",
    )
    parser.add_argument(
        "--keep-near",
        action="store_true",
        help="Remove only exact and high-overlap duplicates. Leave near duplicates in the unique file.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        logger.error("Input file not found: %s", input_path)
        return 1
    for name, value in (
        ("--tfidf-min", args.tfidf_min),
        ("--jaccard-duplicate", args.jaccard_duplicate),
        ("--jaccard-near", args.jaccard_near),
    ):
        if not 0 <= value <= 1:
            logger.error("%s must be between 0 and 1", name)
            return 1
    if args.jaccard_near > args.jaccard_duplicate:
        logger.error("--jaccard-near cannot be higher than --jaccard-duplicate")
        return 1

    output_dir = (args.output_dir or input_path.parent).expanduser().resolve()
    stem = input_path.stem
    try:
        stats = process(
            input_path,
            unique_path=output_dir / f"{stem}_unique.jsonl",
            duplicate_path=output_dir / f"{stem}_duplicates.jsonl",
            min_words=max(1, args.min_words),
            tfidf_min=args.tfidf_min,
            jaccard_dup=args.jaccard_duplicate,
            jaccard_near=args.jaccard_near,
            drop_near=not args.keep_near,
        )
    except Exception:
        logger.exception("Similarity dedupe failed")
        return 1

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
