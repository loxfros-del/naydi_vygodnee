"""Объяснимый final ranking на шкале 0–100 с одним последним cap."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.category_quality import evaluate_category_quality
from app.category_registry import detect_category, get_category_spec
from app.exact_match import (
    ACCESSORY,
    COMPATIBLE_VARIANT,
    EXACT,
    GENERIC_MATCH,
    MODEL_MISMATCH,
    REQUIRED_SPEC_MISMATCH,
    UNKNOWN,
    match_candidate,
)
from app.request_parser import normalize_request_data
from app.search_evidence import assess_product_card, assess_source_trust


@dataclass(frozen=True)
class RankingResult:
    score: float
    raw_score: float
    breakdown: dict[str, float]
    score_cap: int | None = None
    cap_reasons: tuple[str, ...] = ()


def _value(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _request(request: Any) -> dict[str, Any]:
    if isinstance(request, dict) and "category" in request:
        return dict(request)
    attached = getattr(request, "parsed_details", None)
    if isinstance(attached, dict):
        return dict(attached)
    return normalize_request_data(request)


def _facts(candidate: Any) -> dict[str, Any]:
    facts = _value(candidate, "product_facts", {}) or _value(candidate, "facts", {}) or {}
    return facts if isinstance(facts, dict) else {}


def _price(candidate: Any) -> int | None:
    try:
        return int(_value(candidate, "price", None))
    except (TypeError, ValueError):
        return None


def _budget(request: dict[str, Any]) -> int | None:
    value = str(request.get("budget") or "").replace(" ", "")
    return int(value) if value.isdigit() else None


def _present(value: Any) -> bool:
    return value not in (None, "", [], {}, False, "unknown", "UNKNOWN")


def rank_candidate(request: Any, candidate: Any) -> RankingResult:
    parsed = _request(request)
    facts = _facts(candidate)
    status = str(_value(candidate, "verify_status") or _value(candidate, "status") or "").upper()
    requested_category = str(parsed.get("category") or "unknown")
    title = str(_value(candidate, "title") or "")
    candidate_category = str(facts.get("category") or detect_category(title))
    exact = match_candidate(parsed, candidate, requested_category)
    quality = evaluate_category_quality(parsed, candidate)
    trust = assess_source_trust(candidate, status)
    card = assess_product_card(candidate)

    breakdown = {
        "relevance": 0.0,
        "model_match": 0.0,
        "required_facts": 0.0,
        "quality": 0.0,
        "budget": 0.0,
        "availability": 0.0,
        "source": 0.0,
        "verification": 0.0,
        "completeness": 0.0,
        "penalties": 0.0,
    }

    if requested_category == "unknown":
        breakdown["relevance"] = 6.0 if card.is_product_card else 0.0
    elif candidate_category == requested_category:
        breakdown["relevance"] = 15.0
    elif candidate_category == "unknown":
        breakdown["relevance"] = 5.0
    else:
        breakdown["relevance"] = -20.0

    breakdown["model_match"] = {
        EXACT: 18.0,
        COMPATIBLE_VARIANT: 16.0,
        GENERIC_MATCH: 10.0,
        UNKNOWN: 2.0,
        MODEL_MISMATCH: -40.0,
        REQUIRED_SPEC_MISMATCH: -40.0,
        ACCESSORY: -50.0,
    }.get(exact.status, 0.0)

    missing_count = len(exact.differences)
    required_count = max(1, len(parsed.get("required_criteria") or {}))
    breakdown["required_facts"] = max(-12.0, 12.0 * (required_count - missing_count) / required_count)
    if not parsed.get("required_criteria"):
        breakdown["required_facts"] = 8.0

    breakdown["quality"] = max(-15.0, min(15.0, quality.score * 1.5))

    price = _price(candidate)
    budget = _budget(parsed)
    if price is None:
        breakdown["budget"] = 0.0
    elif not budget:
        breakdown["budget"] = 8.0
    elif price <= budget:
        breakdown["budget"] = 12.0
    elif price <= budget * 1.15:
        breakdown["budget"] = 3.0
    else:
        breakdown["budget"] = -20.0

    availability = str(_value(candidate, "availability") or facts.get("availability_text") or "").upper()
    available_fact = facts.get("available")
    if availability == "AVAILABLE" or available_fact is True:
        breakdown["availability"] = 8.0
    elif availability in {"UNAVAILABLE", "REMOVED_LISTING"} or available_fact is False:
        breakdown["availability"] = -30.0
    else:
        breakdown["availability"] = 2.0

    breakdown["source"] = {"high": 7.0, "medium": 4.0, "low": 1.0}.get(trust.source_confidence, 0.0)
    breakdown["verification"] = {"high": 7.0, "medium": 4.0, "low": 1.0}.get(trust.verification_confidence, 0.0)

    spec = get_category_spec(requested_category)
    useful = [key for key in spec.useful_facts if _present(facts.get(key))]
    denominator = max(1, min(6, len(spec.useful_facts)))
    breakdown["completeness"] = min(6.0, 6.0 * len(useful) / denominator)

    penalties = 0.0
    risks = " ".join(str(item) for item in (_value(candidate, "risk_flags", []) or [])).lower()
    if price is None or status == "PRICE_MISSING":
        penalties -= 10.0
    if _value(candidate, "low_price_suspect", False) or "подозрительно низ" in risks:
        penalties -= 12.0
    if card.confidence == "low":
        penalties -= 8.0
    if status in {"NEED_MANUAL_CHECK", "VERIFY_BLOCKED"}:
        penalties -= 4.0
    if str(_value(candidate, "product_quality_level", "")).lower() == "weak":
        penalties -= 5.0
    breakdown["penalties"] = penalties

    raw = max(0.0, min(100.0, sum(breakdown.values())))
    caps: list[tuple[int, str]] = []
    if status == "NEED_MANUAL_CHECK":
        caps.append((69, "manual_check"))
    if status == "VERIFY_BLOCKED":
        caps.append((60, "verify_blocked"))
    if status == "PRICE_MISSING" or price is None:
        caps.append((50, "price_missing"))
    if status == "OVER_BUDGET_SOFT" or (budget and price and budget < price <= budget * 1.15):
        caps.append((70, "over_budget_soft"))
    if status == "OVER_BUDGET_HARD" or (budget and price and price > budget * 1.15):
        caps.append((30, "over_budget_hard"))
    if exact.status in {MODEL_MISMATCH, REQUIRED_SPEC_MISMATCH}:
        caps.append((25, "model_mismatch" if exact.status == MODEL_MISMATCH else "required_spec_mismatch"))
    if exact.status == ACCESSORY:
        caps.append((15, "accessory"))
    if exact.status == UNKNOWN and parsed.get("model"):
        caps.append((55, "exact_model_unknown"))
    if requested_category == "unknown":
        caps.append((60, "unknown_category_manual_first"))
    if quality.level == "weak" or str(_value(candidate, "product_quality_level", "")).lower() == "weak":
        caps.append((70, "weak_quality"))
    if quality.level == "bad" or str(_value(candidate, "product_quality_level", "")).lower() == "bad":
        caps.append((30, "bad_quality"))
    if card.confidence == "low":
        caps.append((60, "low_product_card_confidence"))
    if _value(candidate, "low_price_suspect", False) or "подозрительно низ" in risks:
        caps.append((55, "low_price_suspect"))
    if availability in {"UNAVAILABLE", "REMOVED_LISTING"} or available_fact is False:
        caps.append((20, "unavailable"))
    legacy_cap = _value(candidate, "final_quality_score_cap", None)
    if legacy_cap is not None:
        caps.append((int(legacy_cap), "category_quality"))

    cap = min((value for value, _ in caps), default=None)
    score = min(raw, float(cap)) if cap is not None else raw
    cap_reasons = tuple(dict.fromkeys(reason for value, reason in caps if value == cap)) if cap is not None else ()
    return RankingResult(round(score, 2), round(raw, 2), breakdown, cap, cap_reasons)
