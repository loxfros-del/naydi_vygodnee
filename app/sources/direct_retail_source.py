"""Direct structured retail sources for selected stores."""
from __future__ import annotations

from dataclasses import dataclass
import html
import json
import logging
import re
import time
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import quote_plus, urljoin, urlparse

from app.config import settings
from app.net_client import fetch_http
from app.price_extractor import extract_price
from app.price_guard import normalize_price_candidate

logger = logging.getLogger(__name__)

CITILINK_DIRECT_SOURCE = "citilink_direct"
DNS_DIRECT_SOURCE = "dns_direct"
MVIDEO_DIRECT_SOURCE = "mvideo_direct"
YANDEX_MARKET_DIRECT_SOURCE = "yandex_market_direct"
DIRECT_RETAIL_SOURCE = "direct_retail"


@dataclass(frozen=True)
class DirectRetailDefinition:
    key: str
    source: str
    store: str
    base_url: str


SOURCES: tuple[DirectRetailDefinition, ...] = (
    DirectRetailDefinition("dns", DNS_DIRECT_SOURCE, "DNS", "https://www.dns-shop.ru/search/?q={query}"),
    DirectRetailDefinition("citilink", CITILINK_DIRECT_SOURCE, "Citilink", "https://www.citilink.ru/search/?text={query}"),
    DirectRetailDefinition("mvideo", MVIDEO_DIRECT_SOURCE, "M.Video", "https://www.mvideo.ru/internal/search.jsp?q={query}"),
    DirectRetailDefinition("yandex_market", YANDEX_MARKET_DIRECT_SOURCE, "Yandex Market", "https://market.yandex.ru/search?text={query}"),
)

CATEGORY_TITLE_MARKERS = (
    "купить",
    "каталог",
    "маркетплейс",
    "интернет-магазин",
    "низкой цене",
    "результаты поиска",
)
ARTICLE_TITLE_MARKERS = (
    "обзор",
    "отзыв",
    "отзывы",
    "обсуждение",
    "коммуникатор",
    "рейтинг",
    "лучшие",
    "как выбрать",
)
URL_BLOCK_MARKERS = (
    "/search",
    "/catalog/",
    "/category/",
    "/reviews",
    "/review",
    "/compare",
    "/journal",
    "/blog",
    "/promo",
)


def _max_results() -> int:
    try:
        return max(1, min(int(settings.DIRECT_RETAIL_MAX_RESULTS or 5), 20))
    except (TypeError, ValueError):
        return 5


def _timeout_seconds() -> int:
    try:
        return max(2, int(settings.DIRECT_RETAIL_TIMEOUT_SECONDS or 15))
    except (TypeError, ValueError):
        return 15


def _parsed_namespace(parsed: dict | None) -> SimpleNamespace:
    parsed = parsed or {}
    return SimpleNamespace(
        budget=parsed.get("budget", ""),
        original_query=parsed.get("original_query", "") or parsed.get("query", ""),
        product_name=parsed.get("product_name", "") or parsed.get("product", ""),
        product=parsed.get("product", "") or parsed.get("product_name", ""),
        important_criteria=parsed.get("important_criteria", ""),
    )


def _source_by_key(key: str) -> DirectRetailDefinition:
    return next(item for item in SOURCES if item.key == key)


def _build_url(source: DirectRetailDefinition, query: str) -> str:
    return source.base_url.format(query=quote_plus(query))


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _as_int_price(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(round(value)) if value > 0 else None
    text = _clean_text(value)
    if not text:
        return None
    parsed = extract_price(text, min_price=1, max_price=10_000_000)
    if parsed is not None:
        return parsed
    if not re.search(r"(?:₽|руб(?:\.|лей|ля)?|р\.)", text, re.IGNORECASE):
        return None
    compact = text.replace("\u00a0", " ").replace(" ", "")
    match = re.search(r"\d[\d.,]*", compact)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(0))
    return int(digits) if digits else None


def _iter_json_ld(html_text: str) -> Iterable[Any]:
    pattern = re.compile(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html_text):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def _iter_embedded_json(html_text: str) -> Iterable[Any]:
    next_match = re.search(
        r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if next_match:
        try:
            yield json.loads(html.unescape(next_match.group(1)).strip())
        except json.JSONDecodeError:
            pass

    initial_match = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*({.*?})\s*;</script>",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if initial_match:
        try:
            yield json.loads(html.unescape(initial_match.group(1)).strip())
        except json.JSONDecodeError:
            pass


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _extract_offer(product: dict[str, Any]) -> dict[str, Any]:
    offers = product.get("offers") or product.get("offer") or {}
    if isinstance(offers, list):
        offers = next((item for item in offers if isinstance(item, dict)), {})
    return offers if isinstance(offers, dict) else {}


def _is_productish_dict(item: dict[str, Any]) -> bool:
    type_value = item.get("@type") or item.get("type")
    if isinstance(type_value, list):
        is_product = any(str(value).lower() == "product" for value in type_value)
    else:
        is_product = str(type_value or "").lower() == "product"
    has_title = _first_value(item, "name", "title", "productName")
    has_url = _first_value(item, "url", "link", "productUrl", "product_url", "href")
    offer = _extract_offer(item)
    has_price = _first_value(item, "price", "currentPrice", "salePrice", "value", "amount") or _first_value(offer, "price", "priceValue")
    return bool(is_product or (has_title and has_url and has_price))


def _extract_items_from_json(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in _walk(value):
        if not isinstance(node, dict):
            continue
        item = node.get("item")
        if isinstance(item, dict) and _is_productish_dict(item):
            rows.append(item)
        if _is_productish_dict(node):
            rows.append(node)
    return rows


def _extract_json_items(html_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in list(_iter_json_ld(html_text)) + list(_iter_embedded_json(html_text)):
        rows.extend(_extract_items_from_json(payload))
    return rows


def _extract_regex_items(html_text: str, source: DirectRetailDefinition) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    decoded = html.unescape(html_text)
    href_re = re.compile(r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.{0,900}?)</a>", re.IGNORECASE | re.DOTALL)
    for match in href_re.finditer(decoded):
        href = match.group("href")
        body = match.group("body")
        url = _absolute_url(href, source)
        if not _is_direct_product_url(url, source.source):
            continue
        price_match = re.search(r"(?P<price>\d[\d\s\u00a0]{2,12})(?:\s*)(?:₽|руб(?:\.|лей|ля)?|р\.)", body, re.IGNORECASE)
        if not price_match:
            continue
        title = _clean_text(body)
        title = re.sub(r"\d[\d\s\u00a0]{2,12}\s*(?:₽|руб(?:\.|лей|ля)?|р\.).*", "", title, flags=re.IGNORECASE).strip()
        rows.append({"title": title, "url": url, "price": price_match.group("price") + " ₽"})
    return rows


def _absolute_url(url: Any, source: DirectRetailDefinition) -> str:
    text = _clean_text(url)
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):
        return urljoin(_build_url(source, ""), text)
    return text


def _is_direct_product_url(url: str, source_name: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower().rstrip("/")
    query = parsed.query.lower()
    if not parsed.scheme or not parsed.netloc:
        return False
    if any(marker in path for marker in URL_BLOCK_MARKERS):
        return False
    if any(key in query for key in ("text=", "q=", "query=", "search=")):
        return False
    if source_name == CITILINK_DIRECT_SOURCE:
        return "citilink.ru" in domain and "/product/" in path
    if source_name == MVIDEO_DIRECT_SOURCE:
        return "mvideo.ru" in domain and ("/products/" in path or "/product/" in path)
    if source_name == YANDEX_MARKET_DIRECT_SOURCE:
        return "market.yandex.ru" in domain and ("/card/" in path or "/product--" in path or "/product/" in path)
    return False


def _bad_title(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in CATEGORY_TITLE_MARKERS + ARTICLE_TITLE_MARKERS)


def _item_to_candidate(item: dict[str, Any], source: DirectRetailDefinition, parsed: dict | None) -> dict | None:
    offer = _extract_offer(item)
    title = _clean_text(_first_value(item, "title", "name", "productName"))
    url = _absolute_url(_first_value(item, "url", "link", "productUrl", "product_url", "href") or _first_value(offer, "url"), source)
    if not title or not url or _bad_title(title) or not _is_direct_product_url(url, source.source):
        return None

    raw_price = _first_value(item, "price", "extracted_price", "currentPrice", "salePrice", "value", "amount")
    if raw_price is None:
        raw_price = _first_value(offer, "price", "priceValue", "lowPrice")
    price = _as_int_price(raw_price)
    if price is None:
        return None

    guard = normalize_price_candidate(
        None,
        _parsed_namespace(parsed),
        price=price,
        raw_price=price,
        price_source="direct_store",
        source=source.source,
        title=title,
        snippet=f"{source.store} {price} ₽",
        text=f"{title} {source.store} {price} ₽",
    )
    if guard.price is None or guard.price_reliability not in {"medium", "high"}:
        return None

    return {
        "title": title,
        "price": guard.price,
        "url": url,
        "source": source.source,
        "store": source.store,
        "seller": source.store,
        "price_source": "direct_store",
        "price_reliability": "medium",
        "availability": "UNKNOWN",
        "external_source": DIRECT_RETAIL_SOURCE,
        "price_rejected_reason": guard.price_rejected_reason,
        "price_from_budget_suspect": guard.price_from_budget_suspect,
        "bad_price_context": guard.bad_price_context,
        "raw": item,
        "body": f"{source.store} {guard.price} ₽",
    }


def _fetch_source(source: DirectRetailDefinition, query: str) -> tuple[str, dict[str, Any]]:
    url = _build_url(source, query)
    started = time.monotonic()
    result = fetch_http(url, timeout=_timeout_seconds(), retries=0, use_cache=True)
    elapsed = round(time.monotonic() - started, 3)
    debug = {
        "source": source.source,
        "store": source.store,
        "url": url,
        "status": "ok" if result.ok else result.blocked_reason or "error",
        "elapsed": elapsed,
        "status_code": result.status_code,
        "raw_count": 0,
        "candidates_count": 0,
        "error": result.error,
    }
    return result.html or "", debug


def debug_search_direct_retail_source(
    source: DirectRetailDefinition,
    query: str,
    parsed: dict | None = None,
) -> dict[str, Any]:
    html_text, debug = _fetch_source(source, query)
    rows = _extract_json_items(html_text)
    rows.extend(_extract_regex_items(html_text, source))
    debug["raw_count"] = len(rows)

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in rows:
        candidate = _item_to_candidate(item, source, parsed)
        if not candidate:
            continue
        key = f"{candidate['url']}|{candidate['title'].lower()}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
        if len(candidates) >= _max_results():
            break
    debug["candidates"] = candidates
    debug["candidates_count"] = len(candidates)
    if not candidates and debug["status"] == "ok":
        debug["status"] = "empty"
    return debug


def _search_one(key: str, query: str, parsed: dict | None = None) -> list[dict]:
    if not settings.DIRECT_RETAIL_ENABLED:
        return []
    try:
        debug = debug_search_direct_retail_source(_source_by_key(key), query, parsed)
    except Exception as exc:
        logger.debug("Direct retail source failed: source=%s query=%r error=%s", key, query, exc)
        return []
    return list(debug.get("candidates") or [])


def search_citilink_direct(query: str, parsed: dict | None = None) -> list[dict]:
    return _search_one("citilink", query, parsed)


def search_dns_direct(query: str, parsed: dict | None = None) -> list[dict]:
    return _search_one("dns", query, parsed)


def search_mvideo_direct(query: str, parsed: dict | None = None) -> list[dict]:
    return _search_one("mvideo", query, parsed)


def search_yandex_market_direct(query: str, parsed: dict | None = None) -> list[dict]:
    return _search_one("yandex_market", query, parsed)


def search_direct_retail_sources(query: str, parsed: dict | None = None) -> list[dict]:
    if not settings.DIRECT_RETAIL_ENABLED:
        return []
    rows: list[dict] = []
    for source in SOURCES:
        try:
            rows.extend(debug_search_direct_retail_source(source, query, parsed).get("candidates") or [])
        except Exception as exc:
            logger.debug("Direct retail source failed: source=%s query=%r error=%s", source.source, query, exc)
    return rows


def debug_search_direct_retail_sources(query: str, parsed: dict | None = None) -> list[dict[str, Any]]:
    if not settings.DIRECT_RETAIL_ENABLED:
        return [
            {
                "source": source.source,
                "store": source.store,
                "status": "disabled",
                "elapsed": 0.0,
                "raw_count": 0,
                "candidates_count": 0,
                "candidates": [],
            }
            for source in SOURCES
        ]
    result: list[dict[str, Any]] = []
    for source in SOURCES:
        try:
            result.append(debug_search_direct_retail_source(source, query, parsed))
        except Exception as exc:
            result.append({
                "source": source.source,
                "store": source.store,
                "status": "error",
                "elapsed": 0.0,
                "raw_count": 0,
                "candidates_count": 0,
                "candidates": [],
                "error": str(exc)[:180],
            })
    return result
