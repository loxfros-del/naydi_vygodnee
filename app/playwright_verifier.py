"""Fallback-проверка карточек через Playwright.

Модуль намеренно не участвует в обычной проверке: браузер запускается только
по env-флагу и только для первых проблемных кандидатов.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.config import settings
from app.db import Request
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
BLOCKED_MARKERS = (
    "captcha", "капча", "подтвердите, что вы не робот", "вы не робот",
    "access denied", "доступ запрещ", "доступ ограничен", "forbidden",
    "too many requests", "429", "anti-bot", "проверяем ваш браузер",
    "enable javascript", "включите javascript",
)


@dataclass
class PlaywrightFallbackSummary:
    used: int = 0
    verified: int = 0
    failed: int = 0
    manual_check: int = 0


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


def _eligible(item: VerifiedCandidate, index: int) -> bool:
    candidate = item.candidate
    return (
        index < _max_candidates()
        and item.verify_status in FALLBACK_STATUSES
        and bool(_title(candidate))
        and bool(_url(candidate))
    )


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _budget_value(req: Request) -> int | None:
    try:
        value = int(str(req.budget).replace(" ", ""))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _looks_blocked(title: str, text: str, status_code: int | None) -> bool:
    if status_code in {401, 403, 429, 498}:
        return True
    lowered = f"{title} {text[:3000]}".lower()
    return any(marker in lowered for marker in BLOCKED_MARKERS)


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


def _avito_risks(req: Request) -> list[str]:
    risks = ["проверить продавца", "проверить город", "проверить состояние", "проверить отзывы"]
    if not req.is_used_allowed:
        risks.append("б/у не разрешено клиентом")
    return risks


def _manual_result(candidate: Any, req: Request, reason: str, text: str = "") -> VerifiedCandidate:
    candidate.playwright_used = True
    candidate.playwright_verified = False
    candidate.playwright_failed = True
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
        return _manual_result(candidate, req, f"браузерная проверка не открыла страницу: {exc}")

    status_code = response.status if response else None
    text = _body_text(page)
    title = _page_title(page, _title(candidate))
    html = page.content()
    final_url = page.url or _url(candidate)

    if _looks_blocked(title, text, status_code):
        return _manual_result(candidate, req, "сайт заблокировал браузерную проверку", text=text)

    if not is_valid_product_page(final_url, html, title):
        facts = extract_product_facts(candidate, req, html, text, title, price=getattr(candidate, "price", None), availability="UNKNOWN")
        facts["playwright_checked"] = True
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
        return apply_verified_candidate(candidate, verified)

    availability = extract_availability(_source(candidate), html, text)
    price = extract_verified_price(_source(candidate), html, text, title)

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
            return _manual_result(candidate, req, "браузерная проверка не подтвердила цену", text=text)
        facts = extract_product_facts(candidate, req, html, text, title, price=None, availability=availability)
        facts["playwright_checked"] = True
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
        return apply_verified_candidate(candidate, verified)

    status = _price_status(price, req)
    if status == VERIFIED_OK and availability == "AVAILABLE":
        status = VERIFIED_GOOD
    risks = ["проверено браузером"]
    if availability != "AVAILABLE":
        risks.append("наличие браузером не подтверждено")
    if _source(candidate) == "avito_search":
        risks.extend(_avito_risks(req))
        if not req.is_used_allowed and status == VERIFIED_GOOD:
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
    if not is_playwright_enabled():
        return verified_items, summary

    selected = [(index, item) for index, item in enumerate(verified_items) if _eligible(item, index)]
    if not selected:
        return verified_items, summary

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
                    if result.verify_status in {VERIFIED_GOOD, VERIFIED_OK, OVER_BUDGET_SOFT}:
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
