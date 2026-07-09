"""LLM client for domain, coherence, grammar, and sensitivity checks (Gemini or OpenAI)."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from validation.config import CONTENT_DOMAINS
from validation.env_loader import load_project_env

load_project_env()

logger = logging.getLogger(__name__)

LlmProvider = Literal["gemini", "openai"]

DOMAIN_LABELS = ", ".join(CONTENT_DOMAINS)
_LABEL_RE = re.compile(r"[^a-z0-9_]+")
_last_llm_error: str | None = None
_last_llm_block_reason: str | None = None
_provider_override: str | None = None

DEFAULT_MODELS: dict[LlmProvider, str] = {
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
}

# Shorthand names that are not valid Gemini API model IDs.
GEMINI_MODEL_ALIASES: dict[str, str] = {
    "gemini-3": "gemini-3.5-flash",
    "gemini3": "gemini-3.5-flash",
}


@dataclass(frozen=True)
class LlmCallResult:
    """Thread-safe result from one LLM API call (no global state required)."""

    text: str | None = None
    error: str | None = None
    block_reason: str | None = None

    @property
    def prohibited(self) -> bool:
        return self.block_reason == "PROHIBITED_CONTENT"


@dataclass(frozen=True)
class RecordQualityResult:
    """Combined sensitivity, coherence, and domain assessment for one paragraph."""

    sensitivity_score: float | None = None
    coherence_score: float | None = None
    coherence_issues: tuple[str, ...] = ()
    domain: str | None = None
    error: str | None = None
    block_reason: str | None = None

    @property
    def prohibited(self) -> bool:
        return self.block_reason == "PROHIBITED_CONTENT"

    @property
    def ok(self) -> bool:
        return self.error is None and not self.prohibited


def normalize_provider(raw: str | None) -> LlmProvider:
    value = (raw or "gemini").strip().lower()
    if value in {"openai", "gpt", "chatgpt", "gpt4", "gpt-4"}:
        return "openai"
    return "gemini"


def configure_llm_provider(provider: str | None) -> None:
    """Set provider for this process (CLI flag overrides env)."""
    global _provider_override
    _provider_override = normalize_provider(provider) if provider else None


def get_llm_provider() -> LlmProvider:
    if _provider_override:
        return normalize_provider(_provider_override)
    return normalize_provider(os.environ.get("VALIDATION_LLM_PROVIDER"))


def get_last_llm_error() -> str | None:
    return _last_llm_error


def get_last_llm_block_reason() -> str | None:
    return _last_llm_block_reason


def is_prohibited_content_block() -> bool:
    return _last_llm_block_reason == "PROHIBITED_CONTENT"


def _gemini_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _openai_api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")


def _resolve_gemini_model(raw: str) -> str:
    key = raw.strip().lower()
    return GEMINI_MODEL_ALIASES.get(key, raw.strip())


def _model_name(provider: LlmProvider) -> str:
    explicit = os.environ.get("VALIDATION_LLM_MODEL")
    if explicit:
        return _resolve_gemini_model(explicit) if provider == "gemini" else explicit.strip()
    if provider == "openai":
        return os.environ.get("OPENAI_MODEL", DEFAULT_MODELS["openai"]).strip()
    return _resolve_gemini_model(os.environ.get("GEMINI_MODEL", DEFAULT_MODELS["gemini"]))


def llm_available(provider: LlmProvider | None = None) -> bool:
    active = provider or get_llm_provider()
    if active == "openai":
        return bool(_openai_api_key())
    return bool(_gemini_api_key())


def llm_api_key_hint(provider: LlmProvider | None = None) -> str:
    active = provider or get_llm_provider()
    if active == "openai":
        return "Set OPENAI_API_KEY in .env"
    return "Set GEMINI_API_KEY (or GOOGLE_API_KEY) in .env"


def normalize_category_label(raw: str) -> str:
    label = raw.strip().lower().replace(" ", "_").replace("-", "_")
    label = _LABEL_RE.sub("", label)
    label = re.sub(r"_+", "_", label).strip("_")
    return label


def _parse_category(raw: object) -> tuple[str, bool]:
    label = normalize_category_label(str(raw or "other"))
    if not label:
        return "other", False
    is_custom = label not in CONTENT_DOMAINS
    return label, is_custom


def _gemini_block_reason(response: Any) -> str | None:
    feedback = getattr(response, "prompt_feedback", None)
    if not feedback:
        return None
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason is None:
        return None
    if hasattr(block_reason, "name"):
        return str(block_reason.name)
    return str(block_reason)


def _normalize_block_reason(block_reason: str) -> str:
    if "PROHIBITED_CONTENT" in block_reason.upper():
        return "PROHIBITED_CONTENT"
    return block_reason


def _call_gemini_detailed(system: str, user: str) -> LlmCallResult:
    api_key = _gemini_api_key()
    if not api_key:
        return LlmCallResult(error="GEMINI_API_KEY or GOOGLE_API_KEY is not set")
    try:
        import google.generativeai as genai
    except ImportError:
        error = "Install google-generativeai: pip install google-generativeai"
        logger.error(error)
        return LlmCallResult(error=error)
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=_model_name("gemini"),
            system_instruction=system,
            generation_config=genai.GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        response = model.generate_content(user)
        block_reason = _gemini_block_reason(response)
        if block_reason:
            normalized = _normalize_block_reason(block_reason)
            error = f"response.prompt_feedback block_reason: {block_reason}"
            logger.warning("Gemini blocked request: %s", error)
            return LlmCallResult(error=error, block_reason=normalized)
        if not response.text:
            return LlmCallResult(error="Gemini returned an empty response")
        return LlmCallResult(text=response.text)
    except Exception as exc:
        error = str(exc)
        block_reason = "PROHIBITED_CONTENT" if "PROHIBITED_CONTENT" in error.upper() else None
        logger.error("Gemini API call failed: %s", exc)
        return LlmCallResult(error=error, block_reason=block_reason)


def _call_openai_detailed(system: str, user: str) -> LlmCallResult:
    api_key = _openai_api_key()
    if not api_key:
        return LlmCallResult(error="OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError:
        error = "Install openai: pip install openai"
        logger.error(error)
        return LlmCallResult(error=error)
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=_model_name("openai"),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            return LlmCallResult(error="OpenAI returned an empty response")
        return LlmCallResult(text=content)
    except Exception as exc:
        logger.error("OpenAI API call failed: %s", exc)
        return LlmCallResult(error=str(exc))


def _call_llm_detailed(system: str, user: str) -> LlmCallResult:
    provider = get_llm_provider()
    if provider == "openai":
        return _call_openai_detailed(system, user)
    return _call_gemini_detailed(system, user)


def _call_gemini(system: str, user: str) -> str | None:
    global _last_llm_error, _last_llm_block_reason
    result = _call_gemini_detailed(system, user)
    _last_llm_error = result.error
    _last_llm_block_reason = result.block_reason
    return result.text


def _call_openai(system: str, user: str) -> str | None:
    global _last_llm_error
    result = _call_openai_detailed(system, user)
    _last_llm_error = result.error
    return result.text


def _call_llm(system: str, user: str) -> str | None:
    global _last_llm_error, _last_llm_block_reason
    _last_llm_error = None
    _last_llm_block_reason = None
    provider = get_llm_provider()
    if provider == "openai":
        return _call_openai(system, user)
    return _call_gemini(system, user)


def _parse_json_response_detailed(raw: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if not raw:
        return None, "Empty LLM response"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, "LLM returned invalid JSON"


def _parse_json_response(raw: str | None) -> dict[str, Any] | None:
    global _last_llm_error
    parsed, error = _parse_json_response_detailed(raw)
    if error:
        _last_llm_error = error
    return parsed


def evaluate_record_quality(
    text: str,
    language_code: str,
    *,
    include_sensitivity: bool = True,
) -> RecordQualityResult:
    """
    Single LLM call for sensitivity, coherence, and domain (thread-safe).

    Replaces three separate API calls when processing large JSONL files.
    """
    if include_sensitivity:
        system = (
            "You analyze paragraph text for a speech/TTS dataset in the given language. "
            "Return JSON only with these fields:\n"
            '{"sensitivity_score": 0.0-1.0, "coherence_score": 0.0-1.0, "domain": "<label>"}\n'
            "sensitivity_score: 0=safe, 1=highly sensitive (hate speech, explicit violence, "
            "sexual content, self-harm encouragement).\n"
            "coherence_score: 1=fully coherent natural prose, 0=incoherent scraping garbage.\n"
            f"domain: prefer one label from [{DOMAIN_LABELS}]; otherwise a new 1-3 word "
            "snake_case label (e.g. sports, child_stories). Use 'other' only for noise.\n"
            "No extra fields or explanations."
        )
    else:
        system = (
            "You analyze paragraph text for a speech/TTS dataset in the given language. "
            "Return JSON only with these fields:\n"
            '{"coherence_score": 0.0-1.0, "domain": "<label>"}\n'
            "coherence_score: 1=fully coherent natural prose, 0=incoherent scraping garbage.\n"
            f"domain: prefer one label from [{DOMAIN_LABELS}]; otherwise a new 1-3 word "
            "snake_case label. Use 'other' only for noise.\n"
            "No extra fields or explanations."
        )

    user = json.dumps({"language_code": language_code, "text": text[:4000]}, ensure_ascii=False)
    call = _call_llm_detailed(system, user)
    if call.prohibited:
        return RecordQualityResult(error=call.error, block_reason=call.block_reason)
    if call.error and not call.text:
        return RecordQualityResult(error=call.error)

    parsed, parse_error = _parse_json_response_detailed(call.text)
    if parse_error or parsed is None:
        return RecordQualityResult(error=parse_error or call.error or "LLM parse failed")

    sensitivity_score: float | None = None
    if include_sensitivity:
        sensitivity_score = float(parsed.get("sensitivity_score", 0.0))

    coherence_score = float(parsed.get("coherence_score", parsed.get("coherence", 0.0)))
    issues_raw = parsed.get("coherence_issues") or parsed.get("issues") or []
    coherence_issues = tuple(str(item) for item in issues_raw if item)

    raw_domain = parsed.get("domain") or parsed.get("primary") or "other"
    label, _ = _parse_category(raw_domain)
    domain = _limit_domain_words(label)

    return RecordQualityResult(
        sensitivity_score=sensitivity_score,
        coherence_score=coherence_score,
        coherence_issues=coherence_issues,
        domain=domain,
    )


def _limit_domain_words(label: str, max_words: int = 3) -> str:
    parts = [part for part in label.split("_") if part]
    if not parts:
        return "other"
    return "_".join(parts[:max_words])


def classify_paragraph_domain(text: str, language_code: str) -> str | None:
    """Return a 1–3 word snake_case domain label for a paragraph."""
    system = (
        "You classify paragraph text into a content domain for a speech/TTS dataset. "
        f"Prefer one label from this list when it fits: {DOMAIN_LABELS}. "
        "If none fit well, invent a new concise domain label using 1 to 3 words "
        "in snake_case (e.g. sports, agriculture, child_stories). "
        "Use 'other' only for unclassifiable noise. "
        'Return JSON only: {"domain": "<label>"}. '
        "No explanation or extra fields."
    )
    user = json.dumps(
        {"language_code": language_code, "text": text[:4000]},
        ensure_ascii=False,
    )
    result = _parse_json_response(_call_llm(system, user))
    if result is None:
        return None

    raw = result.get("domain") or result.get("primary") or "other"
    label, _ = _parse_category(raw)
    return _limit_domain_words(label)


def classify_content_domain(text: str, language_code: str) -> dict[str, Any] | None:
    system = (
        "You classify text into content domains for a speech/TTS dataset. "
        "Prefer the single best primary category from this list: "
        f"{DOMAIN_LABELS}. "
        "If none of these fit well, propose a new concise snake_case label "
        "(e.g. sports, agriculture, entertainment, health). "
        "Use 'other' only when the text is truly unclassifiable noise — "
        "otherwise invent a specific label. "
        "Classify using only the paragraph text provided. "
        "Return JSON: "
        '{"primary": "<category>", "secondary": ["<category>", ...], '
        '"confidence": 0.0-1.0, "rationale": "one sentence"}. '
        "secondary may be empty. Labels must be snake_case."
    )
    user = json.dumps(
        {"language_code": language_code, "text": text[:4000]},
        ensure_ascii=False,
    )
    result = _parse_json_response(_call_llm(system, user))
    if result is None:
        return None

    primary, primary_is_custom = _parse_category(result.get("primary", "other"))
    result["primary"] = primary
    result["primary_is_custom"] = primary_is_custom

    secondary: list[str] = []
    secondary_custom: list[str] = []
    for item in result.get("secondary") or []:
        label, is_custom = _parse_category(item)
        if label == primary or label in secondary:
            continue
        secondary.append(label)
        if is_custom:
            secondary_custom.append(label)
    result["secondary"] = secondary
    result["secondary_custom"] = secondary_custom
    return result


def evaluate_coherence(text: str, language_code: str) -> dict[str, Any] | None:
    system = (
        "You evaluate whether a paragraph is coherent and reads as natural prose "
        "in the given language. Return JSON: "
        '{"score": 0.0-1.0, "issues": ["..."], "summary": "..."}. '
        "Score 1.0 = fully coherent; 0.0 = incoherent scraping garbage."
    )
    user = json.dumps({"language_code": language_code, "text": text[:4000]}, ensure_ascii=False)
    return _parse_json_response(_call_llm(system, user))


def evaluate_grammar(text: str, language_code: str) -> dict[str, Any] | None:
    system = (
        "You estimate grammatical error rate for text in the given language. "
        "Return JSON: "
        '{"error_rate": 0.0-1.0, "error_count": int, "examples": ["..."], "summary": "..."}. '
        "error_rate = estimated fraction of words/phrases with grammar issues."
    )
    user = json.dumps({"language_code": language_code, "text": text[:4000]}, ensure_ascii=False)
    return _parse_json_response(_call_llm(system, user))


def evaluate_sensitivity(text: str, language_code: str) -> dict[str, Any] | None:
    system = (
        "You flag sensitive content (hate speech, explicit violence, sexual content, "
        "self-harm encouragement) in the given language. Return JSON: "
        '{"score": 0.0-1.0, "categories": ["..."], "flags": ["..."], "summary": "..."}. '
        "Score 0 = safe; 1 = highly sensitive."
    )
    user = json.dumps({"language_code": language_code, "text": text[:4000]}, ensure_ascii=False)
    return _parse_json_response(_call_llm(system, user))
