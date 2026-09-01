"""Configuration-safe product grouping."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Iterable

from .models import Offer, ProductCondition, ProductGroup, ProductIdentity


_MODEL_CODE_ALIASES = ("model_code", "mpn", "part_number")


# A price group is not a bag of every string an upstream source happened to
# expose.  Product pages frequently disagree about marketing text, delivery,
# colours, or extra fields.  Only configuration dimensions that can identify a
# different purchasable SKU belong in the grouping key.  This keeps an
# unrecognised optional field from fragmenting the market while still keeping
# known, conflicting configurations out of the same median.
_CATEGORY_CONFIGURATION_FIELDS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "phone": (
        ("ram_gb", ("ram_gb", "ram")),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
    "laptop": (
        ("cpu", ("cpu", "processor")),
        ("ram_gb", ("ram_gb", "ram")),
        ("ssd_gb", ("ssd_gb", "ssd")),
        ("gpu", ("gpu",)),
        ("resolution", ("resolution",)),
        ("os", ("os",)),
        ("keyboard_layout", ("keyboard_layout", "keyboard", "layout")),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
    "tv": (
        ("resolution", ("resolution",)),
        ("panel", ("panel", "matrix_type")),
        ("smart_platform", ("smart_platform",)),
        ("hdmi", ("hdmi",)),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
    "monitor": (
        ("resolution", ("resolution",)),
        ("panel", ("panel", "matrix_type")),
        ("response_time", ("response_time",)),
        ("adaptive_sync", ("adaptive_sync",)),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
    "coffee_machine": (
        ("machine_type", ("machine_type", "type")),
        ("cappuccinator", ("cappuccinator",)),
        ("pressure", ("pressure", "pressure_bar")),
        ("grinder", ("grinder",)),
        ("milk_system", ("milk_system",)),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
    "headphones": (
        ("connection", ("connection", "headphone_type")),
        ("anc", ("anc",)),
        ("form_factor", ("form_factor",)),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
    "robot_vacuum": (
        ("navigation", ("navigation", "lidar")),
        ("wet_cleaning", ("wet_cleaning",)),
        ("self_empty_station", ("self_empty_station",)),
        ("suction", ("suction",)),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
    "vacuum": (
        ("type", ("type", "vacuum_type")),
        ("wet_cleaning", ("wet_cleaning",)),
        ("power", ("power", "power_w")),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
    "microwave": (
        ("volume", ("volume", "volume_l")),
        ("power", ("power", "power_w")),
        ("grill", ("grill",)),
        ("inverter", ("inverter",)),
        ("model_code", _MODEL_CODE_ALIASES),
    ),
}
_COMMON_CONFIGURATION_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = ()
_MISSING_VALUES = {
    "", "-", "n a", "na", "none", "null", "unknown", "неизвестно",
    "не указано", "нет данных", "unknown value",
}


def _configuration(identity: ProductIdentity) -> dict[str, Any]:
    return {
        str(key).casefold(): value
        for key, value in (identity.key_configuration or {}).items()
        if value not in (None, "") and str(key).casefold() not in {"color", "colour", "fact_evidence"}
    }


def _first_configuration_value(configuration: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    return next(
        (configuration[key] for key in aliases if configuration.get(key) not in (None, "")),
        None,
    )


def _number_with_units(value: Any, units: tuple[str, ...], *, multiplier: float = 1.0) -> str:
    """Return a stable numeric config value without making a guess from text."""
    if isinstance(value, bool) or value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).casefold().replace(",", ".")
        unit_pattern = "|".join(re.escape(unit) for unit in sorted(units, key=len, reverse=True))
        match = re.search(rf"(?<![0-9a-zа-я])(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>{unit_pattern})?(?![a-zа-я])", text)
        if not match:
            return ""
        number = float(match.group("number"))
        unit = (match.group("unit") or "").casefold()
        if unit in {"тб", "tb", "tib"}:
            number *= 1024
        elif unit in {"квт", "kw"}:
            number *= 1000
        elif unit in {"мпа", "mpa"}:
            number *= 10
        elif unit in {"кпа", "kpa"}:
            number *= 0.01
        elif unit in {"мл", "ml"}:
            number *= 0.001
        elif unit in {"см", "cm"}:
            number /= 2.54
    number *= multiplier
    return f"{number:g}"


def _configuration_value(field: str, value: Any) -> str:
    text = _scalar(value)
    if not text or text in _MISSING_VALUES:
        return ""
    if field in {"ram_gb", "ssd_gb", "storage"}:
        return _number_with_units(value, ("гб", "gb", "gib", "тб", "tb", "tib"))
    if field in {"refresh_rate", "response_time"}:
        return _number_with_units(value, ("гц", "hz", "мс", "ms"))
    if field in {"diagonal", "screen"}:
        return _number_with_units(value, ('"', "″", "дюйм", "дюйма", "дюймов", "inch", "in", "см", "cm"))
    if field == "pressure":
        return _number_with_units(value, ("бар", "bar", "кпа", "kpa", "мпа", "mpa"))
    if field == "volume":
        return _number_with_units(value, ("л", "l", "литр", "литра", "литров", "мл", "ml"))
    if field == "power":
        return _number_with_units(value, ("вт", "w", "квт", "kw"))
    if field == "resolution":
        compact = re.sub(r"\s+", "", text).replace("х", "x")
        if text in {"4k", "4к", "uhd", "ultra hd"} or compact == "3840x2160":
            return "4k"
        if text in {"full hd", "fhd", "1080p"} or compact == "1920x1080":
            return "full_hd"
        if text in {"qhd", "wqhd", "2k", "1440p"} or compact == "2560x1440":
            return "qhd"
    if field == "machine_type":
        return {
            "автомат": "automatic", "автоматическая": "automatic", "автоматический": "automatic",
            "automatic": "automatic", "fully automatic": "automatic",
            "рожковая": "espresso", "рожковый": "espresso", "espresso": "espresso",
            "капсульная": "capsule", "капсульный": "capsule", "capsule": "capsule",
        }.get(text, text)
    if field in {"cappuccinator", "anc", "grill", "grinder", "inverter", "lidar", "wet_cleaning", "self_empty_station"}:
        if value is True or text in {"1", "true", "yes", "да", "есть", "automatic", "капучинатор"}:
            return "true"
        if value is False or text in {"0", "false", "no", "нет", "без", "отсутствует"}:
            return "false"
    if field == "model_code":
        return re.sub(r"[^0-9a-zа-я]+", "", text)
    return text


def _schema_configuration(identity: ProductIdentity) -> dict[str, str]:
    """Keep only known SKU dimensions, normalizing aliases before grouping."""
    configuration = _configuration(identity)
    category = _scalar(identity.category)
    # A retailer-local SKU can change per seller even for the same product.
    # Use a code only when it is an actual model/part number supplied by a
    # source that labels it as such.  Plain ``sku`` and ``vendor_code`` are
    # deliberately excluded from normal market grouping.
    fields = _CATEGORY_CONFIGURATION_FIELDS.get(category, _COMMON_CONFIGURATION_FIELDS)
    result: dict[str, str] = {}
    for field, aliases in fields:
        value = _configuration_value(field, _first_configuration_value(configuration, aliases))
        if value:
            result[field] = value
    return result


def _scalar(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip().casefold()


def canonical_identity_key(identity: ProductIdentity) -> str:
    condition = identity.condition.value if isinstance(identity.condition, ProductCondition) else str(identity.condition)
    payload = {
        "category": _scalar(identity.category),
        "brand": _scalar(identity.brand),
        "model": _scalar(identity.canonical_model),
        "modifiers": sorted({_scalar(item) for item in identity.modifiers if _scalar(item)}),
        "storage": _configuration_value("storage", identity.storage),
        "size": _scalar(identity.size),
        "diagonal": _configuration_value("diagonal", identity.diagonal),
        "refresh_rate": _configuration_value("refresh_rate", identity.refresh_rate),
        "key_configuration": _schema_configuration(identity),
        "condition": condition.casefold(),
        "region_or_sim_variant": _scalar(identity.region_or_sim_variant),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def group_offers(offers: Iterable[Offer]) -> list[ProductGroup]:
    grouped: dict[str, list[Offer]] = {}
    identities: dict[str, ProductIdentity] = {}
    for offer in offers:
        if offer.identity is None:
            fallback = ProductIdentity(canonical_model=offer.title, condition=offer.condition, identity_confidence=0.2)
            key = canonical_identity_key(fallback)
            identity = replace(fallback, canonical_key=key)
            offer = replace(offer, identity=identity)
        else:
            key = canonical_identity_key(offer.identity)
            identity = replace(offer.identity, canonical_key=key)
            offer = replace(offer, identity=identity)
        grouped.setdefault(key, []).append(offer)
        identities.setdefault(key, identity)
    result: list[ProductGroup] = []
    for key, items in grouped.items():
        group_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        result.append(ProductGroup(group_id=group_id, canonical_key=key, identity=identities[key], offers=items))
    result.sort(key=lambda group: (-len(group.offers), group.canonical_key))
    return result


__all__ = ["canonical_identity_key", "group_offers"]
