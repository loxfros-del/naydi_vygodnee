"""Чистый парсер текстовой заявки без зависимости от Telegram/aiogram."""
import re

from app.search_links import build_search_query


def parse_budget(text: str) -> str:
    t = text.lower()
    match = re.search(r"\b(\d{1,3})\s*[кКkK]\b", t)
    if match:
        return str(int(match.group(1)) * 1000)
    match = re.search(r"\b(\d{1,3})\s*тыс(?:яч|\.|и)?\.?\b", t)
    if match:
        return str(int(match.group(1)) * 1000)
    match = re.search(r"\b(\d{1,3})\s+(\d{3})\b", t)
    if match:
        value = int(match.group(1) + match.group(2))
        if 1000 <= value <= 999999:
            return str(value)
    match = re.search(r"\b(\d{4,6})\b", t)
    if match and 1000 <= int(match.group(1)) <= 999999:
        return match.group(1)
    return ""


def parse_city(text: str) -> str:
    city_forms = {
        "ярославле": "Ярославль", "москве": "Москва", "санкт-петербурге": "Санкт-Петербург",
        "петербурге": "Санкт-Петербург", "казани": "Казань", "самаре": "Самара",
        "омске": "Омск", "перми": "Пермь", "тюмени": "Тюмень", "туле": "Тула",
        "воронеже": "Воронеж", "екатеринбурге": "Екатеринбург", "новосибирске": "Новосибирск",
        "нижнем новгороде": "Нижний Новгород", "ростове-на-дону": "Ростов-на-Дону",
    }
    match = re.search(r"в\s+([А-Яа-яЁё]+(?:[-\s][А-Яа-яЁё]+)*)\b", text)
    if not match:
        return ""
    city = match.group(1).strip()
    stop_words = {"видео", "случае", "итоге", "результате", "плане", "магазине", "интернете", "городе", "районе", "центре", "смысле", "отличие", "моменте", "процессе", "конце", "начале", "примере"}
    if city.lower() in stop_words or len(city) < 3:
        return ""
    return city_forms.get(city.lower(), city.title())


def parse_use_case(text: str) -> str:
    lowered = text.lower()
    match = re.search(r"для\s+([А-Яа-яЁёA-Za-z0-9\s]+?)(?:\s+до\s|\s+в\s|\s+купить|$)", text)
    if match:
        use_case = match.group(1).strip()
        if 2 <= len(use_case) <= 50:
            return use_case
    if any(word in lowered for word in ("ps5", "плейстейшен", "playstation", "приставк")):
        return "PS5"
    for phrase in ("для игр", "для работы", "для кухни", "для дома", "для офиса"):
        if phrase in lowered:
            return phrase.removeprefix("для ")
    return ""


def parse_product_name(text: str) -> str:
    result = text.lower().strip()
    for pattern in (
        r"^нуж(?:ен|на|но|ны)\s+",
        r"^хочу\s+", r"^ищу\s+", r"^посоветуйте\s+", r"^подскажите\s+",
        r"^порекомендуйте\s+", r"^мне\s+нуж(?:ен|на|но|ны)\s+",
        r"^мне\s+хочется\s+", r"^дайте\s+", r"^найдите\s+",
        r"^какой\s+", r"^какую\s+", r"^какие\s+",
    ):
        result = re.sub(pattern, "", result)
    result = re.sub(r"\s*до\s+\d{1,3}\s+\d{3}\s*", " ", result)
    result = re.sub(r"\s*до\s+\d+\s*[кК]?\s*", " ", result)
    result = re.sub(r"\s*до\s+\d+\s*тыс.*?\s*", " ", result)
    result = re.sub(r"\s+в\s+[А-Яа-яЁё]+\s*$", " ", result)
    result = re.sub(r"\s+для\s+.+?(?=\s+до\s|\s+в\s|$)", " ", result)
    result = re.sub(r"\s*купить\s*", " ", result)
    result = re.sub(r"(?<![а-яёa-z0-9])б/?у(?![а-яёa-z0-9])", " ", result)
    result = re.sub(r"(?<![а-яёa-z0-9])бу(?![а-яёa-z0-9])", " ", result)
    result = re.sub(r"(?<![а-яёa-z0-9])новый?(?![а-яёa-z0-9])", " ", result)
    result = re.sub(r"\s+", " ", result).strip()
    words = result.split()
    return " ".join(words[:4])[:40] if words else text[:40]


def parse_is_used_allowed(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ("б/у", "бу ", "б/у ", "второй рук", "с рук", "подержан"))


def parse_important_criteria(text: str) -> str:
    lowered = text.lower()
    criteria: list[str] = []
    match = re.search(r"(\d{2})\s*(?:дюйм|\")", lowered)
    if match:
        criteria.append(f"диагональ {match.group(1)}\"")
    if any(word in lowered for word in ("4k", "ultra hd", "ультра hd")):
        criteria.append("4K")
    if any(word in lowered for word in ("full hd", "фулл hd")):
        criteria.append("Full HD")
    if "120 гц" in lowered or "120hz" in lowered:
        criteria.append("120 Гц")
    if "hdmi" in lowered:
        criteria.append("HDMI")
    for brand in ("samsung", "lg", "sony", "xiaomi", "tcl", "hisense", "haier"):
        if brand in lowered:
            criteria.append(f"бренд {brand.title()}")
    return ", ".join(criteria)


def is_ps5_tv(product_name: str, use_case: str, original_query: str) -> bool:
    return "телевизор" in product_name.lower() and any(
        word in f"{use_case} {original_query}".lower()
        for word in ("ps5", "ps 5", "playstation", "плейстейшен", "приставк", "игр")
    )


def full_parse(text: str) -> dict:
    product_name = parse_product_name(text)
    use_case = parse_use_case(text)
    budget = parse_budget(text)
    city = parse_city(text)
    criteria = parse_important_criteria(text)
    if is_ps5_tv(product_name, use_case, text):
        required = ["4K", "HDMI", "43–55 дюймов", "игровой режим/низкая задержка", "120 Гц желательно"]
        existing = {item.strip().lower() for item in criteria.split(",") if item.strip()}
        criteria = ", ".join([item for item in required if item.lower() not in existing] + ([criteria] if criteria else []))
    return {
        "original_query": text.strip(),
        "product_name": product_name,
        "use_case": use_case,
        "budget": budget,
        "city": city,
        "important_criteria": criteria,
        "clean_search_query": build_search_query(product_name, use_case, budget, city, criteria),
        "is_used_allowed": parse_is_used_allowed(text),
    }
