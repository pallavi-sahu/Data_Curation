#!/usr/bin/env python3
"""
Run curation on each URL from a text file and append all rows to one JSONL file.

Example:
  python scripts/batch_curate_urls.py \
    --urls-file tamil.txt \
    --target-lang ta \
    --min-words 500 \
    --output tamil_html_all.jsonl
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow running from repo root: python scripts/batch_curate_urls.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from curation.config import DEFAULT_CONFIDENCE_THRESHOLD, DEFAULT_MIN_WORDS
from curation.extractors.web import extract_text_from_url
from curation.io.writer import append_records
from curation.processing.language import LanguageFilter
from curation.processing.pack import pack_records
from curation.pipeline import process_source

logger = logging.getLogger(__name__)


def load_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def curate_one_url(
    url: str,
    *,
    language_filter: LanguageFilter,
    min_words: int,
    url_timeout: int,
) -> list[dict]:
    raw = extract_text_from_url(url, timeout=url_timeout)
    if not raw.strip():
        return []
    records = process_source(url, raw, language_filter)
    return pack_records(records, min_words=min_words)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Curate many URLs sequentially into one JSONL (append mode).",
    )
    parser.add_argument(
        "--urls-file",
        type=Path,
        default=_REPO_ROOT / "tamil.txt",
        help="Text file with one URL per line (default: tamil.txt in repo root)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPO_ROOT / "tamil_html_all.jsonl",
        help="Combined JSONL output (appended after each URL)",
    )
    parser.add_argument("--target-lang", required=True, metavar="CODE")
    parser.add_argument(
        "--min-words",
        type=int,
        default=DEFAULT_MIN_WORDS,
        help=f"Minimum words per row per URL (default: {DEFAULT_MIN_WORDS})",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    parser.add_argument("--url-timeout", type=int, default=30)
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between URLs (default: 1.0)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=1,
        metavar="N",
        help="1-based index of first URL to process (default: 1)",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=0,
        metavar="N",
        help="1-based index of last URL to process (0 = all)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete output file before starting (default: append / resume)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    urls_path = args.urls_file
    if not urls_path.is_file():
        parser.error(f"URLs file not found: {urls_path}")

    all_urls = load_urls(urls_path)
    start = max(1, args.start)
    end = args.end if args.end > 0 else len(all_urls)
    urls = all_urls[start - 1 : end]
    if not urls:
        parser.error("No URLs to process for the given --start/--end range")

    output_path = args.output
    if args.fresh and output_path.is_file():
        output_path.unlink()
        logger.info("Removed existing output: %s", output_path)

    language_filter = LanguageFilter(
        args.target_lang,
        confidence_threshold=args.confidence,
    )
    if language_filter.needs_fasttext_model():
        language_filter.ensure_model()

    total_rows = 0
    failed = 0

    logger.info(
        "Processing %s URL(s) (%s..%s of %s) -> %s",
        len(urls),
        start,
        start + len(urls) - 1,
        len(all_urls),
        output_path,
    )

    for index, url in enumerate(urls, start=start):
        logger.info("[%s/%s] %s", index, end, url)
        try:
            rows = curate_one_url(
                url,
                language_filter=language_filter,
                min_words=args.min_words,
                url_timeout=args.url_timeout,
            )
            written = append_records(rows, output_path, output_format="jsonl")
            total_rows += written
            logger.info(
                "[%s/%s] wrote %s row(s) (running total: %s)",
                index,
                end,
                written,
                total_rows,
            )
        except Exception:
            failed += 1
            logger.exception("[%s/%s] failed: %s", index, end, url)

        if index < end and args.delay > 0:
            time.sleep(args.delay)

    logger.info(
        "Done. %s row(s) in %s (%s URL(s) failed)",
        total_rows,
        output_path,
        failed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
