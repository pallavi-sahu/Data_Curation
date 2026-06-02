"""Persist curated records to JSONL or CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Literal

OutputFormat = Literal["jsonl", "csv"]

FIELDNAMES = (
    "source",
    "paragraph",
    "language_code",
    "confidence",
    "detection_method",
)


def save_records(
    records: Iterable[dict[str, Any]],
    output_path: str | Path,
    *,
    output_format: OutputFormat = "jsonl",
) -> int:
    """
    Write records to disk. Returns the number of rows written.

    Each record must contain: source, paragraph, language_code, confidence.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(records)

    if output_format == "jsonl":
        _write_jsonl(rows, path)
    elif output_format == "csv":
        _write_csv(rows, path)
    else:
        raise ValueError(f"Unknown output format: {output_format}")

    return len(rows)


def _write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in FIELDNAMES})
