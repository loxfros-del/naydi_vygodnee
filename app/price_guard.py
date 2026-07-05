"""Защитный слой для цен из поисковой выдачи и ранжирования."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


PRICE_RELIABILITY_HIGH = "high"
PRICE_RELIABILITY_MEDIUM = "medium"
PRICE_RELIABILITY_LOW = "low"
PRICE_RELIABILITY_NONE = "none"

TOO_CHEAP_FOR_BUDGET_RISK = "слишком дешёвый товар для бюджета"

_BAD_CONTEXT_RE = re.compile(
    r"(?:до|бюджет|макс|максимум|mah|mAh|мач|гц|hz|отзыв|отзывов|"
    r"вопрос|вопросов|артикул|мес|кэшбэк|кешбэк|балл|баллы|баллов)",
    re.IGNORECASE,
)
_MONEY_CONTEXT_RE = re.compile(
    r"(?:₽|руб(?:\.|лей|ля|ль)?|р\.|цена|стоимость|currentPrice|salePrice)",
    re.IGNORECASE,
)
_CHEAP_WORD_RE = re.compile(r"(?:сам(?:ый|ые|ая)\s+деш[её]в|деш[её]в|бюджетн)", re.IGNORECASE)


@dataclass(frozen=True)
class PriceGuardResult:
    price: int | None
    price_reliability: str
    price_rejected_reason: str = ""
    price_from_budget_suspect: bool = False
    bad_price_context: bool = False
    score_cap_applied: str = ""
    low_price_suspect: bool = False
    risk_flags: tuple[str, ...] = ()


def _budget_value(req: Any = None, budget: Any = None) -> int | None:
    raw = budget
    if raw is None and req is not None:
        raw = getattr(req, "budget", None)
    text = str(raw or "").replace(" ", "").replace("\u00a0", "").strip()
    if text.lower().endswith(("к", "k")) and text[:-1].isdigit():
        return int(text[:-1]) * 1000
    return int(text) if text.isdigit() else None


def _context_text(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts).replace("\u00a0", " ")


def _price_patterns(price: int) -> list[re.Pattern[str]]:
    compact = str(price)
    patterns = [re.compile(rf"(?<!\d){re.escape(compact)}(?!\d)")]
    if price >= 1000:
        spaced = f"{price:,}".replace(",", r"[ \u00a0]?")
        patterns.append(re.compile(rf"(?<!\d){spaced}(?!\d)"))
    if price % 1000 == 0:
        short = price // 1000
        patterns.append(re.compile(rf"(?<![\w]){short}\s*[кКkK](?![\w])"))
    return patterns


def _price_spans(text: str, price: int | None) -> Iterable[tuple[int, int]]:
    if price is None:
        return ()
    spans: list[tuple[int, int]] = []
    for pattern in _price_patterns(int(price)):
        spans.extend((match.start(), match.end()) for match in pattern.finditer(text))
    return spans


def _near_text(text: str, start: int, end: int, window: int = 32) -> str:
    return text[max(0, start - window): min(len(text), end + window)]


def _has_money_context(text: str, price: int | None) -> bool:
    if price is None:
        return False
    for start, end in _price_spans(text, price):
        if _MONEY_CONTEXT_RE.search(_near_text(text, start, end, 48)):
            return True
    return False


def is_bad_price_context(text: str, price: int | None = None, *, window: int = 32) -> bool:
    """True, если число рядом с бюджетом, характеристиками, отзывами и т.п."""
    text = _context_text(text)
    if price is None:
        return bool(_BAD_CONTEXT_RE.search(text))
    for start, end in _price_spans(text, price):
        if _BAD_CONTEXT_RE.search(_near_text(text, start, end, window)):
            return True
    return False


def is_price_from_query_budget(
    price: int | None,
    req: Any = None,
    *,
    budget: Any = None,
    text: str = "",
) -> bool:
    """True, если цена совпала с бюджетом запроса без денежного контекста."""
    value = _budget_value(req, budget)
    if price is None or value is None or int(price) != value:
        return False
    return not _has_money_context(_context_text(text), int(price))


def detect_price_reliability(
    price: int | None,
    *,
    price_source: str = "",
    source: str = "",
    verify_status: str = "",
    raw_price: Any = None,
    text: str = "",
) -> str:
    if price is None:
        return PRICE_RELIABILITY_NONE
    source_text = f"{price_source} {source} {verify_status}".lower()
    if any(marker in source_text for marker in ("requests", "playwright", "api", "verified_good", "verified_ok")):
        return PRICE_RELIABILITY_HIGH
    if isinstance(raw_price, int) and raw_price == price:
        return PRICE_RELIABILITY_MEDIUM
    if _has_money_context(_context_text(text), int(price)):
        return PRICE_RELIABILITY_MEDIUM
    return PRICE_RELIABILITY_LOW


def _is_headphones(req: Any, text: str) -> bool:
    haystack = _context_text(
        getattr(req, "product_name", ""),
        getattr(req, "product", ""),
        getattr(req, "original_query", ""),
        text,
    ).lower()
    return "наушник" in haystack or "headphone" in haystack or "earbuds" in haystack


def _asks_for_cheapest(req: Any) -> bool:
    return bool(_CHEAP_WORD_RE.search(_context_text(getattr(req, "original_query", ""), getattr(req, "important_criteria", ""))))


def normalize_price_candidate(candidate: Any = None, req: Any = None, **kwargs: Any) -> PriceGuardResult:
    """Нормализует цену и записывает диагностику в candidate, если он передан."""
    title = kwargs.get("title", getattr(candidate, "title", ""))
    snippet = kwargs.get("snippet", getattr(candidate, "snippet", ""))
    description = kwargs.get("description", getattr(candidate, "description", ""))
    text = _context_text(kwargs.get("text", ""), title, snippet, description)
    price = kwargs.get("price", getattr(candidate, "price", None))
    raw_price = kwargs.get("raw_price", None)
    price_source = kwargs.get("price_source", getattr(candidate, "price_source", ""))
    source = kwargs.get("source", getattr(candidate, "source", ""))
    verify_status = kwargs.get("verify_status", getattr(candidate, "verify_status", ""))

    try:
        price_value = int(price) if price is not None else None
    except (TypeError, ValueError):
        price_value = None

    reliability = detect_price_reliability(
        price_value,
        price_source=str(price_source or ""),
        source=str(source or ""),
        verify_status=str(verify_status or ""),
        raw_price=raw_price,
        text=text,
    )
    bad_context = bool(price_value is not None and is_bad_price_context(text, price_value))
    from_budget = is_price_from_query_budget(price_value, req, text=text)
    rejected_reason = ""

    if price_value is not None and reliability != PRICE_RELIABILITY_HIGH:
        if from_budget:
            price_value = None
            reliability = PRICE_RELIABILITY_NONE
            rejected_reason = "price_from_budget"
        elif bad_context:
            price_value = None
            reliability = PRICE_RELIABILITY_NONE
            rejected_reason = "bad_price_context"

    risk_flags: list[str] = []
    low_price_suspect = False
    budget = _budget_value(req)
    if (
        price_value is not None
        and _is_headphones(req, text)
        and budget is not None
        and budget >= 5000
        and price_value < 800
        and not _asks_for_cheapest(req)
    ):
        low_price_suspect = True
        risk_flags.append(TOO_CHEAP_FOR_BUDGET_RISK)

    result = PriceGuardResult(
        price=price_value,
        price_reliability=reliability,
        price_rejected_reason=rejected_reason,
        price_from_budget_suspect=from_budget,
        bad_price_context=bad_context,
        low_price_suspect=low_price_suspect,
        risk_flags=tuple(risk_flags),
    )

    if candidate is not None:
        candidate.price = result.price
        candidate.price_reliability = result.price_reliability
        candidate.price_rejected_reason = result.price_rejected_reason
        candidate.price_from_budget_suspect = result.price_from_budget_suspect
        candidate.bad_price_context = result.bad_price_context
        candidate.low_price_suspect = result.low_price_suspect
        if price_source and not getattr(candidate, "price_source", ""):
            candidate.price_source = str(price_source)
        if result.risk_flags:
            risks = list(getattr(candidate, "risk_flags", []) or [])
            risks.extend(result.risk_flags)
            candidate.risk_flags = list(dict.fromkeys(str(item) for item in risks if str(item).strip()))
    return result


def apply_price_rank_penalties(candidate: Any, score: float, *, verify_status: str = "") -> float:
    """Применяет caps к score и пишет причину в candidate.score_cap_applied."""
    reliability = str(getattr(candidate, "price_reliability", "") or PRICE_RELIABILITY_NONE)
    status = str(verify_status or getattr(candidate, "verify_status", "") or "")
    price = getattr(candidate, "price", None)
    risks = {str(item).strip() for item in (getattr(candidate, "risk_flags", []) or [])}
    cap: float | None = None
    reason = ""

    if price is None or reliability == PRICE_RELIABILITY_NONE or status == "PRICE_MISSING":
        cap, reason = 45.0, "price_missing"
    if status in {"VERIFY_BLOCKED", "NEED_MANUAL_CHECK"}:
        if reliability == PRICE_RELIABILITY_LOW:
            cap, reason = min(cap or 60.0, 60.0), "verify_blocked_low_price"
        elif reliability != PRICE_RELIABILITY_HIGH:
            cap, reason = min(cap or 80.0, 80.0), "manual_unconfirmed_price"
    if getattr(candidate, "low_price_suspect", False) or "подозрительно низкая цена" in risks or TOO_CHEAP_FOR_BUDGET_RISK in risks:
        cap, reason = min(cap or 55.0, 55.0), "low_price_suspect"

    if cap is not None and score > cap:
        candidate.score_cap_applied = f"{reason}:{int(cap)}"
        return cap
    candidate.score_cap_applied = ""
    return score
