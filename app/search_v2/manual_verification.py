"""Audited manual verification for V2 offers."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from .models import Offer, VerificationState
from .verification import resolve_verification


_ALIASES = {
    "model": "model",
    "configuration": "model",
    "link": "link",
    "url": "link",
    "price": "price",
    "availability": "availability",
    "available": "availability",
    "seller": "seller",
}


def apply_manual_verification(
    offer: Offer,
    field: str,
    verified: bool = True,
    *,
    verified_by: str,
    verified_at: datetime | None = None,
    note: str = "",
    value: Any = None,
    seller_state: str | None = None,
    explicit_override: bool = False,
) -> Offer:
    canonical = _ALIASES.get(str(field or "").casefold())
    if canonical is None:
        raise ValueError(f"unsupported verification field: {field!r}")
    actor = str(verified_by or "").strip()
    if not actor:
        raise ValueError("verified_by is required")
    timestamp = verified_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    note = str(note or "").strip()
    if explicit_override and (canonical != "model" or not verified or not note):
        raise ValueError("explicit model override requires verified model and audit note")

    current = offer.manual_verification
    metadata = dict(current.metadata or {})
    confirmations = dict(metadata.get("confirmations") or {})
    history = list(metadata.get("history") or [])
    state = "VERIFIED" if verified else "NOT_VERIFIED"
    if canonical == "seller":
        state = str(seller_state or ("VERIFIED" if verified else "REQUIRES_CHECK")).upper()
        if state not in {"VERIFIED", "REQUIRES_CHECK"}:
            raise ValueError("seller_state must be VERIFIED or REQUIRES_CHECK")
        verified = state == "VERIFIED"
        metadata["seller_state"] = state
    event = {
        "field": canonical,
        "state": state,
        "verified": bool(verified),
        "verified_by": actor,
        "verified_at": timestamp.isoformat(),
        "note": note,
    }
    if value is not None:
        event["value"] = value
    if explicit_override:
        event["explicit_override"] = True
        metadata["explicit_model_override"] = True
    confirmations[canonical] = dict(event)
    history.append(dict(event))
    metadata["confirmations"] = confirmations
    metadata["history"] = history

    updates = {
        "verified_by": actor,
        "verified_at": timestamp,
        "note": note,
        "metadata": metadata,
        f"{canonical}_verified": bool(verified),
    }
    manual = replace(current, **updates)
    offer_updates: dict[str, Any] = {"manual_verification": manual}
    if value is not None and canonical == "price":
        offer_updates["price"] = float(value)
    elif value is not None and canonical == "link":
        offer_updates["url"] = str(value)
    updated = replace(offer, **offer_updates)
    return resolve_final_verification(updated)


def manual_checklist_complete(offer: Offer) -> bool:
    manual = offer.manual_verification
    seller_state = str((manual.metadata or {}).get("seller_state") or "UNSET").upper()
    required = all(
        getattr(manual, f"{field}_verified") is True
        for field in ("model", "link", "price", "availability")
    )
    return bool(required and seller_state in {"VERIFIED", "REQUIRES_CHECK"} and manual.verified_by and manual.verified_at)


def resolve_final_verification(offer: Offer) -> Offer:
    final = resolve_verification(
        offer.automatic_verification,
        offer.manual_verification,
        exact_match=offer.exact_match,
    )
    metadata = dict(final.metadata or {})
    metadata["final_manual_status"] = "APPROVED" if manual_checklist_complete(offer) and final.model_verified else "PENDING"
    final = replace(final, metadata=metadata)
    return replace(offer, final_verification=final)


__all__ = ["apply_manual_verification", "manual_checklist_complete", "resolve_final_verification"]
