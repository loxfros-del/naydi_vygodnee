"""SQLite cache and resumable storage for search benchmarks.

The application database is intentionally not used here. This module stores only
normalised JSON benchmark diagnostics and candidate snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, TypeVar
from uuid import uuid4


CACHE_VERSION = "search-cache-v2"
DEFAULT_SUCCESS_TTL_SECONDS = 2 * 60 * 60
DEFAULT_EMPTY_TTL_SECONDS = 20 * 60
DEFAULT_NEGATIVE_TTL_SECONDS = 10 * 60
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "search_cache.sqlite3"

_SECRET_KEY_RE = re.compile(r"(?:token|secret|api[_-]?key|authorization|cookie|password)", re.I)
_LOCKED_MARKERS = ("database is locked", "database is busy", "locked")
_T = TypeVar("_T")


@dataclass(frozen=True)
class CacheLookup:
    state: str
    payload: dict[str, Any] | None = None
    created_at: int = 0
    expires_at: int = 0
    cache_age_seconds: int = 0
    is_stale: bool = False
    error_text: str = ""


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def normalize_budget(value: Any) -> str:
    text = normalize_text(value).replace(" ", "")
    match = re.fullmatch(r"(\d{1,3})[кk]", text)
    if match:
        return str(int(match.group(1)) * 1000)
    digits = "".join(char for char in text if char.isdigit())
    return str(int(digits)) if digits else ""


def sanitize_payload(value: Any) -> Any:
    """Remove credential-like JSON fields before serialising cache data."""
    if isinstance(value, dict):
        return {
            str(key): sanitize_payload(item)
            for key, item in value.items()
            if not _SECRET_KEY_RE.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def ttl_for_status(status: str) -> int:
    normal = normalize_text(status)
    if normal in {"ok", "success", "partial_success", "done", "cache_hit"}:
        return DEFAULT_SUCCESS_TTL_SECONDS
    if normal in {"empty", "no_results"}:
        return DEFAULT_EMPTY_TTL_SECONDS
    if any(marker in normal for marker in ("401", "403", "429", "498", "captcha", "timeout", "non-json", "error")):
        return DEFAULT_NEGATIVE_TTL_SECONDS
    return DEFAULT_NEGATIVE_TTL_SECONDS


class SearchCache:
    """Small cache database with a fresh connection for every public operation."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        cache_version: str = CACHE_VERSION,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path or DEFAULT_CACHE_PATH)
        self.cache_version = cache_version
        self.timeout_seconds = timeout_seconds

    def make_cache_key(
        self,
        *,
        stage: str,
        source: str,
        query: str,
        category: str = "",
        city: str = "",
        budget: Any = "",
        use_case: str = "",
        is_used_allowed: bool = False,
        cache_version: str | None = None,
    ) -> str:
        context = {
            "stage": normalize_text(stage),
            "source": normalize_text(source),
            "query": normalize_text(query),
            "category": normalize_text(category),
            "city": normalize_text(city),
            "budget": normalize_budget(budget),
            "use_case": normalize_text(use_case),
            "is_used_allowed": bool(is_used_allowed),
            "cache_version": cache_version or self.cache_version,
        }
        raw = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(raw.encode("utf-8")).hexdigest()

    def get(self, cache_key: str, *, cache_version: str | None = None) -> CacheLookup:
        expected_version = cache_version or self.cache_version

        def operation(conn: sqlite3.Connection) -> CacheLookup:
            row = conn.execute(
                "SELECT payload_json, created_at, expires_at, cache_version, error_text "
                "FROM cache_entries WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return CacheLookup("CACHE_MISS")
            if str(row["cache_version"]) != expected_version:
                return CacheLookup("CACHE_VERSION_MISMATCH")
            now = int(time.time())
            age = max(0, now - int(row["created_at"]))
            if int(row["expires_at"]) <= now:
                return CacheLookup(
                    "CACHE_EXPIRED",
                    created_at=int(row["created_at"]),
                    expires_at=int(row["expires_at"]),
                    cache_age_seconds=age,
                    is_stale=True,
                    error_text=str(row["error_text"] or ""),
                )
            try:
                payload = json.loads(str(row["payload_json"]))
            except (TypeError, json.JSONDecodeError):
                return CacheLookup("CACHE_CORRUPT", error_text="payload_json is invalid")
            if not isinstance(payload, dict):
                return CacheLookup("CACHE_CORRUPT", error_text="payload_json is not an object")
            return CacheLookup(
                "HIT",
                payload=payload,
                created_at=int(row["created_at"]),
                expires_at=int(row["expires_at"]),
                cache_age_seconds=age,
            )

        return self._run(operation)

    def put(
        self,
        *,
        cache_key: str,
        stage: str,
        query: str,
        source: str,
        payload: dict[str, Any],
        status: str,
        ttl_seconds: int | None = None,
        duration_ms: int | None = None,
        error_text: str = "",
        cache_version: str | None = None,
    ) -> None:
        now = int(time.time())
        ttl = max(1, int(ttl_seconds if ttl_seconds is not None else ttl_for_status(status)))
        payload_json = json.dumps(sanitize_payload(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = cache_version or self.cache_version

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO cache_entries (
                    cache_key, stage, query, source, payload_json, status,
                    created_at, expires_at, cache_version, duration_ms, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    stage=excluded.stage,
                    query=excluded.query,
                    source=excluded.source,
                    payload_json=excluded.payload_json,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at,
                    cache_version=excluded.cache_version,
                    duration_ms=excluded.duration_ms,
                    error_text=excluded.error_text
                """,
                (
                    cache_key, stage, query, source, payload_json, status, now, now + ttl,
                    version, duration_ms, error_text[:1000],
                ),
            )

        self._run(operation)

    def purge_expired(self, *, now: int | None = None) -> int:
        cutoff = int(now if now is not None else time.time())

        def operation(conn: sqlite3.Connection) -> int:
            result = conn.execute("DELETE FROM cache_entries WHERE expires_at <= ?", (cutoff,))
            return max(0, int(result.rowcount))

        return self._run(operation)

    def cache_stats(self) -> dict[str, Any]:
        now = int(time.time())

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            total = int(conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0])
            expired = int(conn.execute("SELECT COUNT(*) FROM cache_entries WHERE expires_at <= ?", (now,)).fetchone()[0])
            runs = int(conn.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0])
            cases = int(conn.execute("SELECT COUNT(*) FROM benchmark_cases").fetchone()[0])
            source_entries = int(conn.execute("SELECT COUNT(*) FROM cache_entries WHERE stage = 'source'").fetchone()[0])
            snapshots = int(conn.execute("SELECT COUNT(*) FROM cache_entries WHERE stage = 'benchmark_snapshot'").fetchone()[0])
            cache_rows = conn.execute("SELECT status, COUNT(*) AS count FROM cache_entries GROUP BY status").fetchall()
            cache_details = conn.execute("SELECT status, error_text, expires_at FROM cache_entries").fetchall()
            case_rows = conn.execute("SELECT status, COUNT(*) AS count FROM benchmark_cases GROUP BY status").fetchall()
            run_rows = conn.execute(
                "SELECT run_id, COUNT(*) AS count FROM benchmark_cases GROUP BY run_id ORDER BY MAX(finished_at) DESC"
            ).fetchall()
            last_run = conn.execute("SELECT run_id FROM benchmark_runs ORDER BY started_at DESC LIMIT 1").fetchone()
            grouped_cache = {str(row["status"]): int(row["count"]) for row in cache_rows}
            grouped_cases = {str(row["status"]): int(row["count"]) for row in case_rows}
            cache_buckets = {"SUCCESS": 0, "EMPTY": 0, "BLOCKED": 0, "TIMEOUT": 0, "EXPIRED": 0, "ERROR": 0}
            for row in cache_details:
                status_text = f"{row['status']} {row['error_text'] or ''}".lower()
                if int(row["expires_at"]) <= now:
                    cache_buckets["EXPIRED"] += 1
                elif any(marker in status_text for marker in ("401", "403", "429", "498", "captcha", "blocked", "rate_limit")):
                    cache_buckets["BLOCKED"] += 1
                elif "timeout" in status_text:
                    cache_buckets["TIMEOUT"] += 1
                elif "empty" in status_text:
                    cache_buckets["EMPTY"] += 1
                elif any(marker in status_text for marker in ("ok", "success", "done", "cache_hit")):
                    cache_buckets["SUCCESS"] += 1
                else:
                    cache_buckets["ERROR"] += 1
            return {
                "entries": total,
                "fresh_entries": total - expired,
                "expired_entries": expired,
                "benchmark_runs": runs,
                "benchmark_cases": cases,
                "source_cache_entries": source_entries,
                "benchmark_snapshots": snapshots,
                "cache_entry_statuses": grouped_cache,
                "cache_entry_buckets": cache_buckets,
                "benchmark_case_statuses": grouped_cases,
                "benchmark_cases_by_run": {str(row["run_id"]): int(row["count"]) for row in run_rows},
                "last_run_id": str(last_run["run_id"]) if last_run else "",
                "unique_run_ids": runs,
                "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            }

        return self._run(operation)

    def start_benchmark_run(
        self,
        *,
        mode: str,
        total_cases: int,
        code_version: str = CACHE_VERSION,
        run_id: str | None = None,
    ) -> str:
        result_id = run_id or uuid4().hex
        now = int(time.time())

        def operation(conn: sqlite3.Connection) -> str:
            conn.execute(
                """
                INSERT INTO benchmark_runs (
                    run_id, started_at, finished_at, mode, code_version,
                    total_cases, completed_cases, summary_json, interrupted
                ) VALUES (?, ?, NULL, ?, ?, ?, 0, '{}', 0)
                ON CONFLICT(run_id) DO UPDATE SET interrupted=0
                """,
                (result_id, now, mode, code_version, total_cases),
            )
            return result_id

        return self._run(operation)

    def save_benchmark_case(
        self,
        *,
        run_id: str,
        case_id: str,
        query: str,
        started_at: int,
        finished_at: int,
        duration_ms: int,
        status: str,
        result: dict[str, Any] | None = None,
        error_text: str = "",
    ) -> None:
        result_json = json.dumps(sanitize_payload(result or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO benchmark_cases (
                    run_id, case_id, query, started_at, finished_at, duration_ms,
                    status, result_json, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, case_id) DO UPDATE SET
                    query=excluded.query,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    duration_ms=excluded.duration_ms,
                    status=excluded.status,
                    result_json=excluded.result_json,
                    error_text=excluded.error_text
                """,
                (run_id, case_id, query, started_at, finished_at, duration_ms, status, result_json, error_text[:1000]),
            )
            conn.execute(
                """
                UPDATE benchmark_runs
                SET completed_cases = (
                    SELECT COUNT(*) FROM benchmark_cases
                    WHERE run_id = ? AND finished_at IS NOT NULL
                )
                WHERE run_id = ?
                """,
                (run_id, run_id),
            )

        self._run(operation)

    def completed_case_ids(self, run_id: str) -> set[str]:
        def operation(conn: sqlite3.Connection) -> set[str]:
            rows = conn.execute(
                """
                SELECT case_id FROM benchmark_cases
                WHERE run_id = ? AND finished_at IS NOT NULL
                  AND status IN ('DONE', 'SUCCESS', 'CACHE_HIT', 'EMPTY')
                """,
                (run_id,),
            ).fetchall()
            return {str(row["case_id"]) for row in rows}

        return self._run(operation)

    def get_benchmark_case(self, run_id: str, case_id: str) -> dict[str, Any] | None:
        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute(
                "SELECT * FROM benchmark_cases WHERE run_id = ? AND case_id = ?",
                (run_id, case_id),
            ).fetchone()
            if row is None:
                return None
            try:
                result = json.loads(str(row["result_json"] or "{}"))
            except json.JSONDecodeError:
                result = {}
            return {**dict(row), "result": result}

        return self._run(operation)

    def finish_benchmark_run(
        self,
        run_id: str,
        *,
        summary: dict[str, Any],
        interrupted: bool = False,
    ) -> None:
        now = int(time.time())
        summary_json = json.dumps(sanitize_payload(summary), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE benchmark_runs SET finished_at = ?, summary_json = ?, interrupted = ? WHERE run_id = ?",
                (now, summary_json, int(interrupted), run_id),
            )

        self._run(operation)

    def get_benchmark_run(self, run_id: str) -> dict[str, Any] | None:
        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute("SELECT * FROM benchmark_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            try:
                summary = json.loads(str(row["summary_json"] or "{}"))
            except json.JSONDecodeError:
                summary = {}
            return {**dict(row), "summary": summary}

        return self._run(operation)

    def _run(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        last_error: Exception | None = None
        for attempt in range(3):
            conn: sqlite3.Connection | None = None
            try:
                conn = self._connect()
                result = operation(conn)
                conn.commit()
                return result
            except sqlite3.OperationalError as exc:
                if not any(marker in str(exc).lower() for marker in _LOCKED_MARKERS) or attempt == 2:
                    raise
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
            finally:
                if conn is not None:
                    conn.close()
        raise RuntimeError("search cache remained locked") from last_error

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return self._open_connection()
        except sqlite3.DatabaseError:
            self._recover_corrupt_database()
            return self._open_connection()

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=self.timeout_seconds)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    query TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    cache_version TEXT NOT NULL,
                    duration_ms INTEGER,
                    error_text TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_entries_expires_at ON cache_entries(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_entries_stage_source ON cache_entries(stage, source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_entries_created_at ON cache_entries(created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at INTEGER,
                    finished_at INTEGER,
                    mode TEXT,
                    code_version TEXT,
                    total_cases INTEGER,
                    completed_cases INTEGER,
                    summary_json TEXT,
                    interrupted INTEGER DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_cases (
                    run_id TEXT,
                    case_id TEXT,
                    query TEXT,
                    started_at INTEGER,
                    finished_at INTEGER,
                    duration_ms INTEGER,
                    status TEXT,
                    result_json TEXT,
                    error_text TEXT,
                    PRIMARY KEY(run_id, case_id)
                )
                """
            )
            return conn
        except Exception:
            conn.close()
            raise

    def _recover_corrupt_database(self) -> None:
        if not self.path.exists():
            return
        suffix = int(time.time())
        corrupt_path = self.path.with_name(f"{self.path.name}.corrupt-{suffix}")
        self.path.replace(corrupt_path)
        for sidecar in (self.path.with_name(f"{self.path.name}-wal"), self.path.with_name(f"{self.path.name}-shm")):
            if sidecar.exists():
                sidecar.replace(sidecar.with_name(f"{sidecar.name}.corrupt-{suffix}"))
