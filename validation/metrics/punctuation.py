"""Punctuation and capitalization sanity checks."""

from __future__ import annotations

import re

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import has_terminal_punctuation, is_all_caps_word, split_sentences, tokenize

REPEATED_PUNCT = re.compile(r"([.!?,:;])\1{2,}")
UNBALANCED = [
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("«", "»"),
    ("“", "”"),
    ('"', '"'),
    ("'", "'"),
]


class PunctuationValidator(BaseValidator):
    name = "punctuation"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        sentences = split_sentences(text)
        tokens = tokenize(text)

        missing_terminal = 0
        for sentence in sentences:
            if not has_terminal_punctuation(sentence):
                missing_terminal += 1

        missing_ratio = missing_terminal / len(sentences) if sentences else 0.0
        caps_words = [t for t in tokens if is_all_caps_word(t)]
        caps_ratio = len(caps_words) / max(len(tokens), 1)

        unbalanced: dict[str, int] = {}
        unbalanced_total = 0
        for open_ch, close_ch in UNBALANCED:
            diff = abs(text.count(open_ch) - text.count(close_ch))
            if diff:
                unbalanced[f"{open_ch}{close_ch}"] = diff
                unbalanced_total += diff

        repeated = REPEATED_PUNCT.findall(text)

        flags: list[str] = []
        if missing_ratio > config.thresholds.max_missing_terminal_punct_ratio:
            flags.append("missing_terminal_punctuation")
        if caps_ratio > config.thresholds.max_all_caps_word_ratio:
            flags.append("high_all_caps_ratio")
        if unbalanced_total > config.thresholds.max_unbalanced_quote_pairs:
            flags.append("unbalanced_brackets_or_quotes")
        if repeated:
            flags.append("repeated_punctuation")

        return MetricResult(
            name=self.name,
            data={
                "sentence_count": len(sentences),
                "missing_terminal_punct_count": missing_terminal,
                "missing_terminal_punct_ratio": round(missing_ratio, 4),
                "all_caps_word_count": len(caps_words),
                "all_caps_word_ratio": round(caps_ratio, 4),
                "unbalanced_pairs": unbalanced,
                "repeated_punctuation": repeated[:10],
            },
            flags=flags,
        )
