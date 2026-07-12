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
_ARTICLE_TITLE_MARKERS = (
    "обзор", "рейтинг", "топ ", "лучшие ", "как выбрать", "сравнение",
    "отзывы покупателей", "видеообзор", "инструкция", "manual", "review",
)


def _value(candidate: Any, name: str, default: Any = "") -> Any:
    if isinstance(candidate, dict):
        return candidate.get(name, default)
    return getattr(candidate, name, default)


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

    if card.confidence == "high" and any(marker in price_source for marker in ("direct_store", "structured", "api", "json")):
        source_confidence, source_reason = "high", "structured product page/result"
    elif card.confidence in {"high", "medium"} and any(marker in source for marker in ("wildberries", "ozon", "market", "mvideo", "dns", "citilink", "direct")):
        source_confidence, source_reason = "medium", "known commerce product card"
    elif card.confidence == "medium":
        source_confidence, source_reason = "medium", card.reason
    else:
        source_confidence, source_reason = "low", card.reason

    if status in {"VERIFIED_GOOD", "VERIFIED_OK", "UNAVAILABLE", "REMOVED_LISTING", "WRONG_PRODUCT", "NOT_PRODUCT_PAGE"}:
        verification_confidence, verification_reason = "high", "страница проверена"
    elif status in {"VERIFY_BLOCKED", "VERIFY_ERROR"}:
        verification_confidence, verification_reason = "low", "страница заблокировала проверку"
    elif status in {"NEED_MANUAL_CHECK", "PRICE_MISSING", "OVER_BUDGET_SOFT"}:
        verification_confidence, verification_reason = "low", "нужна ручная проверка"
    else:
        verification_confidence, verification_reason = "none", "проверка не выполнялась"

    return SourceTrustAssessment(
        source_confidence,
        verification_confidence,
        source_reason,
        verification_reason,
    )
