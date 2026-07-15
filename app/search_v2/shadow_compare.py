"""Pure comparison of legacy and V2 results for administrator diagnostics."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _items(value: Any, name: str) -> list[Any]:
    if isinstance(value, dict):
        raw = value.get(name, [])
    else:
        raw = getattr(value, name, [])
    return list(raw or [])


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _candidate_summary(items: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        exact = _field(item, "exact_match", "")
        rows.append({
            "id": _field(item, "offer_id", _field(item, "id", "")),
            "title": str(_field(item, "title", "") or ""),
            "price": _field(item, "price", None),
            "source": str(_field(item, "source", "") or ""),
            "url": str(_field(item, "url", "") or ""),
            "status": str(getattr(exact, "value", exact) or _field(item, "status", "")),
        })
    return rows


def build_shadow_comparison(
    *,
    request_id: int | str,
    legacy_result: dict[str, Any] | None,
    legacy_candidates: Iterable[Any],
    v2_result: Any,
) -> dict[str, Any]:
    """Build a JSON-safe snapshot without mutating either engine output."""
    legacy_rows = _candidate_summary(legacy_candidates)
    v2_rows = _candidate_summary(_items(v2_result, "normalized_offers"))
    rejected = _candidate_summary(_items(v2_result, "rejected_offers"))
    recommendations = _jsonable(_items(v2_result, "recommendations"))
    attempts = _jsonable(_items(v2_result, "source_attempts"))
    exact_count = sum(row["status"] == "EXACT" for row in v2_rows)
    wrong_count = sum(
        row["status"] in {"MODEL_MISMATCH", "REQUIRED_SPEC_MISMATCH", "ACCESSORY"}
        for row in rejected
    )
    legacy_prices = sorted({int(row["price"]) for row in legacy_rows if row.get("price")})
    v2_prices = sorted({int(row["price"]) for row in v2_rows if row.get("price")})
    return {
        "schema": "search_v2:shadow:1",
        "request_id": str(request_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "legacy": {
            "result": _jsonable(legacy_result or {}),
            "candidates": legacy_rows,
            "prices": legacy_prices,
        },
        "v2": {
            "status": str(getattr(_field(v2_result, "status", ""), "value", _field(v2_result, "status", ""))),
            "candidates": v2_rows,
            "rejected": rejected,
            "exact_count": exact_count,
            "wrong_product_count": wrong_count,
            "prices": v2_prices,
            "recommendations": recommendations,
            "source_attempts": attempts,
            "duration_ms": _field(v2_result, "duration", _field(v2_result, "duration_ms", 0)),
            "errors": _jsonable(_field(v2_result, "errors", [])),
        },
        "delta": {
            "candidate_count": len(v2_rows) - len(legacy_rows),
            "minimum_price": (min(v2_prices) if v2_prices else None),
            "legacy_minimum_price": (min(legacy_prices) if legacy_prices else None),
        },
    }


def format_shadow_comparison(snapshot: dict[str, Any]) -> str:
    """Plain administrator text; Telegram escaping remains the handler's job."""
    legacy = snapshot.get("legacy") if isinstance(snapshot.get("legacy"), dict) else {}
    v2 = snapshot.get("v2") if isinstance(snapshot.get("v2"), dict) else {}
    attempts = v2.get("source_attempts") if isinstance(v2.get("source_attempts"), list) else []
    lines = [
        f"Legacy ↔ V2 · заявка #{snapshot.get('request_id', '')}",
        f"Legacy кандидаты: {len(legacy.get('candidates') or [])}",
        f"V2 кандидаты: {len(v2.get('candidates') or [])}",
        f"V2 exact: {int(v2.get('exact_count') or 0)}",
        f"V2 wrong/rejected: {int(v2.get('wrong_product_count') or 0)}",
        f"V2 рекомендации: {len(v2.get('recommendations') or [])}",
        f"V2 источники: {len(attempts)}",
        f"V2 статус: {v2.get('status') or 'UNKNOWN'}",
        f"V2 время: {int(v2.get('duration_ms') or 0)} мс",
    ]
    for attempt in attempts[:10]:
        if not isinstance(attempt, dict):
            continue
        lines.append(
            f"• {attempt.get('source') or '?'}: {attempt.get('status') or '?'}"
            f" ({int(attempt.get('duration_ms') or attempt.get('duration') or 0)} мс)"
        )
    return "\n".join(lines)


__all__ = ["build_shadow_comparison", "format_shadow_comparison"]
