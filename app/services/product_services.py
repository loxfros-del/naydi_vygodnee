"""Небольшие business-service фасады, не зависящие от Telegram update."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app import db
from app.ai_cards_service import generate_ai_cards_from_candidates
from app.product_config import SearchMode, classify_request, get_service_packages, is_supported_auto_category
from app.request_parser import build_request_search_query
from app.report_builder import build_full_report
from app.services.state_machine import READY, normalize_request_status
from app.ui_formatters import format_pricing


class PaymentPresentationService:
    @staticmethod
    def packages():
        return get_service_packages()

    @staticmethod
    def text() -> str:
        return format_pricing(get_service_packages())


class SearchOrchestrationService:
    @staticmethod
    def route(request: db.Request) -> SearchMode:
        mode = str(getattr(request, "request_mode", "") or "").upper()
        if mode == SearchMode.LINK_COMPARISON.value:
            return SearchMode.LINK_COMPARISON
        if mode == SearchMode.MANUAL.value:
            return SearchMode.MANUAL
        if is_supported_auto_category(getattr(request, "category", "")):
            return SearchMode.AUTO
        decision = classify_request(" ".join(filter(None, (
            request.product_name, request.product, request.original_query,
        ))))
        return decision.mode if decision.mode is SearchMode.AUTO else SearchMode.MANUAL


class RequestService:
    @staticmethod
    def create_from_payload(*, user_id: int, username: str, payload: dict[str, Any], package_code: str) -> int:
        return db.create_request(
            user_id=user_id,
            username=username,
            product=payload.get("product") or payload.get("product_name") or "товар",
            product_name=payload.get("product_name") or payload.get("product") or "товар",
            budget=str(payload.get("budget") or ""),
            city=str(payload.get("city") or ""),
            important_criteria=str(payload.get("important_criteria") or ""),
            clean_search_query=str(payload.get("clean_search_query") or build_request_search_query(payload)),
            is_used_allowed=bool(payload.get("is_used_allowed")),
            original_query=str(payload.get("original_query") or payload.get("product") or ""),
            category=str(payload.get("category") or "manual"),
            request_mode=str(payload.get("request_mode") or SearchMode.MANUAL.value),
            requirements_json=json.dumps(payload, ensure_ascii=False),
            priority=str(payload.get("priority") or "balance"),
            condition=str(payload.get("condition") or "new"),
            package_code=package_code,
        )


@dataclass(frozen=True)
class DeliveryDecision:
    allowed: bool
    reason: str = ""
    report: str = ""


class DeliveryService:
    @staticmethod
    def prepare(request: db.Request) -> DeliveryDecision:
        try:
            status = normalize_request_status(request.status)
        except Exception:
            return DeliveryDecision(False, "Неизвестный статус заявки.")
        if status != READY:
            return DeliveryDecision(False, "Результат ещё не утверждён.")
        report = build_full_report(request)
        if report.startswith("Нельзя"):
            return DeliveryDecision(False, report)
        return DeliveryDecision(True, report=report)


class SearchOrchestrationAICardService:
    @staticmethod
    def generate(request: db.Request, candidates: list[db.SearchResult]) -> dict:
        return generate_ai_cards_from_candidates(request, candidates)


__all__ = [
    "DeliveryDecision",
    "DeliveryService",
    "PaymentPresentationService",
    "RequestService",
    "SearchOrchestrationAICardService",
    "SearchOrchestrationService",
]
