"""Safe bridge from Search V2 offers to the existing bounded page verifier.

The bridge does not add retries or bypass anti-bot controls. It only reuses the
legacy verifier's existing two-page ceiling, network policy and structured price
extraction, then maps the verified facts back into immutable V2 domain models.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Callable

from .exact_match import apply_exact_match
from .models import (
    AvailabilityInfo,
    AvailabilityStatus,
    ExactMatchResult,
    Offer,
    ProductCondition,
    RawOffer,
    SearchRequestV2,
    VerificationAccess,
)
from .normalization import build_product_identity, normalize_condition
from .verification import apply_automatic_verification


_SOURCE_MAP = {
    "yandex_market": "yandex_market_search",
    "ozon": "ozon_search",
    "avito": "avito_search",
    "wildberries": "wildberries",
    "dns": "dns_search",
    "citilink": "citilink_search",
    "mvideo": "mvideo_search",
    "generic_exact": "generic_web",
    "generic_search": "generic_web",
}


def _legacy_source(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return _SOURCE_MAP.get(text, text)


def _request_view(request: SearchRequestV2) -> SimpleNamespace:
    condition = request.condition
    used_allowed = condition in {ProductCondition.ANY, ProductCondition.USED}
    use_case = str(request.optional_specs.get("use_case") or "")
    product = " ".join(
        item for item in (
            request.brand,
            request.canonical_model,
            " ".join(request.model_modifiers),
        ) if item
    ).strip() or request.category
    return SimpleNamespace(
        budget=str(request.budget or ""),
        product_name=product,
        product=product,
        original_query=request.original_query,
        use_case=use_case,
        purpose=use_case,
        criteria="",
        important_criteria="",
        city=request.city,
        is_used_allowed=used_allowed,
    )


def _candidate_view(offer: Offer) -> SimpleNamespace:
    metadata = dict(offer.raw_metadata or {})
    snippet = str(
        metadata.get("snippet")
        or metadata.get("body")
        or metadata.get("description")
        or ""
    )
    source = _legacy_source(offer.source or offer.platform)
    return SimpleNamespace(
        url=offer.url,
        title=offer.title,
        snippet=snippet,
        price=int(offer.price) if offer.price else None,
        source=source,
        raw=metadata,
        seller=offer.seller.name,
        city=offer.city,
        risk_flags=[],
        price_source="",
        price_confidence="",
        price_evidence="",
        availability="",
        product_facts={},
        facts_json="",
        description=snippet,
        verify_status="",
        quality="",
        status="",
        score=0.0,
        used_proxy=False,
        used_browser=False,
        fetch_status_code=None,
        blocked_reason="",
        fetch_provider="",
        retry_count=0,
    )


def _availability(value: str, previous: AvailabilityInfo) -> AvailabilityInfo:
    status_text = str(value or "").upper()
    if status_text == "AVAILABLE":
        status, available, confidence = AvailabilityStatus.IN_STOCK, True, 0.95
    elif status_text in {"UNAVAILABLE", "REMOVED_LISTING"}:
        status, available, confidence = AvailabilityStatus.OUT_OF_STOCK, False, 0.95
    else:
        return previous
    return replace(
        previous,
        status=status,
        available=available,
        source_text=status_text,
        confidence=confidence,
    )


def _verification_access(verified: Any) -> VerificationAccess:
    status = str(getattr(verified, "verify_status", "") or "").upper()
    if status == "VERIFY_BLOCKED":
        return VerificationAccess.BLOCKED
    if bool(getattr(verified, "html_loaded", False)):
        return VerificationAccess.FULL
    if status in {"NEED_MANUAL_CHECK", "PRICE_MISSING"}:
        return VerificationAccess.PARTIAL
    return VerificationAccess.UNKNOWN


def _remap_verified_offer(
    offer: Offer,
    request: SearchRequestV2,
    candidate: SimpleNamespace,
    verified: Any,
) -> Offer:
    status = str(getattr(verified, "verify_status", "") or getattr(candidate, "verify_status", "") or "").upper()
    price = getattr(verified, "price", None)
    if price is None:
        price = getattr(candidate, "price", None)
    title = str(getattr(verified, "title", "") or getattr(candidate, "title", "") or offer.title)
    availability_text = str(
        getattr(verified, "availability", "")
        or getattr(candidate, "availability", "")
        or ""
    )
    metadata = dict(offer.raw_metadata or {})
    facts = dict(offer.facts or {})
    verified_facts = getattr(verified, "facts", None)
    if isinstance(verified_facts, dict):
        facts.update(verified_facts)
        metadata["structured_facts"] = {**dict(metadata.get("structured_facts") or {}), **verified_facts}

    access = _verification_access(verified)
    html_loaded = bool(getattr(verified, "html_loaded", False))
    metadata.update({
        "page_verification_status": status,
        "product_page_verified": bool(html_loaded and status != "NOT_PRODUCT_PAGE"),
        "link_verified": bool(html_loaded and status != "NOT_PRODUCT_PAGE"),
        "price_verified": bool(price is not None and html_loaded),
        "availability_verified": availability_text.upper() in {"AVAILABLE", "UNAVAILABLE", "REMOVED_LISTING"},
        "verification_access": access.value,
    })
    warnings = list(metadata.get("verification_warnings") or [])
    warnings.extend(str(item) for item in getattr(candidate, "risk_flags", []) or [] if str(item).strip())
    reason = str(getattr(verified, "reason", "") or "").strip()
    if reason:
        warnings.append(reason)
    metadata["verification_warnings"] = list(dict.fromkeys(warnings))
    if status in {"VERIFY_ERROR", "VERIFY_BLOCKED"} and reason:
        metadata["source_error"] = reason

    updated = replace(
        offer,
        title=title,
        price=float(price) if price is not None else offer.price,
        price_confidence=0.95 if price is not None and html_loaded else offer.price_confidence,
        availability=_availability(availability_text, offer.availability),
        availability_confidence=max(
            offer.availability_confidence,
            _availability(availability_text, offer.availability).confidence,
        ),
        verification_access=access,
        raw_metadata=metadata,
        facts=facts,
        condition=normalize_condition(getattr(candidate, "condition", offer.condition), title),
    )

    raw = RawOffer(
        source=updated.source,
        platform=updated.platform,
        title=updated.title,
        url=updated.url,
        product_id=updated.product_id,
        seller_name=updated.seller.name,
        price=updated.price,
        old_price=updated.old_price,
        currency=updated.currency,
        availability_text=availability_text,
        condition=updated.condition,
        city=updated.city,
        delivery=updated.delivery,
        image_url=updated.image_url,
        raw_metadata=metadata,
        retrieved_at=updated.retrieved_at,
    )
    updated = replace(updated, identity=build_product_identity(raw, request))
    updated = apply_exact_match(request, updated)
    if status in {"WRONG_PRODUCT", "NOT_PRODUCT_PAGE"}:
        updated = replace(updated, exact_match=ExactMatchResult.MODEL_MISMATCH, product_confidence=0.0)
    return apply_automatic_verification(updated)


async def verify_offer_page(
    offer: Offer,
    request: SearchRequestV2,
    *,
    verifier: Callable[[Any, Any], Any] | None = None,
) -> Offer:
    """Verify one product page through the existing safe network policy."""

    if verifier is None:
        from app.candidate_verifier import verify_candidate

        verifier = verify_candidate
    candidate = _candidate_view(offer)
    request_view = _request_view(request)
    verified = await asyncio.to_thread(verifier, candidate, request_view)
    return _remap_verified_offer(offer, request, candidate, verified)


__all__ = ["verify_offer_page"]
