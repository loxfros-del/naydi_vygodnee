"""Privacy-safe daily market aggregates for Search Engine V2.

This module deliberately stores only anonymous daily price statistics.  It is
separate from the application's operational database and does not persist
queries, offer titles, links, sellers, cities, source names, or credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import median
from typing import Any, Iterable, Mapping
import unicodedata


MARKET_HISTORY_SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MARKET_HISTORY_PATH = Path("data") / "market_history.sqlite3"
DEFAULT_TREND_DAYS = 7
MIN_TREND_OBSERVATIONS = 3
MIN_TREND_CHANGE_PERCENT = 2.0


class MarketLane(str, Enum):
    """Markets that must never be compared as though they were equivalent."""

    NEW_RETAIL = "new_retail"
    NEW_MARKETPLACE = "new_marketplace"
    NEW_PRIVATE = "new_private"
    USED = "used"
    REFURBISHED = "refurbished"


MARKET_LANES = tuple(lane.value for lane in MarketLane)


@dataclass(frozen=True, slots=True)
class ComparableMarketOffer:
    """Minimal in-memory input for a market aggregate.

    ``source`` is used only to verify source diversity during the call; it is
    never written to SQLite.  A caller must explicitly mark an offer as
    comparable.  An offer with an observation date from another UTC day is not
    considered current for a daily snapshot.
    """

    price: float | int | None
    source: str
    comparable: bool = False
    current: bool = True
    observed_at: datetime | date | None = None


@dataclass(frozen=True, slots=True)
class DailyMarketAggregate:
    """A stored daily aggregate with no raw product or locality data."""

    identity_hash: str
    scope_hash: str
    lane: MarketLane
    observed_on: date
    offer_count: int
    source_count: int
    minimum_price: float
    median_price: float
    maximum_price: float


@dataclass(frozen=True, slots=True)
class MarketTrend:
    """A privacy-safe movement of one already-separated market lane.

    This intentionally contains no identity, scope, source, or price values.
    It is an internal aggregate result; callers can turn it into a concise
    customer label without exposing historical observations.
    """

    direction: str
    percent_change: float
    observed_days: int


def _normalized_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _canonical_value(value: Any) -> Any:
    """Produce deterministic JSON-compatible data for hashing only."""

    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            _normalized_text(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: _normalized_text(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical_value(item) for item in value]
        # Specifications are treated as an unordered identity/scope vocabulary.
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, str):
        return _normalized_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _normalized_text(value)


def canonical_hash(value: Any, *, namespace: str) -> str:
    """Return a stable hash and never expose/store the canonical raw value."""

    payload = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    prefix = f"nova-market-history-v{MARKET_HISTORY_SCHEMA_VERSION}:{namespace}:".encode("utf-8")
    return hashlib.sha256(prefix + payload).hexdigest()


def _as_utc_date(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    return value


def _as_lane(value: MarketLane | str) -> MarketLane | None:
    try:
        return value if isinstance(value, MarketLane) else MarketLane(str(value))
    except ValueError:
        return None


def _valid_price(value: float | int | None) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        price = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return price if price > 0 and math.isfinite(price) else None


def build_daily_aggregate(
    *,
    canonical_identity: Any,
    scope: Any,
    lane: MarketLane | str,
    offers: Iterable[ComparableMarketOffer],
    observed_on: date | datetime | None = None,
) -> DailyMarketAggregate | None:
    """Build an aggregate only when its evidence is strong enough to retain.

    At least three current, explicitly comparable prices from two independent
    sources are required.  The raw offers are discarded after this function
    returns.
    """

    normalized_lane = _as_lane(lane)
    snapshot_day = _as_utc_date(observed_on) or datetime.now(timezone.utc).date()
    if normalized_lane is None:
        return None

    prices: list[float] = []
    sources: set[str] = set()
    for offer in offers:
        if not isinstance(offer, ComparableMarketOffer):
            continue
        if not offer.comparable or not offer.current:
            continue
        offer_day = _as_utc_date(offer.observed_at)
        if offer_day is not None and offer_day != snapshot_day:
            continue
        price = _valid_price(offer.price)
        source = _normalized_text(offer.source)
        if price is None or not source:
            continue
        prices.append(price)
        sources.add(source)

    if len(prices) < 3 or len(sources) < 2:
        return None

    return DailyMarketAggregate(
        identity_hash=canonical_hash(canonical_identity, namespace="identity"),
        scope_hash=canonical_hash(scope, namespace="scope"),
        lane=normalized_lane,
        observed_on=snapshot_day,
        offer_count=len(prices),
        source_count=len(sources),
        minimum_price=min(prices),
        median_price=float(median(prices)),
        maximum_price=max(prices),
    )


def build_market_trend(
    aggregates: Iterable[DailyMarketAggregate],
    *,
    minimum_observations: int = MIN_TREND_OBSERVATIONS,
    minimum_change_percent: float = MIN_TREND_CHANGE_PERCENT,
) -> MarketTrend | None:
    """Return a movement only for enough observations of one exact market.

    A daily aggregate is already backed by three current comparable prices
    from two sources.  This second gate also needs multiple *days*, the same
    identity/scope/lane, and a material change so a single noisy update never
    becomes a customer-visible market claim.
    """

    try:
        minimum_count = max(2, int(minimum_observations))
        material_change = abs(float(minimum_change_percent))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(material_change):
        return None

    rows = [item for item in aggregates if isinstance(item, DailyMarketAggregate)]
    if len(rows) < minimum_count:
        return None
    rows.sort(key=lambda item: item.observed_on)
    first = rows[0]
    same_market = (first.identity_hash, first.scope_hash, first.lane)
    if any((item.identity_hash, item.scope_hash, item.lane) != same_market for item in rows[1:]):
        return None

    start = _valid_price(first.median_price)
    end = _valid_price(rows[-1].median_price)
    if start is None or end is None:
        return None
    change = ((end - start) / start) * 100
    if not math.isfinite(change) or abs(change) < material_change:
        return None
    observed_days = (rows[-1].observed_on - first.observed_on).days + 1
    if observed_days < minimum_count:
        return None
    return MarketTrend(
        direction="down" if change < 0 else "up",
        percent_change=round(abs(change), 1),
        observed_days=observed_days,
    )


class NullMarketHistoryStore:
    """No-op implementation for disabled history and safe fallbacks."""

    enabled = False

    def capture(self, **_: Any) -> DailyMarketAggregate | None:
        return None

    def history(self, **_: Any) -> list[DailyMarketAggregate]:
        return []

    def trend(self, **_: Any) -> MarketTrend | None:
        return None

    def prune(self, **_: Any) -> int:
        return 0


class MarketHistoryStore:
    """A best-effort, independent SQLite store for daily market aggregates."""

    enabled = True

    def __init__(
        self,
        path: str | Path = DEFAULT_MARKET_HISTORY_PATH,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self.path = Path(path)
        self.retention_days = max(1, int(retention_days))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.25)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 250")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_market_aggregates (
                identity_hash TEXT NOT NULL,
                scope_hash TEXT NOT NULL,
                lane TEXT NOT NULL,
                observed_on TEXT NOT NULL,
                offer_count INTEGER NOT NULL,
                source_count INTEGER NOT NULL,
                minimum_price REAL NOT NULL,
                median_price REAL NOT NULL,
                maximum_price REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (identity_hash, scope_hash, lane, observed_on)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_daily_market_aggregates_lookup
            ON daily_market_aggregates (identity_hash, scope_hash, lane, observed_on)
            """
        )
        return connection

    def _prune_connection(self, connection: sqlite3.Connection, reference_day: date) -> int:
        # Including the reference day, retain exactly ``retention_days`` daily
        # snapshots (today plus the preceding 89 days at the default of 90).
        cutoff = reference_day - timedelta(days=self.retention_days - 1)
        cursor = connection.execute(
            "DELETE FROM daily_market_aggregates WHERE observed_on < ?",
            (cutoff.isoformat(),),
        )
        return max(0, int(cursor.rowcount))

    def capture(
        self,
        *,
        canonical_identity: Any,
        scope: Any,
        lane: MarketLane | str,
        offers: Iterable[ComparableMarketOffer],
        observed_on: date | datetime | None = None,
    ) -> DailyMarketAggregate | None:
        """Best-effort aggregate upsert; any storage failure is non-fatal."""

        try:
            aggregate = build_daily_aggregate(
                canonical_identity=canonical_identity,
                scope=scope,
                lane=lane,
                offers=offers,
                observed_on=observed_on,
            )
            if aggregate is None:
                return None
            connection = self._connect()
            try:
                self._prune_connection(connection, datetime.now(timezone.utc).date())
                connection.execute(
                    """
                    INSERT INTO daily_market_aggregates (
                        identity_hash, scope_hash, lane, observed_on, offer_count,
                        source_count, minimum_price, median_price, maximum_price, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(identity_hash, scope_hash, lane, observed_on) DO UPDATE SET
                        offer_count = excluded.offer_count,
                        source_count = excluded.source_count,
                        minimum_price = excluded.minimum_price,
                        median_price = excluded.median_price,
                        maximum_price = excluded.maximum_price,
                        updated_at = excluded.updated_at
                    """,
                    (
                        aggregate.identity_hash,
                        aggregate.scope_hash,
                        aggregate.lane.value,
                        aggregate.observed_on.isoformat(),
                        aggregate.offer_count,
                        aggregate.source_count,
                        aggregate.minimum_price,
                        aggregate.median_price,
                        aggregate.maximum_price,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            return aggregate
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None

    def history(
        self,
        *,
        canonical_identity: Any,
        scope: Any,
        lane: MarketLane | str | None = None,
        since: date | datetime | None = None,
        until: date | datetime | None = None,
    ) -> list[DailyMarketAggregate]:
        """Return anonymous historical aggregates; storage failures return []."""

        try:
            identity_hash = canonical_hash(canonical_identity, namespace="identity")
            scope_hash = canonical_hash(scope, namespace="scope")
            normalized_lane = _as_lane(lane) if lane is not None else None
            if lane is not None and normalized_lane is None:
                return []
            clauses = ["identity_hash = ?", "scope_hash = ?"]
            values: list[Any] = [identity_hash, scope_hash]
            if normalized_lane is not None:
                clauses.append("lane = ?")
                values.append(normalized_lane.value)
            if (since_day := _as_utc_date(since)) is not None:
                clauses.append("observed_on >= ?")
                values.append(since_day.isoformat())
            if (until_day := _as_utc_date(until)) is not None:
                clauses.append("observed_on <= ?")
                values.append(until_day.isoformat())
            where = " AND ".join(clauses)
            connection = self._connect()
            try:
                self._prune_connection(connection, datetime.now(timezone.utc).date())
                rows = connection.execute(
                    f"""
                    SELECT identity_hash, scope_hash, lane, observed_on, offer_count,
                           source_count, minimum_price, median_price, maximum_price
                    FROM daily_market_aggregates
                    WHERE {where}
                    ORDER BY observed_on ASC, lane ASC
                    """,
                    values,
                ).fetchall()
                connection.commit()
            finally:
                connection.close()
            return [
                DailyMarketAggregate(
                    identity_hash=str(row["identity_hash"]),
                    scope_hash=str(row["scope_hash"]),
                    lane=MarketLane(str(row["lane"])),
                    observed_on=date.fromisoformat(str(row["observed_on"])),
                    offer_count=int(row["offer_count"]),
                    source_count=int(row["source_count"]),
                    minimum_price=float(row["minimum_price"]),
                    median_price=float(row["median_price"]),
                    maximum_price=float(row["maximum_price"]),
                )
                for row in rows
            ]
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return []

    def trend(
        self,
        *,
        canonical_identity: Any,
        scope: Any,
        lane: MarketLane | str,
        days: int = DEFAULT_TREND_DAYS,
        as_of: date | datetime | None = None,
    ) -> MarketTrend | None:
        """Read one bounded, anonymous market trend without creating history.

        Reading a fresh browser request must stay a no-op until the separate
        store has accumulated sufficient aggregates.  The trend query is
        deliberately read-only and cannot affect current savings or ranking.
        """

        try:
            normalized_lane = _as_lane(lane)
            window_days = int(days)
            reference_day = _as_utc_date(as_of) or datetime.now(timezone.utc).date()
            if normalized_lane is None or window_days < MIN_TREND_OBSERVATIONS or not self.path.is_file():
                return None
            rows = self.history(
                canonical_identity=canonical_identity,
                scope=scope,
                lane=normalized_lane,
                since=reference_day - timedelta(days=window_days - 1),
                until=reference_day,
            )
            return build_market_trend(rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None

    def prune(self, *, reference_day: date | datetime | None = None) -> int:
        """Delete aggregates older than the retention window without raising."""

        try:
            day = _as_utc_date(reference_day) or datetime.now(timezone.utc).date()
            connection = self._connect()
            try:
                deleted = self._prune_connection(connection, day)
                connection.commit()
                return deleted
            finally:
                connection.close()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return 0


__all__ = [
    "ComparableMarketOffer",
    "DEFAULT_MARKET_HISTORY_PATH",
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_TREND_DAYS",
    "DailyMarketAggregate",
    "MARKET_HISTORY_SCHEMA_VERSION",
    "MARKET_LANES",
    "MarketHistoryStore",
    "MarketLane",
    "MarketTrend",
    "MIN_TREND_CHANGE_PERCENT",
    "MIN_TREND_OBSERVATIONS",
    "NullMarketHistoryStore",
    "build_daily_aggregate",
    "build_market_trend",
    "canonical_hash",
]
