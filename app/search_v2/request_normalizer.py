"""Strict adapter from legacy request payloads to :class:`SearchRequestV2`."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from app.category_registry import CATEGORY_SPECS
from app.request_parser import normalize_request_data, split_model_modifiers

from .models import ProductCondition, SearchRequestV2


_SPACE_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_MODIFIER_DISPLAY = {
    "pro max": "Pro Max", "pro": "Pro", "max": "Max", "plus": "Plus",
    "mini": "Mini", "se": "SE", "fe": "FE", "ultra": "Ultra",
    "lite": "Lite", "air": "Air", "e": "e",
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
