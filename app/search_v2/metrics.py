"""Deterministic aggregate metrics for one Search V2 case."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .market_analysis import is_comparable_offer
from .models import (
    AvailabilityStatus,
    ExactMatchResult,
    Offer,
    ProductGroup,
    Recommendation,
    SearchMetrics,
    SourceAttempt,
    SourceStatus,
)


_WRONG = {
    ExactMatchResult.MODEL_MISMATCH,
    ExactMatchResult.REQUIRED_SPEC_MISMATCH,
    ExactMatchResult.ACCESSORY,
}
_MAX_FRESH_AGE = timedelta(hours=36)


def _ratio(numerator: int | float, denominator: int) -> float:
    return round(float(numerator) / denominator, 4) if denominator else 0.0


def _offer_key(offer: Offer) -> str:
    return str(offer.offer_id or offer.product_id or offer.url or f"{offer.source}|{offer.title}|{offer.price}")


def _is_fresh_comparable(offer: Offer, *, now: datetime) -> bool:
    """Use the same bounded freshness rule as current-price evidence."""
    if not is_comparable_offer(offer):
        return False
    observed = offer.retrieved_at
    if not isinstance(observed, datetime):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = now - observed.astimezone(timezone.utc)
    return (
        timedelta(minutes=-5) <= age <= _MAX_FRESH_AGE
        and (offer.cache_age is None or offer.cache_age <= _MAX_FRESH_AGE.total_seconds())
    )


def _top_group_price_span(groups: Iterable[ProductGroup], top: Offer | None) -> float | None:
    if top is None:
        return None
    for group in groups:
        if not any(offer.offer_id == top.offer_id for offer in group.offers):
            continue
        stats = group.market_stats
        if not stats or not stats.median or stats.median <= 0:
            return None
        if stats.minimum is None or stats.maximum is None:
            return None
        return round((stats.maximum - stats.minimum) / stats.median * 100, 2)
    return None


def build_search_metrics(
    *,
    attempts: Iterable[SourceAttempt],
    raw_offer_count: int,
    offers: Iterable[Offer],
    rejected_offers: Iterable[Offer],
    groups: Iterable[ProductGroup],
    recommendations: Iterable[Recommendation],
    duration_ms: float,
    manual_review_required: bool = False,
    admin_review_time_ms: float | None = None,
    now: datetime | None = None,
) -> SearchMetrics:
    attempts_list = list(attempts)
    offers_list = list(offers)
    rejected_list = list(rejected_offers)
    groups_list = list(groups)
    recommendations_list = list(recommendations)[:3]
    completed = len(attempts_list)
    success = sum(item.status in {SourceStatus.SUCCESS, SourceStatus.PARTIAL_SUCCESS} for item in attempts_list)
    timeouts = sum(item.status is SourceStatus.TIMEOUT for item in attempts_list)
    blocked = sum(item.status in {SourceStatus.BLOCKED, SourceStatus.RATE_LIMITED, SourceStatus.UNAUTHORIZED} for item in attempts_list)
    exact = sum(item.exact_match is ExactMatchResult.EXACT for item in offers_list)
    wrong = sum(item.exact_match in _WRONG for item in rejected_list)
    valid_prices = sum(item.price is not None and item.price > 0 for item in offers_list)
    keys = {_offer_key(item) for item in offers_list}
    duplicate_count = max(0, int(raw_offer_count) - len(keys))
    sources = {item.source.casefold() for item in offers_list if item.source}
    useful = sum(
        bool(rec.offer)
        and rec.offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
        and rec.offer.availability.status is not AvailabilityStatus.OUT_OF_STOCK
        for rec in recommendations_list
    )
    top = recommendations_list[0].offer if recommendations_list and recommendations_list[0].offer else None
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    comparable = [item for item in offers_list if is_comparable_offer(item)]
    fresh_comparable = [item for item in comparable if _is_fresh_comparable(item, now=current_time)]
    priced_exact_sources = {
        item.source.casefold()
        for item in offers_list
        if (
            item.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
            and item.price is not None
            and item.price > 0
            and item.source
        )
    }
    return SearchMetrics(
        attempted_source_count=completed,
        successful_source_count=success,
        source_success_rate=_ratio(success, completed),
        source_timeout_rate=_ratio(timeouts, completed),
        blocked_rate=_ratio(blocked, completed),
        raw_offer_count=max(0, int(raw_offer_count)),
        exact_offer_count=exact,
        wrong_model_rejection_count=wrong,
        valid_price_rate=_ratio(valid_prices, len(offers_list)),
        priced_exact_source_count=len(priced_exact_sources),
        comparable_offer_count=len(comparable),
        fresh_comparable_offer_count=len(fresh_comparable),
        stale_comparable_offer_count=max(0, len(comparable) - len(fresh_comparable)),
        product_group_count=len(groups_list),
        market_median_group_count=sum(
            bool(group.market_stats and group.market_stats.median is not None)
            for group in groups_list
        ),
        reference_price_span_percent=_top_group_price_span(groups_list, top),
        recommendation_count=len(recommendations_list),
        top1_exact=bool(top and top.exact_match is ExactMatchResult.EXACT),
        top3_useful=useful,
        duplicate_rate=_ratio(duplicate_count, max(0, int(raw_offer_count))),
        source_diversity=len(sources),
        duration_ms=max(0.0, float(duration_ms)),
        cache_hit_count=sum(bool(item.cache_hit) for item in attempts_list),
        manual_review_required=bool(manual_review_required),
        admin_review_time_ms=admin_review_time_ms,
    )


__all__ = ["build_search_metrics"]
