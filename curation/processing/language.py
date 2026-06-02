"""Language detection and filtering with fastText and script fallback."""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from curation.config import (
    DEFAULT_MODEL_PATH,
    DEFAULT_SCRIPT_RATIO_THRESHOLD,
    FASTTEXT_LID_MODEL_URL,
    FASTTEXT_UNSUPPORTED_INDIC,
    INDIC_LANGUAGE_CODES,
    LANGUAGE_SCRIPT,
)
from curation.processing.script import script_character_ratio

logger = logging.getLogger(__name__)

# fasttext-wheel uses np.array(..., copy=False), which NumPy 2.x rejects.
_NUMPY_PATCHED = False


def _patch_fasttext_numpy_compat() -> None:
    global _NUMPY_PATCHED
    if _NUMPY_PATCHED:
        return
    import numpy as np

    _original_array = np.array

    def _array(obj, *args, **kwargs):
        if kwargs.get("copy") is False:
            return np.asarray(obj)
        return _original_array(obj, *args, **kwargs)

    np.array = _array  # type: ignore[misc, assignment]
    _NUMPY_PATCHED = True


@dataclass(frozen=True)
class LanguagePrediction:
    language_code: str
    confidence: float
    method: str = "fasttext"  # or "script"


class LanguageFilter:
    """
    Keep paragraphs in the target language.

    - Supported by fastText lid.176: label + confidence threshold.
    - Not in lid.176 (brx, doi, kok, mai, mni, sat): Unicode script ratio.
    """

    def __init__(
        self,
        target_language: str,
        *,
        confidence_threshold: float = 0.85,
        script_ratio_threshold: float = DEFAULT_SCRIPT_RATIO_THRESHOLD,
        model_path: Path | None = None,
    ) -> None:
        code = target_language.strip().lower()
        if code not in INDIC_LANGUAGE_CODES:
            supported = ", ".join(sorted(INDIC_LANGUAGE_CODES))
            raise ValueError(
                f"Unsupported language code '{code}'. "
                f"Use one of the 22 scheduled Indic codes: {supported}"
            )
        self.target_language = code
        self.confidence_threshold = confidence_threshold
        self.script_ratio_threshold = script_ratio_threshold
        self._model_path = Path(model_path or DEFAULT_MODEL_PATH)
        self._model = None
        self._use_script = code in FASTTEXT_UNSUPPORTED_INDIC
        self._script = LANGUAGE_SCRIPT.get(code, "devanagari")

    @property
    def uses_script_detection(self) -> bool:
        return self._use_script

    @property
    def script_name(self) -> str:
        return self._script

    def needs_fasttext_model(self) -> bool:
        return not self._use_script

    def ensure_model(self) -> None:
        if self._use_script or self._model is not None:
            return
        self._download_model_if_needed()
        _patch_fasttext_numpy_compat()
        import fasttext

        self._model = fasttext.load_model(str(self._model_path))

    def predict(self, text: str) -> LanguagePrediction:
        if self._use_script:
            ratio = script_character_ratio(text, self._script)
            return LanguagePrediction(
                language_code=self.target_language,
                confidence=ratio,
                method="script",
            )
        self.ensure_model()
        assert self._model is not None
        labels, scores = self._model.predict(text.replace("\n", " "), k=1)
        label = labels[0].removeprefix("__label__")
        return LanguagePrediction(
            language_code=label,
            confidence=float(scores[0]),
            method="fasttext",
        )

    def matches_target(self, text: str) -> tuple[bool, LanguagePrediction]:
        prediction = self.predict(text)
        script_ratio = script_character_ratio(text, self._script)

        if self._use_script:
            matches = script_ratio >= self.confidence_threshold
            if not matches:
                logger.debug(
                    "Script mismatch for %s (script=%s, ratio=%.3f < %.3f)",
                    self.target_language,
                    self._script,
                    script_ratio,
                    self.confidence_threshold,
                )
            prediction = LanguagePrediction(
                language_code=self.target_language,
                confidence=script_ratio,
                method="script",
            )
        else:
            fasttext_ok = (
                prediction.language_code == self.target_language
                and prediction.confidence >= self.confidence_threshold
            )
            script_ok = script_ratio >= self.script_ratio_threshold
            matches = fasttext_ok and script_ok
            if not matches:
                logger.debug(
                    "Dropped paragraph (fasttext=%s/%.3f, script=%s ratio=%.3f)",
                    prediction.language_code,
                    prediction.confidence,
                    self._script,
                    script_ratio,
                )
            # Report the lower of the two signals as the export confidence.
            prediction = LanguagePrediction(
                language_code=prediction.language_code,
                confidence=min(prediction.confidence, script_ratio),
                method="fasttext+script" if not script_ok else prediction.method,
            )
        return matches, prediction

    def _download_model_if_needed(self) -> None:
        if self._model_path.is_file():
            return
        self._model_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading fastText lid model to %s", self._model_path)
        urllib.request.urlretrieve(FASTTEXT_LID_MODEL_URL, self._model_path)
