"""Orchestrate extraction, segmentation, language filtering, and export."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Iterator, Literal

from curation.config import DEFAULT_CONFIDENCE_THRESHOLD, DEFAULT_MIN_WORDS
from curation.extractors.pdf import OcrEngine, extract_text_from_pdf
from curation.extractors.web import extract_text_from_url
from curation.extractors.wikipedia import (
    discover_wikipedia_article_links,
    is_wikipedia_url,
)
from curation.io.writer import OutputFormat, save_records
from curation.processing.language import LanguageFilter
from curation.processing.pack import pack_records
from curation.processing.paragraphs import segment_paragraphs

logger = logging.getLogger(__name__)


def process_source(
    source: str,
    raw_text: str,
    language_filter: LanguageFilter,
) -> list[dict[str, Any]]:
    """
    Segment text and keep every fragment that matches the target language.

    No minimum word length is applied here; short paragraphs are kept.
    """
    records: list[dict[str, Any]] = []
    for paragraph in segment_paragraphs(raw_text):
        matches, prediction = language_filter.matches_target(paragraph)
        if not matches:
            logger.debug(
                "Dropped paragraph from %s (%s=%s, confidence=%.3f)",
                source,
                prediction.method,
                prediction.language_code,
                prediction.confidence,
            )
            continue
        records.append(
            {
                "source": source,
                "paragraph": paragraph,
                "language_code": prediction.language_code,
                "confidence": round(prediction.confidence, 6),
                "detection_method": prediction.method,
            }
        )
    return records


def iter_pdf_paths(pdf_inputs: list[str]) -> Iterator[Path]:
    """Expand file paths and directories into PDF files."""
    for item in pdf_inputs:
        path = Path(item)
        if path.is_dir():
            yield from sorted(path.rglob("*.pdf"))
        elif path.suffix.lower() == ".pdf" and path.is_file():
            yield path
        else:
            logger.warning("Skipping non-PDF path: %s", path)


def run_pipeline(
    *,
    pdf_paths: list[str] | None = None,
    urls: list[str] | None = None,
    target_language: str,
    output_path: str,
    output_format: OutputFormat = "jsonl",
    min_words: int = DEFAULT_MIN_WORDS,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    model_path: Path | None = None,
    url_timeout: int = 30,
    force_ocr: bool = False,
    auto_ocr: bool = True,
    ocr_lang: str | None = None,
    ocr_engine: OcrEngine = "auto",
    ocr_dpi: int = 200,
    ocr_max_pages: int | None = None,
    wikipedia_discover_links: int = 0,
) -> int:
    """
    Run the full curation pipeline and write filtered paragraphs to disk.

    Scraping keeps all language-matching text (any length). ``min_words`` controls
    how fragments are merged into each JSONL/CSV output row.

    Returns the total number of rows written.
    """
    language_filter = LanguageFilter(
        target_language,
        confidence_threshold=confidence_threshold,
        model_path=model_path,
    )
    if language_filter.needs_fasttext_model():
        language_filter.ensure_model()
    elif language_filter.uses_script_detection:
        logger.info(
            "Target language '%s' is not in fastText lid.176; "
            "using %s script detection (threshold=%.2f)",
            target_language,
            language_filter.script_name,
            confidence_threshold,
        )

    all_records: list[dict[str, Any]] = []

    for pdf_path in iter_pdf_paths(pdf_paths or []):
        source = str(pdf_path.resolve())
        logger.info("Extracting PDF: %s", source)
        try:
            raw = extract_text_from_pdf(
                pdf_path,
                force_ocr=force_ocr,
                auto_ocr=auto_ocr,
                ocr_lang=ocr_lang,
                ocr_dpi=ocr_dpi,
                ocr_engine=ocr_engine,
                target_language=target_language,
                ocr_max_pages=ocr_max_pages,
            )
        except Exception:
            logger.exception("Failed to extract PDF: %s", source)
            continue
        if not raw.strip():
            logger.warning("No text extracted from PDF: %s", source)
            continue
        all_records.extend(process_source(source, raw, language_filter))

    expanded_urls = _expand_seed_urls(
        urls or [],
        wikipedia_discover_links=wikipedia_discover_links,
        timeout=url_timeout,
    )

    for index, url in enumerate(expanded_urls):
        url = url.strip()
        if not url:
            continue
        # Gentle pacing to reduce Wikipedia API 429 throttling.
        if index > 0 and is_wikipedia_url(url):
            time.sleep(0.4)
        logger.info("Extracting URL: %s", url)
        try:
            raw = extract_text_from_url(url, timeout=url_timeout)
        except Exception:
            logger.exception("Failed to extract URL: %s", url)
            continue
        if not raw:
            logger.warning("No text extracted from URL: %s", url)
            continue
        all_records.extend(process_source(url, raw, language_filter))

    scraped = len(all_records)
    output_records = pack_records(all_records, min_words=min_words)
    count = save_records(output_records, output_path, output_format=output_format)
    logger.info(
        "Kept %s language-matching fragments, wrote %s output rows (min_words=%s) to %s",
        scraped,
        count,
        min_words,
        output_path,
    )
    return count


def _expand_seed_urls(
    urls: list[str],
    *,
    wikipedia_discover_links: int,
    timeout: int,
) -> list[str]:
    """Expand seed URLs with discovered Wikipedia article links."""
    expanded: list[str] = []
    seen: set[str] = set()

    for raw in urls:
        url = raw.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        expanded.append(url)

        if wikipedia_discover_links <= 0 or not is_wikipedia_url(url):
            continue

        try:
            discovered = discover_wikipedia_article_links(
                url,
                timeout=timeout,
                max_links=wikipedia_discover_links,
            )
        except Exception:
            logger.exception("Failed discovering Wikipedia links from %s", url)
            continue

        new_links = 0
        for link in discovered:
            if link in seen:
                continue
            seen.add(link)
            expanded.append(link)
            new_links += 1
        logger.info("Discovered %s linked Wikipedia articles from %s", new_links, url)

    return expanded
