"""Deterministic JSON serialization for Search Engine V2 domain values."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID


def to_jsonable(value: Any) -> Any:
    """Convert nested domain values to structures accepted by ``json.dumps``."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((to_jsonable(item) for item in value), key=lambda item: repr(item))
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def to_json(value: Any, **kwargs: Any) -> str:
    """Serialize a domain value as stable UTF-8 friendly JSON text."""
    options = {"ensure_ascii": False, "sort_keys": True}
    options.update(kwargs)
    return json.dumps(to_jsonable(value), **options)


__all__ = ["to_json", "to_jsonable"]
