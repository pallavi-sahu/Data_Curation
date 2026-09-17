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
    "mni": "meetei_mayek",
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
    "ks": "urd+eng",
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
    "ks": ["ur", "en"],
    "kok": ["gom", "en"],
    "mai": ["hi", "en"],
    "ml": ["ml", "en"],
    "mni": ["bn", "en"],
    "mr": ["hi", "en"],
    "ne": ["ne", "en"],
    "or": ["hi", "en"],
    "pa": ["hi", "en"],
    "sa": ["sa", "en"],
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

# Wikipedia recursive crawl defaults.
DEFAULT_WIKI_CRAWL_DEPTH = 0
DEFAULT_WIKI_MAX_PAGES = 150
DEFAULT_WIKI_LINKS_PER_PAGE = 40
DEFAULT_WIKI_CRAWL_DELAY = 2.5
# Minimum seconds between Wikipedia API calls (crawl + extract share one cache).
DEFAULT_WIKI_REQUEST_INTERVAL = 2.5

# Localized Wikipedia main-page titles (from each wiki's siteinfo, plus common variants).
# Keys are Wikipedia subdomain codes (`gom` for Konkani; pipeline `--target-lang kok`).
INDIC_WIKIPEDIA_MAIN_PAGE_TITLES: dict[str, tuple[str, ...]] = {
    "as": ("বেটুপাত",),
    "bn": ("প্রধান পাতা",),
    "brx": ("Main Page",),  # incubator only (no brx.wikipedia.org yet)
    "doi": ("Main Page",),  # incubator only
    "gu": ("મુખપૃષ્ઠ",),
    "hi": ("मुखपृष्ठ", "पृष्ठ प्रधान"),
    "kn": ("ಮುಖ್ಯ ಪುಟ", "ಮುಖ್ಯಪುಟ"),
    "ks": ("اَہَم صَفہٕ",),
    "kok": ("मुखेल पान",),
    "gom": ("मुखेल पान",),  # Konkani Wikipedia host
    "mai": ("सम्मुख पन्ना", "मुख्य पृष्ठ"),
    "ml": ("പ്രധാന താൾ", "വിക്കിപീഡിയ"),
    "mni": ("ꯃꯔꯨꯑꯣꯏꯕ ꯂꯃꯥꯏ", "Main Page"),
    "mr": ("मुख्य पान", "मुखपृष्ठ"),
    "ne": ("मुख्य पृष्ठ", "गृहपृष्ठ"),
    "or": ("ପ୍ରଧାନ ପୃଷ୍ଠା",),
    "pa": ("ਮੁੱਖ ਸਫ਼ਾ",),
    "sa": ("मुख्यपृष्ठम्", "मुख्यपृष्ठ"),
    "sat": ("ᱢᱩᱬᱩᱛ ᱥᱟᱦᱴᱟ",),
    "sd": ("مُک صفحو",),
    "ta": ("முதற் பக்கம்", "முதற்பக்கம்"),
    "te": ("మొదటి పేజీ", "మొదటిపేజీ"),
    "ur": ("صفحۂ اول", "صفحہ اول"),
}

# Universal fallbacks (Latin; matched case-insensitively).
WIKIPEDIA_MAIN_PAGE_TITLES_UNIVERSAL: frozenset[str] = frozenset({"main page"})
