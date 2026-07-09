"""Base validator protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod

from validation.config import ValidationConfig
from validation.models import CuratedRecord, MetricResult


class BaseValidator(ABC):
    """One validation metric."""

    name: str

    @abstractmethod
    def validate(self, record: CuratedRecord, config: ValidationConfig) -> MetricResult:
        """Run validation on a single record.

        All text analysis must use ``record.text`` (the JSONL ``paragraph`` field).
        The ``source`` URL is metadata for the report only, not validator input.
        """
