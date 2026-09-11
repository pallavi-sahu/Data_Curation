#!/usr/bin/env python3
"""
Retry Gemini review for CSV rows that failed due to API / parse errors.

Supports both review CSV shapes:

  1. Script CSV (from ``gemini_review_txt_folder.py``):
       script_id, script, verdict, verdict_description

  2. Segment CSV (from ``gemini_review_json_segments.py``):
       script_id, segment_index, text, raw_text, verdict, verdict_description

A row is retried when ``verdict_description`` looks like a failed API call, e.g.:
  - ``LLM parse error: LLM returned invalid JSON``
  - ``LLM error: 504 Stream removed (Deadline Exceeded)``
  - empty-results / batch-missing / rate-limit style messages

``PROHIBITED_CONTENT`` blocks are skipped by default (retry will not help);
pass ``--include-blocked`` to retry them too.

By default the input CSV is updated in place. Pass ``--output`` only if you
want a separate file.

Example:
  .venv/bin/python scripts/retry_gemini_review_csv.py \\
    --csv completed_scripts/malayalam_segments_gemini_review.csv \\
    --batch-size 4 --workers 4
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.env_loader import load_project_env
from validation.llm.client import (
    configure_llm_provider,
    get_llm_provider,
    llm_api_key_hint,
    llm_available,
)

load_project_env()

# Reuse batch reviewers from the original scripts.
import importlib.util


def _load_script_module(name: str, filename: str):
    path = _REPO_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_txt = _load_script_module("gemini_review_txt_folder", "gemini_review_txt_folder.py")
_seg = _load_script_module(
    "gemini_review_json_segments", "gemini_review_json_segments.py"
)

review_script_batch = _txt.review_script_batch
review_segment_batch = _seg.review_segment_batch
build_segment_batches = _seg.build_segment_batches

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 4

# Transient / recoverable failure markers in verdict_description.
_FAIL_PATTERNS = [
    re.compile(r"llm\s*parse\s*error", re.I),
    re.compile(r"llm\s*error\s*:", re.I),
    re.compile(r"invalid\s*json", re.I),
    re.compile(r"deadline\s*exceeded", re.I),
    re.compile(r"\b504\b"),
    re.compile(r"stream\s*removed", re.I),
    re.compile(r"\b429\b"),
    re.compile(r"rate\s*limit", re.I),
    re.compile(r"empty\s*results", re.I),
    re.compile(r"batch\s*missing", re.I),
    re.compile(r"empty\s*response", re.I),
    re.compile(r"timed?\s*out", re.I),
    re.compile(r"connection\s*(reset|error|aborted)", re.I),
    re.compile(r"service\s*unavailable", re.I),
    re.compile(r"\b503\b"),
    re.compile(r"\b500\b"),
]

_BLOCKED_RE = re.compile(r"prohibited[_\s]*content", re.I)


def is_llm_failure(description: str, *, include_blocked: bool) -> bool:
    text = (description or "").strip()
    if not text:
        return False
    if _BLOCKED_RE.search(text) and not include_blocked:
        return False
    return any(pat.search(text) for pat in _FAIL_PATTERNS)


def detect_format(fieldnames: list[str] | None) -> str:
    cols = {c.strip().lower() for c in (fieldnames or [])}
    if {"script_id", "segment_index", "text", "verdict"}.issubset(cols):
        return "segment"
    if {"script_id", "script", "verdict"}.issubset(cols):
        return "script"
    raise ValueError(
        "Unrecognized CSV headers. Expected script CSV "
        "(script_id, script, verdict, ...) or segment CSV "
        f"(script_id, segment_index, text, ...); got {fieldnames}"
    )


def failure_kind(description: str) -> str:
    low = (description or "").lower()
    if "prohibited" in low:
        return "prohibited_content"
    if "invalid json" in low or "parse error" in low:
        return "invalid_json"
    if "504" in low or "deadline" in low or "stream removed" in low:
        return "deadline_504"
    if "429" in low or "rate limit" in low:
        return "rate_limit"
    if "empty results" in low or "empty response" in low:
        return "empty_results"
    if "batch missing" in low:
        return "batch_missing"
    if low.startswith("llm error"):
        return "llm_error"
    return "other_failure"


def load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        fieldnames = list(reader.fieldnames)
        rows = [{k: (v if v is not None else "") for k, v in row.items()} for row in reader]
    return fieldnames, rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def retry_script_rows(
    rows: list[dict[str, str]],
    fail_indices: list[int],
    *,
    language: str,
    batch_size: int,
    workers: int,
) -> dict[str, int]:
    jobs = [
        (rows[i].get("script_id", ""), rows[i].get("script", ""), i)
        for i in fail_indices
    ]
    batches: list[list[tuple[str, str, int]]] = []
    for start in range(0, len(jobs), batch_size):
        batches.append(jobs[start : start + batch_size])

    def _work(batch: list[tuple[str, str, int]]) -> list[tuple[int, dict[str, str]]]:
        pairs = [(sid, script) for sid, script, _ in batch]
        reviewed = review_script_batch(pairs, language)
        out: list[tuple[int, dict[str, str]]] = []
        for (_, _, idx), result in zip(batch, reviewed):
            out.append(
                (
                    idx,
                    {
                        "verdict": result.get("verdict", "review"),
                        "verdict_description": result.get("verdict_description", ""),
                    },
                )
            )
        return out

    updated = 0
    still_failed = 0
    if workers <= 1:
        for batch in batches:
            for idx, patch in _work(batch):
                rows[idx]["verdict"] = patch["verdict"]
                rows[idx]["verdict_description"] = patch["verdict_description"]
                updated += 1
                if is_llm_failure(patch["verdict_description"], include_blocked=True):
                    still_failed += 1
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_work, batch) for batch in batches]
            done = 0
            for fut in as_completed(futures):
                for idx, patch in fut.result():
                    rows[idx]["verdict"] = patch["verdict"]
                    rows[idx]["verdict_description"] = patch["verdict_description"]
                    updated += 1
                    if is_llm_failure(patch["verdict_description"], include_blocked=True):
                        still_failed += 1
                done += 1
                if done % 5 == 0 or done == len(futures):
                    logger.info("Completed %d/%d script batches", done, len(futures))

    return {"retried": updated, "still_failed": still_failed, "batches": len(batches)}


def retry_segment_rows(
    rows: list[dict[str, str]],
    fail_indices: list[int],
    *,
    language: str,
    batch_size: int,
    workers: int,
) -> dict[str, int]:
    # Reconstruct script context from all segments of each script_id in the CSV.
    script_texts: dict[str, list[str]] = {}
    for row in rows:
        sid = row.get("script_id", "")
        text = (row.get("text") or "").strip()
        if sid and text:
            script_texts.setdefault(sid, []).append(text)
    script_context = {
        sid: " ".join(parts) for sid, parts in script_texts.items()
    }

    pending = []
    for i in fail_indices:
        row = rows[i]
        sid = row.get("script_id", "")
        pending.append(
            {
                "script_id": sid,
                "segment_index": row.get("segment_index", ""),
                "text": row.get("text", ""),
                "raw_text": row.get("raw_text", ""),
                "script": script_context.get(sid, row.get("text", "")),
                "_row_index": i,
            }
        )

    batches = build_segment_batches(pending, batch_size)

    def _work(batch: list[dict[str, Any]]) -> list[tuple[int, dict[str, str]]]:
        reviewed = review_segment_batch(batch, language)
        out: list[tuple[int, dict[str, str]]] = []
        for item, result in zip(batch, reviewed):
            out.append(
                (
                    int(item["_row_index"]),
                    {
                        "verdict": result.get("verdict", "review"),
                        "verdict_description": result.get("verdict_description", ""),
                    },
                )
            )
        return out

    updated = 0
    still_failed = 0
    if workers <= 1:
        for bi, batch in enumerate(batches, start=1):
            for idx, patch in _work(batch):
                rows[idx]["verdict"] = patch["verdict"]
                rows[idx]["verdict_description"] = patch["verdict_description"]
                updated += 1
                if is_llm_failure(patch["verdict_description"], include_blocked=True):
                    still_failed += 1
            if bi % 5 == 0 or bi == len(batches):
                logger.info("Completed %d/%d segment batches", bi, len(batches))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_work, batch) for batch in batches]
            done = 0
            for fut in as_completed(futures):
                for idx, patch in fut.result():
                    rows[idx]["verdict"] = patch["verdict"]
                    rows[idx]["verdict_description"] = patch["verdict_description"]
                    updated += 1
                    if is_llm_failure(patch["verdict_description"], include_blocked=True):
                        still_failed += 1
                done += 1
                if done % 5 == 0 or done == len(futures):
                    logger.info("Completed %d/%d segment batches", done, len(futures))

    return {"retried": updated, "still_failed": still_failed, "batches": len(batches)}


def process(
    csv_path: Path,
    *,
    output: Path,
    language: str,
    batch_size: int,
    workers: int,
    include_blocked: bool,
    dry_run: bool,
    passes: int,
) -> dict[str, Any]:
    fieldnames, rows = load_csv(csv_path)
    fmt = detect_format(fieldnames)
    logger.info("Loaded %d rows from %s (format=%s)", len(rows), csv_path, fmt)

    fail_indices = [
        i
        for i, row in enumerate(rows)
        if is_llm_failure(row.get("verdict_description", ""), include_blocked=include_blocked)
    ]
    kind_counts: dict[str, int] = {}
    for i in fail_indices:
        kind = failure_kind(rows[i].get("verdict_description", ""))
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    logger.info(
        "Found %d failed rows to retry: %s",
        len(fail_indices),
        json.dumps(kind_counts, ensure_ascii=False),
    )

    summary: dict[str, Any] = {
        "input": str(csv_path),
        "output": str(output),
        "format": fmt,
        "rows_total": len(rows),
        "failed_found": len(fail_indices),
        "failure_kinds": kind_counts,
        "batch_size": batch_size,
        "passes": passes,
        "dry_run": dry_run,
        "include_blocked": include_blocked,
        "llm_provider": get_llm_provider(),
    }

    if dry_run or not fail_indices:
        if not dry_run and not fail_indices:
            logger.info("No failed rows to retry; writing unchanged CSV to %s", output)
            write_csv(output, fieldnames, rows)
        summary["retried"] = 0
        summary["still_failed"] = len(fail_indices) if dry_run else 0
        stats_path = output.with_name(output.stem + "_retry_stats.json")
        if not dry_run:
            stats_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            logger.info("Dry run summary: %s", json.dumps(summary, ensure_ascii=False))
            stats_path = csv_path.with_name(csv_path.stem + "_retry_dry_run_stats.json")
            stats_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return summary

    total_retried = 0
    still_failed = 0
    for pass_no in range(1, max(1, passes) + 1):
        current_fails = [
            i
            for i, row in enumerate(rows)
            if is_llm_failure(
                row.get("verdict_description", ""), include_blocked=include_blocked
            )
        ]
        if not current_fails:
            logger.info("Pass %d: nothing left to retry", pass_no)
            break
        logger.info(
            "Pass %d/%d: retrying %d rows (batch_size=%d, workers=%d)",
            pass_no,
            passes,
            len(current_fails),
            batch_size,
            workers,
        )
        if fmt == "script":
            stats = retry_script_rows(
                rows,
                current_fails,
                language=language,
                batch_size=batch_size,
                workers=workers,
            )
        else:
            stats = retry_segment_rows(
                rows,
                current_fails,
                language=language,
                batch_size=batch_size,
                workers=workers,
            )
        total_retried += stats["retried"]
        still_failed = stats["still_failed"]
        logger.info(
            "Pass %d done: retried=%d still_failed=%d batches=%d",
            pass_no,
            stats["retried"],
            stats["still_failed"],
            stats["batches"],
        )
        # Shrink batch size on later passes for stubborn failures.
        batch_size = max(1, batch_size // 2)

    write_csv(output, fieldnames, rows)

    remaining_kinds: dict[str, int] = {}
    remaining = 0
    for row in rows:
        if is_llm_failure(row.get("verdict_description", ""), include_blocked=True):
            # Count remaining API-ish failures (including blocked).
            if is_llm_failure(
                row.get("verdict_description", ""), include_blocked=include_blocked
            ) or _BLOCKED_RE.search(row.get("verdict_description", "")):
                kind = failure_kind(row.get("verdict_description", ""))
                # Only count rows that still match retry criteria OR are blocked leftovers
                if is_llm_failure(
                    row.get("verdict_description", ""), include_blocked=include_blocked
                ):
                    remaining += 1
                    remaining_kinds[kind] = remaining_kinds.get(kind, 0) + 1

    summary.update(
        {
            "retried": total_retried,
            "still_failed": remaining,
            "still_failed_kinds": remaining_kinds,
        }
    )
    stats_path = output.with_name(output.stem + "_retry_stats.json")
    stats_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "Wrote %s (retried=%d, still_failed=%d) stats=%s",
        output,
        total_retried,
        remaining,
        stats_path,
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retry Gemini review CSV rows whose verdict_description shows "
            "an API / JSON failure."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="Input review CSV (script or segment format).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write to this CSV instead of overwriting --csv (default: in-place).",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help=argparse.SUPPRESS,  # default behavior; kept for backward compatibility
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Language hint (default: inferred from CSV filename stem).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help=f"Items per Gemini call (default: {_DEFAULT_BATCH_SIZE}; smaller is safer).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel batch requests (default: 4).",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=2,
        help="Retry passes over still-failing rows (default: 2; batch size halves each pass).",
    )
    parser.add_argument(
        "--include-blocked",
        action="store_true",
        help="Also retry PROHIBITED_CONTENT rows (usually useless).",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["gemini", "openai", "gpt"],
        default=None,
        help="LLM backend (default: VALIDATION_LLM_PROVIDER / gemini).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report how many rows would be retried; do not call LLM.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    csv_path = args.csv.expanduser().resolve()
    if not csv_path.is_file():
        logger.error("CSV not found: %s", csv_path)
        return 1

    output = (
        args.output.expanduser().resolve()
        if args.output
        else csv_path  # default: update the input CSV in place
    )

    configure_llm_provider(args.llm_provider)
    if not args.dry_run and not llm_available():
        logger.error("%s", llm_api_key_hint())
        return 1

    # Infer language from filename: malayalam_segments_gemini_review → malayalam
    language = args.language
    if not language:
        stem = csv_path.stem.lower()
        language = stem.split("_")[0] if stem else "unknown"

    try:
        process(
            csv_path,
            output=output,
            language=language,
            batch_size=max(1, args.batch_size),
            workers=max(1, args.workers),
            include_blocked=args.include_blocked,
            dry_run=args.dry_run,
            passes=max(1, args.passes),
        )
    except Exception:
        logger.exception("Retry failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
