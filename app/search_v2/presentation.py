"""Client-safe presentation for V2 recommendations.

This module intentionally knows nothing about Telegram and never renders raw
adapter/network diagnostics. Administrator debug uses shadow/source snapshots.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Iterable


ROLE_LABELS = {
    "BEST_OVERALL": "⭐ Лучший выбор",
    "CHEAP_WITH_RISK": "💰 Дешевле, но с нюансами",
    "RELIABLE": "🛡 Надёжный вариант",
}

_TECHNICAL_MARKERS = (
    "403", "429", "498", "captcha", "капч", "adapter", "provider",
    "browser", "браузер", "blocked automatic", "source_timeout", "traceback",
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_text(value: Any) -> str:
    return str(value.value if isinstance(value, Enum) else value or "")


def _money(value: Any, currency: str = "RUB") -> str:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return "цена уточняется"
    suffix = "₽" if str(currency or "RUB").upper() in {"RUB", "RUR", "₽"} else str(currency)
    return f"{number:,}".replace(",", " ") + f" {suffix}"


def _safe_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if any(marker in text.casefold() for marker in _TECHNICAL_MARKERS):
        return ""
    return text


def _risk_texts(flags: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for flag in flags or []:
        if bool(_field(flag, "resolved_by_manual", False)):
            continue
        code = _enum_text(_field(flag, "code", ""))
        title = _safe_text(_field(flag, "title", ""))
        explanation = _safe_text(_field(flag, "explanation", ""))
        value = title or explanation or _safe_text(code.replace("_", " ").lower())
        if value and value not in result:
            result.append(value)
    return result


def _manual_stamp(offer: Any) -> str:
    manual = _field(offer, "manual_verification", {}) or {}
    required = ("model_verified", "link_verified", "price_verified", "availability_verified")
    if not all(bool(_field(manual, field, False)) for field in required):
        return ""
    raw = _field(manual, "verified_at", "")
    if isinstance(raw, datetime):
        return raw.astimezone().strftime("%d.%m.%Y %H:%M")
    return _safe_text(raw)


def format_client_recommendation(recommendation: Any) -> str:
    offer = _field(recommendation, "offer", None) or recommendation
    role = _enum_text(_field(recommendation, "role", "BEST_OVERALL")).upper()
    seller = _field(offer, "seller", {}) or {}
    seller_name = _safe_text(_field(seller, "name", _field(offer, "seller_name", "")))
    platform = _safe_text(_field(offer, "platform", _field(offer, "source", "")))
    title = _safe_text(_field(offer, "title", "")) or "Товар"
    lines = [ROLE_LABELS.get(role, ROLE_LABELS["BEST_OVERALL"]), "", title]
    lines.append(_money(_field(offer, "price", None), _field(offer, "currency", "RUB")))
    source_line = " · ".join(item for item in (platform, seller_name) if item)
    if source_line:
        lines.append(source_line)

    why = [_safe_text(item) for item in (_field(recommendation, "reasons", []) or [])]
    why = [item for item in why if item]
    if why:
        lines.extend(["", "Почему выбрали:"])
        lines.extend(f"• {item}" for item in why[:3])

    risks = _risk_texts(_field(recommendation, "risk_flags", _field(offer, "risk_flags", [])) or [])
    if risks:
        lines.extend(["", "Что проверить:"])
        lines.extend(f"• {item}" for item in risks[:3])

    stamp = _manual_stamp(offer)
    if stamp:
        lines.extend(["", f"Проверено специалистом: {stamp}"])
    return "\n".join(lines)


def format_client_recommendations(recommendations: Iterable[Any]) -> list[str]:
    """Render no more than three unique offers."""
    rendered: list[str] = []
    seen: set[str] = set()
    for recommendation in recommendations or []:
        offer = _field(recommendation, "offer", None) or recommendation
        identity = str(_field(offer, "offer_id", "") or _field(offer, "url", "") or _field(offer, "title", ""))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        rendered.append(format_client_recommendation(recommendation))
        if len(rendered) == 3:
            break
    return rendered


__all__ = ["format_client_recommendation", "format_client_recommendations"]
