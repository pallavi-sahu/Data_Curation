"""Validation metrics package."""

from validation.metrics.abbreviations import AbbreviationValidator
from validation.metrics.domain import DomainValidator
from validation.metrics.emoji import EmojiValidator
from validation.metrics.encoding import EncodingValidator
from validation.metrics.grammar import GrammarValidator
from validation.metrics.links import LinksValidator
from validation.metrics.nsw import NSWValidator
from validation.metrics.pii import PIIValidator
from validation.metrics.punctuation import PunctuationValidator
from validation.metrics.readability import ReadabilityValidator
from validation.metrics.sensitivity import SensitivityValidator
from validation.metrics.symbols import SymbolsValidator
from validation.metrics.words import WordsValidator
from validation.metrics.coherence import CoherenceValidator

ALL_VALIDATORS = [
    DomainValidator(),
    WordsValidator(),
    LinksValidator(),
    SymbolsValidator(),
    AbbreviationValidator(),
    NSWValidator(),
    EncodingValidator(),
    PunctuationValidator(),
    EmojiValidator(),
    ReadabilityValidator(),
    PIIValidator(),
    CoherenceValidator(),
    GrammarValidator(),
    SensitivityValidator(),
]

DETERMINISTIC_VALIDATORS = [
    v for v in ALL_VALIDATORS
    if v.name not in {"domain", "coherence", "grammar"}
]

__all__ = [
    "ALL_VALIDATORS",
    "DETERMINISTIC_VALIDATORS",
    "DomainValidator",
    "WordsValidator",
    "LinksValidator",
    "SymbolsValidator",
    "AbbreviationValidator",
    "NSWValidator",
    "EncodingValidator",
    "PunctuationValidator",
    "EmojiValidator",
    "ReadabilityValidator",
    "PIIValidator",
    "CoherenceValidator",
    "GrammarValidator",
    "SensitivityValidator",
]
