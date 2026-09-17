"""Readability and sentence complexity metrics."""

from __future__ import annotations

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import latin_character_ratio, split_sentences, tokenize

try:
    import textstat

    TEXTSTAT_AVAILABLE = True
except ImportError:
    TEXTSTAT_AVAILABLE = False

LONG_SENTENCE_THRESHOLD = 60


class ReadabilityValidator(BaseValidator):
    name = "readability"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        sentences = split_sentences(text)
        sentence_lengths = [len(tokenize(s)) for s in sentences]
        mean_sentence_length = (
            sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0.0
        )
        long_sentences = sum(1 for n in sentence_lengths if n >= LONG_SENTENCE_THRESHOLD)

        flesch_kincaid = None
        flesch_reading_ease = None
        latin_ratio = latin_character_ratio(text)
        use_flesch = (
            TEXTSTAT_AVAILABLE
            and latin_ratio >= config.enable_flesch_for_latin_ratio
        )
        if use_flesch:
            flesch_kincaid = round(textstat.flesch_kincaid_grade(text), 2)
            flesch_reading_ease = round(textstat.flesch_reading_ease(text), 2)

        flags: list[str] = []
        if mean_sentence_length > config.thresholds.max_mean_sentence_length:
            flags.append("high_mean_sentence_length")
        if long_sentences > config.thresholds.max_long_sentence_count:
            flags.append("too_many_long_sentences")

        return MetricResult(
            name=self.name,
            data={
                "sentence_count": len(sentences),
                "mean_sentence_length": round(mean_sentence_length, 2),
                "max_sentence_length": max(sentence_lengths) if sentence_lengths else 0,
                "long_sentence_count": long_sentences,
                "long_sentence_threshold_words": LONG_SENTENCE_THRESHOLD,
                "flesch_kincaid_grade": flesch_kincaid,
                "flesch_reading_ease": flesch_reading_ease,
                "latin_character_ratio": round(latin_ratio, 4),
                "textstat_available": TEXTSTAT_AVAILABLE,
                "note": (
                    "Flesch-Kincaid is English-centric; computed only when "
                    "latin_character_ratio exceeds threshold."
                ),
            },
            flags=flags,
        )
