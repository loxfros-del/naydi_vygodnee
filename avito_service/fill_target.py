"""Provider-independent bounded collection until the valid request target is filled."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .matching import matches_requested_city
from .models import CollectionBatch, SearchRequest
from .normalization import normalize_dataset
from .request_intent import check_request_compatibility, parse_request_signature, search_query_variants


@dataclass(frozen=True, slots=True)
class CollectionSafetyLimits:
    max_raw_pages: int = 10
    max_raw_listings: int = 1_000
    max_collection_cost_usd: float = 1.0
    duplicate_saturation_pages: int = 2
    page_size: int = 100


@dataclass(frozen=True, slots=True)
class CollectionPage:
    items: tuple[dict[str, Any], ...]
    cost_usd: float = 0.0
    cost_estimated: bool = True
    exhausted: bool = False
    stop_reason: str = ""


@dataclass(frozen=True, slots=True)
class FillMetrics:
    raw_received: int
    unique_received: int
    correct_city: int
    request_family_matched: int
    valid_target_count: int
    requested_target: int
    target_filled: bool
    shortfall: int
    stop_reason: str
    pages_collected: int
    duplicate_rows: int
    collection_cost_usd: float

    @property
    def raw_collected(self) -> int:
        return self.raw_received

    @property
    def deduplicated(self) -> int:
        return self.unique_received

    def public_dict(self) -> dict[str, object]:
        return {
            "raw_received": self.raw_received,
            "unique_received": self.unique_received,
            # Backward-compatible API aliases.
            "raw_collected": self.raw_received,
            "deduplicated": self.unique_received,
            "correct_city": self.correct_city,
            "request_family_matched": self.request_family_matched,
            "valid_target_count": self.valid_target_count,
            "requested_target": self.requested_target,
            "target_filled": self.target_filled,
            "shortfall": self.shortfall,
            "stop_reason": self.stop_reason,
            "pages_collected": self.pages_collected,
            "duplicate_rows": self.duplicate_rows,
            "collection_cost_usd": round(self.collection_cost_usd, 6),
        }


@dataclass(frozen=True, slots=True)
class FillToTargetResult:
    items: tuple[dict[str, Any], ...]
    metrics: FillMetrics
    cost_estimated: bool = True

    def as_collection_batch(self) -> CollectionBatch:
        return CollectionBatch(
            items=self.items,
            apify_cost_usd=self.metrics.collection_cost_usd,
            apify_cost_estimated=self.cost_estimated,
            requested_count=self.metrics.requested_target,
            capped_count=len(self.items),
            fill_metrics=self.metrics.public_dict(),
        )


PageFetcher = Callable[[SearchRequest, int, str, int], CollectionPage | CollectionBatch | Sequence[Mapping[str, Any]]]


@dataclass(slots=True)
class _QueryState:
    query: str
    next_page: int = 1
    exhausted: bool = False
    exhaustion_reason: str = ""
    duplicate_pages: int = 0


def _page(value: CollectionPage | CollectionBatch | Sequence[Mapping[str, Any]]) -> CollectionPage:
    if isinstance(value, CollectionPage):
        return value
    if isinstance(value, CollectionBatch):
        return CollectionPage(value.items, value.apify_cost_usd, value.apify_cost_estimated,
                              exhausted=not value.items)
    return CollectionPage(tuple(dict(item) for item in value if isinstance(item, Mapping)),
                          exhausted=not value)


def fill_to_target(
    request: SearchRequest,
    fetch_page: PageFetcher,
    limits: CollectionSafetyLimits = CollectionSafetyLimits(),
) -> FillToTargetResult:
    if limits.max_raw_pages < 1 or limits.max_raw_listings < 1 or limits.page_size < 1:
        raise ValueError("Collection safety limits must be positive.")
    target = request.max_results
    signature = parse_request_signature(request)
    variants = search_query_variants(signature) or (request.query.strip(),)
    query_states = [_QueryState(query) for query in variants]
    received_ids: set[str] = set()
    seen: set[str] = set()
    city_ids: set[str] = set()
    family_ids: set[str] = set()
    valid: dict[str, dict[str, Any]] = {}
    raw_received = pages = duplicate_rows = 0
    total_cost = 0.0
    estimated = False
    stop_reason = "PAGE_LIMIT"

    while pages < limits.max_raw_pages and len(valid) < target:
        active = [state for state in query_states if not state.exhausted]
        if not active:
            reasons = {state.exhaustion_reason for state in query_states}
            stop_reason = "DUPLICATE_SATURATION" if "DUPLICATE_SATURATION" in reasons else "SEARCH_EXHAUSTED"
            break
        if raw_received >= limits.max_raw_listings:
            stop_reason = "RAW_LIMIT"
            break
        if total_cost >= limits.max_collection_cost_usd:
            stop_reason = "BUDGET_LIMIT"
            break
        remaining = target - len(valid)
        state = active[0]
        page = _page(fetch_page(
            request, state.next_page, state.query,
            min(limits.page_size, max(1, remaining)),
        ))
        state.next_page += 1
        # Rotate this still-active query behind the others.
        query_states.remove(state)
        query_states.append(state)
        pages += 1
        total_cost += max(0.0, page.cost_usd)
        estimated = estimated or page.cost_estimated
        raw_received += len(page.items)
        if page.stop_reason and page.stop_reason != "SEARCH_EXHAUSTED":
            stop_reason = page.stop_reason
            break
        normalized = normalize_dataset(page.items)
        raw_by_id: dict[str, dict[str, Any]] = {}
        for row in page.items:
            identifier = str(row.get("id") or row.get("avitoId") or row.get("listingId") or "")
            if identifier:
                raw_by_id.setdefault(identifier, row)
                if identifier in received_ids:
                    duplicate_rows += 1
                else:
                    received_ids.add(identifier)
        new_ids = 0
        for listing in normalized:
            identifier = listing.listing_id
            if identifier in seen:
                continue
            seen.add(identifier)
            received_ids.add(identifier)
            new_ids += 1
            if not matches_requested_city(listing, request, final=True):
                continue
            city_ids.add(identifier)
            compatibility = check_request_compatibility(listing, signature)
            sku = compatibility.sku
            if (signature.requested_family == "unknown" and compatibility.matches) or (
                sku is not None and not sku.conflicts and sku.product_type == "console"
                and sku.family == signature.requested_family
            ):
                family_ids.add(identifier)
            if not compatibility.matches:
                continue
            valid[identifier] = raw_by_id.get(identifier, {
                "id": identifier, "title": listing.title, "url": listing.url,
                "price": listing.price, "description": listing.description,
                "parameters": listing.parameters, "location": listing.location,
            })
            if len(valid) >= target:
                break
        state.duplicate_pages = state.duplicate_pages + 1 if new_ids == 0 and page.items else 0
        if len(valid) >= target:
            stop_reason = "TARGET_FILLED"
            break
        if total_cost >= limits.max_collection_cost_usd:
            stop_reason = "BUDGET_LIMIT"
            break
        if raw_received >= limits.max_raw_listings:
            stop_reason = "RAW_LIMIT"
            break
        if state.duplicate_pages >= limits.duplicate_saturation_pages:
            state.exhausted = True
            state.exhaustion_reason = "DUPLICATE_SATURATION"
        if page.exhausted or page.stop_reason == "SEARCH_EXHAUSTED" or not page.items:
            state.exhausted = True
            state.exhaustion_reason = "SEARCH_EXHAUSTED"
        if all(value.exhausted for value in query_states):
            reasons = {value.exhaustion_reason for value in query_states}
            stop_reason = "DUPLICATE_SATURATION" if "DUPLICATE_SATURATION" in reasons else "SEARCH_EXHAUSTED"
            break

    matched = len(valid)
    metrics = FillMetrics(
        raw_received=raw_received,
        unique_received=len(received_ids),
        correct_city=len(city_ids),
        request_family_matched=len(family_ids),
        valid_target_count=matched,
        requested_target=target,
        target_filled=matched >= target,
        shortfall=max(0, target - matched),
        stop_reason=stop_reason,
        pages_collected=pages,
        duplicate_rows=duplicate_rows,
        collection_cost_usd=total_cost,
    )
    return FillToTargetResult(tuple(valid.values())[:target], metrics, estimated)
