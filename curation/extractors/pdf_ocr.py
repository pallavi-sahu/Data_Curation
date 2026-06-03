"""Optional OCR for PDFs with no usable text layer (not used by default)."""

from __future__ import annotations

import logging
import shutil
from typing import Literal

import fitz  # PyMuPDF

from curation.config import DEFAULT_OCR_DPI

logger = logging.getLogger(__name__)

OcrEngine = Literal["tesseract", "easyocr", "auto"]


def extract_pdf_ocr(
    doc: fitz.Document,
    *,
    ocr_lang: str,
    ocr_dpi: int = DEFAULT_OCR_DPI,
    ocr_engine: OcrEngine = "auto",
    target_language: str | None = None,
    max_pages: int | None = None,
) -> str:
    """Render pages to images and run OCR (slow; use only when text layer is empty)."""
    engine = _resolve_ocr_engine(ocr_engine)
    page_limit = max_pages or doc.page_count
    if engine == "tesseract":
        return _ocr_tesseract(doc, ocr_lang, ocr_dpi, page_limit=page_limit)
    return _ocr_easyocr(doc, ocr_lang, target_language, page_limit=page_limit)


def _resolve_ocr_engine(ocr_engine: OcrEngine) -> Literal["tesseract", "easyocr"]:
    if ocr_engine == "tesseract":
        if not shutil.which("tesseract"):
            raise RuntimeError(
                "Tesseract is not installed. Run: "
                "sudo apt install tesseract-ocr tesseract-ocr-hin"
            )
        return "tesseract"
    if ocr_engine == "easyocr":
        return "easyocr"
    if shutil.which("tesseract"):
        return "tesseract"
    logger.info("Tesseract not found; falling back to EasyOCR (pip-only, slower first run)")
    return "easyocr"


def _ocr_tesseract(
    doc: fitz.Document, ocr_lang: str, ocr_dpi: int, *, page_limit: int
) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Install OCR dependencies: pip install pytesseract Pillow"
        ) from exc

    if not shutil.which("tesseract"):
        raise RuntimeError(
            "Tesseract binary not found. Install e.g.: "
            "sudo apt install tesseract-ocr tesseract-ocr-hin"
        )

    matrix = fitz.Matrix(ocr_dpi / 72, ocr_dpi / 72)
    pages: list[str] = []
    total = min(doc.page_count, page_limit)

    for index, page in enumerate(doc, start=1):
        if index > page_limit:
            break
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        text = pytesseract.image_to_string(image, lang=ocr_lang)
        if text.strip():
            pages.append(text)
        if index % 10 == 0 or index == total:
            logger.info("OCR (tesseract): %s/%s pages", index, total)

    return "\n\n".join(pages)


def _ocr_easyocr(
    doc: fitz.Document,
    ocr_lang: str,
    target_language: str | None,
    *,
    page_limit: int,
) -> str:
    try:
        import easyocr
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("EasyOCR not installed. Run: pip install easyocr") from exc

    from curation.config import OCR_EASYOCR_LANG

    if "+" in ocr_lang or "," in ocr_lang:
        langs = [
            part.strip()
            for part in ocr_lang.replace(",", "+").split("+")
            if part.strip()
        ]
    elif target_language and target_language in OCR_EASYOCR_LANG:
        langs = OCR_EASYOCR_LANG[target_language]
    else:
        langs = ["hi", "en"]

    reader = easyocr.Reader(langs, gpu=False, verbose=False)
    matrix = fitz.Matrix(2.0, 2.0)
    pages: list[str] = []
    total = min(doc.page_count, page_limit)

    for index, page in enumerate(doc, start=1):
        if index > page_limit:
            break
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        channels = pix.n
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, channels
        )
        if channels == 4:
            img = img[:, :, :3]
        lines = reader.readtext(img, detail=0, paragraph=True)
        text = "\n".join(lines)
        if text.strip():
            pages.append(text)
        if index % 10 == 0 or index == total:
            logger.info("OCR (easyocr): %s/%s pages", index, total)

    return "\n\n".join(pages)
