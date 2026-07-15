"""Безопасный отбор AI-рекомендаций для клиентского представления."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.db import SearchResult, get_alice_results
from app.ai_cards_service import is_ai_card_candidate_eligible
from app.services.ai_review import is_ai_card_client_approved


ROLE_BEST = "BEST"
ROLE_BACKUP = "BACKUP"
ROLE_BUDGET = "BUDGET"
ROLE_CAUTION = "CAUTION"

ROLE_ALIASES = {
    "BEST": ROLE_BEST,
    "TOP": ROLE_BEST,
    "TOP1": ROLE_BEST,
    "BACKUP": ROLE_BACKUP,
    "APPROVED_BACKUP": ROLE_BACKUP,
    "APPROVED": ROLE_BACKUP,
    "RELIABLE": ROLE_BACKUP,
    "BUDGET": ROLE_BUDGET,
    "APPROVED_BUDGET": ROLE_BUDGET,
    "CHEAP": ROLE_BUDGET,
    "CAUTION": ROLE_CAUTION,
    "DO_NOT_BUY": ROLE_CAUTION,
}

CLIENT_ROLE_ORDER = {
    ROLE_BEST: 0,
    ROLE_BUDGET: 1,
    ROLE_BACKUP: 2,
}

CLIENT_EXCLUDED_STATUSES = frozenset({
    "REJECTED",
    "REJECTED_AUTO",
    "DO_NOT_BUY",
    "CAUTION",
})


def normalize_recommendation_role(value: str | None) -> str:
    """Нормализует legacy-роли без изменения записи в БД."""
    return ROLE_ALIASES.get(str(value or "").strip().upper(), "")


@dataclass(frozen=True)
class ClientRecommendation:
    card: SearchResult
    role: str


def _identity(card: SearchResult) -> tuple[str, str]:
    url = str(getattr(card, "url", "") or "").strip().casefold()
    if url:
        return "url", url.rstrip("/")
    title = " ".join(str(getattr(card, "title", "") or "").casefold().split())
    source = " ".join(str(getattr(card, "source", "") or "").casefold().split())
    return "title", f"{title}|{source}"


class RecommendationService:
    """Возвращает максимум три явно утверждённые Alice-карточки."""

    def __init__(
        self,
        cards_getter: Callable[[int], list[SearchResult]] = get_alice_results,
    ) -> None:
        self._get_cards = cards_getter

    @staticmethod
    def select(cards: Iterable[SearchResult], limit: int = 3) -> list[ClientRecommendation]:
        safe_limit = min(max(int(limit), 0), 3)
        if safe_limit == 0:
            return []

        eligible: list[tuple[int, int, int, SearchResult, str]] = []
        for order, card in enumerate(cards):
            if str(getattr(card, "origin", "") or "").strip().lower() != "alice":
                continue
            if not is_ai_card_client_approved(card):
                continue
            if not is_ai_card_candidate_eligible(card):
                continue
            raw_status = str(getattr(card, "status", "") or "").strip().upper()
            if raw_status in CLIENT_EXCLUDED_STATUSES:
                continue
            role = normalize_recommendation_role(raw_status)
            if role not in CLIENT_ROLE_ORDER:
                continue
            eligible.append((
                CLIENT_ROLE_ORDER[role],
                int(getattr(card, "sort_order", 0) or 0),
                order,
                card,
                role,
            ))

        eligible.sort(key=lambda item: (item[0], item[1], item[2]))
        recommendations: list[ClientRecommendation] = []
        seen_roles: set[str] = set()
        seen_items: set[tuple[str, str]] = set()
        for _priority, _sort_order, _order, card, role in eligible:
            identity = _identity(card)
            if role in seen_roles or identity in seen_items:
                continue
            seen_roles.add(role)
            seen_items.add(identity)
            recommendations.append(ClientRecommendation(card=card, role=role))
            if len(recommendations) >= safe_limit:
                break
        return recommendations

    def for_request(self, request_id: int, limit: int = 3) -> list[ClientRecommendation]:
        return self.select(self._get_cards(request_id), limit=limit)

    @staticmethod
    def client_cards(cards: Iterable[SearchResult], limit: int = 3) -> list[SearchResult]:
        """Совместимый helper для кода, которому нужны исходные SearchResult."""
        return [item.card for item in RecommendationService.select(cards, limit=limit)]
