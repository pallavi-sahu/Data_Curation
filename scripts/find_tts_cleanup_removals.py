#!/usr/bin/env python3
"""
Match cleaned dataset scripts/segments back to raw JSONL paragraphs and report
tokens removed by TTS cleanup, grouped by segment.

Input 1 — raw JSONL with a ``paragraph`` field (e.g. ``*_validated_cleaned.jsonl``).
Input 2 — cleaned dataset: one DS JSON file, a directory of DS JSON files, or a
JSONL file whose records contain ``script`` / ``paragraph`` plus ``segments``.

Outputs (derived from ``--output`` stem):
  1. ``{stem}.jsonl`` — removals:
       {"tamil_script_0190": {"0": ["1858", "1947"], "2": ["30,000"]}}
  2. ``{stem}_accepted_rejected.jsonl`` — per script:
       {"tamil_script_0190": {"Accepted": [3, 5], "Rejected": [0, 1, 2]}}
  3. ``{stem}_script_stats.json`` — script-level counts:
       {"scripts_edited": N, "scripts_untouched": M, "scripts_total": N+M}

Matching (fast path; ``--fuzzy`` is rarely needed and off by default):
  1. Exact normalized ``paragraph`` == ``script``
  2. Prefix-index substring match (script chunk inside a raw paragraph)
  3. Optional bounded fuzzy (token-overlap shortlist, then ratio on top-K only)

Unmatched scripts still get cleanup-based removals — skip ``--fuzzy`` unless
exact/substring match rate is poor for the *same language* raw file.

Removals:
  - If matched raw differs from ``script``, diff raw → cleaned/script.
  - Otherwise apply ``clean_tts_text`` to the script and attribute deleted
    words to overlapping segments.

Example:
  python3 scripts/find_tts_cleanup_removals.py \\
    --raw raw_data/tamil_validated_cleaned.jsonl \\
    --cleaned ~/Downloads/Apollo_dataset/tamil/ \\
    --output Removal/tamil.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

_FUZZY_TOP_K = 12
_FUZZY_EDGE_TOKENS = 24
_FUZZY_MIN_TOKEN_HITS = 3
_FUZZY_DEFAULT_CUTOFF = 0.88

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import importlib.util


def _load_tts_cleanup():
    path = _REPO_ROOT / "validation" / "tts_cleanup.py"
    spec = importlib.util.spec_from_file_location("tts_cleanup", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tts_cleanup"] = module
    spec.loader.exec_module(module)
    return module


_tts = _load_tts_cleanup()
clean_tts_text = _tts.clean_tts_text

logger = logging.getLogger(__name__)

_PREFIX_LEN = 48
_PREFIX_STEP = 24


@lru_cache(maxsize=8192)
def _cached_clean_tts(text: str) -> str:
    cleaned, _ = clean_tts_text(text)
    return cleaned


def _normalize(text: str) -> str:
    return " ".join((text or "").split())


def _script_id_from_record(record: dict[str, Any], fallback: str) -> str:
    file_name = record.get("file_name") or record.get("script_id") or ""
    if file_name:
        return Path(str(file_name)).stem
    if record.get("id"):
        return str(record["id"])
    return fallback


def _extract_segments(data: dict[str, Any]) -> list[dict[str, Any]]:
    segments = data.get("segments")
    if isinstance(segments, list) and segments:
        out: list[dict[str, Any]] = []
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "")
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
        text = str(value or "")
        if not text.strip():
            continue
        flat.append({"segment_index": int(suffix) - 1, "text": text})
    flat.sort(key=lambda s: s["segment_index"])
    return flat


def _iter_cleaned_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield normalized records: script_id, script, segments, source_path."""
    if path.is_dir():
        files = sorted(path.glob("*.json"))
        if not files:
            raise FileNotFoundError(f"No .json files in {path}")
        for file_path in files:
            yield from _iter_cleaned_records(file_path)
        return

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return

    if path.suffix == ".jsonl" or (
        "\n" in text and not text.lstrip().startswith("{")
    ):
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            yield _normalize_cleaned_obj(obj, path, fallback=f"{path.stem}_{line_no}")
        return

    obj = json.loads(text)

    if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], dict):
        data = dict(obj["data"])
        if "id" not in data and obj.get("id"):
            data["id"] = obj["id"]
        yield _normalize_cleaned_obj(data, path, fallback=path.stem)
        return

    if isinstance(obj, list):
        for i, item in enumerate(obj):
            yield _normalize_cleaned_obj(item, path, fallback=f"{path.stem}_{i}")
        return

    if isinstance(obj, dict):
        yield _normalize_cleaned_obj(obj, path, fallback=path.stem)
        return

    raise ValueError(f"Unsupported cleaned dataset format: {path}")


def _normalize_cleaned_obj(
    obj: dict[str, Any],
    path: Path,
    *,
    fallback: str,
) -> dict[str, Any]:
    script = str(obj.get("script") or obj.get("paragraph") or "")
    segments = _extract_segments(obj)
    if not segments and script:
        segments = [{"segment_index": 0, "text": script}]
    return {
        "script_id": _script_id_from_record(obj, fallback),
        "script": script,
        "segments": segments,
        "source_path": str(path),
    }


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
                    "source": record.get("source"),
                    "n_words": len(paragraph.split()),
                }
            )
    return rows


def _edge_tokens(norm: str, n: int = _FUZZY_EDGE_TOKENS) -> set[str]:
    tokens = norm.split()
    if not tokens:
        return set()
    return {t for t in tokens[:n] + tokens[-n:] if len(t) >= 2}


def build_raw_indexes(
    rows: list[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    """Exact-norm, sliding-prefix, and edge-token indexes."""
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
    rows: list[dict[str, Any]],
    *,
    fuzzy: bool = False,
    fuzzy_cutoff: float = _FUZZY_DEFAULT_CUTOFF,
) -> dict[str, Any] | None:
    script_norm = _normalize(script)
    if not script_norm:
        return None

    exact = by_norm.get(script_norm)
    if exact:
        return exact[0]

    # Fast substring candidates via prefix index
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

    # Bounded fuzzy: score only top-_FUZZY_TOP_K rows by shared edge-token count.
    # Cap posting walks so common tokens cannot explode runtime.
    script_words = len(script_norm.split())
    script_toks = _edge_tokens(script_norm)
    overlap: Counter[int] = Counter()
    id_to_row: dict[int, dict[str, Any]] = {}
    posting_budget = 5000
    for token in script_toks:
        postings = by_token.get(token, ())
        # Prefer rarer tokens (shorter posting lists)
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
    # Compare short prefixes only — full-paragraph SequenceMatcher is O(n*m).
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


def segment_char_spans(
    script: str, segments: list[dict[str, Any]]
) -> list[tuple[Any, int, int]]:
    spans: list[tuple[Any, int, int]] = []
    pos = 0
    for seg in segments:
        text = seg["text"]
        idx = script.find(text, pos)
        if idx < 0:
            idx = script.find(text)
        if idx < 0:
            if spans:
                idx = spans[-1][2]
                while idx < len(script) and script[idx].isspace():
                    idx += 1
            else:
                idx = 0
            end = min(len(script), idx + len(text))
        else:
            end = idx + len(text)
        spans.append((seg["segment_index"], idx, end))
        pos = end
    return spans


def word_char_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    pos = 0
    for word in text.split():
        idx = text.find(word, pos)
        if idx < 0:
            idx = pos
        spans.append((idx, idx + len(word), word))
        pos = idx + len(word)
    return spans


def removed_tokens_by_word_diff(before: str, after: str) -> list[tuple[int, int, str]]:
    before_words = before.split()
    after_words = after.split()
    before_spans = word_char_spans(before)
    removed: list[tuple[int, int, str]] = []
    matcher = SequenceMatcher(a=before_words, b=after_words, autojunk=False)
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag not in ("delete", "replace"):
            continue
        for word_index in range(i1, i2):
            start, end, word = before_spans[word_index]
            removed.append((start, end, word))
    return removed


def attribute_removals_to_segments(
    removed: list[tuple[int, int, str]],
    seg_spans: list[tuple[Any, int, int]],
) -> dict[str, list[str]]:
    by_seg: dict[str, list[str]] = {}
    for start, end, word in removed:
        assigned: Any | None = None
        for seg_index, seg_start, seg_end in seg_spans:
            if start < seg_end and end > seg_start:
                assigned = seg_index
                break
        if assigned is None and seg_spans:
            assigned = min(seg_spans, key=lambda s: abs(s[1] - start))[0]
        if assigned is None:
            continue
        by_seg.setdefault(str(assigned), []).append(word)
    return by_seg


def choose_before_after(script: str, raw_paragraph: str | None) -> tuple[str, str, str]:
    script_norm = _normalize(script)
    if raw_paragraph:
        raw_norm = _normalize(raw_paragraph)
        if script_norm == raw_norm:
            return script, _cached_clean_tts(script), "apply_cleanup"
        if script in raw_paragraph:
            return script, _cached_clean_tts(script), "apply_cleanup"

        cleaned_raw = _cached_clean_tts(raw_paragraph)
        cleaned_norm = _normalize(cleaned_raw)
        if script in cleaned_raw or script_norm == cleaned_norm:
            return raw_paragraph, cleaned_raw, "raw_vs_cleaned"
        # Avoid expensive full SequenceMatcher; length+prefix gate is enough
        if abs(len(script_norm) - len(raw_norm)) / max(len(raw_norm), 1) < 0.35:
            if script_norm[:32] == raw_norm[:32] or script_norm[-32:] == raw_norm[-32:]:
                return raw_paragraph, script, "raw_vs_script"

    return script, _cached_clean_tts(script), "apply_cleanup"


def map_raw_removals_onto_script_segments(
    raw_paragraph: str,
    script: str,
    segments: list[dict[str, Any]],
) -> dict[str, list[str]]:
    cleaned_raw = _cached_clean_tts(raw_paragraph)
    if script in cleaned_raw:
        before = raw_paragraph
        after = cleaned_raw
        after_spans = word_char_spans(after)
        before_words = before.split()
        after_words = after.split()
        matcher = SequenceMatcher(a=before_words, b=after_words, autojunk=False)
        script_start = after.find(script)
        script_end = script_start + len(script)
        seg_spans = segment_char_spans(script, segments)
        by_seg: dict[str, list[str]] = {}

        for tag, i1, i2, j1, _j2 in matcher.get_opcodes():
            if tag in ("equal", "insert"):
                continue
            if j1 < len(after_spans):
                anchor_char = after_spans[j1][0]
            elif after_spans:
                anchor_char = after_spans[-1][1]
            else:
                anchor_char = 0
            if not (script_start <= anchor_char <= script_end):
                if anchor_char < script_start - 40 or anchor_char > script_end + 40:
                    continue
            local_char = max(0, min(len(script), anchor_char - script_start))
            removed_words = before_words[i1:i2]
            assigned = None
            for seg_index, seg_start, seg_end in seg_spans:
                if seg_start <= local_char <= seg_end:
                    assigned = seg_index
                    break
            if assigned is None and seg_spans:
                assigned = min(seg_spans, key=lambda s: abs(s[1] - local_char))[0]
            if assigned is not None:
                by_seg.setdefault(str(assigned), []).extend(removed_words)
        return by_seg

    removed = removed_tokens_by_word_diff(raw_paragraph, script)
    if not raw_paragraph:
        return {}
    scale = len(script) / max(len(raw_paragraph), 1)
    remapped = [
        (int(start * scale), int(end * scale), word) for start, end, word in removed
    ]
    return attribute_removals_to_segments(remapped, segment_char_spans(script, segments))


def accepted_rejected_for_script(
    segments: list[dict[str, Any]],
    removals: dict[str, list[str]],
) -> dict[str, list[Any]]:
    rejected: list[Any] = []
    accepted: list[Any] = []
    for seg in segments:
        idx = seg["segment_index"]
        key = str(idx)
        if removals.get(key):
            rejected.append(idx)
        else:
            accepted.append(idx)
    return {"Accepted": accepted, "Rejected": rejected}


def find_removals_for_record(
    record: dict[str, Any],
    by_norm: dict[str, list[dict[str, Any]]],
    by_prefix: dict[str, list[dict[str, Any]]],
    by_token: dict[str, list[dict[str, Any]]],
    rows: list[dict[str, Any]],
    *,
    fuzzy: bool,
) -> dict[str, Any]:
    script = record["script"]
    segments = record["segments"]
    matched = match_raw_paragraph(
        script, by_norm, by_prefix, by_token, rows, fuzzy=fuzzy
    )
    raw_paragraph = matched["paragraph"] if matched else None

    before, after, mode = choose_before_after(script, raw_paragraph)

    if mode in ("raw_vs_cleaned", "raw_vs_script") and raw_paragraph is not None:
        removals = map_raw_removals_onto_script_segments(
            raw_paragraph, script, segments
        )
    else:
        removed = removed_tokens_by_word_diff(before, after)
        removals = attribute_removals_to_segments(
            removed, segment_char_spans(before, segments)
        )

    verdict = accepted_rejected_for_script(segments, removals)
    return {
        "script_id": record["script_id"],
        "removals": removals,
        "accepted_rejected": verdict,
        "match_line_number": matched["line_number"] if matched else None,
        "match_mode": mode,
        "matched": matched is not None,
        "removal_token_count": sum(len(v) for v in removals.values()),
        "segments_with_removals": len(removals),
        "script_edited": bool(removals),
    }


# --- multiprocessing workers need top-level picklable state ---
_WORKER_STATE: dict[str, Any] = {}


def _init_worker(
    by_norm: dict[str, list[dict[str, Any]]],
    by_prefix: dict[str, list[dict[str, Any]]],
    by_token: dict[str, list[dict[str, Any]]],
    rows: list[dict[str, Any]],
    fuzzy: bool,
) -> None:
    _WORKER_STATE["by_norm"] = by_norm
    _WORKER_STATE["by_prefix"] = by_prefix
    _WORKER_STATE["by_token"] = by_token
    _WORKER_STATE["rows"] = rows
    _WORKER_STATE["fuzzy"] = fuzzy


def _process_record_worker(record: dict[str, Any]) -> dict[str, Any]:
    return find_removals_for_record(
        record,
        _WORKER_STATE["by_norm"],
        _WORKER_STATE["by_prefix"],
        _WORKER_STATE["by_token"],
        _WORKER_STATE["rows"],
        fuzzy=_WORKER_STATE["fuzzy"],
    )


def output_paths(output: Path) -> tuple[Path, Path, Path]:
    stem = output.with_suffix("")
    removals_path = output
    accepted_path = Path(f"{stem}_accepted_rejected.jsonl")
    stats_path = Path(f"{stem}_script_stats.json")
    return removals_path, accepted_path, stats_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Match cleaned dataset segments to raw JSONL paragraphs and list "
            "tokens removed by TTS cleanup per segment."
        )
    )
    parser.add_argument(
        "--raw",
        type=Path,
        required=True,
        help="Raw JSONL file with a paragraph field.",
    )
    parser.add_argument(
        "--cleaned",
        type=Path,
        required=True,
        help="Cleaned dataset file or directory of DS JSON files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Primary removals JSONL path (other outputs are derived from this stem).",
    )
    parser.add_argument(
        "--fuzzy",
        action="store_true",
        help=(
            "Enable bounded fuzzy matching (token shortlist + top-K ratio). "
            "Usually unnecessary: unmatched scripts still get cleanup removals. "
            "Do NOT use with a mismatched language --raw file."
        ),
    )
    parser.add_argument(
        "--no-fuzzy",
        action="store_true",
        help=argparse.SUPPRESS,  # kept for backward compatibility
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Process-pool workers for parallel script processing (default: 1).",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Write scripts even when no removals were found.",
    )
    parser.add_argument(
        "--with-meta",
        action="store_true",
        help="Include match metadata alongside the script_id → removals object.",
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
        format="%(levelname)s %(message)s",
    )

    if not args.raw.is_file():
        logger.error("Raw file not found: %s", args.raw)
        return 1
    if not args.cleaned.exists():
        logger.error("Cleaned path not found: %s", args.cleaned)
        return 1

    fuzzy = bool(args.fuzzy) and not args.no_fuzzy
    if fuzzy:
        logger.warning(
            "--fuzzy is enabled: matching is bounded but still slower. "
            "Prefer omitting --fuzzy unless same-language exact/substring "
            "match rate is poor. Unmatched scripts still get cleanup removals."
        )

    rows = load_raw_paragraphs(args.raw)
    by_norm, by_prefix, by_token = build_raw_indexes(rows)
    if not fuzzy:
        # Avoid pickling a large inverted index when fuzzy is unused.
        by_token = {}
    logger.info("Loaded %d raw paragraphs from %s", len(rows), args.raw)

    cleaned_records = list(_iter_cleaned_records(args.cleaned))
    logger.info("Loaded %d cleaned scripts from %s", len(cleaned_records), args.cleaned)

    removals_path, accepted_path, stats_path = output_paths(args.output)
    removals_path.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    workers = max(1, args.workers)

    if workers == 1:
        for i, record in enumerate(cleaned_records, start=1):
            results.append(
                find_removals_for_record(
                    record, by_norm, by_prefix, by_token, rows, fuzzy=fuzzy
                )
            )
            if i % 200 == 0 or i == len(cleaned_records):
                logger.info("Processed %d/%d scripts", i, len(cleaned_records))
    else:
        logger.info("Processing with %d workers", workers)
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_worker,
            initargs=(by_norm, by_prefix, by_token, rows, fuzzy),
        ) as pool:
            futures = [
                pool.submit(_process_record_worker, record)
                for record in cleaned_records
            ]
            done = 0
            for fut in as_completed(futures):
                results.append(fut.result())
                done += 1
                if done % 200 == 0 or done == len(cleaned_records):
                    logger.info("Processed %d/%d scripts", done, len(cleaned_records))

    # Stable order by script_id for reproducible outputs
    results.sort(key=lambda r: r["script_id"])

    written = 0
    matched = 0
    with_removals = 0
    total_tokens = 0
    scripts_edited = 0
    scripts_untouched = 0

    with removals_path.open("w", encoding="utf-8") as out_rem, accepted_path.open(
        "w", encoding="utf-8"
    ) as out_acc:
        for result in results:
            if result["matched"]:
                matched += 1

            removals = result["removals"]
            if result["script_edited"]:
                scripts_edited += 1
            else:
                scripts_untouched += 1

            # Always write accepted/rejected for every script
            out_acc.write(
                json.dumps(
                    {result["script_id"]: result["accepted_rejected"]},
                    ensure_ascii=False,
                )
                + "\n"
            )

            if not removals and not args.include_empty:
                continue

            if args.with_meta:
                payload: dict[str, Any] = {
                    "script_id": result["script_id"],
                    "removals": removals,
                    "match_line_number": result["match_line_number"],
                    "match_mode": result["match_mode"],
                    "matched": result["matched"],
                    "removal_token_count": result["removal_token_count"],
                }
            else:
                payload = {result["script_id"]: removals}
            out_rem.write(json.dumps(payload, ensure_ascii=False) + "\n")
            written += 1
            if removals:
                with_removals += 1
                total_tokens += result["removal_token_count"]

    stats = {
        "scripts_edited": scripts_edited,
        "scripts_untouched": scripts_untouched,
        "scripts_total": scripts_edited + scripts_untouched,
        "scripts_matched_to_raw": matched,
        "removal_rows_written": written,
        "segments_with_removals_scripts": with_removals,
        "removal_tokens_total": total_tokens,
    }
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    logger.info(
        "Wrote removals=%s (%d rows), accepted/rejected=%s, stats=%s "
        "(matched=%d/%d, edited=%d, untouched=%d, tokens=%d)",
        removals_path,
        written,
        accepted_path,
        stats_path,
        matched,
        len(cleaned_records),
        scripts_edited,
        scripts_untouched,
        total_tokens,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
