#!/usr/bin/env python3
"""
Review whole ``.txt`` scripts in a folder with Gemini and write a CSV.

For each ``.txt`` file:
  script_id          — filename without ``.txt``
  script             — full file text
  verdict            — keep / delete / review
  verdict_description — short reason

Acceptance criteria (prompted to Gemini):
  Is the paragraph contextually correct? Check semantics of the full script.

Batching (``--batch-size``, default 5):
  Multiple scripts are reviewed in one Gemini call to cut request overhead
  and cost. Batches are processed in parallel with ``--workers``.

Example:
  .venv/bin/python scripts/gemini_review_txt_folder.py \\
    --folder "Apollo TTS dataset/assamese_phase_3" \\
    --output unused_scripts/assamese_txt_gemini_review.csv \\
    --batch-size 5 --workers 8
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.env_loader import load_project_env
from validation.llm.client import (
    _call_llm_detailed,
    _parse_json_response_detailed,
    configure_llm_provider,
    get_llm_provider,
    llm_api_key_hint,
    llm_available,
)

load_project_env()

logger = logging.getLogger(__name__)

_VERDICTS = {"keep", "delete", "review"}
_DEFAULT_BATCH_SIZE = 5
_SCRIPT_CHAR_LIMIT = 6000

_SYSTEM = (
    "You review full script paragraphs for a speech/TTS dataset.\n"
    "Acceptance criteria: each script must be contextually correct and "
    "semantically coherent as a paragraph (natural prose a speaker could read).\n"
    "Decide one verdict per script:\n"
    "- keep: contextually correct and semantically sound\n"
    "- delete: broken, incoherent, nonsense, or unsuitable for TTS\n"
    "- review: borderline / uncertain; needs human review\n"
    "You will receive a JSON list of scripts. Return JSON only:\n"
    '{"results":[{"script_id":"<id>","verdict":"keep|delete|review",'
    '"verdict_description":"<one or two sentences>"}, ...]}\n'
    "Return exactly one result object per input script_id, same order preferred. "
    "No extra fields."
)


def _normalize_verdict(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value in _VERDICTS:
        return value
    if value in {"accept", "accepted", "ok", "pass"}:
        return "keep"
    if value in {"reject", "rejected", "drop", "remove"}:
        return "delete"
    if value in {"flag", "uncertain", "maybe"}:
        return "review"
    return "review"


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        size = 1
    return [items[i : i + size] for i in range(0, len(items), size)]


def _extract_results_list(parsed: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ("results", "items", "reviews", "data"):
            value = parsed.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if "script_id" in parsed or "verdict" in parsed:
            return [parsed]
    return []


def _error_row(script_id: str, script: str, message: str) -> dict[str, str]:
    return {
        "script_id": script_id,
        "script": script,
        "verdict": "review",
        "verdict_description": message,
    }


def _row_from_result(
    script_id: str,
    script: str,
    result: dict[str, Any] | None,
    *,
    fallback_message: str,
) -> dict[str, str]:
    if not result:
        return _error_row(script_id, script, fallback_message)
    return {
        "script_id": script_id,
        "script": script,
        "verdict": _normalize_verdict(result.get("verdict")),
        "verdict_description": str(
            result.get("verdict_description")
            or result.get("description")
            or result.get("reason")
            or ""
        ).strip(),
    }


def review_script_batch(
    batch: list[tuple[str, str]],
    language: str,
) -> list[dict[str, str]]:
    """Review one batch of (script_id, script) pairs in a single LLM call."""
    payload = {
        "language": language,
        "scripts": [
            {
                "script_id": script_id,
                "script": script[:_SCRIPT_CHAR_LIMIT],
            }
            for script_id, script in batch
        ],
    }
    call = _call_llm_detailed(_SYSTEM, json.dumps(payload, ensure_ascii=False))
    if call.error and not call.text:
        return [
            _error_row(script_id, script, f"LLM error: {call.error}")
            for script_id, script in batch
        ]

    parsed, parse_error = _parse_json_response_detailed(call.text)
    results = _extract_results_list(parsed)
    if parse_error or not results:
        return [
            _error_row(
                script_id,
                script,
                f"LLM parse error: {parse_error or call.error or 'empty results'}",
            )
            for script_id, script in batch
        ]

    by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        sid = str(item.get("script_id") or "").strip()
        if sid:
            by_id[sid] = item

    rows: list[dict[str, str]] = []
    for index, (script_id, script) in enumerate(batch):
        result = by_id.get(script_id)
        if result is None and index < len(results):
            # Fall back to positional match when model drops script_id.
            result = results[index]
        rows.append(
            _row_from_result(
                script_id,
                script,
                result,
                fallback_message="LLM batch missing this script_id",
            )
        )
    return rows


def iter_txt_files(folder: Path) -> list[Path]:
    files = sorted(folder.rglob("*.txt"))
    by_stem: dict[str, Path] = {}
    for path in files:
        by_stem.setdefault(path.stem, path)
    return sorted(by_stem.values(), key=lambda p: p.stem)


def process_folder(
    folder: Path,
    *,
    output: Path,
    language: str,
    workers: int,
    batch_size: int,
    limit: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    files = iter_txt_files(folder)
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"No .txt files found under {folder}")

    jobs = [(path.stem, path.read_text(encoding="utf-8").strip()) for path in files]
    batches = _chunked(jobs, batch_size)

    rows: list[dict[str, str]] = []
    if dry_run:
        rows = [
            {
                "script_id": script_id,
                "script": script,
                "verdict": "",
                "verdict_description": "",
            }
            for script_id, script in jobs
        ]
    elif workers <= 1:
        done_scripts = 0
        for batch in batches:
            rows.extend(review_script_batch(batch, language))
            done_scripts += len(batch)
            logger.info(
                "Reviewed %d/%d scripts (%d batches)",
                done_scripts,
                len(jobs),
                len(batches),
            )
    else:
        logger.info(
            "Reviewing %d scripts in %d batches (batch_size=%d, workers=%d)",
            len(jobs),
            len(batches),
            batch_size,
            workers,
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(review_script_batch, batch, language): i
                for i, batch in enumerate(batches)
            }
            tmp: list[list[dict[str, str]] | None] = [None] * len(batches)
            done = 0
            for fut in as_completed(future_map):
                i = future_map[fut]
                tmp[i] = fut.result()
                done += 1
                if done % 5 == 0 or done == len(batches):
                    logger.info("Completed %d/%d batches", done, len(batches))
        for batch_rows in tmp:
            if batch_rows:
                rows.extend(batch_rows)

    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["script_id", "script", "verdict", "verdict_description"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("verdict") or "unset"
        counts[key] = counts.get(key, 0) + 1

    summary = {
        "folder": str(folder),
        "output": str(output),
        "language": language,
        "scripts": len(rows),
        "batch_size": batch_size,
        "batches": len(batches),
        "verdict_counts": counts,
        "dry_run": dry_run,
        "llm_provider": get_llm_provider(),
    }
    stats_path = output.with_name(output.stem + "_stats.json")
    stats_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "Wrote %s (%d rows, %d batches%s) stats=%s",
        output,
        len(rows),
        len(batches),
        ", dry-run" if dry_run else "",
        stats_path,
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gemini-review .txt scripts in a folder into a CSV of verdicts."
    )
    parser.add_argument(
        "--folder",
        type=Path,
        required=True,
        help="Folder containing .txt script files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: {folder_name}_txt_gemini_review.csv).",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Language hint for the LLM (default: folder name).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help=f"Scripts per Gemini call (default: {_DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel batch requests (default: 8).",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["gemini", "openai", "gpt"],
        default=None,
        help="LLM backend (default: VALIDATION_LLM_PROVIDER / gemini).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N txt files (testing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write CSV with empty verdicts; do not call the LLM.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        logger.error("Folder not found: %s", folder)
        return 1

    configure_llm_provider(args.llm_provider)
    if not args.dry_run and not llm_available():
        logger.error("%s", llm_api_key_hint())
        return 1

    output = (
        args.output.expanduser().resolve()
        if args.output
        else folder.parent / f"{folder.name}_txt_gemini_review.csv"
    )
    language = args.language or folder.name

    try:
        process_folder(
            folder,
            output=output,
            language=language,
            workers=max(1, args.workers),
            batch_size=max(1, args.batch_size),
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except Exception:
        logger.exception("Failed reviewing txt folder")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
