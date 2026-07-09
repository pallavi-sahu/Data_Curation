"""Word count, frequency, and unique-word metrics."""

from __future__ import annotations

from collections import Counter

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import expected_script, script_ratio, tokenize


class WordsValidator(BaseValidator):
    name = "words"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        tokens = tokenize(text)
        word_count = len(tokens)
        freq = Counter(tokens)
        unique_words = len(freq)
        unique_ratio = unique_words / word_count if word_count else 0.0
        script = expected_script(record.language_code)
        script_match_ratio = script_ratio(text, record.language_code)

        flags: list[str] = []
        if word_count == 0:
            flags.append("empty_text")
        if unique_ratio < config.thresholds.min_unique_word_ratio and word_count >= 20:
            flags.append("low_unique_word_ratio")
        if script and script_match_ratio < 0.70:
            flags.append("low_script_match_ratio")

        top_words = [
            {"word": word, "count": count}
            for word, count in freq.most_common(config.top_word_count)
        ]

        return MetricResult(
            name=self.name,
            data={
                "word_count": word_count,
                "unique_words": unique_words,
                "unique_ratio": round(unique_ratio, 4),
                "expected_script": script,
                "script_match_ratio": round(script_match_ratio, 4),
                "top_words": top_words,
                "type_token_ratio": round(unique_ratio, 4),
            },
            flags=flags,
        )
