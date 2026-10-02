"""Persistent pre-start spending reservations, isolated from application databases."""
from __future__ import annotations

from contextlib import contextmanager
from calendar import monthrange
from datetime import date as Date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from typing import Any, Iterable, Mapping

from .errors import ExternalServiceError


_MOSCOW = timezone(timedelta(hours=3))
_UNITS_PER_USD = 100_000_000


def _now() -> datetime:
    return datetime.now(_MOSCOW)


def _error(message: str, code: str = "APIFY_SPEND_LIMIT") -> ExternalServiceError:
    return ExternalServiceError(message, code=code, retryable=False)


def _units(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise _error("Некорректная сумма расходов.", "APIFY_SPEND_STATE_INVALID")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation
        return int((amount * _UNITS_PER_USD).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise _error("Некорректная сумма расходов.", "APIFY_SPEND_STATE_INVALID") from None


def _local_date(value: str | datetime | Date) -> str:
    try:
        if isinstance(value, datetime):
            stamp = value
        elif isinstance(value, Date):
            return value.isoformat()
        elif len(value) == 10:
            return Date.fromisoformat(value).isoformat()
        else:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        return stamp.astimezone(_MOSCOW).date().isoformat()
    except (AttributeError, TypeError, ValueError):
        raise _error("У расхода отсутствует корректная дата.", "APIFY_SPEND_STATE_INVALID") from None


class SpendingGuard:
    """Reserve before every paid start and reconcile every terminal attempt."""

    def __init__(self, path: Path | str, daily_limit_usd: float = 1, monthly_limit_usd: float = 18,
                 billing_cycle_day: int = 1) -> None:
        if isinstance(billing_cycle_day, bool) or not isinstance(billing_cycle_day, int) or not 1 <= billing_cycle_day <= 31:
            raise _error("Некорректный день начала расчётного периода.", "APIFY_SPEND_STATE_INVALID")
        self.path = Path(path)
        self.daily_limit_units = _units(daily_limit_usd)
        self.monthly_limit_units = _units(monthly_limit_usd)
        self.billing_cycle_day = billing_cycle_day

    @contextmanager
    def _locked(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.path.with_name(self.path.name + ".lock").open("a+b")
        except OSError:
            raise _error("Не удалось открыть защиту расходов. Платный запуск запрещён.", "APIFY_SPEND_STATE_INVALID") from None
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                # Windows permits byte-range locks beyond EOF. Writing a
                # bootstrap byte before acquiring the lock can race another
                # process and raise PermissionError outside the retry loop.
            else:
                import fcntl
            deadline = time.monotonic() + 2.0
            while not acquired:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise _error("Защита расходов занята. Платный запуск запрещён.", "APIFY_SPEND_LOCKED") from None
                    time.sleep(0.02)
            yield
        finally:
            if acquired:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
            else:
                stream.close()

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                state = json.load(stream)
        except FileNotFoundError:
            return {"schema": 1, "records": {}}
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise _error("Журнал расходов повреждён или недоступен. Платный запуск запрещён.", "APIFY_SPEND_STATE_INVALID") from None
        if not isinstance(state, dict) or state.get("schema") != 1 or not isinstance(state.get("records"), dict):
            raise _error("Некорректный журнал расходов. Платный запуск запрещён.", "APIFY_SPEND_STATE_INVALID")
        for key, record in state["records"].items():
            if not isinstance(key, str) or not isinstance(record, dict):
                raise _error("Некорректная запись расходов.", "APIFY_SPEND_STATE_INVALID")
            for name in ("reserved_units", "settled_units"):
                value = record.get(name)
                if name == "settled_units" and value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise _error("Некорректная сумма в журнале расходов.", "APIFY_SPEND_STATE_INVALID")
            if _local_date(record.get("date")) != record.get("date"):
                raise _error("Некорректная дата в журнале расходов.", "APIFY_SPEND_STATE_INVALID")
            kind = record.get("settlement_kind")
            if kind is not None and kind not in {"actual", "estimate"}:
                raise _error("Некорректный тип расхода.", "APIFY_SPEND_STATE_INVALID")
            if record.get("settled_units") is None and kind is not None:
                raise _error("Активный резерв не может быть расходом.", "APIFY_SPEND_STATE_INVALID")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(state, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise _error("Не удалось сохранить защиту расходов. Платный запуск запрещён.", "APIFY_SPEND_STATE_INVALID") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _period(self, day: str) -> tuple[str, str]:
        current = Date.fromisoformat(day)
        year, month = current.year, current.month
        def anchor(year: int, month: int) -> Date:
            return Date(year, month, min(self.billing_cycle_day, monthrange(year, month)[1]))
        start = anchor(year, month)
        if current < start:
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
            start = anchor(year, month)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        return start.isoformat(), anchor(next_year, next_month).isoformat()

    def _totals(self, state: dict[str, Any], day: str) -> tuple[int, int]:
        daily = monthly = 0
        period_start, period_end = self._period(day)
        for record in state["records"].values():
            amount = record["reserved_units"] if record["settled_units"] is None else record["settled_units"]
            if record["date"] == day:
                daily += amount
            if period_start <= record["date"] < period_end:
                monthly += amount
        return daily, monthly

    def _breakdown(self, state: dict[str, Any], day: str) -> tuple[int, int, int]:
        actual = estimate = active = 0
        for record in state["records"].values():
            if record["date"] != day:
                continue
            settled = record["settled_units"]
            if settled is None:
                active += record["reserved_units"]
            elif record.get("settlement_kind", "actual") == "estimate":
                estimate += settled
            else:
                actual += settled
        return actual, estimate, active

    def reserve(self, max_charge_usd: float) -> str:
        amount = _units(max_charge_usd)
        if amount <= 0:
            raise _error("Для платного запуска нужен положительный резерв.")
        with self._locked():
            state = self._read()
            day = _local_date(_now())
            daily, monthly = self._totals(state, day)
            if daily + amount > self.daily_limit_units or monthly + amount > self.monthly_limit_units:
                raise _error("Достигнут дневной или месячный лимит расходов Apify. Новый платный запуск запрещён.")
            reservation_id = "reservation:" + secrets.token_hex(16)
            state["records"][reservation_id] = {"date": day, "reserved_units": amount, "settled_units": None}
            self._write(state)
            return reservation_id

    def settle(self, reservation_id: str, actual_cost_usd: float | None, final: bool = True) -> None:
        actual = _units(actual_cost_usd) if actual_cost_usd is not None else None
        if final and actual is None:
            raise _error("Окончательная стоимость запуска неизвестна.", "APIFY_SPEND_STATE_INVALID")
        with self._locked():
            state = self._read()
            record = state["records"].get(reservation_id)
            if record is None:
                raise _error("Резерв расходов не найден.", "APIFY_SPEND_STATE_INVALID")
            if final:
                previous = record["settled_units"] or 0
                # A final receipt replaces a conservative estimate. Once an
                # actual receipt exists, only an upward revision is accepted.
                record["settled_units"] = (
                    max(previous, actual)
                    if record.get("settlement_kind", "actual") == "actual"
                    and record["settled_units"] is not None
                    else actual
                )
                record["settlement_kind"] = "actual"
            elif record.get("settlement_kind") != "actual":
                # Unknown billing is terminal for the job, not an eternally
                # active reservation. Keep the old fail-closed amount as a
                # settled estimate until an exact receipt can replace it.
                record["settled_units"] = max(
                    record["reserved_units"], record["settled_units"] or 0, actual or 0,
                )
                record["settlement_kind"] = "estimate"
            self._write(state)

    def release_unbilled(self, reservation_id: str) -> None:
        """Remove a reservation only after the caller proves no paid run exists.

        An estimate can be corrected after independent provider verification;
        an actual receipt can never be erased through this method.
        """
        with self._locked():
            state = self._read()
            record = state["records"].get(reservation_id)
            if record is None or record.get("settlement_kind") == "actual":
                raise _error("Нельзя освободить подтверждённый или отсутствующий расход.",
                             "APIFY_SPEND_STATE_INVALID")
            del state["records"][reservation_id]
            self._write(state)

    def record_existing(self, run_id: str, actual_cost_usd: float, timestamp: str | datetime | Date) -> None:
        self.seed_initial_spend([{"run_id": run_id, "actual_cost_usd": actual_cost_usd, "timestamp": timestamp}])

    def seed_initial_spend(self, entries: Iterable[Mapping[str, Any]]) -> None:
        prepared = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise _error("Некорректная запись начальных расходов.", "APIFY_SPEND_STATE_INVALID")
            run_id = entry.get("run_id")
            if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 200:
                raise _error("У расхода отсутствует идентификатор запуска.", "APIFY_SPEND_STATE_INVALID")
            prepared.append(("run:" + run_id, _units(entry.get("actual_cost_usd")), _local_date(entry.get("timestamp"))))
        with self._locked():
            state = self._read()
            for key, amount, day in prepared:
                existing = state["records"].get(key)
                if existing is None:
                    state["records"][key] = {
                        "date": day, "reserved_units": amount, "settled_units": amount,
                        "settlement_kind": "actual",
                    }
                else:
                    existing["settled_units"] = max(existing["settled_units"] or existing["reserved_units"], amount)
                    existing["settlement_kind"] = "actual"
            self._write(state)

    def snapshot(self) -> dict[str, float | str]:
        with self._locked():
            state = self._read()
            day = _local_date(_now())
            daily, monthly = self._totals(state, day)
            actual, estimate, active = self._breakdown(state, day)
            period_start, period_end = self._period(day)
            return {"date": day, "dailyCommittedUsd": daily / _UNITS_PER_USD,
                    "monthlyCommittedUsd": monthly / _UNITS_PER_USD,
                    "actualSpendUsd": actual / _UNITS_PER_USD,
                    "settledEstimateUsd": estimate / _UNITS_PER_USD,
                    "activeReservationUsd": active / _UNITS_PER_USD,
                    "dailyLimitUsd": self.daily_limit_units / _UNITS_PER_USD,
                    "monthlyLimitUsd": self.monthly_limit_units / _UNITS_PER_USD,
                    "monthlyPeriodStart": period_start,
                    "monthlyPeriodEndExclusive": period_end}
