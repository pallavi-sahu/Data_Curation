"""Validation configuration, thresholds, and language support."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from curation.config import INDIC_LANGUAGE_CODES, LANGUAGE_SCRIPT

# Languages with dedicated JSONL files in final/ (high-resource focus).
HIGH_RESOURCE_LANGUAGES: frozenset[str] = frozenset(
    {
        "as",  # Assamese
        "gu",  # Gujarati
        "kn",  # Kannada
        "ml",  # Malayalam
        "mr",  # Marathi
        "or",  # Odia
        "pa",  # Punjabi
        "sd",  # Sindhi
        "ta",  # Tamil
        "te",  # Telugu
        "ur",  # Urdu
    }
)

# Map final/*.jsonl filenames to ISO 639-1 codes.
FILENAME_TO_LANG: dict[str, str] = {
    "assamese": "as",
    "bodo": "brx",
    "gujarati": "gu",
    "kannada": "kn",
    "malayalam": "ml",
    "marathi": "mr",
    "odia": "or",
    "punjabi": "pa",
    "sindhi": "sd",
    "tamil": "ta",
    "telugu": "te",
    "urdu": "ur",
    "maithili": "mai",
    "manipuri": "mni",
    "santali": "sat",
}


@dataclass
class ValidationThresholds:
    """Per-metric thresholds used to flag records."""

    max_link_density: float = 0.05
    max_nsw_density: float = 0.15
    max_special_char_ratio: float = 0.08
    max_abbreviation_density: float = 0.10
    max_emoji_count: int = 0
    max_pii_entities: int = 0
    min_unique_word_ratio: float = 0.25
    max_mean_sentence_length: float = 80.0
    max_long_sentence_count: int = 3
    max_missing_terminal_punct_ratio: float = 0.60
    max_all_caps_word_ratio: float = 0.05
    max_unbalanced_quote_pairs: int = 0
    min_coherence_score: float = 0.60
    max_grammar_error_rate: float = 0.15
    max_sensitivity_score: float = 0.30
    min_domain_confidence: float = 0.50


@dataclass
class ValidationConfig:
    """Runtime configuration for the validation pipeline."""

    thresholds: ValidationThresholds = field(default_factory=ValidationThresholds)
    enable_llm: bool = False
    llm_provider: str = "gemini"  # gemini | openai
    write_per_record_jsonl: bool = False
    top_word_count: int = 20
    # Flesch-Kincaid is English-centric; only compute for Latin-heavy text.
    enable_flesch_for_latin_ratio: float = 0.50


DEFAULT_OUTPUT_DIR = Path("validation_reports")

# Preferred content-domain taxonomy for LLM classification (not URL domains).
# The LLM should pick from this list when possible; it may propose a new
# snake_case label when none of these fit (e.g. sports, agriculture).
CONTENT_DOMAINS: tuple[str, ...] = (
    "education",           # textbooks, lessons, grammar, tutorials
    "public_domain",       # encyclopedic reference (Wikipedia, Wikisource)
    "news",                # journalism, current events, reports
    "stories",             # fiction, poetry, essays, literary prose
    "information",         # general factual / how-to / explanatory prose
    "biography",           # people, profiles, history of individuals
    "science_technology",  # STEM, medicine, engineering
    "legal_government",    # law, policy, administration
    "religion_culture",    # faith, rituals, cultural practices
    "other",               # truly unclassifiable; prefer a specific new label instead
)

