"""Deterministic acceptance metrics for already collected search results.

No source adapter or network code is called here.  ``evaluate_acceptance_results``
accepts dictionaries (or simple objects) with this compact contract:

* case: ``candidates``, ``source_attempts``, ``search_time_ms``, optional
  ``market_median``/``market_stats``, ``roles`` and ``expected_roles``;
* candidate: exact status, product-page prediction/truth, price evidence,
  availability/quality facts and normal offer identity fields;
* source attempt: ``source``, ``status`` and ``latency_ms``.

Rates use observed rows only.  Exact recall is case-level: the share of cases
expecting an exact offer that retrieved at least one EXACT/COMPATIBLE_VARIANT.
P95 uses the deterministic nearest-rank definition.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
import math
from statistics import fmean
from typing import Any, Iterable, Mapping

from app.market_analysis import OfferIdentity
from app.sources.offer import SourceStatus, normalize_source_status


EXACT_STATUSES = frozenset({"EXACT", "COMPATIBLE_VARIANT"})
FAILED_SOURCE_STATUSES = frozenset({
    SourceStatus.BLOCKED,
    SourceStatus.RATE_LIMITED,
    SourceStatus.TIMEOUT,
    SourceStatus.INVALID_RESPONSE,
    SourceStatus.ERROR,
})
WEAK_STATUSES = frozenset({
    "WEAK", "WEAK_CANDIDATE", "NEED_MANUAL_CHECK", "VERIFY_BLOCKED", "PRICE_MISSING",
})
ROLE_ALIASES = {
    "BEST": "BEST_OVERALL", "TOP": "BEST_OVERALL", "TOP1": "BEST_OVERALL",
    "BEST_OVERALL": "BEST_OVERALL",
    "BUDGET": "CHEAP_WITH_RISK", "CHEAP": "CHEAP_WITH_RISK",
    "APPROVED_BUDGET": "CHEAP_WITH_RISK", "CHEAP_WITH_RISK": "CHEAP_WITH_RISK",
    "BACKUP": "RELIABLE", "APPROVED": "RELIABLE", "APPROVED_BACKUP": "RELIABLE",
    "RELIABLE": "RELIABLE",
}
STRUCTURED_PRICE_SOURCES = frozenset({
    "structured", "json_ld", "direct", "direct_store", "api", "offer_dto",
})


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _first(value: Any, names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        item = _value(value, name, None)
        if item not in (None, ""):
            return item
    return default


def _facts(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    raw_json = _value(value, "facts_json", "")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict):
            result.update(decoded)
    for name in ("facts", "product_facts", "structured_facts"):
        raw = _value(value, name, {})
        if isinstance(raw, Mapping):
            result.update(raw)
    return result


def _candidate_value(candidate: Any, *names: str, default: Any = None) -> Any:
    facts = _facts(candidate)
    for name in names:
        value = _value(candidate, name, None)
        if value not in (None, ""):
            return value
        value = facts.get(name)
        if value not in (None, ""):
            return value
    return default


def _rows(case: Any, *names: str) -> list[Any]:
    value = _first(case, names, default=[])
    if isinstance(value, Mapping):
        return list(value.values())
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "y", "да"}:
        return True
    if normalized in {"0", "false", "no", "n", "нет"}:
        return False
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(min(max(percentile, 0.0), 1.0) * len(ordered)))
    return float(ordered[rank - 1])


def _normalized_role(value: Any) -> str:
    return ROLE_ALIASES.get(str(value or "").strip().upper(), "")


def _actual_roles(case: Any) -> set[str]:
    raw = _first(case, ("roles", "recommendations", "cards"), default=[])
    if isinstance(raw, Mapping):
        values = list(raw.values())
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = []
    result: set[str] = set()
    for item in values:
        role = item if isinstance(item, str) else _first(item, ("role", "status"), default="")
        normalized = _normalized_role(role)
        if normalized:
            result.add(normalized)
    return result


def _expected_roles(case: Any, has_candidates: bool) -> set[str]:
    raw = _value(case, "expected_roles", None)
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = ["BEST_OVERALL"] if has_candidates else []
    return {role for value in values if (role := _normalized_role(value))}


def _market_median_present(case: Any) -> bool:
    direct = _number(_value(case, "market_median", None))
    if direct is not None and direct > 0:
        return True
    stats = _value(case, "market_stats", None)
    if isinstance(stats, Mapping):
        median_value = _number(stats.get("median"))
    else:
        median_value = _number(getattr(stats, "median", None)) if stats is not None else None
    return median_value is not None and median_value > 0


def _structured_price_observation(candidate: Any) -> tuple[bool, bool]:
    source = str(_candidate_value(candidate, "price_source", "price_evidence", default="") or "").casefold()
    structured = _number(_candidate_value(candidate, "structured_price", default=None))
    found = bool(_candidate_value(candidate, "structured_price_found", default=False))
    has_evidence = found or structured is not None or source in STRUCTURED_PRICE_SOURCES
    if not has_evidence:
        return False, False
    persisted = _number(_candidate_value(
        candidate, "persisted_price", "final_price", "price", default=None,
    ))
    if structured is None:
        # A structured source with no separately retained raw value still
        # persists successfully when its final price exists; an explicit flag
        # may override that inference for adapters that track both stages.
        explicit = _bool_or_none(_candidate_value(candidate, "structured_price_persisted", default=None))
        return True, bool(explicit) if explicit is not None else persisted is not None
    return True, persisted is not None and persisted == structured


def _product_page_observation(candidate: Any) -> tuple[bool, bool]:
    predicted = _bool_or_none(_candidate_value(
        candidate, "product_page_predicted", "classified_product_page", "product_page_verified",
        default=None,
    ))
    truth = _bool_or_none(_candidate_value(
        candidate, "actual_product_page", "product_page_valid", "is_product_page",
        default=None,
    ))
    # Precision needs labelled truth; unknown rows do not enter its denominator.
    return predicted is True and truth is not None, truth is True


def _is_weak(candidate: Any) -> bool:
    status = str(_candidate_value(candidate, "status", "verify_status", default="") or "").upper()
    level = str(_candidate_value(
        candidate, "product_quality_level", "category_quality_level", "quality", default="",
    ) or "").upper()
    return status in WEAK_STATUSES or level == "WEAK"


def _duplicate_count(candidates: list[Any]) -> int:
    seen: set[OfferIdentity] = set()
    duplicates = 0
    for candidate in candidates:
        identity = OfferIdentity.from_offer(candidate)
        if not identity.listing_key or identity.listing_key == "title:":
            continue
        if identity in seen:
            duplicates += 1
        else:
            seen.add(identity)
    return duplicates


@dataclass(frozen=True, slots=True)
class AcceptanceMetrics:
    exact_match_recall: float
    required_spec_mismatch_rate: float
    product_page_precision: float
    structured_price_persistence: float
    source_failure_rate: float
    duplicate_rate: float
    market_median_coverage: float
    role_completeness: float
    weak_candidate_rate: float
    average_search_time_ms: float
    p95_search_time_ms: float
    per_source_latency_ms: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_acceptance_results(results: Iterable[Any]) -> AcceptanceMetrics:
    """Aggregate deterministic metrics from acceptance case result rows."""
    cases = list(results)
    exact_expected_cases = 0
    exact_found_cases = 0
    candidates_total = 0
    required_mismatches = 0
    predicted_product_pages = 0
    true_product_pages = 0
    structured_total = 0
    structured_persisted = 0
    source_total = 0
    source_failures = 0
    duplicates = 0
    median_cases = 0
    expected_role_slots = 0
    completed_role_slots = 0
    weak_candidates = 0
    durations: list[float] = []
    source_latencies: dict[str, list[float]] = defaultdict(list)

    for case in cases:
        candidates = _rows(case, "candidates", "top_candidates", "offers", "saved")
        candidates_total += len(candidates)
        expects_exact = _bool_or_none(_value(case, "expects_exact", True)) is not False
        if expects_exact:
            exact_expected_cases += 1
            if any(
                str(_candidate_value(item, "exact_match_status", "exact_match", default="") or "").upper()
                in EXACT_STATUSES
                for item in candidates
            ):
                exact_found_cases += 1

        duplicates += _duplicate_count(candidates)
        for candidate in candidates:
            exact = str(_candidate_value(
                candidate, "exact_match_status", "exact_match", default="",
            ) or "").upper()
            required_mismatches += int(exact == "REQUIRED_SPEC_MISMATCH")
            weak_candidates += int(_is_weak(candidate))

            labelled_prediction, true_page = _product_page_observation(candidate)
            if labelled_prediction:
                predicted_product_pages += 1
                true_product_pages += int(true_page)

            structured_observed, persisted = _structured_price_observation(candidate)
            if structured_observed:
                structured_total += 1
                structured_persisted += int(persisted)

        median_cases += int(_market_median_present(case))
        actual_roles = _actual_roles(case)
        expected_roles = _expected_roles(case, bool(candidates))
        expected_role_slots += len(expected_roles)
        completed_role_slots += len(actual_roles & expected_roles)

        duration = _number(_first(case, ("search_time_ms", "duration_ms", "elapsed_ms"), default=None))
        if duration is not None and duration >= 0:
            durations.append(duration)

        for attempt in _rows(case, "source_attempts", "attempts", "sources"):
            source = str(_first(attempt, ("source", "platform", "name"), default="unknown") or "unknown")
            status = normalize_source_status(_value(attempt, "status", ""))
            source_total += 1
            source_failures += int(status in FAILED_SOURCE_STATUSES)
            latency = _number(_first(attempt, ("latency_ms", "duration_ms", "elapsed_ms"), default=None))
            if latency is not None and latency >= 0:
                source_latencies[source].append(latency)

    average_duration = round(float(fmean(durations)), 2) if durations else 0.0
    per_source = {
        source: round(float(fmean(values)), 2)
        for source, values in sorted(source_latencies.items())
        if values
    }
    return AcceptanceMetrics(
        exact_match_recall=_rate(exact_found_cases, exact_expected_cases),
        required_spec_mismatch_rate=_rate(required_mismatches, candidates_total),
        product_page_precision=_rate(true_product_pages, predicted_product_pages),
        structured_price_persistence=_rate(structured_persisted, structured_total),
        source_failure_rate=_rate(source_failures, source_total),
        duplicate_rate=_rate(duplicates, candidates_total),
        market_median_coverage=_rate(median_cases, len(cases)),
        role_completeness=_rate(completed_role_slots, expected_role_slots),
        weak_candidate_rate=_rate(weak_candidates, candidates_total),
        average_search_time_ms=average_duration,
        p95_search_time_ms=round(_nearest_rank_percentile(durations, 0.95), 2),
        per_source_latency_ms=per_source,
    )


__all__ = [
    "AcceptanceMetrics",
    "evaluate_acceptance_results",
]
