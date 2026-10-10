#!/usr/bin/env python3
"""Explode a segmented script CSV into one row per segment and, for each segment,
ask Gemini for a data-cleanup verdict (keep / delete / review).

Input CSV layout (as produced upstream):
    id, script, sentence1, sentence2, ... sentenceN

For every ``sentenceX`` cell that is non-empty a row is emitted in the output CSV
with the columns:

    Language | script number/ID | Segments/scripts | verdict | why verdict |
    character/number category | if the numerics, numbers are contextual or not |
    cleaned segment (retain)

The verdict follows this cleanup policy (decided by Gemini per segment):
  1. Cleanup does NOT change the meaning -> keep the sentence, strip only the
     junk tokens ("Keep").
  2. Characters are complex AND change/destroy the meaning -> drop the whole
     sentence ("Delete").
  3. Characters are complex but do NOT change the meaning -> keep the sentence
     but remove just those tokens ("Keep", with cleaned text).
  4. Model is unsure -> "Review" (needs a human).

Only ordinal / everyday-conversational (contextual) numbers are retained; other
numerics and stray special characters are treated as removable.

Usage:
    # .env: GEMINI_API_KEY=your_key_here
    python segment_cleanup_verdict.py cleaned_scripts/manipuri_cleaned_tts_new_check.csv

The script uses Gemini's asynchronous Batch API (discounted pricing). It saves
the submitted job ID next to the output, so re-running safely resumes polling
and downloads the results when the job finishes.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

# Some scripts have very long single cells; lift the CSV field-size cap.
csv.field_size_limit(sys.maxsize)

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_BATCH = 10

OUTPUT_HEADER = [
    "Language",
    "script number/ID",
    "Segments/scripts",
    "verdict",
    "why verdict",
    "character/number category",
    "if the numerics, numbers are contextual or not",
    "cleaned segment (retain)",
]

# Guidance the model uses to bucket the character / number content of a segment.
CATEGORY_GUIDE = """\
Common character/number categories (pick the closest, or coin a short one):
- None / Standard Conversational Text Numbers
- Ordinal numbers (1st, 2nd, dates, chapters)
- Years / calendar dates
- Ratios and map scales / Scores
- Measurements and units (km, kg, area, temperature)
- Currency / money amounts
- Large statistical figures (population, counts)
- Coordinates (latitude / longitude)
- Abbreviations / acronyms with dots (e.g. N.E.I.S.T.)
- OCR noise / broken tokens / stray symbols
- Mathematical / scientific notation
- Reference / citation numbers, IDs, phone-like numbers"""

PROMPT_TEMPLATE = """\
You are running a data-cleanup pass over {language} text segments.

GOAL: produce text that is ready to be read aloud in a speech/TTS task. Remove
non-contextual numerics and stray special characters, but KEEP ordinal /
everyday-conversational numbers only when they are natural and easy to say.

ALWAYS strip these prefixes before judging the segment (do this first):
- Leading timestamps such as "1:00", "01:05", "1:00:30", optionally followed by
  a dash/en-dash/em-dash/colon and spaces (e.g. "1:00 – बेसी मरीजक दौरा।" ->
  "बेसी मरीजक दौरा।"). Remove only the timestamp prefix; keep the rest.
- Leading slide labels like "स्लाइड <number>:" / "स्लाइड <number> :" /
  "Slide <number>:" (any spaces around the number/colon). Remove only that
  prefix; keep the rest of the sentence.
- Leading serial/list numbers such as "4)", "4.", "(4)", "4 )", "4 .",
  "( 4 )", or similar with Arabic or Indic digits, optionally followed by
  spaces. Remove only the serial-number prefix; keep the rest of the sentence.
These prefixes alone are NOT a reason to Delete. After stripping them, evaluate
the remaining text with the policy below.

For EACH segment decide a verdict using this policy:
1. First judge the segment as spoken text. "Keep" only if the cleaned sentence
   is coherent, complete, natural, meaningful, and easy to pronounce aloud.
2. If cleanup does NOT change the meaning and the result passes that spoken-text
   test -> verdict "Keep" (strip only the junk tokens in the cleaned text).
3. If complex characters/tokens can be removed without changing meaning and the
   result passes the spoken-text test -> verdict "Keep" and remove only those
   tokens in the cleaned text.
4. If removing complex characters/tokens breaks or changes the meaning ->
   verdict "Delete" (drop the whole segment).
5. Use "Delete" for nonsensical, incoherent, incomplete, badly corrupted, or
   unnatural sentences that would not be good material for a read-speech task.
6. Use "Delete" when large/unwieldy numbers, long digit strings, statistics,
   identifiers, phone-like numbers, formulas, or similar content make the
   sentence difficult or unnatural to read aloud. Do not keep such a segment
   merely because the number is contextually meaningful.
7. Ordinary conversational numbers, small counts, ordinals, simple dates, and
   scores may be kept only when they are naturally spoken in the sentence.
8. If you are genuinely unsure and a human should look -> verdict "Review".

A segment that is mostly OCR garbage, isolated numbers/symbols, lacks real
sentence structure, does not make sense, or fails the spoken-text test must be
"Delete". Apply the spoken-text test after producing cleaned_segment as a final
gate: if cleaned_segment is not ready to read aloud, change the verdict to
"Delete" and return an empty cleaned_segment.

{category_guide}

Return ONLY a JSON array. One object per input segment, in the SAME order, with:
  "index": integer index of the segment (echo it back)
  "verdict": one of "Keep", "Delete", "Review"
  "why_verdict": short reason (one sentence)
  "category": the character/number category label
  "contextual": one of "Yes (Contextual)", "No (Non-contextual)", "N/A (no numbers)"
  "cleaned_segment": the segment text after first stripping leading timestamps /
      slide labels / serial numbers, then removing non-contextual numerics /
      junk tokens. For "Delete" use an empty string. For "Keep" with nothing
      else to remove, return the prefix-stripped sentence.

Segments (JSON):
{segments_json}
"""


def language_from_filename(path: Path) -> str:
    """`manipuri_cleaned_tts_new_check.csv` -> `Manipuri`."""
    stem = path.stem
    first = re.split(r"[_\-.]", stem)[0]
    return first.capitalize() if first else stem


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file without overriding the environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def iter_input_rows(path: Path):
    """Yield (script_id, seg_index, segment_text) for every non-empty segment.

    Duplicate segments within the same script id are skipped so each unique
    segment is emitted once.
    """
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        sentence_cols = [c for c in (reader.fieldnames or []) if c and c.lower().startswith("sentence")]
        for row in reader:
            script_id = (row.get("id") or "").strip()
            seen: set[str] = set()
            seg_index = 0
            for col in sentence_cols:
                text = (row.get(col) or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                seg_index += 1
                yield script_id, seg_index, text


def load_done_keys(out_path: Path) -> set[tuple[str, str]]:
    """Return {(script_id, segment_text)} already present in the output file."""
    done: set[tuple[str, str]] = set()
    if not out_path.exists():
        return done
    with out_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            done.add(((row.get("script number/ID") or ""), (row.get("Segments/scripts") or "")))
    return done


def make_client(api_key: str):
    try:
        from google import genai  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency hint
        raise SystemExit(
            "google-genai is not installed. Run: pip install google-genai"
        ) from exc
    return genai.Client(api_key=api_key)


def make_prompt(language: str, batch: list[tuple[str, int, str]]) -> str:
    segments = [{"index": i, "segment": seg} for i, (_, _, seg) in enumerate(batch)]
    return PROMPT_TEMPLATE.format(
        language=language,
        category_guide=CATEGORY_GUIDE,
        segments_json=json.dumps(segments, ensure_ascii=False),
    )


def extract_json_array(text: str) -> list[dict]:
    """Pull the first JSON array out of a model response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON array found in model output")
    return json.loads(text[start : end + 1])


def batch_paths(out_path: Path) -> tuple[Path, Path, Path]:
    prefix = Path(f"{out_path}.gemini-batch")
    return (
        Path(f"{prefix}.state.json"),
        Path(f"{prefix}.requests.jsonl"),
        Path(f"{prefix}.manifest.jsonl"),
    )


def create_batch_files(
    request_path: Path,
    manifest_path: Path,
    language: str,
    pending: list[tuple[str, int, str]],
    segments_per_request: int,
) -> int:
    """Create Gemini request JSONL and a local key-to-segments manifest."""
    request_count = 0
    with (
        request_path.open("w", encoding="utf-8") as requests_fh,
        manifest_path.open("w", encoding="utf-8") as manifest_fh,
    ):
        for start in range(0, len(pending), segments_per_request):
            batch = pending[start : start + segments_per_request]
            key = f"request-{request_count:06d}"
            request = {
                "key": key,
                "request": {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": make_prompt(language, batch)}],
                        }
                    ],
                    "generation_config": {
                        "response_mime_type": "application/json",
                        "temperature": 0,
                    },
                },
            }
            manifest = {
                "key": key,
                "segments": [
                    {"script_id": sid, "segment_index": idx, "segment": seg}
                    for sid, idx, seg in batch
                ],
            }
            requests_fh.write(json.dumps(request, ensure_ascii=False) + "\n")
            manifest_fh.write(json.dumps(manifest, ensure_ascii=False) + "\n")
            request_count += 1
    return request_count


def submit_batch_job(client, model: str, request_path: Path, display_name: str):
    from google.genai import types  # type: ignore

    uploaded = client.files.upload(
        file=str(request_path),
        config=types.UploadFileConfig(
            display_name=display_name,
            mime_type="jsonl",
        ),
    )
    return client.batches.create(
        model=model,
        src=uploaded.name,
        config={"display_name": display_name},
    )


def job_state_name(job) -> str:
    state = job.state
    return state.name if hasattr(state, "name") else str(state)


def wait_for_batch(client, job_name: str, poll_interval: int):
    completed = {
        "JOB_STATE_SUCCEEDED",
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_EXPIRED",
    }
    while True:
        job = client.batches.get(name=job_name)
        state = job_state_name(job)
        print(f"Batch job {job_name}: {state}", file=sys.stderr)
        if state in completed:
            return job
        time.sleep(poll_interval)


def response_text(result: dict) -> str:
    try:
        parts = result["response"]["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts)
    except (KeyError, IndexError, TypeError):
        return ""


def load_manifests(path: Path) -> tuple[dict[str, list[dict]], list[str]]:
    manifests: dict[str, list[dict]] = {}
    order: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line)
            manifests[item["key"]] = item["segments"]
            order.append(item["key"])
    return manifests, order


def collect_batch_results(
    client,
    job,
    manifest_path: Path,
    language: str,
    out_path: Path,
) -> int:
    if not job.dest or not job.dest.file_name:
        raise RuntimeError("Batch succeeded but no result file was returned.")

    content = client.files.download(file=job.dest.file_name).decode("utf-8")
    manifests, order = load_manifests(manifest_path)
    processed = 0
    file_exists = out_path.exists() and out_path.stat().st_size > 0

    with out_path.open("a", encoding="utf-8", newline="") as out_fh:
        writer = csv.writer(out_fh)
        if not file_exists:
            writer.writerow(OUTPUT_HEADER)

        for line_number, line in enumerate(content.splitlines()):
            if not line.strip():
                continue
            result = json.loads(line)
            key = result.get("key")
            if not key and line_number < len(order):
                key = order[line_number]
            segments = manifests.pop(key, [])
            parsed: list[dict] = []
            if not result.get("error"):
                try:
                    parsed = extract_json_array(response_text(result))
                except (ValueError, json.JSONDecodeError):
                    pass
            by_index = {
                int(item.get("index", pos)): item
                for pos, item in enumerate(parsed)
                if isinstance(item, dict)
            }
            for pos, segment_info in enumerate(segments):
                writer.writerow(
                    result_to_row(
                        language,
                        str(segment_info["script_id"]),
                        str(segment_info["segment"]),
                        by_index.get(pos, {}),
                    )
                )
                processed += 1

        # Missing/failed requests are retained as Review instead of silently lost.
        for segments in manifests.values():
            for segment_info in segments:
                writer.writerow(
                    result_to_row(
                        language,
                        str(segment_info["script_id"]),
                        str(segment_info["segment"]),
                        {},
                    )
                )
                processed += 1
    return processed


def result_to_row(language: str, script_id: str, segment: str, res: dict) -> list[str]:
    verdict = (res.get("verdict") or "Review").strip().title()
    if verdict not in {"Keep", "Delete", "Review"}:
        verdict = "Review"
    cleaned = res.get("cleaned_segment")
    if cleaned is None:
        cleaned = "" if verdict == "Delete" else segment
    return [
        language,
        script_id,
        segment,
        verdict,
        (res.get("why_verdict") or "").strip(),
        (res.get("category") or "").strip(),
        (res.get("contextual") or "").strip(),
        str(cleaned).strip(),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="Input segmented CSV")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output CSV (default: <input stem>_verdicts.csv)")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model (default: {DEFAULT_MODEL})")
    parser.add_argument("-b", "--batch-size", type=int, default=DEFAULT_BATCH,
                        help=f"Segments per request inside the batch job (default: {DEFAULT_BATCH})")
    parser.add_argument("--language", default=None, help="Override language (default: derived from filename)")
    parser.add_argument("-n", "--limit", type=int, default=None, help="Process at most N segments (for testing)")
    parser.add_argument("--env-file", type=Path, default=Path(".env"),
                        help="File containing GEMINI_API_KEY (default: .env)")
    parser.add_argument("--poll-interval", type=int, default=30,
                        help="Seconds between batch status checks (default: 30)")
    parser.add_argument("--submit-only", action="store_true",
                        help="Submit the batch and exit; rerun later to collect it")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1.")
    if args.poll_interval < 1:
        raise SystemExit("--poll-interval must be at least 1.")

    load_env_file(args.env_file)
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit(
            f"Set GEMINI_API_KEY (or GOOGLE_API_KEY) in {args.env_file}."
        )

    language = args.language or language_from_filename(args.input)
    out_path = args.output or args.input.with_name(f"{args.input.stem}_verdicts.csv")
    state_path, request_path, manifest_path = batch_paths(out_path)
    client = make_client(api_key)

    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        job_name = state["job_name"]
        language = state["language"]
        manifest_path = Path(state["manifest_path"])
        request_path = Path(state["request_path"])
        print(f"Resuming Gemini batch job: {job_name}", file=sys.stderr)
    else:
        done = load_done_keys(out_path)
        if done:
            print(f"Skipping {len(done)} completed segments.", file=sys.stderr)
        pending = [
            (sid, idx, seg)
            for sid, idx, seg in iter_input_rows(args.input)
            if (sid, seg) not in done
        ]
        if args.limit is not None:
            pending = pending[: args.limit]
        if not pending:
            print("Nothing to process.", file=sys.stderr)
            return

        request_count = create_batch_files(
            request_path,
            manifest_path,
            language,
            pending,
            args.batch_size,
        )
        print(
            f"Submitting {len(pending)} segments in {request_count} batch requests...",
            file=sys.stderr,
        )
        job = submit_batch_job(
            client,
            args.model,
            request_path,
            f"{language}-cleanup-{int(time.time())}",
        )
        job_name = job.name
        state_path.write_text(
            json.dumps(
                {
                    "job_name": job_name,
                    "language": language,
                    "model": args.model,
                    "request_path": str(request_path.resolve()),
                    "manifest_path": str(manifest_path.resolve()),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Submitted Gemini batch job: {job_name}", file=sys.stderr)

    if args.submit_only:
        print(f"Re-run this command later to collect {job_name}.", file=sys.stderr)
        return

    try:
        job = wait_for_batch(client, job_name, args.poll_interval)
    except KeyboardInterrupt:
        print(
            f"\nStopped polling. Re-run this command to resume job {job_name}.",
            file=sys.stderr,
        )
        return

    state_name = job_state_name(job)
    if state_name != "JOB_STATE_SUCCEEDED":
        state_path.unlink(missing_ok=True)
        raise SystemExit(f"Batch ended with {state_name}: {getattr(job, 'error', '')}")

    processed = collect_batch_results(
        client,
        job,
        manifest_path,
        language,
        out_path,
    )
    state_path.unlink(missing_ok=True)
    request_path.unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)
    print(f"Done. Wrote verdicts for {processed} segments to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
