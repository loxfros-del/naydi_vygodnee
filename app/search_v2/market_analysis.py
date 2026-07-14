"""Comparable-offer market statistics based on the median."""
from __future__ import annotations

from dataclasses import replace
from statistics import median
from typing import Iterable

from .models import (
    AvailabilityStatus,
    ExactMatchResult,
    MarketStats,
    Offer,
    PriceClass,
    ProductGroup,
)


def is_comparable_offer(offer: Offer) -> bool:
    return bool(
        offer.price
        and offer.price > 0
        and offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
        and offer.availability.status is not AvailabilityStatus.OUT_OF_STOCK
        and (
            offer.price_confidence >= 0.6
            or offer.automatic_verification.price_verified is True
            or offer.manual_verification.price_verified is True
        )
    )


def classify_price(price: float | None, market_median: float | None) -> PriceClass:
    if not price or not market_median or market_median <= 0:
        return PriceClass.UNKNOWN
    deviation = (price - market_median) / market_median * 100
    if deviation <= -25:
        return PriceClass.VERY_CHEAP
    if deviation <= -8:
        return PriceClass.BELOW_MARKET
    if deviation < 15:
        return PriceClass.FAIR
    if deviation < 30:
        return PriceClass.ABOVE_MARKET
    return PriceClass.VERY_EXPENSIVE


def _trimmed_mean(prices: list[float]) -> float | None:
    if not prices:
        return None
    ordered = sorted(prices)
    trim = max(1, int(len(ordered) * 0.1)) if len(ordered) >= 5 else 0
    selected = ordered[trim:-trim] if trim and len(ordered) > trim * 2 else ordered
    return sum(selected) / len(selected)


def analyze_market(offers: Iterable[Offer]) -> MarketStats:
    all_offers = list(offers)
    comparable = [offer for offer in all_offers if is_comparable_offer(offer)]
    prices = [float(offer.price) for offer in comparable if offer.price]
    if not prices:
        return MarketStats(offer_count=len(all_offers), verified_offer_count=0)
    middle = float(median(prices))
    deviations = {
        offer.offer_id: (float(offer.price) - middle) / middle * 100
        for offer in comparable if offer.price and offer.offer_id
    }
    classes = {
        offer.offer_id: classify_price(offer.price, middle)
        for offer in comparable if offer.offer_id
    }
    minimum, maximum = min(prices), max(prices)
    return MarketStats(
        offer_count=len(all_offers),
        verified_offer_count=len(prices),
        minimum=minimum,
        median=middle,
        trimmed_mean=_trimmed_mean(prices),
        maximum=maximum,
        market_range=(minimum, maximum),
        deviation_percent=deviations,
        price_classes=classes,
        currency=comparable[0].currency if comparable else "RUB",
    )


def analyze_product_groups(groups: Iterable[ProductGroup]) -> list[ProductGroup]:
    return [replace(group, market_stats=analyze_market(group.offers)) for group in groups]


__all__ = ["analyze_market", "analyze_product_groups", "classify_price", "is_comparable_offer"]
