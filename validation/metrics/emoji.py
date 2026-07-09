"""Emoji, emoticons, and kaomoji detection."""

from __future__ import annotations

from validation.config import ValidationConfig
from validation.metrics.base import BaseValidator
from validation.models import CuratedRecord, MetricResult
from validation.tokenization import EMOJI_PATTERN, KAOMOJI_PATTERN


class EmojiValidator(BaseValidator):
    name = "emoji"

    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        text = record.text
        emoji_matches = EMOJI_PATTERN.findall(text)
        kaomoji_matches = KAOMOJI_PATTERN.findall(text)

        flags: list[str] = []
        if len(emoji_matches) > config.thresholds.max_emoji_count:
            flags.append("contains_emoji")

        return MetricResult(
            name=self.name,
            data={
                "emoji_count": len(emoji_matches),
                "kaomoji_count": len(kaomoji_matches),
                "total_count": len(emoji_matches) + len(kaomoji_matches),
                "emoji_samples": emoji_matches[:10],
                "kaomoji_samples": kaomoji_matches[:10],
                "has_emoji": bool(emoji_matches),
            },
            flags=flags,
        )
