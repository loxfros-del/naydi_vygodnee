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
from typing import Iterable, Optional
from urllib.parse import urlparse, urlunparse, urlencode

import requests

from app.db import (
    Request,
    create_search_attempt,
    create_search_result,
    get_search_results,
    replace_manual_search_links,
)
from app.price_extractor import extract_price
from app.search_links import build_search_query, generate_search_links

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
    quality_stats: dict[str, int] = field(default_factory=lambda: {
        "total_found": 0,
        "categories": 0,
        "articles": 0,
        "wrong_type": 0,
        "weak": 0,
        "normal": 0,
    })



MARKETPLACE_SOURCES = {
    "wildberries.ru": "wildberries",
    "ozon.ru": "ozon_search",
    "market.yandex.ru": "yandex_market_search",
    "dns-shop.ru": "dns_search",
    "mvideo.ru": "mvideo_search",
    "avito.ru": "avito_search",
}
SITE_SOURCES = (
    ("ozon.ru", "ozon_search"),
    ("market.yandex.ru", "yandex_market_search"),
    ("dns-shop.ru", "dns_search"),
    ("mvideo.ru", "mvideo_search"),
    ("avito.ru", "avito_search"),
)
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
)
# Иностранные домены производителей: их витрины/каталоги не являются
# карточкой товара для покупки в РФ.
FOREIGN_VENDOR_DOMAINS = ("tcl.com", "lg.com", "samsung.com", "sony.com", "philips.com", "hisense.com", "haier.com")
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
}
CATEGORY_MARKERS = (
    "купить по низкой цене", "купить на ozon", "купить на яндекс маркете",
    "телевизоры 4k купить", "каталог", "все товары", "низкие цены",
    "большой ассортимент", "быстрая доставка", "оригинальные товары",
    "распродажа", "скидки и акции",
)
ARTICLE_MARKERS = (
    "лучшие телевизоры", "топ", "рейтинг", "обзор", "подборка", "как выбрать",
    "ign", "игромания", "ixbt", "vc.ru", "dzen", "tiktok", "youtube", "rutube",
)
WRONG_TV_PRODUCT_MARKERS = (
    "консоль", "игровая приставка", "ретро-игры", "проектор", "кронштейн",
    "подсветка", "пульт", "кабель", "подставка", "приставка", "монитор",
    "console", "projector", "bracket", "remote", "cable", "tv stand",
)


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
    queries.extend(f"site:{domain} {site_base}" for domain, _ in SITE_SOURCES)

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


def is_category_page(title: str, snippet: str, url: str) -> bool:
    """Определяет витрины/категории, а не карточки одного товара."""
    text = _candidate_text(title, snippet, url)
    parsed = urlparse(url)
    path = parsed.path.lower()
    is_plural_tv_title = bool(re.search(r"\bтелевизоры\b", title.lower()))
    is_catalog_path = "/catalog/" in path and not ("wildberries" in url.lower() and "/detail" in path)
    is_filter_path = "/f/" in path or "/recipe/" in path
    is_avito_listing = "avito.ru" in url.lower() and "/televizory-as" in path
    is_avito_search = _is_avito_search_page(parsed)
    return (
        any(marker in text for marker in CATEGORY_MARKERS)
        or "/category/" in path
        or is_catalog_path
        or is_filter_path
        or is_plural_tv_title
        or is_avito_listing
        or is_avito_search
    )


def is_article_or_review(title: str, snippet: str, url: str) -> bool:
    """Определяет статьи, видео и обзоры, которые нельзя выдавать за товар."""
    text = _candidate_text(title, snippet, url)
    parsed = urlparse(url)
    path = parsed.path.lower()
    is_editorial_url = any(part in path for part in ("/blog/", "/digest/", "/article/", "/reviews/")) or "club.dns-shop.ru" in url.lower()
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
    # Проект ищет товар в указанном российском городе; Citrus и домены .ua
    # не являются релевантной точкой покупки для такого запроса.
    return bool(request.city) and (domain == "citrus.ua" or domain.endswith(".citrus.ua") or domain.endswith(".ua"))



def _is_tv_request(req: Request) -> bool:
    return any(word in _request_product(req).lower() for word in ("телевизор", " tv"))


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
    trusted_store = candidate.source in {
        "wildberries", "ozon_search", "yandex_market_search", "dns_search", "mvideo_search"
    }
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


def classify_candidate(candidate: ProductCandidate, request: Request) -> tuple[str, list[str]]:
    """Возвращает REJECTED_AUTO только для явного мусора/не-товара."""
    non_purchase_reason = _non_purchase_url_reason(candidate.url)
    if non_purchase_reason:
        return "REJECTED_AUTO", [non_purchase_reason, f"классификация: REJECTED — {non_purchase_reason}"]
    if _is_untrusted_for_russian_request(candidate.url, request):
        return "REJECTED_AUTO", ["нерелевантный зарубежный/социальный источник", "классификация: REJECTED — источник не для покупки в РФ"]
    if is_wrong_product_type(candidate, request):
        return "REJECTED_AUTO", ["не тот тип товара", "классификация: REJECTED — не тот тип товара"]
    if is_category_page(candidate.title, candidate.snippet, candidate.url):
        return "REJECTED_AUTO", ["страница категории", "классификация: REJECTED — страница категории"]
    if is_article_or_review(candidate.title, candidate.snippet, candidate.url):
        return "REJECTED_AUTO", ["обзор/подборка", "классификация: REJECTED — обзор или отзывы"]
    if _is_foreign_vendor_site(candidate.url):
        return "REJECTED_AUTO", ["сайт производителя, не точка покупки", "классификация: REJECTED — не точка покупки"]

    # URL отзывов/характеристик отсекаются выше. В сниппете прямой карточки
    # часто встречаются слова «отзывы» и «характеристики», поэтому здесь
    # оставляем только признаки недоступного товара.
    trash_markers = [
        ("нет в наличии", "нет в наличии"),
        ("продаж прекращен", "продажи прекращены"),
        ("товар закончил", "товар закончился"),
        ("посмотреть аналоги", "нет в наличии"),
        ("no longer available", "нет в наличии"),
        ("out of stock", "нет в наличии"),
    ]
    title_lower = candidate.title.lower()
    snippet_lower = candidate.snippet.lower()
    url_lower = candidate.url.lower()
    for marker, label in trash_markers:
        if marker in title_lower or marker in snippet_lower or marker in url_lower:
            # URL отзывов/характеристик уже отклонён выше. Здесь допускаем
            # карточку с моделью только для текстовых упоминаний в сниппете.
            if _has_concrete_model(candidate):
                return "CANDIDATE", [label]
            return "REJECTED_AUTO", [label, f"классификация: REJECTED — {label}"]

    # Товар с известной ценой выше бюджета не должен попадать в NORMAL.
    budget = _budget_value(request)
    if budget and candidate.price and candidate.price > budget:
        over_budget_pct = (candidate.price - budget) / budget
        # Сильное превышение не имеет смысла показывать для фиксированного бюджета.
        if over_budget_pct > 0.15:
            return "REJECTED_AUTO", [f"выше бюджета ({candidate.price} > {budget})", "классификация: REJECTED — сильно выше бюджета"]
        if over_budget_pct > 0.10:
            return "WEAK_CANDIDATE", [f"сильно выше бюджета ({candidate.price} > {budget})", "классификация: WEAK — выше бюджета более чем на 10%"]
    return "CANDIDATE", []



def _budget_value(req: Request) -> Optional[int]:
    try:
        value = int(str(req.budget).replace(" ", ""))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


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
        if any(word in text for word in ("full hd", "фулл hd", "1080p")):
            score -= 20
        if not has_model:
            score -= 30
            flags.append("нет признаков конкретной модели")
        if _is_ps5_tv(req) and "hdmi" not in text:
            flags.append("HDMI не указан")
        # Для PS5: 60 Гц допустимо, не штрафуем.
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

    if source in {"wildberries", "ozon_search", "yandex_market_search", "dns_search", "mvideo_search"}:
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
    def _parse_payload(response: requests.Response) -> dict:
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

        response = requests.get(
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
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NaydiVygodneeBot/2.0)"},
        )
        response.raise_for_status()
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
    price = raw_price
    min_price, max_price = (7_000, 300_000) if _is_tv_request(req) else (1_000, 10_000_000)
    if not isinstance(price, int) or not min_price <= price <= max_price:
        price = extract_price(f"{title} {snippet}", min_price=min_price, max_price=max_price)
    source = _source_from_url(url, default_source)
    candidate = ProductCandidate(
        request_id=req.id,
        title=title[:300],
        url=url[:500],
        source=source,
        price=price,
        snippet=snippet[:500],
        created_at=datetime.now().isoformat(),
    )
    status, classification_flags = classify_candidate(candidate, req)
    score, risks = score_result(title, snippet, price, req, source, status, classification_flags)
    if isinstance(raw_price, int) and not min_price <= raw_price <= max_price and re.search(r"\b(?:[A-Za-z]{1,6}\d{2,}[A-Za-z0-9-]*|\d{2,}[A-Za-z]{1,6}\d+)\b", title):
        risks.append("цена похожа на номер модели")
    # Неполная карточка магазина — не мусор. Оставляем её как WEAK для
    # проверки администратором вместо прежнего автоматического отклонения.
    if status == "CANDIDATE":
        weak_reasons = _weak_reasons(candidate, req, score)
        if weak_reasons:
            status = "WEAK_CANDIDATE"
            risks.extend(weak_reasons)
            risks.append(f"классификация: WEAK — {'; '.join(weak_reasons)}")
        else:
            risks.append("классификация: NORMAL — товарная карточка с достаточными признаками")
    candidate.status = status
    candidate.score = score
    candidate.risk_flags = list(dict.fromkeys(risks))
    return candidate



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
        rows = adapter.search(query, limit=limit)
    except Exception as exc:  # каждый источник изолирован от остальных
        logger.warning("Автопоиск: %s не выполнил %r: %s", source, query, exc)
        collection.attempts.append(SearchAttemptData(source, query, "ERROR", error_text=str(exc)))
        return

    kept = 0
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
        collection.candidates.append(candidate)
        collection.quality_stats["total_found"] += 1
        if candidate.status == "REJECTED_AUTO":
            if "страница категории" in candidate.risk_flags:
                collection.quality_stats["categories"] += 1
            elif "обзор/подборка" in candidate.risk_flags:
                collection.quality_stats["articles"] += 1
            elif "не тот тип товара" in candidate.risk_flags:
                collection.quality_stats["wrong_type"] += 1
        elif candidate.status == "WEAK_CANDIDATE":
            collection.quality_stats["weak"] += 1
            kept += 1
        else:
            collection.quality_stats["normal"] += 1
            kept += 1
    status = "OK" if rows else "EMPTY"
    collection.attempts.append(SearchAttemptData(source, query, status, len(rows), kept))


def collect_product_candidates(req: Request, max_results: int = 15) -> SearchCollection:
    """Собирает кандидатов без записи в БД; удобно для консольного теста."""
    collection = SearchCollection()
    queries = generate_search_queries(req)
    seen_urls: set[str] = set()
    generic = GenericSearchAdapter()

    # Веб-поиск и site-поиск не зависят от Wildberries и наоборот.
    for query in (query for query in queries if not query.startswith("site:")):
        _collect_from_adapter(generic, req, "generic_web", query, 5, collection, seen_urls)

    wb_query = (req.clean_search_query or "").strip() or build_search_query(
        _request_product(req), req.use_case or req.purpose, req.budget, "", req.important_criteria or req.criteria,
    )
    _collect_from_adapter(WildberriesAdapter(), req, "wildberries", wb_query, 10, collection, seen_urls)

    site_adapter = SearchBySiteAdapter()
    for query in (query for query in queries if query.startswith("site:")):
        domain_match = re.match(r"site:([^\s]+)", query)
        source = MARKETPLACE_SOURCES.get(domain_match.group(1), "generic_web") if domain_match else "generic_web"
        _collect_from_adapter(site_adapter, req, source, query, 5, collection, seen_urls)

    stats = collection.quality_stats
    collection.attempts.append(SearchAttemptData(
        "quality_filter",
        "candidate classification",
        "OK",
        stats["total_found"],
        stats["normal"] + stats["weak"],
        (
            f"categories={stats['categories']}; articles={stats['articles']}; "
            f"wrong_type={stats['wrong_type']}; weak={stats['weak']}; normal={stats['normal']}"

        ),
    ))

    source_priority = {
        "wildberries": 0, "ozon_search": 1, "yandex_market_search": 1,
        "dns_search": 1, "mvideo_search": 1, "avito_search": 2, "generic_web": 3,
    }
    budget = _budget_value(req)
    trusted_sources = {"wildberries", "ozon_search", "yandex_market_search", "dns_search", "mvideo_search", "avito_search"}

    # По умолчанию в список идут только полноценные NORMAL/WEAK карточки.
    active_candidates = [item for item in collection.candidates if item.status != "REJECTED_AUTO"]
    has_normal = any(item.status == "CANDIDATE" for item in active_candidates)

    # Если выдача не дала ни одной NORMAL-карточки, можно показать в самом
    # низу несколько product-like страниц отзывов/характеристик. Это не
    # «готовый товар»: у каждой будет явная причина WEAK для ручной проверки.
    if not has_normal and len(active_candidates) < 8:
        for item in collection.raw_candidates:
            if len(active_candidates) >= 8:
                break
            if item.status != "REJECTED_AUTO" or not _non_purchase_url_reason(item.url):
                continue
            item_text = f"{item.title} {item.snippet}".lower()
            has_identity = (
                _has_concrete_model(item)
                or (any(brand in item_text for brand in KNOWN_TV_BRANDS) and bool(re.search(r"\b(43|50|55|65)\b", item_text)))
            )
            if item.source not in trusted_sources or not has_identity:
                continue
            fallback_reason = _non_purchase_url_reason(item.url)
            if "обзор/подборка" in item.risk_flags:
                collection.quality_stats["articles"] = max(0, collection.quality_stats["articles"] - 1)
            item.status = "WEAK_CANDIDATE"
            item.score = min(item.score, 5.0)
            item.risk_flags = list(dict.fromkeys(item.risk_flags + [
                f"fallback без NORMAL: {fallback_reason}",
                f"классификация: WEAK — {fallback_reason}, проверьте основную карточку",
            ]))
            active_candidates.append(item)
            collection.quality_stats["weak"] += 1

    # Отражаем fallback в Debug поиска: RAW не меняется, но число пригодных
    # NORMAL/WEAK обновляется.
    for attempt in reversed(collection.attempts):
        if attempt.source == "quality_filter":
            attempt.kept_count = sum(item.status != "REJECTED_AUTO" for item in collection.raw_candidates)
            attempt.error_text = (
                f"categories={collection.quality_stats['categories']}; articles={collection.quality_stats['articles']}; "
                f"wrong_type={collection.quality_stats['wrong_type']}; weak={collection.quality_stats['weak']}; "
                f"normal={collection.quality_stats['normal']}"
            )
            break

    def candidate_rank(item: ProductCandidate) -> tuple[int, float, int, str]:
        in_budget = bool(item.price and budget and item.price <= budget)
        if item.status == "CANDIDATE":
            # Лучшие: NORMAL в бюджете, затем NORMAL без цены.
            group = 0 if in_budget else 1 if item.price is None else 2
        elif item.status == "WEAK_CANDIDATE":
            # После NORMAL — карточки доверенных магазинов, затем прочие без цены.
            is_fallback = any(flag.startswith("fallback без NORMAL:") for flag in item.risk_flags)
            group = 6 if is_fallback else 3 if item.source in trusted_sources else 4 if item.price is None else 5
        else:
            group = 9
        return group, -item.score, source_priority.get(item.source, 4), item.title.lower()

    active_candidates.sort(key=candidate_rank)
    collection.candidates = active_candidates[:max_results]
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
                                                     "Реальных карточек не получено; сохранены ручные поисковые ссылки."))
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
            query = f"{model_key} site:{domain}"
            try:
                rows = generic.search(query, limit=3)
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
                )
                kept += 1
            status = "OK" if rows else "EMPTY"
            attempts.append(SearchAttemptData(f"compare_{source}", query, status, len(rows), kept))
    return found_cheaper


def run_product_search(req: Request, max_results: int = 15) -> dict:
    """Собирает и сохраняет кандидатов и диагностику в SQLite."""
    collection = collect_product_candidates(req, max_results=max_results)

    for attempt in collection.attempts:
        create_search_attempt(
            req.id, attempt.source, attempt.query, attempt.status,
            attempt.found_count, attempt.kept_count, attempt.error_text,
        )
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
        )
        existing_urls.add(_normalise_url(candidate.url))
        if candidate.status != "REJECTED_AUTO":
            added += 1

    # Compare search: ищем ту же модель дешевле на других площадках
    normal_candidates = [c for c in collection.candidates if c.status != "REJECTED_AUTO"]
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

    total = sum(1 for result in get_search_results(req.id) if result.status != "REJECTED_AUTO")
    if total:
        return {
            "success": True,
            "found": added,
            "total": total,
            "message": f"Найдено {added} новых кандидатов, всего {total}.",
        }
    return {
        "success": False,
        "found": 0,
        "total": 0,
        "message": "Автопоиск не получил карточек товаров. Откройте «🧪 Debug поиска» или добавьте варианты вручную.",
    }
