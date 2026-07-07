"""Проверка товарных кандидатов перед сохранением для админа."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import time
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

from app.net_client import fetch_http, get_domain_policy
from app.config import settings
from app.db import Request


VERIFIED_GOOD = "VERIFIED_GOOD"
VERIFIED_OK = "VERIFIED_OK"
PRICE_MISMATCH = "PRICE_MISMATCH"
PRICE_MISSING = "PRICE_MISSING"
UNAVAILABLE = "UNAVAILABLE"
REMOVED_LISTING = "REMOVED_LISTING"
NOT_PRODUCT_PAGE = "NOT_PRODUCT_PAGE"
WRONG_PRODUCT = "WRONG_PRODUCT"
BAD_ENCODING = "BAD_ENCODING"
REJECTED = "REJECTED"
VERIFY_ERROR = "VERIFY_ERROR"
VERIFY_BLOCKED = "VERIFY_BLOCKED"
NEED_MANUAL_CHECK = "NEED_MANUAL_CHECK"
OVER_BUDGET_SOFT = "OVER_BUDGET_SOFT"
OVER_BUDGET_HARD = "OVER_BUDGET_HARD"


@dataclass
class VerifiedCandidate:
    candidate: Any
    verify_status: str
    price: Optional[int] = None
    title: str = ""
    availability: str = ""
    reason: str = ""
    risk_flags: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    html_loaded: bool = False
    keep_for_admin: bool = False


UNAVAILABLE_MARKERS = (
    "товар закончился", "этот товар закончился", "нет в наличии",
    "товара нет в наличии", "товар недоступен", "данный товар недоступен",
    "нет в продаже", "снят с продажи", "распродан", "скоро снова поступит",
    "скоро поступит", "сообщить о поступлении", "out of stock", "not available",
)
REMOVED_LISTING_MARKERS = (
    "объявление снято с публикации", "объявление недоступно",
    "страница не найдена", "ошибка 404", "страница 404", "такой страницы нет",
)
AVAILABLE_MARKERS = (
    "в наличии", "доступен", "добавить в корзину", "в корзину", "купить",
    "оформить заказ", "доставка",
)
BAD_URL_PARTS = (
    "/search", "/catalog/0/search", "/category/", "/categories/", "/blog/",
    "/article/", "/articles/", "/reviews", "/review", "/otzyv", "/otzyvy",
    "/compare", "/support", "/manual", "/help",
)
AD_DOMAINS = (
    "bing.com", "googleadservices.com", "doubleclick.net",
    "googleads.g.doubleclick.net", "yabs.yandex.ru",
)
FOREIGN_SHOPS = (
    "amazon.", "ebay.", "walmart.com", "target.com", "bestbuy.com",
    "newegg.com", "alibaba.com", "aliexpress.com", "temu.com",
)
ARTICLE_WORDS = (
    "как выбрать", "лучшие", "топ-", "топ ", "обзор", "рейтинг",
    "подборка", "какой телевизор", "гайд",
)
TV_BRANDS = (
    "tcl", "hisense", "haier", "lg", "samsung", "xiaomi", "tuvio",
    "sber", "sony", "philips", "asano", "hyundai", "yandex", "яндекс",
)
TV_FEATURE_WORDS = ("4k", "uhd", "ultra hd", "qled", "oled", "hdr", "smart tv")
PS5_POSITIVE_WORDS = ("120 гц", "120hz", "144 гц", "144hz", "hdmi 2.1", "vrr", "mini led", "qled", "oled")
PS5_60HZ_RE = re.compile(r"\b60\s*(?:гц|hz)\b")
WRONG_TV_PRODUCT_WORDS = (
    "монитор", "проектор", "кронштейн", "пульт", "кабель", "подставка",
    "приставка", "консоль", "игровая приставка", "ps4", "xbox",
    "playstation", "dualshock", "gamepad", "nintendo", "ufc", "fifa",
    "igra", "console", "projector", "bracket", "remote", "cable",
)
TV_MODEL_RE = re.compile(
    r"\b(?:[A-Za-zА-Яа-я]{1,6}\d+[A-Za-zА-Яа-я][A-Za-zА-Яа-я0-9-]*|\d{2,}[A-Za-zА-Яа-я]{1,6}\d+[A-Za-zА-Яа-я0-9-]*)\b"
)
PRICE_BAD_CONTEXT = (
    "скидк", "бонус", "рассроч", "кредит", "платеж", "платёж",
    "в месяц", "ежемесяч", "кэшбек", "cashback", "балл", "отзыв",
    "вопрос", "артикул", "код товара", "модель", "диагонал", "дюйм",
    "герц", "гц", "hz", "3840", "2160", "доставк", "самовывоз",
    "гаранти", "эконом", "выгода",
)
PRICE_GOOD_CONTEXT = (
    "цена", "price", "товар", "product", "купить", "корзин",
    "sale", "current", "final", "card-price",
)
MOJIBAKE_MARKERS = ("Ð", "Ñ", "Ð¢", "Ðµ", "Рџ", "�")
TRUSTED_BLOCKED_SOURCES = {
    "yandex_market_search", "mvideo_search", "citilink_search",
    "wildberries", "dns_search", "ozon_search",
}
BRAND_NAMES = {
    "tcl": "TCL",
    "hisense": "Hisense",
    "haier": "Haier",
    "lg": "LG",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "tuvio": "Tuvio",
    "sber": "Sber",
    "sony": "Sony",
    "philips": "Philips",
    "asano": "Asano",
    "hyundai": "Hyundai",
    "yandex": "Yandex",
    "яндекс": "Яндекс",
    "starwind": "StarWind",
    "redmi": "Redmi",
}


def _timeout() -> int:
    return max(2, int(getattr(settings, "SEARCH_TIMEOUT_SECONDS", 8) or 8))


def _budget_value(req: Request) -> Optional[int]:
    try:
        value = int(str(req.budget).replace(" ", ""))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _source_from_url(url: str) -> str:
    domain = _domain(url)
    if "ozon.ru" in domain:
        return "ozon_search"
    if "market.yandex.ru" in domain:
        return "yandex_market_search"
    if "wildberries.ru" in domain:
        return "wildberries"
    if "avito.ru" in domain:
        return "avito_search"
    if "dns-shop.ru" in domain:
        return "dns_search"
    if "mvideo.ru" in domain:
        return "mvideo_search"
    if "citilink.ru" in domain:
        return "citilink_search"
    if "megamarket.ru" in domain:
        return "megamarket_search"
    return "generic_web"


def _normalise_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _has_bad_encoding(text: str) -> bool:
    if not text:
        return False
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return True
    return text.count("�") >= 2


def _repair_mojibake(text: str) -> tuple[str, bool]:
    """Возвращает исправленный текст и флаг оставшейся битой кодировки."""
    if not text or not _has_bad_encoding(text):
        return text or "", False
    attempts = []
    for encoding in ("latin1", "cp1251"):
        try:
            attempts.append(text.encode(encoding).decode("utf-8"))
        except UnicodeError:
            continue
    attempts.append(text)
    best = min(attempts, key=lambda value: sum(value.count(marker) for marker in MOJIBAKE_MARKERS))
    return best, _has_bad_encoding(best)


def _repair_candidate_text(candidate: Any) -> bool:
    bad = False
    for attr in ("title", "snippet"):
        value = str(getattr(candidate, attr, "") or "")
        fixed, still_bad = _repair_mojibake(value)
        if fixed and fixed != value:
            setattr(candidate, attr, _normalise_space(fixed))
        bad = bad or still_bad
    return bad


def _price_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        price = int(value)
    else:
        text = str(value)
        if re.search(r"\d+[,.]\d{1,2}$", text.strip()):
            text = re.sub(r"[,.]\d{1,2}$", "", text.strip())
        digits = re.sub(r"\D", "", text)
        if not digits:
            return None
        price = int(digits)
    return price if 1_000 <= price <= 10_000_000 else None


class _FetchResponse:
    def __init__(self, result):
        self.text = result.html or ""
        self.url = result.final_url or ""
        self.status_code = result.status_code
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.fetch_result = result


def _safe_get(url: str) -> _FetchResponse:
    result = fetch_http(url)

    if result.ok:
        return _FetchResponse(result)

    if result.blocked:
        response = requests.Response()
        response.status_code = result.status_code or 0
        response.url = result.final_url or url

        error = requests.HTTPError(
            f"{result.status_code or ''} blocked: {result.blocked_reason} for url: {url}".strip()
        )
        error.response = response
        error.fetch_result = result
        raise error

    error = requests.RequestException(result.error or result.blocked_reason or "HTTP-запрос не выполнен")
    error.fetch_result = result
    raise error


def _looks_like_product_url(url: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower().rstrip("/")
    query = parsed.query.lower()
    if not parsed.scheme or not parsed.netloc:
        return False
    if any(part in domain for part in AD_DOMAINS) or any(part in domain for part in FOREIGN_SHOPS):
        return False
    if any(part in path for part in BAD_URL_PARTS):
        return False
    if any(key in query for key in ("q=", "text=", "search=", "query=")):
        return False
    if "wildberries.ru" in domain:
        return "/catalog/" in path and "/detail" in path
    if "ozon.ru" in domain:
        return "/product/" in path or "/context/detail/id/" in path
    if "market.yandex.ru" in domain:
        return "/product--" in path or "/card/" in path or "/product/" in path
    if "dns-shop.ru" in domain:
        return "/product/" in path
    if "mvideo.ru" in domain:
        return "/products/" in path or "/product/" in path
    if "citilink.ru" in domain:
        return "/product/" in path
    if "megamarket.ru" in domain:
        return "/catalog/details/" in path or "/product/" in path
    if "avito.ru" in domain:
        return bool(re.search(r"_\d{5,}$", path))
    return False


def _page_title(soup: BeautifulSoup, fallback: str = "") -> str:
    for selector, attr in (
        ('meta[property="og:title"]', "content"),
        ('meta[name="title"]', "content"),
        ('h1', ""),
        ('title', ""),
    ):
        node = soup.select_one(selector)
        if not node:
            continue
        value = node.get(attr) if attr else node.get_text(" ", strip=True)
        if value:
            return _normalise_space(str(value))[:300]
    return fallback[:300]


def is_valid_product_page(url: str, html: str, title: str) -> bool:
    """Проверяет, что URL/HTML похожи на карточку товара, а не на статью или поиск."""
    if not _looks_like_product_url(url):
        return False
    parsed_path = urlparse(url).path.lower()
    title_text = _normalise_space(title or "").lower()
    if any(part in parsed_path for part in ("/article", "/articles", "/blog", "/journal")):
        return False
    if any(marker in title_text for marker in ("результаты поиска", "найдено товаров", "каталог товаров")):
        return False
    if any(word in title_text for word in ARTICLE_WORDS) and not any(token in title_text for token in ("купить", "цена", "телевизор")):
        return False
    return True


def _json_prices(value: Any) -> list[int]:
    prices: list[int] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"price", "lowprice", "highprice"}:
                price = _price_int(item)
                if price:
                    prices.append(price)
            elif lowered in {"offers", "aggregateoffer"} or isinstance(item, (dict, list)):
                prices.extend(_json_prices(item))
    elif isinstance(value, list):
        for item in value:
            prices.extend(_json_prices(item))
    return prices


def _extract_json_ld_prices(soup: BeautifulSoup) -> list[int]:
    prices: list[int] = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        prices.extend(_json_prices(data))
    return prices


def _extract_meta_prices(soup: BeautifulSoup) -> list[int]:
    selectors = [
        'meta[itemprop="price"]',
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        'meta[name="price"]',
    ]
    prices: list[int] = []
    for selector in selectors:
        for node in soup.select(selector):
            price = _price_int(node.get("content") or node.get("value"))
            if price:
                prices.append(price)
    return prices


def _context_price_candidates(text: str) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    patterns = [
        r"(?<!\d)(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{4,7})\s*(?:₽|руб\.?|рублей|рубля)",
        r"(?:₽|руб\.?|рублей|рубля)\s*(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{4,7})(?!\d)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            price = _price_int(match.group(1))
            if not price:
                continue
            start, end = match.span()
            context = text[max(0, start - 140): min(len(text), end + 140)].lower()
            if any(marker in context for marker in PRICE_BAD_CONTEXT):
                continue
            score = 1
            if any(marker in context for marker in PRICE_GOOD_CONTEXT):
                score += 3
            if "₽" in context or "руб" in context:
                score += 1
            candidates.append((score, price))
    return candidates


def extract_verified_price(source: str, html: str, text: str, title: str) -> int | None:
    """Достаёт цену осторожно: лучше None, чем цена из рассрочки/бонусов."""
    soup = BeautifulSoup(html or "", "html.parser")
    structured = _extract_meta_prices(soup) + _extract_json_ld_prices(soup)
    structured = [price for price in structured if 1_000 <= price <= 10_000_000]
    if structured:
        return sorted(structured)[0]

    normalized = _normalise_space(text or soup.get_text(" ", strip=True))
    candidates = _context_price_candidates(normalized)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_price = candidates[0]
    if best_score < 3:
        return None
    return best_price


def extract_availability(source: str, html: str, text: str) -> str:
    lowered = _normalise_space(text or BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)).lower()
    source = source or ""
    if any(marker in lowered for marker in REMOVED_LISTING_MARKERS):
        return REMOVED_LISTING
    if source == "avito_search" and any(marker in lowered for marker in (
        "объявление снято с публикации", "объявление недоступно", "страница не найдена", "продано",
    )):
        return REMOVED_LISTING
    if source == "megamarket_search":
        if any(marker in lowered for marker in (
            "нет в наличии", "сообщить о поступлении", "товар закончился", "данный товар недоступен",
        )):
            return "UNAVAILABLE"
        if "похожие товары" in lowered and not any(marker in lowered for marker in ("добавить в корзину", "в корзину", "купить")):
            return "UNAVAILABLE"
    if source == "mvideo_search" and any(marker in lowered for marker in (
        "товар закончился", "товара нет в наличии", "нет в наличии", "скоро поступит",
    )):
        return "UNAVAILABLE"
    if source == "citilink_search" and any(marker in lowered for marker in UNAVAILABLE_MARKERS):
        return "UNAVAILABLE"
    if any(marker in lowered for marker in UNAVAILABLE_MARKERS):
        return "UNAVAILABLE"
    if any(marker in lowered for marker in AVAILABLE_MARKERS):
        return "AVAILABLE"
    return "UNKNOWN"


def _budget_status(price: Optional[int], req: Request) -> str:
    budget = _budget_value(req)
    if price is None:
        return PRICE_MISSING
    if not budget:
        return ""
    if price <= budget:
        return "IN_BUDGET"
    if price <= budget * 1.15:
        return OVER_BUDGET_SOFT
    return OVER_BUDGET_HARD


def _extract_brand(text: str) -> str:
    lowered = text.lower()
    for key, display in BRAND_NAMES.items():
        if re.search(rf"(?<![a-zа-яё0-9]){re.escape(key)}(?![a-zа-яё0-9])", lowered):
            return display
    return ""


def _extract_model(text: str, brand: str = "") -> tuple[str, str]:
    matches = [match.group(0) for match in TV_MODEL_RE.finditer(text)]
    model_code = ""
    for value in matches:
        lower = value.lower()
        if lower in {"1080p"} or lower.endswith("hz") or lower.endswith("гц"):
            continue
        if value.lower() in {"fullhd"}:
            continue
        model_code = value
        break
    if not model_code:
        return "", ""
    model_key = model_code.upper()
    model = f"{brand} {model_key}".strip() if brand and brand.lower() not in model_key.lower() else model_key
    return model, model_key


def _extract_diagonal(text: str, model_key: str = "") -> str:
    patterns = [
        r"(?<!\d)(\d{2,3})\s*(?:\"|”|″|дюйм|дюймов|дюйма)",
        r"(?<!\d)(\d{2,3})\s*(?:см)?\)\s*телевизор",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if 24 <= value <= 100:
                return str(value)
    match = re.search(r"(?<!\d)(\d{2})(?=[A-ZА-Я])", model_key or "")
    if match:
        value = int(match.group(1))
        if 24 <= value <= 100:
            return str(value)
    return ""


def _extract_resolution(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("full hd", "fullhd", "1080p", "1920x1080", "1920 x 1080")):
        return "Full HD"
    if any(word in lowered for word in ("4k", "uhd", "ultra hd", "3840x2160", "3840 x 2160")):
        return "4K"
    return ""


def _extract_refresh_rate(text: str) -> str:
    match = re.search(r"\b(144|120|100|60|50)\s*(?:гц|hz)\b", text, flags=re.IGNORECASE)
    return f"{match.group(1)} Гц" if match else ""


def _extract_hdmi(text: str) -> str:
    lowered = text.lower()
    if re.search(r"hdmi\s*2[.,]1", lowered):
        return "HDMI 2.1"
    if "hdmi" in lowered:
        return "HDMI"
    return ""


def _extract_matrix_type(text: str) -> str:
    lowered = text.lower()
    if "mini led" in lowered or "mini-led" in lowered or "мини led" in lowered:
        return "Mini LED"
    if "oled" in lowered:
        return "OLED"
    if "qled" in lowered:
        return "QLED"
    if re.search(r"\bled\b", lowered):
        return "LED"
    return ""


def _extract_smart_tv(text: str) -> Any:
    lowered = text.lower()
    if "smart tv" in lowered or "смарт тв" in lowered or "смарт-тв" in lowered:
        return True
    return "unknown"


def _extract_os(text: str) -> str:
    lowered = text.lower()
    options = (
        ("google tv", "Google TV"),
        ("android tv", "Android TV"),
        ("tizen", "Tizen"),
        ("webos", "webOS"),
        ("web os", "webOS"),
        ("yaos", "YaOS"),
        ("яндекс тв", "Яндекс ТВ"),
        ("салют", "Салют ТВ"),
    )
    for marker, value in options:
        if marker in lowered:
            return value
    return ""


def _extract_rating_reviews(text: str) -> tuple[Optional[float], Optional[int]]:
    rating = None
    reviews = None
    rating_match = re.search(r"(?:рейтинг|rating)\D{0,20}([1-5](?:[.,]\d)?)", text, flags=re.IGNORECASE)
    if rating_match:
        try:
            rating = float(rating_match.group(1).replace(",", "."))
        except ValueError:
            rating = None
    reviews_match = re.search(r"(\d{1,6})\s*(?:отзыв|reviews?)", text, flags=re.IGNORECASE)
    if reviews_match:
        reviews = int(reviews_match.group(1))
    return rating, reviews


def _availability_bool(availability: str) -> Optional[bool]:
    if availability == "AVAILABLE":
        return True
    if availability in {"UNAVAILABLE", REMOVED_LISTING}:
        return False
    return None


def _category_from_text(text: str) -> str:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("ноутбук", "ноут", "laptop", "macbook")):
        return "laptop"
    if any(word in lowered for word in ("iphone", "айфон", "смартфон", "телефон")):
        return "phone"
    if any(word in lowered for word in ("наушник", "гарнитур", "headphone", "earbuds", "airpods")):
        return "headphones"
    if any(word in lowered for word in ("кресло", "стул", "chair")):
        return "chair"
    if any(word in lowered for word in ("телевизор", " smart tv", " tv ", " qled", " oled")):
        return "tv"
    return "unknown"


def detect_product_category(req: Request, candidate: Any = None, text: str = "") -> str:
    request_text = f"{req.product_name or req.product} {req.original_query}"
    request_category = _category_from_text(request_text)
    if request_category != "unknown":
        return request_category
    candidate_text = " ".join([
        str(getattr(candidate, "title", "") or ""),
        str(getattr(candidate, "snippet", "") or ""),
        text or "",
    ])
    return _category_from_text(candidate_text)


def extract_product_facts(
    candidate: Any,
    req: Request,
    html: str = "",
    text: str = "",
    title: str = "",
    price: Optional[int] = None,
    availability: str = "",
) -> dict[str, Any]:
    """Собирает структурированные факты товара без генерации описания."""
    soup_text = text or BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    combined = _normalise_space(
        " ".join([
            title or getattr(candidate, "title", "") or "",
            getattr(candidate, "snippet", "") or "",
            soup_text[:6000],
        ])
    )
    brand = _extract_brand(combined)
    model, model_key = _extract_model(combined, brand)
    diagonal = _extract_diagonal(combined, model_key)
    resolution = _extract_resolution(combined)
    refresh_rate = _extract_refresh_rate(combined)
    hdmi = _extract_hdmi(combined)
    matrix_type = _extract_matrix_type(combined)
    smart_tv = _extract_smart_tv(combined)
    os_name = _extract_os(combined)
    rating, reviews_count = _extract_rating_reviews(combined)
    actual_price = price if price is not None else getattr(candidate, "price", None)
    source = str(getattr(candidate, "source", "") or _source_from_url(getattr(candidate, "url", "")))
    url = str(getattr(candidate, "url", "") or "")
    category = detect_product_category(req, candidate, combined)
    if category != "tv":
        return {
            "category": category,
            "brand": brand,
            "model": model,
            "model_key": model_key,
            "price": actual_price,
            "store": source,
            "url": url,
            "available": _availability_bool(availability),
            "availability_text": availability or "UNKNOWN",
            "rating": rating,
            "reviews_count": reviews_count,
            "budget_status": _budget_status(actual_price, req),
            "warnings": [],
        }
    facts = {
        "category": category,
        "brand": brand,
        "model": model,
        "model_key": model_key,
        "diagonal": diagonal,
        "resolution": resolution,
        "refresh_rate": refresh_rate,
        "hdmi": hdmi,
        "matrix_type": matrix_type,
        "smart_tv": smart_tv,
        "os": os_name,
        "price": actual_price,
        "store": source,
        "url": url,
        "available": _availability_bool(availability),
        "availability_text": availability or "UNKNOWN",
        "rating": rating,
        "reviews_count": reviews_count,
        "budget_status": _budget_status(actual_price, req),
        "ps5_flags": [],
        "warnings": [],
    }
    if _is_ps5_tv_request(req):
        lowered = combined.lower()
        if resolution == "4K":
            facts["ps5_flags"].append("4K")
        if refresh_rate in {"120 Гц", "144 Гц"}:
            facts["ps5_flags"].append(refresh_rate)
        if hdmi == "HDMI 2.1":
            facts["ps5_flags"].append("HDMI 2.1")
        if "vrr" in lowered:
            facts["ps5_flags"].append("VRR")
        if matrix_type in {"QLED", "OLED", "Mini LED"}:
            facts["ps5_flags"].append(matrix_type)
        if refresh_rate == "60 Гц":
            facts["warnings"].append("60 Гц, для PS5 не идеал")
        if not refresh_rate:
            facts["warnings"].append("герцовка не подтверждена")
        if hdmi != "HDMI 2.1":
            facts["warnings"].append("HDMI 2.1 не подтверждён")
        if resolution == "Full HD":
            facts["warnings"].append("Full HD слабый вариант для PS5")
    facts["ps5_flags"] = list(dict.fromkeys(facts["ps5_flags"]))
    facts["warnings"] = list(dict.fromkeys(facts["warnings"]))
    return facts


def _is_tv_request(req: Request) -> bool:
    product = f"{req.product_name or req.product} {req.original_query}".lower()
    return "телевизор" in product or re.search(r"\btv\b", product) is not None


def _is_ps5_tv_request(req: Request) -> bool:
    text = f"{req.use_case or req.purpose} {req.original_query}".lower()
    return _is_tv_request(req) and "ps5" in text


def _wrong_product(candidate: Any, req: Request) -> bool:
    if not _is_tv_request(req):
        return False
    url = str(getattr(candidate, "url", "") or "")
    path_text = unquote(urlparse(url).path).replace("-", " ").replace("_", " ").lower()
    visible_text = f"{getattr(candidate, 'title', '')} {getattr(candidate, 'snippet', '')}".lower()
    text = f"{visible_text} {path_text}"
    has_tv_word = "телевизор" in text or re.search(r"\btv\b", visible_text) is not None or "smart tv" in text
    if has_tv_word:
        return False
    if any(word in text for word in WRONG_TV_PRODUCT_WORDS):
        return True
    has_brand = any(brand in text for brand in TV_BRANDS)
    has_tv_feature = any(feature in text for feature in TV_FEATURE_WORDS)
    has_model = TV_MODEL_RE.search(visible_text) is not None
    return not (has_brand and (has_tv_feature or has_model))


def classify_verified_candidate(candidate: Any, req: Request) -> str:
    status = getattr(candidate, "verify_status", "")
    if status:
        return status
    if _wrong_product(candidate, req):
        return WRONG_PRODUCT
    availability = getattr(candidate, "availability", "") or ""
    if availability == REMOVED_LISTING:
        return REMOVED_LISTING
    if availability == "UNAVAILABLE":
        return UNAVAILABLE
    source = str(getattr(candidate, "source", "") or _source_from_url(getattr(candidate, "url", "")))
    text = f"{getattr(candidate, 'title', '')} {getattr(candidate, 'snippet', '')}".lower()
    budget = _budget_value(req)
    if _is_ps5_tv_request(req) and budget and budget >= 25_000 and any(word in text for word in ("full hd", "fullhd", "фулл hd", "1080p")):
        return REJECTED
    price = getattr(candidate, "price", None)
    if price is None:
        return PRICE_MISSING
    if price and budget:
        if price > budget * 1.15:
            return OVER_BUDGET_HARD
        if price > budget:
            return OVER_BUDGET_SOFT
    if not _looks_like_product_url(getattr(candidate, "url", "")):
        return NOT_PRODUCT_PAGE
    if availability == "AVAILABLE":
        return VERIFIED_GOOD
    if source in {"citilink_search", "avito_search"} and availability == "UNKNOWN":
        return VERIFIED_OK
    return REJECTED


def _apply_verified(candidate: Any, verified: VerifiedCandidate) -> VerifiedCandidate:
    risks = list(getattr(candidate, "risk_flags", []) or [])
    risks.extend(verified.risk_flags)
    if verified.reason:
        risks.append(verified.reason)
    if verified.price is not None:
        risks = [
            risk for risk in risks
            if str(risk).strip() not in {"цена не подтверждена", "цена не найдена"}
        ]
    title_for_model = verified.title or getattr(candidate, "title", "")
    if title_for_model and TV_MODEL_RE.search(title_for_model):
        risks = [
            risk for risk in risks
            if str(risk).strip() not in {"нет признаков конкретной модели", "точная модель не указана"}
            and not str(risk).strip().startswith("классификация: WEAK — нет признаков конкретной модели")
        ]
    candidate.risk_flags = list(dict.fromkeys(str(item) for item in risks if str(item).strip()))
    if verified.title:
        candidate.title = verified.title[:300]
    if verified.price is not None:
        candidate.price = verified.price
        if not getattr(candidate, "price_source", ""):
            candidate.price_source = "requests" if verified.html_loaded else "search"
    candidate.availability = verified.availability
    if verified.facts:
        candidate.product_facts = verified.facts
        candidate.facts_json = json.dumps(verified.facts, ensure_ascii=False)
    candidate.description = getattr(candidate, "description", "") or getattr(candidate, "snippet", "")
    candidate.verify_status = verified.verify_status
    if verified.verify_status == VERIFIED_GOOD:
        candidate.quality = "GOOD"
        candidate.status = "CANDIDATE"
        candidate.score = min(float(getattr(candidate, "score", 0) or 0) + 15, 99)
    elif verified.verify_status in {VERIFIED_OK, OVER_BUDGET_SOFT, VERIFY_BLOCKED, NEED_MANUAL_CHECK}:
        candidate.quality = "OK"
        candidate.status = "CANDIDATE"
    elif verified.keep_for_admin:
        candidate.quality = "WEAK"
        candidate.status = "WEAK_CANDIDATE"
    else:
        candidate.quality = "TRASH"
        candidate.status = "REJECTED_AUTO"
    return verified


def apply_verified_candidate(candidate: Any, verified: VerifiedCandidate) -> VerifiedCandidate:
    return _apply_verified(candidate, verified)


def _is_blocked_error(exc: Exception) -> bool:
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code in {401, 403, 429, 498}:
            return True
    if isinstance(exc, requests.Timeout):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in (
        "401", "403", "429", "498", "forbidden", "unauthorized",
        "too many requests", "timeout", "timed out", "captcha",
        "access denied", "доступ запрещ", "robot", "anti-bot",
    ))


def _looks_safe_for_manual_check(candidate: Any, req: Request, source: str, url: str) -> bool:
    title = str(getattr(candidate, "title", "") or "").strip()
    price = getattr(candidate, "price", None)
    policy = get_domain_policy(url)
    if not title or not url:
        return False
    if not price and source != "avito_search":
        return False
    if source not in TRUSTED_BLOCKED_SOURCES and not policy.manual_check_friendly:
        return False
    if not _looks_like_product_url(url):
        return False
    lowered = title.lower()
    if any(marker in lowered for marker in ARTICLE_WORDS):
        return False
    if _wrong_product(candidate, req):
        return False
    try:
        price_value = int(price)
    except (TypeError, ValueError):
        return source == "avito_search"
    return 1_000 <= price_value <= 10_000_000


def verify_candidate(candidate: Any, req: Request) -> VerifiedCandidate:
    """Проверяет один кандидат. Любая ошибка превращается в VERIFY_ERROR."""
    url = str(getattr(candidate, "url", "") or "")
    source = str(getattr(candidate, "source", "") or _source_from_url(url))
    if _repair_candidate_text(candidate):
        verified = VerifiedCandidate(candidate, BAD_ENCODING, reason="битая кодировка title/snippet")
        return _apply_verified(candidate, verified)
    if not _looks_like_product_url(url):
        verified = VerifiedCandidate(candidate, NOT_PRODUCT_PAGE, reason="не карточка товара")
        return _apply_verified(candidate, verified)
    if _wrong_product(candidate, req):
        verified = VerifiedCandidate(candidate, WRONG_PRODUCT, reason="не тот товар")
        return _apply_verified(candidate, verified)

    try:
        response = _safe_get(url)
    except Exception as exc:    
        if _is_blocked_error(exc) and _looks_safe_for_manual_check(candidate, req, source, url):
            risks = [
                "страница заблокировала проверку",
                "цена найдена в выдаче, нужна ручная проверка",
            ]
            if source == "avito_search":
                risks.extend(["проверить продавца", "проверить город", "проверить состояние", "проверить отзывы"])
                if not req.is_used_allowed:
                    risks.append("б/у не разрешено клиентом")
            facts = extract_product_facts(
                candidate, req, title=getattr(candidate, "title", ""),
                price=getattr(candidate, "price", None), availability="UNKNOWN",
            )
            verified = VerifiedCandidate(
                candidate,
                VERIFY_BLOCKED,
                price=getattr(candidate, "price", None),
                title=getattr(candidate, "title", ""),
                availability="UNKNOWN",
                reason="страница заблокировала проверку",
                risk_flags=risks,
                facts=facts,
                html_loaded=False,
                keep_for_admin=True,
            )
            return _apply_verified(candidate, verified)
        # Если страница не открылась, не падаем, но не показываем как проверенный товар.
        facts = extract_product_facts(
            candidate, req, title=getattr(candidate, "title", ""),
            price=getattr(candidate, "price", None), availability="UNKNOWN",
        )
        verified = VerifiedCandidate(
            candidate,
            VERIFY_ERROR,
            price=getattr(candidate, "price", None),
            title=getattr(candidate, "title", ""),
            availability="UNKNOWN",
            reason=f"ошибка открытия сайта: {str(exc)[:180]}",
            risk_flags=["страница не проверена"],
            facts=facts,
            html_loaded=False,
            keep_for_admin=False,
        )
        return _apply_verified(candidate, verified)
    fetch_result = getattr(response, "fetch_result", None)
    if fetch_result is not None:
        candidate.used_proxy = fetch_result.used_proxy
        candidate.used_browser = fetch_result.used_browser
        candidate.fetch_status_code = fetch_result.status_code
        candidate.blocked_reason = fetch_result.blocked_reason
        candidate.fetch_provider = fetch_result.fetch_provider
        candidate.retry_count = fetch_result.retry_count
    html = response.text or ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    page_title = _page_title(soup, getattr(candidate, "title", ""))
    page_title, bad_title = _repair_mojibake(page_title)
    if bad_title:
        verified = VerifiedCandidate(candidate, BAD_ENCODING, title=page_title, reason="битая кодировка title", html_loaded=True)
        return _apply_verified(candidate, verified)
    if not is_valid_product_page(response.url or url, html, page_title):
        facts = extract_product_facts(candidate, req, html, text, page_title, price=getattr(candidate, "price", None), availability="UNKNOWN")
        verified = VerifiedCandidate(candidate, NOT_PRODUCT_PAGE, title=page_title, reason="не карточка товара", facts=facts, html_loaded=True)
        return _apply_verified(candidate, verified)

    availability = extract_availability(source, html, text)
    price = extract_verified_price(source, html, text, page_title)
    facts = extract_product_facts(candidate, req, html, text, page_title, price=price, availability=availability)
    risks: list[str] = []
    if availability == REMOVED_LISTING:
        verified = VerifiedCandidate(
            candidate, REMOVED_LISTING, price=price, title=page_title,
            availability=availability, reason="объявление/страница недоступны", facts=facts, html_loaded=True,
        )
        return _apply_verified(candidate, verified)
    if availability == "UNAVAILABLE":
        verified = VerifiedCandidate(
            candidate, UNAVAILABLE, price=price, title=page_title,
            availability=availability, reason="товар недоступен", facts=facts, html_loaded=True,
        )
        return _apply_verified(candidate, verified)
    if price is None:
        verified = VerifiedCandidate(
            candidate, PRICE_MISSING, title=page_title,
            availability=availability, reason="цена не подтверждена", facts=facts, html_loaded=True,
        )
        return _apply_verified(candidate, verified)

    old_price = getattr(candidate, "price", None)
    if old_price and price and abs(old_price - price) / max(price, 1) > 0.25:
        verified = VerifiedCandidate(
            candidate,
            PRICE_MISMATCH,
            price=price,
            title=page_title,
            availability=availability,
            reason=f"цена отличается от выдачи: было {old_price}, стало {price}",
            facts=facts,
            html_loaded=True,
        )
        return _apply_verified(candidate, verified)

    temp = type("VerifiedTemp", (), {})()
    temp.url = response.url or url
    temp.title = page_title or getattr(candidate, "title", "")
    temp.snippet = getattr(candidate, "snippet", "")
    temp.price = price
    temp.availability = availability
    temp.source = source
    temp.verify_status = ""
    verify_status = classify_verified_candidate(temp, req)

    if getattr(candidate, "source", "") == "avito_search" and verify_status == VERIFIED_GOOD:
        verify_status = VERIFIED_OK
        risks.extend(["проверить продавца", "проверить город", "проверить состояние", "проверить отзывы"])
        if not req.is_used_allowed:
            risks.append("б/у не разрешено клиентом")

    if _is_ps5_tv_request(req):
        combined = f"{page_title} {getattr(candidate, 'snippet', '')}".lower()
        if any(word in combined for word in PS5_POSITIVE_WORDS):
            risks.append("есть признаки, полезные для PS5")
        if PS5_60HZ_RE.search(combined):
            risks.append("60 Гц, для PS5 не идеал")

    keep = verify_status in {VERIFIED_GOOD, VERIFIED_OK, OVER_BUDGET_SOFT}
    if verify_status == OVER_BUDGET_HARD:
        risks.append("выше бюджета более чем на 15%")
    elif verify_status == OVER_BUDGET_SOFT:
        risks.append("чуть выше бюджета")
    verified = VerifiedCandidate(
        candidate,
        verify_status,
        price=price,
        title=page_title,
        availability=availability,
        reason="" if keep else verify_status,
        risk_flags=risks,
        facts=facts,
        html_loaded=True,
        keep_for_admin=keep,
    )
    return _apply_verified(candidate, verified)


def verify_candidates(candidates: list[Any], req: Request, limit: int = 30) -> list[VerifiedCandidate]:
    result: list[VerifiedCandidate] = []
    for candidate in candidates[:limit]:
        result.append(verify_candidate(candidate, req))
    return result
