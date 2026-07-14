"""Safe runtime selection for the strangler migration."""
from __future__ import annotations

from enum import Enum
import os


class SearchEngineMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    V2 = "v2"


def get_search_engine_mode(value: str | None = None) -> SearchEngineMode:
    """Return a known mode; an absent or invalid value is always legacy."""
    raw = value if value is not None else os.getenv("SEARCH_ENGINE_MODE", "legacy")
    normalized = str(raw or "").strip().casefold()
    try:
        return SearchEngineMode(normalized)
    except ValueError:
        return SearchEngineMode.LEGACY


__all__ = ["SearchEngineMode", "get_search_engine_mode"]
