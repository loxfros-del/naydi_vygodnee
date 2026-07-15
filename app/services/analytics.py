"""Лёгкая продуктовая аналитика поверх append-only SQLite events."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from app import db


ALLOWED_EVENT_TYPES = frozenset({
    "start",
    "pricing_opened",
    "request_started",
    "request_completed",
    "request_cancelled",
    "payment_started",
    "payment_succeeded",
    "search_started",
    "admin_review_started",
    "recommendation_ready",
    "delivered",
    "feedback_submitted",
    "support_requested",
    "unsupported_category",
})

_SENSITIVE_KEY_PARTS = (
    "token", "secret", "password", "api_key", "authorization", "cookie",
)


def _safe_event_type(event_type: str) -> str:
    value = str(event_type or "").strip().lower()
    if value not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"Неизвестный тип события: {event_type!r}")
    return value


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                continue
            clean[key_text] = _sanitize_value(item)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def track_event(
    event_type: str,
    *,
    user_id: int | None = None,
    request_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> int:
    """Сохраняет одно безопасное продуктовое событие без лишних PII."""
    return db.create_product_event(
        _safe_event_type(event_type),
        user_id=user_id,
        request_id=request_id,
        payload=_sanitize_value(metadata or {}),
        created_at=created_at,
    )


def record_feedback(
    *,
    user_id: int,
    request_id: int | None = None,
    rating: int,
    feedback_type: str = "rating",
    comment: str = "",
) -> db.Feedback:
    feedback_id = db.create_feedback(
        user_id=user_id,
        request_id=request_id,
        rating=rating,
        feedback_type=feedback_type,
        comment=comment,
    )
    track_event(
        "feedback_submitted",
        user_id=user_id,
        request_id=request_id,
        metadata={"rating": int(rating), "feedback_type": feedback_type},
    )
    feedback = db.get_feedback(feedback_id)
    if feedback is None:
        raise RuntimeError("Не удалось прочитать сохранённый feedback")
    return feedback


def get_low_feedback(max_rating: int = 2, limit: int = 50) -> list[db.Feedback]:
    return db.get_low_feedback(max_rating=max_rating, limit=limit)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def build_product_metrics(
    events: Iterable[db.ProductEvent] | None = None,
) -> dict[str, Any]:
    """Строит минимальные метрики детерминированно из списка событий."""
    rows = list(events) if events is not None else db.get_product_events(limit=10_000)
    counts = Counter(row.event_type for row in rows)
    started_at: dict[int, datetime] = {}
    delivered_at: dict[int, datetime] = {}
    manual_requests: set[int] = set()
    ready_requests: set[int] = set()

    for row in rows:
        timestamp = _parse_timestamp(row.created_at)
        if row.request_id is not None and timestamp is not None:
            if row.event_type == "request_started":
                started_at.setdefault(row.request_id, timestamp)
            elif row.event_type == "delivered":
                delivered_at.setdefault(row.request_id, timestamp)
        if row.request_id is not None and row.event_type == "admin_review_started":
            manual_requests.add(row.request_id)
        if row.request_id is not None and row.event_type == "recommendation_ready":
            ready_requests.add(row.request_id)

    durations = [
        (delivered_at[request_id] - started).total_seconds()
        for request_id, started in started_at.items()
        if request_id in delivered_at and delivered_at[request_id] >= started
    ]
    manual_base = ready_requests or set(started_at)
    manual_rate = (
        round(100 * len(manual_requests & manual_base) / len(manual_base), 1)
        if manual_base else 0.0
    )

    return {
        "event_counts": dict(sorted(counts.items())),
        "requests_started": counts["request_started"],
        "requests_completed": counts["request_completed"],
        "payments": counts["payment_succeeded"],
        "ready_recommendations": counts["recommendation_ready"],
        "delivered": counts["delivered"],
        "feedback_count": counts["feedback_submitted"],
        "support_requests": counts["support_requested"],
        "unsupported_categories": counts["unsupported_category"],
        "average_delivery_seconds": (
            round(sum(durations) / len(durations), 1) if durations else None
        ),
        "manual_review_percent": manual_rate,
    }


def event_payload(event: db.ProductEvent) -> dict[str, Any]:
    """Безопасно читает JSON payload для UI/отладки."""
    try:
        value = json.loads(event.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
