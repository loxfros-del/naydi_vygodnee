"""Единый владелец статусов заявки и атомарных переходов."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app import db


NEW = "NEW"
NEED_CLARIFICATION = "NEED_CLARIFICATION"
WAITING_PAYMENT = "WAITING_PAYMENT"
PAID = "PAID"
SEARCHING = "SEARCHING"
ADMIN_REVIEW = "ADMIN_REVIEW"
AI_CARDS_DRAFT = "AI_CARDS_DRAFT"
READY = "READY"
DELIVERED = "DELIVERED"
CANCELLED = "CANCELLED"
FAILED = "FAILED"

CANONICAL_STATUSES = frozenset({
    NEW,
    NEED_CLARIFICATION,
    WAITING_PAYMENT,
    PAID,
    SEARCHING,
    ADMIN_REVIEW,
    AI_CARDS_DRAFT,
    READY,
    DELIVERED,
    CANCELLED,
    FAILED,
})

STATUS_LABELS = {
    NEW: "Новая",
    NEED_CLARIFICATION: "Нужно уточнение",
    WAITING_PAYMENT: "Ожидает оплаты",
    PAID: "Оплачена",
    SEARCHING: "Идёт поиск",
    ADMIN_REVIEW: "Проверка специалистом",
    AI_CARDS_DRAFT: "Карточки готовятся",
    READY: "Готова к отправке",
    DELIVERED: "Отправлена клиенту",
    CANCELLED: "Отменена",
    FAILED: "Ошибка",
}

# Значения остаются читаемыми, поэтому старые строки в SQLite переписывать не нужно.
LEGACY_STATUS_ALIASES = {
    "QUESTIONS": NEED_CLARIFICATION,
    "IN_PROGRESS": SEARCHING,
    "HUMAN_REVIEW": ADMIN_REVIEW,
    "PREVIEW_SENT": WAITING_PAYMENT,
    "READY_FOR_PAYMENT": WAITING_PAYMENT,
    "PAYMENT_CONFIRMED": PAID,
    "AI_CARDS_GENERATED": AI_CARDS_DRAFT,
    "READY_TO_SEND": READY,
    "REPORT_SENT": DELIVERED,
    "CLOSED": DELIVERED,
    "CANCELED": CANCELLED,
    "ERROR": FAILED,
}

# Граф поддерживает и новый payment-first путь, и старый preview-before-payment путь.
# DELIVERED намеренно встречается только среди переходов из READY.
ALLOWED_TRANSITIONS = {
    NEW: frozenset({
        NEED_CLARIFICATION, WAITING_PAYMENT, PAID, SEARCHING, CANCELLED, FAILED,
    }),
    NEED_CLARIFICATION: frozenset({
        NEW, WAITING_PAYMENT, PAID, SEARCHING, CANCELLED, FAILED,
    }),
    WAITING_PAYMENT: frozenset({PAID, SEARCHING, CANCELLED, FAILED}),
    PAID: frozenset({
        SEARCHING, ADMIN_REVIEW, AI_CARDS_DRAFT, READY, CANCELLED, FAILED,
    }),
    SEARCHING: frozenset({
        NEED_CLARIFICATION, ADMIN_REVIEW, READY, WAITING_PAYMENT, CANCELLED, FAILED,
    }),
    ADMIN_REVIEW: frozenset({
        NEED_CLARIFICATION, SEARCHING, AI_CARDS_DRAFT, READY, WAITING_PAYMENT,
        CANCELLED, FAILED,
    }),
    AI_CARDS_DRAFT: frozenset({
        ADMIN_REVIEW, SEARCHING, READY, WAITING_PAYMENT, CANCELLED, FAILED,
    }),
    READY: frozenset({
        WAITING_PAYMENT, ADMIN_REVIEW, AI_CARDS_DRAFT, DELIVERED, CANCELLED, FAILED,
    }),
    DELIVERED: frozenset(),
    CANCELLED: frozenset({NEW}),
    FAILED: frozenset({
        NEW, NEED_CLARIFICATION, WAITING_PAYMENT, PAID, SEARCHING, ADMIN_REVIEW,
        CANCELLED,
    }),
}

_EXTRA_FIELD_NAMES = frozenset({
    "username",
    "product",
    "product_name",
    "use_case",
    "budget",
    "city",
    "important_criteria",
    "clean_search_query",
    "is_used_allowed",
    "original_query",
    "found_products",
    "preview_text",
    "report_text",
    "alice_response",
    "search_links",
    "category",
    "request_mode",
    "requirements_json",
    "priority",
    "condition",
    "package_code",
    "progress_message_id",
    "admin_note",
})
_JSON_FIELDS = frozenset({
    "found_products", "search_links", "requirements_json",
})


class RequestStateError(ValueError):
    """Базовая ошибка state machine."""


class UnknownRequestStatus(RequestStateError):
    pass


class InvalidStatusTransition(RequestStateError):
    pass


class RequestNotFound(RequestStateError):
    pass


def _status_key(status: str) -> str:
    return str(status or "").strip().upper().replace("-", "_").replace(" ", "_")


def normalize_request_status(status: str) -> str:
    """Возвращает canonical status или явно сообщает о неизвестном значении."""
    key = _status_key(status)
    if key in CANONICAL_STATUSES:
        return key
    if key in LEGACY_STATUS_ALIASES:
        return LEGACY_STATUS_ALIASES[key]
    raise UnknownRequestStatus(f"Неизвестный статус заявки: {status!r}")


def allowed_transitions(status: str) -> frozenset[str]:
    return ALLOWED_TRANSITIONS[normalize_request_status(status)]


def can_transition(from_status: str, to_status: str) -> bool:
    try:
        source = normalize_request_status(from_status)
        target = normalize_request_status(to_status)
    except UnknownRequestStatus:
        return False
    return target in ALLOWED_TRANSITIONS[source]


def _normalise_extra_fields(extra_fields: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(extra_fields or {})
    unknown = set(values) - _EXTRA_FIELD_NAMES
    if unknown:
        raise ValueError(f"Недопустимые поля заявки: {sorted(unknown)}")
    for field_name in _JSON_FIELDS:
        value = values.get(field_name)
        if value is not None and not isinstance(value, str):
            values[field_name] = json.dumps(value, ensure_ascii=False, default=str)
    if "is_used_allowed" in values:
        values["is_used_allowed"] = int(bool(values["is_used_allowed"]))
    return values


def transition_request(
    request_id: int,
    to_status: str,
    *,
    actor: str = "",
    reason: str = "",
    extra_fields: dict[str, Any] | None = None,
) -> db.Request:
    """Атомарно меняет статус, поля заявки и пишет request_transitions."""
    target = normalize_request_status(to_status)
    updates = _normalise_extra_fields(extra_fields)
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if not row:
            raise RequestNotFound(f"Заявка #{request_id} не найдена")

        raw_source = str(row["status"] or NEW)
        source = normalize_request_status(raw_source)
        if target not in ALLOWED_TRANSITIONS[source]:
            raise InvalidStatusTransition(
                f"Переход {source} -> {target} запрещён"
            )

        now = datetime.now().isoformat()
        updates["status"] = target
        updates["updated_at"] = now
        assignments = ", ".join(f"{name} = ?" for name in updates)
        conn.execute(
            f"UPDATE requests SET {assignments} WHERE id = ?",
            [*updates.values(), request_id],
        )

        journal_extra = dict(extra_fields or {})
        if _status_key(raw_source) != source:
            journal_extra["legacy_from_status"] = raw_source
        conn.execute(
            """INSERT INTO request_transitions
               (request_id, from_status, to_status, actor, reason,
                extra_fields_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                source,
                target,
                actor,
                reason,
                json.dumps(journal_extra, ensure_ascii=False, default=str),
                now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    request = db.get_request(request_id)
    if request is None:
        raise RequestNotFound(f"Заявка #{request_id} не найдена после перехода")
    return request
