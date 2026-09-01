"""Privacy-safe aggregation for the 30-person Search V2 pilot."""
from __future__ import annotations

from collections import Counter
from typing import Any


PILOT_SCHEMA = "search_v2:real-request-pilot:1"
REQUIRED_CATEGORIES = (
    "phone",
    "laptop",
    "tv",
    "headphones",
    "monitor",
    "chair",
)
REQUIRED_CASES_PER_CATEGORY = 5
ALLOWED_CHANNELS = (
    "telegram_direct",
    "telegram_channel",
    "website",
    "referral",
    "offline",
)

_AUTOMATED_BOOLEAN_FIELDS = (
    "top1_exact",
    "top1_verified",
    "recommendation_created",
    "system_error",
)
_AUTOMATED_COUNT_FIELDS = ("unsafe_candidate_count",)
_AUTOMATED_NUMBER_FIELDS = ("duration_ms",)
_MANUAL_BOOLEAN_FIELDS = (
    "model_matches",
    "link_opens",
    "price_matches",
    "availability_matches",
    "condition_clear",
    "false_savings_claim",
)
_PROHIBITED_KEYS = {
    "request_id",
    "telegram_id",
    "user_id",
    "username",
    "phone",
    "email",
    "name",
    "query",
    "original_query",
    "title",
    "url",
    "city",
    "seller",
}


class PilotManifestError(ValueError):
    """Raised when a pilot manifest is malformed or contains prohibited data."""


def _find_prohibited_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().casefold()
            if normalized in _PROHIBITED_KEYS:
                return str(key)
            nested = _find_prohibited_key(item)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _find_prohibited_key(item)
            if nested:
                return nested
    return None


def _require_nullable_bool(value: Any, *, path: str) -> None:
    if value is not None and not isinstance(value, bool):
        raise PilotManifestError(f"{path} must be true, false or null")


def _require_nullable_number(value: Any, *, path: str, integer: bool = False) -> None:
    if value is None:
        return
    valid = isinstance(value, int) and not isinstance(value, bool) if integer else isinstance(value, (int, float)) and not isinstance(value, bool)
    if not valid or value < 0:
        expected = "a non-negative integer" if integer else "a non-negative number"
        raise PilotManifestError(f"{path} must be {expected} or null")


def _validated_cases(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise PilotManifestError("manifest must be a JSON object")
    if manifest.get("schema") != PILOT_SCHEMA:
        raise PilotManifestError(f"schema must be {PILOT_SCHEMA}")
    prohibited = _find_prohibited_key(manifest)
    if prohibited:
        raise PilotManifestError(f"prohibited personal/search field: {prohibited}")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise PilotManifestError("cases must be a JSON array")

    seen_refs: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        path = f"cases[{index}]"
        if not isinstance(case, dict):
            raise PilotManifestError(f"{path} must be a JSON object")
        participant_ref = str(case.get("participant_ref") or "").strip()
        if not participant_ref:
            raise PilotManifestError(f"{path}.participant_ref is required")
        if participant_ref in seen_refs:
            raise PilotManifestError(f"duplicate participant_ref: {participant_ref}")
        seen_refs.add(participant_ref)

        category = str(case.get("category") or "").strip()
        if category not in REQUIRED_CATEGORIES:
            raise PilotManifestError(f"{path}.category is not supported")
        channel = case.get("channel")
        if channel is not None and channel not in ALLOWED_CHANNELS:
            raise PilotManifestError(f"{path}.channel is not supported")

        for field in ("consent_confirmed", "distinct_participant_confirmed", "real_request_confirmed"):
            if not isinstance(case.get(field), bool):
                raise PilotManifestError(f"{path}.{field} must be true or false")

        automated = case.get("automated")
        manual = case.get("manual_review")
        if not isinstance(automated, dict):
            raise PilotManifestError(f"{path}.automated must be a JSON object")
        if not isinstance(manual, dict):
            raise PilotManifestError(f"{path}.manual_review must be a JSON object")
        for field in _AUTOMATED_BOOLEAN_FIELDS:
            _require_nullable_bool(automated.get(field), path=f"{path}.automated.{field}")
        for field in _AUTOMATED_COUNT_FIELDS:
            _require_nullable_number(automated.get(field), path=f"{path}.automated.{field}", integer=True)
        for field in _AUTOMATED_NUMBER_FIELDS:
            _require_nullable_number(automated.get(field), path=f"{path}.automated.{field}")
        for field in _MANUAL_BOOLEAN_FIELDS:
            _require_nullable_bool(manual.get(field), path=f"{path}.manual_review.{field}")
        validated.append(case)
    return validated


def _validated_channel_funnel(manifest: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    raw = manifest.get("channel_funnel")
    if not isinstance(raw, dict):
        raise PilotManifestError("channel_funnel must be a JSON object")
    assigned = Counter(str(case.get("channel")) for case in cases if case.get("channel") in ALLOWED_CHANNELS)
    result: dict[str, dict[str, int]] = {}
    for channel in ALLOWED_CHANNELS:
        values = raw.get(channel)
        if not isinstance(values, dict):
            raise PilotManifestError(f"channel_funnel.{channel} must be a JSON object")
        invitations = values.get("invitations")
        starts = values.get("starts")
        for field, value in (("invitations", invitations), ("starts", starts)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PilotManifestError(f"channel_funnel.{channel}.{field} must be a non-negative integer")
        if starts > invitations:
            raise PilotManifestError(f"channel_funnel.{channel}.starts cannot exceed invitations")
        if assigned[channel] > starts:
            raise PilotManifestError(f"channel_funnel.{channel}.starts cannot be lower than assigned cases")
        result[channel] = {"invitations": invitations, "starts": starts}
    return result


def _completion_reasons(case: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not case["consent_confirmed"]:
        reasons.append("consent_not_confirmed")
    if not case["distinct_participant_confirmed"]:
        reasons.append("participant_not_confirmed_distinct")
    if not case["real_request_confirmed"]:
        reasons.append("request_not_confirmed_real")
    if case.get("channel") not in ALLOWED_CHANNELS:
        reasons.append("channel_missing")
    automated = case["automated"]
    if any(automated.get(field) is None for field in (*_AUTOMATED_BOOLEAN_FIELDS, *_AUTOMATED_COUNT_FIELDS, *_AUTOMATED_NUMBER_FIELDS)):
        reasons.append("automated_review_incomplete")
    manual = case["manual_review"]
    if any(manual.get(field) is None for field in _MANUAL_BOOLEAN_FIELDS):
        reasons.append("manual_review_incomplete")
    return reasons


def aggregate_real_request_pilot(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate a pilot manifest and return a report with no row identifiers."""
    cases = _validated_cases(manifest)
    funnel = _validated_channel_funnel(manifest, cases)
    completed: list[dict[str, Any]] = []
    incomplete_reasons: Counter[str] = Counter()
    for case in cases:
        reasons = _completion_reasons(case)
        if reasons:
            incomplete_reasons.update(reasons)
        else:
            completed.append(case)

    category_counts = Counter(str(case["category"]) for case in completed)
    channel_counts = Counter(str(case["channel"]) for case in completed)
    category_report = {category: category_counts.get(category, 0) for category in REQUIRED_CATEGORIES}
    channel_report = {channel: channel_counts.get(channel, 0) for channel in ALLOWED_CHANNELS}
    funnel_report: dict[str, dict[str, int | float | None]] = {}
    for channel in ALLOWED_CHANNELS:
        invitations = funnel[channel]["invitations"]
        starts = funnel[channel]["starts"]
        qualified = channel_report[channel]
        funnel_report[channel] = {
            "invitations": invitations,
            "starts": starts,
            "qualified_cases": qualified,
            "start_rate_percent": round(starts * 100 / invitations, 1) if invitations else None,
            "qualified_rate_percent": round(qualified * 100 / invitations, 1) if invitations else None,
            "start_to_qualified_percent": round(qualified * 100 / starts, 1) if starts else None,
        }

    automated_failures: Counter[str] = Counter()
    manual_failures: Counter[str] = Counter()
    durations: list[float] = []
    for case in completed:
        automated = case["automated"]
        manual = case["manual_review"]
        for field in ("top1_exact", "top1_verified", "recommendation_created"):
            if automated[field] is not True:
                automated_failures[field] += 1
        if automated["system_error"] is True:
            automated_failures["system_error"] += 1
        if automated["unsafe_candidate_count"] > 0:
            automated_failures["unsafe_candidate"] += 1
        durations.append(float(automated["duration_ms"]))
        for field in ("model_matches", "link_opens", "price_matches", "availability_matches", "condition_clear"):
            if manual[field] is not True:
                manual_failures[field] += 1
        if manual["false_savings_claim"] is True:
            manual_failures["false_savings_claim"] += 1

    quotas_met = all(category_report[category] >= REQUIRED_CASES_PER_CATEGORY for category in REQUIRED_CATEGORIES)
    sample_complete = bool(
        len(cases) >= len(REQUIRED_CATEGORIES) * REQUIRED_CASES_PER_CATEGORY
        and len(completed) == len(cases)
        and quotas_met
    )
    assisted_flow_ready = bool(
        sample_complete
        and not manual_failures
        and not any(
            automated_failures.get(field)
            for field in ("top1_exact", "recommendation_created", "system_error", "unsafe_candidate")
        )
    )
    rollout_ready = bool(assisted_flow_ready and not automated_failures.get("top1_verified"))

    return {
        "schema": "search_v2:real-request-pilot-report:1",
        "cases": len(cases),
        "completed_cases": len(completed),
        "incomplete_cases": len(cases) - len(completed),
        "category_counts": category_report,
        "channel_counts": channel_report,
        "channel_funnel": funnel_report,
        "incomplete_reason_counts": dict(sorted(incomplete_reasons.items())),
        "automated_failure_counts": dict(sorted(automated_failures.items())),
        "manual_failure_counts": dict(sorted(manual_failures.items())),
        "duration_average_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "quotas_met": quotas_met,
        "sample_complete": sample_complete,
        "assisted_flow_ready": assisted_flow_ready,
        "rollout_ready": rollout_ready,
    }


__all__ = [
    "ALLOWED_CHANNELS",
    "PILOT_SCHEMA",
    "PilotManifestError",
    "REQUIRED_CASES_PER_CATEGORY",
    "REQUIRED_CATEGORIES",
    "aggregate_real_request_pilot",
]
