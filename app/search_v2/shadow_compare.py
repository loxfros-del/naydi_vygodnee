"""Pure comparison of legacy and V2 results for administrator diagnostics."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from statistics import median
from typing import Any, Iterable

from .pilot_sample import REQUIRED_CASES_PER_CATEGORY, REQUIRED_CATEGORIES


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _items(value: Any, name: str) -> list[Any]:
    if isinstance(value, dict):
        raw = value.get(name, [])
    else:
        raw = getattr(value, name, [])
    return list(raw or [])


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _candidate_summary(items: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        exact = _field(item, "exact_match", "")
        final = _field(item, "final_verification", None)
        verification_ready = bool(final) and all(
            _field(final, f"{field}_verified", None) is True
            for field in ("model", "link", "price", "availability", "seller")
        )
        rows.append({
            "id": _field(item, "offer_id", _field(item, "id", "")),
            "title": str(_field(item, "title", "") or ""),
            "price": _field(item, "price", None),
            "source": str(_field(item, "source", "") or ""),
            "url": str(_field(item, "url", "") or ""),
            "status": str(getattr(exact, "value", exact) or _field(item, "status", "")),
            "verification_ready": verification_ready,
        })
    return rows


def build_shadow_comparison(
    *,
    request_id: int | str,
    legacy_result: dict[str, Any] | None,
    legacy_candidates: Iterable[Any],
    v2_result: Any,
) -> dict[str, Any]:
    """Build a JSON-safe snapshot without mutating either engine output."""
    legacy_rows = _candidate_summary(legacy_candidates)
    v2_rows = _candidate_summary(_items(v2_result, "normalized_offers"))
    rejected = _candidate_summary(_items(v2_result, "rejected_offers"))
    recommendations = _jsonable(_items(v2_result, "recommendations"))
    attempts = _jsonable(_items(v2_result, "source_attempts"))
    exact_count = sum(row["status"] == "EXACT" for row in v2_rows)
    wrong_count = sum(
        row["status"] in {"MODEL_MISMATCH", "REQUIRED_SPEC_MISMATCH", "ACCESSORY"}
        for row in rejected
    )
    unsafe_candidate_count = sum(
        row["status"] in {"MODEL_MISMATCH", "REQUIRED_SPEC_MISMATCH", "ACCESSORY"}
        for row in v2_rows
    )
    legacy_prices = sorted({int(row["price"]) for row in legacy_rows if row.get("price")})
    v2_prices = sorted({int(row["price"]) for row in v2_rows if row.get("price")})
    best_offer_id = ""
    for recommendation in _items(v2_result, "recommendations"):
        role = _field(recommendation, "role", "")
        role_text = str(getattr(role, "value", role) or "")
        if role_text == "BEST_OVERALL":
            best_offer_id = str(_field(recommendation, "offer_id", "") or "")
            break
    top_row = next((row for row in v2_rows if str(row.get("id") or "") == best_offer_id), None)
    normalized_request = _field(v2_result, "normalized_request", None)
    category = str(_field(normalized_request, "category", "unknown") or "unknown")
    return {
        "schema": "search_v2:shadow:1",
        "request_id": str(request_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "legacy": {
            "result": _jsonable(legacy_result or {}),
            "candidates": legacy_rows,
            "prices": legacy_prices,
        },
        "v2": {
            "category": category,
            "status": str(getattr(_field(v2_result, "status", ""), "value", _field(v2_result, "status", ""))),
            "candidates": v2_rows,
            "rejected": rejected,
            "exact_count": exact_count,
            "wrong_product_count": wrong_count,
            "unsafe_candidate_count": unsafe_candidate_count,
            "prices": v2_prices,
            "recommendations": recommendations,
            "top1_exact": bool(top_row and top_row.get("status") == "EXACT"),
            "top1_verified": bool(top_row and top_row.get("verification_ready")),
            "source_attempts": attempts,
            "duration_ms": _field(v2_result, "duration", _field(v2_result, "duration_ms", 0)),
            "errors": _jsonable(_field(v2_result, "errors", [])),
        },
        "delta": {
            "candidate_count": len(v2_rows) - len(legacy_rows),
            "minimum_price": (min(v2_prices) if v2_prices else None),
            "legacy_minimum_price": (min(legacy_prices) if legacy_prices else None),
        },
    }


def aggregate_shadow_comparisons(snapshots: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return privacy-safe rollout gates for a fixed shadow sample.

    The aggregate intentionally excludes request IDs, titles, URLs, cities,
    sellers, query text and raw errors. It can be exported as a deployment
    report without becoming a second product/search database.
    """
    rows = [item for item in snapshots if isinstance(item, dict)]
    cases = len(rows)
    exact_top1 = 0
    verified_top1 = 0
    recommended = 0
    rejected_wrong_products = 0
    unsafe_candidates = 0
    system_errors = 0
    source_failures = 0
    durations: list[float] = []
    category_counts = {category: 0 for category in REQUIRED_CATEGORIES}
    for snapshot in rows:
        v2 = snapshot.get("v2") if isinstance(snapshot.get("v2"), dict) else {}
        category = str(v2.get("category") or "unknown")
        if category in category_counts:
            category_counts[category] += 1
        exact_top1 += bool(v2.get("top1_exact"))
        verified_top1 += bool(v2.get("top1_verified"))
        recommended += bool(v2.get("recommendations"))
        rejected_wrong_products += int(v2.get("wrong_product_count") or 0)
        unsafe_candidates += int(v2.get("unsafe_candidate_count") or 0)
        system_errors += str(v2.get("status") or "").upper() in {"ERROR", "TIMEOUT"}
        duration = v2.get("duration_ms")
        if isinstance(duration, (int, float)) and duration >= 0:
            durations.append(float(duration))
        for attempt in v2.get("source_attempts") or []:
            if not isinstance(attempt, dict):
                continue
            if str(attempt.get("status") or "").upper() not in {"SUCCESS", "EMPTY"}:
                source_failures += 1
    category_quotas_met = all(
        category_counts[category] >= REQUIRED_CASES_PER_CATEGORY
        for category in REQUIRED_CATEGORIES
    )
    gates_pass = bool(
        cases >= 30
        and category_quotas_met
        and exact_top1 == cases
        and verified_top1 == cases
        and recommended == cases
        and unsafe_candidates == 0
        and system_errors == 0
    )
    return {
        "schema": "search_v2:shadow-rollout:2",
        "cases": cases,
        "category_counts": category_counts,
        "category_quotas_met": category_quotas_met,
        "top1_exact_cases": exact_top1,
        "top1_verified_cases": verified_top1,
        "recommended_cases": recommended,
        "rejected_wrong_product_count": rejected_wrong_products,
        "unsafe_candidate_count": unsafe_candidates,
        "system_error_cases": system_errors,
        "source_failure_count": source_failures,
        "duration_median_ms": round(median(durations), 1) if durations else None,
        "rollout_ready": gates_pass,
    }


def format_shadow_comparison(snapshot: dict[str, Any]) -> str:
    """Plain administrator text; Telegram escaping remains the handler's job."""
    legacy = snapshot.get("legacy") if isinstance(snapshot.get("legacy"), dict) else {}
    v2 = snapshot.get("v2") if isinstance(snapshot.get("v2"), dict) else {}
    attempts = v2.get("source_attempts") if isinstance(v2.get("source_attempts"), list) else []
    lines = [
        f"Legacy ↔ V2 · заявка #{snapshot.get('request_id', '')}",
        f"Категория: {v2.get('category') or 'unknown'}",
        f"Legacy кандидаты: {len(legacy.get('candidates') or [])}",
        f"V2 кандидаты: {len(v2.get('candidates') or [])}",
        f"V2 exact: {int(v2.get('exact_count') or 0)}",
        f"V2 wrong/rejected: {int(v2.get('wrong_product_count') or 0)}",
        f"V2 рекомендации: {len(v2.get('recommendations') or [])}",
        f"V2 ТОП-1 exact: {'да' if v2.get('top1_exact') else 'нет'}",
        f"V2 ТОП-1 проверен: {'да' if v2.get('top1_verified') else 'нет'}",
        f"V2 unsafe кандидаты: {int(v2.get('unsafe_candidate_count') or 0)}",
        f"V2 источники: {len(attempts)}",
        f"V2 статус: {v2.get('status') or 'UNKNOWN'}",
        f"V2 время: {int(v2.get('duration_ms') or 0)} мс",
    ]
    for attempt in attempts[:10]:
        if not isinstance(attempt, dict):
            continue
        lines.append(
            f"• {attempt.get('source') or '?'}: {attempt.get('status') or '?'}"
            f" ({int(attempt.get('duration_ms') or attempt.get('duration') or 0)} мс)"
        )
    return "\n".join(lines)


__all__ = ["aggregate_shadow_comparisons", "build_shadow_comparison", "format_shadow_comparison"]
