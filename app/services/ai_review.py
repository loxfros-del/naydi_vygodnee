"""Безопасный жизненный цикл проверки AI-карточек администратором."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.db import SearchResult, get_search_result, update_search_result
from app.verification_state import resolve_final_presentation


AI_CARD_DRAFT = "DRAFT"
AI_CARD_GENERATING = "GENERATING"
AI_CARD_GENERATED = "GENERATED"
AI_CARD_APPROVED = "APPROVED"
AI_CARD_REJECTED = "REJECTED"
AI_CARD_ERROR = "ERROR"

AI_CARD_STATUSES = frozenset({
    AI_CARD_DRAFT,
    AI_CARD_GENERATING,
    AI_CARD_GENERATED,
    AI_CARD_APPROVED,
    AI_CARD_REJECTED,
    AI_CARD_ERROR,
})

AI_CARD_TRANSITIONS = {
    AI_CARD_DRAFT: frozenset({
        AI_CARD_GENERATING,
        AI_CARD_GENERATED,
        AI_CARD_APPROVED,
        AI_CARD_REJECTED,
        AI_CARD_ERROR,
    }),
    AI_CARD_GENERATING: frozenset({AI_CARD_GENERATED, AI_CARD_ERROR}),
    AI_CARD_GENERATED: frozenset({
        AI_CARD_DRAFT,
        AI_CARD_GENERATING,
        AI_CARD_APPROVED,
        AI_CARD_REJECTED,
        AI_CARD_ERROR,
    }),
    AI_CARD_APPROVED: frozenset({AI_CARD_REJECTED}),
    AI_CARD_REJECTED: frozenset({AI_CARD_DRAFT, AI_CARD_GENERATING}),
    AI_CARD_ERROR: frozenset({AI_CARD_DRAFT, AI_CARD_GENERATING, AI_CARD_REJECTED}),
}


class AIReviewError(ValueError):
    """Базовая ошибка безопасной проверки AI-карточки."""


class AIReviewTransitionError(AIReviewError):
    """Недопустимый переход жизненного цикла карточки."""


class AIReviewEditError(AIReviewError):
    """Попытка изменить защищённые товарные факты или закрытую карточку."""


def normalize_ai_card_status(value: Any, default: str = AI_CARD_DRAFT) -> str:
    """Возвращает известный статус; старые записи безопасно считаются DRAFT."""
    normalized = str(value or "").strip().upper()
    return normalized if normalized in AI_CARD_STATUSES else default


def get_ai_card_status(card: SearchResult) -> str:
    """Читает новый столбец, сохраняя безопасное поведение для legacy-записей."""
    return normalize_ai_card_status(getattr(card, "ai_card_status", ""))


def can_transition_ai_card(current: Any, target: Any) -> bool:
    """Проверяет разрешённость перехода, включая идемпотентный переход."""
    current_status = normalize_ai_card_status(current)
    target_status = normalize_ai_card_status(target, default="")
    if not target_status:
        return False
    return current_status == target_status or target_status in AI_CARD_TRANSITIONS[current_status]


def is_ai_card_client_approved(card: SearchResult) -> bool:
    """Единственный lifecycle-гейт доступа карточки клиенту."""
    return get_ai_card_status(card) == AI_CARD_APPROVED


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return [stripped]
        if isinstance(decoded, list):
            value = decoded
        else:
            return [stripped]
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text[:500])
    return result


def _admin_meta(card: SearchResult) -> dict[str, Any]:
    raw = str(getattr(card, "admin_note", "") or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"note": raw}
    return value if isinstance(value, dict) else {"note": raw}


class AdminReviewService:
    """Применяет только разрешённые действия к AI-карточкам из БД."""

    _EDITABLE_FIELDS = frozenset({
        "why",
        "snippet",
        "risks",
        "risk_flags",
        "manual_check",
        "image_file_id",
    })

    def __init__(
        self,
        card_getter: Callable[[int], SearchResult | None] = get_search_result,
        card_updater: Callable[..., Any] = update_search_result,
    ) -> None:
        self._get_card = card_getter
        self._update_card = card_updater

    def _require_card(self, result_id: int) -> SearchResult:
        card = self._get_card(result_id)
        if card is None or str(getattr(card, "origin", "")).lower() != "alice":
            raise AIReviewError("AI-карточка не найдена.")
        return card

    def transition(self, result_id: int, target: str) -> SearchResult:
        card = self._require_card(result_id)
        current = get_ai_card_status(card)
        target_status = normalize_ai_card_status(target, default="")
        if not target_status or not can_transition_ai_card(current, target_status):
            raise AIReviewTransitionError(
                f"Недопустимый переход AI-карточки: {current} -> {target or '?'}"
            )
        if current != target_status:
            self._update_card(result_id, ai_card_status=target_status)
        return self._get_card(result_id) or card

    def approve(self, result_id: int) -> SearchResult:
        """Утверждает только подготовленный draft/generated текст."""
        card = self._require_card(result_id)
        current = get_ai_card_status(card)
        if current not in {AI_CARD_DRAFT, AI_CARD_GENERATED, AI_CARD_APPROVED}:
            raise AIReviewTransitionError(
                f"Карточку в статусе {current} нельзя утвердить."
            )
        final = resolve_final_presentation(getattr(card, "facts_json", "") or {})
        if not final.get("presentation_ready"):
            fields = ", ".join(str(item) for item in final.get("unresolved_fields") or [])
            blockers = "; ".join(str(item) for item in final.get("blocking_reasons") or [])
            detail = blockers or fields or "не завершена проверка"
            raise AIReviewTransitionError(
                f"Карточку нельзя утвердить: завершите чек-лист ({detail})."
            )
        return self.transition(result_id, AI_CARD_APPROVED)

    def reject(self, result_id: int) -> SearchResult:
        """Отклоняет draft/generated/approved/error карточку явным действием админа."""
        card = self._require_card(result_id)
        current = get_ai_card_status(card)
        if current == AI_CARD_GENERATING:
            raise AIReviewTransitionError("Нельзя отклонить карточку во время генерации.")
        return self.transition(result_id, AI_CARD_REJECTED)

    def edit(self, result_id: int, **changes: Any) -> SearchResult:
        """Редактирует текст/изображение, не позволяя менять факты кандидата."""
        card = self._require_card(result_id)
        current = get_ai_card_status(card)
        if current not in {AI_CARD_DRAFT, AI_CARD_GENERATED, AI_CARD_ERROR}:
            raise AIReviewEditError(
                f"Карточку в статусе {current} нельзя редактировать. Сначала создайте новый draft."
            )

        unknown = set(changes) - self._EDITABLE_FIELDS
        if unknown:
            fields = ", ".join(sorted(unknown))
            raise AIReviewEditError(f"Нельзя менять защищённые поля: {fields}")
        if not changes:
            raise AIReviewEditError("Нет изменений для сохранения.")

        updates: dict[str, Any] = {"ai_card_status": AI_CARD_DRAFT}
        if "why" in changes or "snippet" in changes:
            why = changes.get("why", changes.get("snippet"))
            updates["snippet"] = str(why or "").strip()[:2000]
        if "risks" in changes or "risk_flags" in changes:
            risks = changes.get("risks", changes.get("risk_flags"))
            updates["risk_flags"] = json.dumps(_as_text_list(risks), ensure_ascii=False)
        if "manual_check" in changes:
            meta = _admin_meta(card)
            meta["manual_check"] = _as_text_list(changes.get("manual_check"))
            updates["admin_note"] = json.dumps(meta, ensure_ascii=False)
        if "image_file_id" in changes:
            updates["image_file_id"] = str(changes.get("image_file_id") or "").strip()[:512]

        self._update_card(result_id, **updates)
        return self._get_card(result_id) or card
