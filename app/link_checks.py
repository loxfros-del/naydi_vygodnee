"""Локальные проверки соответствия ссылки, магазина и рисков Авито.

Модуль не открывает страницы магазинов и не пытается скрейпить площадки:
он только даёт админу понятные предупреждения для ручной проверки.
"""
import re
from statistics import median
from urllib.parse import urlparse


_STORE_DOMAINS = {
    "Авито": ("avito.ru",),
    "Ozon": ("ozon.ru",),
    "Яндекс Маркет": ("market.yandex.ru",),
    "М.Видео": ("mvideo.ru",),
    "DNS": ("dns-shop.ru",),
    "Wildberries": ("wildberries.ru",),
    "Мегамаркет": ("megamarket.ru",),
}


def store_from_url(url: str) -> str:
    """Возвращает известную площадку по домену или пустую строку."""
    try:
        host = urlparse((url or "").strip()).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""
    for store, domains in _STORE_DOMAINS.items():
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return store
    return ""


def normalized_store(store: str) -> str:
    """Нормализует частые написания названий площадок."""
    value = (store or "").lower().replace(".", "").replace(" ", "")
    aliases = {
        "авито": "Авито", "ozon": "Ozon", "яндексмаркет": "Яндекс Маркет",
        "market": "Яндекс Маркет", "мвидео": "М.Видео", "mvideo": "М.Видео",
        "dns": "DNS", "dns-shop": "DNS", "wildberries": "Wildberries", "wb": "Wildberries",
    }
    for alias, canonical in aliases.items():
        if alias in value:
            return canonical
    return ""


def store_url_matches(store: str, url: str) -> bool | None:
    """True — совпадают, False — известные площадки различаются, None — неясно."""
    expected = normalized_store(store)
    actual = store_from_url(url)
    if not expected or not actual:
        return None
    return expected == actual


def store_url_warning(store: str, url: str) -> str:
    if store_url_matches(store, url) is False:
        return "⚠️ магазин и ссылка не совпадают"
    return ""


def _city_slug(city: str) -> str:
    table = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
        "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "-": "_", " ": "_",
    })
    return (city or "").lower().translate(table)


def avito_warnings(
    *,
    city: str,
    is_used_allowed: bool,
    title: str,
    description: str,
    price: int | None,
    comparison_prices: list[int],
    url: str,
) -> list[str]:
    """Возвращает чек-лист ручной проверки объявления Авито."""
    if store_from_url(url) != "Авито":
        return []

    warnings: list[str] = []
    expected_city = _city_slug(city)
    path = urlparse(url).path.lower()
    if expected_city and expected_city not in path:
        warnings.append("⚠️ Авито: проверь, совпадает ли город с заявкой")

    text = f"{title} {description}".lower()
    if not is_used_allowed and re.search(r"\bб[\s/]?у\b|подержан|с рук|used", text):
        warnings.append("⚠️ Авито: товар может быть б/у, а в заявке нужен новый")

    warnings.append("⚠️ Авито: проверь рейтинг и отзывы продавца")
    valid_prices = [value for value in comparison_prices if value and value > 0]
    if price and len(valid_prices) >= 2 and price < median(valid_prices) * 0.6:
        warnings.append("⚠️ Авито: цена заметно ниже других вариантов — проверь объявление")
    return warnings
