#!/usr/bin/env python3
"""Compute unique-word percentage / diversity metrics per language corpus."""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.tokenization import tokenize

OUTPUT_JSON = _REPO_ROOT / "final" / "vocabulary_diversity_reports.json"
OUTPUT_MD = _REPO_ROOT / "final" / "vocabulary_diversity_report.md"

LOW_UNIQUE_WORD_THRESHOLD = 25.0

LANG_PATHS: dict[str, Path] = {
    "Assamese": _REPO_ROOT / "final" / "Assamese" / "assamese.jsonl",
    "bodo": _REPO_ROOT / "final" / "bodo" / "bodo.jsonl",
    "gujarati": _REPO_ROOT / "final" / "gujarati" / "gujarati.jsonl",
    "kannada": _REPO_ROOT / "final" / "kannada" / "kannada.jsonl",
    "maithili": _REPO_ROOT / "final" / "maithili" / "maithili.jsonl",
    "malayalam": _REPO_ROOT / "final" / "malayalam" / "malayalam.jsonl",
    "manipuri": _REPO_ROOT / "final" / "manipuri" / "manipuri.jsonl",
    "marathi": _REPO_ROOT / "final" / "marathi" / "marathi.jsonl",
    "odia": _REPO_ROOT / "final" / "odia" / "odia.jsonl",
    "punjabi": _REPO_ROOT / "final" / "punjabi" / "punjabi.jsonl",
    "santali": _REPO_ROOT / "final" / "santali" / "santali.jsonl",
    "sindhi": _REPO_ROOT / "final" / "sindhi" / "sindhi.jsonl",
    "tamil": _REPO_ROOT / "final" / "tamil" / "tamil.jsonl",
    "telugu": _REPO_ROOT / "final" / "telugu" / "telugu.jsonl",
    "urdu": _REPO_ROOT / "final" / "urdu" / "urdu.jsonl",
}


def _display_name(lang: str) -> str:
    return "Assamese" if lang == "Assamese" else lang.capitalize()


def _pct(numerator: int | float, denominator: int | float) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 0.0


def _round1(value: float) -> float:
    return round(value, 1)


def analyze_language(path: Path) -> dict:
    corpus_freq: Counter[str] = Counter()
    per_script_unique_pct: list[float] = []
    per_script_word_counts: list[int] = []
    scripts_below_threshold = 0
    empty_scripts = 0

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            text = record.get("paragraph") or record.get("text") or ""
            tokens = tokenize(text)
            word_count = len(tokens)
            if word_count == 0:
                empty_scripts += 1
                continue

            unique_words_in_script = len(set(tokens))
            unique_pct = _pct(unique_words_in_script, word_count)
            per_script_unique_pct.append(unique_pct)
            per_script_word_counts.append(word_count)
            if unique_pct < LOW_UNIQUE_WORD_THRESHOLD:
                scripts_below_threshold += 1
            corpus_freq.update(tokens)

    total_words = sum(corpus_freq.values())
    unique_words = len(corpus_freq)
    script_count = len(per_script_unique_pct)

    return {
        "scripts": script_count,
        "empty_scripts": empty_scripts,
        "total_words": total_words,
        "unique_words": unique_words,
        "unique_word_percentage_corpus": _pct(unique_words, total_words),
        "unique_word_percentage_per_script": {
            "mean": _round1(statistics.mean(per_script_unique_pct)) if per_script_unique_pct else None,
            "median": _round1(statistics.median(per_script_unique_pct)) if per_script_unique_pct else None,
            "min": _round1(min(per_script_unique_pct)) if per_script_unique_pct else None,
            "max": _round1(max(per_script_unique_pct)) if per_script_unique_pct else None,
        },
        "scripts_below_25_percent": scripts_below_threshold,
        "scripts_below_25_percent_pct": _pct(scripts_below_threshold, script_count),
        "mean_words_per_script": round(statistics.mean(per_script_word_counts), 1)
        if per_script_word_counts
        else None,
    }


def build_markdown(reports: dict[str, dict]) -> str:
    today = date.today().strftime("%d %B %Y")
    lines = [
        "# Unique Word Diversity Report",
        "",
        f"> **Report date:** {today}  ",
        "> **Corpus:** `final/<language>/<language>.jsonl` (live curated datasets)  ",
        f"> **Machine-readable JSON:** [`vocabulary_diversity_reports.json`](vocabulary_diversity_reports.json)",
        "",
        "Unique-word metrics use the same Indic-aware tokenization as the validation pipeline "
        "(`validation/tokenization.py`).",
        "",
        "## Summary — unique word % by language",
        "",
        "Sorted by average unique word % per script (primary diversity measure).",
        "",
        "| Language | Scripts | Total words | Unique words | Avg unique word % (per script) | "
        "Corpus unique word % | Scripts below 25% |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for lang in sorted(
        reports,
        key=lambda name: reports[name]["unique_word_percentage_per_script"]["mean"] or 0,
        reverse=True,
    ):
        r = reports[lang]
        per_script = r["unique_word_percentage_per_script"]
        mean_pct = per_script["mean"]
        mean_str = f"{mean_pct:.1f}%" if mean_pct is not None else "—"
        lines.append(
            f"| {_display_name(lang)} | {r['scripts']:,} | {r['total_words']:,} | "
            f"{r['unique_words']:,} | {mean_str} | "
            f"{r['unique_word_percentage_corpus']:.2f}% | "
            f"{r['scripts_below_25_percent']:,} ({r['scripts_below_25_percent_pct']:.1f}%) |"
        )

    lines.extend(
        [
            "",
            "### Metric definitions",
            "",
            "- **Avg unique word % (per script)** — for each script, "
            "(unique words in that script ÷ total words in that script) × 100, then averaged "
            "across all scripts. This is the primary diversity measure.",
            "- **Corpus unique word %** — (total distinct unique words across the whole language corpus "
            "÷ total word count across all scripts) × 100.",
            "- **Scripts below 25%** — scripts where fewer than 25% of words are unique within "
            "the passage (validation threshold).",
            "",
            "## Per-language detail",
            "",
        ]
    )

    for lang in sorted(reports, key=str.lower):
        r = reports[lang]
        per_script = r["unique_word_percentage_per_script"]
        lines.extend(
            [
                f"### {_display_name(lang)}",
                "",
                f"- Scripts: **{r['scripts']:,}** | Total words: **{r['total_words']:,}** | "
                f"Unique words: **{r['unique_words']:,}**",
                f"- Avg unique word % (per script): **{per_script['mean']:.1f}%** "
                f"(median **{per_script['median']:.1f}%**, "
                f"range **{per_script['min']:.1f}%**–**{per_script['max']:.1f}%**)",
                f"- Corpus unique word %: **{r['unique_word_percentage_corpus']:.2f}%**",
                f"- Scripts below 25% unique words: **{r['scripts_below_25_percent']:,}** "
                f"({r['scripts_below_25_percent_pct']:.1f}%)",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    reports: dict[str, dict] = {}
    for lang, path in LANG_PATHS.items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing dataset for {lang}: {path}")
        reports[lang] = analyze_language(path)

    payload = {
        "generated_on": date.today().isoformat(),
        "tokenization": "validation.tokenization.tokenize",
        "low_unique_word_threshold_percent": LOW_UNIQUE_WORD_THRESHOLD,
        "languages": reports,
    }

    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(build_markdown(reports), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")


if __name__ == "__main__":
    main()
