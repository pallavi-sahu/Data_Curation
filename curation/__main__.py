"""CLI entry point: python -m curation"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from curation.config import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_MIN_WORDS,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_WIKI_CRAWL_DELAY,
    DEFAULT_WIKI_CRAWL_DEPTH,
    DEFAULT_WIKI_LINKS_PER_PAGE,
    DEFAULT_WIKI_MAX_PAGES,
    INDIC_LANGUAGE_CODES,
)
from curation.pipeline import run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    supported = ", ".join(sorted(INDIC_LANGUAGE_CODES))
    parser = argparse.ArgumentParser(
        description=(
            "Curate text from PDFs, HTML files, and URLs: extract, segment, "
            "filter by Indic language, and export to JSONL/CSV."
        ),
    )
    parser.add_argument(
        "--pdf",
        action="append",
        default=[],
        metavar="PATH",
        help="PDF file or directory (repeatable)",
    )
    parser.add_argument(
        "--html",
        action="append",
        default=[],
        metavar="PATH",
        help="HTML file or directory (repeatable: --html a.html --html dir/)",
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        metavar="URL",
        help="Web URL to extract (repeatable; Wikipedia supported)",
    )
    parser.add_argument(
        "--htmls-file",
        metavar="FILE",
        help="Text file with one local HTML path per line",
    )
    parser.add_argument(
        "--urls-file",
        metavar="FILE",
        help="Text file with one URL per line",
    )
    parser.add_argument(
        "--target-lang",
        required=True,
        metavar="CODE",
        help=f"ISO language code to keep (one of: {supported})",
    )
    parser.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="Output .jsonl or .csv path",
    )
    parser.add_argument(
        "--format",
        choices=("jsonl", "csv"),
        default=DEFAULT_OUTPUT_FORMAT,
        help=f"Output format (default: {DEFAULT_OUTPUT_FORMAT})",
    )
    parser.add_argument(
        "--min-words",
        type=int,
        default=DEFAULT_MIN_WORDS,
        help=(
            f"Minimum words per JSONL/CSV row; merge fragments from the same "
            f"source only (default: {DEFAULT_MIN_WORDS}). Text under the limit "
            f"left at the end of a page is not written."
        ),
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=(
            f"Minimum fastText confidence for target language "
            f"(default: {DEFAULT_CONFIDENCE_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="Path to fastText lid.176.bin (downloaded on first run if missing)",
    )
    parser.add_argument(
        "--url-timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds for URL extraction",
    )
    parser.add_argument(
        "--wiki-discover-links",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Depth-1 shorthand: follow up to N /wiki/ links per seed page. "
            "Use 0 with --wiki-crawl-depth 1 to follow ALL links (no cap). "
            "Ignored if you set --wiki-links-per-page."
        ),
    )
    parser.add_argument(
        "--wiki-crawl-depth",
        type=int,
        default=DEFAULT_WIKI_CRAWL_DEPTH,
        metavar="N",
        help=(
            "Recursively follow links N levels deep from each seed URL "
            f"(default: {DEFAULT_WIKI_CRAWL_DEPTH}). "
            "Wikipedia: /wiki/ article links. Other sites: same-host links from "
            "article/main content (nav/footer stripped)."
        ),
    )
    parser.add_argument(
        "--wiki-max-pages",
        type=int,
        default=DEFAULT_WIKI_MAX_PAGES,
        metavar="N",
        help=(
            f"Maximum pages to fetch per crawl (default: {DEFAULT_WIKI_MAX_PAGES}). "
            "Use 0 for no limit."
        ),
    )
    parser.add_argument(
        "--wiki-links-per-page",
        type=int,
        default=DEFAULT_WIKI_LINKS_PER_PAGE,
        metavar="N",
        help=(
            f"Max /wiki/ links to follow from each page (default: {DEFAULT_WIKI_LINKS_PER_PAGE}). "
            "Use 0 for no limit (all article links on that page)."
        ),
    )
    parser.add_argument(
        "--wiki-crawl-delay",
        type=float,
        default=DEFAULT_WIKI_CRAWL_DELAY,
        help=(
            f"Minimum seconds between crawl HTTP/API requests (default: {DEFAULT_WIKI_CRAWL_DELAY}). "
            "Use 2.5–4.0 for large crawls to avoid HTTP 429."
        ),
    )
    parser.add_argument(
        "--pdf-ocr",
        action="store_true",
        help="Force OCR on PDFs (scanned/image-only; renders pages, slow)",
    )
    parser.add_argument(
        "--auto-ocr",
        action="store_true",
        help="OCR only when the PDF text layer is nearly empty (<15 words/page)",
    )
    parser.add_argument(
        "--no-auto-ocr",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--ocr-lang",
        metavar="LANG",
        default=None,
        help="Tesseract lang(s), e.g. hin+eng (default: inferred from --target-lang)",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=("auto", "tesseract", "easyocr"),
        default="auto",
        help="OCR backend: tesseract (fast), easyocr (pip-only), or auto",
    )
    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=200,
        help="Render DPI for Tesseract OCR (default: 200)",
    )
    parser.add_argument(
        "--ocr-max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Process only the first N PDF pages (text layer or OCR)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    urls = list(args.url)
    if args.urls_file:
        urls_path = Path(args.urls_file)
        if not urls_path.is_file():
            parser.error(f"URLs file not found: {urls_path}")
        urls.extend(
            line.strip()
            for line in urls_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

    html_paths = list(args.html)
    if args.htmls_file:
        htmls_path = Path(args.htmls_file)
        if not htmls_path.is_file():
            parser.error(f"HTML paths file not found: {htmls_path}")
        html_paths.extend(
            line.strip()
            for line in htmls_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

    if not args.pdf and not urls and not html_paths:
        parser.error("Provide at least one --pdf, --html, or --url (or --urls-file)")

    run_pipeline(
        pdf_paths=args.pdf,
        html_paths=html_paths,
        urls=urls,
        target_language=args.target_lang,
        output_path=args.output,
        output_format=args.format,
        min_words=args.min_words,
        confidence_threshold=args.confidence,
        model_path=args.model_path,
        url_timeout=args.url_timeout,
        force_ocr=args.pdf_ocr,
        auto_ocr=args.auto_ocr and not args.no_auto_ocr,
        ocr_lang=args.ocr_lang,
        ocr_engine=args.ocr_engine,
        ocr_dpi=args.ocr_dpi,
        ocr_max_pages=args.ocr_max_pages,
        wikipedia_discover_links=args.wiki_discover_links,
        wikipedia_crawl_depth=args.wiki_crawl_depth,
        wikipedia_max_pages=args.wiki_max_pages,
        wikipedia_links_per_page=args.wiki_links_per_page,
        wikipedia_crawl_delay=args.wiki_crawl_delay,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
