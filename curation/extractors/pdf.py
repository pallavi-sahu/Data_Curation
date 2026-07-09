"""Extract text from PDF textbooks via the embedded text layer (no images)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz  # PyMuPDF

from curation.config import LANGUAGE_SCRIPT, MIN_TEXT_LAYER_WORDS_PER_PAGE
from curation.extractors.pdf_ocr import OcrEngine, extract_pdf_ocr
from curation.processing.script import script_character_ratio

__all__ = ["OcrEngine", "extract_text_from_pdf"]

logger = logging.getLogger(__name__)

# Block type 0 = text, 1 = image in PyMuPDF dict/blocks output.
_TEXT_BLOCK = 0

# Lines that are only page numbers or figure labels.
_PAGE_NOISE_RE = re.compile(
    r"^\s*(?:page\s+)?\d{1,4}\s*(?:of\s+\d{1,4})?\s*$",
    re.IGNORECASE,
)


def extract_text_from_pdf(
    pdf_path: str | Path,
    *,
    force_ocr: bool = False,
    auto_ocr: bool = False,
    ocr_lang: str | None = None,
    ocr_dpi: int = 200,
    ocr_engine: OcrEngine = "auto",
    target_language: str | None = None,
    ocr_max_pages: int | None = None,
) -> str:
    """
    Extract readable text from a PDF using its **text layer** only.

    - Walks PyMuPDF text blocks in reading order; **image blocks are skipped**.
    - Does not render page bitmaps unless OCR is explicitly requested.
    - Use ``--pdf-ocr`` or ``--auto-ocr`` for scanned / image-only PDFs.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    doc = fitz.open(path)
    try:
        _warn_image_front_matter(path, doc)
        page_limit = ocr_max_pages or doc.page_count
        text_layer = _extract_text_layer(doc, max_pages=page_limit)
        word_count = len(text_layer.split())
        pages_used = min(doc.page_count, page_limit)
        words_per_page = word_count / max(pages_used, 1)

        if word_count == 0:
            _warn_fully_scanned_pdf(path, doc, max_pages=pages_used)

        if target_language and text_layer.strip():
            _warn_if_unusable_text_layer(path, text_layer, target_language)

        needs_ocr = force_ocr or (
            auto_ocr and words_per_page < MIN_TEXT_LAYER_WORDS_PER_PAGE
        )

        if needs_ocr:
            lang = _resolve_ocr_lang(ocr_lang, target_language, ocr_engine)
            logger.warning(
                "PDF '%s': using OCR (%s words, %.1f words/page). "
                "For textbooks with a text layer, omit --pdf-ocr and --auto-ocr.",
                path.name,
                word_count,
                words_per_page,
            )
            return extract_pdf_ocr(
                doc,
                ocr_lang=lang,
                ocr_dpi=ocr_dpi,
                ocr_engine=ocr_engine,
                target_language=target_language,
                max_pages=page_limit,
            )

        if word_count:
            logger.info(
                "PDF text layer: %s words across %s page(s) (%.1f words/page, images skipped)",
                word_count,
                pages_used,
                words_per_page,
            )
        else:
            logger.warning(
                "PDF '%s': no text extracted from the text layer. "
                "The file may be scanned, use custom fonts without Unicode mapping, "
                "or be image-only. Try --auto-ocr or --pdf-ocr if appropriate.",
                path.name,
            )
        return text_layer
    finally:
        doc.close()


def _extract_text_layer(doc: fitz.Document, *, max_pages: int) -> str:
    """Collect text blocks per page; ignore image blocks and empty noise lines."""
    page_texts: list[str] = []
    for page_index, page in enumerate(doc):
        if page_index >= max_pages:
            break
        page_text = _extract_page_text(page)
        if page_text.strip():
            page_texts.append(page_text)
    return "\n\n".join(page_texts)


def _extract_page_text(page: fitz.Page) -> str:
    """
    Extract text blocks in reading order (``sort=True``).

    Uses the structured dict representation so image blocks (type 1) are never
    included. Spans within a line are joined; lines within a block are kept;
    blocks are separated by blank lines.
    """
    page_dict = page.get_text("dict", sort=True)
    block_chunks: list[str] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != _TEXT_BLOCK:
            continue

        line_texts: list[str] = []
        for line in block.get("lines", []):
            span_parts: list[str] = []
            for span in line.get("spans", []):
                piece = span.get("text", "")
                if piece:
                    span_parts.append(piece)
            if not span_parts:
                continue
            line_text = _normalize_line("".join(span_parts))
            if line_text and not _PAGE_NOISE_RE.match(line_text):
                line_texts.append(line_text)

        if line_texts:
            block_chunks.append("\n".join(line_texts))

    return "\n\n".join(block_chunks)


def _normalize_line(text: str) -> str:
    """Collapse internal whitespace; strip control characters."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _page_is_image_only(page: fitz.Page) -> bool:
    blocks = page.get_text("dict").get("blocks", [])
    has_text = any(b.get("type") == 0 for b in blocks)
    has_image = any(b.get("type") == 1 for b in blocks)
    return has_image and not has_text


def _sample_page_indices(doc: fitz.Document, *, max_pages: int | None = None) -> list[int]:
    """Sample start, middle, and end pages (deduped) for structure checks."""
    limit = min(doc.page_count, max_pages or doc.page_count)
    if limit <= 0:
        return []
    candidates = [0, 1, 2, limit // 2, limit - 1]
    return sorted({i for i in candidates if 0 <= i < limit})


def _warn_image_front_matter(path: Path, doc: fitz.Document) -> None:
    """Note when opening pages are image-only; text may still appear later."""
    sample = min(3, doc.page_count)
    if sample == 0:
        return

    if all(_page_is_image_only(doc[i]) for i in range(sample)):
        logger.info(
            "PDF '%s': first %s page(s) look like images only (covers/common in "
            "textbooks). Extraction continues through the rest of the file.",
            path.name,
            sample,
        )


def _warn_fully_scanned_pdf(
    path: Path, doc: fitz.Document, *, max_pages: int
) -> None:
    """Warn when no text was found and sampled pages across the file are image-only."""
    indices = _sample_page_indices(doc, max_pages=max_pages)
    if not indices:
        return

    if all(_page_is_image_only(doc[i]) for i in indices):
        logger.warning(
            "PDF '%s': no text layer found (sampled pages %s are image-only). "
            "This file is likely a full scan — use --pdf-ocr or --auto-ocr "
            "(e.g. --ocr-lang mal+eng for Malayalam).",
            path.name,
            ", ".join(str(i + 1) for i in indices),
        )


def _warn_if_unusable_text_layer(
    path: Path, text: str, target_language: str
) -> None:
    """Warn when the text layer looks like legacy/custom font garbage."""
    script = LANGUAGE_SCRIPT.get(target_language)
    if not script:
        return
    ratio = script_character_ratio(text, script)
    if ratio >= 0.15:
        return
    logger.warning(
        "PDF '%s' text layer has very little %s script (%.1f%% of letters). "
        "Common with custom/legacy PDF fonts (e.g. Priyaanka). "
        "Output may be empty after language filtering, or you may need OCR/font tools.",
        path.name,
        script,
        ratio * 100,
    )


def _resolve_ocr_lang(
    ocr_lang: str | None,
    target_language: str | None,
    ocr_engine: OcrEngine,
) -> str:
    if ocr_lang:
        return ocr_lang
    if not target_language:
        return "hin+eng"
    import shutil

    from curation.config import OCR_EASYOCR_LANG, OCR_TESSERACT_LANG

    if ocr_engine in ("tesseract", "auto") and shutil.which("tesseract"):
        return OCR_TESSERACT_LANG.get(target_language, "hin+eng")
    return "+".join(OCR_EASYOCR_LANG.get(target_language, ["hi", "en"]))
