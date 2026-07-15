"""Automatic and final verification resolution for normalized offers."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from typing import Any, Awaitable, Callable, Iterable, Mapping

from .exact_match import is_hard_mismatch
from .models import (
    AvailabilityStatus,
    ExactMatchResult,
    Offer,
    SellerTrust,
    VerificationAccess,
    VerificationState,
)


_FIELDS = ("model", "link", "price", "availability", "seller")


def _warning_fields(warning: str) -> set[str]:
    text = str(warning or "").replace("_", " ").casefold()
    if any(item in text for item in ("weak candidate", "need manual check", "нужна ручная проверка")):
        return set(_FIELDS)
    result: set[str] = set()
    if any(item in text for item in ("model mismatch", "required spec", "wrong product", "accessory", "модель")):
        result.add("model")
    if any(item in text for item in ("403", "429", "captcha", "browser", "link", "page", "ссылка", "страниц")):
        result.add("link")
    if any(item in text for item in ("price missing", "price unverified", "цена не подтверж", "цена не найд")):
        result.add("price")
    if any(item in text for item in ("unavailable", "out of stock", "налич", "снято")):
        result.add("availability")
    if any(item in text for item in ("seller unknown", "seller unverified", "продавец")):
        result.add("seller")
    return result


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def build_automatic_verification(offer: Offer) -> VerificationState:
    metadata = offer.raw_metadata if isinstance(offer.raw_metadata, dict) else {}
    if offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}:
        model_verified: bool | None = True
    elif is_hard_mismatch(offer.exact_match):
        model_verified = False
    else:
        model_verified = None
    link_verified = metadata.get("product_page_verified", metadata.get("link_verified"))
    if link_verified is not None:
        link_verified = bool(link_verified)
    price_verified = metadata.get("price_verified")
    if price_verified is None:
        price_verified = bool(offer.price and offer.price_confidence >= 0.75)
    availability_verified = metadata.get("availability_verified")
    if availability_verified is None:
        availability_verified = (
            offer.availability.status is not AvailabilityStatus.UNKNOWN
            and offer.availability.confidence >= 0.75
        )
    seller_verified = offer.seller.verified
    if seller_verified is None and offer.seller.trust is SellerTrust.HIGH:
        seller_verified = True

    warnings = list(metadata.get("verification_warnings") or [])
    errors = list(metadata.get("source_errors") or [])
    if offer.verification_access is VerificationAccess.BLOCKED:
        errors.append(str(metadata.get("source_error") or "automatic access blocked"))
    if model_verified is False:
        warnings.append(offer.exact_match.value)
    if not offer.price:
        warnings.append("PRICE_MISSING")
    if offer.availability.status is AvailabilityStatus.OUT_OF_STOCK:
        warnings.append("UNAVAILABLE")
    return VerificationState(
        model_verified=model_verified,
        link_verified=link_verified,
        price_verified=bool(price_verified),
        availability_verified=bool(availability_verified),
        seller_verified=seller_verified,
        access=offer.verification_access,
        warnings=_dedupe(warnings),
        errors=_dedupe(errors),
        metadata={"source": "automatic"},
    )


def _valid_explicit_override(manual: VerificationState) -> bool:
    metadata = manual.metadata if isinstance(manual.metadata, dict) else {}
    confirmation = metadata.get("confirmations", {}).get("model", {}) if isinstance(metadata.get("confirmations"), dict) else {}
    return bool(
        manual.model_verified
        and metadata.get("explicit_model_override")
        and confirmation.get("explicit_override")
        and confirmation.get("verified_by")
        and confirmation.get("verified_at")
        and confirmation.get("note")
    )


def resolve_verification(
    automatic: VerificationState,
    manual: VerificationState | None = None,
    *,
    exact_match: ExactMatchResult = ExactMatchResult.UNKNOWN,
) -> VerificationState:
    manual = manual or VerificationState()
    fields: dict[str, bool | None] = {}
    for field in _FIELDS:
        manual_value = getattr(manual, f"{field}_verified")
        fields[field] = manual_value if manual_value is not None else getattr(automatic, f"{field}_verified")
    if is_hard_mismatch(exact_match) and not _valid_explicit_override(manual):
        fields["model"] = False

    seller_state = str((manual.metadata or {}).get("seller_state") or "UNSET").upper()
    resolved_for_warning = {field: fields[field] is True for field in _FIELDS}
    if seller_state == "REQUIRES_CHECK":
        resolved_for_warning["seller"] = True
    kept: list[str] = []
    suppressed: list[str] = []
    for warning in automatic.warnings:
        warning_fields = _warning_fields(warning)
        if warning_fields and all(resolved_for_warning.get(field, False) for field in warning_fields):
            suppressed.append(warning)
        elif warning_fields:
            # Raw automatic codes stay in automatic history; final state uses field-safe wording.
            continue
        else:
            kept.append(warning)
    human = {
        "model": "Модель и комплектацию нужно подтвердить.",
        "link": "Ссылку на товар нужно подтвердить.",
        "price": "Цену нужно подтвердить.",
        "availability": "Наличие нужно подтвердить.",
        "seller": "Продавца нужно дополнительно проверить.",
    }
    for field in _FIELDS:
        if fields[field] is not True:
            kept.append(human[field])
    metadata = dict(manual.metadata or {})
    metadata.update({
        "source": "resolved",
        "seller_state": seller_state,
        "suppressed_automatic_warnings": _dedupe(suppressed),
        "automatic_errors": list(automatic.errors),
        "explicit_model_override_applied": bool(is_hard_mismatch(exact_match) and _valid_explicit_override(manual)),
    })
    return VerificationState(
        model_verified=fields["model"],
        link_verified=fields["link"],
        price_verified=fields["price"],
        availability_verified=fields["availability"],
        seller_verified=fields["seller"],
        access=automatic.access,
        verified_by=manual.verified_by,
        verified_at=manual.verified_at,
        note=manual.note,
        warnings=_dedupe(kept),
        errors=[],
        metadata=metadata,
    )


def apply_automatic_verification(offer: Offer) -> Offer:
    automatic = build_automatic_verification(offer)
    final = resolve_verification(automatic, offer.manual_verification, exact_match=offer.exact_match)
    return replace(offer, automatic_verification=automatic, final_verification=final)


async def verify_offer_pages(
    offers: Iterable[Offer],
    verifier: Callable[[Offer], Offer | VerificationState | Mapping[str, Any] | Awaitable[Any]],
    *,
    max_concurrency: int = 2,
    timeout: float | None = 10.0,
) -> list[Offer]:
    """Run an injected page verifier with a hard two-page concurrency ceiling."""
    ordered = list(offers)
    semaphore = asyncio.Semaphore(max(1, min(int(max_concurrency or 1), 2)))

    async def one(offer: Offer) -> Offer:
        async with semaphore:
            try:
                result = verifier(offer)
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=timeout) if timeout else await result
                if isinstance(result, Offer):
                    return result
                if isinstance(result, VerificationState):
                    automatic = result
                elif isinstance(result, Mapping):
                    allowed = {
                        key: result[key] for key in (
                            "model_verified", "link_verified", "price_verified", "availability_verified",
                            "seller_verified", "verified_by", "verified_at", "note", "warnings", "errors", "metadata",
                        ) if key in result
                    }
                    automatic = replace(offer.automatic_verification, **allowed)
                elif result is None:
                    return offer
                else:
                    raise TypeError("verifier must return Offer, VerificationState, mapping or None")
                final = resolve_verification(automatic, offer.manual_verification, exact_match=offer.exact_match)
                return replace(offer, automatic_verification=automatic, final_verification=final)
            except Exception as exc:
                automatic = replace(
                    offer.automatic_verification,
                    errors=_dedupe([*offer.automatic_verification.errors, f"{type(exc).__name__}: {exc}"]),
                )
                final = resolve_verification(automatic, offer.manual_verification, exact_match=offer.exact_match)
                return replace(offer, automatic_verification=automatic, final_verification=final)

    return list(await asyncio.gather(*(one(offer) for offer in ordered)))


__all__ = [
    "apply_automatic_verification", "build_automatic_verification", "resolve_verification",
    "verify_offer_pages",
]
