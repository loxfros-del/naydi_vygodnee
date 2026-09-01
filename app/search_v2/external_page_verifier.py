"""Fail-closed verification for product links discovered by web search.

Web discovery snippets are only hints.  This verifier fetches one direct page
and replaces a snippet price only when the fetched page exposes both a product
signal and a structured price.  It deliberately has no persistence layer.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from html.parser import HTMLParser
import inspect
import ipaddress
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from app.net_client import fetch_http

from .models import AvailabilityInfo, AvailabilityStatus, Offer, VerificationAccess
from .verification import apply_automatic_verification


_MAX_PRICE = 10_000_000.0
_MAX_HTML_CHARS = 750_000
_MAX_TEXT_CHARS = 120_000
_SEARCH_QUERY_KEYS = {"q", "query", "text", "search", "keyword", "search_query", "psearch"}
_SEARCH_PATH_MARKERS = ("/search", "/find", "/results", "/result", "/search-results")
_ARTICLE_PATH_MARKERS = ("/article", "/articles", "/blog", "/journal", "/news", "/review", "/reviews")
_CATEGORY_PATH_MARKERS = ("/catalog", "/category", "/categories", "/collection", "/collections", "/listing")
_PRODUCT_PATH_MARKERS = ("/product", "/products", "/item", "/items", "/card", "/detail", "/details", "/offer", "/goods", "/sku", "/dp/")
_WILDBERRIES_PRODUCT_PATH_RE = re.compile(r"^/catalog/\d+/detail\.aspx$")
_OUT_OF_STOCK_MARKERS = (
    "outofstock", "out of stock", "soldout", "sold out", "нет в наличии", "товар закончился",
    "распродано", "недоступен",
)
_PREORDER_MARKERS = ("preorder", "pre-order", "предзаказ")
_IN_STOCK_MARKERS = ("instock", "in stock", "в наличии", "available", "доступен", "добавить в корзину")
_ACCESSORY_PAGE_MARKERS = (
    "аксессуары для наушников", "чехлы для наушников", "чехол для наушников",
    "чехлы для гарнитур", "cases for headphones",
)
_MODEL_FORM_FACTOR_CONFLICTS = {
    # These are full-size headphones. A neckband page with the same code in
    # its title is a misleading marketplace card, not an alternative model.
    "wh 1000xm": ("шейным ободом", "шейный обод", "neckband"),
}


def _clean_text(value: Any, *, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _price(value: Any) -> float | None:
    """Parse one structured price, rejecting ranges and implausible values."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = _clean_text(value, limit=80).replace("\u00a0", " ")
        if not text:
            return None
        # Ignore a terminal decimal component, but preserve thousands separators.
        text = re.sub(r"([,.])\d{1,2}\s*(?:₽|руб(?:\.|лей|ля)?|RUB)?$", "", text, flags=re.IGNORECASE)
        digits = re.sub(r"\D", "", text)
        if not digits:
            return None
        try:
            number = float(digits)
        except ValueError:
            return None
    if not math.isfinite(number) or not 0 < number <= _MAX_PRICE:
        return None
    return float(round(number, 2))


def _hostname_is_public(hostname: str) -> bool:
    host = str(hostname or "").strip().casefold().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not any((address.is_private, address.is_loopback, address.is_link_local, address.is_multicast,
                    address.is_reserved, address.is_unspecified))


def is_direct_product_candidate_url(value: Any) -> bool:
    """Return whether a URL can safely be fetched as a direct product candidate.

    This intentionally rejects search, category, article and Yandex search
    pages before any HTTP request.  A product page may still fail later if its
    HTML lacks structured product and price evidence.
    """
    raw = str(value or "").strip()
    if not raw or len(raw) > 2_048:
        return False
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return False
    if parsed.scheme.casefold() not in {"http", "https"} or not _hostname_is_public(parsed.hostname or ""):
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    path = unquote(parsed.path or "/").casefold().rstrip("/") or "/"
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & _SEARCH_QUERY_KEYS:
        return False
    if host.endswith("yandex.ru") and not host.endswith("market.yandex.ru"):
        return False
    if any(marker in path for marker in _SEARCH_PATH_MARKERS + _ARTICLE_PATH_MARKERS):
        return False
    if path == "/":
        return False
    if host == "wildberries.ru" or host.endswith(".wildberries.ru"):
        return bool(_WILDBERRIES_PRODUCT_PATH_RE.fullmatch(path))
    parts = [part for part in path.split("/") if part]
    is_category_path = any(path == marker or path.startswith(f"{marker}/") for marker in _CATEGORY_PATH_MARKERS)
    has_product_marker = any(marker in path for marker in _PRODUCT_PATH_MARKERS)
    # /catalog/phones is a category; /catalog/phones/product-slug can be a product.
    if is_category_path and not has_product_marker and len(parts) <= 2:
        return False
    if host.endswith("market.yandex.ru") and not any(marker in path for marker in ("/product--", "/product/", "/card/")):
        return False
    return True


class _ProductPageParser(HTMLParser):
    """Small bounded HTML collector; it never executes page scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.og_title = ""
        self.meta_prices: list[tuple[float, str]] = []
        self.availability_values: list[str] = []
        self.json_ld: list[str] = []
        self.product_markup = False
        self.text_parts: list[str] = []
        self._capture: str = ""
        self._script_is_json_ld = False
        self._script_parts: list[str] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {str(key or "").casefold(): str(value or "") for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = self._attrs(attrs)
        itemprop = values.get("itemprop", "").casefold()
        itemtype = values.get("itemtype", "").casefold()
        if "schema.org/product" in itemtype or itemprop in {"product", "productid", "sku", "name"}:
            self.product_markup = True
        if itemprop in {"price", "lowprice"}:
            price = _price(values.get("content") or values.get("value"))
            if price is not None:
                self.meta_prices.append((price, "meta:itemprop"))
        if itemprop == "availability":
            self.availability_values.append(values.get("content") or values.get("href") or values.get("value") or "")
        if tag == "meta":
            property_name = values.get("property", "").casefold()
            name = values.get("name", "").casefold()
            content = values.get("content", "")
            if property_name == "og:type" and "product" in content.casefold():
                self.product_markup = True
            if property_name in {"og:title", "twitter:title"} or name == "title":
                if content and not self.og_title:
                    self.og_title = _clean_text(content)
            if property_name in {"product:price:amount", "og:price:amount"} or name in {"price", "product:price"}:
                price = _price(content)
                if price is not None:
                    self.meta_prices.append((price, f"meta:{property_name or name}"))
                    self.product_markup = True
            if property_name in {"product:availability", "og:availability"} or name == "availability":
                self.availability_values.append(content)
        if tag in {"title", "h1"}:
            self._capture = tag
        elif tag == "script":
            script_type = values.get("type", "").casefold()
            self._script_is_json_ld = "ld+json" in script_type
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_is_json_ld:
            if sum(len(part) for part in self._script_parts) < _MAX_HTML_CHARS:
                self._script_parts.append(data)
            return
        text = _clean_text(data, limit=2_000)
        if not text:
            return
        if self._capture == "title":
            self.title_parts.append(text)
        elif self._capture == "h1":
            self.h1_parts.append(text)
        if sum(len(part) for part in self.text_parts) < _MAX_TEXT_CHARS:
            self.text_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "script":
            if self._script_is_json_ld:
                raw = "".join(self._script_parts).strip()
                if raw:
                    self.json_ld.append(raw[:_MAX_HTML_CHARS])
            self._script_is_json_ld = False
            self._script_parts = []
        if tag == self._capture:
            self._capture = ""


def _types(value: Any) -> set[str]:
    raw = value if isinstance(value, list) else [value]
    result: set[str] = set()
    for item in raw:
        text = str(item or "").casefold().rstrip("/")
        if text:
            result.add(text.rsplit("/", 1)[-1])
    return result


def _product_nodes(value: Any) -> Iterable[Mapping[str, Any]]:
    """Yield explicit Product nodes from JSON-LD graphs without list-page guesses."""
    if isinstance(value, list):
        for item in value:
            yield from _product_nodes(item)
        return
    if not isinstance(value, Mapping):
        return
    if _types(value.get("@type")) & {"product", "productmodel"}:
        yield value
    for key in ("@graph", "mainEntity", "mainEntityOfPage"):
        child = value.get(key)
        if isinstance(child, (Mapping, list)):
            yield from _product_nodes(child)


def _mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                yield item


def _price_from_offer_data(value: Any) -> tuple[float | None, str]:
    for offer in _mappings(value):
        price = _price(offer.get("price"))
        if price is not None:
            return price, "json_ld:offer.price"
        for specification in _mappings(offer.get("priceSpecification")):
            price = _price(specification.get("price"))
            if price is not None:
                return price, "json_ld:priceSpecification.price"
    return None, ""


@dataclass(frozen=True)
class _PageEvidence:
    title: str = ""
    price: float | None = None
    price_source: str = ""
    availability: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    availability_text: str = ""
    product_signal: bool = False
    page_text: str = ""


def _availability_status(values: Iterable[Any]) -> tuple[AvailabilityStatus, str]:
    text = " ".join(_clean_text(value, limit=400).casefold() for value in values if value)
    if any(marker in text for marker in _OUT_OF_STOCK_MARKERS):
        return AvailabilityStatus.OUT_OF_STOCK, "unavailable"
    if any(marker in text for marker in _PREORDER_MARKERS):
        return AvailabilityStatus.PREORDER, "preorder"
    if any(marker in text for marker in _IN_STOCK_MARKERS):
        return AvailabilityStatus.IN_STOCK, "available"
    return AvailabilityStatus.UNKNOWN, "unknown"


def _parse_page(html: str, fallback_title: str) -> _PageEvidence:
    parser = _ProductPageParser()
    try:
        parser.feed((html or "")[:_MAX_HTML_CHARS])
        parser.close()
    except (ValueError, RuntimeError):
        return _PageEvidence()

    product_title = ""
    price: float | None = None
    price_source = ""
    availability_values = list(parser.availability_values)
    product_signal = parser.product_markup
    for raw in parser.json_ld:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for product in _product_nodes(data):
            product_signal = True
            if not product_title:
                product_title = _clean_text(product.get("name"))
            if price is None:
                price, price_source = _price_from_offer_data(product.get("offers"))
            for offer_data in _mappings(product.get("offers")):
                availability_values.append(str(offer_data.get("availability") or ""))

    if price is None and parser.meta_prices:
        price, price_source = parser.meta_prices[0]
    title = product_title or parser.og_title or _clean_text(" ".join(parser.h1_parts)) or _clean_text(" ".join(parser.title_parts)) or _clean_text(fallback_title)
    availability, availability_text = _availability_status([*availability_values, " ".join(parser.text_parts)])
    return _PageEvidence(
        title=title,
        price=price,
        price_source=price_source,
        availability=availability,
        availability_text=availability_text,
        product_signal=product_signal,
        page_text=_clean_text(" ".join(parser.text_parts), limit=_MAX_TEXT_CHARS),
    )


def _fetch_field(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


class ExternalProductPageVerifier:
    """Verify one generic/Yandex web-discovery Offer against its direct page.

    ``fetcher`` follows :func:`app.net_client.fetch_http` and is injected in
    tests.  One call is made per offer with zero retries; callers should still
    use ``verify_offer_pages`` for the global two-page concurrency ceiling.
    """

    def __init__(
        self,
        fetcher: Callable[..., Any] | None = None,
        *,
        timeout: float = 6.0,
        max_html_chars: int = _MAX_HTML_CHARS,
    ) -> None:
        self.fetcher = fetcher or fetch_http
        self.timeout = max(2.0, min(float(timeout or 6.0), 10.0))
        self.max_html_chars = max(10_000, min(int(max_html_chars or _MAX_HTML_CHARS), _MAX_HTML_CHARS))

    async def __call__(self, offer: Offer) -> Offer:
        return await self.verify(offer)

    async def _fetch(self, url: str) -> Any:
        result = await asyncio.wait_for(
            asyncio.to_thread(self.fetcher, url, timeout=int(math.ceil(self.timeout)), retries=0, use_cache=True),
            timeout=self.timeout + 1.0,
        )
        if inspect.isawaitable(result):
            return await asyncio.wait_for(result, timeout=self.timeout)
        return result

    @staticmethod
    def _metadata(offer: Offer) -> dict[str, Any]:
        return dict(offer.raw_metadata) if isinstance(offer.raw_metadata, dict) else {}

    @staticmethod
    def _wrong_product_page(offer: Offer, evidence: _PageEvidence) -> str:
        text = " ".join((evidence.title, evidence.page_text)).casefold().replace("ё", "е")
        if any(marker in text for marker in _ACCESSORY_PAGE_MARKERS):
            return "accessory_product_page"
        model = str(getattr(getattr(offer, "identity", None), "canonical_model", "") or "").casefold()
        model = re.sub(r"[^0-9a-zа-я]+", " ", model)
        for model_marker, conflicts in _MODEL_FORM_FACTOR_CONFLICTS.items():
            if model_marker in model and any(marker in text for marker in conflicts):
                return "wrong_product_form_factor"
        return ""

    def _unverified(self, offer: Offer, reason: str, *, blocked: bool = False) -> Offer:
        metadata = self._metadata(offer)
        metadata.update({
            "not_product_page": True,
            "product_page_verified": False,
            "external_page_verified": False,
            "price_verified": False,
            "availability_verified": False,
            "availability": "unknown",
            "external_page_verification": {"verified": False, "reason": reason},
        })
        warnings = list(metadata.get("verification_warnings") or [])
        if "NOT_PRODUCT_PAGE" not in warnings:
            warnings.append("NOT_PRODUCT_PAGE")
        metadata["verification_warnings"] = warnings
        result = replace(
            offer,
            price=None,
            old_price=None,
            price_confidence=0.0,
            availability=AvailabilityInfo(),
            availability_confidence=0.0,
            verification_access=VerificationAccess.BLOCKED if blocked else VerificationAccess.PARTIAL,
            raw_metadata=metadata,
        )
        return apply_automatic_verification(result)

    def _verified(self, offer: Offer, evidence: _PageEvidence, final_url: str, status_code: Any) -> Offer:
        metadata = self._metadata(offer)
        metadata.pop("not_product_page", None)
        availability_verified = evidence.availability is not AvailabilityStatus.UNKNOWN
        metadata.update({
            "product_page_verified": True,
            "external_page_verified": True,
            "price_verified": True,
            "availability_verified": availability_verified,
            "availability": evidence.availability_text,
            "external_page_verification": {
                "verified": True,
                "price_source": evidence.price_source,
                "status_code": int(status_code) if isinstance(status_code, int) else None,
            },
        })
        availability = AvailabilityInfo(
            status=evidence.availability,
            available=True if evidence.availability is AvailabilityStatus.IN_STOCK else (
                False if evidence.availability is AvailabilityStatus.OUT_OF_STOCK else None
            ),
            source_text=evidence.availability_text,
            confidence=0.9 if availability_verified else 0.0,
        )
        result = replace(
            offer,
            title=evidence.title or offer.title,
            url=final_url,
            price=evidence.price,
            price_confidence=max(float(offer.price_confidence or 0.0), 0.9),
            availability=availability,
            availability_confidence=availability.confidence,
            verification_access=VerificationAccess.FULL,
            raw_metadata=metadata,
        )
        return apply_automatic_verification(result)

    async def verify(self, offer: Offer) -> Offer:
        """Fetch one page and fail closed so a web snippet price cannot leak through."""
        if not isinstance(offer, Offer):
            raise TypeError("ExternalProductPageVerifier expects an Offer")
        if not is_direct_product_candidate_url(offer.url):
            return self._unverified(offer, "not_direct_product_url")
        try:
            fetched = await self._fetch(offer.url)
        except (asyncio.TimeoutError, TimeoutError):
            return self._unverified(offer, "fetch_timeout")
        except Exception:
            return self._unverified(offer, "fetch_error")
        if not bool(_fetch_field(fetched, "ok", False)):
            return self._unverified(offer, "fetch_failed", blocked=bool(_fetch_field(fetched, "blocked", False)))

        final_url = str(_fetch_field(fetched, "final_url", "") or offer.url).strip()
        if not is_direct_product_candidate_url(final_url):
            return self._unverified(offer, "redirected_to_non_product_page")
        try:
            parsed = urlsplit(final_url)
            final_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        except ValueError:
            return self._unverified(offer, "invalid_final_url")
        html = str(_fetch_field(fetched, "html", "") or "")[: self.max_html_chars]
        evidence = _parse_page(html, offer.title)
        if not evidence.product_signal:
            return self._unverified(offer, "missing_product_signal")
        if evidence.price is None:
            return self._unverified(offer, "missing_structured_price")
        wrong_product = self._wrong_product_page(offer, evidence)
        if wrong_product:
            return self._unverified(offer, wrong_product)
        return self._verified(offer, evidence, final_url, _fetch_field(fetched, "status_code"))


_EXTERNAL_DISCOVERY_SOURCES = {
    "generic_exact", "generic_search", "generic_web", "yandex_web", "wildberries",
}
_SEARCH_CARD_SOURCES = {"dns", "citilink", "mvideo", "yandex_market"}


def needs_external_page_verification(offer: Offer) -> bool:
    """Return whether a raw card still needs direct-page price and stock proof."""
    metadata = offer.raw_metadata if isinstance(offer.raw_metadata, dict) else {}
    if bool(metadata.get("external_page_verified")):
        return False
    if bool(metadata.get("page_verification_required")):
        return True
    source = str(offer.source or "").casefold()
    if source in _EXTERNAL_DISCOVERY_SOURCES:
        return True
    # Direct-retail adapters currently parse public search result cards.  A
    # card price is useful discovery evidence, but it is not current stock or
    # product-page proof until this verifier opens the direct URL.
    return source in _SEARCH_CARD_SOURCES


def verify_external_offer_if_needed(
    offer: Offer,
    verifier: ExternalProductPageVerifier,
) -> Offer | Any | None:
    """Invoke a verifier only for discovery/search-card offers."""
    return verifier(offer) if needs_external_page_verification(offer) else None


__all__ = [
    "ExternalProductPageVerifier",
    "is_direct_product_candidate_url",
    "needs_external_page_verification",
    "verify_external_offer_if_needed",
]
