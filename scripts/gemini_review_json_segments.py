#!/usr/bin/env python3
"""
Review Apollo DS ``.json`` segments with Gemini and map each segment to raw text.

Inputs:
  1. ``--folder`` — directory of Apollo DS JSON files
  2. ``--raw`` — JSONL of uncleaned paragraphs (``paragraph`` field)

For each segment, write one CSV row:
  script_id          — from ``file_map`` / ``data.file_name`` without ``.txt``
  segment_index      — segment index from the JSON
  text               — cleaned segment text (``segments[].text``)
  raw_text           — corresponding span from the matched raw paragraph;
                       ``<segment not found>`` if the script/segment cannot
                       be mapped to raw
  verdict            — keep / delete / review
  verdict_description — short reason

Acceptance criteria (prompted to Gemini):
  Coherence, contextuality, and semantics of the sentence (segment).
  The full script is provided as context. Raw mapping failures do **not**
  skip the review — the cleaned segment is still judged.

Batching (``--batch-size``, default 12):
  Segments are packed into Gemini calls (preferring same-script groups so
  ``script_context`` is not repeated). Batches run in parallel via ``--workers``.

Example:
  .venv/bin/python scripts/gemini_review_json_segments.py \\
    --folder ~/Downloads/Apollo_dataset/punjabi \\
    --raw raw_data/punjabi_validated_cleaned.jsonl \\
    --output unused_scripts/punjabi_segments_gemini_review.csv \\
    --batch-size 12 --workers 8
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
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

RAW_NOT_FOUND = "<segment not found>"

_PREFIX_LEN = 48
_PREFIX_STEP = 24
_FUZZY_TOP_K = 12
_FUZZY_EDGE_TOKENS = 24
_FUZZY_MIN_TOKEN_HITS = 3
_FUZZY_DEFAULT_CUTOFF = 0.88
_DEFAULT_BATCH_SIZE = 12
_SCRIPT_CHAR_LIMIT = 6000
_SEGMENT_CHAR_LIMIT = 2500

_VERDICTS = {"keep", "delete", "review"}

_SYSTEM = (
    "You review sentence/segments from speech/TTS dataset scripts.\n"
    "Acceptance criteria: coherence of each sentence, contextuality within "
    "its script, and semantics of the sentence.\n"
    "Decide one verdict per segment:\n"
    "- keep: coherent, contextual, and semantically sound\n"
    "- delete: incoherent, out of context, nonsense, or unsuitable for TTS\n"
    "- review: borderline / uncertain; needs human review\n"
    "Use each script_context. If raw_text is missing "
    f"({RAW_NOT_FOUND!r}), still judge the cleaned segment text alone.\n"
    "You will receive one or more scripts, each with segments. Return JSON only:\n"
    '{"results":[{"script_id":"<id>","segment_index":<int>,'
    '"verdict":"keep|delete|review",'
    '"verdict_description":"<one or two sentences>"}, ...]}\n'
    "Return exactly one result per input segment. No extra fields."
)


def _normalize(text: str) -> str:
    return " ".join((text or "").split())


def _edge_tokens(norm: str, n: int = _FUZZY_EDGE_TOKENS) -> set[str]:
    tokens = norm.split()
    if not tokens:
        return set()
    return {t for t in tokens[:n] + tokens[-n:] if len(t) >= 2}


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
        if "verdict" in parsed:
            return [parsed]
    return []


def script_id_from_json(obj: dict[str, Any]) -> str:
    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
    file_name = str(data.get("file_name") or "").strip()
    if file_name:
        return Path(file_name).stem

    file_map = obj.get("file_map")
    if isinstance(file_map, dict) and file_map:
        for value in file_map.values():
            name = str(value or "").strip()
            if name:
                return Path(name).stem

    if obj.get("id"):
        return str(obj["id"])
    return ""


def extract_segments(obj: dict[str, Any]) -> list[dict[str, Any]]:
    data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
    segments = data.get("segments")
    out: list[dict[str, Any]] = []
    if isinstance(segments, list) and segments:
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            idx = seg.get("segment_index", i)
            out.append({"segment_index": idx, "text": text})
        return out

    flat: list[dict[str, Any]] = []
    for key, value in data.items():
        if not key.startswith("segment_") or key == "segment_count":
            continue
        suffix = key[len("segment_") :]
        if not suffix.isdigit():
            continue
        text = str(value or "").strip()
        if not text:
            continue
        flat.append({"segment_index": int(suffix) - 1, "text": text})
    flat.sort(key=lambda s: s["segment_index"])
    return flat


def load_json_records(folder: Path, *, limit: int | None) -> list[dict[str, Any]]:
    files = sorted(folder.glob("*.json"))
    if not files:
        files = sorted(folder.rglob("*.json"))
    if not files:
        raise FileNotFoundError(f"No .json files found under {folder}")
    if limit is not None:
        files = files[:limit]

    records: list[dict[str, Any]] = []
    for path in files:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            continue
        data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
        script = str(data.get("script") or data.get("paragraph") or "").strip()
        script_id = script_id_from_json(obj)
        segments = extract_segments(obj)
        if not script_id or not segments:
            logger.warning("Skipping %s (missing script_id or segments)", path.name)
            continue
        if not script:
            script = " ".join(seg["text"] for seg in segments)
        records.append(
            {
                "source_path": str(path),
                "script_id": script_id,
                "script": script,
                "segments": segments,
            }
        )
    return records


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
) -> dict[str, Any] | None:
    script_norm = _normalize(script)
    if not script_norm:
        return None

    exact = by_norm.get(script_norm)
    if exact:
        return exact[0]

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
        return substring_hits[0][1]

    if not fuzzy:
        return None

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
        return None

    shortlist = [
        id_to_row[rid]
        for rid, hits in overlap.most_common(_FUZZY_TOP_K)
        if hits >= _FUZZY_MIN_TOKEN_HITS
    ]
    if not shortlist:
        return None

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
        return best_row
    return None


def find_raw_segment(raw_paragraph: str, segment: str) -> str | None:
    """Locate *segment* inside *raw_paragraph*; return the raw span text."""
    if not raw_paragraph or not segment:
        return None

    exact = raw_paragraph.find(segment)
    if exact >= 0:
        return raw_paragraph[exact : exact + len(segment)]

    seg_norm = _normalize(segment)
    para_norm = _normalize(raw_paragraph)
    if seg_norm and seg_norm in para_norm:
        tokens = seg_norm.split()
        if tokens:
            pattern = r"\s+".join(re.escape(tok) for tok in tokens)
            match = re.search(pattern, raw_paragraph)
            if match:
                return match.group(0)

    words = raw_paragraph.split()
    seg_words = segment.split()
    if not words or not seg_words:
        return None
    window = max(1, len(seg_words))
    best = ""
    best_ratio = 0.0
    step = max(1, window // 4)
    for i in range(0, max(1, len(words) - window + 1), step):
        chunk = " ".join(words[i : i + window + 2])
        ratio = SequenceMatcher(None, _normalize(segment), _normalize(chunk)).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = chunk
    if best_ratio >= 0.75:
        return best
    return None


def build_segment_batches(
    pending: list[dict[str, Any]],
    batch_size: int,
) -> list[list[dict[str, Any]]]:
    """
    Pack segments into batches.

    Prefer keeping same-script segments together so script_context is shared
    once per group, then fill remaining capacity with the next script.
    """
    by_script: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for item in pending:
        sid = item["script_id"]
        if sid not in by_script:
            by_script[sid] = []
            order.append(sid)
        by_script[sid].append(item)

    # Split oversized scripts first.
    groups: list[list[dict[str, Any]]] = []
    for sid in order:
        groups.extend(_chunked(by_script[sid], batch_size))

    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for group in groups:
        if current and len(current) + len(group) > batch_size:
            batches.append(current)
            current = []
        current.extend(group)
        if len(current) >= batch_size:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def review_segment_batch(
    batch: list[dict[str, Any]],
    language: str,
) -> list[dict[str, Any]]:
    """Review a packed batch of segments in one LLM call."""
    by_script: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in batch:
        sid = item["script_id"]
        if sid not in by_script:
            by_script[sid] = {
                "script_id": sid,
                "script_context": item["script"][:_SCRIPT_CHAR_LIMIT],
                "segments": [],
            }
            order.append(sid)
        by_script[sid]["segments"].append(
            {
                "segment_index": item["segment_index"],
                "text": item["text"][:_SEGMENT_CHAR_LIMIT],
                "raw_text": item["raw_text"][:_SEGMENT_CHAR_LIMIT],
            }
        )

    payload = {
        "language": language,
        "scripts": [by_script[sid] for sid in order],
    }
    call = _call_llm_detailed(_SYSTEM, json.dumps(payload, ensure_ascii=False))

    def _error_rows(message: str) -> list[dict[str, Any]]:
        return [
            {
                "script_id": item["script_id"],
                "segment_index": item["segment_index"],
                "text": item["text"],
                "raw_text": item["raw_text"],
                "verdict": "review",
                "verdict_description": message,
            }
            for item in batch
        ]

    if call.error and not call.text:
        return _error_rows(f"LLM error: {call.error}")

    parsed, parse_error = _parse_json_response_detailed(call.text)
    results = _extract_results_list(parsed)
    if parse_error or not results:
        return _error_rows(
            f"LLM parse error: {parse_error or call.error or 'empty results'}"
        )

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in results:
        sid = str(item.get("script_id") or "").strip()
        idx = item.get("segment_index")
        if sid == "" or idx is None:
            continue
        by_key[(sid, str(idx))] = item

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(batch):
        key = (item["script_id"], str(item["segment_index"]))
        result = by_key.get(key)
        if result is None and index < len(results):
            result = results[index]
        if result is None:
            rows.append(
                {
                    "script_id": item["script_id"],
                    "segment_index": item["segment_index"],
                    "text": item["text"],
                    "raw_text": item["raw_text"],
                    "verdict": "review",
                    "verdict_description": "LLM batch missing this segment",
                }
            )
            continue
        rows.append(
            {
                "script_id": item["script_id"],
                "segment_index": item["segment_index"],
                "text": item["text"],
                "raw_text": item["raw_text"],
                "verdict": _normalize_verdict(result.get("verdict")),
                "verdict_description": str(
                    result.get("verdict_description")
                    or result.get("description")
                    or result.get("reason")
                    or ""
                ).strip(),
            }
        )
    return rows


def process(
    folder: Path,
    raw_path: Path,
    *,
    output: Path,
    language: str,
    workers: int,
    batch_size: int,
    limit: int | None,
    fuzzy: bool,
    fuzzy_cutoff: float,
    dry_run: bool,
) -> dict[str, Any]:
    records = load_json_records(folder, limit=limit)
    logger.info("Loaded %d JSON scripts from %s", len(records), folder)

    raw_rows = load_raw_paragraphs(raw_path)
    by_norm, by_prefix, by_token = build_raw_indexes(raw_rows)
    logger.info("Indexed %d raw paragraphs from %s", len(raw_rows), raw_path)

    pending: list[dict[str, Any]] = []
    scripts_matched = 0
    scripts_unmatched = 0
    segments_raw_found = 0
    segments_raw_missing = 0

    for record in records:
        raw_row = match_raw_paragraph(
            record["script"],
            by_norm,
            by_prefix,
            by_token,
            fuzzy=fuzzy,
            fuzzy_cutoff=fuzzy_cutoff,
        )
        if raw_row is None:
            scripts_unmatched += 1
            raw_paragraph = ""
        else:
            scripts_matched += 1
            raw_paragraph = raw_row["paragraph"]

        for seg in record["segments"]:
            if raw_paragraph:
                raw_seg = find_raw_segment(raw_paragraph, seg["text"])
            else:
                raw_seg = None
            if raw_seg:
                segments_raw_found += 1
                raw_text = raw_seg
            else:
                segments_raw_missing += 1
                raw_text = RAW_NOT_FOUND
            pending.append(
                {
                    "script_id": record["script_id"],
                    "segment_index": seg["segment_index"],
                    "text": seg["text"],
                    "raw_text": raw_text,
                    "script": record["script"],
                }
            )

    logger.info(
        "Mapped scripts matched=%d unmatched=%d; segments raw_found=%d raw_missing=%d",
        scripts_matched,
        scripts_unmatched,
        segments_raw_found,
        segments_raw_missing,
    )

    batches = build_segment_batches(pending, batch_size)
    rows: list[dict[str, Any]] = []

    if dry_run:
        rows = [
            {
                "script_id": item["script_id"],
                "segment_index": item["segment_index"],
                "text": item["text"],
                "raw_text": item["raw_text"],
                "verdict": "",
                "verdict_description": "",
            }
            for item in pending
        ]
    elif workers <= 1:
        done_segs = 0
        for batch in batches:
            rows.extend(review_segment_batch(batch, language))
            done_segs += len(batch)
            logger.info(
                "Reviewed %d/%d segments (%d batches)",
                done_segs,
                len(pending),
                len(batches),
            )
    else:
        logger.info(
            "Reviewing %d segments in %d batches (batch_size=%d, workers=%d)",
            len(pending),
            len(batches),
            batch_size,
            workers,
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(review_segment_batch, batch, language): i
                for i, batch in enumerate(batches)
            }
            tmp: list[list[dict[str, Any]] | None] = [None] * len(batches)
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
    fieldnames = [
        "script_id",
        "segment_index",
        "text",
        "raw_text",
        "verdict",
        "verdict_description",
    ]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    verdict_counts: dict[str, int] = {}
    for row in rows:
        key = row.get("verdict") or "unset"
        verdict_counts[key] = verdict_counts.get(key, 0) + 1

    summary = {
        "folder": str(folder),
        "raw": str(raw_path),
        "output": str(output),
        "language": language,
        "scripts": len(records),
        "scripts_matched_raw": scripts_matched,
        "scripts_unmatched_raw": scripts_unmatched,
        "segments": len(rows),
        "segments_raw_found": segments_raw_found,
        "segments_raw_missing": segments_raw_missing,
        "batch_size": batch_size,
        "batches": len(batches),
        "verdict_counts": verdict_counts,
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
        description=(
            "Gemini-review Apollo DS JSON segments, mapping each to raw text "
            "via fuzzy search against a raw JSONL."
        )
    )
    parser.add_argument(
        "--folder",
        type=Path,
        required=True,
        help="Folder containing Apollo DS .json files.",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        required=True,
        help="Raw JSONL with uncleaned paragraph fields.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: {folder_name}_segments_gemini_review.csv).",
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
        help=f"Segments per Gemini call (default: {_DEFAULT_BATCH_SIZE}).",
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
        help="Only process the first N JSON files (testing).",
    )
    parser.add_argument(
        "--no-fuzzy",
        action="store_true",
        help="Disable fuzzy raw matching (exact + substring only).",
    )
    parser.add_argument(
        "--fuzzy-cutoff",
        type=float,
        default=_FUZZY_DEFAULT_CUTOFF,
        help=f"Fuzzy match cutoff (default: {_FUZZY_DEFAULT_CUTOFF}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Map raw segments and write CSV with empty verdicts (no LLM).",
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
    raw_path = args.raw.expanduser().resolve()
    if not folder.is_dir():
        logger.error("Folder not found: %s", folder)
        return 1
    if not raw_path.is_file():
        logger.error("Raw JSONL not found: %s", raw_path)
        return 1

    configure_llm_provider(args.llm_provider)
    if not args.dry_run and not llm_available():
        logger.error("%s", llm_api_key_hint())
        return 1

    output = (
        args.output.expanduser().resolve()
        if args.output
        else folder.parent / f"{folder.name}_segments_gemini_review.csv"
    )
    language = args.language or folder.name

    try:
        process(
            folder,
            raw_path,
            output=output,
            language=language,
            workers=max(1, args.workers),
            batch_size=max(1, args.batch_size),
            limit=args.limit,
            fuzzy=not args.no_fuzzy,
            fuzzy_cutoff=args.fuzzy_cutoff,
            dry_run=args.dry_run,
        )
    except Exception:
        logger.exception("Failed reviewing JSON segments")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
