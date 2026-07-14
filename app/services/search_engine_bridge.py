"""Safe strangler bridge between Telegram-facing code, legacy search and V2."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
from typing import Any, Awaitable, Callable, Iterable

from app.search_v2.feature_flags import SearchEngineMode, get_search_engine_mode
from app.search_v2.models import RecommendationRole, SearchResultStatus, SearchResultV2
from app.search_v2.serialization import to_jsonable
from app.search_v2.shadow_compare import build_shadow_comparison
from app.search_v2.snapshot_store import SearchV2SnapshotStore


LegacySearch = Callable[[Any], dict[str, Any] | Awaitable[dict[str, Any]]]
CandidateLoader = Callable[[int], Iterable[Any]]
V2Writer = Callable[[Any, SearchResultV2], Any | Awaitable[Any]]


@dataclass(frozen=True)
class SearchEngineExecution:
    mode: SearchEngineMode
    client_result: dict[str, Any]
    v2_result: SearchResultV2 | None = None
    used_legacy_fallback: bool = False
    shadow_snapshot: dict[str, Any] | None = None


def _default_legacy_search(request: Any) -> dict[str, Any]:
    from app.product_search import run_product_search

    return run_product_search(request)


def _default_candidate_loader(request_id: int) -> Iterable[Any]:
    from app.db import get_search_results

    return get_search_results(request_id)


def _default_v2_service() -> Any:
    from app.search_v2.service import SearchServiceV2

    return SearchServiceV2()


async def _call(value: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(value):
        return await value(*args, **kwargs)
    result = await asyncio.to_thread(value, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _request_id(request: Any) -> int:
    if isinstance(request, dict):
        return int(request.get("id") or 0)
    return int(getattr(request, "id", 0) or 0)


def _v2_payload(result: SearchResultV2) -> dict[str, Any]:
    usable = len(result.normalized_offers)
    recommendations = len(result.recommendations)
    success = result.status not in {SearchResultStatus.ERROR, SearchResultStatus.TIMEOUT}
    if success and (usable or result.manual_candidates):
        message = (
            f"Search V2: найдено {usable} точных/сопоставимых предложений; "
            f"рекомендаций: {recommendations}. Требуется проверка специалистом."
        )
    elif success:
        message = "Search V2 не нашёл точных предложений. Требуется ручной поиск."
    else:
        message = "Search V2 завершился системной ошибкой. Использован legacy fallback."
    return {"success": success and bool(usable or result.manual_candidates), "found": usable, "total": usable, "message": message}


def _legacy_facts(offer: Any, suggested_role: str = "") -> dict[str, Any]:
    from app.verification_state import normalize_verification_facts

    automatic = getattr(offer, "automatic_verification", None)
    manual = getattr(offer, "manual_verification", None)
    final = getattr(offer, "final_verification", None)
    availability = getattr(offer, "availability", None)
    exact = getattr(offer, "exact_match", "UNKNOWN")
    exact_text = str(getattr(exact, "value", exact))
    automatic_payload = {
        "exact_match": exact_text,
        "exact_product_verified": bool(getattr(automatic, "model_verified", False)),
        "product_page_verified": bool(getattr(automatic, "link_verified", False)),
        "price_verified": bool(getattr(automatic, "price_verified", False)),
        "availability_verified": bool(getattr(automatic, "availability_verified", False)),
        "seller_verified": bool(getattr(automatic, "seller_verified", False)),
        "warnings": [
            *list(getattr(automatic, "warnings", []) or []),
            *list(getattr(automatic, "errors", []) or []),
        ],
    }
    manual_metadata = dict(getattr(manual, "metadata", {}) or {})
    verified_at = getattr(manual, "verified_at", None)
    manual_payload = {
        "manual_model_verified": bool(getattr(manual, "model_verified", False)),
        "manual_link_verified": bool(getattr(manual, "link_verified", False)),
        "manual_price_verified": bool(getattr(manual, "price_verified", False)),
        "manual_availability_verified": bool(getattr(manual, "availability_verified", False)),
        "manual_seller_verified": bool(getattr(manual, "seller_verified", False)),
        "manual_seller_state": str(manual_metadata.get("seller_state") or "UNSET"),
        "manual_verified_by": str(getattr(manual, "verified_by", "") or ""),
        "manual_verified_at": verified_at.isoformat() if hasattr(verified_at, "isoformat") else str(verified_at or ""),
        "manual_note": str(getattr(manual, "note", "") or ""),
        "confirmations": manual_metadata.get("confirmations", {}),
        "history": manual_metadata.get("history", []),
        "manual_model_override": bool(manual_metadata.get("explicit_model_override")),
    }
    facts = {
        "search_engine": "v2",
        "search_v2_offer": to_jsonable(offer),
        "exact_match": exact_text,
        "platform_name": str(getattr(offer, "platform", "") or ""),
        "seller": str(getattr(getattr(offer, "seller", None), "name", "") or ""),
        "available": getattr(availability, "available", None),
        "automatic_verification": automatic_payload,
        "manual_verification": manual_payload,
        "final_verification": to_jsonable(final) if final is not None else {},
        "v2_recommendation_role": suggested_role,
    }
    if final is not None:
        facts.update({
            "exact_product_verified": bool(getattr(final, "model_verified", False)),
            "product_page_verified": bool(getattr(final, "link_verified", False)),
            "price_verified": bool(getattr(final, "price_verified", False)),
            "availability_verified": bool(getattr(final, "availability_verified", False)),
            "seller_verified": bool(getattr(final, "seller_verified", False)),
        })
    return normalize_verification_facts(facts)


def persist_v2_result(request: Any, result: SearchResultV2) -> int:
    """Map V2 offers to the existing table without changing its schema."""
    from app import db

    request_id = _request_id(request)
    if not request_id:
        raise ValueError("request id is required to persist V2 results")
    for existing in db.get_search_results(request_id):
        if str(getattr(existing, "origin", "") or "").casefold() == "v2":
            db.delete_search_result(existing.id)

    suggested = {
        rec.offer_id: rec.role.value if isinstance(rec.role, RecommendationRole) else str(rec.role)
        for rec in result.recommendations
    }
    rows: list[tuple[Any, str]] = [(offer, "CANDIDATE") for offer in result.normalized_offers]
    seen = {str(offer.offer_id) for offer in result.normalized_offers}
    rows.extend((offer, "CANDIDATE") for offer in result.manual_candidates if str(offer.offer_id) not in seen)
    rows.extend((offer, "REJECTED_AUTO") for offer in result.rejected_offers if str(offer.offer_id) not in seen)
    created = 0
    for index, (offer, status) in enumerate(rows):
        role = suggested.get(str(offer.offer_id), "")
        risks = [flag.title or flag.explanation or flag.code for flag in offer.risk_flags]
        final = offer.final_verification
        db.create_search_result(
            request_id=request_id,
            title=offer.title,
            price=int(offer.price) if offer.price else None,
            source=offer.source,
            url=offer.url,
            snippet="; ".join(reason for rec in result.recommendations if rec.offer_id == offer.offer_id for reason in rec.reasons)[:1000],
            score=max((rec.score for rec in result.recommendations if rec.offer_id == offer.offer_id), default=0.0),
            risk_flags=json.dumps(risks, ensure_ascii=False),
            status=status,
            admin_note=f"Search V2 suggested: {role}" if role else "Search V2",
            origin="v2",
            sort_order=index,
            price_verified=bool(final.price_verified),
            facts_json=json.dumps(_legacy_facts(offer, role), ensure_ascii=False),
        )
        created += 1
    return created


class SearchEngineBridge:
    def __init__(
        self,
        *,
        legacy_search: LegacySearch | None = None,
        v2_service: Any | None = None,
        candidate_loader: CandidateLoader | None = None,
        v2_writer: V2Writer | None = None,
        snapshot_store: SearchV2SnapshotStore | None = None,
    ) -> None:
        self.legacy_search = legacy_search or _default_legacy_search
        self._v2_service = v2_service
        self.candidate_loader = candidate_loader or _default_candidate_loader
        self.v2_writer = v2_writer or persist_v2_result
        self.snapshot_store = snapshot_store or SearchV2SnapshotStore()

    @property
    def v2_service(self) -> Any:
        if self._v2_service is None:
            self._v2_service = _default_v2_service()
        return self._v2_service

    async def _legacy(self, request: Any) -> dict[str, Any]:
        value = await _call(self.legacy_search, request)
        if not isinstance(value, dict):
            raise TypeError("legacy search must return a dict")
        return value

    async def _v2(self, request: Any, *, on_snapshot: Callable[..., Any] | None = None) -> SearchResultV2:
        search = self.v2_service.search
        try:
            supports_snapshot = "on_snapshot" in inspect.signature(search).parameters
        except (TypeError, ValueError):
            supports_snapshot = False
        value = await _call(search, request, **({"on_snapshot": on_snapshot} if supports_snapshot else {}))
        if not isinstance(value, SearchResultV2):
            raise TypeError("SearchServiceV2.search must return SearchResultV2")
        return value

    async def execute(self, request: Any, *, mode: str | SearchEngineMode | None = None) -> SearchEngineExecution:
        resolved = mode if isinstance(mode, SearchEngineMode) else get_search_engine_mode(mode)
        if resolved is SearchEngineMode.LEGACY:
            return SearchEngineExecution(resolved, await self._legacy(request))

        request_id = _request_id(request)
        partial_index = 0

        async def save_partial(stage: str, snapshot: Any) -> None:
            nonlocal partial_index
            if not request_id:
                return
            partial_index += 1
            await asyncio.to_thread(
                self.snapshot_store.save,
                "normalized",
                f"request:{request_id}:partial:{partial_index}",
                {"stage": stage, "snapshot": to_jsonable(snapshot)},
                status="PARTIAL_SUCCESS",
                duration_ms=int(float(getattr(snapshot, "duration", 0.0) or 0.0) * 1000),
            )

        if resolved is SearchEngineMode.SHADOW:
            legacy_task = asyncio.create_task(self._legacy(request))
            v2_task = asyncio.create_task(self._v2(request, on_snapshot=save_partial))
            legacy_result = await legacy_task
            try:
                v2_result = await v2_task
                legacy_candidates = list(self.candidate_loader(request_id)) if request_id else []
                snapshot = build_shadow_comparison(
                    request_id=request_id,
                    legacy_result=legacy_result,
                    legacy_candidates=legacy_candidates,
                    v2_result=v2_result,
                )
                self.snapshot_store.save_shadow(
                    request_id,
                    snapshot,
                    status=v2_result.status.value,
                    duration_ms=int(v2_result.duration),
                    error_text="; ".join(v2_result.errors),
                )
                return SearchEngineExecution(resolved, legacy_result, v2_result=v2_result, shadow_snapshot=snapshot)
            except Exception as exc:
                if not v2_task.done():
                    v2_task.cancel()
                snapshot = {
                    "schema": "search_v2:shadow:1",
                    "request_id": str(request_id),
                    "legacy": {"result": to_jsonable(legacy_result)},
                    "v2": {"status": "ERROR", "errors": [str(exc)[:500]]},
                }
                self.snapshot_store.save_shadow(request_id, snapshot, status="ERROR", error_text=str(exc)[:500])
                return SearchEngineExecution(resolved, legacy_result, shadow_snapshot=snapshot)

        try:
            v2_result = await self._v2(request, on_snapshot=save_partial)
        except Exception:
            return SearchEngineExecution(resolved, await self._legacy(request), used_legacy_fallback=True)
        if v2_result.status in {SearchResultStatus.ERROR, SearchResultStatus.TIMEOUT}:
            return SearchEngineExecution(resolved, await self._legacy(request), v2_result=v2_result, used_legacy_fallback=True)
        await _call(self.v2_writer, request, v2_result)
        self.snapshot_store.save_result(
            f"request:{request_id}",
            v2_result,
            status=v2_result.status.value,
            duration_ms=int(v2_result.duration),
            error_text="; ".join(v2_result.errors),
        )
        return SearchEngineExecution(resolved, _v2_payload(v2_result), v2_result=v2_result)


async def run_search_for_request(request: Any, *, mode: str | SearchEngineMode | None = None) -> dict[str, Any]:
    return (await SearchEngineBridge().execute(request, mode=mode)).client_result


def load_shadow_comparison(request_id: int, store: SearchV2SnapshotStore | None = None) -> dict[str, Any] | None:
    return (store or SearchV2SnapshotStore()).load_shadow(request_id)


__all__ = [
    "SearchEngineBridge", "SearchEngineExecution", "load_shadow_comparison",
    "persist_v2_result", "run_search_for_request",
]
