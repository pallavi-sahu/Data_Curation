#!/usr/bin/env python3
"""
Drop duplicate TTS paragraphs from a JSONL file.

Each input line is one script, for example::

    {"id": "3875", "source": "https://...", "paragraph": "...",
     "language_code": "sa", "detection_method": "dataset",
     "domain": "legal_government", "_word_count": 367}

The output JSONL uses that same shape. The first copy of a paragraph is
kept. A later copy is left out when the normalized text is the same, or
when it is a near copy: TF-IDF cosine at least 0.70 and word overlap at
least 0.85 (duplicate) or 0.60 (near duplicate).

Removed rows are written beside the output, in the same fields, plus
``_similarity_label``, ``_similar_to_id``, ``_similar_to_source``,
``_tfidf_similarity``, ``_jaccard``, and ``_similarity_reason``.

Example:
  python3 scripts/tts_script_similarity_check.py \\
    --input new_data_batch3/sanskrit/sanskrit_tts_merged.jsonl
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_dedupe():
    path = _SCRIPTS_DIR / "dedupe_similar_tts_jsonl.py"
    spec = importlib.util.spec_from_file_location("dedupe_similar_tts_jsonl", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    dedupe = _load_dedupe()
    parser = dedupe.build_parser()
    parser.description = (
        "Read a TTS JSONL, drop duplicate paragraphs, and write a JSONL "
        "of the kept rows in the same format."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Kept-rows JSONL (default: {input_stem}_deduped.jsonl next to the input).",
    )
    args = parser.parse_args(argv)

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
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else output_dir / f"{input_path.stem}_deduped.jsonl"
    )
    duplicate_path = output_path.with_name(f"{output_path.stem}_duplicates.jsonl")

    try:
        stats = dedupe.process(
            input_path,
            unique_path=output_path,
            duplicate_path=duplicate_path,
            min_words=max(1, args.min_words),
            tfidf_min=args.tfidf_min,
            jaccard_dup=args.jaccard_duplicate,
            jaccard_near=args.jaccard_near,
            drop_near=not args.keep_near,
        )
    except Exception:
        logger.exception("Similarity check failed")
        return 1

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
