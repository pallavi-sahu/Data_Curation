#!/usr/bin/env python3
"""Summarize validated JSONL files under ``final/`` into a CSV report."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def discover_languages(final_dir: Path) -> dict[str, Path]:
    """Map language name -> directory containing validated_cleaned (or raw jsonl)."""
    languages: dict[str, Path] = {}

    for path in sorted(final_dir.rglob("*.jsonl")):
        name = path.stem
        if name.endswith("_validated_cleaned"):
            lang = name[: -len("_validated_cleaned")]
        elif path.parent == final_dir and not any(
            suffix in name for suffix in ("_noncoherent", "_validated_cleaned", "_removed_sensitive")
        ):
            lang = name
        else:
            continue
        languages[lang.lower()] = path.parent if path.parent != final_dir else final_dir

    return dict(sorted(languages.items()))


def count_nonempty_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def analyze_validated(path: Path) -> tuple[int, int, int, Counter[str]]:
    total = 0
    no_domain = 0
    domain_other = 0
    domains: Counter[str] = Counter()

    if not path.is_file():
        return 0, 0, 0, domains

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            total += 1
            record = json.loads(line)
            if "domain" not in record:
                no_domain += 1
                continue

            domain = record.get("domain")
            if domain is None or (isinstance(domain, str) and not str(domain).strip()):
                no_domain += 1
            elif str(domain).strip().lower() == "other":
                domain_other += 1
                domains["other"] += 1
            else:
                domains[str(domain).strip()] += 1

    return total, no_domain, domain_other, domains


def format_domains(domains: Counter[str]) -> str:
    if not domains:
        return "[]"
    parts = ", ".join(f"{label}: {count}" for label, count in domains.most_common())
    return f"[{parts}]"


def build_report(final_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    for language, lang_dir in discover_languages(final_dir).items():
        validated_path = lang_dir / f"{language}_validated_cleaned.jsonl"
        if not validated_path.is_file() and lang_dir == final_dir:
            validated_path = final_dir / f"{language}_validated_cleaned.jsonl"

        noncoherent_path = lang_dir / f"{language}_noncoherent.jsonl"
        if not noncoherent_path.is_file() and lang_dir == final_dir:
            noncoherent_path = final_dir / f"{language}_noncoherent.jsonl"

        validated_count, no_domain, domain_other, domains = analyze_validated(validated_path)
        rows.append(
            {
                "language": language,
                "validated_count": validated_count,
                "non_coherent_count": count_nonempty_lines(noncoherent_path),
                "domain_distribution": format_domains(domains),
                "no_domain_tagging": no_domain,
                "failed_domain_tagging": no_domain + domain_other,
                "domain_other_count": domain_other,
            }
        )

    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize final/ validation outputs to CSV.")
    parser.add_argument(
        "--final-dir",
        type=Path,
        default=_REPO_ROOT / "final",
        help="Root final/ directory (default: repo final/).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: <final-dir>/validation_summary_report.csv).",
    )
    args = parser.parse_args(argv)

    final_dir: Path = args.final_dir
    if not final_dir.is_dir():
        print(f"Directory not found: {final_dir}", file=sys.stderr)
        return 1

    output_path = args.output or final_dir / "validation_summary_report.csv"
    rows = build_report(final_dir)

    fieldnames = [
        "language",
        "validated_count",
        "non_coherent_count",
        "domain_distribution",
        "no_domain_tagging",
        "failed_domain_tagging",
        "domain_other_count",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {output_path} ({len(rows)} languages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
