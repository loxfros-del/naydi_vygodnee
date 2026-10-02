"""Auditable, client-safe price advantage evidence for one exact product group."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
import math
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


def _seller_keys(offer: Offer) -> set[str]:
    """Names connect one merchant across platforms; IDs are platform-local."""
    seller = offer.seller
    keys: set[str] = set()
    name = normalize_key(seller.name)
    if name and name not in {"unknown", "неизвестно", "неизвестный продавец"}:
        keys.add(f"name:{name}")
    seller_id = normalize_key(seller.seller_id)
    if seller_id:
        keys.add(f"id:{_source_key(offer)}:{seller_id}")
    return keys


def _source_key(offer: Offer) -> str:
    return normalize_key(offer.source or offer.platform)


def _seller_components(offers: list[Offer]) -> dict[str, str]:
    """Resolve all observed aliases before counting independent merchants."""
    parents: dict[str, str] = {}

    def root(key: str) -> str:
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for offer in offers:
        keys = sorted(_seller_keys(offer))
        if keys:
            anchor = root(keys[0])
            for key in keys[1:]:
                parents[root(key)] = anchor
    return {key: root(key) for key in parents}


def _verification_rejected(offer: Offer) -> bool:
    # A resolved final/manual confirmation may supersede an earlier automatic
    # failure. Explicit negative evidence must never be outweighed by a score.
    for field in ("model_verified", "link_verified", "price_verified", "availability_verified"):
        for state in (offer.final_verification, offer.manual_verification, offer.automatic_verification):
            value = getattr(state, field, None)
            if value is not None:
                if value is False:
                    return True
                break
    return False


def _has_conditional_price(offer: Offer) -> bool:
    # Adapters may carry these terms as facts/metadata even though Offer has
    # only one numeric price. A card/member/installment price is not a common
    # cash price and cannot prove an unconditional saving.
    unconditional_kinds = {"", "regular", "current", "cash", "full", "total", "unconditional"}
    for data in (offer.facts, offer.raw_metadata):
        if not isinstance(data, dict):
            continue
        if data.get("price_conditions") not in (None, "", False, [], {}, ()):
            return True
        if normalize_key(data.get("price_kind")) not in unconditional_kinds:
            return True
    return False


def _eligible(offer: Offer, now: datetime) -> bool:
    return bool(
        is_comparable_offer(offer)
        and math.isfinite(float(offer.price or 0))
        and str(offer.currency or "").upper() in {"RUB", "RUR", "₽"}
        and offer.availability.status is AvailabilityStatus.IN_STOCK
        and offer.availability.available is not False
        and not _verification_rejected(offer)
        and not _has_conditional_price(offer)
        and _seller_keys(offer)
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

    seller_components = _seller_components([selected, *group.offers])
    seen: set[str] = {seller_components[key] for key in _seller_keys(selected)}
    seen_urls: set[str] = set()
    references: list[Offer] = []
    selected_url = canonicalize_url(selected.url)
    if selected_url:
        seen_urls.add(selected_url)
    # When one merchant has several listings, its lowest eligible price is the
    # conservative reference. Input order must not inflate the claimed saving.
    candidates = sorted(
        (offer for offer in group.offers if _eligible(offer, now)),
        key=lambda offer: float(offer.price or 0),
    )
    for offer in candidates:
        if offer is selected or (selected.offer_id and offer.offer_id == selected.offer_id):
            continue
        url = canonicalize_url(offer.url)
        if url in seen_urls:
            continue
        if _configuration_key(offer) != selected_configuration:
            continue
        keys = {seller_components[key] for key in _seller_keys(offer)}
        if not keys or keys & seen:
            continue
        seen.update(keys)
        seen_urls.add(url)
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
        client_reason=(
            f"экономия {_rub(saving)} по цене товара относительно типичной цены {_rub(baseline)}; "
            "доставка и условия скидок проверяются отдельно"
        ),
    )


__all__ = ["MAX_REFERENCE_AGE", "SavingsEvidence", "build_savings_evidence"]
