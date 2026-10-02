"""Offline comparable-market snapshots built once per normalized SKU."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from statistics import median
from typing import Iterable

from .models import AnalyzedListing, NormalizedListing
from .normalization import _city_key
from .risk_rules import evaluate_rules


@dataclass(frozen=True, slots=True)
class NormalizedSku:
    model: str
    variant: str
    storage: str
    condition: str

    @property
    def key(self) -> str:
        return "|".join((self.model, self.variant, self.storage, self.condition))


@dataclass(frozen=True, slots=True)
class MarketSegment:
    seller_kind: str
    listing_count: int
    seller_count: int
    stable_seller_count: int
    median: int
    p25: int
    p75: int
    minimum_sane_price: int
    listing_ids: tuple[str, ...]
    prices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    sku: NormalizedSku
    city: str
    observed_date: str
    segments: tuple[MarketSegment, ...]
    excluded_count: int = 0

    def segment(self, seller_kind: str) -> MarketSegment | None:
        return next((item for item in self.segments if item.seller_kind == seller_kind), None)

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


def normalized_sku(listing: NormalizedListing) -> NormalizedSku | None:
    truth = listing.price_truth
    if not truth.model or not truth.storage or truth.condition.startswith("conflict:"):
        return None
    variant_tokens = set(truth.variant.split())
    if len(variant_tokens & {"slim", "pro", "fat"}) > 1 or {"digital", "disc"} <= variant_tokens:
        return None
    condition = truth.condition
    if condition in {"excellent", "good", "used"}:
        condition = "used"
    if condition not in {"new", "used"}:
        return None
    return NormalizedSku(
        model=truth.model,
        variant=truth.variant,
        storage=truth.storage,
        condition=condition,
    )


def _percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return int(round(ordered[lower] * (1 - weight) + ordered[upper] * weight))


def _market_eligible(listing: NormalizedListing, city: str) -> bool:
    if _city_key(listing.location or listing.address) != _city_key(city):
        return False
    if listing.currency.upper() != "RUB" or listing.price_confidence != "exact" or listing.acquisition_price is None:
        return False
    text = f"{listing.description} {listing.repair_status}".casefold().replace("ё", "е")
    if re.search(r"ремонт|восстанов|refurb|рефаб|на\s+запчаст|trade[- ]?in|трейд[- ]?ин|обмен", text):
        return False
    blocked = {"CONDITION_CONFLICT", "NAND_DEFECT", "HARDWARE_DEFECT", "NON_FINAL_PRICE", "MISSING_DESCRIPTION"}
    return not blocked.intersection(finding.code for finding in evaluate_rules(listing))


def _unwrap(item: NormalizedListing | AnalyzedListing) -> NormalizedListing:
    return item.listing if isinstance(item, AnalyzedListing) else item


def seller_lane(listing: NormalizedListing) -> str:
    value = listing.seller.seller_type.casefold().strip()
    if listing.seller.is_shop or value in {"company", "business", "shop", "компания", "магазин"}:
        return "company"
    if value in {"private", "person", "individual", "частное лицо", "частник"}:
        return "private"
    return "unknown"


def build_market_snapshots(
    items: Iterable[NormalizedListing | AnalyzedListing],
    *,
    city: str,
    observed_date: str | None = None,
) -> dict[str, MarketSnapshot]:
    """Build one immutable market view per SKU, with seller lanes kept separate."""
    observed_date = observed_date or date.today().isoformat()
    all_items = [_unwrap(item) for item in items]
    grouped: dict[tuple[str, str], list[NormalizedListing]] = {}
    excluded: dict[str, int] = {}
    known_skus: dict[str, NormalizedSku] = {}
    for listing in all_items:
        sku = normalized_sku(listing)
        if sku is None:
            continue
        known_skus[sku.key] = sku
        if not _market_eligible(listing, city):
            excluded[sku.key] = excluded.get(sku.key, 0) + 1
            continue
        grouped.setdefault((sku.key, seller_lane(listing)), []).append(listing)

    snapshots: dict[str, MarketSnapshot] = {}
    for sku_key, sku in known_skus.items():
        segments: list[MarketSegment] = []
        for seller_kind in ("company", "private", "unknown"):
            listings = grouped.get((sku_key, seller_kind), [])
            # Duplicate ads from one seller are one market observation.
            by_seller: dict[str, list[NormalizedListing]] = {}
            stable_keys: set[str] = set()
            unidentified: list[NormalizedListing] = []
            for listing in listings:
                if listing.seller.identity_hash:
                    key = "stable:" + listing.seller.identity_hash
                    stable_keys.add(key)
                    by_seller.setdefault(key, []).append(listing)
                elif _weak_seller_name(listing.seller.name):
                    # Weak fallback only: equal normalized names deduplicate,
                    # but never count as stable identities.
                    key = "name:" + " ".join(listing.seller.name.casefold().split())
                    by_seller.setdefault(key, []).append(listing)
                else:
                    unidentified.append(listing)
            representatives = []
            for seller_items in by_seller.values():
                ordered = sorted(seller_items, key=lambda item: (item.acquisition_price or 0, item.listing_id))
                representatives.append(ordered[(len(ordered) - 1) // 2])
            # Unknown seller identity may inform a descriptive price range, but
            # never counts as an independent seller and is never deduplicated by ad id.
            representatives.extend(unidentified)
            prices = sorted(item.acquisition_price for item in representatives if item.acquisition_price is not None)
            if not prices:
                continue
            p25, p75 = _percentile(prices, .25), _percentile(prices, .75)
            lower_fence = p25 - 1.5 * (p75 - p25)
            sane = [value for value in prices if value >= lower_fence]
            segments.append(MarketSegment(
                seller_kind=seller_kind,
                listing_count=len(listings),
                seller_count=len(by_seller),
                stable_seller_count=len(stable_keys),
                median=int(median(prices)),
                p25=p25,
                p75=p75,
                minimum_sane_price=min(sane or prices),
                listing_ids=tuple(item.listing_id for item in sorted(representatives, key=lambda item: item.acquisition_price or 0)),
                prices=tuple(prices),
            ))
        snapshots[sku_key] = MarketSnapshot(
            sku=sku,
            city=_city_key(city),
            observed_date=observed_date,
            segments=tuple(segments),
            excluded_count=excluded.get(sku_key, 0),
        )
    return snapshots


def _weak_seller_name(name: str) -> str:
    normalized = " ".join(name.casefold().split())
    if normalized in {"", "пользователь", "user", "частное лицо", "продавец"}:
        return ""
    return normalized


class SkuMarketSnapshotCache:
    """Date-keyed JSON cache; a snapshot is shared by every candidate of its SKU."""

    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def key(sku: NormalizedSku, city: str, observed_date: str) -> str:
        payload = json.dumps({
            "version": 3, "sku": sku.key, "city": _city_key(city), "date": observed_date,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def put(self, snapshot: MarketSnapshot) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        target = self.path / f"{self.key(snapshot.sku, snapshot.city, snapshot.observed_date)}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 3, **snapshot.public_dict()}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target

    def get(self, sku: NormalizedSku, city: str, observed_date: str) -> MarketSnapshot | None:
        target = self.path / f"{self.key(sku, city, observed_date)}.json"
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if raw.get("version") != 3:
                return None
            loaded_sku = NormalizedSku(**raw["sku"])
            segments = tuple(MarketSegment(**{
                **item, "listing_ids": tuple(item["listing_ids"]), "prices": tuple(item["prices"]),
            }) for item in raw["segments"])
            return MarketSnapshot(
                sku=loaded_sku,
                city=raw["city"],
                observed_date=raw["observed_date"],
                segments=segments,
                excluded_count=raw.get("excluded_count", 0),
            )
        except (OSError, ValueError, TypeError, KeyError):
            return None
