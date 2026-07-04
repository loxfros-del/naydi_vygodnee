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


def _safe_get(url: str) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NaydiVygodneeBot/2.0)",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    }
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            response = requests.get(url, headers=headers, timeout=_timeout(), allow_redirects=True)
            if response.status_code == 429:
                response.raise_for_status()
            if response.status_code in {500, 502, 503, 504} and attempt == 0:
                time.sleep(0.25)
                continue
            response.raise_for_status()
            if not response.encoding or response.encoding.lower() in {"iso-8859-1", "windows-1252"}:
                response.encoding = response.apparent_encoding or "utf-8"
            return response
        except requests.HTTPError:
            raise
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(0.25)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("страница не открылась")


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
    candidate.availability = verified.availability
    candidate.description = getattr(candidate, "description", "") or getattr(candidate, "snippet", "")
    candidate.verify_status = verified.verify_status
    if verified.verify_status == VERIFIED_GOOD:
        candidate.quality = "GOOD"
        candidate.status = "CANDIDATE"
        candidate.score = min(float(getattr(candidate, "score", 0) or 0) + 15, 99)
    elif verified.verify_status in {VERIFIED_OK, OVER_BUDGET_SOFT}:
        candidate.quality = "OK"
        candidate.status = "CANDIDATE"
    elif verified.keep_for_admin:
        candidate.quality = "WEAK"
        candidate.status = "WEAK_CANDIDATE"
    else:
        candidate.quality = "TRASH"
        candidate.status = "REJECTED_AUTO"
    return verified


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
        # Если страница не открылась, не падаем, но не показываем как проверенный товар.
        verified = VerifiedCandidate(
            candidate,
            VERIFY_ERROR,
            price=getattr(candidate, "price", None),
            title=getattr(candidate, "title", ""),
            availability="UNKNOWN",
            reason=f"ошибка открытия сайта: {str(exc)[:180]}",
            risk_flags=["страница не проверена"],
            html_loaded=False,
            keep_for_admin=False,
        )
        return _apply_verified(candidate, verified)

    html = response.text or ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    page_title = _page_title(soup, getattr(candidate, "title", ""))
    page_title, bad_title = _repair_mojibake(page_title)
    if bad_title:
        verified = VerifiedCandidate(candidate, BAD_ENCODING, title=page_title, reason="битая кодировка title", html_loaded=True)
        return _apply_verified(candidate, verified)
    if not is_valid_product_page(response.url or url, html, page_title):
        verified = VerifiedCandidate(candidate, NOT_PRODUCT_PAGE, title=page_title, reason="не карточка товара", html_loaded=True)
        return _apply_verified(candidate, verified)

    availability = extract_availability(source, html, text)
    price = extract_verified_price(source, html, text, page_title)
    risks: list[str] = []
    if availability == REMOVED_LISTING:
        verified = VerifiedCandidate(
            candidate, REMOVED_LISTING, price=price, title=page_title,
            availability=availability, reason="объявление/страница недоступны", html_loaded=True,
        )
        return _apply_verified(candidate, verified)
    if availability == "UNAVAILABLE":
        verified = VerifiedCandidate(
            candidate, UNAVAILABLE, price=price, title=page_title,
            availability=availability, reason="товар недоступен", html_loaded=True,
        )
        return _apply_verified(candidate, verified)
    if price is None:
        verified = VerifiedCandidate(
            candidate, PRICE_MISSING, title=page_title,
            availability=availability, reason="цена не подтверждена", html_loaded=True,
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

    if getattr(candidate, "source", "") == "avito_search" and not req.is_used_allowed and verify_status == VERIFIED_GOOD:
        verify_status = VERIFIED_OK
        risks.extend(["проверить продавца", "проверить город", "проверить состояние", "проверить отзывы", "б/у не разрешено клиентом"])

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
        html_loaded=True,
        keep_for_admin=keep,
    )
    return _apply_verified(candidate, verified)


def verify_candidates(candidates: list[Any], req: Request, limit: int = 30) -> list[VerifiedCandidate]:
    result: list[VerifiedCandidate] = []
    for candidate in candidates[:limit]:
        result.append(verify_candidate(candidate, req))
    return result
