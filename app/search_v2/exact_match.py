"""Hard exact-match gate executed before ranking or recommendations."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .models import ExactMatchResult, Offer, ProductCondition, SearchRequestV2
from .request_semantics import (
    category_title_matches,
    is_generic_request,
    meaningful_model_tokens,
    semantic_tokens,
)


_ACCESSORY_MARKERS = (
    "чехол", "case", "стекло", "кабель", "зарядное устройство", "зарядка для",
    "ремешок", "держатель", "адаптер", "кронштейн", "наушники для",
    "клавиатура для", "запчаст",
)
_MODIFIERS = {"pro", "max", "ultra", "plus", "mini", "air", "se"}
_MINIMUM_NUMERIC_KEYS = {"refresh_rate", "hz", "ram_gb", "ssd_gb", "load_capacity_kg"}
_BOOLEAN_KEYS = {"wireless", "ultrawide", "headrest", "lumbar_support"}
_RESOLUTION_ALIASES = {
    "4k": "4k",
    "uhd": "4k",
    "3840x2160": "4k",
    "2160p": "4k",
    "wqhd": "qhd",
    "qhd": "qhd",
    "2k": "qhd",
    "2560x1440": "qhd",
    "1440p": "qhd",
    "fhd": "fhd",
    "fullhd": "fhd",
    "1920x1080": "fhd",
    "1080p": "fhd",
}
_VALUE_ALIASES = {
    "office": "office",
    "офисное": "office",
    "офисный": "office",
    "ergonomic": "ergonomic",
    "эргономичное": "ergonomic",
    "эргономичный": "ergonomic",
    "gaming": "gaming",
    "игровое": "gaming",
    "игровой": "gaming",
    "mesh": "mesh",
    "сетчатое": "mesh",
    "сетка": "mesh",
    "usb c": "usb-c",
    "usb type c": "usb-c",
    "type c": "usb-c",
}


def _tokens(value: Any) -> list[str]:
    return semantic_tokens(value)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def _candidate_spec(offer: Offer, key: str) -> Any:
    identity = offer.identity
    if identity is None:
        return None
    configuration = identity.key_configuration or {}
    aliases = {
        "storage": identity.storage,
        "storage_gb": identity.storage,
        "memory": identity.storage,
        "ssd_gb": configuration.get("ssd_gb", identity.storage),
        "ram": configuration.get("ram_gb", configuration.get("ram")),
        "ram_gb": configuration.get("ram_gb", configuration.get("ram")),
        "size": identity.size,
        "diagonal": identity.diagonal,
        "refresh_rate": identity.refresh_rate,
        "hz": identity.refresh_rate,
        "resolution": configuration.get("resolution"),
        "features": configuration.get("features", ()),
        "cpu": configuration.get("cpu", configuration.get("cpu_family")),
        "cpu_family": configuration.get("cpu_family", configuration.get("cpu")),
        "gpu": configuration.get("gpu"),
        "matrix": configuration.get("matrix"),
        "connector": configuration.get("connector"),
        "wireless": configuration.get("wireless"),
        "form_factor": configuration.get("form_factor"),
        "ultrawide": configuration.get("ultrawide"),
        "type": configuration.get("type"),
        "material": configuration.get("material"),
        "headrest": configuration.get("headrest"),
        "lumbar_support": configuration.get("lumbar_support"),
        "load_capacity_kg": configuration.get("load_capacity_kg"),
        "condition": identity.condition,
    }
    if key in aliases:
        value = aliases[key]
        return value if value not in (None, "", [], (), {}) else offer.facts.get(key)
    return configuration.get(key, offer.facts.get(key))


def _resolution(value: Any) -> str:
    compact = "".join(_tokens(value))
    for alias, canonical in _RESOLUTION_ALIASES.items():
        if alias in compact:
            return canonical
    return compact


def _feature_tokens(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value or "")
    tokens = set(_tokens(text))
    if "шумоподавление" in tokens or ("активное" in tokens and "шумоподавление" in tokens):
        tokens.add("anc")
    if "беспроводные" in tokens or "беспроводной" in tokens:
        tokens.add("wireless")
    return tokens


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = " ".join(_tokens(value))
    if normalized in {"true", "yes", "1", "да", "есть"}:
        return True
    if normalized in {"false", "no", "0", "нет"}:
        return False
    return None


def _canonical_value(value: Any) -> str:
    normalized = " ".join(_tokens(value))
    return _VALUE_ALIASES.get(normalized, normalized)


def _different(required: Any, candidate: Any, key: str) -> bool:
    if candidate in (None, "", ProductCondition.UNKNOWN):
        return False
    if key == "condition":
        required_value = required.value if isinstance(required, ProductCondition) else str(required).casefold()
        candidate_value = candidate.value if isinstance(candidate, ProductCondition) else str(candidate).casefold()
        return required_value not in {"", "any"} and candidate_value != required_value
    if key == "features":
        return not _feature_tokens(required).issubset(_feature_tokens(candidate))
    if key == "resolution":
        return _resolution(required) != _resolution(candidate)
    if key in _BOOLEAN_KEYS:
        required_bool, candidate_bool = _bool(required), _bool(candidate)
        if required_bool is None or candidate_bool is None:
            return _canonical_value(required) != _canonical_value(candidate)
        return required_bool and not candidate_bool
    required_number, candidate_number = _number(required), _number(candidate)
    if required_number is not None and candidate_number is not None:
        if key in _MINIMUM_NUMERIC_KEYS:
            return candidate_number + 0.01 < required_number
        return abs(required_number - candidate_number) > 0.01
    return _canonical_value(required) != _canonical_value(candidate)


def evaluate_exact_match(request: SearchRequestV2, offer: Offer) -> ExactMatchResult:
    title_tokens = _tokens(offer.title)
    title_text = " ".join(title_tokens)
    if any(marker in title_text for marker in _ACCESSORY_MARKERS):
        return ExactMatchResult.ACCESSORY
    if not offer.identity:
        return ExactMatchResult.UNKNOWN

    generic_request = is_generic_request(request)
    if generic_request:
        if not category_title_matches(request.category, offer.title):
            return ExactMatchResult.MODEL_MISMATCH
        required_model_tokens = meaningful_model_tokens(request)
    else:
        required_model_tokens = _tokens(request.canonical_model)
    if required_model_tokens and not all(token in title_tokens for token in required_model_tokens):
        return ExactMatchResult.MODEL_MISMATCH

    required_modifiers = set(_tokens(" ".join(request.model_modifiers))) & _MODIFIERS
    candidate_modifiers = set(offer.identity.modifiers) & _MODIFIERS
    if not required_modifiers.issubset(candidate_modifiers):
        return ExactMatchResult.MODEL_MISMATCH
    # A different named variant is a different product, not a soft preference.
    conflicting = candidate_modifiers - required_modifiers
    if conflicting and (required_modifiers or not generic_request):
        return ExactMatchResult.MODEL_MISMATCH

    if request.condition is not ProductCondition.ANY:
        candidate_condition = offer.condition if offer.condition is not ProductCondition.UNKNOWN else offer.identity.condition
        if candidate_condition is not ProductCondition.UNKNOWN and candidate_condition is not request.condition:
            return ExactMatchResult.REQUIRED_SPEC_MISMATCH

    missing_required = False
    required_specs = dict(request.required_specs or {})
    for key, required in required_specs.items():
        candidate = _candidate_spec(offer, str(key))
        if candidate in (None, "", ProductCondition.UNKNOWN, [], (), {}):
            missing_required = True
            continue
        if _different(required, candidate, str(key)):
            return ExactMatchResult.REQUIRED_SPEC_MISMATCH

    if generic_request:
        # A category request is exact when all explicit hard requirements were
        # observed. Missing facts stay manual-only rather than becoming trash.
        if missing_required:
            return ExactMatchResult.GENERIC_MATCH
        return ExactMatchResult.EXACT

    if missing_required or offer.identity.identity_confidence < 0.65:
        return ExactMatchResult.GENERIC_MATCH
    return ExactMatchResult.EXACT


def apply_exact_match(request: SearchRequestV2, offer: Offer) -> Offer:
    result = evaluate_exact_match(request, offer)
    confidence = {
        ExactMatchResult.EXACT: 0.98,
        ExactMatchResult.COMPATIBLE_VARIANT: 0.85,
        ExactMatchResult.GENERIC_MATCH: 0.5,
    }.get(result, 0.0)
    return replace(offer, exact_match=result, product_confidence=confidence)


def is_hard_mismatch(value: ExactMatchResult | str) -> bool:
    text = value.value if isinstance(value, ExactMatchResult) else str(value).upper()
    return text in {
        ExactMatchResult.MODEL_MISMATCH.value,
        ExactMatchResult.REQUIRED_SPEC_MISMATCH.value,
        ExactMatchResult.ACCESSORY.value,
    }


__all__ = ["apply_exact_match", "evaluate_exact_match", "is_hard_mismatch"]
