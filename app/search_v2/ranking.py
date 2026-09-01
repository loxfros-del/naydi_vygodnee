"""Deterministic ranking after exact match, grouping, market and risks."""
from __future__ import annotations

import re
from typing import Any, Iterable

from .models import (
    AvailabilityStatus,
    ExactMatchResult,
    MarketStats,
    Offer,
    PlatformTrust,
    PriceClass,
    SearchRequestV2,
    SellerTrust,
)
from .risk_engine import risk_penalty


_TOKEN_RE = re.compile(r"[0-9a-zа-я]+", re.IGNORECASE)


def _tokens(value: Any) -> set[str]:
    return set(_TOKEN_RE.findall(str(value or "").replace("ё", "е").casefold()))


def _numbers(value: Any) -> set[float]:
    return {float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[.,]\d+)?", str(value or ""))}


def _matches_optional(required: Any, candidate: Any) -> bool:
    """Return true only for an explicit, compatible optional fact.

    Optional facts rank already correct products; absence never penalizes a
    listing and fuzzy prose never becomes a bonus.
    """
    if candidate in (None, "", [], {}):
        return False
    if isinstance(required, bool):
        return required is candidate
    required_numbers = _numbers(required)
    candidate_numbers = _numbers(candidate)
    if required_numbers and candidate_numbers:
        return required_numbers.issubset(candidate_numbers)
    wanted = _tokens(required)
    actual = _tokens(candidate)
    return bool(wanted and wanted.issubset(actual))


def _optional_bonus(offer: Offer, request: SearchRequestV2 | None) -> float:
    if request is None or not request.optional_specs:
        return 0.0
    identity = offer.identity
    facts = dict(offer.facts or {})
    configuration = dict(identity.key_configuration or {}) if identity else {}
    bonus = 0.0
    for key, required in request.optional_specs.items():
        candidate = configuration.get(key, facts.get(key))
        if _matches_optional(required, candidate):
            bonus += 2.0
    # User's stated priority can tune a correct result, never bypass its
    # exact-match or safety requirements.
    if request.priority == "price" and offer.price:
        bonus += 1.0
    elif request.priority in {"reliability", "reliable"} and (
        offer.platform_trust is PlatformTrust.HIGH_RETAIL
        or offer.seller.trust is SellerTrust.HIGH
    ):
        bonus += 1.0
    return min(8.0, bonus)


def is_rankable(offer: Offer) -> bool:
    return bool(
        offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
        and offer.availability.status is not AvailabilityStatus.OUT_OF_STOCK
        and offer.price
        and offer.price > 0
    )


def score_offer(offer: Offer, market_stats: MarketStats | None = None, request: SearchRequestV2 | None = None) -> float:
    if not is_rankable(offer):
        return 0.0
    score = 45.0
    score += 20.0 if offer.exact_match is ExactMatchResult.EXACT else 13.0
    score += {
        PlatformTrust.HIGH_RETAIL: 11.0,
        PlatformTrust.HIGH_MARKETPLACE: 8.0,
        PlatformTrust.CLASSIFIED: 3.0,
        PlatformTrust.UNKNOWN: 0.0,
    }[offer.platform_trust]
    score += {
        SellerTrust.HIGH: 11.0,
        SellerTrust.MEDIUM: 5.0,
        SellerTrust.LOW: -10.0,
        SellerTrust.UNKNOWN: 0.0,
    }[offer.seller.trust]
    price_class = market_stats.price_classes.get(offer.offer_id, PriceClass.UNKNOWN) if market_stats else PriceClass.UNKNOWN
    score += {
        PriceClass.VERY_CHEAP: 5.0,
        PriceClass.BELOW_MARKET: 10.0,
        PriceClass.FAIR: 7.0,
        PriceClass.ABOVE_MARKET: 0.0,
        PriceClass.VERY_EXPENSIVE: -8.0,
        PriceClass.UNKNOWN: 0.0,
    }[price_class]
    if offer.availability.status is AvailabilityStatus.IN_STOCK:
        score += 7.0
    if offer.final_verification.price_verified is True:
        score += 2.0
    if offer.final_verification.availability_verified is True:
        score += 2.0
    if request and request.budget and offer.price and offer.price > request.budget:
        score -= min(20.0, (offer.price - request.budget) / request.budget * 40.0)
    # Keep a little headroom: an explicit optional match may distinguish two
    # otherwise equally safe offers.  The public score remains bounded.
    score = min(96.0, score)
    score += _optional_bonus(offer, request)
    score -= risk_penalty(offer)
    return round(max(0.0, min(100.0, score)), 3)


def rank_offers(
    offers: Iterable[Offer],
    market_stats: MarketStats | None = None,
    request: SearchRequestV2 | None = None,
) -> list[tuple[Offer, float]]:
    ranked = [(offer, score_offer(offer, market_stats, request)) for offer in offers if is_rankable(offer)]
    ranked.sort(key=lambda item: (-item[1], float(item[0].price or 10**18), item[0].offer_id))
    return ranked


__all__ = ["is_rankable", "rank_offers", "score_offer"]
