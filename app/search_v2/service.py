"""High-level Search Engine V2 product/offer pipeline."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re
import time
from typing import Any, Awaitable, Callable, Iterable

from .exact_match import is_hard_mismatch
from .grouping import group_offers
from .manual_verification import resolve_final_verification
from .market_analysis import analyze_product_groups, is_comparable_offer, is_extreme_price_outlier
from .market_history import ComparableMarketOffer, MarketLane, NullMarketHistoryStore, canonical_hash
from .metrics import build_search_metrics
from .models import (
    AvailabilityStatus,
    ExactMatchResult,
    Offer,
    PlatformTrust,
    ProductCondition,
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
from .normalization import is_product_page_url, normalize_raw_offer
from .orchestrator import OrchestrationResult, PartialSnapshot, SearchSourceOrchestrator
from .query_planner import QueryPlannerV2
from .recommendations import select_recommendations
from .request_normalizer import normalize_legacy_request
from .risk_engine import apply_risks
from .verification import verify_offer_pages


SnapshotCallback = Callable[[str, PartialSnapshot], Any | Awaitable[Any]]
_SECRET_RE = re.compile(r"(?i)(token|secret|api[_-]?key|authorization|cookie|password)\s*[=:]\s*[^\s;&]+")
_MARKET_HISTORY_MAX_AGE = timedelta(hours=36)


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


def _page_verification_priority(offer: Offer) -> tuple[int, int, float, str]:
    """Verify the most promising exact direct links before weaker rows."""
    exact_rank = {
        ExactMatchResult.EXACT: 0,
        ExactMatchResult.COMPATIBLE_VARIANT: 1,
        ExactMatchResult.GENERIC_MATCH: 2,
        ExactMatchResult.UNKNOWN: 3,
    }.get(offer.exact_match, 4)
    missing_price = 0 if offer.price and offer.price > 0 else 1
    return exact_rank, missing_price, float(offer.price or 10**18), str(offer.offer_id)


def _priced_exact_sources(offers: Iterable[Offer]) -> set[str]:
    """Sources that can actually establish a comparable market price."""
    return {
        str(offer.source or offer.platform).casefold()
        for offer in offers
        if (
            offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
            and bool(offer.price and offer.price > 0)
            and str(offer.source or offer.platform).strip()
        )
    }


def _priced_comparable_offer_count(offers: Iterable[Offer]) -> int:
    """Count usable comparable prices without confusing snippets with offers."""
    return sum(
        1
        for offer in offers
        if (
            offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
            and bool(offer.price and offer.price > 0)
            and bool(offer.url)
        )
    )


def _refresh_verified_external_offer(offer: Offer, request: SearchRequestV2) -> Offer:
    """Re-run identity matching after a web page replaces a search snippet.

    Web discovery starts with a result title only.  A verified page can reveal
    a different memory/SIM variant, so the final title and price must pass the
    normal exact-match boundary a second time before recommendation.
    """
    metadata = dict(offer.raw_metadata) if isinstance(offer.raw_metadata, dict) else {}
    verification = metadata.get("external_page_verification")
    if not (
        bool(metadata.get("external_page_verified"))
        and isinstance(verification, dict)
        and verification.get("verified") is True
    ):
        return offer
    raw = RawOffer(
        source=offer.source,
        platform=offer.platform,
        title=offer.title,
        url=offer.url,
        product_id=offer.product_id,
        seller_name=offer.seller.name,
        price=offer.price,
        old_price=offer.old_price,
        currency=offer.currency,
        availability_text=offer.availability.source_text,
        condition=offer.condition,
        city=offer.city,
        delivery=offer.delivery,
        image_url=offer.image_url,
        raw_metadata=metadata,
        retrieved_at=offer.retrieved_at,
    )
    refreshed = normalize_raw_offer(raw, request)
    return replace(refreshed, cache_age=offer.cache_age)


def _risk_groups(groups: Iterable[ProductGroup]) -> list[ProductGroup]:
    result: list[ProductGroup] = []
    for group in groups:
        offers = [resolve_final_verification(apply_risks(offer, group.market_stats)) for offer in group.offers]
        result.append(replace(group, offers=offers))
    return result


def _market_history_condition(offer: Offer) -> ProductCondition:
    """Use the normalized condition without mixing new, used and refurbished."""
    if offer.condition is not ProductCondition.UNKNOWN:
        return offer.condition
    if offer.identity is not None:
        return offer.identity.condition
    return ProductCondition.UNKNOWN


def _market_history_lane(offer: Offer) -> MarketLane | None:
    """Choose a non-interchangeable market lane, or skip uncertain offers."""
    condition = _market_history_condition(offer)
    if condition is ProductCondition.REFURBISHED:
        return MarketLane.REFURBISHED
    if condition is ProductCondition.USED:
        return MarketLane.USED
    if condition is not ProductCondition.NEW:
        return None
    if offer.platform_trust is PlatformTrust.HIGH_RETAIL:
        return MarketLane.NEW_RETAIL
    if offer.platform_trust is PlatformTrust.HIGH_MARKETPLACE:
        return MarketLane.NEW_MARKETPLACE
    if (
        offer.platform_trust is PlatformTrust.CLASSIFIED
        or str(offer.seller.seller_type or "").casefold() == "private"
    ):
        return MarketLane.NEW_PRIVATE
    return None


def _is_current_market_history_offer(offer: Offer, now: datetime) -> bool:
    """Keep history as strict as the current-price evidence boundary."""
    timestamp = offer.retrieved_at
    if not isinstance(timestamp, datetime):
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = now - timestamp.astimezone(timezone.utc)
    if not timedelta(minutes=-5) <= age <= _MARKET_HISTORY_MAX_AGE:
        return False
    return offer.cache_age is None or 0 <= offer.cache_age <= _MARKET_HISTORY_MAX_AGE.total_seconds()


def _is_market_history_eligible(offer: Offer, now: datetime) -> bool:
    """History only sees current, direct, exact comparable product pages."""
    return bool(
        is_comparable_offer(offer)
        and offer.availability.status is AvailabilityStatus.IN_STOCK
        and is_product_page_url(offer.url)
        and _is_current_market_history_offer(offer, now)
    )


def _market_history_identity(identity: Any) -> dict[str, Any] | None:
    """Return a hash-only input; no title, link, seller or query is persisted."""
    if identity is None:
        return None
    category = str(getattr(identity, "category", "") or "").strip()
    model = str(getattr(identity, "canonical_model", "") or "").strip()
    if not category or not model:
        return None
    return {
        "category": category,
        "brand": str(getattr(identity, "brand", "") or ""),
        "model": model,
        "modifiers": list(getattr(identity, "modifiers", ()) or ()),
        "storage": getattr(identity, "storage", None),
        "size": getattr(identity, "size", None),
        "diagonal": getattr(identity, "diagonal", None),
        "refresh_rate": getattr(identity, "refresh_rate", None),
        "configuration": dict(getattr(identity, "key_configuration", {}) or {}),
        "condition": _market_history_condition_value(getattr(identity, "condition", None)),
        "region_or_sim_variant": str(getattr(identity, "region_or_sim_variant", "") or ""),
    }


def _market_history_condition_value(value: Any) -> str:
    return str(getattr(value, "value", value) or ProductCondition.UNKNOWN.value)


def _market_history_scope(request: SearchRequestV2, offer: Offer) -> dict[str, str]:
    """Keep raw locality out of even the injected history-store contract."""
    metadata = offer.raw_metadata if isinstance(offer.raw_metadata, dict) else {}
    source_confirmed_city = bool(metadata.get("region_scope_confirmed"))
    city = str(
        offer.city
        or offer.availability.city
        or offer.seller.city
        or (request.city if source_confirmed_city else "")
        or ""
    ).strip()
    delivery = str(offer.delivery or offer.availability.delivery or "").strip()
    return {
        "city_hash": canonical_hash(city or "unknown", namespace="market-city"),
        "coverage": "local" if city else ("delivery" if delivery else "unknown"),
        "currency": str(offer.currency or "RUB").upper(),
    }


class SearchServiceV2:
    """One public V2 API; dependencies are injectable for deterministic tests."""

    def __init__(
        self,
        *,
        normalizer: Callable[[Any], SearchRequestV2] = normalize_legacy_request,
        planner: QueryPlannerV2 | None = None,
        orchestrator: SearchSourceOrchestrator | None = None,
        market_history_store: Any | None = None,
        page_verifier: Callable[[Offer], Any] | None = None,
        page_verification_limit: int = 6,
        web_discovery_sources: Iterable[str] = (),
        discovery_sources: Iterable[str] = ("yandex_market", "ozon", "avito"),
        anchor_sources: Iterable[str] = ("dns",),
        generic_sources: Iterable[str] = ("generic_search",),
        minimum_exact_sources_before_fallback: int = 2,
        minimum_comparable_offers_before_fallback: int = 3,
        overall_timeout: float = 45.0,
        page_verification_timeout: float = 8.0,
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
        self.market_history_store = market_history_store if market_history_store is not None else NullMarketHistoryStore()
        self.page_verifier = page_verifier
        self.page_verification_limit = max(1, min(int(page_verification_limit or 6), 10))
        self.web_discovery_sources = tuple(web_discovery_sources)
        self.discovery_sources = tuple(discovery_sources)
        self.anchor_sources = tuple(anchor_sources)
        self.generic_sources = tuple(generic_sources)
        self.minimum_exact_sources_before_fallback = max(1, int(minimum_exact_sources_before_fallback))
        self.minimum_comparable_offers_before_fallback = max(1, int(minimum_comparable_offers_before_fallback))
        self.overall_timeout = max(1.0, float(overall_timeout))
        self.page_verification_timeout = max(0.1, float(page_verification_timeout))

    def _can_verify_flagged_page(self, offer: Offer) -> bool:
        """Allow an unknown direct URL shape only through the bounded verifier."""
        if self.page_verifier is None:
            return False
        metadata = offer.raw_metadata if isinstance(offer.raw_metadata, dict) else {}
        if bool(metadata.get("page_verification_required")):
            return True
        registry = getattr(self.orchestrator, "registry", None)
        adapter = registry.get(offer.source) if registry is not None else None
        capabilities = getattr(adapter, "capabilities", None)
        return bool(getattr(capabilities, "requires_page_verification", False))

    def _capture_market_history(
        self,
        request: SearchRequestV2,
        groups: Iterable[ProductGroup],
    ) -> None:
        """Best-effort snapshot capture outside ranking and client output.

        The durable store receives an identity and a scope only to hash them.
        It never receives titles, queries, URLs, sellers, or raw locality data
        for storage. Any failure is intentionally invisible to the search.
        """
        now = datetime.now(timezone.utc)
        try:
            for group in groups:
                canonical_identity = _market_history_identity(group.identity)
                if canonical_identity is None:
                    continue
                buckets: dict[tuple[MarketLane, str, str, str], tuple[dict[str, str], list[ComparableMarketOffer]]] = {}
                for offer in group.offers:
                    if not _is_market_history_eligible(offer, now):
                        continue
                    lane = _market_history_lane(offer)
                    if lane is None:
                        continue
                    scope = _market_history_scope(request, offer)
                    key = (lane, scope["city_hash"], scope["coverage"], scope["currency"])
                    entry = buckets.get(key)
                    if entry is None:
                        entry = (scope, [])
                        buckets[key] = entry
                    entry[1].append(
                        ComparableMarketOffer(
                            price=offer.price,
                            source=str(offer.source or offer.platform or ""),
                            comparable=True,
                            current=True,
                            observed_at=offer.retrieved_at,
                        )
                    )
                for (lane, _city_hash, _coverage, _currency), (scope, offers) in buckets.items():
                    try:
                        self.market_history_store.capture(
                            canonical_identity=canonical_identity,
                            scope=scope,
                            lane=lane,
                            offers=offers,
                            observed_on=now,
                        )
                    except Exception:
                        # A history snapshot is never allowed to degrade search.
                        continue
        except Exception:
            # Keep a misconfigured optional dependency outside the result path.
            return

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
        source_capabilities = {}
        registry = getattr(self.orchestrator, "registry", None)
        for source in sources:
            adapter = registry.get(source) if registry is not None else None
            if adapter is not None:
                source_capabilities[source] = getattr(adapter, "capabilities", None)
        plan = self.planner.plan(
            request,
            sources=sources,
            include_over_budget=include_over_budget,
            source_capabilities=source_capabilities,
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

        # Start with the primary marketplace scan. Optional web discovery can
        # be slow or temporarily unavailable, so it must never consume the
        # first time window and prevent the core sources from producing a
        # usable result. Its links are still fail-closed later by the direct
        # page verifier.
        for stage, sources in (
            ("discovery", self.discovery_sources),
            ("web_discovery", self.web_discovery_sources),
            ("anchor", self.anchor_sources),
        ):
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
        exact_price_sources = _priced_exact_sources(preview)
        comparable_price_count = _priced_comparable_offer_count(preview)
        needs_broad_coverage = (
            len(exact_price_sources) < self.minimum_exact_sources_before_fallback
            or comparable_price_count < self.minimum_comparable_offers_before_fallback
        )
        if needs_broad_coverage and self.generic_sources and time.monotonic() < deadline:
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

        # Reject obvious wrong models and non-product links before opening any
        # external page.  This keeps web verification bounded and prevents a
        # broad Yandex result page from consuming the product-page budget.
        rejected: list[Offer] = []
        verification_pool: list[Offer] = []
        for offer in offers:
            if (
                is_hard_mismatch(offer.exact_match)
                or offer.availability.status is AvailabilityStatus.OUT_OF_STOCK
                or (
                    bool(offer.raw_metadata.get("not_product_page"))
                    and not self._can_verify_flagged_page(offer)
                )
            ):
                rejected.append(apply_risks(offer))
            else:
                verification_pool.append(offer)

        if self.page_verifier and verification_pool:
            selected = sorted(verification_pool, key=_page_verification_priority)[: self.page_verification_limit]
            selected_ids = {offer.offer_id for offer in selected}
            verified = await verify_offer_pages(
                selected,
                self.page_verifier,
                max_concurrency=2,
                timeout=self.page_verification_timeout,
            )
            refreshed = [
                _refresh_verified_external_offer(offer, normalized_request)
                for offer in verified
            ]
            refreshed_by_id = {offer.offer_id: offer for offer in refreshed}
            verification_pool = [
                refreshed_by_id.get(offer.offer_id, offer)
                if offer.offer_id in selected_ids else offer
                for offer in verification_pool
            ]

        retained: list[Offer] = []
        for offer in verification_pool:
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
        initial_groups = analyze_product_groups(group_offers(comparable))
        price_outliers = {
            offer.offer_id
            for group in initial_groups
            for offer in group.offers
            if is_extreme_price_outlier(offer, group.market_stats)
        }
        if price_outliers:
            # A title is not enough to override a price that is wildly below
            # several direct offers of the same model.  Do not expose these
            # rows as "cheap with caveats": they are usually accessories,
            # broken listings or misleading marketplace cards.
            for offer in retained:
                if offer.offer_id in price_outliers:
                    metadata = dict(offer.raw_metadata) if isinstance(offer.raw_metadata, dict) else {}
                    metadata["price_outlier_rejected"] = True
                    rejected.append(apply_risks(replace(offer, raw_metadata=metadata)))
            retained = [offer for offer in retained if offer.offer_id not in price_outliers]
            comparable = [offer for offer in comparable if offer.offer_id not in price_outliers]
        groups = _risk_groups(analyze_product_groups(group_offers(comparable)))
        self._capture_market_history(normalized_request, groups)
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
        recommendations = select_recommendations(recommendation_groups, request=normalized_request)
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
