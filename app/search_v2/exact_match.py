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


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[0-9a-zа-я]+", str(value or "").replace("ё", "е").casefold())


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def _candidate_spec(offer: Offer, key: str) -> Any:
    identity = offer.identity
    if identity is None:
        return None
    aliases = {
        "storage": identity.storage,
        "storage_gb": identity.storage,
        "memory": identity.storage,
        "size": identity.size,
        "diagonal": identity.diagonal,
        "refresh_rate": identity.refresh_rate,
        "hz": identity.refresh_rate,
        "condition": identity.condition,
    }
    if key in aliases:
        return aliases[key]
    return identity.key_configuration.get(key, offer.facts.get(key))


def _different(required: Any, candidate: Any, key: str) -> bool:
    if candidate in (None, "", ProductCondition.UNKNOWN):
        return False
    if key == "condition":
        required_value = required.value if isinstance(required, ProductCondition) else str(required).casefold()
        candidate_value = candidate.value if isinstance(candidate, ProductCondition) else str(candidate).casefold()
        return required_value not in {"", "any"} and candidate_value != required_value
    required_number, candidate_number = _number(required), _number(candidate)
    if required_number is not None and candidate_number is not None:
        return abs(required_number - candidate_number) > 0.01
    return " ".join(_tokens(required)) != " ".join(_tokens(candidate))


def evaluate_exact_match(request: SearchRequestV2, offer: Offer) -> ExactMatchResult:
    title_tokens = _tokens(offer.title)
    title_text = " ".join(title_tokens)
    if any(marker in title_text for marker in _ACCESSORY_MARKERS):
        return ExactMatchResult.ACCESSORY
    if not offer.identity:
        return ExactMatchResult.UNKNOWN

    model_tokens = _tokens(request.canonical_model)
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
        if candidate in (None, "", ProductCondition.UNKNOWN):
            missing_required = True
            continue
        if _different(required, candidate, str(key)):
            return ExactMatchResult.REQUIRED_SPEC_MISMATCH

    if not model_tokens:
        # Category requests (TV, monitor, laptop, chair) can still be exact
        # when every explicit hard specification is present. A broad request
        # without structured requirements remains GENERIC_MATCH/manual-only.
        if required_specs and not missing_required:
            return ExactMatchResult.EXACT
        return ExactMatchResult.GENERIC_MATCH
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
