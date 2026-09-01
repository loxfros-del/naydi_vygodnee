"""Детерминированный parser пользовательского товарного запроса."""
from __future__ import annotations

import json
import re
from typing import Any

from app.category_registry import contains_marker, detect_category, get_category_spec, known_brand, normalize_text
from app.search_links import build_search_query


_SPACE_RE = re.compile(r"\s+")
_BUDGET_AMOUNT = (
    r"(?:\d{1,3}(?:[ \u00a0.]\d{3})+|\d{4,6}|"
    r"\d{1,3}\s*(?:[кk]|тыс(?:яч(?:а|и)?|\.)?))"
)
_BUDGET_CONTEXT_RE = re.compile(
    rf"(?:\bдо\b|\bне\s+дороже\b|\bмаксимум\b|\bбюджет(?:ом)?\b|"
    rf"\bв\s+пределах\b|\bза\b)\s*(?P<amount>{_BUDGET_AMOUNT})",
    re.IGNORECASE,
)
_CURRENCY_AMOUNT_RE = re.compile(
    rf"(?P<amount>{_BUDGET_AMOUNT})\s*(?:₽|руб(?:\.|лей|ля|ль)?)(?![a-zа-я])",
    re.IGNORECASE,
)
_SHORT_BUDGET_RE = re.compile(
    r"(?<![\w])(?P<amount>\d{1,3}\s*(?:[кk]|тыс(?:яч(?:а|и)?|\.)?))(?![\w])",
    re.IGNORECASE,
)
_PLAIN_BUDGET_RE = re.compile(
    r"(?<![\w])(?P<amount>\d{1,3}(?:[ \u00a0]\d{3})+|\d{4,6})(?![\w])",
    re.IGNORECASE,
)

_CITY_FORMS = {
    "москве": "Москва", "москва": "Москва",
    "санкт-петербурге": "Санкт-Петербург", "санкт-петербург": "Санкт-Петербург",
    "петербурге": "Санкт-Петербург", "питере": "Санкт-Петербург",
    "ярославле": "Ярославль", "ярославль": "Ярославль",
    "казани": "Казань", "казань": "Казань",
    "самаре": "Самара", "самара": "Самара",
    "омске": "Омск", "омск": "Омск",
    "перми": "Пермь", "пермь": "Пермь",
    "тюмени": "Тюмень", "тюмень": "Тюмень",
    "туле": "Тула", "тула": "Тула",
    "воронеже": "Воронеж", "воронеж": "Воронеж",
    "екатеринбурге": "Екатеринбург", "екатеринбург": "Екатеринбург",
    "новосибирске": "Новосибирск", "новосибирск": "Новосибирск",
    "нижнем новгороде": "Нижний Новгород", "нижний новгород": "Нижний Новгород",
    "ростове-на-дону": "Ростов-на-Дону", "ростов-на-дону": "Ростов-на-Дону",
    "краснодаре": "Краснодар", "краснодар": "Краснодар",
    "красноярске": "Красноярск", "красноярск": "Красноярск",
    "уфе": "Уфа", "уфа": "Уфа",
    "челябинске": "Челябинск", "челябинск": "Челябинск",
    "сочи": "Сочи",
}

_CATEGORY_DISPLAY = {
    "phone": "смартфон",
    "laptop": "ноутбук",
    "tv": "телевизор",
    "headphones": "наушники",
    "chair": "офисное кресло",
    "monitor": "монитор",
    "robot_vacuum": "робот-пылесос",
    "vacuum": "пылесос",
    "microwave": "микроволновка",
    "coffee_machine": "кофемашина",
    "mattress": "матрас",
    "bed": "кровать",
    "unknown": "товар",
}

_MODEL_VARIANT_DISPLAY = {
    "pro max": "Pro Max", "pro": "Pro", "max": "Max", "plus": "Plus",
    "mini": "Mini", "se": "SE", "fe": "FE", "ultra": "Ultra",
    "lite": "Lite", "air": "Air", "e": "e",
}

_WIZARD_CATEGORY_TO_SEARCH_CATEGORY = {
    "smartphones": "phone",
    "laptops": "laptop",
    "televisions": "tv",
    "headphones": "headphones",
    "monitors": "monitor",
    "office_chairs": "chair",
}

_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:какой|какая|какие|сколько|нужно|нужен|нужна|нужны|важен|важна|"
    r"есть\s+требования|будете|для\s+каких|монитор\s+нужен|укажите)\b[^:]{0,140}:\s*",
    re.IGNORECASE,
)


def _amount_to_int(raw: str) -> int | None:
    text = normalize_text(raw).replace("\u00a0", " ").strip(" .")
    multiplier = 1
    if re.search(r"(?:[кk]|тыс)", text, re.IGNORECASE):
        multiplier = 1_000
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    value = int(digits) * multiplier
    return value if 1_000 <= value <= 10_000_000 and not 2000 <= value <= 2039 else None


def parse_budget(text: str) -> str:
    """Возвращает бюджет, не принимая за него размеры и характеристики."""
    for pattern in (_BUDGET_CONTEXT_RE, _CURRENCY_AMOUNT_RE, _SHORT_BUDGET_RE, _PLAIN_BUDGET_RE):
        for match in pattern.finditer(text or ""):
            before = (text or "")[max(0, match.start() - 24):match.start()]
            tail = (text or "")[match.end():match.end() + 16]
            if pattern is _SHORT_BUDGET_RE and re.fullmatch(r"4\s*[кk]", match.group("amount"), re.IGNORECASE):
                continue
            if pattern is _PLAIN_BUDGET_RE and re.search(r"(?:модель|артикул|код|год)\s*$", before, re.IGNORECASE):
                continue
            if re.match(r"\s*(?:mah|мач|гц|hz|гб|gb|кг|kg|вт|w|л(?:итр)?)\b", tail, re.IGNORECASE):
                continue
            value = _amount_to_int(match.group("amount"))
            if value is not None:
                return str(value)
    return ""


def parse_city(text: str) -> str:
    normalized = normalize_text(text)
    for form in sorted(_CITY_FORMS, key=len, reverse=True):
        pattern = re.escape(form).replace(r"\ ", r"\s+")
        if re.search(rf"(?:\bв\s+|\bгород(?:е)?\s+){pattern}\b", normalized):
            return _CITY_FORMS[form]
    match = re.search(
        r"(?:\bв\s+|\bгород(?:е)?\s+)([А-ЯЁ][а-яё-]{2,}(?:\s+[А-ЯЁ][а-яё-]{2,}){0,2})"
        r"(?=\s+(?:до|за|бюджет)|[,.!?]|$)",
        text or "",
    )
    return match.group(1).strip() if match else ""


def parse_use_case(text: str) -> str:
    lowered = normalize_text(text)
    match = re.search(
        r"\bдля\s+(.+?)(?=\s+(?:до|за|бюджет(?:ом)?|в\s+[А-ЯЁ])\b|[,.;]|$)",
        text or "",
        re.IGNORECASE,
    )
    if match:
        value = _SPACE_RE.sub(" ", match.group(1)).strip()
        if 2 <= len(value) <= 80:
            return value
    if any(word in lowered for word in ("ps5", "ps 5", "playstation", "плейстейшен")):
        return "PS5"
    for marker, value in (
        ("для игр", "игр"), ("для работы", "работы"), ("для учебы", "учёбы"),
        ("для учебы", "учёбы"), ("для офиса", "офиса"), ("для дома", "дома"),
    ):
        if marker in lowered:
            return value
    return ""


def parse_condition(text: str) -> str:
    lowered = normalize_text(text)
    if re.search(r"(?<![a-zа-я0-9])(?:б\s*/?\s*у|бу|used|подержан\w*|с\s+рук)(?![a-zа-я0-9])", lowered):
        return "used"
    if re.search(r"(?<![a-zа-я0-9])(?:нов(?:ый|ая|ое|ые)|new)(?![a-zа-я0-9])", lowered):
        return "new"
    return "any"


def _extract_model(text: str, category: str, brand: str) -> str:
    normalized = normalize_text(text)
    iphone = re.search(
        r"\b(?:apple\s+)?(?:iphone|айфон)\s*(\d{1,2})\s*"
        r"(pro\s+max|pro|max|plus|mini|se|ultra|lite|air|fe|e)?\b",
        normalized,
    )
    if iphone:
        variant = _MODEL_VARIANT_DISPLAY.get(_SPACE_RE.sub(" ", iphone.group(2) or "").strip(), "")
        suffix = variant if variant == "e" else f" {variant}" if variant else ""
        return f"iPhone {iphone.group(1)}{suffix}"

    galaxy = re.search(
        r"\b(?:samsung\s+)?galaxy\s+([asz]\s*\d{2,3})\s*(ultra|fe|plus|\+|lite)?\b",
        normalized,
    )
    if galaxy:
        code = galaxy.group(1).replace(" ", "").upper()
        variant_key = "plus" if galaxy.group(2) == "+" else (galaxy.group(2) or "")
        variant = _MODEL_VARIANT_DISPLAY.get(variant_key, variant_key.upper())
        prefix = "Samsung " if brand.lower() == "samsung" or "samsung" in normalized else ""
        return f"{prefix}Galaxy {code}{f' {variant}' if variant else ''}"

    sony = re.search(r"\b(?:sony\s+)?(wh\s*[- ]?\s*1000\s*xm\s*\d)\b", normalized)
    if sony:
        code = re.sub(r"\s+", "", sony.group(1)).upper().replace("WH-", "WH-")
        if not code.startswith("WH-"):
            code = "WH-" + code.removeprefix("WH")
        return f"Sony {code}"

    if category in {"phone", "laptop", "tv", "monitor", "headphones"} and brand:
        escaped = re.escape(brand.lower())
        match = re.search(
            rf"\b{escaped}\s+([a-z0-9][a-z0-9-]*(?:\s+[a-z0-9][a-z0-9-]*){{0,2}})",
            normalized,
        )
        if match and re.search(r"\d", match.group(1)):
            tokens = [token for token in match.group(1).split() if token not in {"гб", "gb", "дюймов", "дюйма"}]
            return f"{brand} {' '.join(tokens[:3]).upper()}".strip()
    return ""


def _extract_model_modifiers(text: str, model: str) -> list[str]:
    normalized = normalize_text(f"{model} {text}")
    result: list[str] = []
    for modifier in get_category_spec("phone").model_modifiers:
        if modifier == "e":
            if re.search(r"\b(?:iphone\s*\d{1,2}|galaxy\s*[asz]\d{2,3})e\b", normalized):
                result.append("e")
            continue
        pattern = re.escape(modifier).replace(r"\ ", r"\s+")
        if re.search(rf"\b{pattern}\b", normalized):
            result.append(modifier)
    generation = re.search(r"\b(\d{1,2})(?:-?е|го)?\s+поколени", normalized)
    if generation:
        result.append(f"{generation.group(1)} поколение")
    return list(dict.fromkeys(result))


def _storage_gb(text: str) -> int | None:
    normalized = normalize_text(text)
    matches: list[int] = []
    for match in re.finditer(r"(?<!\d)(1|2|32|64|128|256|512|1024|2048)\s*(тб|tb|гб|gb)\b", normalized):
        value = int(match.group(1)) * (1024 if match.group(2) in {"тб", "tb"} else 1)
        before = normalized[max(0, match.start() - 18):match.start()]
        if re.search(r"(?:озу|ram|оператив)\s*$", before):
            continue
        matches.append(value)
    return max(matches) if matches else None


def _labeled_capacity(text: str, labels: str) -> int | None:
    normalized = normalize_text(text)
    patterns = (
        rf"(?:{labels})\s*[:/-]?\s*(\d{{1,4}})\s*(?:гб|gb)?\b",
        rf"(\d{{1,4}})\s*(?:гб|gb)\s*(?:{labels})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    return None


def _extract_major_criteria(text: str, category: str) -> dict[str, Any]:
    normalized = normalize_text(text)
    criteria: dict[str, Any] = {}

    size_match = re.search(r"(?<!\d)(\d{2,3})\s*[xх×]\s*(\d{2,3})(?!\d)", normalized)
    if size_match:
        criteria["size"] = f"{size_match.group(1)}x{size_match.group(2)}"

    diagonal_match = re.search(
        r"(?<!\d)(\d{2,3}(?:[.,]\d)?)\s*(?:\"|”|″|дюйм(?:а|ов)?)(?![a-zа-я])",
        normalized,
    )
    if diagonal_match:
        criteria["diagonal"] = diagonal_match.group(1).replace(",", ".")

    refresh_match = re.search(r"(?<!\d)(50|60|75|90|100|120|144|165|180|240|360)\s*(?:гц|hz)\b", normalized)
    if refresh_match:
        criteria["refresh_rate"] = int(refresh_match.group(1))

    storage = _storage_gb(normalized)
    if storage is not None:
        criteria["storage_gb"] = storage
    ram = _labeled_capacity(normalized, r"ram|озу|оператив(?:ная)?\s+память")
    if ram is not None:
        criteria["ram_gb"] = ram
    ssd = _labeled_capacity(normalized, r"ssd|накопитель")
    if ssd is not None:
        criteria["ssd_gb"] = ssd

    if re.search(r"\b(?:4k|4к|uhd|ultra\s*hd)\b", normalized):
        criteria["resolution"] = "4K"
    elif re.search(r"\b(?:qhd|wqhd|2k|2560\s*[xх×]\s*1440)\b", normalized):
        criteria["resolution"] = "QHD"
    elif re.search(r"\b(?:full\s*hd|fhd|1080p|1920\s*[xх×]\s*1080)\b", normalized):
        criteria["resolution"] = "Full HD"

    cpu = re.search(
        r"\b((?:amd\s+)?ryzen\s*[3579](?:\s+\d{3,5}[a-z]*)?|"
        r"(?:intel\s+)?(?:core\s+)?i[3579](?:[- ]?\d{3,5}[a-z]*)?|"
        r"n95|n100|n150|n5095|celeron|pentium\s+silver)\b",
        normalized,
    )
    if cpu:
        criteria["cpu"] = _SPACE_RE.sub(" ", cpu.group(1)).upper()

    volume = re.search(r"(?<!\d)(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\s*(?:л|литр(?:а|ов)?)\b", normalized)
    if volume:
        criteria["volume_l"] = (
            [int(volume.group(1)), int(volume.group(2))]
            if volume.group(2) else int(volume.group(1))
        )
    load = re.search(r"(?<!\d)(\d{2,3})\s*(?:кг|kg)\b", normalized)
    if load:
        criteria["load_kg"] = int(load.group(1))
    battery = re.search(r"(?<!\d)(\d{4,6})\s*(?:mah|мач|мaч)\b", normalized)
    if battery:
        criteria["battery_mah"] = int(battery.group(1))

    boolean_markers = (
        ("wet_cleaning", ("влажная уборка", "влажной уборкой", "моющий")),
        ("lidar", ("лидар", "lidar")),
        ("cappuccinator", ("капучинатор", "milk system", "молочная система")),
        ("lift_mechanism", ("подъемн", "подъёмн")),
        ("anc", ("anc", "шумоподавление", "шумоподавлением", "noise cancelling")),
        ("wireless", ("беспровод", "wireless", "bluetooth")),
        ("ips", ("ips",)),
        ("adaptive_sync", ("adaptive sync", "freesync", "g-sync", "gsync")),
        ("grill", ("грил", "grill")),
        ("inverter", ("инвертор", "inverter")),
        ("medium_firmness", ("средней жесткости", "средней жёсткости")),
        ("automatic", ("автоматическая", "автоматический")),
        ("pet_hair", ("шерсть животных", "шерсти животных")),
    )
    for key, markers in boolean_markers:
        if any(marker in normalized for marker in markers):
            criteria[key] = True

    if category == "tv" and "ps5" in normalized:
        criteria.setdefault("resolution", "4K")
    return criteria


def _human_criteria(criteria: dict[str, Any], brand: str = "") -> list[str]:
    result: list[str] = []
    if brand:
        result.append(f"бренд {brand}")
    labels = {
        "size": lambda value: str(value),
        "diagonal": lambda value: f"диагональ {value}\"",
        "refresh_rate": lambda value: f"{value} Гц",
        "storage_gb": lambda value: f"{value} ГБ",
        "ram_gb": lambda value: f"RAM {value} ГБ",
        "ssd_gb": lambda value: f"SSD {value} ГБ",
        "resolution": str,
        "cpu": str,
        "volume_l": lambda value: f"{value[0]}–{value[1]} л" if isinstance(value, list) else f"{value} л",
        "load_kg": lambda value: f"нагрузка {value} кг",
        "battery_mah": lambda value: f"{value} mAh",
        "wet_cleaning": lambda _: "влажная уборка",
        "lidar": lambda _: "лидар",
        "cappuccinator": lambda _: "капучинатор",
        "lift_mechanism": lambda _: "подъёмный механизм",
        "anc": lambda _: "ANC",
        "wireless": lambda _: "беспроводные",
        "ips": lambda _: "IPS",
        "adaptive_sync": lambda _: "Adaptive Sync",
        "grill": lambda _: "гриль",
        "inverter": lambda _: "инвертор",
        "medium_firmness": lambda _: "средняя жёсткость",
        "automatic": lambda _: "автоматическая",
        "pet_hair": lambda _: "для шерсти животных",
    }
    for key, value in criteria.items():
        if value in (None, "", False) or key not in labels:
            continue
        result.append(labels[key](value))
    return list(dict.fromkeys(result))


def parse_request_details(text: str) -> dict[str, Any]:
    category = detect_category(text)
    brand = known_brand(category, text)
    model = _extract_model(text, category, brand)
    criteria = _extract_major_criteria(text, category)
    condition = parse_condition(text)
    modifiers = _extract_model_modifiers(text, model)
    desired: dict[str, Any] = {}
    required = dict(criteria)
    desired_hint = re.search(r"(?:желательно|лучше|хорошо\s+бы)\s+(.+?)(?:[,.;]|$)", normalize_text(text))
    if desired_hint:
        hinted = _extract_major_criteria(desired_hint.group(1), category)
        for key, value in hinted.items():
            desired[key] = required.pop(key, value)
    return {
        "category": category,
        "brand": brand,
        "model": model,
        "model_modifiers": modifiers,
        "condition": condition,
        "budget": parse_budget(text),
        "city": parse_city(text),
        "use_case": parse_use_case(text),
        "required_criteria": required,
        "desired_criteria": desired,
        **criteria,
    }


def parse_product_name(text: str) -> str:
    details = parse_request_details(text)
    if details["model"]:
        return str(details["model"])
    category = str(details["category"])
    display = _CATEGORY_DISPLAY.get(category, "товар")
    brand = str(details["brand"] or "")
    if brand and category not in {"unknown", "mattress", "bed"}:
        return f"{brand} {display}"
    if category != "unknown":
        return display

    result = normalize_text(text)
    result = re.sub(
        r"^(?:мне\s+)?(?:нуж(?:ен|на|но|ны)|хочу|ищу|посоветуйте|подскажите|"
        r"порекомендуйте|найдите|купить)\s+",
        "",
        result,
    )
    result = _BUDGET_CONTEXT_RE.sub("", result)
    result = _CURRENCY_AMOUNT_RE.sub("", result)
    result = _SPACE_RE.sub(" ", result).strip(" ,.-")
    return " ".join(result.split()[:8])[:80] or (text or "товар")[:80]


def parse_is_used_allowed(text: str) -> bool:
    return parse_condition(text) == "used"


def parse_important_criteria(text: str) -> str:
    details = parse_request_details(text)
    values = _human_criteria(
        {**details.get("required_criteria", {}), **details.get("desired_criteria", {})},
        str(details.get("brand") or ""),
    )
    return ", ".join(values)


def is_ps5_tv(product_name: str, use_case: str, original_query: str) -> bool:
    return detect_category(f"{product_name} {original_query}") == "tv" and any(
        word in normalize_text(f"{use_case} {original_query}")
        for word in ("ps5", "ps 5", "playstation", "плейстейшен", "приставк", "игр")
    )


def full_parse(text: str) -> dict[str, Any]:
    details = parse_request_details(text)
    product_name = parse_product_name(text)
    criteria = parse_important_criteria(text)
    use_case = str(details["use_case"])
    if is_ps5_tv(product_name, use_case, text):
        required = ["4K", "HDMI", "игровой режим/низкая задержка", "120 Гц желательно"]
        existing = {item.strip().lower() for item in criteria.split(",") if item.strip()}
        criteria = ", ".join([item for item in required if item.lower() not in existing] + ([criteria] if criteria else []))
    result: dict[str, Any] = {
        "original_query": (text or "").strip(),
        "product_name": product_name,
        "use_case": use_case,
        "budget": str(details["budget"]),
        "city": str(details["city"]),
        "important_criteria": criteria,
        "clean_search_query": build_search_query(product_name, use_case, str(details["budget"]), str(details["city"]), criteria),
        "is_used_allowed": details["condition"] == "used",
        "category": details["category"],
        "brand": details["brand"],
        "model": details["model"],
        "model_modifiers": details["model_modifiers"],
        "condition": details["condition"],
        "required_criteria": details["required_criteria"],
        "desired_criteria": details["desired_criteria"],
    }
    for key in (
        "size", "diagonal", "refresh_rate", "storage_gb", "ram_gb", "ssd_gb",
        "resolution", "cpu", "volume_l", "load_kg", "battery_mah",
    ):
        if key in details:
            result[key] = details[key]
    return result


def normalize_legacy_requirement_text(value: object) -> str:
    """Removes persisted wizard question labels while keeping their answers."""
    parts: list[str] = []
    for raw_part in re.split(r"[;\n]+", str(value or "")):
        part = _SPACE_RE.sub(" ", raw_part).strip(" ,.-")
        if not part:
            continue
        cleaned = _QUESTION_PREFIX_RE.sub("", part).strip(" ,.-")
        if cleaned and cleaned.lower() not in {"не важно", "неважно", "нет", "любой", "любая"}:
            parts.append(cleaned)
    return "; ".join(dict.fromkeys(parts))


def split_model_modifiers(model: object, modifiers: object = ()) -> tuple[str, list[str]]:
    """Splits a display model into base model plus strict variant modifiers."""
    full_model = _SPACE_RE.sub(" ", str(model or "")).strip()
    parsed_modifiers = [str(item).strip().lower() for item in (modifiers or []) if str(item).strip()]
    if not parsed_modifiers and full_model:
        parsed_modifiers = _extract_model_modifiers(full_model, full_model)
    ordered = sorted(dict.fromkeys(parsed_modifiers), key=lambda item: (-len(item), item))
    base = full_model
    for modifier in ordered:
        display = _MODEL_VARIANT_DISPLAY.get(modifier, modifier)
        base = re.sub(rf"\s+{re.escape(display)}$", "", base, flags=re.IGNORECASE).strip()
    return base or full_model, ordered


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _object_value(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _feature_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = re.split(r"[;\n]+", str(value or ""))
    result: list[str] = []
    for item in raw:
        cleaned = normalize_legacy_requirement_text(item)
        if cleaned:
            result.extend(part.strip() for part in cleaned.split(";") if part.strip())
    return list(dict.fromkeys(result))


def normalize_request_data(request: Any) -> dict[str, Any]:
    """Builds one canonical request snapshot without requiring a DB migration."""
    payload = _json_dict(_object_value(request, "requirements_json", ""))
    if isinstance(request, dict):
        payload = {**request, **payload}

    product_name = str(
        payload.get("product_name")
        or payload.get("product")
        or _object_value(request, "product_name")
        or _object_value(request, "product")
        or ""
    ).strip()
    original_query = str(
        payload.get("original_query")
        or _object_value(request, "original_query")
        or product_name
    ).strip()
    parsed = full_parse(" ".join(part for part in (product_name, original_query) if part))

    category_value = str(payload.get("category") or _object_value(request, "category") or "").strip()
    category = _WIZARD_CATEGORY_TO_SEARCH_CATEGORY.get(category_value, category_value)
    if category in {"", "manual"}:
        category = str(parsed.get("category") or "unknown")

    stored_model = str(payload.get("model") or "").strip()
    stored_modifiers = payload.get("model_modifiers") or []
    if stored_model:
        model, model_modifiers = split_model_modifiers(stored_model, stored_modifiers)
    else:
        model, model_modifiers = split_model_modifiers(parsed.get("model"), parsed.get("model_modifiers"))

    required = dict(parsed.get("required_criteria") or {})
    required.update(dict(payload.get("required_criteria") or {}))
    desired = dict(parsed.get("desired_criteria") or {})
    desired.update(dict(payload.get("desired_criteria") or {}))

    details = payload.get("category_details") or {}
    if not isinstance(details, dict):
        details = {}
    memory_text = " ".join(str(item or "") for item in (
        payload.get("storage_gb"), details.get("phone_memory"),
        payload.get("requirements"), _object_value(request, "important_criteria"),
    ))
    storage_match = re.search(
        r"(?:объ[её]м\s+памяти\s+нужен\s*:\s*)?(?<!\d)(64|128|256|512|1024)(?:\s*(?:гб|gb))?\b",
        normalize_text(memory_text),
    )
    storage_gb = int(storage_match.group(1)) if storage_match else _number_from_value(payload.get("storage_gb"))
    if storage_gb is not None:
        required["storage_gb"] = storage_gb

    if details.get("phone_color"):
        required["color"] = normalize_legacy_requirement_text(details["phone_color"])
    if details.get("phone_sim_region"):
        required["sim_variant"] = normalize_legacy_requirement_text(details["phone_sim_region"])

    legacy_requirements = normalize_legacy_requirement_text(
        payload.get("requirements")
        or _object_value(request, "important_criteria")
        or _object_value(request, "criteria")
    )
    legacy_is_storage_only = bool(
        storage_gb is not None
        and re.fullmatch(r"(?:64|128|256|512|1024)(?:\s*(?:гб|gb))?", normalize_text(legacy_requirements))
    )
    required_features = _feature_list(
        payload.get("required_features") or ("" if legacy_is_storage_only else legacy_requirements)
    )
    optional_features = _feature_list(payload.get("optional_features"))
    budget = str(payload.get("budget") or _object_value(request, "budget") or parsed.get("budget") or "").replace(" ", "")
    city = str(payload.get("city") or _object_value(request, "city") or parsed.get("city") or "").strip()
    condition = str(payload.get("condition") or _object_value(request, "condition") or parsed.get("condition") or "any")

    brand = str(
        payload.get("brand")
        or parsed.get("brand")
        or known_brand(category, " ".join(part for part in (product_name, original_query) if part))
        or ("Apple" if normalize_text(model).startswith("iphone ") else "")
    )

    result = {
        **parsed,
        "original_query": original_query,
        "product_name": product_name or str(parsed.get("product_name") or "товар"),
        "category": category or "unknown",
        "brand": brand,
        "model": model,
        "model_modifiers": model_modifiers,
        "storage_gb": storage_gb,
        "budget": budget,
        "city": city,
        "condition": condition,
        "priority": str(payload.get("priority") or _object_value(request, "priority") or "balance"),
        "required_features": required_features,
        "optional_features": optional_features,
        "required_criteria": required,
        "desired_criteria": desired,
    }
    return result


def _number_from_value(value: object) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def build_request_search_identity(request: Any) -> str:
    """Keep the saved product category in every external search phrase."""
    details = normalize_request_data(request)
    identity = str(details.get("product_name") or details.get("model") or "товар").strip()
    category = str(details.get("category") or "unknown")
    category_label = _CATEGORY_DISPLAY.get(category, "")
    spec = get_category_spec(category)
    has_category_word = any(
        contains_marker(identity, marker)
        for marker in (*spec.names, *spec.required_product_markers)
    )
    if category_label and category_label != "товар" and not has_category_word:
        identity = f"{identity} {category_label}".strip()
    return identity or "товар"


def build_request_search_query(request: Any) -> str:
    """Builds a clean query from canonical values, never wizard prompts."""
    details = normalize_request_data(request)
    criteria = _human_criteria(dict(details.get("required_criteria") or {}))
    criteria.extend(str(item) for item in details.get("required_features") or [])
    request_text = " ".join(str(getattr(request, name, "") or "") for name in ("use_case", "original_query", "criteria"))
    if str(details.get("category") or "") == "laptop" and re.search(r"\b(?:игров\w*|gaming|game)\b", request_text, re.I):
        criteria.insert(0, "игровой")
    clean_criteria = ", ".join(dict.fromkeys(item for item in criteria if item))
    return build_search_query(
        build_request_search_identity(request),
        "",
        str(details.get("budget") or ""),
        str(details.get("city") or ""),
        clean_criteria,
    )
