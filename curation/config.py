"""Shared configuration and constants for the curation pipeline."""

from __future__ import annotations

from pathlib import Path

# FastText language-ID model (176 languages, ISO 639-1 codes).
FASTTEXT_LID_MODEL_URL = (
    "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
)
DEFAULT_MODEL_DIR = Path.home() / ".cache" / "data_curation" / "models"
DEFAULT_MODEL_PATH = DEFAULT_MODEL_DIR / "lid.176.bin"

# The 22 scheduled languages of India (ISO 639-1 / common codes).
INDIC_LANGUAGE_CODES: frozenset[str] = frozenset(
    {
        "as",  # Assamese
        "bn",  # Bengali
        "brx",  # Bodo
        "doi",  # Dogri
        "gu",  # Gujarati
        "hi",  # Hindi
        "kn",  # Kannada
        "ks",  # Kashmiri
        "kok",  # Konkani
        "mai",  # Maithili
        "ml",  # Malayalam
        "mni",  # Manipuri
        "mr",  # Marathi
        "ne",  # Nepali
        "or",  # Odia
        "pa",  # Punjabi
        "sa",  # Sanskrit
        "sat",  # Santali
        "sd",  # Sindhi
        "ta",  # Tamil
        "te",  # Telugu
        "ur",  # Urdu
    }
)

# Languages absent from fastText lid.176 — filtered by Unicode script instead.
FASTTEXT_UNSUPPORTED_INDIC: frozenset[str] = frozenset(
    {"brx", "doi", "kok", "mai", "mni", "sat"}
)

# Primary script for each scheduled language (used when fastText has no label).
LANGUAGE_SCRIPT: dict[str, str] = {
    "as": "bengali",
    "bn": "bengali",
    "brx": "devanagari",
    "doi": "devanagari",
    "gu": "gujarati",
    "hi": "devanagari",
    "kn": "kannada",
    "ks": "arabic",
    "kok": "devanagari",
    "mai": "devanagari",
    "ml": "malayalam",
    "mni": "bengali",
    "mr": "devanagari",
    "ne": "devanagari",
    "or": "oriya",
    "pa": "gurmukhi",
    "sa": "devanagari",
    "sat": "ol_chiki",
    "sd": "arabic",
    "ta": "tamil",
    "te": "telugu",
    "ur": "arabic",
}

# Tesseract language packs for OCR (install via apt: tesseract-ocr-<lang>).
OCR_TESSERACT_LANG: dict[str, str] = {
    "as": "ben+eng",
    "bn": "ben+eng",
    "brx": "hin+eng",
    "doi": "hin+eng",
    "gu": "guj+eng",
    "hi": "hin+eng",
    "kn": "kan+eng",
    "ks": "hin+eng",
    "kok": "hin+eng",
    "mai": "hin+eng",
    "ml": "mal+eng",
    "mni": "ben+eng",
    "mr": "hin+eng",
    "ne": "hin+eng",
    "or": "ori+eng",
    "pa": "pan+eng",
    "sa": "san+eng",
    "sat": "hin+eng",
    "sd": "snd+eng",
    "ta": "tam+eng",
    "te": "tel+eng",
    "ur": "urd+eng",
}

# EasyOCR language codes (pip-only fallback when Tesseract is not installed).
OCR_EASYOCR_LANG: dict[str, list[str]] = {
    "as": ["bn", "en"],
    "bn": ["bn", "en"],
    "brx": ["hi", "en"],
    "doi": ["hi", "en"],
    "gu": ["gu", "en"],
    "hi": ["hi", "en"],
    "kn": ["kn", "en"],
    "kok": ["hi", "en"],
    "mai": ["hi", "en"],
    "ml": ["ml", "en"],
    "mni": ["bn", "en"],
    "mr": ["hi", "en"],
    "ne": ["hi", "en"],
    "or": ["hi", "en"],
    "pa": ["hi", "en"],
    "sa": ["hi", "en"],
    "sat": ["hi", "en"],
    "sd": ["hi", "en"],
    "ta": ["ta", "en"],
    "te": ["te", "en"],
    "ur": ["hi", "en"],
}

# Defaults aligned with pipeline requirements.
DEFAULT_MIN_WORDS = 500
DEFAULT_CONFIDENCE_THRESHOLD = 0.85
DEFAULT_SCRIPT_RATIO_THRESHOLD = 0.85
DEFAULT_OUTPUT_FORMAT = "jsonl"
DEFAULT_OCR_DPI = 200
# If a PDF yields fewer words-per-page than this, treat it as image-only and OCR.
MIN_TEXT_LAYER_WORDS_PER_PAGE = 15.0
