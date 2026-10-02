"""One collection-only live pilot for the bounded Avito fill-to-target collector."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.apify import ZenStudioProvider
from avito_service.config import load_config
from avito_service.http_api import build_service
from avito_service.matching import matches_requested_city
from avito_service.models import SearchRequest
from avito_service.normalization import normalize_dataset
from avito_service.request_intent import (
    check_request_compatibility,
    normalize_listing_sku,
    parse_request_signature,
)


CAP_USD = 0.25
TARGET = 20


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _raw_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("avitoId") or row.get("listingId") or "")


class ObservedProvider(ZenStudioProvider):
    """Capture page/query provenance without changing the production collector."""

    def __init__(self, config, spending_guard) -> None:
        super().__init__(config, spending_guard)
        self.pages: list[dict[str, Any]] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self._active_run_id = ""

    def _json_request(self, method, url, **kwargs):
        result = super()._json_request(method, url, **kwargs)
        data = result.get("data") if isinstance(result, Mapping) else None
        if isinstance(data, Mapping) and data.get("id") and data.get("status"):
            run_id = str(data["id"])
            self.runs[run_id] = dict(data)
            if method.upper() == "POST" and "/runs?" in url:
                self._active_run_id = run_id
        return result

    def _collect_payload(self, payload, requested_count, deadline_at, max_charge_usd):
        self._active_run_id = ""
        batch = super()._collect_payload(payload, requested_count, deadline_at, max_charge_usd)
        search_url = str(payload.get("searchUrl") or "")
        query = str(payload.get("query") or "")
        page_number = 1
        if search_url:
            parsed = parse_qs(urlparse(search_url).query)
            query = str((parsed.get("q") or [query])[0])
            try:
                page_number = int((parsed.get("p") or [1])[0])
            except (TypeError, ValueError):
                page_number = 1
        self.pages.append({
            "query_variant": query,
            "page": page_number,
            "requested": requested_count,
            "raw_count": len(batch.items),
            "accounted_cost_usd": batch.apify_cost_usd,
            "cost_estimated": batch.apify_cost_estimated,
            "run_id": self._active_run_id,
            "items": list(batch.items),
        })
        return batch

    def refresh_receipts(self) -> None:
        """Read existing runs only; never starts or retries collection."""
        for run_id in tuple(self.runs):
            expected_items = next(
                (page["raw_count"] for page in self.pages if page["run_id"] == run_id), 0
            )
            for attempt in range(10):
                result = self._json_request(
                    "GET", f"{self.config.apify_api_url}/actor-runs/{quote(run_id)}", timeout=10,
                )
                data = result.get("data") if isinstance(result, Mapping) else None
                if isinstance(data, Mapping) and self._complete_event_cost(data, expected_items) is not None:
                    break
                if attempt < 9:
                    time.sleep(3)


def main() -> None:
    request = SearchRequest(
        query="PlayStation 5",
        location="Ярославль",
        category="gaming",
        max_results=TARGET,
        desired_results=3,
        required_condition="",
        pickup_only=True,
    )
    base = load_config()
    if not base.apify_ready:
        raise SystemExit("APIFY_TOKEN is not configured")
    # This runner is collection-only. The report budget mirrors the explicit
    # $0.25 ceiling, while AI receives no usable budget and is never invoked.
    config = replace(
        base,
        apify_max_charge_usd=CAP_USD,
        ai_max_cost_rub=0.0,
        report_max_cost_rub=CAP_USD * max(base.usd_rub_rate, 1.0),
        collection_max_raw_pages=10,
        collection_max_raw_listings=1_000,
        collection_duplicate_saturation_pages=2,
    )
    service = build_service(config)
    provider = ObservedProvider(service.provider.config, service.provider.spending_guard)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "runtime" / "avito_fill_target_pilot" / stamp
    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / "request.json", asdict(request))
    print("PILOT_OUTPUT " + str(output), flush=True)

    batch = provider.collect_to_target(
        request,
        deadline_at=time.monotonic() + 300,
        max_charge_usd=CAP_USD,
    )
    provider.refresh_receipts()
    signature = parse_request_signature(request)

    first_seen: dict[str, str] = {}
    unique_rows: dict[str, Mapping[str, Any]] = {}
    variant_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"pages_requested": 0, "raw_results": 0, "valid_results": 0}
    )
    for page in provider.pages:
        variant = page["query_variant"]
        stats = variant_stats[variant]
        stats["pages_requested"] += 1
        stats["raw_results"] += page["raw_count"]
        for row in page["items"]:
            identifier = _raw_id(row)
            if identifier and identifier not in unique_rows:
                unique_rows[identifier] = row
                first_seen[identifier] = variant

    mismatch = 0
    audit_counts: Counter[str] = Counter()
    for listing in normalize_dataset(tuple(unique_rows.values())):
        if not matches_requested_city(listing, request, final=True):
            audit_counts["wrong_city"] += 1
            continue
        compatibility = check_request_compatibility(listing, signature)
        if compatibility.reason == "REQUEST_SKU_MISMATCH":
            mismatch += 1
        if compatibility.matches:
            variant_stats[first_seen[listing.listing_id]]["valid_results"] += 1

    normalized_valid = normalize_dataset(batch.items)
    listings = []
    validation = {"ps5_pro": 0, "ps4": 0, "pure_accessory": 0,
                  "duplicates": 0, "wrong_city": 0, "not_in_target": 0}
    valid_ids: set[str] = set()
    for listing in normalized_valid:
        sku = normalize_listing_sku(listing)
        compatibility = check_request_compatibility(listing, signature)
        if listing.listing_id in valid_ids:
            validation["duplicates"] += 1
        valid_ids.add(listing.listing_id)
        validation["ps5_pro"] += int(sku.family == "ps5_pro" or sku.form_factor == "pro")
        validation["ps4"] += int(sku.family == "ps4")
        validation["pure_accessory"] += int(sku.product_type != "console")
        validation["wrong_city"] += int(not matches_requested_city(listing, request, final=True))
        validation["not_in_target"] += int(not compatibility.matches)
        listings.append({
            "id": listing.listing_id,
            "title": listing.title,
            "price": listing.price,
            "normalized_sku": sku.key,
            "city": "Ярославль" if matches_requested_city(listing, request, final=True) else listing.location,
            "query_variant_that_found_it": first_seen.get(listing.listing_id, ""),
        })

    metrics = dict(batch.fill_metrics)
    actual_costs = []
    for page in provider.pages:
        receipt = provider.runs.get(page["run_id"], {})
        cost = provider._complete_event_cost(receipt, page["raw_count"])
        if cost is not None and math.isfinite(cost) and cost >= 0:
            actual_costs.append(cost)
    actual_cost = sum(actual_costs) if len(actual_costs) == len(provider.pages) else None
    if actual_cost is not None and actual_cost > CAP_USD + 1e-9:
        raise RuntimeError(f"Actual Apify cost exceeded hard cap: {actual_cost}")
    accounted_cost = float(batch.apify_cost_usd)
    raw = int(metrics.get("raw_received", 0))
    city = int(metrics.get("correct_city", 0))
    family = int(metrics.get("request_family_matched", 0))
    valid = int(metrics.get("valid_target_count", 0))
    basis_cost = actual_cost if actual_cost is not None else accounted_cost
    forecast_raw = math.ceil(200 * raw / valid) if valid else None
    forecast_cost = basis_cost * 200 / valid if valid else None
    result = {
        **metrics,
        "REQUEST_SKU_MISMATCH_count": mismatch,
        "duplicate_count": metrics.get("duplicate_rows", 0),
        "pages_queries_used": [
            {key: page[key] for key in ("query_variant", "page", "requested", "raw_count")}
            for page in provider.pages
        ],
        "query_variants": dict(variant_stats),
        "actual_apify_cost_usd": actual_cost,
        "accounted_apify_cost_usd": accounted_cost,
        "cost_per_raw_listing": basis_cost / raw if raw else None,
        "cost_per_correct_city_listing": basis_cost / city if city else None,
        "cost_per_valid_family_listing": basis_cost / family if family else None,
        "raw_per_valid_ratio": raw / valid if valid else None,
        "estimated_raw_for_200_valid": forecast_raw,
        "estimated_collection_cost_for_200_valid": forecast_cost,
        "forecast_note": "Approximate extrapolation from one small live pilot sample.",
        "validation": validation,
        "listings": listings,
        "ai_text_review": False,
        "photo_ai": False,
        "market_final_verification": False,
        "direct_finalist_refresh": False,
        "actor_runs": len(provider.pages),
    }
    _write_json(output / "pages.json", provider.pages)
    _write_json(output / "receipts.json", list(provider.runs.values()))
    _write_json(output / "result.json", result)
    print("PILOT_RESULT " + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
