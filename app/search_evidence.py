"""Product-card и source/verification confidence как независимые evidence."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProductCardAssessment:
    is_product_card: bool
    confidence: str
    reason: str


@dataclass(frozen=True)
class SourceTrustAssessment:
    source_confidence: str
    verification_confidence: str
    source_reason: str
    verification_reason: str
    platform_name: str = ""
    platform_type: str = "OTHER"
    platform_trust: str = "UNKNOWN"
    seller_trust: str = "UNKNOWN"
    seller_reason: str = "Данные конкретного продавца не получены"
    product_page_verified: bool = False
    exact_product_verified: bool = False
    price_verified: bool = False
    availability_verified: bool = False
    seller_verified: bool = False
    verification_access: str = "NOT_ATTEMPTED"


_NON_PRODUCT_PATHS = (
    "/search", "/catalog/", "/category/", "/categories/", "/brand/",
    "/collections/", "/blog/", "/article/", "/articles/", "/review/",
    "/reviews/", "/rating/", "/video/", "/sitemap", "/manual/",
    "/support/", "/compare/",
)
_PRODUCT_PATHS = (
    "/product/", "/products/", "/product--", "/card/", "/detail",
    "/details/", "/item/", "/goods/", "/offer/", "/p/",
)
_KNOWN_COMMERCE_DOMAINS = (
    "wildberries.ru", "ozon.ru", "market.yandex.ru", "avito.ru", "dns-shop.ru",
    "mvideo.ru", "citilink.ru", "megamarket.ru", "onlinetrade.ru", "eldorado.ru",
)

_PLATFORMS = {
    "dns": ("DNS", "RETAIL", "HIGH"),
    "citilink": ("Ситилинк", "RETAIL", "HIGH"),
    "mvideo": ("М.Видео", "RETAIL", "HIGH"),
    "ozon": ("Ozon", "MARKETPLACE", "HIGH"),
    "yandex_market": ("Яндекс Маркет", "MARKETPLACE", "HIGH"),
    "wildberries": ("Wildberries", "MARKETPLACE", "HIGH"),
    "avito": ("Avito", "CLASSIFIED", "CLASSIFIED"),
}
_ARTICLE_TITLE_MARKERS = (
    "обзор", "рейтинг", "топ ", "лучшие ", "как выбрать", "сравнение",
    "отзывы покупателей", "видеообзор", "инструкция", "manual", "review",
)


def _value(candidate: Any, name: str, default: Any = "") -> Any:
    if isinstance(candidate, dict):
        return candidate.get(name, default)
    return getattr(candidate, name, default)


def _facts(candidate: Any) -> dict[str, Any]:
    value = _value(candidate, "product_facts", {}) or _value(candidate, "facts", {}) or {}
    return value if isinstance(value, dict) else {}


def _platform(candidate: Any) -> tuple[str, str, str]:
    source = str(_value(candidate, "source", "") or "").lower()
    url = str(_value(candidate, "url", "") or "").lower()
    combined = f"{source} {url}"
    if "dns-shop.ru" in combined or re.search(r"(?:^|_)dns(?:_|$)", source):
        return _PLATFORMS["dns"]
    if "citilink.ru" in combined or "citilink" in source:
        return _PLATFORMS["citilink"]
    if "mvideo.ru" in combined or "mvideo" in source or "m_video" in source:
        return _PLATFORMS["mvideo"]
    if "ozon.ru" in combined or "ozon" in source:
        return _PLATFORMS["ozon"]
    if "market.yandex.ru" in combined or "yandex_market" in source or "market_yandex" in source:
        return _PLATFORMS["yandex_market"]
    if "wildberries.ru" in combined or "wildberries" in source or source == "wb":
        return _PLATFORMS["wildberries"]
    if "avito.ru" in combined or "avito" in source:
        return _PLATFORMS["avito"]
    return "", "OTHER", "UNKNOWN"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _seller_assessment(candidate: Any, platform_type: str) -> tuple[str, str, bool]:
    facts = _facts(candidate)
    seller = str(_value(candidate, "seller", "") or facts.get("seller") or "").strip()
    seller_type = str(facts.get("seller_type") or "").strip().lower()
    rating = _number(_value(candidate, "rating", None))
    if rating is None:
        rating = _number(facts.get("seller_rating") or facts.get("rating"))
    reviews = _number(_value(candidate, "reviews_count", None))
    if reviews is None:
        reviews = _number(facts.get("reviews_count"))
    verified = bool(seller or rating is not None or reviews is not None)

    if platform_type == "RETAIL":
        return "HIGH", "Продавцом является крупная торговая сеть", True
    if rating is not None and rating < 4.0:
        return "LOW", "Получен низкий рейтинг продавца", verified
    if reviews is not None and reviews < 5:
        return "LOW", "Получено мало отзывов о продавце", verified
    if rating is not None and reviews is not None and rating >= 4.7 and reviews >= 100:
        return "HIGH", "Высокий рейтинг и достаточное число отзывов", True
    if rating is not None and reviews is not None and rating >= 4.3 and reviews >= 20:
        return "MEDIUM", "Есть нормальный рейтинг и история отзывов", True
    if platform_type == "CLASSIFIED" and seller_type in {"shop", "professional", "магазин", "профессиональный"}:
        return "MEDIUM", "Объявление размещено магазином или профессиональным продавцом", verified
    return "UNKNOWN", "Надёжность конкретного продавца требует проверки", verified


def assess_product_card(candidate: Any) -> ProductCardAssessment:
    title = str(_value(candidate, "title", "") or "").strip()
    url = str(_value(candidate, "url", "") or "").strip()
    source = str(_value(candidate, "source", "") or "").lower()
    price_source = str(_value(candidate, "price_source", "") or "").lower()
    if not title or len(title) < 6 or not url:
        return ProductCardAssessment(False, "none", "нет title или URL")
    try:
        parsed = urlparse(url)
    except ValueError:
        return ProductCardAssessment(False, "none", "некорректный URL")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ProductCardAssessment(False, "none", "некорректный URL")
    path = parsed.path.lower().rstrip("/") + "/"
    title_text = title.lower().replace("ё", "е")
    if path.endswith((".pdf/", ".xml/", ".txt/")):
        return ProductCardAssessment(False, "none", "служебный документ")
    if any(marker in title_text for marker in _ARTICLE_TITLE_MARKERS):
        return ProductCardAssessment(False, "none", "статья/обзор, не карточка")
    has_product_path = any(marker in path for marker in _PRODUCT_PATHS)
    has_non_product_path = any(marker in path for marker in _NON_PRODUCT_PATHS)
    if has_non_product_path and not has_product_path:
        return ProductCardAssessment(False, "none", "search/category/brand page")
    if re.search(r"/(?:specifications?|characteristics?)/?$", path):
        return ProductCardAssessment(False, "none", "страница характеристик без покупки")

    domain = parsed.netloc.lower().removeprefix("www.")
    known_domain = any(domain == item or domain.endswith(f".{item}") for item in _KNOWN_COMMERCE_DOMAINS)
    structured = any(marker in price_source for marker in ("direct_store", "structured", "api", "json"))
    direct_source = any(marker in source for marker in ("direct_retail", "wildberries", "searchapi", "serpapi"))
    if structured and (has_product_path or known_domain):
        return ProductCardAssessment(True, "high", "structured product evidence")
    if has_product_path and known_domain:
        return ProductCardAssessment(True, "high", "точный product/detail URL")
    if has_product_path:
        return ProductCardAssessment(True, "medium", "product-like URL")
    if known_domain and direct_source:
        return ProductCardAssessment(True, "medium", "direct marketplace result")
    if known_domain and _value(candidate, "price", None) not in (None, "", 0):
        return ProductCardAssessment(True, "medium", "commerce listing с ценой")
    if _value(candidate, "price", None) not in (None, "", 0):
        return ProductCardAssessment(True, "low", "generic web с ценой")
    return ProductCardAssessment(False, "low", "generic web без сильных признаков карточки")


def assess_source_trust(candidate: Any, verify_status: str = "") -> SourceTrustAssessment:
    card = assess_product_card(candidate)
    source = str(_value(candidate, "source", "") or "").lower()
    status = str(verify_status or _value(candidate, "verify_status", "") or "").upper()
    price_source = str(_value(candidate, "price_source", "") or "").lower()
    facts = _facts(candidate)
    platform_name, platform_type, platform_trust = _platform(candidate)
    seller_trust, seller_reason, seller_verified = _seller_assessment(candidate, platform_type)

    if platform_trust in {"HIGH", "CLASSIFIED"}:
        source_confidence, source_reason = "high", f"известная площадка: {platform_name}"
    elif card.confidence == "high" and any(marker in price_source for marker in ("direct_store", "structured", "api", "json")):
        source_confidence, source_reason = "high", "structured product page/result"
    elif card.confidence in {"high", "medium"} and any(marker in source for marker in ("wildberries", "ozon", "market", "mvideo", "dns", "citilink", "direct")):
        source_confidence, source_reason = "medium", "known commerce product card"
    elif card.confidence == "medium":
        source_confidence, source_reason = "medium", card.reason
    else:
        source_confidence, source_reason = "low", card.reason

    blocked = status in {"VERIFY_BLOCKED", "VERIFY_ERROR"} or bool(facts.get("verification_access") == "BLOCKED")
    if status in {"VERIFIED_GOOD", "VERIFIED_OK", "UNAVAILABLE", "REMOVED_LISTING", "WRONG_PRODUCT", "NOT_PRODUCT_PAGE"}:
        verification_confidence, verification_reason = "high", "страница проверена"
        verification_access = "AVAILABLE"
    elif blocked:
        verification_confidence, verification_reason = "low", "страница заблокировала проверку"
        verification_access = "BLOCKED"
    elif status in {"NEED_MANUAL_CHECK", "PRICE_MISSING", "OVER_BUDGET_SOFT"}:
        verification_confidence, verification_reason = "low", "нужна ручная проверка"
        verification_access = str(facts.get("verification_access") or "NOT_ATTEMPTED")
    else:
        verification_confidence, verification_reason = "none", "проверка не выполнялась"
        verification_access = str(facts.get("verification_access") or "NOT_ATTEMPTED")

    manual = facts.get("manual_verified") if isinstance(facts.get("manual_verified"), dict) else {}
    exact_status = str(facts.get("exact_match") or facts.get("exact_match_status") or "").upper()
    exact_product_verified = bool(
        manual.get("model")
        or facts.get("exact_product_verified")
        or exact_status in {"EXACT", "COMPATIBLE_VARIANT"}
        or (exact_status == "GENERIC_MATCH" and not facts.get("exact_match_differences"))
    )
    product_page_verified = bool(manual.get("url") or facts.get("product_page_verified") or card.confidence in {"high", "medium"})
    strong_price_evidence = str(facts.get("price_evidence") or _value(candidate, "price_evidence", "") or "").lower()
    price_verified = bool(
        manual.get("price")
        or facts.get("price_verified")
        or (
            not blocked
            and _value(candidate, "price", None) not in (None, "", 0)
            and any(marker in strong_price_evidence for marker in ("direct", "structured", "currency"))
        )
    )
    available = facts.get("available")
    availability_verified = bool(
        manual.get("availability")
        or facts.get("availability_verified")
        or (not blocked and available in {True, False})
    )

    return SourceTrustAssessment(
        source_confidence,
        verification_confidence,
        source_reason,
        verification_reason,
        platform_name,
        platform_type,
        platform_trust,
        seller_trust,
        seller_reason,
        product_page_verified,
        exact_product_verified,
        price_verified,
        availability_verified,
        seller_verified,
        verification_access,
    )
