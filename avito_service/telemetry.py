"""Best-effort structured traces for the manually enabled Avito live pilot."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from threading import Lock
import time
from typing import Any, Mapping

from .models import AnalysisReport, SearchRequest


TRACE_SCHEMA_VERSION = 1
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _milliseconds(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def normalized_search(request: SearchRequest) -> dict[str, Any]:
    """Return only search filters; never credentials, headers or provider payloads."""
    return {
        "query": request.query.strip()[:500],
        "location": request.location.strip()[:160],
        "category": request.category,
        "mode": request.mode,
        "priority": request.priority,
        "max_results": request.max_results,
        "desired_results": request.desired_results,
        "price_min": request.price_min,
        "price_max": request.price_max,
        "required_storage": request.required_storage,
        "required_sim": request.required_sim,
        "required_condition": request.required_condition,
        "attributes": dict(request.attributes),
        "pickup_only": request.pickup_only,
    }


def search_fingerprint(request: SearchRequest) -> str:
    value = normalized_search(request)
    value["query"] = request.query.casefold().strip()
    value["location"] = request.location.casefold().strip()
    packed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


class PilotTraceStore:
    """One atomic JSON file per job. Storage failure is intentionally non-fatal."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = Lock()
        self.last_error = ""

    def write(self, trace: Mapping[str, Any]) -> bool:
        job_id = str(trace.get("job_id") or "")
        if not _SAFE_JOB_ID.fullmatch(job_id):
            self.last_error = "invalid job id"
            return False
        try:
            body = json.dumps(trace, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            with self._lock:
                self.path.mkdir(parents=True, exist_ok=True)
                target = self.path / f"{job_id}.json"
                temporary = self.path / f".{job_id}.tmp"
                temporary.write_text(body + "\n", encoding="utf-8")
                temporary.replace(target)
            self.last_error = ""
            return True
        except (OSError, TypeError, ValueError) as exc:
            self.last_error = type(exc).__name__
            return False


class SearchTrace:
    """Thread-safe, best-effort trace recorder for one search job."""

    def __init__(
        self,
        job_id: str,
        request: SearchRequest,
        *,
        store: PilotTraceStore | None = None,
        parse_ms: int | float | None = None,
        usd_rub_rate: float = 100.0,
        clock: Any = time.monotonic,
    ) -> None:
        self.job_id = job_id
        self.store = store
        self._clock = clock
        self._lock = Lock()
        self._started_mono = float(clock())
        self._stage_started = self._started_mono
        self._current_stage = "queued"
        self._cancel_mono: float | None = None
        self._data: dict[str, Any] = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "job_id": job_id,
            "query": request.query.strip()[:500],
            "normalized_query": normalized_search(request),
            "search_fingerprint": search_fingerprint(request),
            "started_at": _iso_now(),
            "finished_at": None,
            "status": "running",
            "timings": {
                "parse_ms": round(max(0.0, float(parse_ms or 0))),
                "waiting_before_collection_ms": 0,
                "collection_ms": 0,
                "market_analysis_ms": 0,
                "filter_ms": 0,
                "text_ai_ms": 0,
                "photo_ai_ms": 0,
                "final_revalidation_ms": 0,
                "ranking_ms": 0,
                "response_preparation_ms": 0,
                "total_ms": None,
                "stage_ms": {},
            },
            "collection": {
                "listings_collected": None,
                "unique_listing_ids": None,
                "duplicates_removed": None,
                "apify_runs": [],
            },
            "filtering": {
                "after_location_filter": None,
                "after_price_filter": None,
                "after_model_filter": None,
                "after_category_filter": None,
                "after_hard_filters": None,
                "text_ai_candidates": None,
                "text_ai_approved": None,
                "photo_ai_candidates": None,
                "photo_ai_approved": None,
                "rejection_reasons": [],
                "text_routing": [],
                "bargain_funnel": {},
                "bargain_decisions": [],
            },
            "ai": {
                "model": None,
                "prompt_version": "avito-strict-v1",
                "cache_hits": 0,
                "cache_misses": 0,
                "text": self._empty_ai_section(),
                "photo": self._empty_ai_section(),
            },
            "verification": {
                "photo_completed": None,
                "final_revalidation_candidates": None,
                "final_revalidation_returned": None,
                "final_revalidation_completed": None,
                "final_revalidation_outcomes": [],
            },
            "cost": {
                "usd_rub_rate": max(1.0, float(usd_rub_rate)),
                "apify_usd": None,
                "apify_estimated_usd": None,
                "apify_rub": None,
                "apify_estimated_rub": None,
                "ai_rub": None,
                "ai_estimated_rub": None,
                "other_rub": None,
                "total_rub": None,
                "estimated_total_rub": None,
                "by_stage": {
                    name: {"actual_rub": 0.0, "estimated_rub": 0.0, "accounted_rub": 0.0}
                    for name in ("collection", "text_ai", "photo_ai", "revalidation")
                },
            },
            "result": {
                "final_results": None,
                "empty_reason": "",
                "outcome": None,
                "published_after_cancel": False,
            },
            "cancel": {
                "requested": False,
                "requested_at": None,
                "stage": None,
                "active_apify_run_ids": [],
                "abort_requested": False,
                "abort_confirmed": None,
                "worker_stopped_at": None,
                "stop_latency_ms": None,
                "expensive_stage_started_after_cancel": False,
            },
            "reuse": {
                "active_job_dedup_hits": 0,
                "market_cache": {"hit": None, "age_seconds": None, "miss_reason": None},
                "ai_cache_hit": False,
            },
            "error": None,
        }
        self._persist()

    @staticmethod
    def _empty_ai_section() -> dict[str, Any]:
        return {
            "calls": 0,
            "listings": 0,
            "duration_ms": 0,
            "input_tokens": None,
            "output_tokens": None,
            "actual_cost_rub": None,
            "estimated_cost_rub": None,
            "errors": 0,
            "retries": 0,
            "packets": [],
        }

    def _snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def snapshot(self) -> dict[str, Any]:
        return self._snapshot()

    def _persist(self) -> None:
        if self.store is None:
            return
        try:
            self.store.write(self._snapshot())
        except Exception:
            pass

    def _refresh_stage_costs_locked(self) -> None:
        rate = max(1.0, float(self._data["cost"].get("usd_rub_rate") or 100.0))
        values = {
            name: {"actual_rub": 0.0, "estimated_rub": 0.0, "accounted_rub": 0.0}
            for name in ("collection", "text_ai", "photo_ai", "revalidation")
        }
        for run in self._data["collection"]["apify_runs"]:
            stage = "revalidation" if run.get("purpose") == "final_revalidation" else "collection"
            cost = run.get("accounted_cost_usd")
            if cost is None and run.get("cost_estimated") is False:
                cost = run.get("actual_cost_usd")
            try:
                rub = max(0.0, float(cost)) * rate
            except (TypeError, ValueError):
                continue
            estimated = run.get("cost_estimated") is not False
            key = "estimated_rub" if estimated else "actual_rub"
            values[stage][key] += rub
            values[stage]["accounted_rub"] += rub
        for kind, stage in (("text", "text_ai"), ("photo", "photo_ai")):
            section = self._data["ai"][kind]
            actual = max(0.0, float(section.get("actual_cost_rub") or 0.0))
            estimated = max(0.0, float(section.get("estimated_cost_rub") or 0.0))
            values[stage]["actual_rub"] = actual
            values[stage]["estimated_rub"] = estimated
            values[stage]["accounted_rub"] = actual + estimated
        self._data["cost"]["by_stage"] = {
            stage: {key: round(amount, 4) for key, amount in costs.items()}
            for stage, costs in values.items()
        }

    def _close_stage_locked(self, now: float) -> None:
        elapsed = _milliseconds(now - self._stage_started)
        stage_ms = self._data["timings"]["stage_ms"]
        stage_ms[self._current_stage] = int(stage_ms.get(self._current_stage, 0)) + elapsed
        aliases = {
            "queued": "waiting_before_collection_ms",
            "collect": "collection_ms",
            "market": "market_analysis_ms",
            "prepare": "filter_ms",
            "text": "text_ai_ms",
            "photo": "photo_ai_ms",
            "refresh": "final_revalidation_ms",
            "ranking": "ranking_ms",
        }
        alias = aliases.get(self._current_stage)
        if alias:
            self._data["timings"][alias] += elapsed
        self._stage_started = now

    def stage(self, name: str) -> None:
        try:
            now = float(self._clock())
            with self._lock:
                if name == self._current_stage:
                    return
                self._close_stage_locked(now)
                self._current_stage = str(name)[:80]
            self._persist()
        except Exception:
            pass

    def dedup_hit(self) -> None:
        try:
            with self._lock:
                self._data["reuse"]["active_job_dedup_hits"] += 1
            self._persist()
        except Exception:
            pass

    def record_cache(
        self, kind: str, *, hit: bool, age_seconds: float | None = None,
        miss_reason: str | None = None,
    ) -> None:
        try:
            with self._lock:
                if kind == "market":
                    self._data["reuse"]["market_cache"] = {
                        "hit": bool(hit),
                        "age_seconds": round(max(0.0, age_seconds), 3) if age_seconds is not None else None,
                        "miss_reason": None if hit else str(miss_reason or "unknown")[:80],
                    }
                elif kind == "ai":
                    self._data["reuse"]["ai_cache_hit"] = (
                        self._data["reuse"]["ai_cache_hit"] or bool(hit)
                    )
                    key = "cache_hits" if hit else "cache_misses"
                    self._data["ai"][key] += 1
            self._persist()
        except Exception:
            pass

    def record_final_refresh(
        self, requested_ids: tuple[str, ...], *, returned_ids: tuple[str, ...] = (),
        outcomes: Mapping[str, str] | None = None,
    ) -> None:
        """Record only public listing IDs and fixed reason codes, never page data."""
        allowed = {
            "verified", "missing", "url_changed", "stale", "evidence_changed",
            "rule_failed", "provider_error", "not_run",
        }
        try:
            requested = tuple(str(value) for value in requested_ids if str(value).isdigit())[:10]
            returned = {str(value) for value in returned_ids if str(value).isdigit()}
            states = outcomes or {}
            rows = [{
                "listing_id": listing_id,
                "returned": listing_id in returned,
                "status": states.get(listing_id) if states.get(listing_id) in allowed else "not_run",
            } for listing_id in requested]
            with self._lock:
                self._data["verification"].update({
                    "final_revalidation_candidates": len(requested),
                    "final_revalidation_returned": sum(row["returned"] for row in rows),
                    "final_revalidation_completed": sum(row["status"] == "verified" for row in rows),
                    "final_revalidation_outcomes": rows,
                })
            self._persist()
        except Exception:
            pass

    def expensive_stage_started(self, name: str) -> None:
        try:
            with self._lock:
                if self._cancel_mono is not None:
                    self._data["cancel"]["expensive_stage_started_after_cancel"] = True
        except Exception:
            pass

    def record_ai(
        self,
        kind: str,
        *,
        model: str,
        calls: int,
        listings: int,
        duration_ms: int,
        cost_rub: float,
        cost_estimated: bool,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        errors: int = 0,
        retries: int = 0,
        actual_cost_rub: float | None = None,
        estimated_cost_rub: float | None = None,
    ) -> None:
        try:
            if kind not in {"text", "photo"}:
                return
            with self._lock:
                section = self._data["ai"][kind]
                self._data["ai"]["model"] = str(model or "unknown")[:160]
                section["calls"] += max(0, int(calls))
                section["listings"] += max(0, int(listings))
                section["duration_ms"] += max(0, int(duration_ms))
                section["errors"] += max(0, int(errors))
                section["retries"] += max(0, int(retries))
                for key, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
                    if value is not None:
                        section[key] = int(section[key] or 0) + max(0, int(value))
                if actual_cost_rub is None and estimated_cost_rub is None:
                    cost_key = "estimated_cost_rub" if cost_estimated else "actual_cost_rub"
                    section[cost_key] = round(
                        float(section[cost_key] or 0.0) + max(0.0, float(cost_rub)), 4,
                    )
                else:
                    section["actual_cost_rub"] = round(
                        float(section["actual_cost_rub"] or 0.0)
                        + max(0.0, float(actual_cost_rub or 0.0)), 4,
                    )
                    section["estimated_cost_rub"] = round(
                        float(section["estimated_cost_rub"] or 0.0)
                        + max(0.0, float(estimated_cost_rub or 0.0)), 4,
                    )
                # A review can combine an uncertain parent request with billed
                # child replies. Its boolean estimate flag loses that split.
                # Use complete packet receipts when they reconcile to the total.
                packets = section["packets"]
                packet_budgets = [packet["budget"] for packet in packets]
                packet_amounts = [_number(budget.get("accounted_cost_rub")) for budget in packet_budgets]
                packet_calls = sum(not budget.get("reservation_blocked") for budget in packet_budgets)
                if (kind == "text" and packets and packet_calls == section["calls"]
                        and all(value is not None and value >= 0 for value in packet_amounts)
                        and all(isinstance(budget.get("cost_estimated"), bool) for budget in packet_budgets)
                        and math.isclose(sum(packet_amounts),
                            float(section["actual_cost_rub"] or 0) + float(section["estimated_cost_rub"] or 0),
                            rel_tol=0, abs_tol=0.0001)):
                    section["actual_cost_rub"] = round(sum(
                        value for value, budget in zip(packet_amounts, packet_budgets)
                        if not budget["cost_estimated"]), 4)
                    section["estimated_cost_rub"] = round(sum(
                        value for value, budget in zip(packet_amounts, packet_budgets)
                        if budget["cost_estimated"]), 4)
                self._refresh_stage_costs_locked()
            self._persist()
        except Exception:
            pass

    def record_ai_packet(self, record: Mapping[str, Any], *, kind: str = "text") -> None:
        """Store bounded structural metadata; raw prompts/responses are rejected by omission."""
        try:
            if kind not in {"text", "photo"}:
                return
            def ids(name: str) -> list[str]:
                value = record.get(name)
                if not isinstance(value, (list, tuple)):
                    return []
                return [str(item)[:80] for item in value[:20] if str(item)]

            budget = record.get("budget") if isinstance(record.get("budget"), Mapping) else {}
            safe_budget = {
                key: budget.get(key)
                for key in (
                    "reservation_rub", "reservation_state", "reservation_blocked",
                    "accounted_cost_rub", "cost_estimated", "active_reservations",
                )
            }
            safe = {
                "listing_ids": ids("listing_ids"),
                "batch_size": max(0, int(record.get("batch_size") or 0)),
                "attempt": max(0, int(record.get("attempt") or 0)),
                "split_depth": max(0, int(record.get("split_depth") or 0)),
                "model": str(record.get("model") or "")[:160],
                "route": str(record.get("route") or "")[:240],
                "duration_ms": max(0, int(record.get("duration_ms") or 0)),
                "provider_error_code": (
                    str(record.get("provider_error_code"))[:120]
                    if record.get("provider_error_code") else None
                ),
                "http_status": (
                    int(record["http_status"])
                    if isinstance(record.get("http_status"), int) else None
                ),
                "parse_schema_error": (
                    str(record.get("parse_schema_error"))[:160]
                    if record.get("parse_schema_error") else None
                ),
                "expected_result_count": max(0, int(record.get("expected_result_count") or 0)),
                "returned_result_count": max(0, int(record.get("returned_result_count") or 0)),
                "completed_result_count": max(0, int(record.get("completed_result_count") or 0)),
                "missing_ids": ids("missing_ids"),
                "duplicate_ids": ids("duplicate_ids"),
                "unknown_ids": ids("unknown_ids"),
                "invalid_items": max(0, int(record.get("invalid_items") or 0)),
                "finish_reason": (
                    str(record.get("finish_reason"))[:80]
                    if record.get("finish_reason") is not None else None
                ),
                "will_split": bool(record.get("will_split")),
                "budget": safe_budget,
                **{
                    key: str(record[key])[:120] if record.get(key) is not None else None
                    for key in ("transport_phase", "transport_error_type", "transport_errno",
                                "transport_winerror", "tls_reason", "request_outcome",
                                "transport_elapsed_seconds", "transport_timeout_seconds", "transport_proxy_mode")
                },
            }
            with self._lock:
                packets = self._data["ai"][kind]["packets"]
                if len(packets) < 100:
                    packets.append(safe)
            self._persist()
        except Exception:
            pass

    def record_apify(self, event: str, record: Mapping[str, Any]) -> None:
        try:
            safe = {
                key: record.get(key)
                for key in (
                    "trace_run_id", "purpose", "run_id", "started_at", "finished_at",
                    "duration_ms", "status", "requested_max_total_charge_usd",
                    "actual_cost_usd", "accounted_cost_usd", "cost_estimated", "items",
                    "cancel_requested", "abort_requested", "abort_confirmed", "used",
                    "error_code", "error_type", "reservation_accounting",
                    "phase", "http_status", "provider_error_code", "sanitized_response",
                    "network_error_type", "network_errno", "actor_id", "start_run_duration_ms",
                    "run_id_received", "request_reached_provider", "no_run_proven",
                )
            }
            identity = str(safe.get("trace_run_id") or safe.get("run_id") or "")
            with self._lock:
                runs = self._data["collection"]["apify_runs"]
                target = next((item for item in runs if item.get("trace_run_id") == identity), None)
                if target is None:
                    target = {}
                    runs.append(target)
                previous_event = target.get("event")
                # Keep nullable fields explicit: ``null`` means that the provider
                # did not expose an actual value, rather than a measured zero.
                target.update(safe)
                target["trace_run_id"] = identity
                target["event"] = event
                if (event == "started" and self._cancel_mono is not None
                        and previous_event not in {"starting", "started"}):
                    self._data["cancel"]["expensive_stage_started_after_cancel"] = True
                if target.get("abort_requested"):
                    self._data["cancel"]["abort_requested"] = True
                if "abort_confirmed" in target:
                    self._data["cancel"]["abort_confirmed"] = bool(target["abort_confirmed"])
                self._refresh_stage_costs_locked()
            self._persist()
        except Exception:
            pass

    def request_cancel(self, stage: str) -> None:
        try:
            now = float(self._clock())
            with self._lock:
                if self._cancel_mono is not None:
                    return
                self._cancel_mono = now
                active = [
                    str(run.get("run_id")) for run in self._data["collection"]["apify_runs"]
                    if run.get("run_id") and run.get("event") == "started"
                ]
                self._data["cancel"].update({
                    "requested": True,
                    "requested_at": _iso_now(),
                    "stage": stage,
                    "active_apify_run_ids": active,
                })
                self._data["status"] = "cancelled"
            self._persist()
        except Exception:
            pass

    def finish_report(self, report: AnalysisReport, *, response_preparation_ms: int = 0, usd_rub_rate: float = 100.0) -> None:
        try:
            now = float(self._clock())
            costs = report.costs
            pipeline = report.pipeline
            with self._lock:
                self._close_stage_locked(now)
                self._current_stage = "finished"
                self._data["timings"]["response_preparation_ms"] = max(0, int(response_preparation_ms))
                self._data["timings"]["total_ms"] = _milliseconds(now - self._started_mono)
                raw = int(pipeline.raw_collected_count)
                unique = int(pipeline.deduplicated_count)
                self._data["collection"].update({
                    "listings_collected": raw,
                    "unique_listing_ids": unique,
                    "duplicates_removed": max(0, raw - unique),
                })
                self._data["filtering"].update({
                    "after_location_filter": int(pipeline.correct_city_count),
                    "after_model_filter": int(pipeline.request_family_matched_count),
                    "after_hard_filters": int(pipeline.deterministic_eligible_count),
                    "text_ai_candidates": int(pipeline.text_attempted_count),
                    "text_ai_approved": int(pipeline.text_matched_count),
                    "photo_ai_candidates": int(pipeline.photo_attempted_count),
                    "photo_ai_approved": int(pipeline.photo_completed_count),
                    "rejection_reasons": list(pipeline.rejection_reasons),
                    "text_routing": list(pipeline.text_routing),
                    "bargain_funnel": dict(pipeline.bargain_funnel),
                    "bargain_decisions": [dict(item) for item in pipeline.bargain_decisions],
                })
                self._data["verification"].update({
                    "photo_completed": int(pipeline.photo_completed_count),
                })
                if self._data["verification"]["final_revalidation_candidates"] is None:
                    self._data["verification"]["final_revalidation_candidates"] = 0
                    self._data["verification"]["final_revalidation_returned"] = 0
                    self._data["verification"]["final_revalidation_completed"] = 0
                actual_apify = None if costs.apify_cost_estimated else round(costs.apify_cost_usd, 6)
                estimated_apify = round(costs.apify_cost_usd, 6) if costs.apify_cost_estimated else None
                actual_ai = None if costs.ai_cost_estimated else round(costs.ai_cost_rub, 4)
                estimated_ai = round(costs.ai_cost_rub, 4) if costs.ai_cost_estimated else None
                rate = max(1.0, float(usd_rub_rate))
                all_actual = actual_apify is not None and actual_ai is not None
                self._data["cost"].update({
                    "usd_rub_rate": rate,
                    "apify_usd": actual_apify,
                    "apify_estimated_usd": estimated_apify,
                    "apify_rub": round(actual_apify * rate, 4) if actual_apify is not None else None,
                    "apify_estimated_rub": round(estimated_apify * rate, 4) if estimated_apify is not None else None,
                    "ai_rub": actual_ai,
                    "ai_estimated_rub": estimated_ai,
                    "other_rub": None,
                    "total_rub": round(actual_apify * rate + actual_ai, 4) if all_actual else None,
                    "estimated_total_rub": round(costs.estimated_total_rub, 4),
                })
                self._refresh_stage_costs_locked()
                final_results = int(pipeline.final_visible_count)
                self._data["result"].update({
                    "final_results": final_results,
                    "empty_reason": report.empty_reason,
                    "outcome": report.outcome or ("SUCCESS" if final_results else "EMPTY_VERIFIED"),
                    "published_after_cancel": self._cancel_mono is not None,
                })
                if self._cancel_mono is None:
                    self._data["status"] = "success" if final_results else "empty"
                    self._data["finished_at"] = _iso_now()
            self._persist()
        except Exception:
            pass

    def finish_error(self, code: str, error_type: str) -> None:
        try:
            now = float(self._clock())
            with self._lock:
                self._close_stage_locked(now)
                self._current_stage = "finished"
                if self._cancel_mono is None:
                    self._data["status"] = "error"
                self._data["finished_at"] = _iso_now()
                self._data["timings"]["total_ms"] = _milliseconds(now - self._started_mono)
                self._data["error"] = {
                    "code": str(code or "INTERNAL_ERROR")[:120],
                    "type": str(error_type or "Exception")[:120],
                }
                self._data["result"]["outcome"] = "ERROR"
            self._persist()
        except Exception:
            pass

    def worker_stopped(self, *, result_published: bool = False) -> None:
        try:
            now = float(self._clock())
            with self._lock:
                if self._data["timings"]["total_ms"] is None:
                    self._close_stage_locked(now)
                    self._current_stage = "finished"
                    self._data["timings"]["total_ms"] = _milliseconds(now - self._started_mono)
                if self._cancel_mono is not None:
                    self._data["status"] = "cancelled"
                    self._data["finished_at"] = _iso_now()
                    self._data["cancel"]["worker_stopped_at"] = _iso_now()
                    self._data["cancel"]["stop_latency_ms"] = _milliseconds(now - self._cancel_mono)
                    self._data["result"]["published_after_cancel"] = bool(result_published)
                    self._data["result"]["outcome"] = "CANCELLED"
            self._persist()
        except Exception:
            pass
