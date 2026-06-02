"""Unicode script detection for languages not covered by fastText lid.176."""

from __future__ import annotations

# Inclusive Unicode ranges per script (BMP blocks used by scheduled Indic languages).
SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "devanagari": [(0x0900, 0x097F)],
    "bengali": [(0x0980, 0x09FF)],
    "gurmukhi": [(0x0A00, 0x0A7F)],
    "gujarati": [(0x0A80, 0x0AFF)],
    "oriya": [(0x0B00, 0x0B7F)],
    "tamil": [(0x0B80, 0x0BFF)],
    "telugu": [(0x0C00, 0x0C7F)],
    "kannada": [(0x0C80, 0x0CFF)],
    "malayalam": [(0x0D00, 0x0D7F)],
    "ol_chiki": [(0x1C50, 0x1C7F)],
    "meetei_mayek": [(0xABC0, 0xABFF), (0xAA00, 0xAA5F)],
    "arabic": [(0x0600, 0x06FF), (0x0750, 0x077F)],
}


def _in_script(char: str, script: str) -> bool:
    code = ord(char)
    for start, end in SCRIPT_RANGES.get(script, []):
        if start <= code <= end:
            return True
    return False


def script_character_ratio(text: str, script: str) -> float:
    """
    Share of letters/symbols that belong to the expected script (0.0–1.0).

    Used as a confidence score when fastText has no label for the target language
    (e.g. Bodo / brx).
    """
    relevant = [c for c in text if c.isalpha() or _in_script(c, script)]
    if not relevant:
        return 0.0
    in_script = sum(1 for c in relevant if _in_script(c, script))
    return in_script / len(relevant)
