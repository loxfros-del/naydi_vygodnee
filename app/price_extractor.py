"""Evidence-aware извлечение цены без принятия характеристик за деньги."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Optional


PRICE_CONFIDENCE_HIGH = "high"
PRICE_CONFIDENCE_MEDIUM = "medium"
PRICE_CONFIDENCE_LOW = "low"
PRICE_CONFIDENCE_NONE = "none"

PRICE_EVIDENCE_DIRECT = "direct_store"
PRICE_EVIDENCE_STRUCTURED = "structured_page"
PRICE_EVIDENCE_CURRENCY = "snippet_currency"
PRICE_EVIDENCE_LABELED = "labeled_text"
PRICE_EVIDENCE_INFERRED = "inferred"
PRICE_EVIDENCE_REJECTED_SPEC = "rejected_spec_context"


@dataclass(frozen=True)
class PriceExtractionResult:
    price: int | None
    confidence: str
    evidence: str
    raw_value: str = ""
    rejected_reason: str = ""


_NUMBER_RE = re.compile(
    r"(?<![\w])(?P<number>\d{1,3}(?:[ \u00a0.]\d{3})+|\d{4,7}|\d{1,3}\s*[кКkK])(?![\w])"
)
_CURRENCY_AFTER_RE = re.compile(r"^\s*(?:₽|руб(?:\.|лей|ля|ль)?|р\.)", re.IGNORECASE)
_CURRENCY_BEFORE_RE = re.compile(r"(?:₽|руб(?:\.|лей|ля|ль)?|р\.)\s*$", re.IGNORECASE)
_PRICE_LABEL_BEFORE_RE = re.compile(
    r"(?:цена|стоимость|стоит|за|от|sale\s*price|current\s*price)\s*[:—–-]?\s*$",
    re.IGNORECASE,
)
_PRICE_LABEL_AFTER_RE = re.compile(r"^\s*(?:цена|стоимость)\b", re.IGNORECASE)
_COMMERCE_CONTEXT_RE = re.compile(
    r"(?:купить|заказать|скидка|акция|цена|стоимость|₽|руб|price|в\s+корзину)",
    re.IGNORECASE,
)
_SPEC_PREFIX_RE = re.compile(
    r"(?:ram|озу|оператив(?:ная)?\s*память|память|ssd|hdd|emmc|nvme|"
    r"накопитель|разрешение|частота|диагональ|нагрузка|мощность|аккумулятор|"
    r"емкость|ёмкость|артикул|модель|код|отзывов?|оценок?|год|до|бюджет|"
    r"максимум|не\s+дороже)\s*[:/-]?\s*$",
    re.IGNORECASE,
)
_SPEC_SUFFIX_RE = re.compile(
    r"^\s*(?:gb|гб|tb|тб|mah|мач|мaч|hz|гц|kg|кг|вт|w|дюйм(?:а|ов)?|"
    r"inch|литр(?:а|ов)?|л\b|отзыв(?:а|ов)?|оценок?|шт\.?|пиксел|"
    r"артикул|модель|код|(?:встроенн\w*\s+)?игр\w*)",
    re.IGNORECASE,
)


def _number(raw: str) -> int:
    compact = raw.replace("\u00a0", " ").replace(" ", "").replace(".", "")
    return int(compact[:-1]) * 1000 if compact.lower().endswith(("к", "k")) else int(compact)


def _valid(value: int, min_price: int, max_price: int) -> bool:
    return min_price <= value <= max_price


def _looks_like_spec_number(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 36):start]
    after = text[end:end + 36]
    near = text[max(0, start - 18):min(len(text), end + 18)]
    if _SPEC_PREFIX_RE.search(before) or _SPEC_SUFFIX_RE.search(after):
        return True
    if re.search(r"\d\s*[xх×]\s*$", before) or re.match(r"\s*[xх×]\s*\d", after):
        return True
    if re.search(r"(?:1920|2560|3840)\s*[xх×]\s*(?:1080|1440|2160)", near):
        return True
    if re.search(r"\b(?:19|20)\d{2}\s*(?:г\.?|год(?:а|у)?)\b", near, re.IGNORECASE):
        return True
    if re.search(r"(?:артикул|sku|model|модель|код)\D{0,8}\d", near, re.IGNORECASE):
        return True
    return False


def _structured_result(
    structured_price: Any,
    *,
    price_source: str,
    min_price: int,
    max_price: int,
) -> PriceExtractionResult | None:
    if structured_price in (None, ""):
        return None
    try:
        value = int(structured_price)
    except (TypeError, ValueError):
        digits = re.sub(r"\D", "", str(structured_price))
        value = int(digits) if digits else 0
    if not _valid(value, min_price, max_price):
        return None
    source = (price_source or "").lower()
    evidence = PRICE_EVIDENCE_DIRECT if any(marker in source for marker in ("direct", "api", "store")) else PRICE_EVIDENCE_STRUCTURED
    return PriceExtractionResult(value, PRICE_CONFIDENCE_HIGH, evidence, str(structured_price))


def extract_price_evidence(
    text: str,
    min_price: int = 1_000,
    max_price: int = 10_000_000,
    *,
    structured_price: Any = None,
    price_source: str = "",
    allow_safe_bare: bool = True,
) -> PriceExtractionResult:
    """Извлекает лучшую цену и возвращает confidence/evidence."""
    structured = _structured_result(
        structured_price,
        price_source=price_source,
        min_price=min_price,
        max_price=max_price,
    )
    if structured is not None:
        return structured
    if not text:
        return PriceExtractionResult(None, PRICE_CONFIDENCE_NONE, "")

    normalized = text.replace("\u00a0", " ")
    accepted: list[tuple[int, int, PriceExtractionResult]] = []
    rejected_spec = False
    for match in _NUMBER_RE.finditer(normalized):
        raw = match.group("number")
        value = _number(raw)
        if not _valid(value, min_price, max_price):
            continue
        before = normalized[max(0, match.start() - 48):match.start()]
        after = normalized[match.end():match.end() + 48]
        has_currency = bool(_CURRENCY_AFTER_RE.match(after) or _CURRENCY_BEFORE_RE.search(before))
        has_label = bool(_PRICE_LABEL_BEFORE_RE.search(before) or _PRICE_LABEL_AFTER_RE.match(after))
        if _looks_like_spec_number(normalized, match.start(), match.end()) and not has_currency:
            rejected_spec = True
            continue
        if 2000 <= value <= 2039 and not (has_currency or has_label):
            rejected_spec = True
            continue
        if has_currency:
            accepted.append((0, match.start(), PriceExtractionResult(value, PRICE_CONFIDENCE_HIGH, PRICE_EVIDENCE_CURRENCY, raw)))
            continue
        if has_label:
            accepted.append((1, match.start(), PriceExtractionResult(value, PRICE_CONFIDENCE_MEDIUM, PRICE_EVIDENCE_LABELED, raw)))
            continue

        formatted_thousands = bool(re.search(r"[ \u00a0.]", raw))
        short_thousands = raw.lower().strip().endswith(("к", "k"))
        safe_context = bool(_COMMERCE_CONTEXT_RE.search(normalized))
        if allow_safe_bare and (formatted_thousands or short_thousands or safe_context):
            accepted.append((2, match.start(), PriceExtractionResult(value, PRICE_CONFIDENCE_LOW, PRICE_EVIDENCE_INFERRED, raw)))

    if accepted:
        accepted.sort(key=lambda item: (item[0], item[1]))
        return accepted[0][2]
    if rejected_spec:
        return PriceExtractionResult(
            None,
            PRICE_CONFIDENCE_NONE,
            PRICE_EVIDENCE_REJECTED_SPEC,
            rejected_reason=PRICE_EVIDENCE_REJECTED_SPEC,
        )
    return PriceExtractionResult(None, PRICE_CONFIDENCE_NONE, "")


def extract_price(text: str, min_price: int = 1_000, max_price: int = 10_000_000) -> Optional[int]:
    """Backward-compatible wrapper, возвращающий только int или None."""
    return extract_price_evidence(text, min_price=min_price, max_price=max_price).price


def format_price(price: Optional[int]) -> str:
    if not price or price <= 0:
        return ""
    return f"{price:,}".replace(",", " ") + " ₽"
