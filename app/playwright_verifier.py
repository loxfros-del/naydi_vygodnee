"""Fallback-проверка карточек через Playwright.

Модуль намеренно не участвует в обычной проверке: браузер запускается только
по env-флагу и только для первых проблемных кандидатов.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.db import Request
from app.net_client import get_domain_policy
from app.price_extractor import extract_price
from app.candidate_verifier import (
    NEED_MANUAL_CHECK,
    NOT_PRODUCT_PAGE,
    OVER_BUDGET_HARD,
    OVER_BUDGET_SOFT,
    PRICE_MISSING,
    REMOVED_LISTING,
    UNAVAILABLE,
    VERIFIED_GOOD,
    VERIFIED_OK,
    VERIFY_BLOCKED,
    VERIFY_ERROR,
    VerifiedCandidate,
    apply_verified_candidate,
    extract_availability,
    extract_product_facts,
    extract_verified_price,
    is_valid_product_page,
)


FALLBACK_STATUSES = {VERIFY_BLOCKED, NEED_MANUAL_CHECK, PRICE_MISSING, VERIFY_ERROR}
BLOCKED_SKIP_REASONS = {"rate_limited", "blocked_498"}
PLAYWRIGHT_DOMAIN_POLICY = {
    "ozon_search": {"max_candidates": 2, "browser_fallback": True},
    "wildberries": {"max_candidates": 0, "browser_fallback": False},
    "avito_search": {"max_candidates": 0, "browser_fallback": False},
    "dns_search": {"max_candidates": None, "browser_fallback": True},
    "mvideo_search": {"max_candidates": None, "browser_fallback": True},
    "citilink_search": {"max_candidates": None, "browser_fallback": True},
    "yandex_market_search": {"max_candidates": None, "browser_fallback": True},
    "megamarket_search": {"max_candidates": None, "browser_fallback": True},
}
BLOCKED_MARKERS = (
    "captcha", "капча", "подтвердите, что вы не робот", "вы не робот",
    "access denied", "доступ запрещ", "доступ ограничен", "forbidden",
    "too many requests", "429", "anti-bot", "проверяем ваш браузер",
    "enable javascript", "включите javascript",
)
BAD_URL_MARKERS = (
    "/search", "/catalog/0/search", "/category/", "/categories/", "/blog/",
    "/article/", "/articles/", "/reviews", "/review", "/otzyv", "/otzyvy",
    "/compare", "/support", "/manual", "/help", "/offers", "/characteristics",
    "/specification",
)
PRICE_SELECTORS = (
    '[itemprop="price"]',
    '[data-auto*="price"]',
    '[data-widget*="price"]',
    '[class*="price"]',
    '[class*="Price"]',
)
GENERIC_TITLE_MARKERS = (
    "интернет-магазин",
    "большой каталог",
    "низкие цены",
    "результаты поиска",
    "каталог товаров",
)
MODEL_CODE_RE = re.compile(
    r"\b(?:[A-Za-zА-Яа-я]{1,6}\d{2,}[A-Za-zА-Яа-я0-9-]*|\d{2,}[A-Za-zА-Яа-я]{1,6}\d+[A-Za-zА-Яа-я0-9-]*)\b"
)


@dataclass
class PlaywrightFallbackSummary:
    used: int = 0
    verified: int = 0
    failed: int = 0
    manual_check: int = 0
    skipped: int = 0
    skipped_reason: dict[str, int] | None = None
    browser_provider: str = "local"
    browser_provider_fallback_reason: str = ""


@dataclass(frozen=True)
class BrowserProviderConfig:
    requested: str
    effective: str
    fallback_reason: str = ""


def _browser_provider_config() -> BrowserProviderConfig:
    requested = str(getattr(settings, "BROWSER_PROVIDER", "local") or "local").strip().lower()
    if requested not in {"local", "browserbase"}:
        return BrowserProviderConfig(requested=requested, effective="local", fallback_reason="unknown_provider")
    if requested == "local":
        return BrowserProviderConfig(requested="local", effective="local")

    api_key = str(getattr(settings, "BROWSERBASE_API_KEY", "") or "").strip()
    project_id = str(getattr(settings, "BROWSERBASE_PROJECT_ID", "") or "").strip()
    if not api_key or not project_id:
        return BrowserProviderConfig(
            requested="browserbase",
            effective="local",
            fallback_reason="missing_browserbase_credentials",
        )
    return BrowserProviderConfig(requested="browserbase", effective="browserbase")


def is_playwright_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_PLAYWRIGHT_VERIFIER", False)) and _max_candidates() > 0


def _timeout_ms() -> int:
    return max(1000, int(getattr(settings, "PLAYWRIGHT_TIMEOUT_MS", 8000) or 8000))


def _max_candidates() -> int:
    return max(0, int(getattr(settings, "PLAYWRIGHT_MAX_CANDIDATES", 5) or 0))


def _source(candidate: Any) -> str:
    return str(getattr(candidate, "source", "") or "")


def _title(candidate: Any) -> str:
    return str(getattr(candidate, "title", "") or "").strip()


def _url(candidate: Any) -> str:
    return str(getattr(candidate, "url", "") or "").strip()


def _looks_like_product_url(candidate: Any) -> bool:
    url = _url(candidate)
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower().rstrip("/")
    query = parsed.query.lower()
    if not parsed.scheme or not parsed.netloc:
        return False
    if any(marker in path for marker in BAD_URL_MARKERS):
        return False
    if any(key in query for key in ("q=", "text=", "search=", "query=")):
        return False

    source = _source(candidate)
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
    if source == "citilink_search":
        return "citilink.ru" in domain and ("/product/" in path or bool(re.search(r"-\d{5,}$", path)))
    if source == "megamarket_search":
        return "megamarket.ru" in domain and ("/catalog/details/" in path or "/product/" in path)
    if source == "avito_search":
        return "avito.ru" in domain and bool(re.search(r"_\d{5,}$", path))
    return False


def _has_generic_title(candidate: Any) -> bool:
    title = _title(candidate).lower()
    return any(marker in title for marker in GENERIC_TITLE_MARKERS) and MODEL_CODE_RE.search(title) is None


def _policy(candidate: Any) -> dict[str, Any]:
    return PLAYWRIGHT_DOMAIN_POLICY.get(_source(candidate), {"max_candidates": None, "browser_fallback": False})


def _candidate_blocked_reason(candidate: Any) -> str:
    blocked_reason = str(getattr(candidate, "blocked_reason", "") or "").strip()
    if blocked_reason:
        return blocked_reason
    text = " ".join(str(item) for item in (getattr(candidate, "risk_flags", []) or [])).lower()
    if "429" in text or "rate_limited" in text:
        return "rate_limited"
    if "498" in text or "blocked_498" in text:
        return "blocked_498"
    return ""


def _skip_reason(item: VerifiedCandidate) -> str:
    candidate = item.candidate
    blocked_reason = _candidate_blocked_reason(candidate)
    if item.verify_status == VERIFIED_GOOD:
        return "already_good"
    if item.verify_status not in FALLBACK_STATUSES:
        return "status_not_eligible"
    if _source(candidate) == "generic_web":
        return "generic_web"
    if blocked_reason in BLOCKED_SKIP_REASONS:
        return blocked_reason
    if not _title(candidate) or not _url(candidate):
        return "missing_title_or_url"
    if not _looks_like_product_url(candidate):
        return "not_product_url"
    if _has_generic_title(candidate) and getattr(candidate, "price", None) is None:
        return "generic_title"
    local_policy = _policy(candidate)
    if not local_policy.get("browser_fallback", False):
        return f"{_source(candidate) or 'unknown'}_manual_check"
    if not get_domain_policy(_url(candidate)).browser_fallback:
        return "domain_policy_disabled"
    return ""


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _budget_value(req: Request) -> int | None:
    try:
        value = int(str(req.budget).replace(" ", ""))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _blocked_reason(title: str, text: str, status_code: int | None = None, error: str = "") -> str:
    if status_code == 429:
        return "rate_limited"
    if status_code == 498:
        return "blocked_498"
    if status_code in {401, 403}:
        return "forbidden"
    lowered = f"{title} {text[:3000]} {error}".lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "too many requests" in lowered or "429" in lowered:
        return "rate_limited"
    if "captcha" in lowered or "капча" in lowered or "вы не робот" in lowered:
        return "captcha"
    if "access denied" in lowered or "доступ запрещ" in lowered or "forbidden" in lowered:
        return "forbidden"
    if any(marker in lowered for marker in BLOCKED_MARKERS):
        return "captcha"
    return ""


def _body_text(page: Any) -> str:
    try:
        return _normalise(page.locator("body").inner_text(timeout=min(_timeout_ms(), 3000)))
    except Exception:
        return ""


def _page_title(page: Any, fallback: str) -> str:
    for selector in ("h1", '[data-auto="product-title"]', '[itemprop="name"]'):
        try:
            value = page.locator(selector).first.inner_text(timeout=800)
            if value:
                return _normalise(value)[:300]
        except Exception:
            pass
    try:
        value = page.title()
        if value:
            return _normalise(value)[:300]
    except Exception:
        pass
    return fallback[:300]


def _visible_price_text(page: Any) -> str:
    chunks: list[str] = []
    for selector in PRICE_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 8)
            for index in range(count):
                value = locator.nth(index).inner_text(timeout=400)
                if value:
                    chunks.append(value)
        except Exception:
            continue
    return _normalise(" ".join(chunks))


def _extract_playwright_price(page: Any, source: str, html: str, text: str, title: str) -> int | None:
    price = extract_verified_price(source, html, text, title)
    if price is not None:
        return price
    visible_price_text = _visible_price_text(page)
    if not visible_price_text:
        return None
    return extract_price(f"{title} {visible_price_text}", min_price=1_000, max_price=10_000_000)


def _avito_risks(req: Request) -> list[str]:
    risks = ["проверить продавца", "проверить город", "проверить состояние", "проверить отзывы"]
    if not req.is_used_allowed:
        risks.append("б/у не разрешено клиентом")
    return risks


def _manual_result(
    candidate: Any,
    req: Request,
    reason: str,
    text: str = "",
    blocked_reason: str = "",
) -> VerifiedCandidate:
    candidate.playwright_used = True
    candidate.playwright_verified = False
    candidate.playwright_failed = True
    if blocked_reason:
        candidate.blocked_reason = blocked_reason
    if getattr(candidate, "price", None) is not None and not getattr(candidate, "price_source", ""):
        candidate.price_source = "search"
    risks = ["браузерная проверка не подтвердила товар"]
    if reason:
        risks.append(reason[:180])
    if _source(candidate) == "avito_search":
        risks.extend(_avito_risks(req))
    facts = extract_product_facts(
        candidate,
        req,
        text=text,
        title=_title(candidate),
        price=getattr(candidate, "price", None),
        availability="UNKNOWN",
    )
    facts["playwright_checked"] = True
    facts["playwright_verified"] = False
    if blocked_reason:
        facts["blocked_reason"] = blocked_reason
    verified = VerifiedCandidate(
        candidate,
        VERIFY_BLOCKED,
        price=getattr(candidate, "price", None),
        title=_title(candidate),
        availability="UNKNOWN",
        reason=reason[:180] if reason else "",
        risk_flags=risks,
        facts=facts,
        html_loaded=False,
        keep_for_admin=True,
    )
    return apply_verified_candidate(candidate, verified)


def _price_status(price: int, req: Request) -> str:
    budget = _budget_value(req)
    if not budget:
        return VERIFIED_OK
    if price > budget * 1.15:
        return OVER_BUDGET_HARD
    if price > budget:
        return OVER_BUDGET_SOFT
    return VERIFIED_OK


def _verify_page(page: Any, candidate: Any, req: Request) -> VerifiedCandidate:
    candidate.playwright_used = True
    candidate.playwright_verified = False
    candidate.playwright_failed = False
    timeout = _timeout_ms()
    try:
        response = page.goto(_url(candidate), wait_until="domcontentloaded", timeout=timeout)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout, 3000))
        except Exception:
            pass
    except Exception as exc:
        reason = _blocked_reason("", "", error=str(exc)) or "timeout"
        return _manual_result(
            candidate,
            req,
            f"браузерная проверка не открыла страницу: {reason}",
            blocked_reason=reason,
        )

    status_code = response.status if response else None
    text = _body_text(page)
    title = _page_title(page, _title(candidate))
    html = page.content()
    final_url = page.url or _url(candidate)

    blocked_reason = _blocked_reason(title, text, status_code)
    if blocked_reason:
        return _manual_result(
            candidate,
            req,
            f"сайт заблокировал браузерную проверку: {blocked_reason}",
            text=text,
            blocked_reason=blocked_reason,
        )

    if not is_valid_product_page(final_url, html, title):
        facts = extract_product_facts(candidate, req, html, text, title, price=getattr(candidate, "price", None), availability="UNKNOWN")
        facts["playwright_checked"] = True
        facts["blocked_reason"] = "not_product_page"
        verified = VerifiedCandidate(
            candidate,
            NOT_PRODUCT_PAGE,
            title=title,
            availability="UNKNOWN",
            reason="браузерная проверка: не карточка товара",
            risk_flags=["браузерная проверка: не карточка товара"],
            facts=facts,
            html_loaded=True,
            keep_for_admin=False,
        )
        candidate.playwright_failed = True
        candidate.blocked_reason = "not_product_page"
        return apply_verified_candidate(candidate, verified)

    availability = extract_availability(_source(candidate), html, text)
    price = _extract_playwright_price(page, _source(candidate), html, text, title)

    if availability == REMOVED_LISTING:
        facts = extract_product_facts(candidate, req, html, text, title, price=price, availability=availability)
        facts["playwright_checked"] = True
        verified = VerifiedCandidate(
            candidate,
            REMOVED_LISTING,
            price=price,
            title=title,
            availability=availability,
            reason="браузерная проверка: объявление/страница недоступны",
            facts=facts,
            html_loaded=True,
            keep_for_admin=False,
        )
        candidate.playwright_failed = True
        return apply_verified_candidate(candidate, verified)

    if availability == "UNAVAILABLE":
        facts = extract_product_facts(candidate, req, html, text, title, price=price, availability=availability)
        facts["playwright_checked"] = True
        verified = VerifiedCandidate(
            candidate,
            UNAVAILABLE,
            price=price,
            title=title,
            availability=availability,
            reason="браузерная проверка: товар недоступен",
            facts=facts,
            html_loaded=True,
            keep_for_admin=False,
        )
        candidate.playwright_failed = True
        return apply_verified_candidate(candidate, verified)

    if price is None:
        if getattr(candidate, "price", None) is not None:
            return _manual_result(
                candidate,
                req,
                "браузерная проверка не подтвердила цену",
                text=text,
                blocked_reason="price_missing",
            )
        facts = extract_product_facts(candidate, req, html, text, title, price=None, availability=availability)
        facts["playwright_checked"] = True
        facts["blocked_reason"] = "price_missing"
        verified = VerifiedCandidate(
            candidate,
            PRICE_MISSING,
            title=title,
            availability=availability,
            reason="браузерная проверка: цена не найдена",
            risk_flags=["браузерная проверка: цена не найдена"],
            facts=facts,
            html_loaded=True,
            keep_for_admin=False,
        )
        candidate.playwright_failed = True
        candidate.blocked_reason = "price_missing"
        return apply_verified_candidate(candidate, verified)

    status = _price_status(price, req)
    if status == VERIFIED_OK and availability == "AVAILABLE":
        status = VERIFIED_GOOD
    risks = ["проверено браузером"]
    if availability != "AVAILABLE":
        risks.append("наличие браузером не подтверждено")
    if _source(candidate) == "avito_search":
        risks.extend(_avito_risks(req))
        if status == VERIFIED_GOOD:
            status = VERIFIED_OK
    if status == OVER_BUDGET_SOFT:
        risks.append("чуть выше бюджета")
    elif status == OVER_BUDGET_HARD:
        risks.append("выше бюджета более чем на 15%")

    facts = extract_product_facts(candidate, req, html, text, title, price=price, availability=availability)
    facts["playwright_checked"] = True
    facts["playwright_verified"] = True
    facts["price_source"] = "playwright"
    candidate.price_source = "playwright"
    candidate.playwright_verified = True
    candidate.playwright_failed = False
    candidate.blocked_reason = ""
    verified = VerifiedCandidate(
        candidate,
        status,
        price=price,
        title=title,
        availability=availability,
        reason="" if status != OVER_BUDGET_HARD else status,
        risk_flags=risks,
        facts=facts,
        html_loaded=True,
        keep_for_admin=status in {VERIFIED_GOOD, VERIFIED_OK, OVER_BUDGET_SOFT},
    )
    return apply_verified_candidate(candidate, verified)


def verify_with_playwright_fallback(
    verified_items: list[VerifiedCandidate],
    req: Request,
) -> tuple[list[VerifiedCandidate], PlaywrightFallbackSummary]:
    summary = PlaywrightFallbackSummary()
    provider_config = _browser_provider_config()
    summary.browser_provider = provider_config.effective
    summary.browser_provider_fallback_reason = provider_config.fallback_reason
    if not is_playwright_enabled():
        return verified_items, summary

    skipped_reasons: Counter[str] = Counter()
    selected: list[tuple[int, VerifiedCandidate]] = []
    per_source: Counter[str] = Counter()
    for index, item in enumerate(verified_items):
        reason = _skip_reason(item)
        if reason:
            if reason != "status_not_eligible":
                skipped_reasons[reason] += 1
            continue
        source = _source(item.candidate)
        max_for_source = _policy(item.candidate).get("max_candidates")
        if max_for_source is not None and per_source[source] >= int(max_for_source):
            skipped_reasons[f"{source}_limit"] += 1
            continue
        selected.append((index, item))
        per_source[source] += 1
        if len(selected) >= _max_candidates():
            break

    summary.skipped = sum(skipped_reasons.values())
    summary.skipped_reason = dict(skipped_reasons)
    if not selected:
        return verified_items, summary

    if provider_config.effective == "browserbase":
        return _verify_with_browserbase_stub(verified_items, req, selected, summary)
    return _verify_with_local_playwright(verified_items, req, selected, summary)


def _verify_with_browserbase_stub(
    verified_items: list[VerifiedCandidate],
    req: Request,
    selected: list[tuple[int, VerifiedCandidate]],
    summary: PlaywrightFallbackSummary,
) -> tuple[list[VerifiedCandidate], PlaywrightFallbackSummary]:
    summary.browser_provider = "browserbase"
    for index, item in selected:
        summary.used += 1
        summary.failed += 1
        result = _manual_result(
            item.candidate,
            req,
            "browserbase provider не подключён в этой сборке",
            blocked_reason="browserbase_not_implemented",
        )
        verified_items[index] = result
        if result.verify_status == VERIFY_BLOCKED:
            summary.manual_check += 1
    return verified_items, summary


def _verify_with_local_playwright(
    verified_items: list[VerifiedCandidate],
    req: Request,
    selected: list[tuple[int, VerifiedCandidate]],
    summary: PlaywrightFallbackSummary,
) -> tuple[list[VerifiedCandidate], PlaywrightFallbackSummary]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        for index, item in selected:
            summary.used += 1
            summary.failed += 1
            result = _manual_result(item.candidate, req, f"Playwright недоступен: {exc}")
            verified_items[index] = result
            if result.verify_status == VERIFY_BLOCKED:
                summary.manual_check += 1
        return verified_items, summary

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=bool(getattr(settings, "PLAYWRIGHT_HEADLESS", True)))
            context = browser.new_context(
                locale="ru-RU",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            try:
                for index, item in selected:
                    summary.used += 1
                    page = context.new_page()
                    try:
                        result = _verify_page(page, item.candidate, req)
                    except Exception as exc:
                        result = _manual_result(item.candidate, req, f"ошибка Playwright: {exc}")
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass
                    verified_items[index] = result
                    if getattr(result.candidate, "playwright_verified", False):
                        summary.verified += 1
                    else:
                        summary.failed += 1
                    if result.verify_status == VERIFY_BLOCKED:
                        summary.manual_check += 1
            finally:
                context.close()
                browser.close()
    except Exception as exc:
        for index, item in selected:
            summary.used += 1
            summary.failed += 1
            result = _manual_result(item.candidate, req, f"браузерная проверка не запустилась: {exc}")
            verified_items[index] = result
            if result.verify_status == VERIFY_BLOCKED:
                summary.manual_check += 1

    return verified_items, summary
