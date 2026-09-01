"""Safe runtime selection for the strangler migration."""
from __future__ import annotations

from enum import Enum
import hashlib
import os
from typing import Any


class SearchEngineMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    CANARY = "canary"
    V2 = "v2"


def get_search_engine_mode(value: str | None = None) -> SearchEngineMode:
    """Return a known mode; an absent or invalid value is always legacy."""
    raw = value if value is not None else os.getenv("SEARCH_ENGINE_MODE", "legacy")
    normalized = str(raw or "").strip().casefold()
    try:
        return SearchEngineMode(normalized)
    except ValueError:
        return SearchEngineMode.LEGACY


def get_v2_rollout_percent(value: Any = None) -> int:
    """Return a safe 0..100 canary percentage; invalid input disables it."""
    raw = value if value is not None else os.getenv("SEARCH_ENGINE_V2_ROLLOUT_PERCENT", "0")
    try:
        return max(0, min(int(str(raw or "0").strip()), 100))
    except (TypeError, ValueError):
        return 0


def rollout_bucket(request_id: Any) -> int | None:
    """Map a stable request ID to a privacy-safe bucket from 0 through 99."""
    identity = str(request_id or "").strip()
    if not identity or identity == "0":
        return None
    digest = hashlib.sha256(f"search-v2-canary:{identity}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 100


def resolve_search_engine_mode(
    configured: str | SearchEngineMode | None,
    *,
    request_id: Any,
    rollout_percent: Any = None,
) -> SearchEngineMode:
    """Resolve ``canary`` to V2 or legacy without using request contents."""
    mode = configured if isinstance(configured, SearchEngineMode) else get_search_engine_mode(configured)
    if mode is not SearchEngineMode.CANARY:
        return mode
    percent = get_v2_rollout_percent(rollout_percent)
    bucket = rollout_bucket(request_id)
    return SearchEngineMode.V2 if bucket is not None and bucket < percent else SearchEngineMode.LEGACY


__all__ = [
    "SearchEngineMode",
    "get_search_engine_mode",
    "get_v2_rollout_percent",
    "resolve_search_engine_mode",
    "rollout_bucket",
]
