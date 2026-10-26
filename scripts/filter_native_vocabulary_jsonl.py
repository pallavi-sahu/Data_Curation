#!/usr/bin/env python3
"""
Split TTS JSONL into readable native prose vs foreign words in native script.

The text is already in the target script (Devanagari for Sanskrit). Latin
tokens were removed earlier, so English shows up as transliteration:
वर्डप्रेस्, ब्लॉग्, सर्च इञ्जिन, क्रियेटिव कॉमन्स. A speaker cannot read
that aloud as Sanskrit.

One LLM call per batch counts foreign words. The share is that count
divided by every whitespace-separated word in the paragraph. A paragraph
is rejected when the share is above ``--max-foreign-ratio`` (default 0.10).

Outputs (same directory as the input, unless ``--output-dir`` is set):

  ``{stem}_native.jsonl``   — kept; readable in the target language
  ``{stem}_foreign.jsonl``  — rejected; too many transliterated foreign words

A row Gemini blocks as ``PROHIBITED_CONTENT`` is written to the foreign
file. Other API failures are left out of both files. Re-run with
``--resume`` to retry only those ids.

Example:
  python3 scripts/filter_native_vocabulary_jsonl.py \\
    --input new_data_batch3/sanskrit/sanskrit_tts_merged.jsonl \\
    --language sanskrit --workers 8 --batch-size 2
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.env_loader import load_project_env
from validation.llm.client import (
    _call_llm_detailed,
    configure_llm_provider,
    get_llm_provider,
    llm_api_key_hint,
    llm_available,
)

load_project_env()

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 2
_DEFAULT_MAX_FOREIGN_RATIO = 0.10
_SCRIPT_CHAR_LIMIT = 8000

_SYSTEM = """You judge paragraphs for a text-to-speech dataset.
The writing system is already the target language's script. Do not flag Latin letters.
Flag words from English or another modern language that were only spelled phonetically in that script.

Native means a reader of the target language can pronounce the word as that language.
Count a token as foreign when it is a transliteration or an unassimilated borrowing, for example
English blog, website, shopping, WordPress, plugin, SEO, matrix, container, daemon, Creative Commons,
or a foreign personal, brand, or news name (Marcus, HSBC, BBC, Fairhead).
Classical, Vedic, and technical vocabulary of the target language is native, even if it is dense or archaic.
A word-for-word translation is still native when the words themselves are native vocabulary.
Do not decide keep or reject. Only count.

Split the paragraph on whitespace. That token count is the total, including function words.
foreign_word_count is how many of those tokens are foreign. Repeated foreign tokens each count.
foreign_word_ratio = foreign_word_count / total tokens. Use 0 when there are no foreign tokens.

Return JSON only:
{"results":[{"id":"<id>","foreign_word_count":0,"foreign_word_ratio":0.0,"examples":["<foreign word>"],"reason":"<one short sentence>"}]}
One object per input id. examples is at most 6 foreign words copied from the paragraph, or an empty list.
No other fields."""


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    size = max(1, size)
    return [items[i : i + size] for i in range(0, len(items), size)]


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


def _seen_ids(paths: list[Path]) -> set[str]:
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        for record in _load_jsonl(path):
            row_id = str(record.get("id") or "").strip()
            if row_id:
                seen.add(row_id)
    return seen


def _parse_results(raw: str | None) -> tuple[list[dict[str, Any]], str | None]:
    if not raw:
        return [], "Empty LLM response"
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [], "LLM returned invalid JSON"
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)], None
    if isinstance(parsed, dict):
        for key in ("results", "items", "data"):
            value = parsed.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)], None
        if "id" in parsed or "foreign_word_ratio" in parsed:
            return [parsed], None
    return [], "LLM JSON had no results list"


def _ratio(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _examples(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    examples: list[str] = []
    for item in raw:
        word = str(item or "").strip()
        if word and word not in examples:
            examples.append(word)
        if len(examples) >= 6:
            break
    return examples


def judge_batch(
    batch: list[dict[str, Any]],
    language: str,
) -> tuple[dict[str, dict[str, Any]], str | None, bool]:
    """Return id -> judgment, an error, and whether Gemini blocked the call.

    On failure the map is empty. ``prohibited`` is true only for
    ``PROHIBITED_CONTENT``.
    """
    payload = {
        "language": language,
        "scripts": [
            {
                "id": str(record.get("id") or ""),
                "text": str(record.get("paragraph") or "")[:_SCRIPT_CHAR_LIMIT],
            }
            for record in batch
        ],
    }
    call = _call_llm_detailed(_SYSTEM, json.dumps(payload, ensure_ascii=False))
    prohibited = bool(call.prohibited) or "PROHIBITED_CONTENT" in (call.error or "").upper()
    if call.error and not call.text:
        return {}, call.error or "LLM error", prohibited
    results, parse_error = _parse_results(call.text)
    if parse_error or not results:
        return {}, parse_error or call.error or "empty results", prohibited

    by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        row_id = str(item.get("id") or "").strip()
        if row_id:
            by_id[row_id] = item
    if not by_id and len(results) == len(batch):
        for record, item in zip(batch, results):
            by_id[str(record.get("id") or "")] = item
    return by_id, None, False


def _foreign_ratio(
    record: dict[str, Any],
    judgment: dict[str, Any],
) -> tuple[float | None, int | None, int]:
    """Foreign tokens divided by every whitespace-separated word."""
    total_words = len(str(record.get("paragraph") or "").split())
    raw_count = judgment.get("foreign_word_count")
    try:
        foreign_count = int(raw_count)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        foreign_count = None
    if foreign_count is not None and total_words > 0:
        foreign_count = min(max(foreign_count, 0), total_words)
        return foreign_count / total_words, foreign_count, total_words
    return _ratio(judgment.get("foreign_word_ratio")), foreign_count, total_words


def _annotate(
    record: dict[str, Any],
    judgment: dict[str, Any],
    *,
    max_foreign_ratio: float,
) -> dict[str, Any]:
    ratio, foreign_count, total_words = _foreign_ratio(record, judgment)
    examples = _examples(judgment.get("examples") or judgment.get("foreign_words"))
    reason = str(judgment.get("reason") or judgment.get("verdict_description") or "").strip()
    if ratio is None:
        verdict = "reject"
        reason = reason or "LLM did not return a foreign-word count"
        ratio = 1.0
    elif ratio > max_foreign_ratio:
        verdict = "reject"
    else:
        verdict = "keep"
    updated = dict(record)
    updated["_native_verdict"] = verdict
    updated["_foreign_word_count"] = foreign_count
    updated["_total_word_count"] = total_words
    updated["_foreign_word_ratio"] = round(ratio, 3)
    updated["_foreign_examples"] = examples
    updated["_native_reason"] = reason
    return updated


def _prohibited_row(record: dict[str, Any], error: str) -> dict[str, Any]:
    """A blocked paragraph is not readable TTS material, so it is rejected."""
    updated = dict(record)
    updated["_native_verdict"] = "reject"
    updated["_foreign_word_ratio"] = None
    updated["_foreign_examples"] = []
    updated["_native_reason"] = f"Gemini blocked the request: {error}"
    return updated


class _JsonlWriter:
    def __init__(self, path: Path, *, resume: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a" if resume else "w", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            self._handle.write(line)
            self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def process(
    input_path: Path,
    *,
    native_path: Path,
    foreign_path: Path,
    language: str,
    max_foreign_ratio: float,
    workers: int,
    batch_size: int,
    limit: int | None,
    resume: bool,
) -> dict[str, Any]:
    records = _load_jsonl(input_path)
    if limit is not None:
        records = records[:limit]

    already: set[str] = set()
    if resume:
        already = _seen_ids([native_path, foreign_path])
        records = [row for row in records if str(row.get("id") or "") not in already]

    stats: dict[str, Any] = {
        "input": str(input_path),
        "native": str(native_path),
        "foreign": str(foreign_path),
        "language": language,
        "max_foreign_ratio": max_foreign_ratio,
        "llm_provider": get_llm_provider(),
        "skipped_already_judged": len(already),
        "pending": len(records),
        "kept": 0,
        "rejected": 0,
        "prohibited": 0,
        "failed": 0,
    }
    if not records:
        logger.info("Nothing left to judge (skipped %d already judged).", len(already))
        return stats

    batches = _chunked(records, batch_size)
    native_writer = _JsonlWriter(native_path, resume=resume)
    foreign_writer = _JsonlWriter(foreign_path, resume=resume)
    stats_lock = threading.Lock()

    def _write_rejected(record: dict[str, Any], *, prohibited: bool) -> None:
        foreign_writer.write(record)
        with stats_lock:
            stats["rejected"] += 1
            if prohibited:
                stats["prohibited"] += 1

    def _consume(batch: list[dict[str, Any]]) -> None:
        by_id, error, prohibited = judge_batch(batch, language)
        if error and prohibited:
            # A pair can be blocked by one paragraph. Judge each row alone
            # so the other one is not rejected with it.
            if len(batch) > 1:
                for record in batch:
                    _consume([record])
                return
            logger.info(
                "Prohibited content for id %s; writing to the foreign file",
                batch[0].get("id"),
            )
            _write_rejected(_prohibited_row(batch[0], error), prohibited=True)
            return
        if error:
            logger.error("Batch failed (%d rows): %s", len(batch), error)
            with stats_lock:
                stats["failed"] += len(batch)
            return
        for record in batch:
            row_id = str(record.get("id") or "")
            judgment = by_id.get(row_id)
            if judgment is None:
                logger.error("Missing judgment for id %s", row_id)
                with stats_lock:
                    stats["failed"] += 1
                continue
            annotated = _annotate(record, judgment, max_foreign_ratio=max_foreign_ratio)
            if annotated["_native_verdict"] == "keep":
                native_writer.write(annotated)
                with stats_lock:
                    stats["kept"] += 1
            else:
                _write_rejected(annotated, prohibited=False)

    try:
        logger.info(
            "Judging %d paragraphs in %d batches (batch_size=%d, workers=%d, max_foreign_ratio=%.2f)",
            len(records),
            len(batches),
            batch_size,
            workers,
            max_foreign_ratio,
        )
        if workers <= 1:
            for index, batch in enumerate(batches, start=1):
                _consume(batch)
                if index % 5 == 0 or index == len(batches):
                    logger.info("Completed %d/%d batches", index, len(batches))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_consume, batch) for batch in batches]
                done = 0
                for future in as_completed(futures):
                    future.result()
                    done += 1
                    if done % 5 == 0 or done == len(batches):
                        logger.info("Completed %d/%d batches", done, len(batches))
    finally:
        native_writer.close()
        foreign_writer.close()

    logger.info(
        "Kept %d, rejected %d (%d prohibited), failed %d (retry failures with --resume)",
        stats["kept"],
        stats["rejected"],
        stats["prohibited"],
        stats["failed"],
    )
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reject TTS paragraphs whose words are mostly English (or another language) "
            "transliterated into the target script. Write native and foreign JSONL files."
        )
    )
    parser.add_argument("--input", type=Path, required=True, help="Input JSONL with paragraph and id.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the two output files (default: the input's directory).",
    )
    parser.add_argument(
        "--language",
        default="sanskrit",
        help="Language name passed to the LLM (default: sanskrit).",
    )
    parser.add_argument(
        "--max-foreign-ratio",
        type=float,
        default=_DEFAULT_MAX_FOREIGN_RATIO,
        help=(
            "Reject when foreign words / all words is above this "
            f"(default: {_DEFAULT_MAX_FOREIGN_RATIO}). A share of exactly this value is kept."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help=f"Paragraphs per LLM call (default: {_DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel LLM calls (default: 8).",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["gemini", "openai", "gpt"],
        default=None,
        help="LLM backend (default: VALIDATION_LLM_PROVIDER, then gemini).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Only the first N rows (for a trial).")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip ids already present in the native or foreign output, and append.",
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
    if not 0 <= args.max_foreign_ratio <= 1:
        logger.error("--max-foreign-ratio must be between 0 and 1")
        return 1

    configure_llm_provider(args.llm_provider)
    if not llm_available():
        logger.error("%s", llm_api_key_hint())
        return 1

    output_dir = (args.output_dir or input_path.parent).expanduser().resolve()
    stem = input_path.stem
    native_path = output_dir / f"{stem}_native.jsonl"
    foreign_path = output_dir / f"{stem}_foreign.jsonl"

    try:
        stats = process(
            input_path,
            native_path=native_path,
            foreign_path=foreign_path,
            language=args.language,
            max_foreign_ratio=args.max_foreign_ratio,
            workers=max(1, args.workers),
            batch_size=max(1, args.batch_size),
            limit=args.limit,
            resume=args.resume,
        )
    except Exception:
        logger.exception("Native-vocabulary filter failed")
        return 1

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if stats["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
