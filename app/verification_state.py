"""Pure resolver for automatic, manual and client-facing verification state.

The module deliberately does not know about Telegram, SQLite or ORM objects.  It
accepts a ``facts_json`` mapping (or its JSON representation), preserves the
automatic evidence and returns JSON-serialisable dictionaries.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any, Mapping


MANUAL_FIELDS = ("model", "link", "price", "availability", "seller")
SELLER_VERIFIED = "VERIFIED"
SELLER_REQUIRES_CHECK = "REQUIRES_CHECK"
SELLER_UNSET = "UNSET"

_HARD_MODEL_MISMATCHES = {"MODEL_MISMATCH", "REQUIRED_SPEC_MISMATCH"}
_NON_OVERRIDABLE_EXACT = {"ACCESSORY"}
_NON_OVERRIDABLE_VERIFY = {
    "WRONG_PRODUCT",
    "UNAVAILABLE",
    "REMOVED_LISTING",
    "NOT_PRODUCT_PAGE",
    "REJECTED",
    "REJECTED_AUTO",
}
_AUTOMATIC_MODEL_OK = {"EXACT", "EXACT_MATCH", "MATCH", "CONFIRMED"}

_AUTOMATIC_LEGACY_KEYS = {
    "available",
    "availability",
    "availability_verified",
    "browser_used",
    "condition",
    "exact_match",
    "exact_match_status",
    "exact_product_verified",
    "fetch_diagnostics",
    "fetch_error",
    "fetch_status_code",
    "final_facts",
    "final_reasons",
    "link_verified",
    "model",
    "price",
    "price_verified",
    "product_page_verified",
    "proxy_used",
    "risk_flags",
    "seller",
    "seller_reason",
    "seller_trust",
    "seller_verified",
    "source_error",
    "source_status",
    "status",
    "structured_facts",
    "url",
    "verification_access",
    "verify_status",
    "warnings",
}

_FIELD_ALIASES = {
    "model": "model",
    "configuration": "model",
    "config": "model",
    "url": "link",
    "link": "link",
    "price": "price",
    "availability": "availability",
    "available": "availability",
    "seller": "seller",
}

_CLIENT_FIELD_WARNINGS = {
    "model": "Модель и комплектацию нужно подтвердить.",
    "link": "Ссылку на товар нужно подтвердить.",
    "price": "Цену нужно подтвердить.",
    "availability": "Наличие нужно подтвердить.",
    "seller": "Продавца нужно дополнительно проверить.",
}

_BLOCKER_WARNINGS = {
    "MODEL_MISMATCH": "Модель товара не соответствует запросу.",
    "REQUIRED_SPEC_MISMATCH": "Обязательная комплектация не соответствует запросу.",
    "ACCESSORY": "Предложение является аксессуаром, а не нужным товаром.",
    "WRONG_PRODUCT": "Предложение не соответствует запрошенному товару.",
    "UNAVAILABLE": "Предложение недоступно.",
    "REMOVED_LISTING": "Объявление снято с публикации.",
    "NOT_PRODUCT_PAGE": "Ссылка не ведёт на карточку нужного товара.",
    "REJECTED": "Предложение отклонено.",
    "REJECTED_AUTO": "Предложение отклонено.",
    "MANUAL_MODEL_REJECTED": "Администратор не подтвердил модель товара.",
}


def _facts_dict(facts_json: str | Mapping[str, Any] | None) -> dict[str, Any]:
    if facts_json is None or facts_json == "":
        return {}
    if isinstance(facts_json, str):
        try:
            value = json.loads(facts_json)
        except json.JSONDecodeError as exc:
            raise ValueError("facts_json must contain a JSON object") from exc
    elif isinstance(facts_json, Mapping):
        value = dict(facts_json)
    else:
        raise TypeError("facts_json must be a mapping, JSON object string or None")
    if not isinstance(value, dict):
        raise ValueError("facts_json must contain a JSON object")
    return copy.deepcopy(value)


def _dict(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                return decoded
    return [value]


def _deduplicate_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _first_present(containers: list[Mapping[str, Any]], keys: tuple[str, ...]) -> tuple[bool, Any]:
    for container in containers:
        for key in keys:
            if key in container:
                return True, container.get(key)
    return False, None


def _automatic_snapshot(facts: Mapping[str, Any]) -> dict[str, Any]:
    existing = facts.get("automatic_verification")
    if isinstance(existing, Mapping):
        automatic = _dict(existing)
    else:
        automatic = {
            key: copy.deepcopy(facts[key])
            for key in _AUTOMATIC_LEGACY_KEYS
            if key in facts
        }

    warning_values: list[Any] = []
    for key in ("warnings", "final_reasons", "risk_flags"):
        warning_values.extend(_list(automatic.get(key)))
    automatic["warnings"] = _deduplicate_strings(warning_values)
    return automatic


def _manual_snapshot(facts: Mapping[str, Any]) -> dict[str, Any]:
    manual = _dict(facts.get("manual_verification"))
    legacy = _dict(facts.get("manual_verified"))

    decisions = _dict(manual.get("decisions"))
    flag_aliases = {
        "model": ("manual_model_verified", "model"),
        "link": ("manual_link_verified", "link", "url"),
        "price": ("manual_price_verified", "price"),
        "availability": ("manual_availability_verified", "availability", "available"),
        "seller": ("manual_seller_verified", "seller"),
    }
    for field, aliases in flag_aliases.items():
        flag_name = f"manual_{field}_verified"
        present, value = _first_present([manual], aliases)
        if not present:
            present, value = _first_present([facts], (flag_name,))
        if not present:
            present, value = _first_present([legacy], aliases)
        manual[flag_name] = bool(value) if present else False
        # Legacy false flags were also used as defaults, so only a true flag is
        # a safe imported decision. Explicit rejections live in ``decisions``.
        if field not in decisions and present and bool(value):
            decisions[field] = "VERIFIED"

    state_present, state = _first_present([manual], ("manual_seller_state", "seller_state"))
    if not state_present:
        state_present, state = _first_present([facts], ("manual_seller_state",))
    if not state_present:
        state_present, state = _first_present([legacy], ("manual_seller_state", "seller_state"))
    seller_state = str(state or "").strip().upper() if state_present else ""
    if seller_state not in {SELLER_VERIFIED, SELLER_REQUIRES_CHECK}:
        seller_state = SELLER_VERIFIED if manual["manual_seller_verified"] else SELLER_UNSET
    manual["manual_seller_state"] = seller_state
    if seller_state == SELLER_REQUIRES_CHECK:
        manual["manual_seller_verified"] = False
        decisions["seller"] = SELLER_REQUIRES_CHECK
    elif seller_state == SELLER_VERIFIED:
        manual["manual_seller_verified"] = True
        decisions["seller"] = "VERIFIED"

    for key in ("manual_verified_by", "manual_verified_at", "manual_note"):
        present, value = _first_present([manual], (key, key.removeprefix("manual_")))
        if not present:
            present, value = _first_present([facts], (key,))
        manual[key] = str(value or "").strip() if present else ""

    override_present, override = _first_present([manual], ("manual_model_override", "model_override"))
    if not override_present:
        override_present, override = _first_present([facts], ("manual_model_override",))
    manual["manual_model_override"] = bool(override) if override_present else False
    manual["decisions"] = decisions
    manual["confirmations"] = _dict(manual.get("confirmations"))
    manual["history"] = [
        _dict(item) for item in _list(manual.get("history")) if isinstance(item, Mapping)
    ]
    return manual


def _normalised_sections(facts_json: str | Mapping[str, Any] | None) -> dict[str, Any]:
    facts = _facts_dict(facts_json)
    facts["automatic_verification"] = _automatic_snapshot(facts)
    facts["manual_verification"] = _manual_snapshot(facts)
    return facts


def _status(automatic: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(automatic.get(key) or "").strip().upper()
        if value:
            return value
    return ""


def _valid_model_override(manual: Mapping[str, Any]) -> bool:
    if not manual.get("manual_model_override") or not manual.get("manual_model_verified"):
        return False
    confirmation = _dict(_dict(manual.get("confirmations")).get("model"))
    return all(
        (
            bool(confirmation.get("explicit_override")),
            bool(str(confirmation.get("verified_by") or manual.get("manual_verified_by") or "").strip()),
            bool(str(confirmation.get("verified_at") or manual.get("manual_verified_at") or "").strip()),
            bool(str(confirmation.get("note") or manual.get("manual_note") or "").strip()),
        )
    )


def _automatic_field_states(automatic: Mapping[str, Any]) -> dict[str, bool]:
    exact = _status(automatic, "exact_match", "exact_match_status")
    model_ok = bool(automatic.get("exact_product_verified")) or exact in _AUTOMATIC_MODEL_OK
    return {
        "model": model_ok,
        "link": bool(
            automatic.get("product_page_verified")
            or automatic.get("link_verified")
            or automatic.get("url_verified")
        ),
        "price": bool(automatic.get("price_verified")),
        "availability": bool(automatic.get("availability_verified")),
        "seller": bool(automatic.get("seller_verified")),
    }


def _warning_fields(warning: str) -> set[str]:
    text = " ".join(warning.casefold().replace("_", " ").split())
    if any(token in text for token in (
        "weak candidate", "need manual check", "нужна ручная проверка",
        "нужно проверить детали", "needs manual check",
    )):
        return set(MANUAL_FIELDS)
    fields: set[str] = set()
    if any(token in text for token in (
        "model mismatch", "required spec mismatch", "wrong product", "accessory",
        "неверная модель", "не та модель", "модель не совпад", "комплектация не совпад",
    )):
        fields.add("model")
    if any(token in text for token in (
        "verify blocked", "not product page", "сайт не проверен", "ссылка не проверена",
        "страница не провер", "проверка страницы не удалась", "карточка заблокирована",
        "403", "429", "captcha", "капча", "browser blocked", "browser", "access blocked",
    )):
        fields.add("link")
    if any(token in text for token in (
        "price missing", "price unverified", "цена не подтверждена", "цена не найдена",
        "цену нужно подтвердить",
    )):
        fields.add("price")
    if any(token in text for token in (
        "unavailable", "removed listing", "нет в наличии", "наличие не подтверждено",
        "наличие неизвестно", "наличие нужно подтвердить",
    )):
        fields.add("availability")
    if any(token in text for token in (
        "seller unknown", "seller unverified", "неизвестный продавец",
        "продавец не подтверж", "продавца нужно проверить",
    )):
        fields.add("seller")
    return fields


def _automatic_warnings(automatic: Mapping[str, Any]) -> list[str]:
    warnings = list(_list(automatic.get("warnings")))
    exact = _status(automatic, "exact_match", "exact_match_status")
    verify = _status(automatic, "verify_status")
    status = _status(automatic, "status")
    if exact in _HARD_MODEL_MISMATCHES | _NON_OVERRIDABLE_EXACT:
        warnings.append(exact)
    if verify and verify not in {"VERIFIED", "VERIFIED_OK", "OK", "SUCCESS"}:
        warnings.append(verify)
    if status in {"WEAK_CANDIDATE", "REJECTED", "REJECTED_AUTO"}:
        warnings.append(status)
    return _deduplicate_strings(warnings)


def _final_values(facts: Mapping[str, Any]) -> dict[str, Any]:
    automatic = _dict(facts.get("automatic_verification"))
    manual = _dict(facts.get("manual_verification"))
    values: dict[str, Any] = {}
    for source in (
        facts,
        _dict(automatic.get("structured_facts")),
        _dict(automatic.get("final_facts")),
        automatic,
    ):
        for key in ("model", "url", "price", "availability", "seller"):
            if key in source and source.get(key) not in (None, ""):
                values[key] = copy.deepcopy(source.get(key))
    for field, confirmation_value in _dict(manual.get("confirmations")).items():
        confirmation = _dict(confirmation_value)
        if "value" not in confirmation:
            continue
        key = "url" if field == "link" else field
        values[key] = copy.deepcopy(confirmation.get("value"))
    return values


def _resolve_normalised(facts: Mapping[str, Any]) -> dict[str, Any]:
    automatic = _dict(facts.get("automatic_verification"))
    manual = _dict(facts.get("manual_verification"))
    exact = _status(automatic, "exact_match", "exact_match_status")
    verify = _status(automatic, "verify_status")
    raw_status = _status(automatic, "status")
    override_valid = _valid_model_override(manual)

    blockers: list[str] = []
    if exact in _HARD_MODEL_MISMATCHES and not override_valid:
        blockers.append(exact)
    if exact in _NON_OVERRIDABLE_EXACT:
        blockers.append(exact)
    if verify in _NON_OVERRIDABLE_VERIFY:
        blockers.append(verify)
    if raw_status in {"REJECTED", "REJECTED_AUTO"}:
        blockers.append(raw_status)
    if _dict(manual.get("decisions")).get("model") == "NOT_VERIFIED":
        blockers.append("MANUAL_MODEL_REJECTED")
    blockers = _deduplicate_strings(blockers)

    automatic_states = _automatic_field_states(automatic)
    decisions = _dict(manual.get("decisions"))
    final_states: dict[str, bool] = {}
    sources: dict[str, str] = {}
    for field in MANUAL_FIELDS:
        decision = str(decisions.get(field) or "UNSET").upper()
        if field == "seller" and manual.get("manual_seller_state") == SELLER_REQUIRES_CHECK:
            final_states[field] = False
            sources[field] = "manual_requires_check"
        elif decision == "VERIFIED":
            final_states[field] = bool(manual.get(f"manual_{field}_verified"))
            sources[field] = "manual"
        elif decision == "NOT_VERIFIED":
            final_states[field] = False
            sources[field] = "manual_rejected"
        else:
            final_states[field] = automatic_states[field]
            sources[field] = "automatic" if automatic_states[field] else "unverified"
    if exact in _HARD_MODEL_MISMATCHES:
        final_states["model"] = override_valid
        sources["model"] = "manual_override" if override_valid else "hard_mismatch"
    elif exact in _NON_OVERRIDABLE_EXACT:
        final_states["model"] = False
        sources["model"] = "hard_mismatch"

    seller_state = str(manual.get("manual_seller_state") or SELLER_UNSET).upper()
    if seller_state not in {SELLER_VERIFIED, SELLER_REQUIRES_CHECK}:
        seller_state = SELLER_VERIFIED if final_states["seller"] else SELLER_UNSET
    seller_decided = seller_state in {SELLER_VERIFIED, SELLER_REQUIRES_CHECK}

    manual_started = any(
        str(value or "").strip().upper() not in {"", "UNSET"}
        for value in decisions.values()
    )
    manual_required = manual_started and all(
        final_states[field] for field in ("model", "link", "price", "availability")
    ) and seller_decided
    if exact in _HARD_MODEL_MISMATCHES:
        manual_required = manual_required and override_valid
    if exact in _NON_OVERRIDABLE_EXACT:
        manual_required = False
    audit_complete = bool(
        str(manual.get("manual_verified_by") or "").strip()
        and str(manual.get("manual_verified_at") or "").strip()
    )
    manual_complete = manual_required and audit_complete and not blockers

    if blockers:
        final_manual_status = "BLOCKED"
        status = "BLOCKED"
    elif manual_complete:
        final_manual_status = "APPROVED"
        status = "APPROVED"
    elif manual_required:
        final_manual_status = "PENDING_AUDIT"
        status = "NEEDS_REVIEW"
    else:
        final_manual_status = "PENDING"
        status = "VERIFIED" if all(final_states.values()) else "NEEDS_REVIEW"

    suppression_states = dict(final_states)
    suppression_states["seller"] = seller_decided
    kept_warnings: list[str] = []
    suppressed_warnings: list[str] = []
    translated_warnings: list[str] = []
    for warning in _automatic_warnings(automatic):
        fields = _warning_fields(warning)
        if not fields:
            kept_warnings.append(warning)
        elif all(suppression_states.get(field, False) for field in fields):
            suppressed_warnings.append(warning)
        else:
            translated_warnings.append(warning)

    unresolved_fields = [field for field in MANUAL_FIELDS if not final_states[field]]
    if seller_state == SELLER_REQUIRES_CHECK and "seller" not in unresolved_fields:
        unresolved_fields.append("seller")
    client_warnings = list(kept_warnings)
    for field in unresolved_fields:
        client_warnings.append(_CLIENT_FIELD_WARNINGS[field])
    client_warnings.extend(_BLOCKER_WARNINGS[item] for item in blockers if item in _BLOCKER_WARNINGS)
    client_warnings = _deduplicate_strings(client_warnings)

    manually_confirmed = [
        field for field in MANUAL_FIELDS
        if decisions.get(field) == "VERIFIED" and bool(manual.get(f"manual_{field}_verified"))
    ]
    if override_valid and "model" not in manually_confirmed:
        manually_confirmed.append("model")

    return {
        "status": status,
        "final_manual_status": final_manual_status,
        "presentation_ready": status in {"APPROVED", "VERIFIED"},
        "specialist_verified": manual_complete,
        "model_verified": final_states["model"],
        "link_verified": final_states["link"],
        "price_verified": final_states["price"],
        "availability_verified": final_states["availability"],
        "seller_verified": seller_state == SELLER_VERIFIED,
        "seller_state": seller_state,
        "model_override_applied": override_valid and exact in _HARD_MODEL_MISMATCHES,
        "verification_source": sources,
        "confirmed_fields": [field for field in MANUAL_FIELDS if final_states[field]],
        "manually_confirmed_fields": manually_confirmed,
        "unresolved_fields": unresolved_fields,
        "blocking_reasons": blockers,
        "warnings": client_warnings,
        "suppressed_automatic_warnings": suppressed_warnings,
        "translated_automatic_warnings": translated_warnings,
        "checked_by": str(manual.get("manual_verified_by") or ""),
        "checked_at": str(manual.get("manual_verified_at") or ""),
        "manual_note": str(manual.get("manual_note") or ""),
        "final_facts": _final_values(facts),
    }


def _write_legacy_aliases(facts: dict[str, Any], final: Mapping[str, Any]) -> None:
    manual = _dict(facts.get("manual_verification"))
    facts["manual_verified"] = {
        "model": bool(manual.get("manual_model_verified")),
        "url": bool(manual.get("manual_link_verified")),
        "price": bool(manual.get("manual_price_verified")),
        "availability": bool(manual.get("manual_availability_verified")),
        "seller": bool(manual.get("manual_seller_verified")),
    }
    for field in MANUAL_FIELDS:
        facts[f"manual_{field}_verified"] = bool(manual.get(f"manual_{field}_verified"))
    facts["manual_seller_state"] = str(manual.get("manual_seller_state") or SELLER_UNSET)
    facts["manual_verified_by"] = str(manual.get("manual_verified_by") or "")
    facts["manual_verified_at"] = str(manual.get("manual_verified_at") or "")
    facts["manual_note"] = str(manual.get("manual_note") or "")
    facts["manual_model_override"] = bool(manual.get("manual_model_override"))
    facts["final_manual_status"] = str(final.get("final_manual_status") or "PENDING")
    facts["final_presentation_state"] = copy.deepcopy(dict(final))


def normalize_verification_facts(
    facts_json: str | Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a non-mutating, JSON-serialisable facts mapping with all three states."""
    facts = _normalised_sections(facts_json)
    final = _resolve_normalised(facts)
    _write_legacy_aliases(facts, final)
    return facts


def resolve_final_presentation(
    facts_json: str | Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve the client-safe final state without discarding automatic history."""
    facts = _normalised_sections(facts_json)
    return _resolve_normalised(facts)


def apply_manual_confirmation(
    facts_json: str | Mapping[str, Any] | None,
    field: str,
    verified: bool = True,
    *,
    verified_by: str,
    verified_at: str | datetime | None = None,
    note: str = "",
    value: Any = None,
    seller_state: str | None = None,
    explicit_override: bool = False,
) -> dict[str, Any]:
    """Apply one audited checklist decision and recalculate final presentation.

    ``explicit_override`` is accepted only for a verified model and requires a
    non-empty audit note.  A regular model confirmation never overrides an
    automatic hard mismatch.
    """
    canonical_field = _FIELD_ALIASES.get(str(field or "").strip().casefold())
    if canonical_field is None:
        raise ValueError(f"unsupported manual verification field: {field!r}")
    actor = str(verified_by or "").strip()
    if not actor:
        raise ValueError("verified_by is required")
    note_text = str(note or "").strip()
    if explicit_override:
        if canonical_field != "model" or not verified:
            raise ValueError("explicit_override is allowed only for a verified model")
        if not note_text:
            raise ValueError("explicit model override requires an audit note")

    if isinstance(verified_at, datetime):
        timestamp_value = verified_at
        if timestamp_value.tzinfo is None:
            timestamp_value = timestamp_value.replace(tzinfo=timezone.utc)
        timestamp = timestamp_value.isoformat(timespec="seconds")
    elif verified_at is None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        timestamp = str(verified_at).strip()
        if not timestamp:
            raise ValueError("verified_at must not be empty")

    facts = _normalised_sections(facts_json)
    manual = _dict(facts.get("manual_verification"))
    decisions = _dict(manual.get("decisions"))

    if canonical_field == "seller":
        normalised_seller_state = str(seller_state or "").strip().upper()
        if not normalised_seller_state:
            normalised_seller_state = SELLER_VERIFIED if verified else SELLER_REQUIRES_CHECK
        if normalised_seller_state not in {SELLER_VERIFIED, SELLER_REQUIRES_CHECK}:
            raise ValueError("seller_state must be VERIFIED or REQUIRES_CHECK")
        flag_value = normalised_seller_state == SELLER_VERIFIED
        manual["manual_seller_state"] = normalised_seller_state
        decisions[canonical_field] = normalised_seller_state
    else:
        flag_value = bool(verified)
        decisions[canonical_field] = "VERIFIED" if flag_value else "NOT_VERIFIED"

    manual[f"manual_{canonical_field}_verified"] = flag_value
    if canonical_field == "model":
        if explicit_override:
            manual["manual_model_override"] = True
        elif not flag_value:
            manual["manual_model_override"] = False

    event: dict[str, Any] = {
        "field": canonical_field,
        "state": decisions[canonical_field],
        "verified": flag_value,
        "verified_by": actor,
        "verified_at": timestamp,
        "note": note_text,
    }
    if value is not None:
        event["value"] = copy.deepcopy(value)
    if explicit_override:
        event["explicit_override"] = True

    confirmations = _dict(manual.get("confirmations"))
    confirmations[canonical_field] = copy.deepcopy(event)
    history = [
        _dict(item) for item in _list(manual.get("history")) if isinstance(item, Mapping)
    ]
    history.append(copy.deepcopy(event))
    manual["decisions"] = decisions
    manual["confirmations"] = confirmations
    manual["history"] = history
    manual["manual_verified_by"] = actor
    manual["manual_verified_at"] = timestamp
    manual["manual_note"] = note_text
    facts["manual_verification"] = manual

    final = _resolve_normalised(facts)
    _write_legacy_aliases(facts, final)
    return facts
