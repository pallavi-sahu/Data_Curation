"""Regex-based TTS cleanup for patterns listed in validation_regex.txt."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

try:
    import ftfy
except ImportError:  # pragma: no cover
    ftfy = None  # type: ignore[assignment]

# TTS paragraph word-count targets
TTS_MIN_WORDS = 300
TTS_MAX_WORDS = 450
TTS_SPLIT_TARGET_LOW = 300
TTS_SPLIT_TARGET_HIGH = 400

MULTISPACE = re.compile(r" {2,}")
SPACE_BEFORE_PUNCT = re.compile(r" +([.,;:!?)\]}])")
ORPHAN_PUNCT_RUN = re.compile(r"(?:\s*[,;:]\s*){2,}")
DANGLING_CONNECTORS = re.compile(
    r"(?:^|\s)(?:and|or|the|a|an|of|in|on|at|to|from|with)\s*(?=[.,;:!?।\u0964]|$)",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r'[.!?।॥]["\')\]]*(?:\s+|$)')
BOX_PLACEHOLDER = re.compile(r"[\u25a0-\u25ff\u2610-\u2612]+")

# ---------------------------------------------------------------------------
# Pattern registry — order is critical (compound before simple digit patterns)
# ---------------------------------------------------------------------------
PATTERNS: list[tuple[str, re.Pattern[str]]] = []


def _pat(name: str, pattern: str, flags: int = 0) -> None:
    PATTERNS.append((name, re.compile(pattern, flags | re.UNICODE)))


# TIER 1 — Long / compound patterns that contain digits internally
_pat(
    "isbn_issn_doi",
    r"\b(?:ISBN|ISSN|DOI)[:\s\-]*[\d\-xX\.\/]+",
    re.IGNORECASE,
)
_pat("ip_address", r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_pat(
    "hex_color",
    r"#[0-9A-Fa-f]{3,8}\b"
    r"|RGB\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)",
    re.IGNORECASE,
)
_pat(
    "file_paths",
    r"(?<!\w)(?:[a-zA-Z]:[\\/]|\.{1,2}[\\/]|\/)[\w\-\.\/\\]+"
    r"|\b\w+\.(?:jsonl|json|txt|csv|pdf|docx?|xlsx?|pptx?|zip|tgz|tar|gz|"
    r"py|js|ts|html|css|xml|yaml|yml|md|sh|bat|log|dat|db|sql|mp3|wav|ogg)\b"
    r"|\b[a-z]{2}\.wikipedia\.org/wiki/\S+",
    re.IGNORECASE,
)
_pat(
    "html_entities",
    r"&(?:nbsp|amp|lt|gt|quot|apos|copy|reg|trade|mdash|ndash|hellip|"
    r"#\d+|#x[0-9A-Fa-f]+);",
)
_pat(
    "latlon_block",
    r"Coordinates\s*:\s*[\d°′″NSEWnsew\s\/\.]+(?:[NSEWnsew][\s\/\.]*)+"
    r"|\d+°\d+[′\']\s*\d*[″\"]?\s*[NSns]\s*/\s*\d+\.?\d*°\s*[NSns]"
    r"|\d+°\d+[′\']\s*\d*[″\"]?\s*[EWew]\s*/\s*\d+\.?\d*°\s*[EWew]",
    re.IGNORECASE,
)
_pat("dms_notation", r"\d+°\s*\d+[′\']\s*\d*\.?\d*[″\"]?\s*[NSEWnsew]")
_pat("temperature", r"-?\d+\.?\d*\s*°\s*[CcFfKk]\b")
_pat(
    "scientific_notation",
    r"\d+\.?\d*\s*[×x]\s*10\s*\^?\s*-?\d+"
    r"|\d+\.?\d*[eE][+\-]?\d+",
    re.IGNORECASE,
)
_pat(
    "chemical_formula",
    r"\b(?=[A-Z][a-zA-Z]*\d)(?:[A-Z][a-z]?\d*)+\b"
    r"|\bpH\s*\d+\.?\d*\b",
)
_pat(
    "subscript_superscript",
    r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ]+"
    r"|[₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎]+",
)
_pat(
    "bank_card_numbers",
    r"\b\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4}\b"
    r"|\b\d{13,19}\b",
)
_pat(
    "currency",
    r"(?:₹|Rs\.?|USD|EUR|GBP|INR|\$|€|£|¥)\s*[\d,]+(?:\.\d+)?"
    r"|[\d,]+(?:\.\d+)?\s*(?:rupees?|dollars?|euros?|pounds?)\b",
    re.IGNORECASE,
)
_pat("numeric_commas", r"\b\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\b")

# TIER 2 — Unit / scale patterns
_UNITS = (
    r"km/h|m/s|km²|m²|km³|m³|sq\.?\s*km|sq\.?\s*m"
    r"|km\b|cm\b|mm\b|nm\b|μm\b"
    r"|kg\b|g\b|mg\b|μg\b|lb\b|oz\b"
    r"|kPa\b|MPa\b|GPa\b|Pa\b|bar\b|atm\b"
    r"|kHz\b|MHz\b|GHz\b|Hz\b"
    r"|kW\b|MW\b|GW\b|W\b"
    r"|kV\b|mV\b|V\b|mA\b|A\b"
    r"|ml\b|mL\b|L\b|l\b"
    r"|ha\b|acre\b"
    r"|ft\b|in\b|mi\b|yd\b"
    r"|mph\b|kmph\b|knot\b"
    r"|cal\b|kcal\b|J\b|kJ\b|MJ\b"
    r"|mol\b|mmol\b"
    r"|dB\b|lux\b|rpm\b"
    r"|metre?s?\b|kilometer?s?\b|centimeter?s?\b|millimeter?s?\b"
    r"|meters?\b|metres?\b|mts?\b|liters?\b|litres?\b"
)
_pat("measurement_units", r"-?\d+\.?\d*\s*(?:" + _UNITS + r")", re.IGNORECASE)
_pat("percentage", r"-?\d+\.?\d*\s*%")
_pat(
    "timezone",
    r"\b(?:UTC|GMT|IST|EST|PST|CST|MST|BST|CET|JST|AEST|"
    r"HST|AKST|AST|NST|ADT|PDT|MDT|CDT|EDT|NZST|"
    r"UTC[+\-]\d{1,2}(?::\d{2})?|GMT[+\-]\d{1,2}(?::\d{2})?)\b",
)
_pat("clock_time", r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\b")
_pat(
    "duration",
    r"\b\d+\s*h(?:r|rs|ours?)?\b"
    r"|\b\d+\s*m(?:in|ins|inutes?)\b"
    r"|\b\d+\s*(?:seconds?|secs?)\b",
    re.IGNORECASE,
)
_pat(
    "dates",
    r"\b\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}\b"
    r"|\b\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}\b"
    r"|\b\d{2,4}\s*[-–—]\s*\d{2,4}\b",
)
_pat("ratio_scale", r"\b\d+\s*:\s*\d+\b")
_pat(
    "version_model",
    r"\bv\d+\.\d+(?:\.\d+)*\b"
    r"|\b(?:version|ver|rev)\s*\d+[\.\d]*\b"
    r"|\b(?:iPhone|iPad|Android|Windows|macOS|iOS|Ubuntu|Debian)\s*\d+[\.\d]*\b",
    re.IGNORECASE,
)
_pat(
    "page_chapter",
    r"\b(?:page|pg)\s*\d+(?:\s*of\s*\d+)?\b"
    r"|\b(?:chapter|ch)\s*\d+(?:\.\d+)?\b"
    r"|\b(?:lesson|part|section|unit|பாடம்|అధ్యాయం|ಅಧ್ಯಾಯ|അദ്ധ്യായം|"
    r"அதிகாரம்|பாகம்|పాఠం|अध्याय|പാഠം|પાઠ|পাঠ)\s*\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)
_pat(
    "fig_table_ref",
    r"\b(?:fig(?:ure)?|table|chart|diagram|image|photo)\s*\.?\s*\d+(?:\.\d+)?\b"
    r"|\b(?:சித்திரம்|படம்|Fig|Table|चित्र|ചിത്രം|చిత్రం|ಚಿತ್ರ|ଚିତ୍ର|ছবি)\s*\.?\s*\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)

# TIER 3 — Simple / standalone patterns
_pat(
    "roman_numerals",
    r"\b(?=[MDCLXVI])M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})"
    r"(?:IX|IV|V?I{0,3})\b(?<=[MDCLXVI])"
    r"|\b(?:Chapter|Part|Volume|Vol\.|Book)\s+[IVXLCDM]{1,8}\b",
    re.IGNORECASE,
)
_pat(
    "citation_brackets",
    r"\[\s*\d+\s*\]"
    r"|\[\s*\]"
    r"|\[citation needed\]"
    r"|\[note\s*\d*\]"
    r"|\[edit\]",
    re.IGNORECASE,
)
_pat("footnote_markers", r"[†‡§¶]|(?<!\w)[*†‡](?!\w)")
_pat("ordinals_english", r"\b\d+\s*(?:st|nd|rd|th)\b", re.IGNORECASE)
_pat(
    "ordinals_indic",
    r"\d+\s*(?:"
    r"వ\b|వతి\b|ஆம்\b|வது\b|ஆவது\b|ನೇ\b|ನೆ\b|-ാം\b|aam\b|"
    r"vīṃ\b|वीं\b|वां\b|वाँ\b|वा\b|ਵੀਂ\b|ਵਾਂ\b|ম\b|তম\b"
    r"|\d+\s+வயது\b"
    r")",
)
_pat("pin_postal", r"\b\d{4,6}\b")
_pat(
    "license_plate",
    r"\b[A-Z]{2}[\s\-]?\d{2}[\s\-]?[A-Z]{1,2}[\s\-]?\d{4}\b",
)
_pat(
    "author_year_citation",
    r"\(\s*[A-Z][a-zA-Z]+(?:\s+(?:et al\.?|and|&)\s+[A-Z][a-zA-Z]+)?\s*,\s*\d{4}[a-z]?\s*\)"
    r"|\bet al\.?\b"
    r"|\bibid\.?\b"
    r"|\bop\.\s*cit\.?\b"
    r"|\bpp?\.\s*\d+(?:\s*[-–—]\s*\d+)?\b",
    re.IGNORECASE,
)
_pat("hashtag_mention", r"#\w+|@\w+")
_pat("copyright_trademark", r"[©®™℠]")
_pat(
    "math_operators",
    r"[×÷±∓⊕⊗∞∑∏√∫∂∇≤≥≠≈∝⊂⊃∈∉∀∃]"
    r"|\s[+\-\*<>=]\s",
)
_pat(
    "kaomoji_ascii",
    r'(?<!\w)(?:[:;=8B][\-oO\*\'"]?[\)\(\]\[dDpP\/\:\}\{@\|\\oO]'
    r'|[\)\(\]\[dDpP\/\:\}\{@\|\\oO][\-oO\*\'"]?[:;=8B])(?!\w)'
    r"|(?:\^\^|>_<|o\.O|O\.o|@_@|\._\.|=_=|:D|XD|><)",
)

# TIER 4 — Catch-all digit / symbol cleanup
_pat("decimals", r"\b\d+\.\d+\b")
_pat("degree_symbol", r"°")
_pat("list_bullets_inline", r"(?:^|\s)[•·▪▫▸▹►◆◇○●]\s+", re.MULTILINE)
_pat("backtick", r"`+")
_pat("pipe_char", r"\|")
_pat("asterisk_noise", r"\*+")
_pat("bare_integers", r"\d+")

_BLOCK = [
    re.compile(
        r"^[ \t]*(?:See also|References?|Further reading|External links?|"
        r"Notes?|Bibliography|Sources?|Footnotes?|citation needed)\s*:?.*$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^[ \t]*[-=*]{3,}[ \t]*$", re.MULTILINE),
    re.compile(r"^[ \t]*(?:\d+\.|[a-zA-Z]\)|[ivxlcdm]+\.)[ \t]+", re.MULTILINE),
    re.compile(r"^[ \t]*\|[^\n]+\|[ \t]*$", re.MULTILINE),
    re.compile(r"^[ \t]*(?:\.[ \t]*)+(?=\S)", re.MULTILINE),
]

LIST_BULLET_MARKERS = re.compile(
    r"(?:"
    r"^\s*[•●○▪▫◦]\s+"
    r"|^\s*\d+\.\s+"
    r"|^\s*[a-zA-Z]\)\s+"
    r"|^\s*\|\s*[^|]+\s*\|"
    r")",
    re.MULTILINE,
)

# Backward-compatible alias for tests / callers that referenced the old name.
TTS_CLEANUP_PATTERNS = tuple(PATTERNS)


@dataclass
class TTSCleanupStats:
    records_in: int = 0
    records_out: int = 0
    records_dropped_empty: int = 0
    records_dropped_list_heavy: int = 0
    records_split: int = 0
    records_overflow: int = 0
    removals_by_label: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "records_in": self.records_in,
            "records_out": self.records_out,
            "records_dropped_empty": self.records_dropped_empty,
            "records_dropped_list_heavy": self.records_dropped_list_heavy,
            "records_split": self.records_split,
            "records_overflow": self.records_overflow,
            "removals_by_label": dict(sorted(self.removals_by_label.items())),
            "total_removals": sum(self.removals_by_label.values()),
        }


@dataclass
class TTSChunk:
    text: str
    is_overflow: bool = False


def paragraph_word_count(text: str) -> int:
    return len((text or "").split())


def is_list_heavy_paragraph(text: str, *, min_markers: int = 5) -> bool:
    """True when the paragraph looks like a list/table rather than prose."""
    markers = LIST_BULLET_MARKERS.findall(text)
    if len(markers) >= min_markers:
        return True
    if text.count("|") >= 6:
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) >= 4:
        numbered = sum(1 for line in lines if re.match(r"^\s*\d+\.\s+", line))
        if numbered / len(lines) >= 0.4:
            return True
    return False


def _strip_wiki_markup(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\[\[([^\[\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[{2,}", "", text)
    text = re.sub(r"\]{2,}", "", text)
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\[([^\[\]]*)\]", r"\1", text)
    text = text.replace("[", "").replace("]", "")
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = text.replace("{", "").replace("}", "")
    return text


def _strip_parens(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\([^()]*\)", "", text)
    text = text.replace("(", "").replace(")", "")
    return text


def _normalize_whitespace(text: str) -> str:
    text = MULTISPACE.sub(" ", text)
    text = SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = ORPHAN_PUNCT_RUN.sub(", ", text)
    text = DANGLING_CONNECTORS.sub(" ", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\[\s*\]", "", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _cosmetic_cleanup(text: str) -> str:
    lines = text.splitlines()
    cleaned_lines: list[str] = []
    for line in lines:
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line and not re.fullmatch(r"[\W\s]+", line):
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines).strip()
    text = re.sub(r"\s+([।॥\.!?])", r"\1", text)
    text = re.sub(r"^[ \t]*[।॥]+[ \t]*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*(?:\.[ \t]*)+", "", text, flags=re.MULTILINE)
    return text.strip()


def _split_at_sentence_boundary(
    text: str,
    target_words: int,
    *,
    min_words: int,
    max_words: int,
) -> tuple[str, str]:
    """Split *text* near *target_words* on a sentence boundary when possible."""
    words = text.split()
    total = len(words)
    if total <= max_words:
        return text, ""

    lo = max(min_words, 1)
    hi = min(total - 1, max_words)
    if lo > hi:
        split_at = min(target_words, total - 1)
        return " ".join(words[:split_at]), " ".join(words[split_at:])

    best_idx: int | None = None
    best_distance = total + 1
    for idx in range(lo, hi + 1):
        prefix = " ".join(words[:idx])
        if not SENTENCE_END.search(prefix):
            continue
        distance = abs(idx - target_words)
        if distance < best_distance:
            best_distance = distance
            best_idx = idx

    if best_idx is None:
        split_at = min(max(target_words, min_words), total - 1)
    else:
        split_at = best_idx

    return " ".join(words[:split_at]), " ".join(words[split_at:])


def split_paragraph_for_tts(text: str) -> list[TTSChunk]:
    """
    Split long paragraphs into TTS-friendly chunks.

    Rules:
      - 300–450 words: keep as one chunk
      - 451–599 words: first chunk ~300–400 words, remainder marked overflow
      - >=600 words: bisect, then apply rules recursively to each half
    """
    text = (text or "").strip()
    if not text:
        return []

    word_count = paragraph_word_count(text)
    if word_count <= TTS_MAX_WORDS:
        return [TTSChunk(text)]

    if word_count < 600:
        first, rest = _split_at_sentence_boundary(
            text,
            target_words=(TTS_SPLIT_TARGET_LOW + TTS_SPLIT_TARGET_HIGH) // 2,
            min_words=TTS_SPLIT_TARGET_LOW,
            max_words=TTS_SPLIT_TARGET_HIGH,
        )
        chunks = [TTSChunk(first)]
        if rest.strip():
            chunks.append(TTSChunk(rest, is_overflow=True))
        return chunks

    midpoint = word_count // 2
    first, rest = _split_at_sentence_boundary(
        text,
        target_words=midpoint,
        min_words=max(TTS_MIN_WORDS, midpoint - 75),
        max_words=midpoint + 75,
    )
    chunks: list[TTSChunk] = []
    for part in (first, rest):
        if part.strip():
            chunks.extend(split_paragraph_for_tts(part))
    return chunks


def clean_tts_text(
    text: str,
    *,
    stats: TTSCleanupStats | None = None,
) -> tuple[str, dict[str, int]]:
    """Remove TTS-hostile patterns from paragraph text."""
    counts: dict[str, int] = {}

    if ftfy is not None:
        text = ftfy.fix_text(text)

    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    text = BOX_PLACEHOLDER.sub(" ", text)
    text = unicodedata.normalize("NFC", text)
    text = _strip_wiki_markup(text)
    text = _strip_parens(text)

    for blk in _BLOCK:
        matches = blk.findall(text)
        if matches:
            counts["block_patterns"] = counts.get("block_patterns", 0) + len(matches)
            text = blk.sub(" ", text)

    for label, pattern in PATTERNS:
        matches = pattern.findall(text)
        if not matches:
            continue
        removed = len(matches) if isinstance(matches[0], str) else len(matches)
        counts[label] = counts.get(label, 0) + removed
        text = pattern.sub(" ", text)

    text = _cosmetic_cleanup(text)
    text = _normalize_whitespace(text)

    if stats is not None:
        for label, count in counts.items():
            stats.removals_by_label[label] = stats.removals_by_label.get(label, 0) + count

    return text, counts


def clean_tts_record(
    record: dict[str, Any],
    *,
    stats: TTSCleanupStats | None = None,
    drop_list_heavy: bool = True,
    list_heavy_min_markers: int = 5,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Clean the ``paragraph`` field of a JSONL record."""
    meta: dict[str, Any] = {"dropped": False, "drop_reason": None, "removals": {}}
    paragraph = str(record.get("paragraph") or "")

    if drop_list_heavy and is_list_heavy_paragraph(
        paragraph, min_markers=list_heavy_min_markers
    ):
        meta["dropped"] = True
        meta["drop_reason"] = "list_heavy"
        if stats is not None:
            stats.records_dropped_list_heavy += 1
        return None, meta

    cleaned_text, removals = clean_tts_text(paragraph, stats=stats)
    meta["removals"] = removals

    if not cleaned_text.strip():
        meta["dropped"] = True
        meta["drop_reason"] = "empty_after_cleanup"
        if stats is not None:
            stats.records_dropped_empty += 1
        return None, meta

    cleaned = dict(record)
    cleaned["paragraph"] = cleaned_text
    return cleaned, meta
