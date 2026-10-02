"""Small in-process job registry for truthful browser search progress."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from threading import Lock, Thread
import secrets
import time
from typing import Any

from .errors import AvitoServiceError, SearchCancelledError
from .models import SearchRequest
from .service import AvitoAnalysisService
from .telemetry import PilotTraceStore, SearchTrace


SEARCH_SOFT_TARGET_SECONDS = 45
WORKER_DEADLINE_SECONDS = 360
SEARCH_DEADLINE_SECONDS = WORKER_DEADLINE_SECONDS + 5


@dataclass(slots=True)
class SearchJob:
    job_id: str
    started_at: float
    deadline_at: float
    created_at: float = field(default_factory=time.time)
    state: str = "running"
    stage: str = "queued"
    message: str = "Запрос принят. Запускаем поиск…"
    percent: int = 2
    result: dict[str, Any] | None = None
    admin_result: dict[str, Any] | None = None
    error: str = ""
    error_code: str = ""
    retryable: bool = True
    worker_finished: bool = False
    owner_only: bool = False
    trace: SearchTrace | None = field(default=None, repr=False)
    updated_at: float = field(default_factory=time.monotonic)
    lock: Lock = field(default_factory=Lock, repr=False)

    def progress(self, stage: str, message: str, percent: int) -> None:
        with self.lock:
            if self.state == "cancelled":
                raise SearchCancelledError()
            if self.state != "running":
                return
            self.stage = stage
            self.message = message[:240]
            self.percent = max(self.percent, min(99, max(0, percent)))
            self.updated_at = time.monotonic()

    def cancel_requested(self) -> bool:
        with self.lock:
            return self.state == "cancelled"

    def cancel(self) -> None:
        cancelled = False
        stage = ""
        with self.lock:
            if self.worker_finished or self.state in {"complete", "error", "cancelled"}:
                return
            stage = self.stage
            self.state = "cancelled"
            self.stage = "cancelled"
            self.percent = 100
            self.error = "Поиск остановлен."
            self.error_code = "SEARCH_CANCELLED"
            self.retryable = False
            self.message = self.error
            self.result = None
            self.admin_result = None
            self.updated_at = time.monotonic()
            cancelled = True
        if cancelled and self.trace is not None:
            self.trace.request_cancel(stage)

    def snapshot(self, *, include_admin: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self.lock:
            if self.state == "running" and now >= self.deadline_at:
                self.state = "timeout"
                self.stage = "timeout"
                self.percent = 100
                self.error = (
                    "Проверка превысила время ожидания. Если сервер завершит её, результат сохранится по номеру поиска."
                )
                self.error_code = "SEARCH_TIMEOUT"
                self.retryable = True
                self.message = self.error
            payload: dict[str, Any] = {
                "jobId": self.job_id,
                "createdAt": datetime.fromtimestamp(self.created_at, timezone.utc).isoformat(),
                "state": self.state,
                "stage": self.stage,
                "message": self.message,
                "percent": self.percent,
                "elapsedSeconds": round(min(now, self.deadline_at) - self.started_at, 1),
                "remainingSeconds": max(0, int(self.deadline_at - now + 0.999)),
                "softTargetExceeded": now - self.started_at >= SEARCH_SOFT_TARGET_SECONDS,
                "alive": self.state == "running" and now - self.updated_at < 20,
                "workerAlive": not self.worker_finished,
            }
            if self.error:
                payload["error"] = self.error
                payload["errorCode"] = self.error_code or "SEARCH_ERROR"
                payload["retryable"] = self.retryable
            if self.result is not None:
                payload["result"] = self.admin_result if include_admin and self.admin_result is not None else self.result
            return payload


class SearchJobRegistry:
    def __init__(
        self,
        service: AvitoAnalysisService,
        *,
        max_active: int = 2,
        max_starts_per_hour: int = 20,
        trace_store: PilotTraceStore | None = None,
    ) -> None:
        self.service = service
        self.max_active = max(1, max_active)
        self._jobs: dict[str, SearchJob] = {}
        self._request_jobs: dict[tuple[SearchRequest, str], str] = {}
        self.max_starts_per_hour = max(1, max_starts_per_hour)
        self._starts: deque[float] = deque()
        self._lock = Lock()
        self.trace_store = trace_store

    def _cleanup(self) -> None:
        cutoff = time.monotonic() - 900
        self._jobs = {
            job_id: job for job_id, job in self._jobs.items()
            if job.updated_at >= cutoff or not job.worker_finished
        }
        self._request_jobs = {
            search: job_id for search, job_id in self._request_jobs.items()
            if job_id in self._jobs
        }

    def start(self, search: SearchRequest, *, parse_ms: int | float | None = None) -> SearchJob:
        return self._start(search, parse_ms=parse_ms)

    def start_dataset(
        self, search: SearchRequest, listings: Any, *, parse_ms: int | float | None = None,
    ) -> SearchJob:
        if not isinstance(listings, list) or not listings:
            raise ValueError("Dataset должен содержать непустой массив объявлений.")
        digest = hashlib.sha256(json.dumps(listings, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return self._start(search, listings=listings, source_key=digest, parse_ms=parse_ms)

    def _start(
        self,
        search: SearchRequest,
        *,
        listings: list[Any] | None = None,
        source_key: str = "search",
        parse_ms: int | float | None = None,
    ) -> SearchJob:
        key = (search, source_key)
        with self._lock:
            self._cleanup()
            existing_id = self._request_jobs.get(key)
            existing = self._jobs.get(existing_id or "")
            if existing is not None and not existing.worker_finished:
                if existing.trace is not None:
                    existing.trace.dedup_hit()
                return existing
            active = sum(not job.worker_finished for job in self._jobs.values())
            if active >= self.max_active:
                raise AvitoServiceError(
                    "Уже выполняется поиск. Дождитесь его завершения перед новым запросом.",
                    code="SEARCH_BUSY",
                )
            now = time.monotonic()
            while self._starts and self._starts[0] <= now - 3600:
                self._starts.popleft()
            if len(self._starts) >= self.max_starts_per_hour:
                raise AvitoServiceError(
                    "Часовой лимит поиска исчерпан. Попробуйте позже.",
                    code="SEARCH_RATE_LIMIT",
                )
            job_id = secrets.token_urlsafe(12)
            trace = SearchTrace(
                job_id, search, store=self.trace_store, parse_ms=parse_ms,
                usd_rub_rate=getattr(self.service, "usd_rub_rate", 100.0),
            )
            job = SearchJob(
                job_id=job_id,
                started_at=now,
                deadline_at=now + SEARCH_DEADLINE_SECONDS,
                owner_only=listings is not None,
                trace=trace,
            )
            self._jobs[job.job_id] = job
            self._request_jobs[key] = job.job_id
            self._starts.append(now)
        Thread(target=self._run, args=(job, search, listings), daemon=True).start()
        return job

    def _run(self, job: SearchJob, search: SearchRequest, listings: list[Any] | None = None) -> None:
        def emit_progress(stage: str, message: str, percent: int) -> None:
            job.progress(stage, message, percent)
            if job.trace is not None:
                job.trace.stage(stage)

        emit_progress.cancel_requested = job.cancel_requested  # type: ignore[attr-defined]
        emit_progress.trace = job.trace  # type: ignore[attr-defined]
        try:
            if listings is None:
                report = self.service.search(
                    search, deadline_seconds=WORKER_DEADLINE_SECONDS, progress=emit_progress,
                )
            else:
                report = self.service.analyze_dataset(
                    listings, search, deadline_at=job.started_at + WORKER_DEADLINE_SECONDS,
                    started_at=job.started_at, progress=emit_progress,
                )
            if job.trace is not None:
                job.trace.stage("response")
            response_started = time.monotonic()
            result = report.public_dict()
            admin = report.public_dict(include_admin=True)
            owner_payload = dict(result)
            for name in ("adminCosts", "adminAudit", "adminRecommendations", "adminDiscoveredListings"):
                if name in admin:
                    owner_payload[name] = admin[name]
            owner_payload["adminWarnings"] = list(report.admin_warnings)
            if listings is not None:
                owner_payload["preview"] = True
            response_preparation_ms = round((time.monotonic() - response_started) * 1000)
            with job.lock:
                if job.state == "cancelled":
                    return
                # A browser wait limit must never discard a finished report.
                job.result = result
                job.admin_result = owner_payload
                job.error = ""
                job.error_code = ""
                job.state = "complete"
                job.stage = "complete"
                job.percent = 100
                job.message = (f"Поиск завершён. Найдено: {result.get('visibleCount', len(result.get('recommendations', [])))}; "
                               f"проверенных рекомендаций: {result.get('verifiedCount', len(result.get('recommendations', [])))}.")
                job.updated_at = time.monotonic()
            if job.trace is not None:
                job.trace.finish_report(
                    report,
                    response_preparation_ms=response_preparation_ms,
                    usd_rub_rate=getattr(self.service, "usd_rub_rate", 100.0),
                )
        except (ValueError, AvitoServiceError) as exc:
            message = str(exc)[:300]
            with job.lock:
                if job.state in {"running", "timeout"}:
                    job.state = "error"
                    job.stage = "error"
                    job.percent = 100
                    job.error = message
                    job.error_code = getattr(exc, "code", "INVALID_REQUEST")
                    job.retryable = getattr(exc, "retryable", False)
                    job.message = message
                    job.updated_at = time.monotonic()
            if job.trace is not None and not isinstance(exc, SearchCancelledError):
                job.trace.finish_error(getattr(exc, "code", "INVALID_REQUEST"), type(exc).__name__)
        except Exception:
            with job.lock:
                if job.state in {"running", "timeout"}:
                    job.state = "error"
                    job.stage = "error"
                    job.percent = 100
                    job.error = "Внутренняя ошибка поиска. Повторите запрос."
                    job.error_code = "INTERNAL_ERROR"
                    job.retryable = True
                    job.message = job.error
                    job.updated_at = time.monotonic()
            if job.trace is not None:
                job.trace.finish_error("INTERNAL_ERROR", "Exception")
        finally:
            with job.lock:
                job.worker_finished = True
                job.updated_at = time.monotonic()
                result_published = job.result is not None
            if job.trace is not None:
                job.trace.worker_stopped(result_published=result_published)

    def get(self, job_id: str) -> SearchJob | None:
        with self._lock:
            self._cleanup()
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> SearchJob | None:
        with self._lock:
            self._cleanup()
            job = self._jobs.get(job_id)
        if job is not None:
            job.cancel()
        return job
