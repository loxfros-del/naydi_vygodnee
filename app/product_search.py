"""Автопоиск v2: собирает карточки товаров из нескольких независимых источников.

Поиск никогда не считает ссылку на страницу поиска товаром. Если источники
недоступны, диагностическая информация и ручные ссылки сохраняются отдельно.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import re
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlparse, urlunparse, urlencode

import requests

from app.config import settings
from app.candidate_verifier import (
    BAD_ENCODING,
    NEED_MANUAL_CHECK,
    NOT_PRODUCT_PAGE,
    OVER_BUDGET_SOFT,
    PRICE_MISSING,
    REMOVED_LISTING,
    UNAVAILABLE,
    VERIFIED_GOOD,
    VERIFIED_OK,
    VERIFY_BLOCKED,
    detect_product_category,
    verify_candidate,
    verify_candidates,
)
from app.db import (
    Request,
    create_search_attempt,
    create_search_result,
    get_search_results,
    replace_manual_search_links,
)
from app.net_client import FetchResult, fetch_http
from app.playwright_verifier import verify_with_playwright_fallback
from app.price_guard import (
    TOO_CHEAP_FOR_BUDGET_RISK,
    apply_price_rank_penalties,
    normalize_price_candidate,
)
from app.product_quality import (
    BAD_PRODUCT_RISK,
    DIRECT_QUALITY_MANUAL_RISK,
    DIRECT_RETAIL_USED_RISK,
    headphone_brand_quality as quality_headphone_brand_quality,
    direct_product_quality_level as quality_direct_product_quality_level,
    final_product_quality_level as quality_final_product_quality_level,
)
from app.price_extractor import extract_price
from app.search_links import build_search_query, generate_search_links
from app.sources.direct_retail_source import (
    CITILINK_DIRECT_SOURCE,
    MVIDEO_DIRECT_SOURCE,
    YANDEX_MARKET_DIRECT_SOURCE,
    debug_search_direct_retail_sources,
)
from app.sources.searchapi_source import SEARCHAPI_SOURCE, debug_searchapi_google_shopping
from app.sources.serpapi_source import SERPAPI_SOURCE, debug_serpapi_google_shopping

logger = logging.getLogger(__name__)

# Совместимо и с новым пакетом ddgs, и со старым duckduckgo-search.
try:  # pragma: no cover - зависит от установленного пакета
    from ddgs import DDGS  # type: ignore
except ImportError:  # pragma: no cover - зависит от установленного пакета
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:
        DDGS = None  # type: ignore[misc, assignment]


@dataclass
class ProductCandidate:
    """Нормализованная карточка товара для проверки администратором."""
    id: int = 0
    request_id: int = 0
    title: str = ""
    url: str = ""
    source: str = "generic_web"
    price: Optional[int] = None
    snippet: str = ""
    score: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    status: str = "CANDIDATE"
    quality: str = "WEAK"
    source_type: str = "web"
    origin: str = "auto"
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    seller: str = ""
    city: str = ""
    availability: str = ""
    description: str = ""
    product_facts: dict[str, object] = field(default_factory=dict)
    facts_json: str = ""
    price_source: str = ""
    price_reliability: str = "none"
    price_rejected_reason: str = ""
    price_from_budget_suspect: bool = False
    bad_price_context: bool = False
    score_cap_applied: str = ""
    low_price_suspect: bool = False
    external_source: str = ""
    raw: dict[str, object] = field(default_factory=dict)
    created_at: str = ""


@dataclass
class SearchAttemptData:
    source: str
    query: str
    status: str
    found_count: int = 0
    kept_count: int = 0
    error_text: str = ""


@dataclass
class SearchCollection:
    candidates: list[ProductCandidate] = field(default_factory=list)
    # Все разобранные карточки до quality_filter. Нужны QA и debug: в
    # candidates остаются только NORMAL/WEAK, а raw_candidates не теряются.
    raw_candidates: list[ProductCandidate] = field(default_factory=list)
    attempts: list[SearchAttemptData] = field(default_factory=list)
    manual_links: list[dict] = field(default_factory=list)
    verified_rejections: list[object] = field(default_factory=list)
    quality_stats: dict[str, int] = field(default_factory=lambda: {
        "raw": 0,
        "total_found": 0,
        "trash": 0,
        "good": 0,
        "ok": 0,
        "categories": 0,
        "articles": 0,
        "wrong_type": 0,
        "weak": 0,
        "normal": 0,
        "saved": 0,
    })
    verify_stats: dict[str, int] = field(default_factory=lambda: {
        "checked": 0,
        "VERIFY_ERROR": 0,
        "VERIFY_BLOCKED": 0,
        "NEED_MANUAL_CHECK": 0,
        "UNAVAILABLE": 0,
        "REMOVED_LISTING": 0,
        "PRICE_MISSING": 0,
        "BAD_ENCODING": 0,
        "PRICE_MISMATCH": 0,
        "WRONG_PRODUCT": 0,
        "NOT_PRODUCT_PAGE": 0,
        "REJECTED": 0,
        "VERIFIED_GOOD": 0,
        "VERIFIED_OK": 0,
        "OVER_BUDGET_SOFT": 0,
        "OVER_BUDGET_HARD": 0,
        "playwright_used": 0,
        "playwright_verified": 0,
        "playwright_failed": 0,
        "playwright_skipped": 0,
        "browser_provider": "",
        "browser_provider_fallback_reason": "",
        "manual_check_after_playwright": 0,
        "manual_check_saved_without_price": 0,
        "low_price_suspect": 0,
        "city_mismatch": 0,
        "price_from_budget_suspect": 0,
        "bad_price_context": 0,
        "score_cap_applied": 0,
        "saved": 0,
    })


@dataclass(frozen=True)
class SearchSourceDefinition:
    key: str
    source: str
    display_name: str
    domains: tuple[str, ...]
    source_type: str
    enabled_attr: str
    priority: int


QUALITY_GOOD = "GOOD"
QUALITY_OK = "OK"
QUALITY_WEAK = "WEAK"
QUALITY_TRASH = "TRASH"
QUALITY_TO_STATUS = {
    QUALITY_GOOD: "CANDIDATE",
    QUALITY_OK: "CANDIDATE",
    QUALITY_WEAK: "WEAK_CANDIDATE",
    QUALITY_TRASH: "REJECTED_AUTO",
}


SEARCH_SOURCES: tuple[SearchSourceDefinition, ...] = (
    SearchSourceDefinition("ozon", "ozon_search", "Ozon", ("ozon.ru",), "marketplace", "ENABLE_SEARCH_OZON", 1),
    SearchSourceDefinition("yandex_market", "yandex_market_search", "Яндекс Маркет", ("market.yandex.ru",), "marketplace", "ENABLE_SEARCH_YANDEX_MARKET", 1),
    SearchSourceDefinition("wildberries", "wildberries", "Wildberries", ("wildberries.ru",), "marketplace_api", "ENABLE_SEARCH_WILDBERRIES", 0),
    SearchSourceDefinition("avito", "avito_search", "Avito", ("avito.ru",), "classifieds", "ENABLE_SEARCH_AVITO", 2),
    SearchSourceDefinition("dns", "dns_search", "DNS", ("dns-shop.ru",), "retail", "ENABLE_SEARCH_DNS", 1),
    SearchSourceDefinition("mvideo", "mvideo_search", "М.Видео", ("mvideo.ru",), "retail", "ENABLE_SEARCH_MVIDEO", 1),
    SearchSourceDefinition("citilink", "citilink_search", "Ситилинк", ("citilink.ru",), "retail", "ENABLE_SEARCH_CITILINK", 1),
    SearchSourceDefinition("megamarket", "megamarket_search", "Мегамаркет", ("megamarket.ru",), "marketplace", "ENABLE_SEARCH_MEGAMARKET", 1),
)

MARKETPLACE_SOURCES = {
    domain: source.source
    for source in SEARCH_SOURCES
    for domain in source.domains
}
SITE_SOURCES = tuple((source.domains[0], source.source) for source in SEARCH_SOURCES if source.key != "wildberries")
SOURCE_TYPE_BY_SOURCE = {source.source: source.source_type for source in SEARCH_SOURCES}
SOURCE_PRIORITY = {source.source: source.priority for source in SEARCH_SOURCES}
TRUSTED_PRODUCT_SOURCES = {source.source for source in SEARCH_SOURCES}
SOURCE_TYPE_BY_SOURCE[SEARCHAPI_SOURCE] = "shopping_api"
SOURCE_TYPE_BY_SOURCE[SERPAPI_SOURCE] = "shopping_api"
DIRECT_RETAIL_SOURCES = {
    CITILINK_DIRECT_SOURCE,
    MVIDEO_DIRECT_SOURCE,
    YANDEX_MARKET_DIRECT_SOURCE,
}
TRUSTED_PRODUCT_SOURCES.update(DIRECT_RETAIL_SOURCES)
for direct_source in DIRECT_RETAIL_SOURCES:
    SOURCE_TYPE_BY_SOURCE[direct_source] = "retail"
RANK_TRUSTED_SOURCES = {
    "yandex_market_search",
    "mvideo_search",
    "citilink_search",
    "dns_search",
    "ozon_search",
    "wildberries",
    "megamarket_search",
    SERPAPI_SOURCE,
    *DIRECT_RETAIL_SOURCES,
}
LOW_PRICE_LIMITS = {
    "laptop": 10_000,
    "phone": 5_000,
    "tv": 5_000,
    "chair": 1_000,
    "headphones": 300,
}
LOW_PRICE_RISK = "подозрительно низкая цена"
CITY_MISMATCH_RISK = "город объявления не совпадает с запросом"
GENERIC_TITLE_RISK = "слишком общий title"
AVITO_CITY_SLUGS = {
    "москва": {"moskva", "moscow"},
    "ярославль": {"yaroslavl"},
}
AVITO_NON_CITY_PATHS = {"rossiya", "all", "audio_i_video", "bytovaya_tehnika", "transport"}
KNOWN_TV_BRANDS = ("tcl", "hisense", "haier", "lg", "samsung", "xiaomi", "tuvio", "sber", "sony", "philips", "asano")
# Ориентир для score. Низкий score переводит товарную карточку в WEAK,
# а не в REJECTED_AUTO: администратор должен иметь возможность её проверить.
MIN_CANDIDATE_SCORE = 30.0
# Площадки для compare-поиска (ищем ту же модель дешевле)
COMPARE_SITES = (
    ("ozon.ru", "ozon_search"),
    ("market.yandex.ru", "yandex_market_search"),
    ("wildberries.ru", "wildberries"),
    ("dns-shop.ru", "dns_search"),
    ("mvideo.ru", "mvideo_search"),
    ("citilink.ru", "citilink_search"),
    ("megamarket.ru", "megamarket_search"),
)
# Иностранные домены производителей: их витрины/каталоги не являются
# карточкой товара для покупки в РФ.
FOREIGN_VENDOR_DOMAINS = ("tcl.com", "lg.com", "samsung.com", "sony.com", "philips.com", "hisense.com", "haier.com")
FOREIGN_SHOP_DOMAINS = (
    "amazon.com", "amazon.de", "amazon.co.uk", "ebay.com", "ebay.co.uk",
    "walmart.com", "target.com", "bestbuy.com", "newegg.com", "alibaba.com",
    "aliexpress.com", "temu.com",
)
AD_REDIRECT_DOMAINS = (
    "bing.com", "googleadservices.com", "googleads.g.doubleclick.net",
    "doubleclick.net", "yabs.yandex.ru",
)
NON_PURCHASE_URL_MARKERS = {
    "/opinion/": "страница мнений/отзывов",
    "/reviews/": "страница отзывов",
    "/otzyv": "страница отзывов",
    "/specification": "страница характеристик",
    "/characteristics": "страница характеристик",
    "/accessories": "страница аксессуаров",
    "/support": "страница поддержки",
    "/manual": "инструкция, не страница покупки",
    "/compare": "страница сравнения",
    "/search": "страница поиска",
    "/articles/": "статья, не карточка товара",
}
CATEGORY_MARKERS = (
    "купить по низкой цене", "купить на ozon", "купить на яндекс маркете",
    "телевизоры 4k купить", "каталог", "все товары", "низкие цены",
    "большой ассортимент", "быстрая доставка", "оригинальные товары",
    "распродажа", "скидки и акции", "найдено товаров", "результаты поиска",
)
ARTICLE_MARKERS = (
    "лучшие телевизоры", "топ", "рейтинг", "обзор", "подборка", "как выбрать",
    "ign", "игромания", "ixbt", "vc.ru", "dzen", "tiktok", "youtube", "rutube",
    "lifehacker", "лайфхакер", "какой телевизор выбрать", "что купить",
)
WRONG_TV_PRODUCT_MARKERS = (
    "консоль", "игровая приставка", "ретро-игры", "проектор", "кронштейн",
    "подсветка", "пульт", "кабель", "подставка", "приставка", "монитор",
    "ps4", "xbox", "playstation", "dualshock", "gamepad", "nintendo",
    "ufc", "fifa", "igra", "console", "projector", "bracket", "remote",
    "cable", "tv stand",
)


def _search_timeout() -> int:
    return max(2, int(getattr(settings, "SEARCH_TIMEOUT_SECONDS", 8) or 8))


class _FetchHttpResponse:
    def __init__(self, result: FetchResult, fallback_url: str):
        self.text = result.html or ""
        self.url = result.final_url or fallback_url
        self.status_code = result.status_code or 0
        self.fetch_result = result

    def json(self, **kwargs: Any) -> Any:
        return json.loads(self.text, **kwargs)


def _response_from_fetch_result(result: FetchResult, url: str) -> _FetchHttpResponse:
    if result.ok:
        return _FetchHttpResponse(result, url)

    if result.blocked_reason == "timeout":
        exc = requests.Timeout(result.error or f"timeout for url: {url}")
        exc.fetch_result = result
        raise exc

    if result.blocked:
        response = requests.Response()
        response.status_code = result.status_code or 0
        response.url = result.final_url or url
        error = requests.HTTPError(
            f"{result.status_code or ''} blocked: {result.blocked_reason or 'blocked'} for url: {url}".strip()
        )
        error.response = response
        error.fetch_result = result
        raise error

    exc = requests.RequestException(result.error or result.blocked_reason or "HTTP-запрос не выполнен")
    exc.fetch_result = result
    raise exc


def _is_source_enabled(source: SearchSourceDefinition) -> bool:
    return bool(getattr(settings, source.enabled_attr, True))


def _is_source_name_enabled(source_name: str) -> bool:
    source = next((item for item in SEARCH_SOURCES if item.source == source_name), None)
    return _is_source_enabled(source) if source else True


def _enabled_site_sources() -> list[SearchSourceDefinition]:
    return [source for source in SEARCH_SOURCES if source.key != "wildberries" and _is_source_enabled(source)]


def _is_generic_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_SEARCH_GENERIC", True))


def _request_product(req: Request) -> str:
    return (req.product_name or req.product or "товар").strip()


def _is_ps5_tv(req: Request) -> bool:
    product = _request_product(req).lower()
    purpose = f"{req.use_case or req.purpose} {req.original_query}".lower()
    is_tv = any(word in product for word in ("телевизор", "tv"))
    is_ps5 = any(word in purpose for word in ("ps5", "ps 5", "playstation", "плейстейшен", "приставк", "игр"))
    return is_tv and is_ps5


def generate_search_queries(req: Request) -> list[str]:
    """Создаёт пачку запросов, включая целевые site:-запросы.

    Для телевизора для PS5 список намеренно предсказуем: так его удобно
    проверять из ``tools/test_search.py`` и в отладке бота.
    """
    product = _request_product(req)
    budget = str(req.budget or "").strip()
    city = str(req.city or "").strip()
    # Если парсер уже собрал чистый запрос — используем его как основной
    clean = str(req.clean_search_query or "").strip()
    base = clean or build_search_query(
        product,
        req.use_case or req.purpose,
        budget,
        city,
        req.important_criteria or req.criteria,
    )

    queries: list[str] = []
    if _is_ps5_tv(req):
        limit = budget or "45000"
        # Если есть чистый запрос, начинаем с него — он точнее шаблонных вариаций
        if clean:
            queries = [clean]
        queries += [
            f"телевизор 4K для PS5 до {limit}" + (f" {city}" if city else ""),
            f"телевизор 43 4K Smart TV до {limit}",
            f"телевизор 55 4K Smart TV до {limit}",
            f"телевизор 4K HDMI PS5 до {limit}",
            f"телевизор TCL 43 4K до {limit}",
            f"телевизор Haier 43 4K до {limit}",
            f"телевизор Hisense 43 4K до {limit}",
            f"телевизор Tuvio 55 4K до {limit}",
        ]
    else:
        queries = [base]
        if budget:
            queries.extend([f"{product} до {budget}", f"{product} купить до {budget}"])
        if _is_headphones_request(req) and (_budget_value(req) or 0) >= 3_000:
            limit = budget or ""
            suffix = f" до {limit}" if limit else ""
            queries[1:1] = [
                f"Soundcore наушники{suffix}",
                f"JBL наушники{suffix}",
            ]
            queries.extend([
                f"QCY наушники{suffix}",
                f"Sony наушники{suffix}",
                f"Xiaomi наушники{suffix}",
                f"Baseus наушники{suffix}",
                f"Anker Soundcore наушники{suffix}",
            ])
        if req.important_criteria or req.criteria:
            queries.append(f"{product} {req.important_criteria or req.criteria}")
        if city:
            queries.append(f"{product} купить {city}")

    # Site-поиск делает отдельный адаптер, но запросы показываем в debug.
    site_base = f"{product}"
    if _is_ps5_tv(req):
        site_base += " 4K PS5"
    elif req.use_case or req.purpose:
        site_base += f" {req.use_case or req.purpose}"
    if budget:
        site_base += f" до {budget}"
    queries.extend(f"site:{source.domains[0]} {site_base}" for source in _enabled_site_sources())

    unique: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = " ".join(query.split())
        if normalized.lower() not in seen:
            seen.add(normalized.lower())
            unique.append(normalized)
    return unique


def _source_from_url(url: str, default: str = "generic_web") -> str:
    domain = extract_domain(url)
    for known_domain, source in MARKETPLACE_SOURCES.items():
        if domain == known_domain or domain.endswith(f".{known_domain}"):
            return source
    return default


def extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _normalise_url(url: str) -> str:
    """Убирает трекинг-параметры для дедупликации, не меняя ссылку в карточке."""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    query_pairs = [
        part for part in parsed.query.split("&")
        if part and not part.lower().startswith(("utm_", "yclid=", "gclid="))
    ]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "&".join(query_pairs), ""))


def _looks_like_real_candidate(title: str, url: str) -> bool:
    """Отсеивает только явный мусор; отсутствие цены/характеристик допустимо."""
    if not title or not url or not _normalise_url(url):
        return False
    lowered = title.lower().strip()
    obvious_noise = ("капча", "captcha", "404", "вакансии", "контакты", "политика конфиденциальности")
    return not any(marker in lowered for marker in obvious_noise)


def clean_candidate_title(title: str) -> str:
    """Убирает из заголовка магазинный хвост и лишние пробелы, не меняя модель."""
    cleaned = re.sub(r"\s+", " ", (title or "")).strip()
    return cleaned.split(" | ", 1)[0].strip()[:300]


def _candidate_text(title: str, snippet: str, url: str) -> str:
    return f"{title} {snippet} {url}".lower()


def extract_model_key(title: str) -> str:
    """Извлекает ключ модели: бренд + диагональ + 4K + код модели.

    Примеры:
        "Tuvio Телевизор 55 дюймов Smart TV 4K TD55UFBCV51" → "Tuvio 55 4K TD55UFBCV51"
        "Телевизор Haier 43 Smart TV S4 4K" → "Haier 43 4K Smart TV S4"
        "75 (190.5 см) Телевизор TCL 75P7K" → "TCL 75P7K 4K"
    """
    text = title.lower().strip()
    parts: list[str] = []

    # Бренд
    for brand in KNOWN_TV_BRANDS:
        if brand in text:
            parts.append(brand.title())
            break

    # Диагональ
    diag_match = re.search(r"\b(\d{2})\s*(?:дюйм|\"|''|″|см)", text)
    if diag_match:
        diag = int(diag_match.group(1))
        if diag >= 20 and diag <= 100:
            parts.append(str(diag))
    # Диагональ в формате "75 (190.5 см)"
    diag_match2 = re.search(r"\b(\d{2})\s*\(\d+", text)
    if not diag_match and diag_match2:
        diag = int(diag_match2.group(1))
        if 20 <= diag <= 100:
            parts.append(str(diag))

    # 4K / UHD / Ultra HD
    if any(word in text for word in ("4k", "ultra hd", "uhd")):
        parts.append("4K")

    # Модель: код типа TD55UFBCV51, 75P7K, 43E77Q, UE55U8000
    model_match = re.search(
        r"\b([A-Za-z]{1,4}\d{2,3}[A-Za-z]{1,4}[A-Za-z0-9-]*)\b",
        title,
    )
    if model_match:
        parts.append(model_match.group(1).upper())
    else:
        # Fallback: более жадный поиск модельного кода
        model_match2 = re.search(
            r"\b([A-Za-z]{2,6}\s*[A-Za-z0-9]{2,7}[A-Za-z0-9-]*)\b",
            title,
        )
        if model_match2:
            code = model_match2.group(1).replace(" ", "").upper()
            if len(code) >= 5:
                parts.append(code)

    return " ".join(parts).strip()


def _is_avito_search_page(parsed) -> bool:
    """Страница поиска/раздела Avito, а не конкретное объявление."""
    domain = parsed.netloc.lower().removeprefix("www.")
    if "avito.ru" not in domain:
        return False
    path = parsed.path.lower()
    query = parsed.query.lower()
    # Объявление Avito заканчивается на «..._<цифры>» (slug + числовой id).
    is_item = bool(re.search(r"_\d{5,}/?$", path.rstrip("/")))
    if is_item:
        return False
    # Поиск по q=, известные разделы-каталоги и листинги «*-asgBox*».
    has_query = "q=" in query
    is_catalog_section = any(part in path for part in (
        "/audio_i_video", "/bytovaya_tehnika", "/elektronika", "/televizory",
    ))
    return has_query or is_catalog_section


def _is_yandex_market_non_product(parsed) -> bool:
    """Страницы Я.Маркета, которые не являются карточкой товара."""
    domain = parsed.netloc.lower().removeprefix("www.")
    if "market.yandex.ru" not in domain:
        return False
    path = parsed.path.lower()
    # Карточка товара — только /card/ или /product--.../<id>.
    is_card = "/card/" in path or "/product--" in path or "/product/" in path
    if is_card and "/reviews" not in path and "/offers" not in path:
        return False
    # Отзывы, офферы, сравнения, поиск — не товар.
    return any(part in path for part in ("/offers/", "/reviews", "/cc/", "/search")) or "/offers/" in path or path.endswith("/reviews")


def _domain_matches(domain: str, known: str) -> bool:
    return domain == known or domain.endswith(f".{known}")


def _is_ad_redirect(url: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    if any(_domain_matches(domain, item) for item in AD_REDIRECT_DOMAINS):
        return True
    return any(marker in path for marker in ("/aclick", "/adclick", "/ads/", "/clk"))


def _is_foreign_shop(url: str) -> bool:
    domain = extract_domain(url)
    return any(_domain_matches(domain, item) for item in FOREIGN_SHOP_DOMAINS)


def _is_direct_product_url(source: str, url: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower().rstrip("/")
    query = parsed.query.lower()
    if not parsed.scheme or not parsed.netloc or _is_ad_redirect(url):
        return False
    if any(marker in path for marker in (
        "/search", "/catalog/0/search", "/offers", "/reviews", "/review",
        "/otzyv", "/otzyvy", "/compare", "/characteristics", "/specification",
    )):
        return False
    if any(key in query for key in ("q=", "text=", "search=", "query=")):
        return False
    source = source or _source_from_url(url)
    if source in {SEARCHAPI_SOURCE, SERPAPI_SOURCE}:
        source = _source_from_url(url)
    if source == "wildberries":
        return "wildberries.ru" in domain and "/catalog/" in path and "/detail" in path
    if source == "ozon_search":
        return "ozon.ru" in domain and ("/product/" in path or "/context/detail/id/" in path)
    if source == "yandex_market_search":
        return "market.yandex.ru" in domain and ("/product--" in path or "/card/" in path or "/product/" in path)
    if source == "dns_search":
        return "dns-shop.ru" in domain and "/product/" in path
    if source == "mvideo_search":
        return "mvideo.ru" in domain and ("/products/" in path or "/product/" in path)
    if source == "avito_search":
        return "avito.ru" in domain and bool(re.search(r"_\d{5,}$", path))
    if source == "citilink_search":
        return "citilink.ru" in domain and ("/product/" in path or bool(re.search(r"-\d{5,}$", path)))
    if source == "megamarket_search":
        return "megamarket.ru" in domain and ("/catalog/details/" in path or "/product/" in path)
    if source == CITILINK_DIRECT_SOURCE:
        return "citilink.ru" in domain and "/product/" in path
    if source == MVIDEO_DIRECT_SOURCE:
        return "mvideo.ru" in domain and ("/products/" in path or "/product/" in path)
    if source == YANDEX_MARKET_DIRECT_SOURCE:
        return "market.yandex.ru" in domain and ("/card/" in path or "/product--" in path or "/product/" in path)
    return False


def _is_search_or_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()
    if _is_direct_product_url(_source_from_url(url), url):
        return False
    return (
        "/search" in path
        or "/catalog/0/search" in path
        or "/catalog/" in path
        or "/category/" in path
        or "/f/" in path
        or "/recipe/" in path
        or any(key in query for key in ("q=", "text=", "search=", "query="))
    )


def is_category_page(title: str, snippet: str, url: str) -> bool:
    """Определяет витрины/категории, а не карточки одного товара."""
    text = _candidate_text(title, snippet, url)
    parsed = urlparse(url)
    path = parsed.path.lower()
    if _is_direct_product_url(_source_from_url(url), url):
        return False
    is_plural_tv_title = bool(re.search(r"\bтелевизоры\b", title.lower()))
    is_catalog_path = "/catalog/" in path
    is_filter_path = "/f/" in path or "/recipe/" in path
    is_avito_listing = "avito.ru" in url.lower() and "/televizory-as" in path
    is_avito_search = _is_avito_search_page(parsed)
    return (
        any(marker in text for marker in CATEGORY_MARKERS)
        or _is_search_or_listing_url(url)
        or "/category/" in path
        or is_catalog_path
        or is_filter_path
        or is_plural_tv_title
        or is_avito_listing
        or is_avito_search
    )


def is_article_or_review(title: str, snippet: str, url: str) -> bool:
    """Определяет статьи, видео и обзоры, которые нельзя выдавать за товар."""
    if _is_direct_product_url(_source_from_url(url), url):
        return False
    text = _candidate_text(title, snippet, url)
    parsed = urlparse(url)
    path = parsed.path.lower()
    is_editorial_url = any(part in path for part in (
        "/blog/", "/digest/", "/article/", "/articles/", "/reviews/", "/review/",
    )) or "club.dns-shop.ru" in url.lower() or "journal.citilink.ru" in url.lower()
    is_ym_non_product = _is_yandex_market_non_product(parsed)
    return any(marker in text for marker in ARTICLE_MARKERS) or is_editorial_url or is_ym_non_product


def _is_foreign_vendor_site(url: str) -> bool:
    """Иностранный сайт производителя (tcl.com и т.п.) — это не точка покупки."""
    domain = extract_domain(url)
    return any(domain == d or domain.endswith(f".{d}") for d in FOREIGN_VENDOR_DOMAINS)


def _non_purchase_url_reason(url: str) -> str:
    """Причина, по которой URL не является страницей покупки, если она есть."""
    lowered = url.lower()
    for marker, reason in NON_PURCHASE_URL_MARKERS.items():
        if marker in lowered:
            return reason
    return ""


def _is_untrusted_for_russian_request(url: str, request: Request) -> bool:
    """Отсекает соцсети и украинские магазины для заявки по российскому городу."""
    domain = extract_domain(url)
    if domain == "facebook.com" or domain.endswith(".facebook.com"):
        return True
    if _is_foreign_shop(url):
        return True
    # Проект ищет товар в указанном российском городе; Citrus и домены .ua
    # не являются релевантной точкой покупки для такого запроса.
    return bool(request.city) and (domain == "citrus.ua" or domain.endswith(".citrus.ua") or domain.endswith(".ua"))



def _is_tv_request(req: Request) -> bool:
    return any(word in _request_product(req).lower() for word in ("телевизор", " tv"))


def _is_laptop_request(req: Request) -> bool:
    return any(word in _request_product(req).lower() for word in ("ноутбук", "laptop"))


def _is_chair_request(req: Request) -> bool:
    product = _request_product(req).lower()
    return "кресл" in product or "стул" in product or "chair" in product


def is_wrong_product_type(candidate: ProductCandidate, request: Request) -> bool:
    """Исключает аксессуары и консоли для заявки на телевизор."""
    if not _is_tv_request(request):
        return False
    text = _candidate_text(candidate.title, candidate.snippet, candidate.url)
    # «Телевизор для приставки» допустим; сама приставка — нет.
    text_without_allowed_context = re.sub(r"для\s+(?:игровой\s+)?приставк\w*", "", text)
    has_tv = "телевизор" in text or re.search(r"\btv\b", text) is not None
    wrong_marker_found = any(marker in text_without_allowed_context for marker in WRONG_TV_PRODUCT_MARKERS)
    has_brand = any(brand in text for brand in KNOWN_TV_BRANDS)
    has_tv_feature = any(marker in text for marker in ("4k", "uhd", "ultra hd", "qled", "oled"))
    trusted_store = candidate.source in TRUSTED_PRODUCT_SOURCES
    # Поисковая карточка магазина может называться только кодом модели
    # («Samsung UE55U8000»). Это не неправильный тип товара: оставляем её
    # для WEAK-проверки, если есть бренд + признаки ТВ/модель.
    looks_like_store_tv = trusted_store and has_brand and (has_tv_feature or _has_concrete_model(candidate))
    if not has_tv and looks_like_store_tv and not wrong_marker_found:
        return False
    # Наличие слова «телевизор» спасает только единственный разрешённый
    # контекст «для приставки», но не «телевизор + консоль/кронштейн».
    return (not has_tv) or (wrong_marker_found and not (has_tv and "приставк" not in text_without_allowed_context and all(
        marker not in text_without_allowed_context for marker in WRONG_TV_PRODUCT_MARKERS if marker != "приставка"
    )))


def _has_concrete_model(candidate: ProductCandidate) -> bool:
    text = candidate.title
    # UE55U8000, 43P745, TD55UFBCV51 и сходные обозначения модели.
    return bool(re.search(r"\b(?:[A-Za-zА-Яа-я]{1,6}\d+[A-Za-zА-Яа-я][A-Za-zА-Яа-я0-9-]*|\d{2,}[A-Za-zА-Яа-я]{1,6}\d+[A-Za-zА-Яа-я0-9-]*)\b", text))


def is_real_product_candidate(candidate: ProductCandidate, request: Request) -> bool:
    """Карточка допустима для списка, если не является автоматически отклоняемым мусором."""
    return bool(candidate.title and candidate.url) and not (
        is_wrong_product_type(candidate, request)
        or is_category_page(candidate.title, candidate.snippet, candidate.url)
        or is_article_or_review(candidate.title, candidate.snippet, candidate.url)
    )


def _avito_risk_flags(request: Request) -> list[str]:
    flags = [
        "проверить продавца",
        "проверить город",
        "проверить состояние",
        "проверить отзывы",
    ]
    if not request.is_used_allowed:
        flags.append("б/у не разрешено клиентом")
    return flags


def _extract_avito_city(url: str, snippet: str = "") -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parts and parts[0] not in {"rossiya", "all", "audio_i_video", "bytovaya_tehnika"}:
        return parts[0].replace("_", " ").title()
    match = re.search(r"\b(?:г\.|город)\s*([А-Яа-яЁё-]{3,})", snippet or "")
    return match.group(1) if match else ""


def classify_candidate(candidate: ProductCandidate, request: Request) -> tuple[str, list[str]]:
    """Классифицирует карточку в GOOD/OK/WEAK/TRASH без изменения схемы БД."""
    title_lower = candidate.title.lower()
    snippet_lower = candidate.snippet.lower()
    url_lower = candidate.url.lower()
    if _is_ad_redirect(candidate.url) or "huge selection" in title_lower or re.search(r"\bshop\b.*\bhuge selection\b", title_lower):
        return QUALITY_TRASH, ["рекламная redirect-ссылка", "классификация: TRASH — реклама"]
    non_purchase_reason = _non_purchase_url_reason(candidate.url)
    if non_purchase_reason and not _is_direct_product_url(candidate.source, candidate.url):
        return QUALITY_TRASH, [non_purchase_reason, f"классификация: TRASH — {non_purchase_reason}"]
    if _is_untrusted_for_russian_request(candidate.url, request):
        return QUALITY_TRASH, ["нерелевантный зарубежный/социальный источник", "классификация: TRASH — источник не для покупки в РФ"]
    if is_wrong_product_type(candidate, request):
        return QUALITY_TRASH, ["не тот тип товара", "классификация: TRASH — не тот тип товара"]
    if is_category_page(candidate.title, candidate.snippet, candidate.url):
        return QUALITY_TRASH, ["страница категории", "классификация: TRASH — страница категории"]
    if is_article_or_review(candidate.title, candidate.snippet, candidate.url):
        return QUALITY_TRASH, ["обзор/подборка", "классификация: TRASH — обзор или отзывы"]
    if _is_foreign_vendor_site(candidate.url):
        return QUALITY_TRASH, ["сайт производителя, не точка покупки", "классификация: TRASH — не точка покупки"]

    unavailable_markers = [
        ("нет в наличии", "нет в наличии"),
        ("продаж прекращен", "продажи прекращены"),
        ("товар закончил", "товар закончился"),
        ("посмотреть аналоги", "нет в наличии"),
        ("no longer available", "нет в наличии"),
        ("out of stock", "нет в наличии"),
    ]
    for marker, label in unavailable_markers:
        if marker in title_lower or marker in snippet_lower or marker in url_lower:
            return QUALITY_WEAK, [label, f"классификация: WEAK — {label}"]

    budget = _budget_value(request)
    if budget and candidate.price and candidate.price > budget:
        over_budget_pct = (candidate.price - budget) / budget
        if over_budget_pct > 0.15:
            return QUALITY_TRASH, [f"выше бюджета ({candidate.price} > {budget})", "классификация: TRASH — сильно выше бюджета"]
        return QUALITY_WEAK, [f"выше бюджета ({candidate.price} > {budget})", "классификация: WEAK — выше бюджета"]

    direct_product = _is_direct_product_url(candidate.source, candidate.url)
    has_model = _has_concrete_model(candidate)
    source_is_known_store = candidate.source in TRUSTED_PRODUCT_SOURCES

    flags: list[str] = []
    if candidate.source == "avito_search":
        flags.extend(_avito_risk_flags(request))
        if not request.is_used_allowed:
            return QUALITY_WEAK, flags + ["классификация: WEAK — Avito только после ручной проверки"]

    if direct_product and has_model and candidate.price and source_is_known_store:
        return QUALITY_GOOD, flags + ["классификация: GOOD — модель, цена и прямая ссылка"]
    if direct_product and has_model:
        reason = "цена не найдена" if candidate.price is None else "источник требует ручной проверки"
        return QUALITY_OK, flags + [reason, f"классификация: OK — есть модель и прямая ссылка, {reason}"]

    weak_reasons = []
    if not direct_product:
        weak_reasons.append("не подтверждена прямая карточка товара")
    if not has_model:
        weak_reasons.append("нет признаков конкретной модели")
    return QUALITY_WEAK, flags + weak_reasons + [f"классификация: WEAK — {'; '.join(weak_reasons) or 'нужна ручная проверка'}"]



def _budget_value(req: Request) -> Optional[int]:
    try:
        value = int(str(req.budget).replace(" ", ""))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _normalise_city_key(value: str) -> str:
    value = (value or "").lower().replace("ё", "е").strip()
    return re.sub(r"[^a-zа-я0-9]+", "_", value).strip("_")


def _avito_url_city_slug(url: str) -> str:
    parsed = urlparse(url)
    parts = [part.lower() for part in parsed.path.strip("/").split("/") if part]
    if not parts or parts[0] in AVITO_NON_CITY_PATHS:
        return ""
    return parts[0].replace("_", "-")


def _is_low_price_suspect(req: Request, candidate: ProductCandidate) -> bool:
    price = getattr(candidate, "price", None)
    if price is None:
        return False
    try:
        price_value = int(price)
    except (TypeError, ValueError):
        return False
    category = detect_product_category(req, candidate)
    limit = LOW_PRICE_LIMITS.get(category)
    return bool(limit and price_value < limit)


def _is_avito_city_mismatch(req: Request, candidate: ProductCandidate) -> bool:
    if candidate.source != "avito_search" or not req.city:
        return False
    slug = _avito_url_city_slug(candidate.url)
    if not slug:
        return False
    expected = AVITO_CITY_SLUGS.get(_normalise_city_key(req.city))
    return bool(expected and slug not in expected)


def _has_risk_flag(candidate: ProductCandidate, risk: str) -> bool:
    return risk in {str(item).strip() for item in (candidate.risk_flags or [])}


def _add_risk_flag(candidate: ProductCandidate, risk: str) -> None:
    risks = list(candidate.risk_flags or [])
    if risk not in risks:
        risks.append(risk)
    candidate.risk_flags = list(dict.fromkeys(str(item) for item in risks if str(item).strip()))


def _has_generic_title(candidate: ProductCandidate) -> bool:
    title = (candidate.title or "").strip().lower()
    if not title:
        return True
    normalized = re.sub(r"[^a-zа-яё0-9]+", " ", title).strip()
    if normalized in {
        "ноутбуки", "ноутбуки купить", "телевизоры", "смартфоны",
        "мобильные телефоны", "наушники", "офисные кресла",
        "компьютерные кресла",
    }:
        return True
    if not _has_concrete_model(candidate) and any(marker in normalized for marker in (
        "каталог", "результаты поиска", "интернет магазин", "купить в",
    )):
        return True
    return False


def _is_direct_retail_candidate(candidate: ProductCandidate) -> bool:
    return (
        candidate.source in DIRECT_RETAIL_SOURCES
        or getattr(candidate, "external_source", "") == "direct_retail"
        or getattr(candidate, "price_source", "") == "direct_store"
    )


def _risk_text(candidate: ProductCandidate) -> str:
    return " | ".join(str(item).strip().lower() for item in (candidate.risk_flags or []) if str(item).strip())


def _has_weak_classification_risk(candidate: ProductCandidate) -> bool:
    text = _risk_text(candidate)
    return (
        "классификация: weak" in text
        or "нет признаков конкретной модели" in text
        or "низкий score, нужна ручная проверка" in text
    )


def _is_headphones_request(req: Request) -> bool:
    return detect_product_category(req) == "headphones"


def _headphone_brand_quality(req: Request, candidate: ProductCandidate) -> str:
    return quality_headphone_brand_quality(candidate, enabled=_is_headphones_request(req), budget=_budget_value(req))


def _direct_product_quality_level(req: Request, candidate: ProductCandidate) -> str:
    return quality_direct_product_quality_level(
        candidate,
        is_direct_retail=_is_direct_retail_candidate(candidate),
        is_used_allowed=bool(getattr(req, "is_used_allowed", False)),
        brand_quality=_headphone_brand_quality(req, candidate),
        has_weak_classification=_has_weak_classification_risk(candidate),
    )


def _final_product_quality_level(req: Request, candidate: ProductCandidate) -> tuple[str, str]:
    return quality_final_product_quality_level(
        candidate,
        is_chair=_is_chair_request(req),
        is_laptop=_is_laptop_request(req),
        is_ps5_tv=_is_ps5_tv(req),
        has_weak_classification=_has_weak_classification_risk(candidate),
        quality_is_weak=candidate.quality == QUALITY_WEAK,
    )


def _candidate_budget_status(req: Request, candidate: ProductCandidate) -> str:
    price = getattr(candidate, "price", None)
    if price is None:
        return PRICE_MISSING
    try:
        price_value = int(price)
    except (TypeError, ValueError):
        return PRICE_MISSING
    budget = _budget_value(req)
    if not budget:
        return ""
    return "IN_BUDGET" if price_value <= budget else OVER_BUDGET_SOFT if price_value <= budget * 1.15 else "OVER_BUDGET_HARD"


def _candidate_rank_score(req: Request, candidate: ProductCandidate) -> float:
    status = _verified_status(candidate)
    score = {
        VERIFIED_GOOD: 100.0,
        VERIFIED_OK: 70.0,
        NEED_MANUAL_CHECK: 50.0,
        VERIFY_BLOCKED: 50.0,
        OVER_BUDGET_SOFT: 35.0,
    }.get(status, 20.0)

    price = getattr(candidate, "price", None)
    budget_status = _candidate_budget_status(req, candidate)
    if price is None:
        score -= 40
    else:
        score += 30
    if budget_status == "IN_BUDGET":
        score += 20
    elif budget_status == OVER_BUDGET_SOFT:
        score -= 10
    elif budget_status == "OVER_BUDGET_HARD":
        score -= 40

    if candidate.source in RANK_TRUSTED_SOURCES:
        score += 10
    elif candidate.source == "avito_search":
        score += 5

    brand_quality = getattr(candidate, "brand_quality", "") or _headphone_brand_quality(req, candidate)
    if brand_quality == "known_headphone_brand":
        score += 8
    elif brand_quality == "unknown_headphone_brand" and _is_direct_retail_candidate(candidate):
        score -= 15

    if _has_risk_flag(candidate, LOW_PRICE_RISK):
        score = min(score - 50, 80)
    if _has_risk_flag(candidate, CITY_MISMATCH_RISK):
        score = min(score - 50, 75)
    if status in {NOT_PRODUCT_PAGE, UNAVAILABLE, REMOVED_LISTING, PRICE_MISSING}:
        score -= 80 if status != PRICE_MISSING else 40
    if _has_risk_flag(candidate, GENERIC_TITLE_RISK):
        score -= 30
    if not candidate.title or not candidate.url:
        score -= 80

    return max(0.0, min(200.0, score))


def _apply_ranking_sanity(req: Request, candidate: ProductCandidate) -> None:
    keep_low_searchapi_price = (
        getattr(candidate, "external_source", "") == "searchapi"
        and getattr(candidate, "price_reliability", "") == "low"
    )
    keep_direct_store_price = getattr(candidate, "price_source", "") == "direct_store"
    normalize_price_candidate(candidate, req)
    if keep_low_searchapi_price and candidate.price is not None:
        candidate.price_reliability = "low"
    if keep_direct_store_price and candidate.price is not None:
        candidate.price_reliability = "medium"
    if _is_low_price_suspect(req, candidate):
        _add_risk_flag(candidate, LOW_PRICE_RISK)
    if _is_avito_city_mismatch(req, candidate):
        _add_risk_flag(candidate, CITY_MISMATCH_RISK)
    if _has_generic_title(candidate):
        _add_risk_flag(candidate, GENERIC_TITLE_RISK)
    if getattr(candidate, "low_price_suspect", False):
        _add_risk_flag(candidate, LOW_PRICE_RISK)
        _add_risk_flag(candidate, TOO_CHEAP_FOR_BUDGET_RISK)

    if _has_risk_flag(candidate, LOW_PRICE_RISK) or _has_risk_flag(candidate, CITY_MISMATCH_RISK):
        candidate.quality = QUALITY_WEAK if candidate.quality != QUALITY_TRASH else candidate.quality
    candidate.score = apply_price_rank_penalties(candidate, _candidate_rank_score(req, candidate), verify_status=_verified_status(candidate))
    if _has_risk_flag(candidate, DIRECT_QUALITY_MANUAL_RISK) and candidate.score > 80:
        candidate.score = 80
        candidate.score_cap_applied = "manual_quality:80"
    if _has_risk_flag(candidate, DIRECT_RETAIL_USED_RISK) and candidate.score > 55:
        candidate.score = 55
        candidate.score_cap_applied = "retail_new_for_used_request:55"
    if getattr(candidate, "product_quality_level", "") == "weak" and candidate.score > 80:
        candidate.score = 80
        candidate.score_cap_applied = candidate.score_cap_applied or "weak_quality:80"
    if getattr(candidate, "product_quality_level", "") == "bad" and candidate.score > 40:
        candidate.score = 40
        candidate.score_cap_applied = candidate.score_cap_applied or "bad_quality:40"


def score_result(
    title: str,
    snippet: str,
    price: Optional[int],
    req: Request,
    source: str = "generic_web",
    status: str = "CANDIDATE",
    classification_flags: Optional[list[str]] = None,
) -> tuple[float, list[str]]:
    """Оценивает только товарные карточки; мусор не может стать топом."""
    flags = list(classification_flags or [])
    if status == "REJECTED_AUTO":
        if "не тот тип товара" in flags:
            return 0.0, flags
        return 20.0, flags

    text = f"{title} {snippet}".lower()
    score = 0.0
    has_model = _has_concrete_model(ProductCandidate(title=title))
    budget = _budget_value(req)
    is_tv = _is_tv_request(req)

    if is_tv:
        if "телевизор" in title.lower():
            score += 30
        if any(brand in text for brand in KNOWN_TV_BRANDS):
            score += 20
        has_4k = any(word in text for word in ("4k", "ultra hd", "uhd"))
        if has_4k:
            score += 20
        elif _is_ps5_tv(req):
            score -= 10
            flags.append("4K не подтверждён")
        if re.search(r"\b(43|50|55|65)\s*(?:\"|дюйм|''|led|qled|uhd|4k|см)\b", text):
            score += 10
        if any(word in text for word in ("full hd", "fullhd", "фулл hd", "1080p")):
            score -= 20
            if _is_ps5_tv(req) and budget and budget >= 25_000:
                flags.append("Full HD для PS5 не подходит")
        if not has_model:
            score -= 30
            flags.append("нет признаков конкретной модели")
        if _is_ps5_tv(req) and "hdmi" not in text:
            flags.append("HDMI не указан")
        if _is_ps5_tv(req):
            if any(word in text for word in ("120 гц", "120hz", "144 гц", "144hz")):
                score += 20
            if "hdmi 2.1" in text:
                score += 20
            if "vrr" in text:
                score += 12
            if any(word in text for word in ("qled", "oled", "mini led")):
                score += 10
            if re.search(r"\b60\s*(?:гц|hz)\b", text):
                score -= 8
                flags.append("60 Гц, для PS5 не идеал")
        # 75″ и больше не должны автоматически становиться лучшим выбором.
        if _is_ps5_tv(req) and re.search(r"\b(75|77|85|98)\s*(?:\"|дюйм)", text):
            score -= 15
            flags.append("слишком большой экран для PS5 (рекомендуем 43-65″)")

    if price is None:
        score -= 10
        flags.append("цена не подтверждена")
    elif budget and price <= budget:
        score += 10
    elif budget and price > budget:
        score -= 20
        flags.append("выше бюджета")

    if source in TRUSTED_PRODUCT_SOURCES:
        score += 10
    if source == "generic_web" and not has_model:
        score = min(score, 40)
    if not req.is_used_allowed and any(word in text for word in ("б/у", "бу ", "used")):
        score -= 12

    return max(0.0, min(95.0, score)), list(dict.fromkeys(flags))


def _weak_reasons(candidate: ProductCandidate, req: Request, score: float) -> list[str]:
    """Причины, по которым товарную страницу стоит проверить вручную."""
    text = f"{candidate.title} {candidate.snippet}".lower()
    reasons: list[str] = []
    if candidate.price is None:
        reasons.append("цена не найдена")
    if _is_ps5_tv(req):
        if "hdmi" not in text:
            reasons.append("HDMI не указан")
        if not re.search(r"\b(?:60|75|100|120|144)\s*(?:гц|hz)\b", text):
            reasons.append("герцовка не указана")
    if _is_tv_request(req) and not _has_concrete_model(candidate):
        reasons.append("точная модель не указана")
    if score < MIN_CANDIDATE_SCORE and not reasons:
        reasons.append("низкий score, нужна ручная проверка")
    return reasons


class GenericSearchAdapter:
    """Обычный веб-поиск через DDGS, когда пакет доступен."""
    source = "generic_web"

    def search(self, query: str, limit: int = 5) -> list[dict]:
        if DDGS is None:
            raise RuntimeError("DDGS не установлен (нужен пакет ddgs или duckduckgo-search)")
        try:
            client = DDGS(timeout=_search_timeout())
        except TypeError:
            client = DDGS()
        try:
            return list(client.text(query, max_results=limit) or [])[:limit]
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()


class WildberriesAdapter:
    """Публичная выдача Wildberries без ключа и без платного API."""
    source = "wildberries"
    endpoint = "https://search.wb.ru/exactmatch/ru/common/v13/search"

    @staticmethod
    def _parse_payload(response: _FetchHttpResponse) -> dict:
        """WB иногда отдаёт не-JSON (антибот/HTML) с неверным Content-Type.

        Поэтому разбор делаем терпимым: сначала пробуем response.json(),
        затем json.loads() по тексту с обрезкой возможного мусорного префикса.
        При неудаче возвращаем пустой dict, не роняя источник.
        """
        try:
            data = response.json()
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError):
            pass
        text = (response.text or "").strip()
        # Иногда ответ начинается с мусора до первой фигурной скобки.
        brace = text.find("{")
        if brace > 0:
            text = text[brace:]
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Wildberries вернул не-JSON ответ: {exc}") from exc

    def search(self, query: str, limit: int = 10) -> list[dict]:

        fetch_result = fetch_http(
            self.endpoint,
            params={
                "appType": 1,
                "curr": "rub",
                "dest": -1257786,
                "query": query,
                "resultset": "catalog",
                "sort": "popular",
                "spp": 30,
                "suppressSpellcheck": "false",
                "page": 1,
            },
            headers={"User-Agent": "Mozilla/5.0 (compatible; NaydiVygodneeBot/2.0)"},
            timeout=_search_timeout(),
            retries=1,
        )
        response = _response_from_fetch_result(fetch_result, self.endpoint)
        payload = self._parse_payload(response)
        if not payload:
            return []
        products = payload.get("data", {}).get("products") or payload.get("products") or []

        rows: list[dict] = []
        for item in products[:limit]:
            product_id = item.get("id")
            if not product_id:
                continue
            raw_price = item.get("salePriceU") or item.get("priceU")
            price = int(raw_price / 100) if isinstance(raw_price, (int, float)) and raw_price else None
            title = " ".join(part for part in [item.get("brand", ""), item.get("name", "")] if part).strip()
            snippet_bits = []
            if item.get("rating"):
                snippet_bits.append(f"рейтинг {item['rating']}")
            if item.get("feedbacks"):
                snippet_bits.append(f"отзывов {item['feedbacks']}")
            if price:
                snippet_bits.append(f"{price} ₽")
            rows.append({
                "title": title,
                "href": f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
                "body": ", ".join(snippet_bits),
                "price": price,
                "rating": item.get("rating"),
                "reviews_count": item.get("feedbacks"),
                "availability": "есть в выдаче",
            })
        return rows


class SearchBySiteAdapter(GenericSearchAdapter):
    """Запускает DDGS site:-запросы для магазинов, где API не нужен."""


def _candidate_from_row(row: dict, req: Request, default_source: str) -> Optional[ProductCandidate]:
    title = clean_candidate_title(str(row.get("title") or row.get("name") or ""))
    url = str(row.get("href") or row.get("url") or "").strip()
    snippet = str(row.get("body") or row.get("snippet") or "").strip()
    if not _looks_like_real_candidate(title, url):
        return None
    raw_price = row.get("price")
    source = str(row.get("source") or _source_from_url(url, default_source) or default_source)
    price = raw_price
    price_source = str(row.get("price_source") or "")
    explicit_reliability = str(row.get("price_reliability") or "")
    min_price, max_price = (7_000, 300_000) if _is_tv_request(req) else (1_000, 10_000_000)
    if isinstance(price, int) and min_price <= price <= max_price:
        if not price_source:
            price_source = "api" if source == "wildberries" else "search_result"
    else:
        price = extract_price(f"{title} {snippet}", min_price=min_price, max_price=max_price)
        if not price_source:
            price_source = "search_snippet" if price is not None else ""
    rating = row.get("rating")
    try:
        rating_value = float(rating) if rating not in (None, "") else None
    except (TypeError, ValueError):
        rating_value = None
    reviews = row.get("reviews_count") or row.get("feedbacks")
    try:
        reviews_count = int(reviews) if reviews not in (None, "") else None
    except (TypeError, ValueError):
        reviews_count = None
    city = str(row.get("city") or "").strip()
    if source == "avito_search" and not city:
        city = _extract_avito_city(url, snippet)
    candidate = ProductCandidate(
        request_id=req.id,
        title=title[:300],
        url=url[:500],
        source=source,
        source_type=SOURCE_TYPE_BY_SOURCE.get(source, "web"),
        price=price,
        snippet=snippet[:500],
        rating=rating_value,
        reviews_count=reviews_count,
        seller=str(row.get("seller") or "").strip()[:200],
        external_source=str(row.get("external_source") or "").strip()[:80],
        raw=row.get("raw") if isinstance(row.get("raw"), dict) else {},
        city=city[:120],
        availability=str(row.get("availability") or "").strip()[:120],
        description=str(row.get("description") or snippet or "").strip()[:500],
        price_source=price_source,
        price_reliability=explicit_reliability or "none",
        created_at=datetime.now().isoformat(),
    )
    normalize_price_candidate(candidate, req, raw_price=raw_price, price_source=price_source, text=f"{title} {snippet}")
    if explicit_reliability:
        candidate.price_reliability = explicit_reliability if candidate.price is not None else "none"
    for attr in ("price_rejected_reason", "price_from_budget_suspect", "bad_price_context"):
        if row.get(attr) and not getattr(candidate, attr, None):
            setattr(candidate, attr, row.get(attr))
    price = candidate.price
    row_risks = row.get("risk_flags") if isinstance(row.get("risk_flags"), list) else []
    guard_risks = list(candidate.risk_flags or []) + [str(item) for item in row_risks if str(item).strip()]
    quality, classification_flags = classify_candidate(candidate, req)
    status = QUALITY_TO_STATUS[quality]
    score, risks = score_result(title, snippet, price, req, source, status, classification_flags)
    if isinstance(raw_price, int) and not min_price <= raw_price <= max_price and re.search(r"\b(?:[A-Za-z]{1,6}\d{2,}[A-Za-z0-9-]*|\d{2,}[A-Za-z]{1,6}\d+)\b", title):
        risks.append("цена похожа на номер модели")
    if quality == QUALITY_WEAK:
        risks.extend(_weak_reasons(candidate, req, score))
    if candidate.city:
        risks.append(f"город: {candidate.city}")
    if candidate.rating is not None:
        risks.append(f"рейтинг: {candidate.rating:g}")
    if candidate.reviews_count is not None:
        risks.append(f"отзывов: {candidate.reviews_count}")
    candidate.quality = quality
    candidate.status = status
    candidate.score = score
    candidate.risk_flags = list(dict.fromkeys(guard_risks + risks))
    return candidate


def _is_rate_limited_exception(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    return "429" in str(exc).lower() or "too many requests" in str(exc).lower()


def _is_temporary_exception(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, TimeoutError)):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in (
        "timeout", "timed out", "connection", "temporarily", "temporary",
        "dns", "name resolution", "429", "too many requests", "503", "502", "504",
    ))


def _search_with_retry(adapter: GenericSearchAdapter | WildberriesAdapter, query: str, limit: int) -> list[dict]:
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            return adapter.search(query, limit=limit)
        except Exception as exc:
            last_exc = exc
            if _is_rate_limited_exception(exc) or not _is_temporary_exception(exc) or attempt == 1:
                break
            time.sleep(0.25)
    if last_exc:
        raise last_exc
    return []


def _bump_quality_stats(collection: SearchCollection, candidate: ProductCandidate) -> None:
    stats = collection.quality_stats
    stats["raw"] += 1
    stats["total_found"] += 1
    if candidate.quality == QUALITY_TRASH:
        stats["trash"] += 1
        if "страница категории" in candidate.risk_flags:
            stats["categories"] += 1
        elif "обзор/подборка" in candidate.risk_flags:
            stats["articles"] += 1
        elif "не тот тип товара" in candidate.risk_flags:
            stats["wrong_type"] += 1
        return
    if candidate.quality == QUALITY_GOOD:
        stats["good"] += 1
        stats["normal"] += 1
    elif candidate.quality == QUALITY_OK:
        stats["ok"] += 1
        stats["normal"] += 1
    else:
        stats["weak"] += 1



def _collect_from_adapter(
    adapter: GenericSearchAdapter | WildberriesAdapter,
    req: Request,
    source: str,
    query: str,
    limit: int,
    collection: SearchCollection,
    seen_urls: set[str],
) -> None:
    try:
        rows = _search_with_retry(adapter, query, limit)
    except Exception as exc:  # каждый источник изолирован от остальных
        logger.warning("Автопоиск: %s не выполнил %r: %s", source, query, exc)
        collection.attempts.append(SearchAttemptData(source, query, "ERROR", error_text=str(exc)))
        return

    kept = 0
    quality_counts = {QUALITY_GOOD: 0, QUALITY_OK: 0, QUALITY_WEAK: 0, QUALITY_TRASH: 0}
    for row in rows:
        try:
            candidate = _candidate_from_row(row, req, source)
        except Exception as exc:
            # Одна некорректная карточка не должна ломать поиск по источнику.
            logger.warning("Автопоиск: не удалось разобрать карточку %s: %s", source, exc)
            continue
        if not candidate:
            continue
        normalized_url = _normalise_url(candidate.url)
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        collection.raw_candidates.append(candidate)
        _bump_quality_stats(collection, candidate)
        quality_counts[candidate.quality] += 1
        if candidate.quality != QUALITY_TRASH:
            collection.candidates.append(candidate)
            kept += 1
    status = "OK" if rows else "EMPTY"
    collection.attempts.append(SearchAttemptData(
        source,
        query,
        status,
        len(rows),
        kept,
        (
            f"GOOD={quality_counts[QUALITY_GOOD]}; OK={quality_counts[QUALITY_OK]}; "
            f"WEAK={quality_counts[QUALITY_WEAK]}; TRASH={quality_counts[QUALITY_TRASH]}"
        ),
    ))


def _normalised_title_key(title: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", (title or "").lower()).strip()


def _searchapi_store_key(candidate: ProductCandidate) -> str:
    store = (candidate.seller or extract_domain(candidate.url) or "").lower().strip()
    title = _normalised_title_key(candidate.title)
    return f"{store}|{title}" if store and title else ""


def _searchapi_dedupe_keys(candidate: ProductCandidate) -> set[str]:
    keys: set[str] = set()
    normalized_url = _normalise_url(candidate.url)
    title_key = _normalised_title_key(candidate.title)
    store_key = _searchapi_store_key(candidate)
    if normalized_url:
        keys.add(f"url:{normalized_url}")
    if title_key:
        keys.add(f"title:{title_key}")
    if store_key:
        keys.add(f"store_title:{store_key}")
    return keys


def _existing_searchapi_dedupe_keys(collection: SearchCollection) -> set[str]:
    keys: set[str] = set()
    for candidate in collection.raw_candidates:
        keys.update(_searchapi_dedupe_keys(candidate))
    return keys


def _request_as_searchapi_parsed(req: Request) -> dict[str, object]:
    return {
        "original_query": req.original_query,
        "product_name": req.product_name or req.product,
        "product": req.product,
        "budget": req.budget,
        "city": req.city,
        "important_criteria": req.important_criteria or req.criteria,
    }


def _collect_from_searchapi(
    req: Request,
    query: str,
    collection: SearchCollection,
    seen_urls: set[str],
) -> None:
    try:
        debug = debug_searchapi_google_shopping(query, parsed=_request_as_searchapi_parsed(req))
    except Exception as exc:
        logger.debug("Автопоиск: %s не выполнил %r: %s", SEARCHAPI_SOURCE, query, exc)
        collection.attempts.append(SearchAttemptData(SEARCHAPI_SOURCE, query, "ERROR", error_text=str(exc)))
        return

    rows = list(debug.get("candidates") or [])
    debug_status = str(debug.get("status") or "error")
    if debug_status not in {"ok", "empty"}:
        logger.debug(
            "Автопоиск: %s status=%s query=%r elapsed=%ss error=%s",
            SEARCHAPI_SOURCE,
            debug_status,
            query,
            debug.get("elapsed", 0),
            debug.get("error") or "",
        )
        collection.attempts.append(SearchAttemptData(
            SEARCHAPI_SOURCE,
            query,
            debug_status.upper(),
            0,
            0,
            str(debug.get("error") or debug.get("error_class") or ""),
        ))
        return

    existing_keys = _existing_searchapi_dedupe_keys(collection)
    kept = 0
    duplicates = 0
    quality_counts = {QUALITY_GOOD: 0, QUALITY_OK: 0, QUALITY_WEAK: 0, QUALITY_TRASH: 0}
    for row in rows:
        try:
            candidate = _candidate_from_row(row, req, SEARCHAPI_SOURCE)
        except Exception as exc:
            logger.warning("Автопоиск: не удалось разобрать карточку %s: %s", SEARCHAPI_SOURCE, exc)
            continue
        if not candidate:
            continue
        normalized_url = _normalise_url(candidate.url)
        dedupe_keys = _searchapi_dedupe_keys(candidate)
        if normalized_url in seen_urls or existing_keys.intersection(dedupe_keys):
            duplicates += 1
            continue
        if normalized_url:
            seen_urls.add(normalized_url)
        existing_keys.update(dedupe_keys)
        collection.raw_candidates.append(candidate)
        _bump_quality_stats(collection, candidate)
        quality_counts[candidate.quality] += 1
        if candidate.quality != QUALITY_TRASH:
            collection.candidates.append(candidate)
            kept += 1
    status = "OK" if rows else "EMPTY"
    collection.attempts.append(SearchAttemptData(
        SEARCHAPI_SOURCE,
        query,
        status,
        len(rows),
        kept,
        (
            f"GOOD={quality_counts[QUALITY_GOOD]}; OK={quality_counts[QUALITY_OK]}; "
            f"WEAK={quality_counts[QUALITY_WEAK]}; TRASH={quality_counts[QUALITY_TRASH]}; "
            f"duplicates={duplicates}"
        ),
    ))


def _collect_from_serpapi(
    req: Request,
    query: str,
    collection: SearchCollection,
    seen_urls: set[str],
) -> None:
    try:
        debug = debug_serpapi_google_shopping(query, parsed=_request_as_searchapi_parsed(req))
    except Exception as exc:
        logger.debug("Автопоиск: %s не выполнил %r: %s", SERPAPI_SOURCE, query, exc)
        collection.attempts.append(SearchAttemptData(SERPAPI_SOURCE, query, "ERROR", error_text=str(exc)))
        return

    rows = list(debug.get("candidates") or [])
    debug_status = str(debug.get("status") or "error")
    if debug_status not in {"ok", "empty"}:
        logger.debug(
            "Автопоиск: %s status=%s query=%r elapsed=%ss error=%s",
            SERPAPI_SOURCE,
            debug_status,
            query,
            debug.get("elapsed", 0),
            debug.get("error") or "",
        )
        collection.attempts.append(SearchAttemptData(
            SERPAPI_SOURCE,
            query,
            debug_status.upper(),
            0,
            0,
            str(debug.get("error") or debug.get("error_class") or ""),
        ))
        return

    existing_keys = _existing_searchapi_dedupe_keys(collection)
    kept = 0
    duplicates = 0
    quality_counts = {QUALITY_GOOD: 0, QUALITY_OK: 0, QUALITY_WEAK: 0, QUALITY_TRASH: 0}
    for row in rows:
        try:
            candidate = _candidate_from_row(row, req, SERPAPI_SOURCE)
        except Exception as exc:
            logger.warning("Автопоиск: не удалось разобрать карточку %s: %s", SERPAPI_SOURCE, exc)
            continue
        if not candidate:
            continue
        normalized_url = _normalise_url(candidate.url)
        dedupe_keys = _searchapi_dedupe_keys(candidate)
        if normalized_url in seen_urls or existing_keys.intersection(dedupe_keys):
            duplicates += 1
            continue
        if normalized_url:
            seen_urls.add(normalized_url)
        existing_keys.update(dedupe_keys)
        collection.raw_candidates.append(candidate)
        _bump_quality_stats(collection, candidate)
        quality_counts[candidate.quality] += 1
        if candidate.quality != QUALITY_TRASH:
            collection.candidates.append(candidate)
            kept += 1
    status = "OK" if rows else "EMPTY"
    collection.attempts.append(SearchAttemptData(
        SERPAPI_SOURCE,
        query,
        status,
        len(rows),
        kept,
        (
            f"GOOD={quality_counts[QUALITY_GOOD]}; OK={quality_counts[QUALITY_OK]}; "
            f"WEAK={quality_counts[QUALITY_WEAK]}; TRASH={quality_counts[QUALITY_TRASH]}; "
            f"duplicates={duplicates}"
        ),
    ))


def _collect_from_direct_retail(
    req: Request,
    query: str,
    collection: SearchCollection,
    seen_urls: set[str],
) -> None:
    try:
        debug_rows = debug_search_direct_retail_sources(query, parsed=_request_as_searchapi_parsed(req))
    except Exception as exc:
        logger.debug("Автопоиск: direct retail не выполнил %r: %s", query, exc)
        collection.attempts.append(SearchAttemptData("direct_retail", query, "ERROR", error_text=str(exc)))
        return

    existing_keys = _existing_searchapi_dedupe_keys(collection)
    for debug in debug_rows:
        source = str(debug.get("source") or "direct_retail")
        status_text = str(debug.get("status") or "error")
        rows = list(debug.get("candidates") or [])
        if status_text not in {"ok", "empty"} and not rows:
            collection.attempts.append(SearchAttemptData(
                source,
                query,
                status_text.upper(),
                int(debug.get("raw_count") or 0),
                0,
                str(debug.get("error") or "")[:250],
            ))
            continue

        kept = 0
        duplicates = 0
        quality_counts = {QUALITY_GOOD: 0, QUALITY_OK: 0, QUALITY_WEAK: 0, QUALITY_TRASH: 0}
        for row in rows:
            try:
                candidate = _candidate_from_row(row, req, source)
            except Exception as exc:
                logger.warning("Автопоиск: не удалось разобрать карточку %s: %s", source, exc)
                continue
            if not candidate:
                continue
            normalized_url = _normalise_url(candidate.url)
            dedupe_keys = _searchapi_dedupe_keys(candidate)
            if normalized_url in seen_urls or existing_keys.intersection(dedupe_keys):
                duplicates += 1
                continue
            if normalized_url:
                seen_urls.add(normalized_url)
            existing_keys.update(dedupe_keys)
            collection.raw_candidates.append(candidate)
            _bump_quality_stats(collection, candidate)
            quality_counts[candidate.quality] += 1
            if candidate.quality != QUALITY_TRASH:
                collection.candidates.append(candidate)
                kept += 1
        status = "OK" if rows else "EMPTY"
        collection.attempts.append(SearchAttemptData(
            source,
            query,
            status,
            int(debug.get("raw_count") or len(rows)),
            kept,
            (
                f"GOOD={quality_counts[QUALITY_GOOD]}; OK={quality_counts[QUALITY_OK]}; "
                f"WEAK={quality_counts[QUALITY_WEAK]}; TRASH={quality_counts[QUALITY_TRASH]}; "
                f"duplicates={duplicates}; elapsed={debug.get('elapsed', 0)}s"
            ),
        ))


def _verified_status(candidate: ProductCandidate) -> str:
    return str(getattr(candidate, "verify_status", "") or "")


def _model_dedupe_key(candidate: ProductCandidate) -> str:
    model_key = extract_model_key(candidate.title).lower()
    if model_key and len(model_key) >= 5:
        return model_key
    text = re.sub(r"[^a-zа-яё0-9]+", " ", candidate.title.lower())
    stop = {
        "телевизор", "smart", "tv", "led", "qled", "oled", "ultra", "hd",
        "черный", "чёрный", "купить", "см", "дюйм", "дюймов",
    }
    words = [word for word in text.split() if word not in stop]
    return " ".join(words[:6]) or candidate.url.lower()


def _verification_rank(req: Request, candidate: ProductCandidate) -> tuple[int, int, float, int, int, int, int, int, str]:
    order = {
        VERIFIED_GOOD: 0,
        VERIFIED_OK: 1,
        NEED_MANUAL_CHECK: 2,
        VERIFY_BLOCKED: 2,
        OVER_BUDGET_SOFT: 3,
    }
    try:
        price = int(candidate.price or 0)
    except (TypeError, ValueError):
        price = 0
    budget_status = _candidate_budget_status(req, candidate)
    budget_order = 0 if budget_status == "IN_BUDGET" else 1 if budget_status == "" else 2
    risk_order = int(
        _has_risk_flag(candidate, LOW_PRICE_RISK)
        or _has_risk_flag(candidate, CITY_MISMATCH_RISK)
        or _has_risk_flag(candidate, GENERIC_TITLE_RISK)
    )
    low_price_order = int(_has_risk_flag(candidate, LOW_PRICE_RISK) or getattr(candidate, "low_price_suspect", False))
    brand_quality = getattr(candidate, "brand_quality", "") or _headphone_brand_quality(req, candidate)
    brand_order = 0
    if _is_headphones_request(req):
        if brand_quality == "known_headphone_brand":
            brand_order = -1
        elif brand_quality == "unknown_headphone_brand":
            brand_order = 1
    return (
        low_price_order,
        brand_order,
        -float(candidate.score or 0),
        risk_order,
        order.get(_verified_status(candidate), 9),
        1 if candidate.price is None else 0,
        budget_order,
        price or 10_000_000,
        candidate.title.lower(),
    )


def _dedupe_verified_candidates(req: Request, candidates: list[ProductCandidate]) -> list[ProductCandidate]:
    grouped: dict[str, list[ProductCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(_model_dedupe_key(candidate), []).append(candidate)

    result: list[ProductCandidate] = []
    for items in grouped.values():
        items.sort(key=lambda item: _verification_rank(req, item))
        kept_for_group = items[:2]
        hidden = max(0, len(items) - len(kept_for_group))
        if hidden:
            kept_for_group[0].risk_flags = list(dict.fromkeys(
                list(kept_for_group[0].risk_flags) + [f"скрыто дублей: {hidden}"]
            ))
        result.extend(kept_for_group)
    result.sort(key=lambda item: _verification_rank(req, item))
    return result


def _demote_verified_item(item: object, status: str, reason: str) -> None:
    candidate = item.candidate
    item.verify_status = status
    item.keep_for_admin = True
    if not getattr(item, "reason", ""):
        item.reason = reason
    risks = list(getattr(item, "risk_flags", []) or [])
    risks.extend([reason, DIRECT_QUALITY_MANUAL_RISK])
    item.risk_flags = list(dict.fromkeys(str(risk) for risk in risks if str(risk).strip()))
    candidate.verify_status = status
    candidate.why_not_verified_good = reason
    _add_risk_flag(candidate, reason)
    _add_risk_flag(candidate, DIRECT_QUALITY_MANUAL_RISK)
    if status in {VERIFY_BLOCKED, NEED_MANUAL_CHECK}:
        candidate.quality = QUALITY_WEAK
        candidate.status = "WEAK_CANDIDATE"
    elif status == VERIFIED_OK:
        candidate.quality = QUALITY_OK
        candidate.status = "CANDIDATE"


def _apply_direct_retail_verified_sanity(req: Request, verified_items: list[object]) -> None:
    for item in verified_items:
        candidate = item.candidate
        if not _is_direct_retail_candidate(candidate):
            continue
        brand_quality = _headphone_brand_quality(req, candidate)
        if brand_quality:
            candidate.brand_quality = brand_quality
        product_quality_level = _direct_product_quality_level(req, candidate)
        if product_quality_level:
            candidate.product_quality_level = product_quality_level
        if product_quality_level == "retail_new_for_used_request":
            _add_risk_flag(candidate, DIRECT_RETAIL_USED_RISK)
            candidate.why_not_verified_good = DIRECT_RETAIL_USED_RISK
        weak_quality = _has_weak_classification_risk(candidate)
        if weak_quality:
            _add_risk_flag(candidate, DIRECT_QUALITY_MANUAL_RISK)
            candidate.why_not_verified_good = "weak classification не подтверждает качество товара"
        if item.verify_status != VERIFIED_GOOD:
            continue
        if product_quality_level == "retail_new_for_used_request":
            _demote_verified_item(item, NEED_MANUAL_CHECK, DIRECT_RETAIL_USED_RISK)
        elif weak_quality:
            _demote_verified_item(item, NEED_MANUAL_CHECK, DIRECT_QUALITY_MANUAL_RISK)
        elif brand_quality == "unknown_headphone_brand":
            _demote_verified_item(item, VERIFIED_OK, "бренд наушников не распознан")


def _apply_final_product_quality_guard(req: Request, verified_items: list[object]) -> None:
    for item in verified_items:
        candidate = item.candidate
        level, reason = _final_product_quality_level(req, candidate)
        if not level:
            continue
        candidate.product_quality_level = level
        if level == "bad":
            _add_risk_flag(candidate, reason or BAD_PRODUCT_RISK)
            candidate.why_not_verified_good = reason or BAD_PRODUCT_RISK
            candidate.quality = QUALITY_TRASH
            candidate.status = "REJECTED_AUTO"
            item.keep_for_admin = False
            if item.verify_status == VERIFIED_GOOD:
                item.verify_status = NOT_PRODUCT_PAGE
            if not getattr(item, "reason", ""):
                item.reason = reason or BAD_PRODUCT_RISK
            item.risk_flags = list(dict.fromkeys(list(getattr(item, "risk_flags", []) or []) + [reason or BAD_PRODUCT_RISK]))
            continue
        if level != "weak":
            continue
        _add_risk_flag(candidate, reason or DIRECT_QUALITY_MANUAL_RISK)
        _add_risk_flag(candidate, DIRECT_QUALITY_MANUAL_RISK)
        candidate.why_not_verified_good = reason or DIRECT_QUALITY_MANUAL_RISK
        candidate.quality = QUALITY_WEAK
        candidate.status = "WEAK_CANDIDATE"
        if item.verify_status in {VERIFIED_GOOD, VERIFIED_OK, OVER_BUDGET_SOFT}:
            item.verify_status = NEED_MANUAL_CHECK
            candidate.verify_status = NEED_MANUAL_CHECK
            item.keep_for_admin = True
        else:
            candidate.verify_status = item.verify_status
        if not getattr(item, "reason", ""):
            item.reason = candidate.why_not_verified_good
        item.risk_flags = list(dict.fromkeys(
            list(getattr(item, "risk_flags", []) or []) + [candidate.why_not_verified_good, DIRECT_QUALITY_MANUAL_RISK]
        ))


def _apply_verification(collection: SearchCollection, req: Request) -> None:
    candidates_to_verify = [
        item for item in collection.candidates
        if item.status != "REJECTED_AUTO" and item.quality != QUALITY_TRASH
    ]
    verified = verify_candidates(candidates_to_verify, req, limit=30)
    verified, playwright_summary = verify_with_playwright_fallback(verified, req)
    _apply_direct_retail_verified_sanity(req, verified)
    _apply_final_product_quality_guard(req, verified)
    stats = collection.verify_stats
    stats["checked"] = len(verified)
    stats["playwright_used"] = playwright_summary.used
    stats["playwright_verified"] = playwright_summary.verified
    stats["playwright_failed"] = playwright_summary.failed
    stats["playwright_skipped"] = playwright_summary.skipped
    stats["playwright_skipped_reason"] = playwright_summary.skipped_reason or {}
    stats["browser_provider"] = playwright_summary.browser_provider
    stats["browser_provider_fallback_reason"] = playwright_summary.browser_provider_fallback_reason
    stats["manual_check_after_playwright"] = playwright_summary.manual_check

    kept: list[ProductCandidate] = []
    manual_without_price: list[ProductCandidate] = []
    for item in verified:
        status = item.verify_status
        stats[status] = stats.get(status, 0) + 1
        if item.keep_for_admin and item.candidate.price:
            kept.append(item.candidate)
        elif item.keep_for_admin and status in {VERIFY_BLOCKED, NEED_MANUAL_CHECK} and getattr(item.candidate, "playwright_used", False):
            manual_without_price.append(item.candidate)
        else:
            collection.verified_rejections.append(item)

    if len(kept) < 3 and manual_without_price:
        extra = manual_without_price[:3 - len(kept)]
        kept.extend(extra)
        stats["manual_check_saved_without_price"] = len(extra)

    for candidate in kept:
        _apply_ranking_sanity(req, candidate)
    kept = _dedupe_verified_candidates(req, kept)
    # В админку отправляем не весь хвост поисковой выдачи, а только лучшие.
    collection.candidates = kept[:10]
    stats["low_price_suspect"] = sum(1 for item in collection.candidates if _has_risk_flag(item, LOW_PRICE_RISK))
    stats["city_mismatch"] = sum(1 for item in collection.candidates if _has_risk_flag(item, CITY_MISMATCH_RISK))
    stats["price_from_budget_suspect"] = sum(1 for item in collection.candidates if getattr(item, "price_from_budget_suspect", False))
    stats["bad_price_context"] = sum(1 for item in collection.candidates if getattr(item, "bad_price_context", False))
    stats["score_cap_applied"] = sum(1 for item in collection.candidates if getattr(item, "score_cap_applied", ""))
    stats["saved"] = len(collection.candidates)
    collection.quality_stats["saved"] = len(collection.candidates)
    collection.attempts.append(SearchAttemptData(
        "candidate_verifier",
        "verify candidate pages",
        "OK",
        stats["checked"],
        stats["saved"],
        (
            f"VERIFY_ERROR={stats.get('VERIFY_ERROR', 0)}; "
            f"{VERIFY_BLOCKED}={stats.get(VERIFY_BLOCKED, 0)}; "
            f"{NEED_MANUAL_CHECK}={stats.get(NEED_MANUAL_CHECK, 0)}; "
            f"UNAVAILABLE={stats.get('UNAVAILABLE', 0)}; "
            f"{REMOVED_LISTING}={stats.get(REMOVED_LISTING, 0)}; "
            f"{PRICE_MISSING}={stats.get(PRICE_MISSING, 0)}; "
            f"{BAD_ENCODING}={stats.get(BAD_ENCODING, 0)}; "
            f"PRICE_MISMATCH={stats.get('PRICE_MISMATCH', 0)}; "
            f"WRONG_PRODUCT={stats.get('WRONG_PRODUCT', 0)}; "
            f"NOT_PRODUCT_PAGE={stats.get('NOT_PRODUCT_PAGE', 0)}; "
            f"VERIFIED_GOOD={stats.get('VERIFIED_GOOD', 0)}; "
            f"VERIFIED_OK={stats.get('VERIFIED_OK', 0)}; "
            f"OVER_BUDGET_SOFT={stats.get('OVER_BUDGET_SOFT', 0)}; "
            f"OVER_BUDGET_HARD={stats.get('OVER_BUDGET_HARD', 0)}; "
            f"playwright_used={stats.get('playwright_used', 0)}; "
            f"playwright_verified={stats.get('playwright_verified', 0)}; "
            f"playwright_failed={stats.get('playwright_failed', 0)}; "
            f"playwright_skipped={stats.get('playwright_skipped', 0)}; "
            f"playwright_skipped_reason={stats.get('playwright_skipped_reason', {})}; "
            f"browser_provider={stats.get('browser_provider', '')}; "
            f"browser_provider_fallback_reason={stats.get('browser_provider_fallback_reason', '')}; "
            f"manual_check_after_playwright={stats.get('manual_check_after_playwright', 0)}; "
            f"manual_check_saved_without_price={stats.get('manual_check_saved_without_price', 0)}; "
            f"low_price_suspect={stats.get('low_price_suspect', 0)}; "
            f"city_mismatch={stats.get('city_mismatch', 0)}; "
            f"price_from_budget_suspect={stats.get('price_from_budget_suspect', 0)}; "
            f"bad_price_context={stats.get('bad_price_context', 0)}; "
            f"score_cap_applied={stats.get('score_cap_applied', 0)}; "
            f"saved={stats['saved']}"
        ),
    ))


def collect_product_candidates(req: Request, max_results: int = 15) -> SearchCollection:
    """Собирает кандидатов без записи в БД; удобно для консольного теста."""
    collection = SearchCollection()
    queries = generate_search_queries(req)
    seen_urls: set[str] = set()
    generic = GenericSearchAdapter()

    # Веб-поиск и site-поиск не зависят от Wildberries и друг от друга.
    if _is_generic_enabled():
        generic_queries = [query for query in queries if not query.startswith("site:")]
        for query in generic_queries[:3]:
            _collect_from_adapter(generic, req, "generic_web", query, 5, collection, seen_urls)

    wb_query = (req.clean_search_query or "").strip() or build_search_query(
        _request_product(req), req.use_case or req.purpose, req.budget, "", req.important_criteria or req.criteria,
    )
    wb_source = next((source for source in SEARCH_SOURCES if source.key == "wildberries"), None)
    if wb_source and _is_source_enabled(wb_source):
        _collect_from_adapter(WildberriesAdapter(), req, "wildberries", wb_query, 10, collection, seen_urls)

    site_adapter = SearchBySiteAdapter()
    for query in (query for query in queries if query.startswith("site:")):
        domain_match = re.match(r"site:([^\s]+)", query)
        source = MARKETPLACE_SOURCES.get(domain_match.group(1), "generic_web") if domain_match else "generic_web"
        _collect_from_adapter(site_adapter, req, source, query, 6, collection, seen_urls)

    if settings.DIRECT_RETAIL_ENABLED:
        direct_query = (req.clean_search_query or "").strip() or wb_query
        _collect_from_direct_retail(req, direct_query, collection, seen_urls)

    if settings.SEARCHAPI_ENABLED:
        searchapi_query = (req.clean_search_query or "").strip() or wb_query
        _collect_from_searchapi(req, searchapi_query, collection, seen_urls)

    if settings.SERPAPI_ENABLED:
        serpapi_query = (req.clean_search_query or "").strip() or wb_query
        _collect_from_serpapi(req, serpapi_query, collection, seen_urls)

    _apply_verification(collection, req)
    collection.candidates = collection.candidates[:min(max_results, 10)]
    collection.verify_stats["saved"] = len(collection.candidates)
    collection.quality_stats["saved"] = len(collection.candidates)

    stats = collection.quality_stats
    collection.attempts.append(SearchAttemptData(
        "quality_filter",
        "candidate classification",
        "OK",
        stats["raw"],
        stats["saved"],
        (
            f"RAW={stats['raw']}; TRASH={stats['trash']}; GOOD={stats['good']}; "
            f"OK={stats['ok']}; WEAK={stats['weak']}; saved={stats['saved']}; "
            f"categories={stats['categories']}; articles={stats['articles']}; wrong_type={stats['wrong_type']}"
        ),
    ))

    if not collection.candidates:
        # Эти ссылки никогда не становятся ProductCandidate: они лишь помогут
        # админу продолжить поиск и останутся видны в Debug поиска.
        collection.manual_links = generate_search_links(
            product_name=_request_product(req),
            city=req.city,
            use_case=req.use_case or req.purpose,
            budget=req.budget,
            important_criteria=req.important_criteria or req.criteria,
            clean_search_query=req.clean_search_query or "",
        )
        collection.attempts.append(SearchAttemptData("manual_fallback", wb_query, "OK", len(collection.manual_links), 0,
                                                     "Проверенных вариантов мало или нет; сохранены ручные поисковые ссылки."))
    return collection


def _run_compare_search(req: Request, candidates: list[ProductCandidate], attempts: list[SearchAttemptData]) -> int:
    """Для каждого нормального кандидата ищет ту же модель на других площадках.
    
    Возвращает количество найденных более дешёвых альтернатив.
    """
    found_cheaper = 0
    existing_urls = {_normalise_url(row.url) for row in get_search_results(req.id)}
    generic = GenericSearchAdapter()
    seen_model_keys: dict[str, int] = {}  # ключ → лучшая цена из оригинала

    for candidate in candidates:
        if candidate.status == "REJECTED_AUTO":
            continue
        model_key = extract_model_key(candidate.title)
        if not model_key or len(model_key) < 5:
            continue
        normalized_key = model_key.lower()
        # Запоминаем лучшую цену для этого ключа
        if normalized_key in seen_model_keys:
            seen_model_keys[normalized_key] = min(seen_model_keys[normalized_key], candidate.price or 999999)
        else:
            seen_model_keys[normalized_key] = candidate.price or 999999

        for domain, source in COMPARE_SITES:
            if not _is_source_name_enabled(source):
                continue
            query = f"{model_key} site:{domain}"
            try:
                rows = _search_with_retry(generic, query, limit=3)
            except Exception as exc:
                logger.debug("Compare search: %s не выполнил %r: %s", source, query, exc)
                attempts.append(SearchAttemptData(f"compare_{source}", query, "ERROR", error_text=str(exc)))
                continue
            kept = 0
            for row in rows:
                try:
                    comp = _candidate_from_row(row, req, source)
                except Exception as exc:
                    logger.debug("Compare search: не разобрать карточку %s: %s", source, exc)
                    continue
                if not comp:
                    continue
                normalized_url = _normalise_url(comp.url)
                if normalized_url in existing_urls:
                    continue
                verified = verify_candidate(comp, req)
                if not verified.keep_for_admin or not comp.price or _verified_status(comp) not in {VERIFIED_GOOD, VERIFIED_OK, OVER_BUDGET_SOFT, VERIFY_BLOCKED}:
                    continue
                existing_urls.add(normalized_url)
                # Если цена ниже, чем у оригинального кандидата с тем же ключом
                comp_model_key = extract_model_key(comp.title).lower() or normalized_key
                best_known = seen_model_keys.get(comp_model_key, comp.price or 999999)
                if comp.price and best_known and comp.price < best_known:
                    comp.risk_flags.append("найден как более дешёвая альтернатива")
                    comp.score = min(comp.score + 5, 95)
                    found_cheaper += 1
                seen_model_keys[comp_model_key] = min(best_known, comp.price or 999999)
                comp.status = comp.status if comp.status == "CANDIDATE" else comp.status
                create_search_result(
                    request_id=req.id,
                    title=comp.title,
                    url=comp.url,
                    source=comp.source,
                    price=comp.price,
                    snippet=comp.snippet,
                    score=comp.score,
                    risk_flags=json.dumps(comp.risk_flags, ensure_ascii=False),
                    status=comp.status,
                    facts_json=getattr(comp, "facts_json", "") or json.dumps(getattr(comp, "product_facts", {}) or {}, ensure_ascii=False),
                )
                kept += 1
            status = "OK" if rows else "EMPTY"
            attempts.append(SearchAttemptData(f"compare_{source}", query, status, len(rows), kept))
    return found_cheaper


def run_product_search(req: Request, max_results: int = 15) -> dict:
    """Собирает и сохраняет кандидатов и диагностику в SQLite."""
    collection = collect_product_candidates(req, max_results=max_results)

    if collection.manual_links:
        replace_manual_search_links(req.id, collection.manual_links)

    existing_urls = {_normalise_url(row.url) for row in get_search_results(req.id)}
    added = 0
    for candidate in collection.candidates:
        if _normalise_url(candidate.url) in existing_urls:
            continue
        create_search_result(
            request_id=req.id,
            title=candidate.title,
            url=candidate.url,
            source=candidate.source,
            price=candidate.price,
            snippet=candidate.snippet,
            score=candidate.score,
            risk_flags=json.dumps(candidate.risk_flags, ensure_ascii=False),
            status=candidate.status,
            facts_json=getattr(candidate, "facts_json", "") or json.dumps(getattr(candidate, "product_facts", {}) or {}, ensure_ascii=False),
        )
        existing_urls.add(_normalise_url(candidate.url))
        if candidate.status != "REJECTED_AUTO":
            added += 1

    # Compare search: ищем ту же модель дешевле на других площадках
    normal_candidates = [c for c in collection.candidates if c.quality in {QUALITY_GOOD, QUALITY_OK}]
    compare_found = _run_compare_search(req, normal_candidates, collection.attempts)
    stats = collection.quality_stats
    stats["compare_total"] = len([a for a in collection.attempts if a.source.startswith("compare_")])
    stats["compare_found_cheaper"] = compare_found
    collection.attempts.append(SearchAttemptData(
        "compare_summary",
        f"compare search по {len(normal_candidates)} кандидатам",
        "OK",
        stats["compare_total"],
        compare_found,
        f"compare_total={stats['compare_total']}; compare_found_cheaper={compare_found}",
    ))

    for attempt in collection.attempts:
        create_search_attempt(
            req.id, attempt.source, attempt.query, attempt.status,
            attempt.found_count, attempt.kept_count, attempt.error_text,
        )

    total = sum(1 for result in get_search_results(req.id) if result.status != "REJECTED_AUTO")
    if total:
        message = f"Найдено {added} новых кандидатов, всего {total}."
        if collection.verify_stats.get("saved", 0) < 3:
            message += " Нормальных проверенных вариантов мало. Нужно ручное уточнение / Алиса / Gemini."
        return {
            "success": True,
            "found": added,
            "total": total,
            "message": message,
        }
    return {
        "success": False,
        "found": 0,
        "total": 0,
        "message": "Автопоиск не получил карточек товаров. Откройте «🧪 Debug поиска» или добавьте варианты вручную.",
    }
