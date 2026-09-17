"""Indic-aware tokenization and script utilities for validation metrics."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from curation.config import LANGUAGE_SCRIPT
from curation.processing.script import char_in_script, script_character_ratio

# Latin letters plus common Indic punctuation used as token boundaries.
WORD_BOUNDARY = r"[\s\u0964\u0965\u2026\u00A0\u200B\u200C\u200D\uFEFF\.,;:!?\(\)\[\]{}\"'«»—–\-/\\|@#$%^&*+=<>~`]+"

# Sequences of letters/numbers/markers in any script.
TOKEN_PATTERN = re.compile(
    r"[^\s\u0964\u0965\u2026\u00A0\u200B\u200C\u200D\uFEFF\.,;:!?\(\)\[\]{}\"'«»—–\-/\\|@#$%^&*+=<>~`]+"
)

# Sentence-ending punctuation across scheduled Indic languages.
# · Danda (। ॥) — Devanagari languages + widely used in digital text for
#   Gujarati, Bengali, Assamese, Odia, Tamil, Telugu, Kannada, Malayalam, Punjabi
# · Arabic full stop (۔) and question mark (؟) — Urdu, Sindhi, Kashmiri (Arabic script)
# · Latin . ! ? … — universal fallback in scraped web text
SENTENCE_TERMINATORS = frozenset(".\u0964\u0965\u06D4\u061F\u2026!?")

# Split after terminators; whitespace after punct is optional (common in Indic text).
# Latin period is excluded between digits (e.g. 1.47.0-wmf.4, 3.14).
SENTENCE_TERMINATOR_RUN = re.compile(
    r"(?:"
    r"[\u0964\u0965\u06D4\u061F\u2026!?]+"
    r"|(?<!\d)\.(?!\d)"
    r")"
    r"[\s\u200B\u200C\u200D]*"
)

URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s\]\)\"'<>]+",
    re.IGNORECASE,
)

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Common abbreviations: ALL-CAPS Latin 2+ chars, dotted forms (U.S.A.), Indic digit refs.
ACRONYM_PATTERN = re.compile(
    r"\b(?:[A-Z]{2,}(?:\.[A-Z]{2,})*|[A-Z]\.(?:[A-Z]\.)+[A-Z]?)\b"
)

# Non-standard words: numbers, dates, times, currency, percentages, phone-like strings.
NSW_PATTERNS: dict[str, re.Pattern[str]] = {
    "numbers": re.compile(r"\d+(?:[.,]\d+)*"),
    "dates": re.compile(
        r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4}\b",
        re.IGNORECASE,
    ),
    "times": re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\b"),
    "currency": re.compile(r"(?:₹|Rs\.?|INR|\$|€|£)\s*\d+(?:[.,]\d+)*"),
    "percentages": re.compile(r"\d+(?:[.,]\d+)*\s*%"),
    "phone_like": re.compile(r"(?:\+?\d[\d\s\-().]{7,}\d)"),
    "urls": URL_PATTERN,
    "emails": EMAIL_PATTERN,
}

MOJIBAKE_MARKERS = (
    "â€™",
    "â€œ",
    "â€",
    "Ã",
    "Â",
    "ï¿½",
    "�",
)

HTML_ENTITY_PATTERN = re.compile(r"&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);")

SMART_QUOTE_CHARS = frozenset("\u2018\u2019\u201c\u201d\u201e\u201f\u00ab\u00bb")

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "]+",
    flags=re.UNICODE,
)

# ASCII-only emoticons; avoids flagging Indic parenthetical glosses like (સૉફ્ટવેર).
KAOMOJI_PATTERN = re.compile(
    r"[\(\)\[\]]"
    r"[\x00-\x7F]{1,12}"
    r"[\(\)\[\]]"
)


def expected_script(language_code: str) -> str | None:
    return LANGUAGE_SCRIPT.get(language_code)


def tokenize(text: str) -> list[str]:
    """Split text into word-like tokens (works across Indic scripts)."""
    return [t for t in TOKEN_PATTERN.findall(text) if t.strip()]


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, keeping terminal punctuation on each segment."""
    text = text.strip()
    if not text:
        return []

    sentences: list[str] = []
    start = 0
    for match in SENTENCE_TERMINATOR_RUN.finditer(text):
        chunk = text[start : match.end()].strip()
        if chunk:
            sentences.append(chunk)
        start = match.end()

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def has_terminal_punctuation(text: str) -> bool:
    """True if text ends with a recognised sentence terminator."""
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in SENTENCE_TERMINATORS


def latin_character_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    latin = sum(1 for c in letters if "LATIN" in unicodedata.name(c, ""))
    return latin / len(letters)


def script_ratio(text: str, language_code: str) -> float:
    script = expected_script(language_code)
    if not script:
        return 0.0
    return script_character_ratio(text, script)


def count_char_categories(text: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for char in text:
        if char.isspace():
            counts["whitespace"] += 1
        elif char.isdigit():
            counts["digits"] += 1
        elif char.isalpha():
            counts["letters"] += 1
        else:
            category = unicodedata.category(char)
            counts[category] += 1
    return dict(counts)


def is_latin_letter(char: str) -> bool:
    return "LATIN" in unicodedata.name(char, "")


def is_all_caps_word(token: str) -> bool:
    """True only for Latin tokens written entirely in uppercase."""
    latin_letters = [c for c in token if c.isalpha() and is_latin_letter(c)]
    if len(latin_letters) < 2:
        return False
    return all(c.isupper() for c in latin_letters)


def token_belongs_to_script(token: str, script: str) -> bool:
    """True when every letter in the token belongs to the expected script."""
    letters = [c for c in token if c.isalpha()]
    if not letters:
        return True
    return all(char_in_script(c, script) for c in letters)


# Wikipedia interlanguage glosses, e.g. ( Tamil : சென்னai , English : Chennai ;)
WIKI_LANGUAGE_GLOSS = re.compile(
    r"\(\s*"
    r"(?:"
    r"[\u0900-\u0C7F\u0D00-\u0D7F\u0980-\u09FF\u0600-\u06FF\u0B80-\u0BFF\u0A00-\u0A7F\u0A80-\u0AFFA-Za-z]+"
    r"\s*:\s*"
    r"[^()]+?"
    r")"
    r"(?:\s*,\s*"
    r"[\u0900-\u0C7F\u0D00-\u0D7F\u0980-\u09FF\u0600-\u06FF\u0B80-\u0BFF\u0A00-\u0A7F\u0A80-\u0AFFA-Za-z]+"
    r"\s*:\s*"
    r"[^()]+?"
    r")*"
    r"\s*;?\s*\)",
    re.UNICODE,
)

_EMPTY_PAREN_GROUP = re.compile(r"\(\s*[,;:\.\s\"'\-]*\)")
_MULTI_COMMA = re.compile(r"(?:\s*,\s*){2,}")
# Leftover label/colon debris after foreign tokens are stripped from mixed glosses.
_GLOSS_LABEL_RESIDUE = re.compile(
    r"\(\s*"
    r"[\u0900-\u0C7F\u0D00-\u0D7F\u0980-\u09FF\u0600-\u06FF\u0B80-\u0BFF\u0A00-\u0A7F\u0A80-\u0AFF]+"
    r"\s*,\s*:\s*,\s*",
    re.UNICODE,
)
_GLOSS_ORPHAN_COLON = re.compile(r"\(\s*:\s*,\s*")


def remove_foreign_script_words(text: str, language_code: str) -> tuple[str, int]:
    """
    Drop tokens whose letters fall outside the language's expected script.

    Also removes Wikipedia-style language gloss parentheticals and Latin words.
    """
    script = expected_script(language_code)
    if not script:
        return text, 0

    removed = 0
    glosses = WIKI_LANGUAGE_GLOSS.findall(text)
    if glosses:
        removed += len(glosses)
        text = WIKI_LANGUAGE_GLOSS.sub("", text)

    parts: list[str] = []
    last = 0
    for match in TOKEN_PATTERN.finditer(text):
        parts.append(text[last : match.start()])
        token = match.group()
        if token_belongs_to_script(token, script):
            parts.append(token)
        else:
            removed += 1
        last = match.end()
    parts.append(text[last:])
    text = "".join(parts)

    text = _EMPTY_PAREN_GROUP.sub("", text)
    text = _GLOSS_LABEL_RESIDUE.sub("(", text)
    text = _GLOSS_ORPHAN_COLON.sub("(", text)
    text = _MULTI_COMMA.sub(", ", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\(\s*,\s*", "(", text)
    text = re.sub(r"\s*,\s*\)", ")", text)
    return text.strip(), removed


def _token_is_pure_latin(token: str) -> bool:
    letters = [c for c in token if c.isalpha()]
    if not letters:
        return False
    return all(is_latin_letter(c) for c in letters)


def remove_latin_words(text: str) -> tuple[str, int]:
    """Remove tokens written entirely in the Latin script."""
    removed = 0
    parts: list[str] = []
    last = 0
    for match in TOKEN_PATTERN.finditer(text):
        parts.append(text[last : match.start()])
        token = match.group()
        if _token_is_pure_latin(token):
            removed += 1
        else:
            parts.append(token)
        last = match.end()
    parts.append(text[last:])
    text = "".join(parts)

    text = _EMPTY_PAREN_GROUP.sub("", text)
    text = _GLOSS_LABEL_RESIDUE.sub("(", text)
    text = _GLOSS_ORPHAN_COLON.sub("(", text)
    text = _MULTI_COMMA.sub(", ", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\(\s*,\s*", "(", text)
    text = re.sub(r"\s*,\s*\)", ")", text)
    return text.strip(), removed
