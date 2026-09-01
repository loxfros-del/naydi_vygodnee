"""Strict adapter from legacy request payloads to :class:`SearchRequestV2`."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from app.category_registry import CATEGORY_SPECS, contains_marker
from app.request_parser import normalize_request_data, split_model_modifiers

from .models import ProductCondition, SearchRequestV2


_SPACE_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_MODIFIER_DISPLAY = {
    "pro max": "Pro Max", "pro": "Pro", "max": "Max", "plus": "Plus",
    "mini": "Mini", "se": "SE", "fe": "FE", "ultra": "Ultra",
    "lite": "Lite", "air": "Air", "e": "e",
}

# V2 can safely cover more technical product types without turning every
# free-form phrase into a product.  The escape hatch is intentionally narrow:
# a listed technical brand plus a concrete model code are both mandatory.
# Unknown brands or category-only requests stay unsupported and never trigger
# source traffic.
_GENERIC_TECH_BRANDS = tuple(CATEGORY_SPECS["generic_tech"].known_brands)
_GENERIC_TECH_STOP_WORDS = {
    "аксессуар", "батарея", "блок", "в", "видеокарта", "выбрать", "гаджет", "для", "дрель",
    "до", "за", "запчасть", "зарядка", "игры", "инструмент", "ищу", "кабель", "камера", "купить",
    "модель", "мне", "монитор", "на", "найди", "найдите", "наушники", "нужен", "нужна",
    "новый", "new", "used", "бу", "подержанный", "ноутбук", "от", "планшет", "пылесос",
    "проектор", "просто", "руб", "рублей", "р", "роутер", "смартфон", "телевизор", "телефон",
    "техника", "товар", "цена", "хочу", "чехол",
}
_GENERIC_TECH_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-я][0-9A-Za-zА-Яа-я+./-]*")
_GENERIC_TECH_BUDGET_TOKEN_RE = re.compile(r"(?:\d{1,3}[кk]|\d{4,6})$", re.IGNORECASE)
_MACBOOK_TOKEN_RE = re.compile(
    r"\b(?:apple\s+)?macbook\b(?P<tail>(?:\s+(?:air|pro|max|"
    # A display size must end at a token boundary.  Without that boundary
    # ``16GB/512GB`` was read as a 16-inch screen and the RAM requirement was
    # silently lost.
    r"1[3-7](?:[.,]\d+)?(?=\s|$|[\"”″])|m\d+)){1,5})",
    re.IGNORECASE,
)
_COMPACT_LAPTOP_MEMORY_RE = re.compile(
    r"(?<!\d)(?P<ram>4|8|16|24|32|64|128)\s*(?:гб|gb)?\s*/\s*"
    r"(?P<storage>128|256|512|1024|2048)\s*(?:гб|gb)?(?!\d)",
    re.IGNORECASE,
)

# The legacy parser intentionally returns broadly useful boolean criteria.
# V2 comparisons use the category facts' canonical field names, so map only
# a small, explicit set of unambiguous user-selected facts.  Each rule is
# category-scoped: an "automatic" word must never alter an unrelated product.
_EXPLICIT_SPEC_ALIASES: dict[str, dict[str, tuple[str, Any]]] = {
    "coffee_machine": {
        "automatic": ("machine_type", "automatic"),
    },
    "monitor": {
        "ips": ("panel", "IPS"),
    },
    "tv": {
        "ips": ("panel", "IPS"),
    },
    "headphones": {
        "wireless": ("connection", "wireless"),
    },
    "mattress": {
        "medium_firmness": ("firmness", "средняя"),
    },
}


def _value(request: Any, name: str, default: Any = "") -> Any:
    if isinstance(request, Mapping):
        return request.get(name, default)
    return getattr(request, name, default)


def _payload(request: Any) -> dict[str, Any]:
    if isinstance(request, str):
        return {"original_query": request, "product_name": request}
    result = dict(request) if isinstance(request, Mapping) else {}
    raw_json = _value(request, "requirements_json", "")
    if isinstance(raw_json, Mapping):
        result.update(raw_json)
    elif raw_json:
        try:
            decoded = json.loads(str(raw_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = {}
        if isinstance(decoded, dict):
            result.update(decoded)
    return result


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip(" ,;:")


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    match = _NUMBER_RE.search(str(value).replace(" ", ""))
    if not match:
        return None
    parsed = float(match.group().replace(",", "."))
    return int(parsed) if parsed.is_integer() else parsed


def _budget(value: Any) -> int | None:
    text = _clean(value).casefold().replace("\u00a0", " ")
    match = _NUMBER_RE.search(text.replace(" ", ""))
    if not match:
        return None
    amount = float(match.group().replace(",", "."))
    if re.search(r"(?:^|\d)\s*(?:к|k|тыс)", text):
        amount *= 1_000
    result = int(amount)
    return result if result > 0 else None


def _condition(value: Any) -> ProductCondition:
    normalized = _clean(value).casefold()
    if normalized in {"new", "новый", "новая", "новое", "только новый"}:
        return ProductCondition.NEW
    if normalized in {"used", "б/у", "бу", "подержанный", "подержанная"}:
        return ProductCondition.USED
    if normalized in {"refurbished", "восстановленный", "восстановленная"}:
        return ProductCondition.REFURBISHED
    if normalized in {"unknown", "неизвестно"}:
        return ProductCondition.UNKNOWN
    return ProductCondition.ANY


def _modifier(value: Any) -> str:
    cleaned = _clean(value)
    return _MODIFIER_DISPLAY.get(cleaned.casefold(), cleaned)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _generic_tech_identity(value: Any) -> tuple[str, str]:
    """Return a trustworthy ``(brand, model)`` pair for an unknown device.

    We accept only a known technical brand followed by a compact model phrase
    containing a digit.  A digit is important here: it separates a model such
    as ``Canon EOS R50`` or ``Bosch GSR 18V-45`` from vague requests such as
    ``хорошая камера Canon``.  The model phrase stops at common product and
    shopping words, preventing a price, city, or request prose from becoming
    an invented identity.
    """
    text = _clean(value)
    if not text:
        return "", ""
    for brand in sorted(_GENERIC_TECH_BRANDS, key=len, reverse=True):
        pattern = re.escape(brand).replace(r"\ ", r"\s+")
        match = re.search(rf"(?<![0-9A-Za-zА-Яа-я]){pattern}(?![0-9A-Za-zА-Яа-я])", text, re.IGNORECASE)
        if not match:
            continue
        tail = text[match.end():]
        tokens: list[str] = []
        for token_match in _GENERIC_TECH_TOKEN_RE.finditer(tail):
            token = token_match.group(0)
            if (
                token.casefold() in _GENERIC_TECH_STOP_WORDS
                or _GENERIC_TECH_BUDGET_TOKEN_RE.fullmatch(token)
            ):
                break
            tokens.append(token)
            if len(tokens) >= 4:
                break
        if not _has_generic_tech_model_code(tokens):
            continue
        # A model code is normally one to four compact tokens.  Do not accept
        # a prose tail that happens to contain a price or a year.
        model = _clean(f"{brand} {' '.join(tokens)}")
        if model:
            return brand, model
    return "", ""


def _has_generic_tech_model_code(tokens: list[str]) -> bool:
    """Require a model code, not only a price or a calendar year.

    Most technical SKUs contain an alpha-numeric code (``R50``, ``GSR``,
    ``18V-45``).  A small number use a generation number after a named model
    (``DJI Mini 4``), which is accepted only with a non-year digit and another
    model word.  This keeps ``Canon 2026`` and prices such as ``Canon 60к``
    out of the supported search path.
    """
    if not tokens:
        return False
    if any(
        any(character.isalpha() for character in token)
        and any(character.isdigit() for character in token)
        for token in tokens
    ):
        return True
    has_non_year_number = any(
        token.isdigit() and not re.fullmatch(r"(?:19|20)\d{2}", token)
        for token in tokens
    )
    has_model_word = any(token.isalpha() and len(token) >= 2 for token in tokens)
    return has_non_year_number and has_model_word and len(tokens) >= 2


def _legacy_requirement_specs(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Recover structured values from old wizard question/answer strings."""
    texts = " ".join(
        _clean(payload.get(key))
        for key in ("requirements", "important_criteria", "criteria")
        if payload.get(key)
    )
    result: dict[str, Any] = {}
    patterns: tuple[tuple[str, str], ...] = (
        ("storage_gb", r"(?:памят\w*|накопител\w*|storage)[^\d]{0,30}(64|128|256|512|1024)\s*(?:гб|gb)?"),
        ("ram_gb", r"(?:оператив\w*|ram|озу)[^\d]{0,30}(4|8|16|24|32|64|128)\s*(?:гб|gb)?"),
        ("ssd_gb", r"(?:ssd)[^\d]{0,20}(128|256|512|1024|2048)\s*(?:гб|gb)?"),
        ("diagonal", r"(?:диагонал\w*|экран)[^\d]{0,30}(\d{2}(?:[.,]\d)?)\s*(?:дюйм\w*|\")?"),
        ("refresh_rate", r"(?:герцов\w*|частот\w*|refresh)[^\d]{0,30}(60|75|90|100|120|144|165|240)\s*(?:гц|hz)?"),
        ("size", r"(?:размер\w*)?[^\d]{0,20}(\d{2,3}\s*[xх×]\s*\d{2,3})\s*(?:см)?"),
    )
    for key, pattern in patterns:
        match = re.search(pattern, texts, re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).replace(",", ".")
        if key == "size":
            result[key] = re.sub(r"\s*[xх×]\s*", "x", raw)
        else:
            result[key] = _number(raw)
    # The oldest phone wizard stored only ``"Какой объём памяти нужен: 256"``.
    if "storage_gb" not in result:
        match = re.search(r"объ[её]м\s+памяти[^\d]{0,40}(64|128|256|512|1024)", texts, re.IGNORECASE)
        if match:
            result["storage_gb"] = int(match.group(1))
    return result


def _feature_tokens(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = re.split(r"[;\n]+", str(value or ""))
    return _dedupe([_clean(item) for item in candidates if _clean(item)])


def _compact_laptop_memory(text: Any) -> tuple[int, int] | None:
    """Parse the common laptop shorthand ``16/256`` as RAM/storage.

    It deliberately accepts only realistic RAM and SSD capacities, so prices,
    screen sizes and arbitrary slash-separated model fragments do not become
    mandatory configuration by accident.
    """
    match = _COMPACT_LAPTOP_MEMORY_RE.search(_clean(text))
    if not match:
        return None
    return int(match.group("ram")), int(match.group("storage"))


def _canonicalize_explicit_category_specs(category: str, specs: dict[str, Any]) -> dict[str, Any]:
    """Translate only explicit, unambiguous user criteria to fact keys.

    The request parser uses generic booleans such as ``automatic`` and
    ``ips``.  Offers, however, expose these properties as category facts
    (``machine_type`` and ``panel``).  Bridging the names here makes the
    user-selected parameter a real strict condition.  We deliberately do not
    infer absent specs, and an explicitly structured canonical value wins a
    conflicting free-text marker.
    """
    result = dict(specs)
    for source_key, (target_key, target_value) in _EXPLICIT_SPEC_ALIASES.get(category, {}).items():
        if result.get(source_key) is not True:
            continue
        result.pop(source_key, None)
        result.setdefault(target_key, target_value)
    return result


def _strip_laptop_modifiers_from_model(model: str, modifiers: list[str]) -> str:
    """Keep Air as a separate required modifier when it appears before M4.

    ``split_model_modifiers`` handles suffixes well (``iPhone 16 Pro``), but
    laptop names commonly put the variant in the middle (``MacBook Air M4``).
    Without this the planner emits a duplicate ``Air`` token and retailers
    receive a noisier query.
    """
    result = _clean(model)
    for modifier in modifiers:
        display = _MODIFIER_DISPLAY.get(_clean(modifier).casefold(), _clean(modifier))
        if not display:
            continue
        candidate = re.sub(rf"(?<!\w){re.escape(display)}(?!\w)", " ", result, flags=re.IGNORECASE)
        candidate = _clean(candidate)
        if candidate and candidate != result:
            result = candidate
    return result or _clean(model)


def _macbook_identity(value: Any) -> tuple[str, list[str]]:
    """Extract a compact MacBook identity from a free-form laptop request.

    The generic legacy parser only keeps three tokens after ``Apple``.  For
    ``Ноутбук Apple MacBook Air 13 M4`` that loses the M4 chip, which makes an
    M3 or an older model eligible for the same search.  Keep model-family,
    display size and chip in the canonical model, while variants such as Air
    remain separate hard modifiers.  Category words and the duplicate brand
    are deliberately excluded because the planner adds the brand itself.
    """
    match = _MACBOOK_TOKEN_RE.search(_clean(value))
    if not match:
        return "", []
    tokens = [token.casefold() for token in match.group("tail").split()]
    chip = next((token.upper() for token in tokens if re.fullmatch(r"m\d+", token)), "")
    if not chip:
        # A generic "MacBook Air" query is still valid, but this helper must
        # never replace a concrete parsed model with an identity that lost its
        # generation/chip discriminator.
        return "", []
    diagonal = next(
        (token.replace(",", ".") for token in tokens if re.fullmatch(r"1[3-7](?:[.,]\d+)?", token)),
        "",
    )
    modifiers = [
        _MODIFIER_DISPLAY.get(token, token.title())
        for token in tokens
        if token in _MODIFIER_DISPLAY and token != "e"
    ]
    model = _clean(" ".join(("MacBook", diagonal, chip)))
    return model, _dedupe(modifiers)


def spec_token(key: str, value: Any) -> str:
    """Canonical human token used by both normalizer and planner validation."""
    if value in (None, "", False):
        return ""
    if key == "storage_gb":
        return f"{value} ГБ"
    if key == "ram_gb":
        return f"RAM {value} ГБ"
    if key == "ssd_gb":
        return f"SSD {value} ГБ"
    if key == "diagonal":
        return f"{value} дюймов"
    if key == "refresh_rate":
        return f"{value} Гц"
    if key == "size":
        return str(value).replace("х", "x").replace("×", "x")
    if key == "machine_type":
        return {
            "automatic": "автоматическая",
            "espresso": "рожковая",
            "capsule": "капсульная",
        }.get(_clean(value).casefold(), _clean(value))
    if key == "connection" and _clean(value).casefold() == "wireless":
        return "беспроводные"
    if key == "firmness" and _clean(value).casefold() in {"medium", "средняя"}:
        return "средняя жёсткость"
    if key == "cappuccinator" and value is True:
        return "капучинатор"
    if key == "wet_cleaning" and value is True:
        return "влажная уборка"
    if key == "lift_mechanism" and value is True:
        return "подъёмный механизм"
    if isinstance(value, bool):
        return key.replace("_", " ") if value else ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_clean(item) for item in value if _clean(item))
    return _clean(value)


def normalize_legacy_request(request: Any) -> SearchRequestV2:
    """Build a strict, dependency-free V2 request from a legacy object/dict/string."""
    payload = _payload(request)
    legacy_input: Any = payload if isinstance(request, str) else request
    if payload and not isinstance(request, str):
        # Preserve object attributes while allowing decoded requirements_json to win.
        legacy_input = {**{
            key: _value(request, key, "") for key in (
                "product", "product_name", "original_query", "budget", "city",
                "condition", "priority", "category", "important_criteria", "criteria",
            )
        }, **payload}
    details = normalize_request_data(legacy_input)

    base_model, parsed_modifiers = split_model_modifiers(
        details.get("model") or payload.get("model") or "",
        details.get("model_modifiers") or payload.get("model_modifiers") or (),
    )
    raw_model_text = _clean(details.get("model") or payload.get("model") or "")
    raw_model_without_compact_memory = re.sub(
        r"(?<!\d)(?:4|8|16|24|32|64|128)\s*/\s*"
        r"(?:128|256|512|1024|2048)(?:\s*(?:гб|gb))?(?!\d)",
        " ",
        raw_model_text,
        flags=re.IGNORECASE,
    )
    # A compact ``16/256`` configuration is a requirement, not part of a
    # model name.  Removing it here lets the title match when a store formats
    # the same configuration as ``16 GB / 256 GB``.
    if raw_model_without_compact_memory != raw_model_text:
        base_model, parsed_modifiers = split_model_modifiers(
            raw_model_without_compact_memory,
            details.get("model_modifiers") or payload.get("model_modifiers") or (),
        )
    modifiers = _dedupe([_modifier(item) for item in parsed_modifiers])

    required_specs = dict(details.get("required_criteria") or {})
    required_specs.update(_legacy_requirement_specs({**payload, **{
        "important_criteria": _value(request, "important_criteria", payload.get("important_criteria", "")),
        "criteria": _value(request, "criteria", payload.get("criteria", "")),
    }}))
    storage = _number(details.get("storage_gb") or payload.get("storage_gb"))
    if storage is not None:
        required_specs["storage_gb"] = storage
    required_specs = {
        str(key): value for key, value in required_specs.items()
        if value not in (None, "", False, [], {})
    }

    optional_specs = dict(details.get("desired_criteria") or {})
    required_features = _feature_tokens(details.get("required_features"))
    optional_features = _feature_tokens(details.get("optional_features"))
    if required_features:
        required_specs["features"] = required_features
    if optional_features:
        optional_specs["features"] = optional_features

    brand = _clean(details.get("brand") or payload.get("brand"))
    if not brand and base_model.casefold().startswith("iphone "):
        brand = "Apple"
    condition = _condition(details.get("condition") or payload.get("condition"))
    category = _clean(details.get("category") or "unknown").casefold()

    if category in {"unknown", "generic_tech"}:
        # Unknown is not automatically a product.  It becomes generic technical
        # equipment only when the request has a recognised brand *and* a model
        # code; otherwise the service remains fail-closed.  Even an explicitly
        # supplied ``generic_tech`` category must prove the same identity, so a
        # caller cannot turn arbitrary prose into a supported search.
        generic_candidates = (
            _clean(payload.get("product_name") or payload.get("product")),
            _clean(payload.get("original_query")),
            _clean(payload.get("model")),
            _clean(details.get("original_query")),
        )
        generic_source = " ".join(item for item in generic_candidates if item)
        generic_brand, generic_model = "", ""
        # The same product is often present in several legacy fields.  Parse
        # them one at a time, otherwise the duplicated phrase can become part
        # of a model code.
        for candidate in generic_candidates:
            generic_brand, generic_model = _generic_tech_identity(candidate)
            if generic_brand and generic_model:
                break
        generic_accessory = any(
            contains_marker(generic_source, marker)
            for marker in CATEGORY_SPECS["generic_tech"].accessory_markers
        )
        if generic_brand and generic_model and not generic_accessory and not contains_marker(generic_source, "ремонт"):
            category = "generic_tech"
            brand = generic_brand
            base_model = generic_model
            modifiers = []
        elif category == "generic_tech":
            category = "unknown"

    if category == "laptop":
        # Prefer the structured MacBook identity from a complete user phrase
        # over the generic legacy parser's truncated ``Apple MACBOOK 13``.
        # Inspect fields independently: they frequently contain the same
        # phrase and joining them can turn duplicated prose into a model.
        for candidate in (
            _clean(payload.get("model")),
            _clean(payload.get("product_name") or payload.get("product")),
            _clean(payload.get("original_query")),
            _clean(details.get("model")),
            _clean(details.get("original_query")),
        ):
            macbook_model, macbook_modifiers = _macbook_identity(candidate)
            if macbook_model:
                base_model = macbook_model
                modifiers = _dedupe([*macbook_modifiers, *modifiers])
                break
        base_model = _strip_laptop_modifiers_from_model(base_model, modifiers)
        if not brand and "macbook" in base_model.casefold():
            brand = "Apple"
        compact_memory = _compact_laptop_memory(" ".join((
            _clean(payload.get("product_name") or payload.get("product")),
            _clean(payload.get("original_query")),
            _clean(payload.get("model")),
        )))
        if compact_memory:
            ram_gb, storage_gb = compact_memory
            required_specs.setdefault("ram_gb", ram_gb)
            required_specs.setdefault("storage_gb", storage_gb)

    # Make a user-written category feature match the same normalized fact
    # name that offer extraction produces.  This happens after category
    # detection and before query construction, so it neither infers a new
    # feature nor weakens an existing hard parameter.
    required_specs = _canonicalize_explicit_category_specs(category, required_specs)
    optional_specs = _canonicalize_explicit_category_specs(category, optional_specs)

    hard_tokens: list[str] = []
    if brand:
        hard_tokens.append(brand)
    if base_model:
        hard_tokens.append(base_model)
    hard_tokens.extend(modifiers)
    hard_tokens.extend(spec_token(key, value) for key, value in required_specs.items())
    if condition not in {ProductCondition.ANY, ProductCondition.UNKNOWN}:
        hard_tokens.append(condition.value)

    city = _clean(details.get("city") or payload.get("city"))
    budget = _budget(details.get("budget") or payload.get("budget"))
    soft_tokens = [
        *(spec_token(key, value) for key, value in optional_specs.items()),
        *([city] if city else []),
        *([f"до {budget}"] if budget else []),
    ]

    # Parser fields can contain both the free-form request and the extracted
    # model.  Once generic identity is proven, retain the compact canonical
    # model rather than rebuilding it from those duplicated fields.
    if category == "generic_tech":
        hard_tokens = [brand, base_model]

    return SearchRequestV2(
        original_query=_clean(details.get("original_query") or payload.get("original_query")),
        category=category or "unknown",
        brand=brand,
        canonical_model=_clean(base_model),
        model_modifiers=modifiers,
        required_specs=required_specs,
        optional_specs=optional_specs,
        budget=budget,
        city=city,
        condition=condition,
        priority=_clean(details.get("priority") or payload.get("priority") or "balance").casefold(),
        supported_category=category in CATEGORY_SPECS and category != "unknown",
        hard_tokens=_dedupe(hard_tokens),
        soft_tokens=_dedupe(soft_tokens),
    )


# Public aliases make migration call sites explicit without tying them to legacy names.
normalize_request = normalize_legacy_request
from_legacy_request = normalize_legacy_request


__all__ = [
    "from_legacy_request", "normalize_legacy_request", "normalize_request", "spec_token",
]
