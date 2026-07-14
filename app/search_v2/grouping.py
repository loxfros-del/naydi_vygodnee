"""Configuration-safe product grouping."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Iterable

from .models import Offer, ProductCondition, ProductGroup, ProductIdentity


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
        "storage": _scalar(identity.storage),
        "size": _scalar(identity.size),
        "diagonal": _scalar(identity.diagonal),
        "refresh_rate": _scalar(identity.refresh_rate),
        "key_configuration": {
            str(key).casefold(): _scalar(value)
            for key, value in sorted(identity.key_configuration.items())
            if value not in (None, "") and str(key).casefold() not in {"color", "colour"}
        },
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
