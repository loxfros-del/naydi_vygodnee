"""High-level Search Engine V2 product/offer pipeline."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import inspect
import re
import time
from typing import Any, Awaitable, Callable, Iterable

from .exact_match import is_hard_mismatch
from .grouping import group_offers
from .manual_verification import resolve_final_verification
from .market_analysis import analyze_product_groups
from .metrics import build_search_metrics
from .models import (
    AvailabilityStatus,
    ExactMatchResult,
    Offer,
    ProductGroup,
    QueryPlan,
    RawOffer,
    SearchRequestV2,
    SearchResultStatus,
    SearchResultV2,
    SourceAttempt,
    SourceStatus,
    VerificationAccess,
)
from .normalization import normalize_raw_offer
from .orchestrator import OrchestrationResult, PartialSnapshot, SearchSourceOrchestrator
from .query_planner import QueryPlannerV2
from .recommendations import select_recommendations
from .request_normalizer import normalize_legacy_request
from .risk_engine import apply_risks
from .verification import verify_offer_pages


SnapshotCallback = Callable[[str, PartialSnapshot], Any | Awaitable[Any]]
_SECRET_RE = re.compile(r"(?i)(token|secret|api[_-]?key|authorization|cookie|password)\s*[=:]\s*[^\s;&]+")


def _safe_error(value: Any) -> str:
    text = " ".join(str(value or "").split())[:500]
    return _SECRET_RE.sub(r"\1=<redacted>", text)


def _raw_identity(raw: RawOffer) -> tuple[str, str]:
    source = str(raw.source or raw.platform or "").casefold()
    identity = str(raw.product_id or raw.url or f"{raw.title}|{raw.price}|{raw.seller_name}").casefold().rstrip("/")
    return source, identity


def _dedupe_raw_offers(items: Iterable[RawOffer]) -> list[RawOffer]:
    result: list[RawOffer] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = _raw_identity(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_offers(items: Iterable[Offer]) -> list[Offer]:
    result: list[Offer] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.offer_id or item.url or f"{item.source}|{item.title}|{item.price}")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _manual_required(offer: Offer) -> bool:
    final = offer.final_verification
    required = (
        final.model_verified,
        final.link_verified,
        final.price_verified,
        final.availability_verified,
        final.seller_verified,
    )
    return (
        not all(value is True for value in required)
        or offer.verification_access is VerificationAccess.BLOCKED
        or offer.exact_match in {ExactMatchResult.GENERIC_MATCH, ExactMatchResult.UNKNOWN}
    )


def _risk_groups(groups: Iterable[ProductGroup]) -> list[ProductGroup]:
    result: list[ProductGroup] = []
    for group in groups:
        offers = [resolve_final_verification(apply_risks(offer, group.market_stats)) for offer in group.offers]
        result.append(replace(group, offers=offers))
    return result


class SearchServiceV2:
    """One public V2 API; dependencies are injectable for deterministic tests."""

    def __init__(
        self,
        *,
        normalizer: Callable[[Any], SearchRequestV2] = normalize_legacy_request,
        planner: QueryPlannerV2 | None = None,
        orchestrator: SearchSourceOrchestrator | None = None,
        page_verifier: Callable[[Offer], Any] | None = None,
        discovery_sources: Iterable[str] = ("yandex_market", "ozon", "avito"),
        anchor_sources: Iterable[str] = ("dns",),
        generic_sources: Iterable[str] = ("generic_search",),
        overall_timeout: float = 45.0,
        page_verification_timeout: float = 8.0,
        page_verification_limit: int = 4,
    ) -> None:
        self.normalizer = normalizer
        self.planner = planner or QueryPlannerV2(max_queries_per_source=2)
        if orchestrator is None:
            from app.search_cache import DEFAULT_CACHE_PATH
            from .cache import SQLiteSourceCache

            orchestrator = SearchSourceOrchestrator(
                cache=SQLiteSourceCache(DEFAULT_CACHE_PATH),
                max_concurrency=3,
                per_source_timeout=12.0,
                case_timeout=min(30.0, max(1.0, overall_timeout)),
            )
        self.orchestrator = orchestrator
        self.page_verifier = page_verifier
        self.discovery_sources = tuple(discovery_sources)
        self.anchor_sources = tuple(anchor_sources)
        self.generic_sources = tuple(generic_sources)
        self.overall_timeout = max(1.0, float(overall_timeout))
        self.page_verification_timeout = max(0.1, float(page_verification_timeout))
        self.page_verification_limit = max(0, min(int(page_verification_limit or 0), 8))

    async def _stage(
        self,
        *,
        stage: str,
        request: SearchRequestV2,
        sources: tuple[str, ...],
        deadline: float,
        include_over_budget: bool,
        on_snapshot: SnapshotCallback | None,
    ) -> tuple[QueryPlan, OrchestrationResult | None, str]:
        plan = self.planner.plan(
            request,
            sources=sources,
            include_over_budget=include_over_budget,
        )
        if not plan.source_queries:
            return plan, None, ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return plan, None, f"{stage}: overall timeout"

        async def relay(snapshot: PartialSnapshot) -> None:
            if on_snapshot is None:
                return
            value = on_snapshot(stage, snapshot)
            if asyncio.iscoroutine(value):
                await value

        try:
            result = await asyncio.wait_for(
                self.orchestrator.run(request, plan, on_snapshot=relay),
                timeout=remaining,
            )
            return plan, result, ""
        except asyncio.TimeoutError:
            return plan, None, f"{stage}: overall timeout"
        except Exception as exc:
            return plan, None, f"{stage}: {_safe_error(exc)}"

    @staticmethod
    def _combine_plans(request: SearchRequestV2, plans: Iterable[QueryPlan]) -> QueryPlan:
        accepted = []
        rejected = []
        seen: set[tuple[str, str, str]] = set()
        for plan in plans:
            for query in plan.source_queries:
                key = (query.source, query.tier.value, query.query.casefold())
                if key not in seen:
                    seen.add(key)
                    accepted.append(query)
            rejected.extend(plan.rejected_queries)
        return QueryPlan(request=request, source_queries=accepted, rejected_queries=rejected)

    @staticmethod
    def _normalize(raws: Iterable[RawOffer], request: SearchRequestV2) -> tuple[list[Offer], list[str]]:
        offers: list[Offer] = []
        errors: list[str] = []
        for raw in raws:
            try:
                offers.append(normalize_raw_offer(raw, request))
            except Exception as exc:
                errors.append(f"normalize:{raw.source}:{_safe_error(exc)}")
        return _dedupe_offers(offers), errors

    async def search(
        self,
        request: Any,
        *,
        on_snapshot: SnapshotCallback | None = None,
    ) -> SearchResultV2:
        started = time.monotonic()
        try:
            normalized_request = request if isinstance(request, SearchRequestV2) else self.normalizer(request)
        except Exception as exc:
            return SearchResultV2(
                status=SearchResultStatus.ERROR,
                duration=(time.monotonic() - started) * 1000,
                errors=[f"request_normalization:{_safe_error(exc)}"],
            )
        if not normalized_request.supported_category:
            return SearchResultV2(
                normalized_request=normalized_request,
                status=SearchResultStatus.UNSUPPORTED_CATEGORY,
                duration=(time.monotonic() - started) * 1000,
            )

        deadline = started + self.overall_timeout
        include_over_budget = bool(normalized_request.budget)
        plans: list[QueryPlan] = []
        results: list[OrchestrationResult] = []
        errors: list[str] = []

        for stage, sources in (("discovery", self.discovery_sources), ("anchor", self.anchor_sources)):
            if not sources:
                continue
            plan, result, error = await self._stage(
                stage=stage,
                request=normalized_request,
                sources=sources,
                deadline=deadline,
                include_over_budget=include_over_budget,
                on_snapshot=on_snapshot,
            )
            plans.append(plan)
            if result is not None:
                results.append(result)
            if error:
                errors.append(error)

        raw_offers = _dedupe_raw_offers(raw for result in results for raw in result.raw_offers)
        preview, preview_errors = self._normalize(raw_offers, normalized_request)
        errors.extend(preview_errors)
        has_exact = any(offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT} for offer in preview)
        if not has_exact and self.generic_sources and time.monotonic() < deadline:
            plan, result, error = await self._stage(
                stage="generic_fallback",
                request=normalized_request,
                sources=self.generic_sources,
                deadline=deadline,
                include_over_budget=include_over_budget,
                on_snapshot=on_snapshot,
            )
            plans.append(plan)
            if result is not None:
                results.append(result)
            if error:
                errors.append(error)
            raw_offers = _dedupe_raw_offers(raw for item in results for raw in item.raw_offers)

        offers, normalization_errors = self._normalize(raw_offers, normalized_request)
        errors.extend(normalization_errors)
        if self.page_verifier and offers and self.page_verification_limit:
            # Only exact product pages that still lack a price are opened. This
            # keeps page verification bounded and avoids wasting network calls
            # on hard mismatches, search pages or already priced offers.
            eligible = [
                offer for offer in offers
                if offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
                and bool(offer.url)
                and not bool(offer.raw_metadata.get("not_product_page"))
                and not offer.price
            ]
            remaining = max(0.0, deadline - time.monotonic())
            time_budget_limit = int((remaining * 2) // self.page_verification_timeout) if remaining else 0
            selected = eligible[: min(self.page_verification_limit, max(0, time_budget_limit))]

            async def bound_page_verifier(offer: Offer) -> Any:
                verifier = self.page_verifier
                if verifier is None:
                    return None
                try:
                    parameters = inspect.signature(verifier).parameters
                    accepts_request = len(parameters) >= 2
                except (TypeError, ValueError):
                    accepts_request = False
                result = verifier(offer, normalized_request) if accepts_request else verifier(offer)
                if inspect.isawaitable(result):
                    return await result
                return result

            if selected:
                verified = await verify_offer_pages(
                    selected,
                    bound_page_verifier,
                    max_concurrency=2,
                    timeout=self.page_verification_timeout,
                )
                by_verified_id = {offer.offer_id: offer for offer in verified}
                offers = [by_verified_id.get(offer.offer_id, offer) for offer in offers]

        rejected: list[Offer] = []
        retained: list[Offer] = []
        for offer in offers:
            if (
                is_hard_mismatch(offer.exact_match)
                or offer.availability.status is AvailabilityStatus.OUT_OF_STOCK
                or bool(offer.raw_metadata.get("not_product_page"))
            ):
                rejected.append(apply_risks(offer))
            else:
                retained.append(offer)

        comparable = [
            offer for offer in retained
            if offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
            and bool(offer.url)
        ]
        groups = _risk_groups(analyze_product_groups(group_offers(comparable)))
        by_id = {offer.offer_id: offer for group in groups for offer in group.offers}
        normalized_offers = [by_id.get(offer.offer_id, apply_risks(offer)) for offer in retained]

        recommendation_groups = groups
        if normalized_request.budget:
            in_budget_ids = {
                offer.offer_id for group in groups for offer in group.offers
                if offer.price and offer.price <= normalized_request.budget
            }
            if in_budget_ids:
                recommendation_groups = [
                    replace(group, offers=[offer for offer in group.offers if offer.offer_id in in_budget_ids])
                    for group in groups
                ]
                recommendation_groups = [group for group in recommendation_groups if group.offers]
        recommendations = select_recommendations(recommendation_groups)
        manual_candidates = _dedupe_offers(offer for offer in normalized_offers if _manual_required(offer))

        attempts: list[SourceAttempt] = [
            replace(attempt, error=_safe_error(attempt.error))
            for result in results for attempt in result.attempts
        ]
        source_errors = [_safe_error(error) for result in results for error in result.errors if error]
        errors = list(dict.fromkeys(error for error in (*errors, *source_errors) if error))
        duration_ms = (time.monotonic() - started) * 1000
        timed_out = any(result.case_timed_out or result.status is SourceStatus.TIMEOUT for result in results) or any("timeout" in error for error in errors)
        partial = any(result.status not in {SourceStatus.SUCCESS, SourceStatus.EMPTY} for result in results)

        if not comparable:
            status = SearchResultStatus.TIMEOUT if timed_out and not retained else SearchResultStatus.NO_EXACT_MATCH
        elif partial or errors:
            status = SearchResultStatus.PARTIAL_SUCCESS
        elif manual_candidates:
            status = SearchResultStatus.MANUAL_REVIEW_REQUIRED
        else:
            status = SearchResultStatus.SUCCESS
        metrics = build_search_metrics(
            attempts=attempts,
            raw_offer_count=len(raw_offers),
            offers=normalized_offers,
            rejected_offers=rejected,
            groups=groups,
            recommendations=recommendations,
            duration_ms=duration_ms,
            manual_review_required=bool(manual_candidates),
        )
        return SearchResultV2(
            normalized_request=normalized_request,
            query_plan=self._combine_plans(normalized_request, plans),
            source_attempts=attempts,
            raw_offer_count=len(raw_offers),
            normalized_offers=normalized_offers,
            rejected_offers=rejected,
            product_groups=groups,
            market_stats=[group.market_stats for group in groups if group.market_stats is not None],
            recommendations=recommendations,
            manual_candidates=manual_candidates,
            metrics=metrics,
            duration=duration_ms,
            status=status,
            errors=errors,
        )


__all__ = ["SearchServiceV2"]
