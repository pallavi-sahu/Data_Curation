"""Extract per-record detail payloads for flagged validation metrics."""

from __future__ import annotations

from typing import Any, Callable

from validation.models import ValidationResult


def _ref(result: ValidationResult) -> dict[str, Any]:
    return {
        "line_number": result.line_number,
        "source": result.source,
    }


def _flatten_nsw_samples(samples: dict[str, Any] | None) -> list[str]:
    items: list[str] = []
    for values in (samples or {}).values():
        if isinstance(values, list):
            items.extend(str(v) for v in values)
    return items


def _detail(
    result: ValidationResult,
    data: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {**_ref(result), **extra}


# (metric_name, flag) -> extractor(metric.data, ValidationResult) -> dict
DETAIL_EXTRACTORS: dict[tuple[str, str], Callable[[dict[str, Any], ValidationResult], dict[str, Any]]] = {
    ("nsw", "high_nsw_density"): lambda d, r: _detail(
        r, d, density=d.get("density"), nsw_items=_flatten_nsw_samples(d.get("samples")),
    ),
    ("links", "high_link_density"): lambda d, r: _detail(
        r, d, urls=d.get("urls", []), link_density=d.get("link_density"),
    ),
    ("links", "contains_raw_urls"): lambda d, r: _detail(
        r, d, urls=d.get("urls", []),
    ),
    ("pii", "pii_detected"): lambda d, r: _detail(
        r, d, entities=d.get("entities", []),
    ),
    ("emoji", "contains_emoji"): lambda d, r: _detail(
        r, d, emoji_samples=d.get("emoji_samples", []),
    ),
    ("encoding", "mojibake_detected"): lambda d, r: _detail(
        r, d, mojibake_markers=d.get("mojibake_markers", []),
    ),
    ("encoding", "html_entities_present"): lambda d, r: _detail(
        r, d, html_entities=d.get("html_entities_sample", []),
    ),
    ("encoding", "non_breaking_spaces"): lambda d, r: _detail(
        r, d, nbsp_count=d.get("nbsp_count"),
    ),
    ("encoding", "ftfy_would_modify"): lambda d, r: _detail(r, d),
    ("encoding", "needs_unicode_normalization"): lambda d, r: _detail(r, d),
    ("punctuation", "missing_terminal_punctuation"): lambda d, r: _detail(
        r,
        d,
        missing_terminal_punct_ratio=d.get("missing_terminal_punct_ratio"),
        sentence_count=d.get("sentence_count"),
    ),
    ("punctuation", "high_all_caps_ratio"): lambda d, r: _detail(
        r, d, all_caps_word_ratio=d.get("all_caps_word_ratio"),
    ),
    ("punctuation", "unbalanced_brackets_or_quotes"): lambda d, r: _detail(
        r, d, unbalanced_pairs=d.get("unbalanced_pairs", {}),
    ),
    ("punctuation", "repeated_punctuation"): lambda d, r: _detail(
        r, d, repeated_punctuation=d.get("repeated_punctuation", []),
    ),
    ("words", "low_unique_word_ratio"): lambda d, r: _detail(
        r, d, unique_ratio=d.get("unique_ratio"), word_count=d.get("word_count"),
    ),
    ("words", "low_script_match_ratio"): lambda d, r: _detail(
        r, d, script_match_ratio=d.get("script_match_ratio"),
    ),
    ("words", "empty_text"): lambda d, r: _detail(r, d),
    ("symbols", "high_special_char_ratio"): lambda d, r: _detail(
        r,
        d,
        special_char_ratio=d.get("special_char_ratio"),
        distinct_special_chars=d.get("distinct_special_chars", []),
    ),
    ("symbols", "contains_format_characters"): lambda d, r: _detail(
        r, d, format_char_count=d.get("format_char_count"),
    ),
    ("abbreviations", "high_abbreviation_density"): lambda d, r: _detail(
        r, d, abbreviations=d.get("items", []), density=d.get("density"),
    ),
    ("readability", "high_mean_sentence_length"): lambda d, r: _detail(
        r,
        d,
        mean_sentence_length=d.get("mean_sentence_length"),
        max_sentence_length=d.get("max_sentence_length"),
    ),
    ("readability", "too_many_long_sentences"): lambda d, r: _detail(
        r,
        d,
        long_sentence_count=d.get("long_sentence_count"),
        mean_sentence_length=d.get("mean_sentence_length"),
    ),
    ("domain", "low_domain_confidence"): lambda d, r: _detail(
        r, d, category=d.get("category"), confidence=d.get("confidence"), rationale=d.get("rationale"),
    ),
    ("domain", "uncategorized_content"): lambda d, r: _detail(
        r, d, category=d.get("category"), rationale=d.get("rationale"),
    ),
    ("coherence", "low_coherence"): lambda d, r: _detail(
        r, d, score=d.get("score"), issues=d.get("issues", []), summary=d.get("summary"),
    ),
    ("grammar", "high_grammar_error_rate"): lambda d, r: _detail(
        r,
        d,
        error_rate=d.get("error_rate"),
        examples=d.get("examples", []),
        summary=d.get("summary"),
    ),
    ("sensitivity", "sensitive_content"): lambda d, r: _detail(
        r, d, score=d.get("score"), categories=d.get("categories", []), summary=d.get("llm_summary"),
    ),
    ("sensitivity", "potential_pii_in_text"): lambda d, r: _detail(
        r, d, regex_hits=d.get("regex_hits", {}),
    ),
}


def extract_flag_detail(
    metric_name: str,
    flag: str,
    data: dict[str, Any],
    result: ValidationResult,
) -> dict[str, Any]:
    extractor = DETAIL_EXTRACTORS.get((metric_name, flag))
    if extractor:
        return extractor(data, result)
    return _detail(result, data)
