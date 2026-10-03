"""Orchestrate collection, bounded AI review, normalization, and ranking."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
from statistics import median
from threading import Event, Lock
import time
from typing import Any, Callable, Protocol

from .ai import MultimodalReviewer, incomplete_review
from .errors import AvitoServiceError, SearchCancelledError
from .discovery import build_discovered_listings
from .matching import matches_listing_request, matches_requested_city
from .market_cache import MarketSnapshotCache
from .models import (
    AIReview,
    AnalysisReport,
    AnalyzedListing,
    CollectionBatch,
    CostSummary,
    NormalizedListing,
    PipelineAudit,
    ReviewVerdict,
    RiskFinding,
    SearchRequest,
    Severity,
)
from .normalization import normalize_dataset
from .ranking import (
    MIN_COMPARABLE_SELLERS,
    independent_market_representatives,
    is_customer_safe,
    is_market_reference,
    merge_market_evidence,
    rank_listings,
)
from .rejection import aggregate_rejections, route_text_ai
from .risk_rules import evaluate_rules
from .verification import VerificationState, evaluate_verification


class ListingProvider(Protocol):
    def collect(self, search: SearchRequest) -> CollectionBatch | list[dict[str, Any]]: ...


ProgressCallback = Callable[[str, str, int], None]


@dataclass(frozen=True, slots=True)
class _ReviewRun:
    reviews: tuple[AIReview, ...]
    ai_cost_rub: float
    deterministic_eligible_count: int
    text_attempted_count: int
    text_completed_count: int
    text_matched_count: int
    photo_attempted_count: int
    photo_completed_count: int
    cached_count: int
    skipped_count: int
    deadline_reached: bool
    text_route_counts: tuple[tuple[str, int], ...] = ()
    bargain_funnel: dict[str, Any] | None = None
    bargain_decisions: tuple[dict[str, Any], ...] = ()
    photo_attempted_ids: tuple[str, ...] = ()


def _emit(
    callback: ProgressCallback | None,
    stage: str,
    message: str,
    percent: int,
) -> None:
    if callback is not None:
        callback(stage, message, max(0, min(100, percent)))


def _time_left(deadline_at: float | None) -> float:
    return float("inf") if deadline_at is None else deadline_at - time.monotonic()


def _trace(progress: ProgressCallback | None) -> Any:
    return getattr(progress, "trace", None)


def _raise_if_cancelled(progress: ProgressCallback | None) -> None:
    cancel_requested = getattr(progress, "cancel_requested", None)
    if callable(cancel_requested) and cancel_requested():
        raise SearchCancelledError()


def _known_token_total(reviews: tuple[AIReview, ...], field: str) -> int | None:
    values = [getattr(review, field, None) for review in reviews]
    known = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return sum(known) if known else None


class AvitoAnalysisService:
    def __init__(
        self,
        provider: ListingProvider | None,
        reviewer: MultimodalReviewer,
        *,
        ai_concurrency: int = 2,
        ai_text_batch_size: int = 6,
        ai_text_max_listings: int = 40,
        ai_max_listings: int = 10,
        ai_max_cost_rub: float = 15.0,
        ai_cache_ttl_seconds: int = 43_200,
        report_max_cost_rub: float = 50.0,
        usd_rub_rate: float = 100.0,
        minimum_report_price_rub: int = 199,
        target_cost_multiplier: float = 4.0,
        market_cache: MarketSnapshotCache | None = None,
    ) -> None:
        self.provider = provider
        self.reviewer = reviewer
        # Budget enforcement is sequential so actual AI cost is known before the next request.
        self.ai_concurrency = max(1, min(ai_concurrency, 4))
        self.ai_text_batch_size = max(2, min(ai_text_batch_size, 10))
        self.ai_text_max_listings = max(10, min(ai_text_max_listings, 200))
        self.ai_max_listings = max(1, min(ai_max_listings, 20))
        self.ai_max_cost_rub = max(0.0, ai_max_cost_rub)
        self.ai_cache_ttl_seconds = max(60, ai_cache_ttl_seconds)
        self.report_max_cost_rub = max(0.0, report_max_cost_rub)
        self.usd_rub_rate = max(1.0, usd_rub_rate)
        self.minimum_report_price_rub = max(0, minimum_report_price_rub)
        self.target_cost_multiplier = max(1.0, target_cost_multiplier)
        self._review_cache: dict[str, tuple[float, AIReview]] = {}
        self._cache_lock = Lock()
        self.market_cache = market_cache or MarketSnapshotCache()

    @staticmethod
    def _surface_provider_failure(exc: AvitoServiceError) -> bool:
        return getattr(exc, "code", "") in {
            "AI_AUTH", "AI_QUOTA", "AI_RATE_LIMIT", "AI_UNAVAILABLE", "AI_NETWORK_ERROR",
            "AI_HTTP_ERROR", "AI_INVALID_RESPONSE", "AI_PROVIDER_ERROR", "AI_TIMEOUT",
        }

    def _review_text_one(
        self, listing: NormalizedListing, request: SearchRequest
    ) -> AIReview:
        try:
            return self.reviewer.review_text(listing, request)
        except AvitoServiceError as exc:
            if self._surface_provider_failure(exc):
                raise
            return incomplete_review(listing, str(exc))
        except Exception:
            return incomplete_review(listing, "Непредвиденная ошибка текстового AI-анализа.")

    def _review_text_many(
        self,
        listings: tuple[NormalizedListing, ...],
        request: SearchRequest,
    ) -> tuple[AIReview, ...]:
        batch_method = getattr(self.reviewer, "review_text_batch", None)
        if callable(batch_method):
            try:
                reviews = tuple(batch_method(listings, request))
            except AvitoServiceError as exc:
                if self._surface_provider_failure(exc):
                    raise
                return tuple(incomplete_review(listing, str(exc)) for listing in listings)
            except Exception:
                return tuple(
                    incomplete_review(listing, "Непредвиденная ошибка пакетного AI-анализа.")
                    for listing in listings
                )
            by_id = {review.listing_id: review for review in reviews}
            if len(by_id) == len(listings):
                return tuple(
                    by_id.get(
                        listing.listing_id,
                        incomplete_review(listing, "Нейросеть пропустила объявление в пакете."),
                    )
                    for listing in listings
                )
            return tuple(
                by_id.get(
                    listing.listing_id,
                    incomplete_review(listing, "Нейросеть пропустила объявление в пакете."),
                )
                for listing in listings
            )
        return tuple(self._review_text_one(listing, request) for listing in listings)

    def _review_photos_one(self, listing: NormalizedListing, text_review: AIReview) -> AIReview:
        try:
            return self.reviewer.review_photos(listing, text_review)
        except AvitoServiceError as exc:
            if self._surface_provider_failure(exc):
                raise
            return replace(text_review, error=str(exc), verdict=ReviewVerdict.CAUTION)
        except Exception:
            return replace(
                text_review,
                error="Непредвиденная ошибка AI-анализа фотографий.",
                verdict=ReviewVerdict.CAUTION,
            )

    @staticmethod
    def _cache_key(listing: NormalizedListing, request: SearchRequest) -> str:
        evidence = {
            "review_policy": "strict-evidence-routing-no-stock-v4",
            "id": listing.listing_id,
            "title": listing.title,
            "price": listing.price,
            "description": listing.description,
            "images": listing.images,
            "parameters": listing.analysis_parameters,
            "seller": (listing.seller.identity_hash, listing.seller.name, listing.seller.kind),
            "location": listing.location,
            "address": listing.address,
            "delivery": listing.delivery,
            "costs": (listing.mandatory_fee_rub, listing.delivery_cost_rub, listing.delivery_required),
            "condition_facts": (listing.completeness, listing.parts_status, listing.repair_status,
                                listing.battery_health_percent),
            "request": {
                "query": request.query.casefold().strip(),
                "location": request.location.casefold().strip(),
                "category": request.category,
                "mode": request.mode,
                "priority": request.priority,
                "price_min": request.price_min,
                "price_max": request.price_max,
                "required_storage": request.required_storage,
                "required_sim": request.required_sim,
                "required_condition": request.required_condition,
                "attributes": request.attributes,
                "pickup_only": request.pickup_only,
            },
        }
        packed = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(packed.encode("utf-8")).hexdigest()

    def _cached_review(self, listing: NormalizedListing, request: SearchRequest) -> AIReview | None:
        key = self._cache_key(listing, request)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._review_cache.get(key)
            if cached is None:
                return None
            expires_at, review = cached
            if expires_at <= now:
                del self._review_cache[key]
                return None
        return replace(review, cost_rub=0.0, cost_estimated=False, request_count=0, cached=True)

    def _remember_review(
        self,
        listing: NormalizedListing,
        review: AIReview,
        request: SearchRequest,
    ) -> None:
        if not review.text_analyzed or review.error:
            return
        with self._cache_lock:
            self._review_cache[self._cache_key(listing, request)] = (
                time.monotonic() + self.ai_cache_ttl_seconds,
                review,
            )

    @staticmethod
    def _seller_key(listing: NormalizedListing) -> str | None:
        return listing.seller.identity_hash or None

    def _candidate_ids(
        self,
        listings: tuple[NormalizedListing, ...],
        risks: dict[str, tuple[RiskFinding, ...]],
        request: SearchRequest,
    ) -> tuple[str, ...]:
        eligible: list[NormalizedListing] = []
        for listing in listings:
            route = route_text_ai(listing, request, risks[listing.listing_id])
            if not route.needs_ai:
                continue
            eligible.append(listing)

        if request.priority == "quality":
            eligible.sort(key=lambda item: (
                -(item.seller.rating or 0.0),
                -(item.seller.review_count or 0),
                item.acquisition_price or 10**18,
            ))
        elif request.priority == "budget":
            eligible.sort(key=lambda item: (item.acquisition_price or 10**18, -int(item.seller.is_shop)))
        elif request.mode == "find":
            eligible.sort(key=lambda item: (
                -(item.seller.rating or 0.0),
                item.acquisition_price or 10**18,
                -(item.seller.review_count or 0),
            ))
        else:
            eligible.sort(key=lambda item: (item.acquisition_price or 10**18, -int(item.seller.is_shop)))
        unique: list[NormalizedListing] = []
        repeats: list[NormalizedListing] = []
        seen_sellers: set[str | None] = set()
        for listing in eligible:
            seller = self._seller_key(listing)
            if seller in seen_sellers:
                repeats.append(listing)
            else:
                seen_sellers.add(seller)
                unique.append(listing)

        def mix(values: list[NormalizedListing]) -> list[NormalizedListing]:
            companies = [item for item in values if item.seller.kind == "company"]
            private = [item for item in values if item.seller.kind == "private"]
            result: list[NormalizedListing] = []
            while companies or private:
                result.extend(companies[:3])
                del companies[:3]
                if private:
                    result.append(private.pop(0))
                if not companies and private:
                    result.extend(private)
                    private.clear()
            return result

        ordered = mix(unique) + mix(repeats)
        return tuple(item.listing_id for item in ordered[:self.ai_text_max_listings])

    def _photo_candidate_ids(
        self,
        listings: tuple[NormalizedListing, ...],
        risks: dict[str, tuple[RiskFinding, ...]],
        reviews: dict[str, AIReview],
        limit: int,
        request: SearchRequest,
        market_analyzed: tuple[AnalyzedListing, ...] = (),
        require_bargain_evidence: bool = False,
        bargain_diagnostics: list[dict[str, Any]] | None = None,
    ) -> tuple[str, ...]:
        candidates = []
        text_safety_reasons: dict[str, str] = {}
        for listing in listings:
            decision = evaluate_verification(
                listing, risks[listing.listing_id], reviews[listing.listing_id], request, stage="text",
            )
            if decision.state is VerificationState.NEEDS_EVIDENCE and decision.next_stage == "photo_ai":
                candidates.append(listing)
                text_safety_reasons[listing.listing_id] = decision.primary_reason
        if not candidates:
            return ()

        all_prices = [listing.acquisition_price for listing in candidates if listing.acquisition_price]
        overall_median = float(median(all_prices)) if all_prices else 0.0
        savings: dict[str, float] = {}
        market_request = replace(request, price_min=None, price_max=None)
        references = merge_market_evidence(tuple(
            AnalyzedListing(listing, risks[listing.listing_id], reviews[listing.listing_id])
            for listing in listings
        ), market_analyzed)
        reference_groups: dict[tuple[str, ...], list[AnalyzedListing]] = {}
        for reference in references:
            if not is_market_reference(reference, market_request):
                continue
            reference_groups.setdefault(
                reference.comparable_key(pickup_only=request.pickup_only), []
            ).append(reference)

        gate_active = bool(
            request.mode == "bargain" and market_analyzed and require_bargain_evidence
        )
        diagnostics_by_id: dict[str, dict[str, Any]] = {}
        for listing in candidates:
            candidate = AnalyzedListing(listing, risks[listing.listing_id], reviews[listing.listing_id])
            candidate_key = candidate.comparable_key(pickup_only=request.pickup_only)
            representatives = independent_market_representatives(
                candidate, reference_groups.get(candidate_key, []),
            )
            independent_prices = [
                item.listing.acquisition_price for item in representatives
                if item.listing.acquisition_price is not None
            ]
            seller_count = len(independent_prices)
            reference_price = (
                int(median(independent_prices))
                if seller_count >= MIN_COMPARABLE_SELLERS else None
            )
            delta_rub = (
                reference_price - listing.acquisition_price
                if reference_price is not None and listing.acquisition_price is not None else None
            )
            delta_percent = (
                round(delta_rub / reference_price * 100, 1)
                if delta_rub is not None and reference_price else None
            )
            if delta_percent is not None:
                savings[listing.listing_id] = delta_percent

            if not gate_active:
                status = "skipped"
                reason_code = (
                    "MARKET_REFERENCE_SNAPSHOT_UNAVAILABLE"
                    if request.mode == "bargain" and require_bargain_evidence and not market_analyzed
                    else "BARGAIN_GATE_NOT_REQUIRED"
                )
            elif listing.acquisition_price is None:
                status, reason_code = "fail", "CANDIDATE_FULL_PRICE_UNKNOWN"
            elif not self._seller_key(listing):
                status, reason_code = "fail", "CANDIDATE_SELLER_ID_UNKNOWN"
            elif seller_count < MIN_COMPARABLE_SELLERS:
                status, reason_code = "fail", "INSUFFICIENT_COMPARABLE_SELLERS"
            elif delta_rub is None or delta_rub <= 0:
                status, reason_code = "fail", "NO_POSITIVE_SAVING"
            else:
                status, reason_code = "pass", "POSITIVE_SAVING_WITH_MINIMUM_SAMPLE"

            kit_signature = hashlib.sha256(candidate_key[9].encode("utf-8")).hexdigest()
            diagnostics_by_id[listing.listing_id] = {
                "listing_id": listing.listing_id,
                "text_safety_reason": text_safety_reasons.get(listing.listing_id, ""),
                "status": status,
                "reason_code": reason_code,
                "photo_admission_status": (
                    "pass" if listing.acquisition_price is not None else "fail"
                ),
                "photo_admission_reason_code": (
                    "" if listing.acquisition_price is not None else "CANDIDATE_FULL_PRICE_UNKNOWN"
                ),
                "candidate_price": listing.price,
                "candidate_full_price": listing.acquisition_price,
                "mandatory_fee_rub": listing.mandatory_fee_rub,
                "delivery_required": listing.delivery_required,
                "delivery_cost_rub": listing.delivery_cost_rub if listing.delivery_required else None,
                "price_basis": listing.price_basis,
                "currency": listing.currency.upper(),
                "sku": candidate_key[1],
                "model_source": candidate.ai_review.identified_model or listing.model,
                "storage": candidate_key[2],
                "storage_source": candidate.ai_review.storage or listing.storage,
                "condition": candidate_key[4],
                "condition_source": candidate.ai_review.condition or listing.condition,
                "market_lane": candidate_key[0],
                "seller_kind": listing.seller.kind,
                "kit_known": bool(listing.completeness),
                "kit_class": (
                    "unknown" if candidate_key[9] == "unknown" else
                    "normalized_components" if candidate_key[9].startswith("components:") else
                    "unstructured"
                ),
                "kit_signature": kit_signature,
                "location": listing.location,
                "pickup_only": request.pickup_only,
                "delivery": listing.delivery,
                "seller_identity_available": bool(self._seller_key(listing)),
                "comparable_seller_count": seller_count,
                "reference_listing_ids": [item.listing.listing_id for item in representatives],
                "reference_prices": [item.listing.acquisition_price for item in representatives],
                "reference_price": reference_price,
                "delta_rub": delta_rub,
                "delta_percent": delta_percent,
                "minimum_comparable_sellers": MIN_COMPARABLE_SELLERS,
                "minimum_saving_rub": 1,
                "minimum_saving_percent": 0,
                "photo_ai_eligible": False,
            }

        # Market evidence controls later savings claims only. Text/photo safety
        # controls admission; missing full price remains a separate blocker.
        candidates = [
            item for item in candidates
            if diagnostics_by_id[item.listing_id]["photo_admission_status"] == "pass"
        ]

        def score(listing: NormalizedListing) -> float:
            review = reviews[listing.listing_id]
            seller = listing.seller
            value = review.confidence * 30
            value += 20 if review.verdict is ReviewVerdict.APPROVE else -10
            value += (seller.rating or 0.0) * 3 + math.log10((seller.review_count or 0) + 1) * 3
            value -= (len(review.defects) + len(review.conflicts) + len(review.price_conditions)) * 12
            if request.mode == "bargain":
                if overall_median > 0 and listing.acquisition_price:
                    price_score = (overall_median - listing.acquisition_price) / overall_median * 20
                    multiplier = {
                        "quality": 0.45,
                        "balanced": 1.0,
                        "budget": 1.8,
                    }[request.priority]
                    value += max(-15.0, min(price_score * multiplier, 25.0))
                saving_multiplier = {
                    "quality": 0.8,
                    "balanced": 2.0,
                    "budget": 3.0,
                }[request.priority]
                value += max(-25.0, min(savings.get(listing.listing_id, 0.0) * saving_multiplier, 40.0))
            return value

        candidates.sort(key=lambda item: (score(item), -(item.acquisition_price or 10**18)), reverse=True)
        unique: list[NormalizedListing] = []
        repeats: list[NormalizedListing] = []
        seen_sellers: set[str | None] = set()
        for listing in candidates:
            seller = self._seller_key(listing)
            (repeats if seller in seen_sellers else unique).append(listing)
            seen_sellers.add(seller)

        def mix(values: list[NormalizedListing]) -> list[NormalizedListing]:
            if not values:
                return []
            first = values[0]
            remaining = values[1:]
            companies = [item for item in remaining if item.seller.kind == "company"]
            private = [item for item in remaining if item.seller.kind == "private"]
            result: list[NormalizedListing] = [first]
            company_streak = 1 if first.seller.kind == "company" else 0
            while companies or private:
                if companies and (company_streak < 3 or not private):
                    result.append(companies.pop(0))
                    company_streak += 1
                elif private:
                    result.append(private.pop(0))
                    company_streak = 0
            return result

        ordered = mix(unique) + mix(repeats)
        if request.mode == "bargain" and limit > 0:
            # Seller reputation helps rank similar offers, but it must not
            # crowd the cheapest text-approved, fully specified offer out of
            # the limited photo budget. Photos and final refresh still decide
            # whether this offer can be shown to the customer.
            def specified_and_approved(item: NormalizedListing) -> bool:
                review = reviews[item.listing_id]
                analyzed = AnalyzedListing(item, risks[item.listing_id], review)
                return bool(
                    review.verdict is ReviewVerdict.APPROVE
                    and review.confidence >= 0.5
                    and not review.defects and not review.conflicts and not review.price_conditions
                    and analyzed.condition_evidence_complete()
                    and analyzed.configuration_evidence_complete()
                )

            price_leaders = [item for item in candidates if specified_and_approved(item)]
            if price_leaders:
                cheapest = min(price_leaders, key=lambda item: (item.acquisition_price or 10**18, item.listing_id))
                ordered = [cheapest, *(item for item in ordered if item.listing_id != cheapest.listing_id)]
        selected = tuple(item.listing_id for item in ordered[:max(0, limit)])
        selected_ids = set(selected)
        if bargain_diagnostics is not None:
            for listing_id, diagnostic in diagnostics_by_id.items():
                diagnostic["photo_ai_eligible"] = listing_id in selected_ids
            bargain_diagnostics.extend(
                diagnostics_by_id[listing.listing_id]
                for listing in listings if listing.listing_id in diagnostics_by_id
            )
        return selected

    def _review_with_budget(
        self,
        listings: tuple[NormalizedListing, ...],
        risks: dict[str, tuple[RiskFinding, ...]],
        request: SearchRequest,
        *,
        deadline_at: float | None = None,
        progress: ProgressCallback | None = None,
        ai_budget_rub: float | None = None,
        market_analyzed: tuple[AnalyzedListing, ...] = (),
        reuse_market_text: bool = False,
    ) -> _ReviewRun:
        budget = self.ai_max_cost_rub if ai_budget_rub is None else max(0.0, ai_budget_rub)
        route_counts = Counter(
            route_text_ai(listing, request, risks[listing.listing_id]).state.value
            for listing in listings
        )
        candidate_ids = self._candidate_ids(listings, risks, request)
        _emit(
            progress,
            "text",
            f"Отобрано {len(candidate_ids)} объявлений. Нейросеть читает описания…",
            34,
        )
        print(
            f"[ai-text] Отобрано {len(candidate_ids)} из {len(listings)} объявлений для анализа текста.",
            flush=True,
        )
        by_listing_id = {item.listing_id: item for item in listings}
        by_id = {
            item.listing_id: incomplete_review(
                item,
                "AI-анализ не выполнялся: объявление не прошло бюджетный предфильтр.",
            )
            for item in listings
        }
        ai_cost_rub = 0.0
        text_cost_rub = 0.0
        text_reviewed_count = 0
        photo_reviewed_count = 0
        cached_count = 0
        deadline_reached = False

        set_deadline = getattr(self.reviewer, "set_deadline", None)
        if callable(set_deadline):
            set_deadline(deadline_at)

        pending_ids: list[str] = []
        trace = _trace(progress)
        set_packet_telemetry = getattr(self.reviewer, "set_packet_telemetry", None)
        if callable(set_packet_telemetry):
            set_packet_telemetry(trace.record_ai_packet if trace is not None else None)
        market_by_id = {item.listing.listing_id: item for item in market_analyzed}
        for position, listing_id in enumerate(candidate_ids, 1):
            listing = by_listing_id[listing_id]
            cached = self._cached_review(listing, request)
            if cached is not None:
                by_id[listing.listing_id] = cached
                cached_count += 1
                print(f"[ai-text] {position}/{len(candidate_ids)}: результат взят из кэша.", flush=True)
                if trace is not None:
                    trace.record_cache("ai", hit=True)
                continue
            market_source = market_by_id.get(listing_id)
            if (
                reuse_market_text
                and market_source is not None
                and market_source.ai_review.text_analyzed
                and market_source.ai_review.matches_request
                and not market_source.ai_review.error
                and self._review_evidence_unchanged(market_source.listing, listing)
            ):
                by_id[listing_id] = replace(
                    market_source.ai_review,
                    cost_rub=0.0, cost_estimated=False, request_count=0, cached=True,
                )
                cached_count += 1
                if trace is not None:
                    trace.record_cache("ai", hit=True)
                continue
            if trace is not None:
                trace.record_cache("ai", hit=False)
            pending_ids.append(listing_id)

        text_budget = budget * 0.35
        minimum_pool = min(self.ai_max_listings, len(candidate_ids))
        stopped_for_provider = False
        total_batches = math.ceil(len(pending_ids) / self.ai_text_batch_size) if pending_ids else 0
        for batch_index, offset in enumerate(range(0, len(pending_ids), self.ai_text_batch_size), 1):
            _raise_if_cancelled(progress)
            batch_ids = pending_ids[offset:offset + self.ai_text_batch_size]
            batch_listings = tuple(by_listing_id[listing_id] for listing_id in batch_ids)
            if _time_left(deadline_at) <= 14:
                deadline_reached = True
                for listing in batch_listings:
                    by_id[listing.listing_id] = incomplete_review(
                        listing,
                        "Текстовый AI-анализ остановлен по лимиту времени.",
                    )
                print("[ai-text] Новые пакеты остановлены по лимиту времени.", flush=True)
                break
            average_cost = text_cost_rub / text_reviewed_count if text_reviewed_count else 0.0
            if (
                ai_cost_rub >= budget
                or (
                    text_reviewed_count >= minimum_pool
                    and (
                        text_cost_rub >= text_budget
                        or (average_cost > 0 and text_cost_rub + average_cost * len(batch_listings) > text_budget)
                    )
                )
            ):
                for listing in batch_listings:
                    by_id[listing.listing_id] = incomplete_review(
                        listing,
                        "Текстовый AI-анализ остановлен по лимиту стоимости.",
                    )
                print(f"[ai-text] Пакет {batch_index}/{total_batches}: остановлено лимитом стоимости.", flush=True)
                break
            print(
                f"[ai-text] Пакет {batch_index}/{total_batches}: "
                f"анализ {len(batch_listings)} полных описаний…",
                flush=True,
            )
            call_started = time.monotonic()
            if trace is not None:
                trace.expensive_stage_started("text_ai")
            try:
                batch_reviews = self._review_text_many(batch_listings, request)
            except AvitoServiceError as exc:
                # Preserve sources for diagnostics. Only a reservation explicitly
                # retained by the reviewer is accounted; a pre-request budget
                # block costs zero and must not consume the whole search allowance.
                batch_reviews = tuple(incomplete_review(listing, str(exc)) for listing in batch_listings)
                diagnostics = getattr(exc, "diagnostics", {}) or {}
                accounted = max(0.0, float(diagnostics.get("accounted_cost_rub") or 0.0))
                batch_reviews = (replace(batch_reviews[0],
                    cost_rub=accounted,
                    cost_estimated=bool(diagnostics.get("cost_estimated") and accounted > 0)),
                    *batch_reviews[1:])
            if trace is not None:
                call_count = sum(max(0, review.request_count) for review in batch_reviews)
                actual_cost = sum(
                    max(0.0, review.cost_rub) for review in batch_reviews if not review.cost_estimated
                )
                estimated_cost = sum(
                    max(0.0, review.cost_rub) for review in batch_reviews if review.cost_estimated
                )
                trace.record_ai(
                    "text",
                    model=getattr(getattr(self.reviewer, "config", None), "ai_model", type(self.reviewer).__name__),
                    calls=call_count,
                    listings=len(batch_listings),
                    duration_ms=round((time.monotonic() - call_started) * 1000),
                    cost_rub=sum(max(0.0, review.cost_rub) for review in batch_reviews),
                    cost_estimated=any(review.cost_estimated for review in batch_reviews),
                    input_tokens=_known_token_total(batch_reviews, "input_tokens"),
                    output_tokens=_known_token_total(batch_reviews, "output_tokens"),
                    errors=sum(bool(review.error) for review in batch_reviews),
                    retries=max(0, call_count - 1),
                    actual_cost_rub=actual_cost,
                    estimated_cost_rub=estimated_cost,
                )
            batch_cost = sum(max(0.0, review.cost_rub) for review in batch_reviews)
            text_reviewed_count += len(batch_listings)
            text_cost_rub += batch_cost
            ai_cost_rub += batch_cost
            for listing, review in zip(batch_listings, batch_reviews, strict=True):
                by_id[listing.listing_id] = review
                self._remember_review(listing, review, request)
            completed = sum(review.text_analyzed for review in batch_reviews)
            percent = 34 + round(28 * batch_index / max(1, total_batches))
            _emit(
                progress,
                "text",
                f"Проверено описаний: {text_reviewed_count} из {len(candidate_ids)}.",
                percent,
            )
            print(
                f"[ai-text] Пакет {batch_index}/{total_batches}: готово {completed}/"
                f"{len(batch_listings)}; расход {batch_cost:.2f} руб.",
                flush=True,
            )
            if completed == 0:
                stopped_for_provider = True
                remaining_ids = pending_ids[offset + len(batch_ids):]
                for listing_id in remaining_ids:
                    listing = by_listing_id[listing_id]
                    by_id[listing_id] = incomplete_review(
                        listing,
                        "Текстовый AI-анализ остановлен после ошибки провайдера.",
                    )
                print(
                    f"[ai-text] Провайдер не вернул пригодный пакет; "
                    f"остальные {len(remaining_ids)} запросов не отправлены.",
                    flush=True,
                )
                break

        if callable(set_packet_telemetry):
            set_packet_telemetry(None)

        if stopped_for_provider:
            print("[ai-text] Используйте другую активную модель, если сбой повторится.", flush=True)

        _raise_if_cancelled(progress)

        # Try a few reserve candidates, but stop as soon as the requested 3/5
        # customer-ready results are complete. This keeps vision fast and cheap
        # while allowing failed/rejected photo reviews to be replaced.
        # Preparing reserve IDs is free. Keep every allowed reserve available and
        # stop the loop immediately when the safe quota is reached.
        photo_attempt_limit = min(
            self.ai_max_listings,
            max(request.desired_results + 2, request.desired_results * 2),
        )
        viable_cached_count = sum(
            review.cached
            and is_customer_safe(
                AnalyzedListing(
                    by_listing_id[listing_id],
                    risks[listing_id],
                    review,
                ),
                request,
                require_freshness=False,
            )
            for listing_id, review in by_id.items()
            if listing_id in candidate_ids
        )
        available_photo_slots = max(0, photo_attempt_limit - viable_cached_count)
        bargain_diagnostics: list[dict[str, Any]] = []
        photo_ids = self._photo_candidate_ids(
            listings,
            risks,
            by_id,
            available_photo_slots,
            request,
            market_analyzed,
            require_bargain_evidence=reuse_market_text,
            bargain_diagnostics=bargain_diagnostics,
        )
        print(
            f"[ai-photo] Подготовлено до {len(photo_ids)} финалистов с резервом; "
            "фотографии анализируются до набора выбранной квоты.",
            flush=True,
        )
        photo_cost_rub = 0.0
        attempted_photo_ids: set[str] = set()
        bargain_baseline = 0
        if request.mode == "bargain" and reuse_market_text and viable_cached_count:
            bargain_baseline = viable_cached_count

        def completed_quota() -> int:
            current_evidence = tuple(AnalyzedListing(by_listing_id[listing_id], risks[listing_id], review)
                                     for listing_id, review in by_id.items())
            completed = tuple(item for item in current_evidence if item.listing.listing_id in candidate_ids)
            if request.mode == "bargain":
                if reuse_market_text:
                    # The requested count is for safe exact matches; comparable
                    # evidence controls savings labels, not admission.
                    return bargain_baseline + sum(
                        item.listing.listing_id in attempted_photo_ids
                        and is_customer_safe(item, request, require_freshness=False)
                        for item in completed
                    )
                return sum(is_customer_safe(item, request, require_freshness=False) for item in completed)
            return sum(is_customer_safe(item, request, require_freshness=False) for item in completed)

        completed_target = completed_quota()
        cursor = 0
        photo_provider_failed = Event()

        def run_photo(listing_id: str) -> AIReview:
            worker_deadline = getattr(self.reviewer, "set_deadline", None)
            if callable(worker_deadline):
                worker_deadline(deadline_at)
            try:
                return self._review_photos_one(by_listing_id[listing_id], by_id[listing_id])
            except AvitoServiceError as exc:
                photo_provider_failed.set()
                text_review = by_id[listing_id]
                return replace(text_review, error=str(exc), verdict=ReviewVerdict.CAUTION,
                    cost_rub=text_review.cost_rub + remaining_budget / batch_size,
                    cost_estimated=True)

        while cursor < len(photo_ids) and completed_target < request.desired_results:
            _raise_if_cancelled(progress)
            if _time_left(deadline_at) <= 2:
                deadline_reached = True
                print("[ai-photo] Новые запросы остановлены по лимиту времени.", flush=True)
                break
            average_photo_cost = photo_cost_rub / photo_reviewed_count if photo_reviewed_count else 0.0
            remaining_budget = max(0.0, budget - ai_cost_rub)
            # The first vision request establishes the real provider cost. Until
            # then launch only one, otherwise concurrency could overspend at once.
            affordable = (
                1 if photo_reviewed_count == 0 and remaining_budget > 0
                else max(0, int(remaining_budget / max(average_photo_cost, 0.01)))
            )
            needed = max(1, request.desired_results - completed_target)
            batch_size = min(
                self.ai_concurrency,
                needed,
                len(photo_ids) - cursor,
                affordable,
            )
            if batch_size <= 0:
                print("[ai-photo] Новые запросы остановлены по лимиту стоимости.", flush=True)
                break
            batch_ids = photo_ids[cursor:cursor + batch_size]
            cursor += batch_size
            attempted_photo_ids.update(batch_ids)
            for listing_id in batch_ids:
                listing = by_listing_id[listing_id]
                print(
                    f"[ai-photo] Анализ всех {len(listing.images)} фото объявления {listing_id}…",
                    flush=True,
                )
            call_started = time.monotonic()
            if trace is not None:
                trace.expensive_stage_started("photo_ai")
            if batch_size == 1:
                batch_reviews = {batch_ids[0]: run_photo(batch_ids[0])}
            else:
                batch_reviews: dict[str, AIReview] = {}
                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    futures = {executor.submit(run_photo, listing_id): listing_id for listing_id in batch_ids}
                    for future in as_completed(futures):
                        listing_id = futures[future]
                        try:
                            batch_reviews[listing_id] = future.result()
                        except AvitoServiceError as exc:
                            if self._surface_provider_failure(exc):
                                raise
                            batch_reviews[listing_id] = replace(
                                by_id[listing_id],
                                error=str(exc),
                                verdict=ReviewVerdict.CAUTION,
                            )
                        except Exception:
                            batch_reviews[listing_id] = replace(
                                by_id[listing_id],
                                error="Непредвиденная ошибка AI-анализа фотографий.",
                                verdict=ReviewVerdict.CAUTION,
                            )
            if trace is not None:
                photo_costs = []
                photo_calls = 0
                photo_retries = 0
                input_tokens = 0
                output_tokens = 0
                input_known = False
                output_known = False
                for listing_id in batch_ids:
                    before = by_id[listing_id]
                    after = batch_reviews[listing_id]
                    photo_costs.append(max(0.0, after.cost_rub - before.cost_rub))
                    item_calls = max(0, after.request_count - before.request_count)
                    photo_calls += item_calls
                    photo_retries += max(0, item_calls - 1)
                    if after.input_tokens is not None or before.input_tokens is not None:
                        input_known = True
                        input_tokens += max(0, int(after.input_tokens or 0) - int(before.input_tokens or 0))
                    if after.output_tokens is not None or before.output_tokens is not None:
                        output_known = True
                        output_tokens += max(0, int(after.output_tokens or 0) - int(before.output_tokens or 0))
                trace.record_ai(
                    "photo",
                    model=getattr(getattr(self.reviewer, "config", None), "ai_model", type(self.reviewer).__name__),
                    calls=photo_calls,
                    listings=len(batch_ids),
                    duration_ms=round((time.monotonic() - call_started) * 1000),
                    cost_rub=sum(photo_costs),
                    cost_estimated=any(batch_reviews[item].cost_estimated for item in batch_ids),
                    input_tokens=input_tokens if input_known else None,
                    output_tokens=output_tokens if output_known else None,
                    errors=sum(bool(batch_reviews[item].error) for item in batch_ids),
                    retries=photo_retries,
                )
            for listing_id in batch_ids:
                listing = by_listing_id[listing_id]
                text_review = by_id[listing_id]
                review = batch_reviews[listing_id]
                photo_reviewed_count += 1
                photo_cost = max(0.0, review.cost_rub - text_review.cost_rub)
                photo_cost_rub += photo_cost
                ai_cost_rub += photo_cost
                by_id[listing_id] = review
                self._remember_review(listing, review, request)
                state = "готово" if review.is_complete_for(listing) else "неполный ответ"
                print(
                    f"[ai-photo] {photo_reviewed_count}/{len(photo_ids)}: {state}; "
                    f"охват={len(review.photo_coverage)}/{len(listing.images)}, "
                    f"расход этапа {photo_cost:.2f} руб.",
                    flush=True,
                )
            completed_target = completed_quota()
            _emit(
                progress,
                "photo",
                f"Проверено фото финалистов: {photo_reviewed_count}. "
                f"Готово вариантов: {completed_target} из {request.desired_results}.",
                68 + round(25 * min(1.0, completed_target / max(1, request.desired_results))),
            )
            if photo_provider_failed.is_set():
                break

        if completed_target >= request.desired_results:
            print(
                f"[ai-photo] Набрано {completed_target} из {request.desired_results}; "
                "оставшиеся фото не отправляются.",
                flush=True,
            )

        for listing_id in candidate_ids:
            listing = by_listing_id[listing_id]
            review = by_id[listing_id]
            if (
                review.text_analyzed
                and not review.is_complete_for(listing)
                and listing_id in photo_ids
                and listing_id not in attempted_photo_ids
            ):
                by_id[listing_id] = replace(
                    review,
                    error="Фотографии не анализировались: вариант не вошёл в финальный шорт-лист.",
                )

        skipped_count = max(0, len(listings) - text_reviewed_count - cached_count)
        for diagnostic in bargain_diagnostics:
            listing_id = str(diagnostic["listing_id"])
            review = by_id[listing_id]
            diagnostic["photo_ai_attempted"] = listing_id in attempted_photo_ids
            diagnostic["photo_ai_completed"] = review.is_complete_for(by_listing_id[listing_id])
            diagnostic["final_revalidated"] = False
            diagnostic["final_status"] = "pending"
            diagnostic["final_reason_code"] = "FINAL_REVALIDATION_NOT_RUN"
        bargain_funnel = {
            "text_safe": len(bargain_diagnostics),
            "bargain_evaluated": sum(
                item["status"] in {"pass", "fail"} for item in bargain_diagnostics
            ),
            "bargain_pass": sum(item["status"] == "pass" for item in bargain_diagnostics),
            "bargain_fail": sum(item["status"] == "fail" for item in bargain_diagnostics),
            "bargain_fail_reasons": dict(Counter(
                item["reason_code"] for item in bargain_diagnostics if item["status"] == "fail"
            )),
            "photo_ai_eligible": sum(
                bool(item["photo_ai_eligible"]) for item in bargain_diagnostics
            ),
            "photo_ai_attempted": sum(bool(item["photo_ai_attempted"]) for item in bargain_diagnostics),
            "photo_ai_completed": sum(bool(item["photo_ai_completed"]) for item in bargain_diagnostics),
        }
        return _ReviewRun(
            reviews=tuple(by_id[item.listing_id] for item in listings),
            ai_cost_rub=ai_cost_rub,
            deterministic_eligible_count=len(candidate_ids),
            text_attempted_count=text_reviewed_count,
            text_completed_count=sum(review.text_analyzed for review in by_id.values()),
            text_matched_count=sum(
                review.text_analyzed and review.matches_request for review in by_id.values()
            ),
            photo_attempted_count=photo_reviewed_count,
            photo_completed_count=sum(
                review.is_complete_for(by_listing_id[listing_id])
                for listing_id, review in by_id.items()
            ),
            cached_count=cached_count,
            skipped_count=skipped_count,
            deadline_reached=deadline_reached,
            text_route_counts=tuple(sorted(route_counts.items())),
            bargain_funnel=bargain_funnel,
            bargain_decisions=tuple(bargain_diagnostics),
            photo_attempted_ids=tuple(sorted(attempted_photo_ids)),
        )

    def _cost_summary(
        self,
        *,
        apify_cost_usd: float,
        apify_cost_estimated: bool,
        ai_cost_rub: float,
        text_reviewed_count: int,
        photo_reviewed_count: int,
        cached_count: int,
        skipped_count: int,
        ai_cost_estimated: bool = False,
    ) -> CostSummary:
        total_rub = max(0.0, apify_cost_usd) * self.usd_rub_rate + max(0.0, ai_cost_rub)
        suggested = max(
            self.minimum_report_price_rub,
            math.ceil(total_rub * self.target_cost_multiplier / 10) * 10,
        )
        return CostSummary(
            apify_cost_usd=max(0.0, apify_cost_usd),
            apify_cost_estimated=apify_cost_estimated,
            ai_cost_rub=max(0.0, ai_cost_rub),
            ai_cost_estimated=ai_cost_estimated,
            estimated_total_rub=total_rub,
            budget_rub=self.report_max_cost_rub,
            within_budget=total_rub <= self.report_max_cost_rub,
            ai_reviewed_count=text_reviewed_count,
            ai_text_reviewed_count=text_reviewed_count,
            ai_photo_reviewed_count=photo_reviewed_count,
            ai_cached_count=cached_count,
            ai_skipped_count=skipped_count,
            suggested_min_price_rub=suggested,
        )

    def _review_market(
        self, listings: tuple[NormalizedListing, ...], request: SearchRequest,
        *, budget_rub: float, deadline_at: float | None,
        progress: ProgressCallback | None,
    ) -> tuple[tuple[AnalyzedListing, ...], float, int]:
        """Screen a price-independent reference sample without paying for photos."""
        broad = replace(request, price_min=None, price_max=None)
        eligible: list[NormalizedListing] = []
        seen: set[str] = set()
        for listing in listings:
            findings = evaluate_rules(listing)
            identity = self._seller_key(listing)
            if (identity and identity not in seen and listing.is_recently_collected()
                and not any(risk.severity is not Severity.INFO for risk in findings)
                and matches_listing_request(listing, broad)):
                seen.add(identity)
                eligible.append(listing)
        # Preserve discovery order; never select reference prices by cheapness.
        eligible = eligible[:max(20, self.ai_text_max_listings)]
        results: list[AnalyzedListing] = []
        cost = 0.0
        attempted = 0
        set_deadline = getattr(self.reviewer, "set_deadline", None)
        if callable(set_deadline):
            set_deadline(deadline_at)
        set_packet_telemetry = getattr(self.reviewer, "set_packet_telemetry", None)
        trace = _trace(progress)
        if callable(set_packet_telemetry):
            set_packet_telemetry(trace.record_ai_packet if trace is not None else None)
        for offset in range(0, len(eligible), self.ai_text_batch_size):
            _raise_if_cancelled(progress)
            batch = tuple(eligible[offset:offset + self.ai_text_batch_size])
            predicted = cost / attempted * len(batch) if attempted else 0.0
            if cost >= budget_rub or cost + predicted > budget_rub or _time_left(deadline_at) < 20:
                break
            _emit(progress, "market", f"Проверяем сопоставимость рынка: {offset} из {len(eligible)}…", 25)
            call_started = time.monotonic()
            if trace is not None:
                trace.expensive_stage_started("market_text_ai")
            reviews = self._review_text_many(batch, broad)
            if trace is not None:
                call_count = sum(max(0, review.request_count) for review in reviews)
                trace.record_ai(
                    "text",
                    model=getattr(getattr(self.reviewer, "config", None), "ai_model", type(self.reviewer).__name__),
                    calls=call_count,
                    listings=len(batch),
                    duration_ms=round((time.monotonic() - call_started) * 1000),
                    cost_rub=sum(max(0.0, review.cost_rub) for review in reviews),
                    cost_estimated=any(review.cost_estimated for review in reviews),
                    input_tokens=_known_token_total(reviews, "input_tokens"),
                    output_tokens=_known_token_total(reviews, "output_tokens"),
                    errors=sum(bool(review.error) for review in reviews),
                    retries=max(0, call_count - 1),
                    actual_cost_rub=sum(
                        max(0.0, review.cost_rub) for review in reviews if not review.cost_estimated
                    ),
                    estimated_cost_rub=sum(
                        max(0.0, review.cost_rub) for review in reviews if review.cost_estimated
                    ),
                )
            cost += sum(max(0.0, review.cost_rub) for review in reviews)
            attempted += len(batch)
            for listing, review in zip(batch, reviews, strict=True):
                results.append(AnalyzedListing(listing, evaluate_rules(listing), review))
            if not any(review.text_analyzed for review in reviews):
                break
        if callable(set_packet_telemetry):
            set_packet_telemetry(None)
        return tuple(results), cost, attempted

    @staticmethod
    def _review_evidence_unchanged(before: NormalizedListing, after: NormalizedListing) -> bool:
        # A changed price is re-ranked; changed product evidence needs a new AI pass.
        same_seller = (
            before.seller.identity_hash == after.seller.identity_hash
            and before.seller.kind == after.seller.kind
        ) if before.seller.identity_hash and after.seller.identity_hash else before.seller == after.seller
        return same_seller and before.analysis_parameters == after.analysis_parameters and all(getattr(before, name) == getattr(after, name) for name in (
            "listing_id", "title", "description", "images", "currency",
            "location", "address", "delivery", "completeness", "repair_status", "parts_status", "battery_health_percent",
            "mandatory_fee_rub", "delivery_cost_rub", "delivery_required",
        ))

    @staticmethod
    def _invalidate_finalist_verification(
        analyzed: tuple[AnalyzedListing, ...],
        selected_ids: set[str],
        *,
        failed: bool,
    ) -> tuple[AnalyzedListing, ...]:
        """Never let an earlier observation stand in for the required final refresh."""
        status = "failed" if failed else "unverified"
        return tuple(
            replace(item, listing=replace(item.listing, verified_at="", verification_status=status))
            if item.listing.listing_id in selected_ids else item
            for item in analyzed
        )

    def _verify_finalists(
        self, analyzed: tuple[AnalyzedListing, ...], request: SearchRequest,
        *, deadline_at: float | None, allowance_usd: float,
        progress: ProgressCallback | None,
        market_analyzed: tuple[AnalyzedListing, ...] = (),
    ) -> tuple[tuple[AnalyzedListing, ...], float, bool]:
        planned = rank_listings(analyzed, request, market_analyzed=market_analyzed, require_freshness=False)
        selected = tuple(item.analyzed.listing for item in planned
                         if is_customer_safe(item.analyzed, request, require_freshness=False)
                         and evaluate_verification(
                             item.analyzed.listing, item.analyzed.deterministic_risks,
                             item.analyzed.ai_review, request, stage="final",
                         ).state is VerificationState.PASS)[:request.desired_results]
        refresh = getattr(self.provider, "refresh", None)
        trace = _trace(progress)
        if not selected:
            return analyzed, 0.0, False
        selected_ids = {item.listing_id for item in selected}
        requested_ids = tuple(item.listing_id for item in selected)
        if not callable(refresh) or allowance_usd <= 0 or _time_left(deadline_at) < 5:
            if trace is not None:
                trace.record_final_refresh(requested_ids)
            return self._invalidate_finalist_verification(
                analyzed, selected_ids, failed=False,
            ), 0.0, False
        _emit(progress, "refresh", "Обновляем цену и активность объявлений финалистов…", 94)
        provider_kwargs: dict[str, Any] = {
            "deadline_at": deadline_at,
            "max_charge_usd": allowance_usd,
        }
        if getattr(self.provider, "supports_telemetry", False) and trace is not None:
            provider_kwargs["telemetry"] = trace.record_apify
            provider_kwargs["purpose"] = "final_revalidation"
        cancel_requested = getattr(progress, "cancel_requested", None)
        if getattr(self.provider, "supports_cancellation", False) and callable(cancel_requested):
            provider_kwargs["cancel_requested"] = cancel_requested
        try:
            batch = refresh(selected, **provider_kwargs)
        except SearchCancelledError:
            raise
        except AvitoServiceError:
            # A failed paid refresh may still incur a charge; reserve its allowance.
            if trace is not None:
                trace.record_final_refresh(requested_ids, outcomes={key: "provider_error" for key in requested_ids})
            return self._invalidate_finalist_verification(
                analyzed, selected_ids, failed=True,
            ), allowance_usd, True
        if not isinstance(batch, CollectionBatch):
            if trace is not None:
                trace.record_final_refresh(requested_ids, outcomes={key: "provider_error" for key in requested_ids})
            return self._invalidate_finalist_verification(
                analyzed, selected_ids, failed=True,
            ), allowance_usd, True
        fresh_by_id = {item.listing_id: item for item in normalize_dataset(batch.items)}
        result = []
        outcomes: dict[str, str] = {}
        for item in analyzed:
            if item.listing.listing_id not in selected_ids:
                result.append(item)
                continue
            fresh = fresh_by_id.get(item.listing.listing_id)
            if fresh is None:
                outcomes[item.listing.listing_id] = "missing"
                result.append(replace(item, listing=replace(item.listing, verification_status="failed")))
                continue
            if fresh.url != item.listing.url or not fresh.is_recently_collected(60):
                outcomes[item.listing.listing_id] = "url_changed" if fresh.url != item.listing.url else "stale"
                result.append(replace(item, listing=replace(item.listing, verification_status="failed")))
                continue
            review = item.ai_review
            evidence_changed = not self._review_evidence_unchanged(item.listing, fresh)
            if evidence_changed:
                review = replace(review, verdict=ReviewVerdict.CAUTION,
                                 conflicts=(*review.conflicts, "Данные объявления изменились при повторной проверке."))
            findings = evaluate_rules(fresh)
            positive = not any(risk.severity is not Severity.INFO for risk in findings)
            outcomes[item.listing.listing_id] = (
                "evidence_changed" if evidence_changed else "verified" if positive else "rule_failed"
            )
            fresh = replace(fresh, verified_at=fresh.collected_at if positive else "",
                            verification_status="verified" if positive else "failed")
            result.append(AnalyzedListing(fresh, findings, review))
        if trace is not None:
            trace.record_final_refresh(requested_ids, returned_ids=tuple(fresh_by_id), outcomes=outcomes)
        return tuple(result), batch.apify_cost_usd, batch.apify_cost_estimated

    def analyze_dataset(
        self,
        raw_items: Any,
        request: SearchRequest,
        *,
        apify_cost_usd: float = 0.0,
        apify_cost_estimated: bool = False,
        collection_was_capped: bool = False,
        deadline_at: float | None = None,
        progress: ProgressCallback | None = None,
        started_at: float | None = None,
        market_analyzed: tuple[AnalyzedListing, ...] = (),
        market_ai_cost_rub: float = 0.0,
        market_text_count: int = 0,
        refresh_finalists: bool = False,
        collection_budget_remaining_usd: float = 0.0,
        discovery_sources: tuple[AnalyzedListing, ...] = (),
        collection_metrics: dict[str, object] | None = None,
        reuse_market_text: bool = False,
    ) -> AnalysisReport:
        run_started_at = started_at or time.monotonic()
        remaining_ai_budget = min(
            max(0.0, self.ai_max_cost_rub - market_ai_cost_rub),
            max(0.0, self.report_max_cost_rub - apify_cost_usd * self.usd_rub_rate
                - collection_budget_remaining_usd * self.usd_rub_rate - market_ai_cost_rub),
        )
        begin_budget = getattr(self.reviewer, "begin_budget", None)
        if callable(begin_budget):
            begin_budget(remaining_ai_budget)
        _emit(progress, "prepare", "Проверяем объявления и отсеиваем явные риски…", 28)
        listings = normalize_dataset(raw_items)
        # Market collection is already paid for. Relevant rows may still go
        # through the complete candidate/photo/refresh pipeline, never straight
        # to a client card or a second paid collection just to fill a quota.
        merged = {listing.listing_id: listing for listing in listings}
        for source in discovery_sources:
            listing = source.listing
            if (listing.listing_id not in merged and listing.is_recently_collected()
                    and matches_listing_request(listing, request)):
                merged[listing.listing_id] = listing
        listings = tuple(merged.values())
        collection_metrics = collection_metrics or {
            "raw_collected": len(listings), "deduplicated": len(listings),
            "correct_city": sum(matches_requested_city(item, request, final=True) for item in listings),
            "request_family_matched": len(listings), "requested_target": request.max_results,
            "target_filled": len(listings) >= request.max_results, "stop_reason": "SINGLE_BATCH",
        }
        risks = {listing.listing_id: evaluate_rules(listing) for listing in listings}
        review_run = self._review_with_budget(
            listings,
            risks,
            request,
            deadline_at=deadline_at,
            progress=progress,
            ai_budget_rub=remaining_ai_budget,
            market_analyzed=market_analyzed,
            reuse_market_text=reuse_market_text,
        )
        analyzed = tuple(
            AnalyzedListing(listing=listing, deterministic_risks=risks[listing.listing_id], ai_review=review)
            for listing, review in zip(listings, review_run.reviews, strict=True)
        )
        if refresh_finalists:
            analyzed, refresh_cost, refresh_estimated = self._verify_finalists(
                analyzed, request, deadline_at=deadline_at,
                allowance_usd=collection_budget_remaining_usd, progress=progress,
                market_analyzed=market_analyzed,
            )
            apify_cost_usd += refresh_cost
            apify_cost_estimated = apify_cost_estimated or refresh_estimated
        _emit(progress, "ranking", "Сравниваем одинаковые товары и собираем итог…", 97)
        recommendations = rank_listings(analyzed, request, market_analyzed=market_analyzed)
        safe_count = sum(is_customer_safe(item.analyzed, request) for item in recommendations)
        recommendation_by_id = {item.analyzed.listing.listing_id: item for item in recommendations}
        analyzed_by_id = {item.listing.listing_id: item for item in analyzed}
        final_counts: Counter[str] = Counter()
        for decision in review_run.bargain_decisions:
            listing_id = str(decision["listing_id"])
            item = analyzed_by_id[listing_id]
            photo_complete = item.ai_review.is_complete_for(item.listing)
            final_check = evaluate_verification(
                item.listing, item.deterministic_risks, item.ai_review, request, stage="final",
            )
            freshly_verified = item.listing.is_freshly_verified()
            decision["photo_ai_attempted"] = listing_id in review_run.photo_attempted_ids
            decision["photo_ai_completed"] = photo_complete
            decision["final_revalidated"] = freshly_verified
            if not decision.get("photo_ai_eligible"):
                final_status, final_reason = "pending", "PHOTO_AI_NOT_SCHEDULED"
            elif not photo_complete:
                if listing_id in review_run.photo_attempted_ids:
                    final_status = "rejected"
                    final_reason = final_check.primary_reason or "PHOTO_EVIDENCE_UNRESOLVED"
                else:
                    final_status, final_reason = "pending", "PHOTO_AI_NOT_RUN"
            elif not freshly_verified:
                if refresh_finalists and item.listing.verification_status == "failed":
                    final_status, final_reason = "rejected", "FINAL_REVALIDATION_FAILED"
                else:
                    final_status, final_reason = "pending", "FINAL_REVALIDATION_NOT_RUN"
            elif final_check.state is not VerificationState.PASS or not is_customer_safe(item, request):
                final_status = "rejected"
                final_reason = final_check.primary_reason or "FINAL_SAFETY_REJECTED"
            else:
                recommendation = recommendation_by_id.get(listing_id)
                if recommendation is None:
                    final_status, final_reason = "rejected", "FINAL_RANKING_REJECTED"
                elif recommendation.below_comparables:
                    final_status, final_reason = "bargain", "POSITIVE_SAVING_WITH_MINIMUM_SAMPLE"
                else:
                    final_status, final_reason = "exact_match", "SAVINGS_NOT_CONFIRMED"
            decision["final_status"] = final_status
            decision["final_reason_code"] = final_reason
            final_counts[final_status] += 1
        bargain_funnel = dict(review_run.bargain_funnel or {})
        bargain_funnel.update({
            "final_revalidated": sum(bool(item.get("final_revalidated"))
                                      for item in review_run.bargain_decisions),
            "final": final_counts["bargain"] + final_counts["exact_match"] + final_counts["rejected"],
            "bargain": final_counts["bargain"],
            "exact_match": final_counts["exact_match"],
            "rejected": final_counts["rejected"],
            "pending": final_counts["pending"],
        })
        discovered = build_discovered_listings(analyzed, request, recommendations)
        if min(safe_count, request.desired_results) + len(discovered) < request.desired_results and discovery_sources:
            discovered = build_discovered_listings((*analyzed, *discovery_sources), request, recommendations)
        costs = self._cost_summary(
            apify_cost_usd=apify_cost_usd,
            apify_cost_estimated=apify_cost_estimated,
            ai_cost_rub=review_run.ai_cost_rub + market_ai_cost_rub,
            ai_cost_estimated=any(review.cost_estimated for review in review_run.reviews)
                or any(item.ai_review.cost_estimated for item in (market_analyzed or ())),
            text_reviewed_count=review_run.text_attempted_count + market_text_count,
            photo_reviewed_count=review_run.photo_attempted_count,
            cached_count=review_run.cached_count,
            skipped_count=review_run.skipped_count,
        )
        warnings: list[str] = []
        admin_warnings: list[str] = []
        if not listings:
            warnings.append("Zen не вернул объявления.")
        if collection_was_capped:
            admin_warnings.append("Количество объявлений уменьшено автоматически, чтобы не превысить бюджет.")
        if not bool(collection_metrics.get("target_filled")):
            admin_warnings.append(
                "Целевое число релевантных объявлений не набрано: "
                f"{collection_metrics.get('request_family_matched', len(listings))}/"
                f"{collection_metrics.get('requested_target', request.max_results)}; "
                f"остановка={collection_metrics.get('stop_reason', 'UNKNOWN')}."
            )
        if review_run.skipped_count:
            admin_warnings.append("Не завершившие текстовый анализ объявления не участвуют в оценке рынка.")
        if not refresh_finalists:
            admin_warnings.append("Импорт — предпросмотр анализа. Цена и активность объявления не обновлены.")
        complete_count = sum(item.ai_review.is_complete_for(item.listing) for item in analyzed)
        eligible_ids = set(self._candidate_ids(listings, risks, request))
        pending_photos = set(self._photo_candidate_ids(
            tuple(item.listing for item in analyzed),
            {item.listing.listing_id: item.deterministic_risks for item in analyzed},
            {item.listing.listing_id: item.ai_review for item in analyzed},
            len(analyzed), request, market_analyzed,
        ))
        incomplete_candidates = [
            item for item in analyzed
            if item.listing.listing_id in eligible_ids
            and not item.ai_review.is_complete_for(item.listing)
            and (not item.ai_review.text_analyzed or item.ai_review.error
                 or item.listing.listing_id in pending_photos)
        ]
        if incomplete_candidates:
            warnings.append("Объявления с незавершённым AI-анализом не включены в результат.")
            admin_warnings.append(
                "До 10 некритичных кандидатов с неполным AI-анализом показаны администратору как CAUTION."
            )
        if not costs.within_budget:
            admin_warnings.append(
                "Расчётная себестоимость превысила установленный лимит; новые AI-запросы остановлены."
            )
        if review_run.ai_cost_rub > self.ai_max_cost_rub:
            admin_warnings.append(
                "Фактическая стоимость последнего AI-запроса превысила резерв этапа; "
                "последующие AI-запросы остановлены."
            )
        empty_reason = ""
        if not safe_count:
            # Explain a proven evidence gap even when it also prevented photo
            # completion. Do not attribute rejected/mismatched cards, or a mix
            # containing a condition-complete candidate, to this one cause.
            text_matches = [
                item for item in analyzed
                if item.listing.listing_id in eligible_ids
                and item.ai_review.text_analyzed and not item.ai_review.error
                and item.ai_review.matches_request
                and item.ai_review.verdict is ReviewVerdict.APPROVE
                and item.ai_review.confidence >= 0.5
                and not (item.ai_review.conflicts or item.ai_review.defects or item.ai_review.price_conditions)
                and not any(risk.severity is not Severity.INFO for risk in item.deterministic_risks)
                and matches_listing_request(
                    item.listing, request, identified_model=item.ai_review.identified_model,
                    storage=item.ai_review.storage, sim_variant=item.ai_review.sim_variant,
                    condition=item.ai_review.condition, final=True,
                )
            ]
            condition_gaps: dict[str, int] = {}
            if text_matches and all(not item.condition_evidence_complete() for item in text_matches):
                for item in text_matches:
                    missing = []
                    listing = item.listing
                    if item.market_lane() == "unknown":
                        missing.append("состояние")
                    if not listing.completeness or listing.completeness.casefold() in {"unknown", "неизвестно", "не указано"}:
                        missing.append("комплект")
                    for field in missing:
                        condition_gaps[field] = condition_gaps.get(field, 0) + 1
            if not listings:
                empty_reason = "Источник не вернул объявлений по этому запросу."
            elif not eligible_ids:
                empty_reason = "Нет объявлений, соответствующих модели и обязательным условиям запроса."
            elif condition_gaps:
                detail = ", ".join(f"{field} — {count}" for field, count in condition_gaps.items())
                empty_reason = (
                    "Не хватает сведений продавца о состоянии или комплекте. "
                    f"Количество подходящих по тексту объявлений с пробелами: {detail}. "
                    "Недостаток сведений не означает, что товар неисправен."
                )
            elif incomplete_candidates:
                empty_reason = "Проверка подходящих объявлений не завершена. Непроверенные варианты не показаны."
            elif request.mode == "bargain":
                empty_reason = "Нет точных совпадений с завершёнными проверками и подтверждённой актуальной ценой."
            else:
                empty_reason = "Объявления не прошли проверку соответствия, состояния или актуальности цены."
            # Keep detailed reasons in owner diagnostics, not three repeated
            # empty-result warnings that suggest lowering verification quality.
            admin_warnings.extend(warnings)
            warnings = [empty_reason]
            if incomplete_candidates:
                admin_warnings.append("Объявления с незавершённым AI-анализом исключены из результата.")
        deadline_reached = review_run.deadline_reached or _time_left(deadline_at) <= 0
        collection_stop_reason = str(collection_metrics.get("stop_reason", ""))
        collection_incomplete = bool(
            not collection_metrics.get("target_filled")
            and collection_stop_reason not in {"SEARCH_EXHAUSTED", "SINGLE_BATCH"}
        )
        if deadline_reached:
            warnings.append(
                "Достигнут лимит времени; в результат включены только завершённые проверки."
            )
        pipeline = PipelineAudit(
            collected_count=len(listings),
            raw_collected_count=int(collection_metrics.get(
                "raw_received", collection_metrics.get("raw_collected", len(listings)))),
            deduplicated_count=int(collection_metrics.get(
                "unique_received", collection_metrics.get("deduplicated", len(listings)))),
            correct_city_count=int(collection_metrics.get("correct_city", len(listings))),
            request_family_matched_count=int(collection_metrics.get("request_family_matched", len(listings))),
            valid_target_count=int(collection_metrics.get("valid_target_count", len(listings))),
            requested_target=int(collection_metrics.get("requested_target", request.max_results)),
            target_filled=bool(collection_metrics.get("target_filled")),
            collection_stop_reason=str(collection_metrics.get("stop_reason", "")),
            basic_filtered_count=review_run.deterministic_eligible_count,
            deterministic_eligible_count=review_run.deterministic_eligible_count,
            text_attempted_count=review_run.text_attempted_count,
            text_completed_count=review_run.text_completed_count,
            text_matched_count=review_run.text_matched_count,
            photo_attempted_count=review_run.photo_attempted_count,
            photo_completed_count=review_run.photo_completed_count,
            high_confidence_count=sum(
                item.ai_review.confidence >= 0.75
                and is_customer_safe(item, request, require_freshness=False)
                for item in analyzed
            ),
            final_visible_count=min(safe_count, request.desired_results),
            elapsed_seconds=time.monotonic() - run_started_at,
            deadline_reached=deadline_reached,
            rejection_reasons=aggregate_rejections(
                analyzed, request, eligible_ids, recommendations,
            ),
            text_routing=tuple(
                {"state": state, "count": count}
                for state, count in review_run.text_route_counts
            ),
            bargain_funnel=review_run.bargain_funnel or {},
            bargain_decisions=review_run.bargain_decisions,
        )
        _emit(progress, "complete", f"Готово: проверено вариантов — {min(safe_count, request.desired_results)}.", 100)
        return AnalysisReport(
            query=request.query,
            location=request.location,
            collected_count=len(listings),
            analyzed_count=complete_count,
            mode=request.mode,
            priority=request.priority,
            result_limit=request.desired_results,
            recommendations=recommendations,
            discovered_listings=discovered,
            warnings=tuple(warnings),
            admin_warnings=tuple(admin_warnings),
            costs=costs,
            pipeline=pipeline,
            request=request,
            empty_reason=empty_reason,
            outcome=("SUCCESS" if safe_count else (
                "SEARCH_INCOMPLETE"
                if incomplete_candidates or deadline_reached or collection_incomplete
                else "EMPTY_VERIFIED"
            )),
        )

    def _discovery_only_report(
        self, sources: tuple[AnalyzedListing, ...], request: SearchRequest,
        *, warning: str, apify_cost_usd: float, apify_cost_estimated: bool,
        ai_cost_rub: float, ai_cost_estimated: bool, started_at: float,
        progress: ProgressCallback | None, outcome: str = "SEARCH_INCOMPLETE",
    ) -> AnalysisReport:
        """Return already collected evidence when a later provider stage fails."""
        discovered = build_discovered_listings(sources, request)
        _emit(progress, "complete", "Проверка не завершена. Подтверждённых результатов нет.", 100)
        return AnalysisReport(
            query=request.query, location=request.location, mode=request.mode, priority=request.priority,
            result_limit=request.desired_results, collected_count=len(sources), analyzed_count=0,
            discovered_listings=discovered,
            warnings=("Проверка не завершена. Подтверждённых результатов нет.",),
            admin_warnings=(warning,), request=request,
            empty_reason="Не удалось завершить проверку источников. Непроверенные предложения не показаны.",
            costs=self._cost_summary(apify_cost_usd=apify_cost_usd, apify_cost_estimated=apify_cost_estimated,
                ai_cost_rub=ai_cost_rub, ai_cost_estimated=ai_cost_estimated,
                text_reviewed_count=sum(item.ai_review.text_analyzed for item in sources),
                photo_reviewed_count=0, cached_count=0,
                skipped_count=sum(not item.ai_review.text_analyzed for item in sources)),
            pipeline=PipelineAudit(text_completed_count=sum(item.ai_review.text_analyzed for item in sources),
                elapsed_seconds=time.monotonic() - started_at),
            outcome=outcome,
        )

    def search(
        self,
        request: SearchRequest,
        *,
        deadline_seconds: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> AnalysisReport:
        if self.provider is None:
            raise AvitoServiceError("Реальный сбор Avito не настроен.")
        begin_budget = getattr(self.reviewer, "begin_budget", None)
        if callable(begin_budget):
            begin_budget(self.ai_max_cost_rub)
        started_at = time.monotonic()
        deadline_at = started_at + deadline_seconds if deadline_seconds is not None else None
        _emit(progress, "collect", "Подключились к сервису. Собираем рынок Авито…", 8)
        if getattr(self.provider, "collect", None) is None:
            raise AvitoServiceError("Реальный сбор Avito не настроен.")
        provider_config = getattr(self.provider, "config", None)
        fill_collector = getattr(self.provider, "collect_to_target", None)
        collector = fill_collector if callable(fill_collector) else getattr(self.provider, "collect_market", None)
        sharing_capability = getattr(self.provider, "can_share_discovery", None)
        shared_discovery = bool(
            callable(collector) and callable(sharing_capability) and sharing_capability(request)
        )
        unit_cost = getattr(provider_config, "apify_full_listing_cost_usd", 0.00799)
        start_cost = getattr(provider_config, "apify_start_cost_usd", 0.005)
        collection_allowance = max(0.0, self.report_max_cost_rub - self.ai_max_cost_rub) / self.usd_rub_rate
        collection_allowance = min(collection_allowance, getattr(provider_config, "apify_max_charge_usd", collection_allowance))
        collection_starts = 2 if shared_discovery else 3
        # Up to 200 source listings plus a separately reserved final refresh.
        # A larger requested pool cannot spend beyond the same source allowance.
        total_slots = min(200 + request.desired_results,
                          math.floor(max(0.0, collection_allowance - collection_starts * start_cost) / unit_cost))
        refresh_slots = min(request.desired_results, max(0, total_slots // 4))
        discovery_slots = max(0, total_slots - refresh_slots)
        market_slots = (min(request.max_results, discovery_slots) if shared_discovery
                        else min(max(20, request.max_results), discovery_slots * 2 // 3))
        candidate_slots = 0 if shared_discovery else min(request.max_results, discovery_slots - market_slots)
        if (market_slots < 1 if shared_discovery else candidate_slots < 1) or refresh_slots < 1:
            raise AvitoServiceError("Лимита отчёта недостаточно для сбора и повторной проверки.")
        reviewer_config = getattr(self.reviewer, "config", None)
        # Collection strategy is not part of market identity. A broad snapshot
        # remains reusable when deterministic price bounds become narrower.
        cache_version = ":strict-identity-kit-v6"
        cache_key = self.market_cache.key(request, getattr(reviewer_config, "ai_model", "") + cache_version)
        cache_lookup = getattr(self.market_cache, "lookup", None)
        if callable(cache_lookup):
            market_analyzed, cache_meta = cache_lookup(cache_key)
        else:
            market_analyzed = self.market_cache.get(cache_key)
            cache_meta = {"hit": market_analyzed is not None, "age_seconds": None}
        if shared_discovery and market_analyzed is not None:
            # A partial AI cache is not a complete source pool. Only a recent
            # snapshot covering the requested collection size replaces a run.
            market_analyzed = tuple(item for item in market_analyzed if item.listing.is_recently_collected())
            if (not callable(fill_collector)
                    and len({item.listing.listing_id for item in market_analyzed}) < request.max_results):
                market_analyzed = None
                cache_meta = {**cache_meta, "hit": False, "miss_reason": "coverage_insufficient"}
        trace = _trace(progress)
        if trace is not None:
            trace.record_cache(
                "market",
                hit=market_analyzed is not None,
                age_seconds=cache_meta.get("age_seconds") if market_analyzed is not None else None,
                miss_reason=cache_meta.get("miss_reason") if market_analyzed is None else None,
            )
        market_cost = 0.0
        market_reserved = 0.0
        market_ai_cost = 0.0
        market_text_count = 0
        market_estimated = False
        market_batch: CollectionBatch | None = None
        market_listings: tuple[NormalizedListing, ...] = ()
        if market_analyzed is None and callable(collector) and market_slots:
            market_reserved = market_slots * unit_cost + start_cost
            collection_request = replace(
                request, price_min=None, price_max=None,
                max_results=request.max_results if callable(fill_collector) else market_slots,
            )
            collector_kwargs: dict[str, Any] = {
                "deadline_at": deadline_at,
                "max_charge_usd": market_reserved,
            }
            cancel_requested = getattr(progress, "cancel_requested", None)
            if getattr(self.provider, "supports_cancellation", False) and callable(cancel_requested):
                collector_kwargs["cancel_requested"] = cancel_requested
            if getattr(self.provider, "supports_telemetry", False) and trace is not None:
                collector_kwargs["telemetry"] = trace.record_apify
                collector_kwargs["purpose"] = "market_collection"
            market_batch = collector(collection_request, **collector_kwargs)
            market_cost = market_batch.apify_cost_usd
            market_estimated = market_batch.apify_cost_estimated
            market_listings = normalize_dataset(market_batch.items)
            try:
                market_analyzed, market_ai_cost, market_text_count = self._review_market(
                    market_listings, request, budget_rub=self.ai_max_cost_rub * 0.3,
                    deadline_at=deadline_at, progress=progress,
                )
            except SearchCancelledError:
                raise
            except AvitoServiceError as exc:
                sources = tuple(AnalyzedListing(listing, evaluate_rules(listing),
                    incomplete_review(listing, str(exc))) for listing in market_listings)
                return self._discovery_only_report(sources, request,
                    warning=f"AI-проверка остановлена: {exc}. Исходные объявления сохранены для диагностики.",
                    apify_cost_usd=market_cost, apify_cost_estimated=market_estimated,
                    ai_cost_rub=self.ai_max_cost_rub * 0.3, ai_cost_estimated=True,
                    started_at=started_at, progress=progress)
            if shared_discovery:
                reviewed = {item.listing.listing_id: item for item in market_analyzed}
                all_sources = tuple(reviewed.get(listing.listing_id) or AnalyzedListing(
                    listing, evaluate_rules(listing),
                    incomplete_review(listing, "Исходное объявление ещё не прошло AI-проверку."),
                ) for listing in market_listings)
                self.market_cache.put(cache_key, all_sources)
            else:
                self.market_cache.put(cache_key, market_analyzed)
        market_analyzed = market_analyzed or ()
        known_market = {item.listing.listing_id: item for item in market_analyzed}
        discovery_sources = tuple(
            known_market.get(listing.listing_id) or AnalyzedListing(
                listing, evaluate_rules(listing),
                incomplete_review(listing, "Рыночное объявление ещё не прошло полную проверку."),
            ) for listing in market_listings
        ) or market_analyzed
        refresh_allowance = refresh_slots * unit_cost + start_cost
        # Apify billing can lag behind SUCCEEDED. Do not spend an apparently
        # unused reservation again while the first run's events settle.
        market_budget_used = max(market_cost, market_reserved)
        if shared_discovery:
            _emit(progress, "collect", "Единая выборка собрана. Проверяем подходящие объявления…", 24)
            return self.analyze_dataset(
                market_batch.items if market_batch is not None else (),
                request,
                apify_cost_usd=market_cost,
                apify_cost_estimated=market_estimated,
                collection_was_capped=bool(market_batch and market_batch.capped_count < request.max_results),
                deadline_at=deadline_at, progress=progress, started_at=started_at,
                market_analyzed=market_analyzed, market_ai_cost_rub=market_ai_cost,
                market_text_count=market_text_count, refresh_finalists=True,
                discovery_sources=discovery_sources,
                collection_budget_remaining_usd=min(refresh_allowance,
                    max(0.0, collection_allowance - market_budget_used)),
                collection_metrics=market_batch.fill_metrics if market_batch else None,
                reuse_market_text=True,
            )
        candidate_allowance = max(0.0, collection_allowance - market_budget_used - refresh_allowance)
        candidate_request = replace(request, max_results=candidate_slots)
        try:
            if getattr(self.provider, "supports_collection_budget", False):
                collect_kwargs: dict[str, Any] = {
                    "deadline_at": deadline_at,
                    "max_charge_usd": candidate_allowance,
                }
                cancel_requested = getattr(progress, "cancel_requested", None)
                if getattr(self.provider, "supports_cancellation", False) and callable(cancel_requested):
                    collect_kwargs["cancel_requested"] = cancel_requested
                if getattr(self.provider, "supports_telemetry", False) and trace is not None:
                    collect_kwargs["telemetry"] = trace.record_apify
                    collect_kwargs["purpose"] = "candidate_collection"
                collected = self.provider.collect(candidate_request, **collect_kwargs)
            elif getattr(self.provider, "supports_deadline", False):
                collected = self.provider.collect(candidate_request, deadline_at=deadline_at)
            else:
                collected = self.provider.collect(candidate_request)
        except SearchCancelledError:
            raise
        except AvitoServiceError as exc:
            if not discovery_sources:
                raise
            return self._discovery_only_report(discovery_sources, request,
                warning=f"Дополнительный сбор остановлен: {exc}. Исходные объявления сохранены для диагностики.",
                apify_cost_usd=market_cost + candidate_allowance, apify_cost_estimated=True,
                ai_cost_rub=market_ai_cost,
                ai_cost_estimated=any(item.ai_review.cost_estimated for item in market_analyzed),
                started_at=started_at, progress=progress,
                outcome="SPEND_LIMIT" if getattr(exc, "code", "") in {
                    "APIFY_SPEND_LIMIT", "COLLECTION_BUDGET"
                } else "SEARCH_INCOMPLETE")
        _emit(progress, "collect", "Объявления собраны. Готовим данные к проверке…", 24)
        if isinstance(collected, CollectionBatch):
            return self.analyze_dataset(
                collected.items,
                request,
                apify_cost_usd=collected.apify_cost_usd + market_cost,
                apify_cost_estimated=collected.apify_cost_estimated or market_estimated,
                collection_was_capped=collected.capped_count < collected.requested_count,
                deadline_at=deadline_at,
                progress=progress,
                started_at=started_at,
                market_analyzed=market_analyzed, market_ai_cost_rub=market_ai_cost,
                market_text_count=market_text_count, refresh_finalists=True,
                discovery_sources=discovery_sources,
                collection_budget_remaining_usd=max(0.0, collection_allowance - market_budget_used
                    - max(collected.apify_cost_usd, candidate_allowance)),
                collection_metrics=collected.fill_metrics,
                reuse_market_text=True,
            )
        return self.analyze_dataset(
            collected,
            request,
            apify_cost_usd=market_cost,
            apify_cost_estimated=market_estimated,
            deadline_at=deadline_at,
            progress=progress,
            started_at=started_at,
            market_analyzed=market_analyzed, market_ai_cost_rub=market_ai_cost,
            market_text_count=market_text_count, refresh_finalists=True,
            discovery_sources=discovery_sources,
            collection_budget_remaining_usd=max(0.0, collection_allowance - market_budget_used - candidate_allowance),
            reuse_market_text=True,
        )
