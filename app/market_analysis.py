"""Pure market grouping, price statistics and recommendation role planning.

The module deliberately keeps a product group identity separate from an offer
identity.  Offers from different sellers therefore remain available for market
comparison even when they describe the same product configuration.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
import json
import math
import re
from statistics import fmean, median
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.request_parser import parse_request_details


_TRACKING_QUERY_KEYS = {"from", "ref", "erid", "gclid", "yclid"}
_MODEL_MODIFIERS = (
    "pro max", "pro", "ultra", "plus", "max", "mini", "lite", "air", "fe", "se",
)
_BLOCKED_EXACT = {"MODEL_MISMATCH", "REQUIRED_SPEC_MISMATCH", "ACCESSORY"}
_UNAVAILABLE = {
    "UNAVAILABLE", "REMOVED_LISTING", "OUT_OF_STOCK", "SOLD", "INACTIVE", "DELETED",
}
_RETAIL_ANCHORS = (
    "dns", "dns-shop", "днс", "citilink", "ситилинк", "mvideo", "мвидео", "м видео",
)


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _present(value: Any) -> bool:
    return value not in (None, "", [], {}, ())


def _normalize(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", text).strip()


def _facts(offer: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    raw_json = _value(offer, "facts_json", "")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict):
            result.update(decoded)
    for name in ("facts", "product_facts", "structured_facts"):
        value = _value(offer, name, {})
        if isinstance(value, dict):
            result.update(value)
    return result


def _field(offer: Any, facts: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = _value(offer, name, None)
        if _present(value):
            return value
        value = facts.get(name)
        if _present(value):
            return value
    return default


def _number(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return int(value)
    text = str(value).replace("\xa0", " ").strip().casefold()
    match = re.search(r"\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    number = float(match.group(0).replace(",", "."))
    if "tb" in text or "тб" in text:
        number *= 1024
    return int(number)


def _modifiers(value: Any, model: str) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    elif _present(value):
        raw = re.split(r"[,;/|]+", str(value))
    else:
        normalized_model = _normalize(model)
        raw = [modifier for modifier in _MODEL_MODIFIERS if re.search(rf"\b{re.escape(modifier)}\b", normalized_model)]
        if "pro max" in raw:
            raw = [item for item in raw if item not in {"pro", "max"}]
    return tuple(sorted({_normalize(item) for item in raw if _normalize(item)}))


def _base_model(model: str, modifiers: tuple[str, ...]) -> str:
    normalized = _normalize(model)
    for modifier in sorted(modifiers, key=len, reverse=True):
        normalized = re.sub(rf"\b{re.escape(modifier)}\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _condition(value: Any) -> str:
    normalized = _normalize(value)
    if normalized in {"new", "новый", "новое", "новая"}:
        return "new"
    if normalized in {"used", "бу", "б у", "подержанный", "подержанное"}:
        return "used"
    if normalized in {"refurbished", "refurb", "восстановленный", "восстановленное"}:
        return "refurbished"
    return normalized or "unknown"


def _configuration(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return tuple(sorted(f"{_normalize(key)}={_normalize(item)}" for key, item in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(sorted({_normalize(item) for item in value if _normalize(item)}))
    normalized = _normalize(value)
    return (normalized,) if normalized else ()


@dataclass(frozen=True, slots=True)
class ProductGroupKey:
    """Canonical identity of one comparable product configuration."""

    category: str
    brand: str
    model: str
    modifiers: tuple[str, ...] = ()
    storage_gb: int | None = None
    size: str = ""
    condition: str = "unknown"
    region: str = ""
    sim_variant: str = ""
    key_configuration: tuple[str, ...] = ()

    @classmethod
    def from_offer(cls, offer: Any) -> "ProductGroupKey":
        facts = _facts(offer)
        title = str(_field(offer, facts, "title", default="") or "")
        parsed = parse_request_details(title) if title else {}
        category = _field(offer, facts, "category", default=parsed.get("category") or "unknown")
        brand = _field(offer, facts, "brand", default=parsed.get("brand") or "")
        model_value = str(_field(
            offer, facts, "model", "model_key", default=parsed.get("model") or title,
        ) or "")
        modifier_value = _field(
            offer, facts, "model_modifiers", "modifiers", default=parsed.get("model_modifiers") or (),
        )
        modifiers = _modifiers(modifier_value, model_value)
        storage = _number(_field(
            offer, facts, "storage_gb", "storage", "memory", default=parsed.get("storage_gb"),
        ))
        size_value = _field(
            offer, facts, "size", "diagonal", default=parsed.get("size") or parsed.get("diagonal") or "",
        )
        return cls(
            category=_normalize(category) or "unknown",
            brand=_normalize(brand),
            model=_base_model(model_value, modifiers),
            modifiers=modifiers,
            storage_gb=storage,
            size=_normalize(size_value),
            condition=_condition(_field(offer, facts, "condition", default=parsed.get("condition") or "unknown")),
            region=_normalize(_field(offer, facts, "region", "regional_version", "region_variant", default="")),
            sim_variant=_normalize(_field(offer, facts, "sim_variant", "sim", "sim_type", default="")),
            key_configuration=_configuration(_field(
                offer, facts, "key_configuration", "configuration", default=(),
            )),
        )


def _normalized_url(value: Any) -> str:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    return urlunparse((
        parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), "", urlencode(query), "",
    ))


@dataclass(frozen=True, slots=True)
class OfferIdentity:
    """Identity of one seller listing; deliberately not a product group key."""

    platform: str
    seller: str
    listing_key: str

    @classmethod
    def from_offer(cls, offer: Any) -> "OfferIdentity":
        facts = _facts(offer)
        raw = _value(offer, "raw", {})
        raw = raw if isinstance(raw, Mapping) else {}
        platform = _normalize(_field(offer, facts, "platform", "source", default=""))
        seller = _normalize(_field(offer, facts, "seller", "seller_name", "store", default=""))
        product_id = _field(offer, facts, "product_id", "offer_id", "sku", default=None)
        if not _present(product_id):
            product_id = next((raw.get(key) for key in ("product_id", "offer_id", "sku", "nmId", "id") if _present(raw.get(key))), None)
        url = _normalized_url(_field(offer, facts, "url", default=""))
        if url:
            listing_key = f"url:{url}"
        elif _present(product_id):
            listing_key = f"id:{_normalize(product_id)}"
        else:
            title = _normalize(_field(offer, facts, "title", default=""))
            listing_key = f"title:{title}"
        return cls(platform=platform, seller=seller, listing_key=listing_key)


def group_offers(offers: Iterable[Any]) -> dict[ProductGroupKey, list[Any]]:
    """Group comparable configurations without removing offers from sellers."""
    groups: dict[ProductGroupKey, list[Any]] = defaultdict(list)
    for offer in offers:
        groups[ProductGroupKey.from_offer(offer)].append(offer)
    return dict(groups)


def deduplicate_offers(offers: Iterable[Any]) -> list[Any]:
    """Remove duplicate listings only; never deduplicate by product group."""
    result: list[Any] = []
    seen: set[OfferIdentity] = set()
    for offer in offers:
        identity = OfferIdentity.from_offer(offer)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(offer)
    return result


class OfferPriceClass(str, Enum):
    VERY_CHEAP = "VERY_CHEAP"
    BELOW_MARKET = "BELOW_MARKET"
    FAIR = "FAIR"
    ABOVE_MARKET = "ABOVE_MARKET"
    VERY_EXPENSIVE = "VERY_EXPENSIVE"
    UNKNOWN = "UNKNOWN"


def price_deviation_percent(price: int | float | None, market_median: int | float | None) -> float | None:
    if price is None or market_median is None or float(market_median) <= 0:
        return None
    return round((float(price) - float(market_median)) * 100.0 / float(market_median), 2)


def classify_offer_price(price: int | float | None, market_median: int | float | None) -> OfferPriceClass:
    deviation = price_deviation_percent(price, market_median)
    if deviation is None:
        return OfferPriceClass.UNKNOWN
    if deviation <= -25:
        return OfferPriceClass.VERY_CHEAP
    if deviation <= -10:
        return OfferPriceClass.BELOW_MARKET
    if deviation <= 10:
        return OfferPriceClass.FAIR
    if deviation < 25:
        return OfferPriceClass.ABOVE_MARKET
    return OfferPriceClass.VERY_EXPENSIVE


@dataclass(frozen=True, slots=True)
class MarketPriceStats:
    minimum: float
    median: float
    trimmed_mean: float
    maximum: float
    count: int
    market_range: tuple[float, float]

    @property
    def verified_count(self) -> int:
        return self.count

    def deviation_percent(self, price: int | float | None) -> float | None:
        return price_deviation_percent(price, self.median)

    def classify(self, price: int | float | None) -> OfferPriceClass:
        return classify_offer_price(price, self.median)


def _manual_flag(facts: Mapping[str, Any], key: str) -> bool:
    manual = facts.get("manual_verification") or facts.get("manual_verified")
    if isinstance(manual, Mapping):
        return bool(manual.get(key) or manual.get(f"{key}_verified"))
    return bool(facts.get(f"manual_{key}_verified"))


def _exact_status(offer: Any, facts: Mapping[str, Any]) -> str:
    return str(_field(offer, facts, "exact_match_status", "exact_match", default="") or "").upper()


def _available(offer: Any, facts: Mapping[str, Any]) -> bool:
    available = _field(offer, facts, "available", default=None)
    if available is False:
        return False
    status = str(_field(offer, facts, "availability", "listing_status", "verify_status", default="") or "").upper()
    return status not in _UNAVAILABLE


def _trusted_price(offer: Any, facts: Mapping[str, Any]) -> bool:
    if bool(_field(offer, facts, "price_verified", default=False)) or _manual_flag(facts, "price"):
        return True
    confidence = str(_field(offer, facts, "price_confidence", default="") or "").casefold()
    if confidence in {"high", "medium"}:
        return True
    evidence = str(_field(offer, facts, "price_evidence", "price_source", default="") or "").casefold()
    return evidence in {"direct", "direct_store", "structured", "json_ld", "currency", "labeled"}


def is_comparable_offer(offer: Any, *, group_key: ProductGroupKey | None = None) -> bool:
    """Return whether an offer may contribute to the market price baseline."""
    facts = _facts(offer)
    exact = _exact_status(offer, facts)
    if exact in _BLOCKED_EXACT:
        return False
    if exact not in {"EXACT", "COMPATIBLE_VARIANT"}:
        manually_exact = _manual_flag(facts, "model") or bool(facts.get("exact_product_verified"))
        if not manually_exact:
            return False
    if not _available(offer, facts) or not _trusted_price(offer, facts):
        return False
    price = _number(_field(offer, facts, "price", default=None))
    if price is None or price <= 0:
        return False
    return group_key is None or ProductGroupKey.from_offer(offer) == group_key


def compute_market_price_stats(
    offers: Iterable[Any],
    *,
    group_key: ProductGroupKey | None = None,
    trim_ratio: float = 0.10,
) -> MarketPriceStats | None:
    """Calculate robust price statistics for one comparable product group.

    Passing mixed configurations without ``group_key`` is rejected so callers
    cannot accidentally combine storage, size or condition variants.
    """
    rows = list(offers)
    if group_key is None:
        keys = {ProductGroupKey.from_offer(item) for item in rows}
        if len(keys) > 1:
            raise ValueError("market price statistics require one ProductGroupKey")
        group_key = next(iter(keys), None)
    prices = sorted(
        float(_number(_field(item, _facts(item), "price")))
        for item in rows
        if is_comparable_offer(item, group_key=group_key)
    )
    if not prices:
        return None
    ratio = min(max(float(trim_ratio), 0.0), 0.40)
    trim_count = int(len(prices) * ratio)
    trimmed = prices[trim_count:len(prices) - trim_count] if trim_count else prices
    return MarketPriceStats(
        minimum=prices[0],
        median=float(median(prices)),
        trimmed_mean=round(float(fmean(trimmed)), 2),
        maximum=prices[-1],
        count=len(prices),
        market_range=(prices[0], prices[-1]),
    )


def analyze_market(offers: Iterable[Any], *, trim_ratio: float = 0.10) -> dict[ProductGroupKey, MarketPriceStats]:
    result: dict[ProductGroupKey, MarketPriceStats] = {}
    for key, group in group_offers(offers).items():
        stats = compute_market_price_stats(group, group_key=key, trim_ratio=trim_ratio)
        if stats is not None:
            result[key] = stats
    return result


class RecommendationRole(str, Enum):
    BEST_OVERALL = "BEST_OVERALL"
    CHEAP_WITH_RISK = "CHEAP_WITH_RISK"
    RELIABLE = "RELIABLE"


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    role: RecommendationRole
    offer: Any
    identity: OfferIdentity
    reason: str


def _score(offer: Any) -> float:
    facts = _facts(offer)
    raw = _field(offer, facts, "final_score", "score", default=0)
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _risk_items(offer: Any) -> list[str]:
    facts = _facts(offer)
    result: list[str] = []
    for value in (
        _field(offer, facts, "risk_flags", default=[]),
        facts.get("warnings", []),
        facts.get("final_reasons", []),
    ):
        if isinstance(value, (list, tuple, set)):
            result.extend(str(item).strip() for item in value if str(item).strip())
        elif _present(value):
            result.append(str(value).strip())
    seller_type = _normalize(_field(offer, facts, "seller_type", default=""))
    seller_trust = _normalize(_field(offer, facts, "seller_trust", default=""))
    condition = ProductGroupKey.from_offer(offer).condition
    if seller_type in {"private", "частный", "unknown", "неизвестный"}:
        result.append(f"seller_type:{seller_type}")
    if seller_trust in {"low", "unknown", "none", "низкий", "неизвестный"}:
        result.append(f"seller_trust:{seller_trust}")
    if condition in {"used", "refurbished"}:
        result.append(f"condition:{condition}")
    return list(dict.fromkeys(result))


def _is_reliable(offer: Any) -> bool:
    facts = _facts(offer)
    source = _normalize(_field(offer, facts, "source", "platform", default=""))
    if any(anchor in source for anchor in _RETAIL_ANCHORS):
        return True
    if bool(_field(offer, facts, "official_store", default=False)):
        return True
    platform_type = str(_field(offer, facts, "platform_type", default="") or "").upper()
    seller_trust = str(_field(offer, facts, "seller_trust", default="") or "").upper()
    seller_verified = bool(_field(offer, facts, "seller_verified", default=False)) or _manual_flag(facts, "seller")
    return platform_type == "RETAIL" or (seller_trust == "HIGH" and seller_verified)


def _role_sort_key(offer: Any, stats: MarketPriceStats | None) -> tuple[float, int, int, float]:
    price = _number(_field(offer, _facts(offer), "price", default=None)) or 10**12
    price_class = stats.classify(price) if stats else OfferPriceClass.UNKNOWN
    price_balance = int(price_class in {OfferPriceClass.BELOW_MARKET, OfferPriceClass.FAIR})
    return (_score(offer), price_balance, -len(_risk_items(offer)), -float(price))


def plan_recommendation_roles(
    offers: Iterable[Any],
    *,
    group_key: ProductGroupKey | None = None,
    market_stats: MarketPriceStats | None = None,
) -> list[RoleAssignment]:
    """Plan at most three unique, semantically justified recommendation roles."""
    rows = list(offers)
    if group_key is None:
        keys = {ProductGroupKey.from_offer(item) for item in rows}
        # Recommendation alternatives may be different models.  Unlike market
        # statistics, role planning may compare them, but it must not calculate
        # a shared median for mixed configurations.
        group_key = next(iter(keys), None) if len(keys) == 1 else None
    eligible = [item for item in rows if is_comparable_offer(item, group_key=group_key)]

    # A repeated DB row or tracking URL must not consume another role.
    best_by_identity: dict[OfferIdentity, Any] = {}
    for item in eligible:
        identity = OfferIdentity.from_offer(item)
        previous = best_by_identity.get(identity)
        if previous is None or _score(item) > _score(previous):
            best_by_identity[identity] = item
    pool = list(best_by_identity.values())
    if not pool:
        return []
    stats = market_stats
    if stats is None and group_key is not None:
        stats = compute_market_price_stats(pool, group_key=group_key)

    assignments: list[RoleAssignment] = []
    used: set[OfferIdentity] = set()
    best = max(pool, key=lambda item: _role_sort_key(item, stats))
    best_identity = OfferIdentity.from_offer(best)
    assignments.append(RoleAssignment(
        RecommendationRole.BEST_OVERALL, best, best_identity,
        "лучший баланс итогового score, цены и рисков",
    ))
    used.add(best_identity)

    best_price = _number(_field(best, _facts(best), "price", default=None))
    cheap_candidates = []
    for item in pool:
        identity = OfferIdentity.from_offer(item)
        price = _number(_field(item, _facts(item), "price", default=None))
        if identity in used or price is None or best_price is None or price >= best_price:
            continue
        price_risk = stats is not None and stats.classify(price) == OfferPriceClass.VERY_CHEAP
        if _risk_items(item) or price_risk:
            cheap_candidates.append(item)
    if cheap_candidates:
        cheap = min(cheap_candidates, key=lambda item: (
            _number(_field(item, _facts(item), "price", default=None)) or 10**12,
            -_score(item),
        ))
        identity = OfferIdentity.from_offer(cheap)
        assignments.append(RoleAssignment(
            RecommendationRole.CHEAP_WITH_RISK, cheap, identity,
            "самая низкая цена среди точных предложений с явно сохраненным риском",
        ))
        used.add(identity)

    reliable_candidates = [
        item for item in pool
        if OfferIdentity.from_offer(item) not in used and _is_reliable(item)
    ]
    if reliable_candidates:
        reliable = max(reliable_candidates, key=lambda item: _role_sort_key(item, stats))
        identity = OfferIdentity.from_offer(reliable)
        assignments.append(RoleAssignment(
            RecommendationRole.RELIABLE, reliable, identity,
            "крупная торговая сеть или подтвержденный надежный продавец",
        ))

    return assignments[:3]


__all__ = [
    "MarketPriceStats",
    "OfferIdentity",
    "OfferPriceClass",
    "ProductGroupKey",
    "RecommendationRole",
    "RoleAssignment",
    "analyze_market",
    "classify_offer_price",
    "compute_market_price_stats",
    "deduplicate_offers",
    "group_offers",
    "is_comparable_offer",
    "plan_recommendation_roles",
    "price_deviation_percent",
]
