"""Load project ``.env`` into ``os.environ`` before LLM client reads keys."""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_LOADED = False


def load_project_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _ENV_LOADED = True
        return
    load_dotenv(_PROJECT_ROOT / ".env")
    _ENV_LOADED = True
