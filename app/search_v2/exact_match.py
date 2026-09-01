"""Hard exact-match gate executed before ranking or recommendations."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .models import ExactMatchResult, Offer, ProductCondition, SearchRequestV2


_ACCESSORY_MARKERS = (
    "чехол", "case", "стекло", "кабель", "заряд", "ремешок", "держатель",
    "адаптер", "кронштейн", "наушники для", "клавиатура для", "запчаст",
)
_MODIFIERS = {"pro", "max", "ultra", "plus", "mini", "air", "se"}
_PHONE_VARIANT_KEYS = {"sim_variant", "region", "region_or_sim_variant"}
_CAPACITY_KEYS = {"storage", "storage_gb", "memory", "ram", "ram_gb", "ssd", "ssd_gb"}
_NUMERIC_KEYS = {
    *_CAPACITY_KEYS,
    "refresh_rate", "hz", "diagonal", "screen", "pressure", "pressure_bar",
    "volume", "volume_l", "power", "power_w",
}
_BOOLEAN_KEYS = {
    "automatic", "anc", "cappuccinator", "grill", "grinder", "headrest",
    "inverter", "lidar", "lift_mechanism", "lumbar_support", "mapping",
    "mattress_included", "self_empty_station", "wet_cleaning",
}
_CODE_KEYS = {
    "sku", "model_code", "mpn", "part_number", "article", "article_number",
    "vendor_code", "product_id",
}
_MISSING_FACT_MARKERS = {
    "", "-", "n a", "na", "none", "null", "unknown", "неизвестно",
    "не указано", "нет данных", "неизвестен", "unknown value",
}
_SPEC_ALIASES = {
    "storage": ("storage", "storage_gb", "memory"),
    "storage_gb": ("storage_gb", "storage", "memory"),
    "memory": ("memory", "storage", "storage_gb"),
    "ram": ("ram", "ram_gb"),
    "ram_gb": ("ram_gb", "ram"),
    "ssd": ("ssd", "ssd_gb"),
    "ssd_gb": ("ssd_gb", "ssd"),
    "diagonal": ("diagonal", "screen"),
    "screen": ("screen", "diagonal"),
    "refresh_rate": ("refresh_rate", "hz"),
    "hz": ("hz", "refresh_rate"),
    "volume": ("volume", "volume_l"),
    "volume_l": ("volume_l", "volume"),
    "pressure": ("pressure", "pressure_bar"),
    "pressure_bar": ("pressure_bar", "pressure"),
    "power": ("power", "power_w"),
    "power_w": ("power_w", "power"),
    "sim_variant": ("sim_variant", "region", "region_or_sim_variant"),
    "region": ("region", "region_or_sim_variant", "sim_variant"),
    "region_or_sim_variant": ("region_or_sim_variant", "sim_variant", "region"),
    "sku": ("sku", "model_code", "mpn", "part_number", "article", "article_number", "vendor_code"),
    "model_code": ("model_code", "mpn", "part_number", "sku", "vendor_code"),
    "mpn": ("mpn", "part_number", "model_code", "sku", "vendor_code"),
    "part_number": ("part_number", "mpn", "model_code", "sku", "vendor_code"),
    "article": ("article", "article_number", "sku", "vendor_code"),
    "article_number": ("article_number", "article", "sku", "vendor_code"),
    "vendor_code": ("vendor_code", "sku", "model_code", "mpn", "part_number"),
    "product_id": ("product_id",),
}
_MODEL_CONTEXT_TOKENS = {
    "товар", "ноутбук", "ноут", "laptop", "ультрабук", "смартфон",
    "телефон", "phone", "кофемашина", "кофеварка", "телевизор", "tv",
    "монитор", "наушники", "кресло", "пылесос", "матрас", "кровать",
}


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[0-9a-zа-я]+", str(value or "").replace("ё", "е").casefold())


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def _value_tokens(value: Any) -> str:
    """Return a stable, punctuation-insensitive human value representation."""
    return " ".join(_tokens(value))


def _is_missing_value(value: Any) -> bool:
    if value is None or value is ProductCondition.UNKNOWN:
        return True
    return _value_tokens(value) in _MISSING_FACT_MARKERS


def _number_with_unit(value: Any, key: str) -> tuple[str, float, bool] | None:
    """Parse only a value with a known unit for the named hard specification.

    A bare number is accepted only when the entire value is numeric.  This
    prevents a model number such as ``M4`` from being mistaken for a RAM or
    power value.  The boolean means that a non-base unit was converted, which
    matters for the small display-size rounding allowance below.
    """
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        raw_number = float(value)
        text = ""
    else:
        raw_number = None
        text = str(value).casefold().replace("ё", "е")

    if key in _CAPACITY_KEYS:
        dimension, units = "capacity", {
            "гб": 1.0, "gb": 1.0, "gib": 1.0, "тб": 1024.0, "tb": 1024.0, "tib": 1024.0,
        }
    elif key in {"refresh_rate", "hz"}:
        dimension, units = "frequency", {"гц": 1.0, "hz": 1.0, "кгц": 1000.0, "khz": 1000.0}
    elif key in {"diagonal", "screen"}:
        dimension, units = "diagonal", {
            "\"": 1.0, "″": 1.0, "in": 1.0, "inch": 1.0, "дюйм": 1.0, "дюйма": 1.0,
            "дюймов": 1.0, "см": 1 / 2.54, "cm": 1 / 2.54,
        }
    elif key in {"pressure", "pressure_bar"}:
        dimension, units = "pressure", {"бар": 1.0, "bar": 1.0, "кпа": 0.01, "kpa": 0.01, "мпа": 10.0, "mpa": 10.0}
    elif key in {"volume", "volume_l"}:
        dimension, units = "volume", {
            "л": 1.0, "l": 1.0, "литр": 1.0, "литра": 1.0, "литров": 1.0,
            "мл": 0.001, "ml": 0.001,
        }
    elif key in {"power", "power_w"}:
        dimension, units = "power", {"вт": 1.0, "w": 1.0, "квт": 1000.0, "kw": 1000.0}
    else:
        return None

    if raw_number is not None:
        return dimension, raw_number, False
    unit_pattern = "|".join(sorted((re.escape(unit) for unit in units), key=len, reverse=True))
    match = re.search(
        rf"(?<![0-9a-zа-я])(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>{unit_pattern})(?![a-zа-я])",
        text,
        re.IGNORECASE,
    )
    if match:
        unit = match.group("unit").casefold()
        multiplier = units[unit]
        return dimension, float(match.group("number").replace(",", ".")) * multiplier, multiplier != 1.0
    if re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", text):
        return dimension, float(text.strip().replace(",", ".")), False
    return None


def _boolean_value(value: Any, key: str) -> bool | None:
    """Return an explicit fact value, never guessing from an arbitrary title."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    normalized = _value_tokens(value)
    if normalized in {"0", "false", "no", "нет", "без", "отсутствует", "не поддерживается"} or normalized.startswith("без "):
        return False
    if normalized in {"1", "true", "yes", "да", "есть", "поддерживается", "в наличии"}:
        return True
    if key == "automatic" and normalized in {"automatic", "автомат", "автоматическая", "автоматический"}:
        return True
    if key == "cappuccinator" and normalized in {"капучинатор", "milk system", "молочная система"}:
        return True
    return None


def _canonical_resolution(value: Any) -> str:
    normalized = _value_tokens(value)
    compact = re.sub(r"\s+", "", normalized).replace("х", "x")
    if normalized in {"4k", "4к", "uhd", "ultra hd"} or compact == "3840x2160":
        return "4k"
    if normalized in {"full hd", "fhd", "1080p"} or compact == "1920x1080":
        return "full_hd"
    if normalized in {"qhd", "wqhd", "2k", "1440p"} or compact == "2560x1440":
        return "qhd"
    return normalized


def _canonical_enum(value: Any, key: str) -> str:
    """Normalize only explicit, category-safe enum spellings."""
    normalized = _value_tokens(value)
    aliases = {
        "machine_type": {
            "автомат": "automatic", "автоматическая": "automatic", "автоматический": "automatic",
            "automatic": "automatic", "fully automatic": "automatic",
            "рожковая": "espresso", "рожковый": "espresso", "espresso": "espresso",
            "капсульная": "capsule", "капсульный": "capsule", "capsule": "capsule",
        },
        "connection": {
            "беспроводные": "wireless", "беспроводной": "wireless", "wireless": "wireless", "bluetooth": "wireless",
            "проводные": "wired", "проводной": "wired", "wired": "wired",
        },
        "smart_platform": {
            "google tv": "google_tv", "googletv": "google_tv", "android tv": "android_tv",
            "androidtv": "android_tv", "webos": "webos", "web os": "webos", "tizen": "tizen",
            "yaos": "yaos", "яндекс тв": "yaos", "салют тв": "salute_tv",
        },
        "panel": {
            "oled": "oled", "qled": "qled", "mini led": "mini_led", "miniled": "mini_led",
            "ips": "ips", "va": "va", "tn": "tn",
        },
        "form_factor": {
            "полноразмерные": "over_ear", "полноразмерный": "over_ear", "over ear": "over_ear",
            "накладные": "on_ear", "накладной": "on_ear", "on ear": "on_ear",
            "внутриканальные": "in_ear", "внутриканальный": "in_ear", "in ear": "in_ear",
            "вкладыши": "earbuds", "earbuds": "earbuds",
        },
        "color": {
            "black": "black", "черный": "black", "белый": "white", "white": "white",
            "blue": "blue", "синий": "blue", "green": "green", "зеленый": "green",
            "red": "red", "красный": "red",
        },
    }
    return aliases.get(key, {}).get(normalized, normalized)


def _canonical_code(value: Any) -> str:
    """Formatting-insensitive, but otherwise exact SKU/model-code comparison."""
    if isinstance(value, bool):
        return ""
    return re.sub(r"[^0-9a-zа-я]+", "", str(value or "").casefold())


def _has_specific_model_identifier(value: Any) -> bool:
    """Return whether a request names a concrete model rather than a family."""
    return any(any(character.isdigit() for character in token) for token in _tokens(value))


def _model_tokens(value: Any) -> list[str]:
    """Drop product-type words before comparing a concrete model name."""
    return [token for token in _tokens(value) if token not in _MODEL_CONTEXT_TOKENS]


def _candidate_spec(offer: Offer, key: str) -> Any:
    identity = offer.identity
    if identity is None:
        return None
    normalized_key = str(key).casefold()
    aliases = {
        "storage": identity.storage,
        "storage_gb": identity.storage,
        "memory": identity.storage,
        "size": identity.size,
        "diagonal": identity.diagonal,
        "refresh_rate": identity.refresh_rate,
        "hz": identity.refresh_rate,
        "condition": identity.condition,
        "sim_variant": identity.region_or_sim_variant,
        "region": identity.region_or_sim_variant,
        "region_or_sim_variant": identity.region_or_sim_variant,
        "product_id": offer.product_id,
    }
    for candidate_key in _SPEC_ALIASES.get(normalized_key, (normalized_key,)):
        # ``ram``/``ssd`` are human-readable source labels.  They may stand in
        # for their numeric aliases only when the title itself proved the
        # value; otherwise a stale snippet/opaque metadata field must not turn
        # an unconfigured laptop into an exact configuration.
        if (
            normalized_key in {"ram_gb", "ssd_gb"}
            and candidate_key != normalized_key
            and not _is_title_confirmed_fact(offer, candidate_key)
        ):
            continue
        value = aliases.get(candidate_key)
        if not _is_missing_value(value):
            return value
        value = identity.key_configuration.get(candidate_key, offer.facts.get(candidate_key))
        if not _is_missing_value(value):
            return value
    return None


def _is_title_confirmed_fact(offer: Offer, key: str) -> bool:
    evidence = offer.facts.get("fact_evidence") if isinstance(offer.facts, dict) else None
    item = evidence.get(key) if isinstance(evidence, dict) else None
    return isinstance(item, dict) and item.get("confidence") == "high" and item.get("evidence") == "title"


def _phone_variant_kinds(value: Any) -> set[str]:
    text = " ".join(_tokens(value))
    kinds: set[str] = set()
    if "esim" in text or "е sim" in text:
        kinds.add("esim")
    if re.search(r"(?:dual|две|2)\s*(?:sim|сим)", text):
        kinds.add("dual_sim")
    if re.search(r"(?:^|\s)(?:ru|eac|рст|ростест)(?:$|\s)", text):
        kinds.add("ru")
    if "global" in text or "глобальн" in text:
        kinds.add("global")
    return kinds


def _is_unconfirmed_candidate(required: Any, candidate: Any, key: str) -> bool:
    """Keep absent or unparseable hard facts out of exact recommendations."""
    if _is_missing_value(candidate):
        return True
    normalized_key = str(key).casefold()
    if normalized_key in _BOOLEAN_KEYS:
        return _boolean_value(candidate, normalized_key) is None
    if normalized_key in _NUMERIC_KEYS:
        return _number_with_unit(candidate, normalized_key) is None
    if normalized_key in _PHONE_VARIANT_KEYS and _phone_variant_kinds(required):
        return not _phone_variant_kinds(candidate)
    return False


def _different(required: Any, candidate: Any, key: str) -> bool:
    if _is_missing_value(candidate):
        return False
    normalized_key = str(key).casefold()
    if normalized_key == "condition":
        required_value = required.value if isinstance(required, ProductCondition) else str(required).casefold()
        candidate_value = candidate.value if isinstance(candidate, ProductCondition) else str(candidate).casefold()
        return required_value not in {"", "any"} and candidate_value != required_value
    if normalized_key in _PHONE_VARIANT_KEYS:
        required_kinds = _phone_variant_kinds(required)
        candidate_kinds = _phone_variant_kinds(candidate)
        if required_kinds and candidate_kinds:
            return not required_kinds.issubset(candidate_kinds)
    if normalized_key in _CODE_KEYS:
        return _canonical_code(required) != _canonical_code(candidate)
    if normalized_key in _BOOLEAN_KEYS:
        required_value = _boolean_value(required, normalized_key)
        candidate_value = _boolean_value(candidate, normalized_key)
        if required_value is not None and candidate_value is not None:
            return required_value != candidate_value
    if normalized_key in _NUMERIC_KEYS:
        required_number = _number_with_unit(required, normalized_key)
        candidate_number = _number_with_unit(candidate, normalized_key)
        if required_number is not None and candidate_number is not None:
            required_dimension, required_value, required_converted = required_number
            candidate_dimension, candidate_value, candidate_converted = candidate_number
            if required_dimension != candidate_dimension:
                return True
            tolerance = 0.15 if required_dimension == "diagonal" and (required_converted or candidate_converted) else 0.01
            return abs(required_value - candidate_value) > tolerance
    if normalized_key == "resolution":
        return _canonical_resolution(required) != _canonical_resolution(candidate)
    enum_key = {
        "matrix_type": "panel",
        "headphone_type": "connection",
    }.get(normalized_key, normalized_key)
    return _canonical_enum(required, enum_key) != _canonical_enum(candidate, enum_key)


def evaluate_exact_match(request: SearchRequestV2, offer: Offer) -> ExactMatchResult:
    title_tokens = _tokens(offer.title)
    title_text = " ".join(title_tokens)
    if any(marker in title_text for marker in _ACCESSORY_MARKERS):
        return ExactMatchResult.ACCESSORY
    if request.category == "headphones":
        headphone_markers = {"наушники", "наушник", "гарнитура", "headphones", "headset", "earbuds"}
        wrong_type_markers = {"клавиатура", "keyboard", "keycap"}
        if not headphone_markers.intersection(title_tokens) and wrong_type_markers.intersection(title_tokens):
            return ExactMatchResult.REQUIRED_SPEC_MISMATCH
    if not offer.identity:
        return ExactMatchResult.UNKNOWN

    model_tokens = _model_tokens(request.canonical_model)
    if model_tokens and not all(token in title_tokens for token in model_tokens):
        return ExactMatchResult.MODEL_MISMATCH

    required_modifiers = set(_tokens(" ".join(request.model_modifiers))) & _MODIFIERS
    candidate_modifiers = set(offer.identity.modifiers) & _MODIFIERS
    if not required_modifiers.issubset(candidate_modifiers):
        return ExactMatchResult.MODEL_MISMATCH
    # A different named variant is a different product, not a soft preference.
    conflicting = candidate_modifiers - required_modifiers
    if conflicting and (required_modifiers or model_tokens):
        return ExactMatchResult.MODEL_MISMATCH

    if request.condition is not ProductCondition.ANY:
        candidate_condition = offer.condition if offer.condition is not ProductCondition.UNKNOWN else offer.identity.condition
        if candidate_condition is not ProductCondition.UNKNOWN and candidate_condition is not request.condition:
            return ExactMatchResult.REQUIRED_SPEC_MISMATCH

    missing_required = False
    required_specs = dict(request.required_specs or {})
    for key, required in required_specs.items():
        candidate = _candidate_spec(offer, str(key))
        if _is_unconfirmed_candidate(required, candidate, str(key)):
            missing_required = True
            continue
        if _different(required, candidate, str(key)):
            return ExactMatchResult.REQUIRED_SPEC_MISMATCH

    if model_tokens and not _has_specific_model_identifier(request.canonical_model):
        # Names such as "DeLonghi Magnifica" identify a product family, not one
        # comparable SKU.  Different ECAM models must remain separate until the
        # user names a model code or a manual check confirms equivalence.
        return ExactMatchResult.GENERIC_MATCH

    if not model_tokens:
        # Category requests (TV, monitor, laptop, chair) can still be exact
        # when every explicit hard specification is present. A broad request
        # without structured requirements remains GENERIC_MATCH/manual-only.
        if required_specs and not missing_required:
            return ExactMatchResult.EXACT
        return ExactMatchResult.GENERIC_MATCH
    if missing_required or offer.identity.identity_confidence < 0.65:
        return ExactMatchResult.GENERIC_MATCH
    if request.category == "laptop" and not any(
        key in required_specs for key in ("storage", "storage_gb", "ram", "ram_gb", "ssd", "ssd_gb")
    ) and offer.identity.storage not in (None, ""):
        # A MacBook generation can ship with several memory configurations.
        # With no RAM/SSD chosen, it is a legitimate alternative, but it must
        # not be described as the one exact configuration requested.
        return ExactMatchResult.COMPATIBLE_VARIANT
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
