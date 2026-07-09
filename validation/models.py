"""Data models for validation input records and metric results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CuratedRecord:
    """One row from a curated JSONL file."""

    source: str
    paragraph: str
    language_code: str
    confidence: float | None = None
    detection_method: str | None = None
    line_number: int | None = None

    @property
    def text(self) -> str:
        """Body text for all validators — always the ``paragraph`` field only."""
        return self.paragraph

    @classmethod
    def from_dict(cls, data: dict[str, Any], line_number: int | None = None) -> CuratedRecord:
        return cls(
            source=data.get("source", ""),
            paragraph=data.get("paragraph", ""),
            language_code=data.get("language_code", ""),
            confidence=data.get("confidence"),
            detection_method=data.get("detection_method"),
            line_number=line_number,
        )


@dataclass
class MetricResult:
    """Result from a single validator."""

    name: str
    data: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    available: bool = True
    error: str | None = None


@dataclass
class ValidationResult:
    """Full validation output for one curated record."""

    source: str
    language_code: str
    line_number: int | None
    metrics: dict[str, MetricResult] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    pass_validation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "language_code": self.language_code,
            "line_number": self.line_number,
            "metrics": {
                name: {
                    "data": m.data,
                    "flags": m.flags,
                    "available": m.available,
                    "error": m.error,
                }
                for name, m in self.metrics.items()
            },
            "flags": self.flags,
            "pass": self.pass_validation,
        }
