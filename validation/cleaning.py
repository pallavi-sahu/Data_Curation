"""Conservative text cleaning for curated JSONL records."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from validation.config import ValidationThresholds
from validation.metrics.pii import PII_PATTERNS
from validation.tokenization import (
    EMOJI_PATTERN,
    URL_PATTERN,
    has_terminal_punctuation,
    remove_foreign_script_words,
    remove_latin_words,
    split_sentences,
)

try:
    import ftfy

    FTFY_AVAILABLE = True
except ImportError:
    FTFY_AVAILABLE = False

# Same pairs checked by validation/metrics/punctuation.py
BRACKET_PAIRS: tuple[tuple[str, str], ...] = (
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("«", "»"),
    ("\u201c", "\u201d"),
    ('"', '"'),
    ("\u2018", "\u2019"),
)

# Languages that typically use Arabic full stop in digital text.
_ARABIC_STOP_LANGS = frozenset({"ur", "sd", "ks"})

# Indic languages where danda (।) is the preferred sentence mark.
_INDIC_LANGS = frozenset(
    {
        "as",
        "bn",
        "brx",
        "doi",
        "gu",
        "hi",
        "kn",
        "ks",
        "kok",
        "mai",
        "ml",
        "mni",
        "mr",
        "ne",
        "or",
        "pa",
        "sa",
        "sat",
        "sd",
        "ta",
        "te",
        "ur",
    }
)

REDACTED = "[REDACTED]"
MULTISPACE = re.compile(r" {2,}")
SPACE_BEFORE_PUNCT = re.compile(r" +([.,;:!?)\]}])")
FORMAT_CHAR_CATEGORIES = frozenset({"Cf", "Cc", "Co"})
# Scraping artifacts: ". . . .", "----", ">>>", "<<<" (keep 1–2 of each).
DECORATIVE_DOT_RUN = re.compile(r"(?:\.\s*){3,}")
DECORATIVE_DASH_RUN = re.compile(r"(?:[—–\-]\s*){3,}")
DECORATIVE_GT_RUN = re.compile(r"(?:>\s*){3,}")
DECORATIVE_LT_RUN = re.compile(r"(?:<\s*){3,}")
ELLIPSIS_CHAR = re.compile(r"…+")

# manatelugukathalu.com serial-page navigation boilerplate
SERIAL_NAV_PATTERN = re.compile(
    r"(?:"
    r"(?:ఇక\s+)?[\u0C00-\u0C7F][\u0C00-\u0C7F\s\-]*?-\s*పార్ట్\s*\d+\s*కోసం\s*ఇక్కడ\s*క్లిక్\s*చేయండి"
    r"|(?:ఇక\s+)?[\u0C00-\u0C7F][\u0C00-\u0C7F\s\-]*?-\s*పార్ట్\s*\d+\s*చదవండి"
    r"|[\u0C00-\u0C7F][\u0C00-\u0C7F\s\-:|]*?\s*కోసం\s*ఇక్కడ\s*క్లిక్\s*చేయండి"
    r"|గత\s+ఎపిసోడ్\s*ల\s*కోసం\s*ఇక్కడ\s*క్లిక్\s*చేయండి"
    r"|జరిగిన\s+కథ\s*కోసం\s*ఇక్కడ\s*క్లిక్\s*చేయండి"
    r")"
    r"\s*\.?\s*",
    re.UNICODE,
)
# Scraped paragraph prefixes like "((((((((" from manatelugukathalu.com
LEADING_PAREN_RUN = re.compile(r"^\({4,}")


@dataclass
class CleaningConfig:
    """Options for the JSONL cleaning pipeline."""

    enable_llm_sensitivity: bool = False
    llm_provider: str = "gemini"
    max_sensitivity_score: float = ValidationThresholds.max_sensitivity_score
    drop_sensitive_records: bool = True


@dataclass
class CleanStats:
    """Per-record and aggregate cleaning counters."""

    records_in: int = 0
    records_out: int = 0
    ftfy_applied: int = 0
    pii_redactions: int = 0
    links_removed: int = 0
    emoji_removed: int = 0
    format_chars_removed: int = 0
    serial_nav_removed: int = 0
    leading_parens_removed: int = 0
    foreign_script_words_removed: int = 0
    latin_words_removed: int = 0
    punctuation_fixed: int = 0
    brackets_fixed: int = 0
    decorative_dot_dash_removed: int = 0
    empty_after_clean: int = 0
    llm_sensitivity_checked: int = 0
    sensitive_removed: int = 0
    llm_sensitivity_failed: int = 0
    per_record: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "records_in": self.records_in,
            "records_out": self.records_out,
            "ftfy_applied": self.ftfy_applied,
            "pii_redactions": self.pii_redactions,
            "links_removed": self.links_removed,
            "emoji_removed": self.emoji_removed,
            "format_chars_removed": self.format_chars_removed,
            "serial_nav_removed": self.serial_nav_removed,
            "leading_parens_removed": self.leading_parens_removed,
            "foreign_script_words_removed": self.foreign_script_words_removed,
            "latin_words_removed": self.latin_words_removed,
            "punctuation_fixed": self.punctuation_fixed,
            "brackets_fixed": self.brackets_fixed,
            "decorative_dot_dash_removed": self.decorative_dot_dash_removed,
            "empty_after_clean": self.empty_after_clean,
            "llm_sensitivity_checked": self.llm_sensitivity_checked,
            "sensitive_removed": self.sensitive_removed,
            "llm_sensitivity_failed": self.llm_sensitivity_failed,
        }


def default_sentence_terminator(language_code: str) -> str:
    if language_code in _ARABIC_STOP_LANGS:
        return "\u06D4"
    if language_code in _INDIC_LANGS:
        return "\u0964"
    return "."


def apply_ftfy(text: str) -> tuple[str, bool]:
    if not FTFY_AVAILABLE:
        return text, False
    fixed = ftfy.fix_text(text, normalization="NFC")
    return fixed, fixed != text


def redact_pii(text: str) -> tuple[str, int]:
    """Replace detected PII spans with a placeholder (record is kept)."""
    spans: list[tuple[int, int]] = []
    for _entity_type, pattern, _score in PII_PATTERNS:
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end()))

    if not spans:
        return text, 0

    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    count = len(merged)
    for start, end in reversed(merged):
        text = text[:start] + REDACTED + text[end:]
    return text, count


def remove_links(text: str) -> tuple[str, int]:
    """Strip http(s):// and www. URLs from paragraph text."""
    matches = URL_PATTERN.findall(text)
    if not matches:
        return text, 0
    cleaned = URL_PATTERN.sub("", text)
    cleaned = MULTISPACE.sub(" ", cleaned)
    cleaned = SPACE_BEFORE_PUNCT.sub(r"\1", cleaned)
    return cleaned.strip(), len(matches)


def remove_format_characters(text: str) -> tuple[str, int]:
    """Strip invisible Unicode format/control/private-use characters (Cf, Cc, Co)."""
    removed = 0
    kept: list[str] = []
    for char in text:
        if unicodedata.category(char) in FORMAT_CHAR_CATEGORIES:
            removed += 1
        else:
            kept.append(char)
    return "".join(kept), removed


def remove_serial_navigation(text: str) -> tuple[str, int]:
    """Strip Telugu serial part navigation links (manatelugukathalu.com boilerplate)."""
    matches = SERIAL_NAV_PATTERN.findall(text)
    if not matches:
        return text, 0
    cleaned = SERIAL_NAV_PATTERN.sub("", text)
    cleaned = MULTISPACE.sub(" ", cleaned)
    return cleaned.strip(), len(matches)


def remove_leading_paren_runs(text: str) -> tuple[str, int]:
    """Strip long runs of opening parens at paragraph start (scraping artifacts)."""
    match = LEADING_PAREN_RUN.match(text)
    if not match:
        return text, 0
    return text[match.end() :].lstrip(), len(match.group())


def remove_emoji(text: str) -> tuple[str, int]:
    matches = EMOJI_PATTERN.findall(text)
    if not matches:
        return text, 0
    cleaned = EMOJI_PATTERN.sub("", text)
    cleaned = MULTISPACE.sub(" ", cleaned)
    return cleaned, len(matches)


def remove_decorative_dot_dash_runs(text: str) -> tuple[str, int]:
    """Strip runs of 3+ dots, dashes, or angle brackets; keep up to two."""
    removed = 0
    for pattern in (
        DECORATIVE_DOT_RUN,
        DECORATIVE_DASH_RUN,
        DECORATIVE_GT_RUN,
        DECORATIVE_LT_RUN,
        ELLIPSIS_CHAR,
    ):
        matches = pattern.findall(text)
        removed += len(matches)
        text = pattern.sub("", text)
    if removed:
        text = MULTISPACE.sub(" ", text)
        text = SPACE_BEFORE_PUNCT.sub(r"\1", text)
    return text, removed


def fix_unbalanced_brackets(text: str) -> tuple[str, bool]:
    changed = False
    for open_ch, close_ch in BRACKET_PAIRS:
        diff = text.count(open_ch) - text.count(close_ch)
        if diff > 0:
            text += close_ch * diff
            changed = True
        elif diff < 0:
            text = open_ch * (-diff) + text
            changed = True
    return text, changed


def fix_terminal_punctuation(text: str, language_code: str) -> tuple[str, bool]:
    sentences = split_sentences(text, language_code)
    if not sentences:
        return text, False

    terminator = default_sentence_terminator(language_code)
    fixed_sentences: list[str] = []
    changed = False

    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped:
            continue
        if not has_terminal_punctuation(stripped):
            stripped = stripped + terminator
            changed = True
        fixed_sentences.append(stripped)

    if not fixed_sentences:
        return text, False

    return " ".join(fixed_sentences), changed


def clean_paragraph(
    text: str,
    language_code: str = "",
    *,
    remove_latin: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Run all cleaning steps on paragraph text. Returns (cleaned_text, change_flags)."""
    changes: dict[str, Any] = {
        "ftfy_applied": False,
        "pii_redactions": 0,
        "links_removed": 0,
        "emoji_removed": 0,
        "format_chars_removed": 0,
        "serial_nav_removed": 0,
        "leading_parens_removed": 0,
        "foreign_script_words_removed": 0,
        "latin_words_removed": 0,
        "punctuation_fixed": False,
        "brackets_fixed": False,
        "decorative_dot_dash_removed": 0,
    }

    if FTFY_AVAILABLE:
        text, changes["ftfy_applied"] = apply_ftfy(text)
    else:
        text = unicodedata.normalize("NFC", text)

    text, changes["format_chars_removed"] = remove_format_characters(text)
    text, changes["pii_redactions"] = redact_pii(text)
    text, changes["links_removed"] = remove_links(text)
    text, changes["emoji_removed"] = remove_emoji(text)
    text, changes["decorative_dot_dash_removed"] = remove_decorative_dot_dash_runs(text)
    text, changes["serial_nav_removed"] = remove_serial_navigation(text)
    text, changes["leading_parens_removed"] = remove_leading_paren_runs(text)
    text, changes["foreign_script_words_removed"] = remove_foreign_script_words(
        text, language_code
    )
    if remove_latin:
        text, changes["latin_words_removed"] = remove_latin_words(text)
    text, bracket_changed = fix_unbalanced_brackets(text)
    changes["brackets_fixed"] = bracket_changed
    text, changes["punctuation_fixed"] = fix_terminal_punctuation(text, language_code)
    text = text.strip()
    return text, changes


def clean_record(
    record: dict[str, Any],
    *,
    remove_latin: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a copy of the JSONL record with a cleaned ``paragraph`` field."""
    cleaned = dict(record)
    paragraph = str(record.get("paragraph") or "")
    language_code = str(record.get("language_code") or "")

    cleaned_text, changes = clean_paragraph(
        paragraph,
        language_code,
        remove_latin=remove_latin,
    )
    cleaned["paragraph"] = cleaned_text
    return cleaned, changes


def assess_llm_sensitivity(
    text: str,
    language_code: str,
    config: CleaningConfig,
) -> tuple[bool, dict[str, Any]]:
    """
    Return whether text exceeds the sensitivity threshold (LLM only).

    On LLM failure the record is treated as not sensitive so it is kept.
    """
    from validation.llm.client import (
        configure_llm_provider,
        evaluate_sensitivity,
        get_last_llm_error,
        get_llm_provider,
        llm_api_key_hint,
        llm_available,
    )

    configure_llm_provider(config.llm_provider)

    details: dict[str, Any] = {
        "method": "llm",
        "provider": get_llm_provider(),
        "threshold": config.max_sensitivity_score,
    }

    if not llm_available():
        details["method"] = "llm_unavailable"
        details["error"] = llm_api_key_hint()
        details["sensitive"] = False
        details["dropped"] = False
        return False, details

    result = evaluate_sensitivity(text, language_code)
    if result is None:
        details["method"] = "llm_failed"
        details["error"] = get_last_llm_error()
        details["sensitive"] = False
        details["dropped"] = False
        return False, details

    score = float(result.get("score", 0.0))
    sensitive = score > config.max_sensitivity_score
    details.update(
        {
            "score": round(score, 4),
            "categories": list(result.get("categories") or []),
            "summary": result.get("summary"),
            "sensitive": sensitive,
            "dropped": sensitive and config.drop_sensitive_records,
        }
    )
    return sensitive, details
