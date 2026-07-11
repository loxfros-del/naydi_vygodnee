"""Строгое извлечение цены из поискового заголовка и сниппета."""
import re
from typing import Optional


_NUMBER_RE = re.compile(
    r"(?<![\w])(?P<number>\d{1,3}(?:[ \u00a0]\d{3})+|\d{4,6}|\d{2,3}\s*[кКkK])(?![\w])"
)
_CURRENCY_RE = re.compile(r"^(?:\s*(?:₽|руб(?:\.|лей)?|р\.))", re.IGNORECASE)
_PRICE_MARKER_RE = re.compile(r"(?:цена|стоимость|стоит)\s*[:—–-]?\s*$", re.IGNORECASE)

_SPEC_PREFIX_RE = re.compile(
    r"(?:ram|озу|оператив(?:ная)?|память|ssd|hdd|emmc|nvme|накопитель)\s*$",
    re.IGNORECASE,
)
_SPEC_SUFFIX_RE = re.compile(
    r"^\s*(?:gb|гб|tb|тб|mah|мач|hz|гц|kg|кг|дюйм(?:ов)?|inch)\b",
    re.IGNORECASE,
)


def _looks_like_spec_number(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 24):start]
    after = text[end:end + 24]
    if _SPEC_PREFIX_RE.search(before) or _SPEC_SUFFIX_RE.search(after):
        return True

    if re.search(r"[xх]\s*$", before, re.IGNORECASE) or re.match(r"\s*[xх]", after, re.IGNORECASE):
        return True

    if re.search(r"-\s*$", before) and re.match(r"\s*(?:гц|hz)\b", after, re.IGNORECASE):
        return True

    return False

def _number(raw: str) -> int:
    compact = raw.replace("\u00a0", " ").replace(" ", "")
    return int(compact[:-1]) * 1000 if compact.lower().endswith(("к", "k")) else int(compact)


def _is_valid_price(value: int, min_price: int, max_price: int) -> bool:
    return (
        min_price <= value <= max_price
        and not 2000 <= value <= 2039
    )


def extract_price(text: str, min_price: int = 1_000, max_price: int = 10_000_000) -> Optional[int]:
    """Извлекает цену только при явном денежном контексте.

    Подходят ``44 999 ₽``, ``44999 руб.``, ``45 000 рублей``, ``45к`` и
    ``45 k``. Голые числа из карточки (например, ``89999``) допустимы только
    после отсечения моделей, характеристик, отзывов и количества игр.
    """
    if not text:
        return None
    normalized = text.replace("\u00a0", " ")

    for match in _NUMBER_RE.finditer(normalized):
        raw_number = match.group("number")
        value = _number(raw_number)
        if _looks_like_spec_number(normalized, match.start(), match.end()):
            continue

        if not _is_valid_price(value, min_price, max_price):
            continue

        before = normalized[max(0, match.start() - 32):match.start()]
        after = normalized[match.end():match.end() + 32]
        has_currency = bool(_CURRENCY_RE.match(after))
        has_price_marker = bool(_PRICE_MARKER_RE.search(before))
        # Не допускаем «цена 45000 встроенных игр»: даже после маркера это
        # описание комплектации, а не денежное значение.
        is_games_count = bool(re.search(r"(?:встроенн\w*\s+)?игр\w*", after, re.IGNORECASE))
        is_review_count = bool(re.search(r"(?:отзыв\w*|оцен\w*|rating)", after, re.IGNORECASE))
        is_bare_product_price = bool(re.fullmatch(r"\d{4,6}|\d{2,3}\s*[кКkK]", raw_number))
        # В product_search сюда передаётся только title + snippet, не текст
        # заявки, поэтому бюджет пользователя не может стать ценой товара.
        if (has_currency or has_price_marker or is_bare_product_price) and not (is_games_count or is_review_count):
            return value
    return None


def format_price(price: Optional[int]) -> str:
    """Форматирует цену в читаемый вид: ``45 000 ₽``."""
    if not price or price <= 0:
        return ""
    return f"{price:,}".replace(",", " ") + " ₽"
