"""Auditable, client-safe price advantage evidence for one exact product group."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from .grouping import canonical_identity_key
from .market_analysis import is_comparable_offer
from .models import AvailabilityStatus, Offer, ProductGroup, utc_now
from .normalization import canonicalize_url, is_product_page_url, normalize_key


MAX_REFERENCE_AGE = timedelta(hours=36)


def _rub(value: float | int) -> str:
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"


def _is_current(offer: Offer, now: datetime) -> bool:
    timestamp = offer.retrieved_at
    if not isinstance(timestamp, datetime):
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = now - timestamp.astimezone(timezone.utc)
    return timedelta(minutes=-5) <= age <= MAX_REFERENCE_AGE and (offer.cache_age is None or offer.cache_age <= MAX_REFERENCE_AGE.total_seconds())


def _seller_or_url_key(offer: Offer) -> str:
    seller = offer.seller
    seller_key = normalize_key(seller.seller_id or seller.name)
    if seller_key:
        return f"seller:{seller_key}"
    return f"url:{canonicalize_url(offer.url)}"


def _source_key(offer: Offer) -> str:
    return normalize_key(offer.source or offer.platform)


def _eligible(offer: Offer, now: datetime) -> bool:
    return bool(
        is_comparable_offer(offer)
        and offer.availability.status is AvailabilityStatus.IN_STOCK
        and is_product_page_url(offer.url)
        and _is_current(offer, now)
    )


def _configuration_key(offer: Offer) -> str:
    if offer.identity is None:
        return ""
    return offer.identity.canonical_key or canonical_identity_key(offer.identity)


@dataclass(frozen=True, slots=True)
class SavingsEvidence:
    selected_price: float
    baseline_price: float
    saving_rub: int
    saving_percent: float
    comparable_offer_count: int
    source_count: int
    reference_offer_ids: tuple[str, ...]
    client_reason: str


def build_savings_evidence(
    selected: Offer,
    group: ProductGroup,
    *,
    now: datetime | None = None,
) -> SavingsEvidence | None:
    """Prove an advantage using independent, current offers other than ``selected``."""
    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not _eligible(selected, now):
        return None
    selected_configuration = _configuration_key(selected)
    if not selected_configuration:
        return None

    seen: set[str] = set()
    references: list[Offer] = []
    selected_url = canonicalize_url(selected.url)
    for offer in group.offers:
        if offer is selected or (selected.offer_id and offer.offer_id == selected.offer_id):
            continue
        if selected_url and canonicalize_url(offer.url) == selected_url:
            continue
        if _configuration_key(offer) != selected_configuration:
            continue
        if not _eligible(offer, now):
            continue
        key = _seller_or_url_key(offer)
        if not key or key in seen:
            continue
        seen.add(key)
        references.append(offer)

    sources = {_source_key(offer) for offer in references if _source_key(offer)}
    if len(references) < 3 or len(sources) < 2:
        return None
    prices = [float(offer.price) for offer in references if offer.price]
    baseline = float(median(prices))
    selected_price = float(selected.price or 0)
    saving = baseline - selected_price
    if saving <= 0:
        return None
    saving_rub = int(round(saving))
    return SavingsEvidence(
        selected_price=selected_price,
        baseline_price=baseline,
        saving_rub=saving_rub,
        saving_percent=saving / baseline * 100,
        comparable_offer_count=len(references),
        source_count=len(sources),
        reference_offer_ids=tuple(offer.offer_id for offer in references if offer.offer_id),
        client_reason=f"экономия {_rub(saving)} относительно типичной цены {_rub(baseline)}",
    )


__all__ = ["MAX_REFERENCE_AGE", "SavingsEvidence", "build_savings_evidence"]
