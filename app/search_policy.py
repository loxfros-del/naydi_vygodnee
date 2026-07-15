from __future__ import annotations

import re
from typing import Any

from app.exact_match import ACCESSORY, COMPATIBLE_VARIANT, EXACT, MODEL_MISMATCH, REQUIRED_SPEC_MISMATCH
from app.search_evidence import assess_product_card


NORMAL_STATUSES = {"VERIFIED_GOOD", "VERIFIED_OK"}

ADMIN_SAVE_STATUSES = {
    "VERIFIED_GOOD",
    "VERIFIED_OK",
    "NEED_MANUAL_CHECK",
    "VERIFY_BLOCKED",
    "PRICE_MISSING",
    "OVER_BUDGET_SOFT",
    "WEAK_CANDIDATE",
}

DROP_STATUSES = {
    "WRONG_PRODUCT",
    "UNAVAILABLE",
    "REMOVED_LISTING",
    "NOT_PRODUCT_PAGE",
    "OVER_BUDGET_HARD",
    "BAD_ENCODING",
}

_STALE_MODEL_REASONS = (
    "нет признаков конкретной модели",
    "точная модель не указана",
    "классификация: weak — нет признаков конкретной модели",
    "классификация: weak - нет признаков конкретной модели",
    "weak classification не подтверждает качество товара",
)

REAL_BLOCK_MARKERS = (
    "403",
    "401",
    "429",
    "captcha",
    "timeout",
    "forbidden",
    "unauthorized",
    "rate_limited",
    "blocked",
    "network",
    "proxy",
)

ARTICLE_MARKERS = (
    "топ",
    "рейтинг",
    "обзор",
    "отзывы",
    "как выбрать",
    "лучшие",
    "сравнение",
    "инструкция",
    "pdf",
)

CATEGORY_URL_MARKERS = (
    "/search",
    "/catalog/",
    "/category/",
    "/brand/",
    "/collections/",
)

PRODUCT_URL_MARKERS = (
    "/product/",
    "/products/",
    "/card/",
    "/detail",
    "/details/",
    "/item/",
    "/goods/",
)


def _text(candidate: dict[str, Any]) -> str:
    parts = [
        candidate.get("title"),
        candidate.get("snippet"),
        candidate.get("description"),
        candidate.get("url"),
        " ".join(map(str, candidate.get("reasons") or [])),
        " ".join(map(str, candidate.get("flags") or [])),
    ]
    return " ".join(str(x) for x in parts if x).lower()


def get_status(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("verify_status")
        or candidate.get("status")
        or candidate.get("final_status")
        or ""
    ).upper()


def set_status(candidate: dict[str, Any], status: str) -> dict[str, Any]:
    candidate["verify_status"] = status
    candidate["status"] = status
    return candidate


def add_reason(candidate: dict[str, Any], reason: str) -> None:
    if not reason:
        return
    reasons = candidate.setdefault("reasons", [])
    if reason not in reasons:
        reasons.append(reason)

    if not candidate.get("why_not_verified_good"):
        candidate["why_not_verified_good"] = reason


def clean_final_reasons(candidate: dict[str, Any]) -> list[str]:
    """Deduplicates reasons and removes claims contradicted by final evidence."""
    exact_status = str(candidate.get("exact_match_status") or candidate.get("exact_match") or "").upper()
    status = get_status(candidate)
    strong_exact = exact_status in {EXACT, COMPATIBLE_VARIANT}
    mismatch = exact_status in {MODEL_MISMATCH, REQUIRED_SPEC_MISMATCH, ACCESSORY}
    values = [
        *(candidate.get("reasons") or []),
        *(candidate.get("risk_flags") or candidate.get("flags") or []),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" ;,.-")
        if not text:
            continue
        lowered = text.casefold()
        if strong_exact and any(marker in lowered for marker in _STALE_MODEL_REASONS):
            continue
        if mismatch and ("точная модель" in lowered or "точное соответствие" in lowered):
            continue
        if status in NORMAL_STATUSES and lowered in {
            "нужна ручная проверка", "нужна ручная проверка качества товара",
        }:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(text)
    candidate["reasons"] = result
    candidate["risk_flags"] = result
    candidate["why_not_verified_good"] = "" if status in NORMAL_STATUSES else (result[0] if result else "")
    return result


def cap_score(candidate: dict[str, Any], cap: int, reason: str) -> None:
    try:
        score = int(candidate.get("score") or 0)
    except Exception:
        score = 0

    if score > cap:
        candidate["score"] = cap
        candidate["score_cap_applied"] = reason
    elif "score" not in candidate:
        candidate["score"] = min(score, cap)


def is_normal_candidate(candidate: dict[str, Any]) -> bool:
    return get_status(candidate) in NORMAL_STATUSES


def is_real_block(candidate: dict[str, Any]) -> bool:
    text = _text(candidate)
    return any(marker in text for marker in REAL_BLOCK_MARKERS)


def looks_like_article_or_category(candidate: dict[str, Any]) -> bool:
    text = _text(candidate)
    url = str(candidate.get("url") or "").lower()

    if any(marker in text for marker in ARTICLE_MARKERS):
        return True

    if any(marker in url for marker in CATEGORY_URL_MARKERS):
        if not any(marker in url for marker in PRODUCT_URL_MARKERS):
            return True

    if url.endswith(".pdf"):
        return True

    return False


def looks_like_product_card(candidate: dict[str, Any]) -> bool:
    return assess_product_card(candidate).is_product_card


def normalize_for_admin_save(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Делает финальную честную политику:
    - GOOD/OK остаются normal.
    - manual/blocked/price_missing можно сохранить админу.
    - wrong/unavailable/trash не считаются normal и обычно не сохраняются.
    """

    quality = str(candidate.get("product_quality_level") or "").lower()
    price = candidate.get("price")
    budget_status = str(candidate.get("budget_status") or "").upper()
    text = _text(candidate)
    card = assess_product_card(candidate)
    candidate["product_card_confidence"] = card.confidence
    candidate["product_card_reason"] = card.reason
    exact_status = str(candidate.get("exact_match_status") or candidate.get("exact_match") or "").upper()

    if exact_status in {MODEL_MISMATCH, REQUIRED_SPEC_MISMATCH, ACCESSORY}:
        set_status(candidate, "WRONG_PRODUCT")
        cap_score(candidate, 20, exact_status.lower())
        add_reason(candidate, str(candidate.get("exact_match_reason") or "обязательные требования не совпадают"))
        clean_final_reasons(candidate)
        return candidate

    if quality == "bad":
        set_status(candidate, "WRONG_PRODUCT")
        cap_score(candidate, 30, "bad_product_quality")
        add_reason(candidate, "мусорный или неподходящий товар")
        clean_final_reasons(candidate)
        return candidate

    if looks_like_article_or_category(candidate):
        set_status(candidate, "NOT_PRODUCT_PAGE")
        cap_score(candidate, 30, "not_product_page")
        add_reason(candidate, "не карточка товара")
        clean_final_reasons(candidate)
        return candidate

    if not card.is_product_card and card.confidence == "none":
        set_status(candidate, "NOT_PRODUCT_PAGE")
        cap_score(candidate, 30, "not_product_page")
        add_reason(candidate, card.reason or "не карточка товара")
        clean_final_reasons(candidate)
        return candidate

    unavailable_markers = (
        "товар закончился",
        "нет в наличии",
        "скоро снова поступит",
        "посмотреть аналоги",
        "sold out",
        "unavailable",
    )
    if any(marker in text for marker in unavailable_markers):
        set_status(candidate, "UNAVAILABLE")
        cap_score(candidate, 40, "unavailable")
        add_reason(candidate, "товар недоступен")
        clean_final_reasons(candidate)
        return candidate

    status = get_status(candidate)
    if status in NORMAL_STATUSES and card.confidence == "low":
        set_status(candidate, "NEED_MANUAL_CHECK")
        cap_score(candidate, 65, "low_product_card_confidence")
        add_reason(candidate, "низкая уверенность, что это карточка товара")
        status = "NEED_MANUAL_CHECK"
    if status == "VERIFY_BLOCKED" and not is_real_block(candidate):
        set_status(candidate, "NEED_MANUAL_CHECK")
        cap_score(candidate, 80, "manual_check_not_blocked")
        add_reason(candidate, "нужна ручная проверка")
        status = "NEED_MANUAL_CHECK"

    if budget_status == "OVER_BUDGET_HARD" or status == "OVER_BUDGET_HARD":
        set_status(candidate, "OVER_BUDGET_HARD")
        cap_score(candidate, 40, "over_budget_hard")
        add_reason(candidate, "цена сильно выше бюджета")
        clean_final_reasons(candidate)
        return candidate

    strong_fresh_evidence = bool(
        exact_status in {EXACT, COMPATIBLE_VARIANT}
        and price not in (None, "", 0)
        and candidate.get("available") is True
        and card.confidence == "high"
        and str(candidate.get("verification_confidence") or "").lower() == "high"
        and str(candidate.get("price_confidence") or "").lower() == "high"
    )
    if quality == "weak" and status in NORMAL_STATUSES and not strong_fresh_evidence:
        set_status(candidate, "NEED_MANUAL_CHECK")
        cap_score(candidate, 80, "weak_quality")
        add_reason(candidate, "нужна ручная проверка качества товара")
        status = "NEED_MANUAL_CHECK"

    if price in (None, "", 0):
        if status in NORMAL_STATUSES:
            set_status(candidate, "PRICE_MISSING")
            status = "PRICE_MISSING"
        cap_score(candidate, 40, "price_missing")
        add_reason(candidate, "цена не подтверждена")

    if budget_status == "OVER_BUDGET_SOFT":
        if status in NORMAL_STATUSES:
            set_status(candidate, "OVER_BUDGET_SOFT")
            status = "OVER_BUDGET_SOFT"
        cap_score(candidate, 60, "over_budget_soft")
        add_reason(candidate, "цена выше бюджета")

    clean_final_reasons(candidate)
    return candidate


def should_save_for_admin(candidate: dict[str, Any]) -> bool:
    status = get_status(candidate)

    if status in DROP_STATUSES:
        return False

    if looks_like_article_or_category(candidate):
        return False

    if status in NORMAL_STATUSES:
        return True

    if status in {"NEED_MANUAL_CHECK", "VERIFY_BLOCKED", "OVER_BUDGET_SOFT", "WEAK_CANDIDATE"}:
        return looks_like_product_card(candidate)

    if status == "PRICE_MISSING":
        return looks_like_product_card(candidate)

    return False


def summarize_saved_statuses(candidates: list[dict[str, Any]]) -> dict[str, int]:
    result = {
        "saved_for_admin": len(candidates),
        "VERIFIED_GOOD": 0,
        "VERIFIED_OK": 0,
        "NEED_MANUAL_CHECK": 0,
        "VERIFY_BLOCKED": 0,
        "PRICE_MISSING": 0,
        "WEAK_OTHER": 0,
        "WRONG_UNAVAILABLE_OVER_BUDGET": 0,
    }

    for candidate in candidates:
        status = get_status(candidate)
        if status in result:
            result[status] += 1
        elif status in {"WRONG_PRODUCT", "UNAVAILABLE", "OVER_BUDGET_SOFT", "OVER_BUDGET_HARD"}:
            result["WRONG_UNAVAILABLE_OVER_BUDGET"] += 1
        else:
            result["WEAK_OTHER"] += 1

    return result
