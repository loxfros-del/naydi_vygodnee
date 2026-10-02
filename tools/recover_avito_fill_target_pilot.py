"""Recover the report for an already-finished fill-target pilot using GETs only."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Mapping
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.apify import ZenStudioProvider
from avito_service.config import load_config
from avito_service.errors import ExternalServiceError
from avito_service.fill_target import CollectionPage, CollectionSafetyLimits, fill_to_target
from avito_service.matching import matches_requested_city
from avito_service.models import SearchRequest
from avito_service.normalization import normalize_dataset
from avito_service.request_intent import (
    check_request_compatibility,
    normalize_listing_sku,
    parse_request_signature,
    search_query_variants,
)


CAP_USD = 0.25


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def raw_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("avitoId") or row.get("listingId") or "")


def main(output_name: str) -> None:
    output = ROOT / "runtime" / "avito_fill_target_pilot" / output_name
    request = SearchRequest(**json.loads((output / "request.json").read_text(encoding="utf-8")))
    config = replace(load_config(), apify_max_charge_usd=CAP_USD)
    provider = ZenStudioProvider(config)
    actor = quote(config.apify_actor_id.replace("/", "~"), safe="~")
    # Folder timestamp is written immediately before the first paid start.
    began = datetime.strptime(output_name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    window_start = began - timedelta(minutes=5)  # tolerate local/provider clock skew
    window_end = began + timedelta(minutes=15)
    list_url = f"{config.apify_api_url}/acts/{actor}/runs?" + urlencode({"limit": 100, "desc": "true"})
    response = provider._json_request("GET", list_url, timeout=20)
    data = response.get("data") if isinstance(response, Mapping) else None
    candidates = data.get("items") if isinstance(data, Mapping) else None
    if not isinstance(candidates, list):
        raise RuntimeError("Apify did not return the recent run list")
    runs = sorted(
        (dict(run) for run in candidates if isinstance(run, Mapping)
         and run.get("startedAt") and window_start <= stamp(str(run["startedAt"])) <= window_end),
        key=lambda run: str(run["startedAt"]),
    )
    if len(runs) != 2:
        raise RuntimeError(f"Expected exactly two existing pilot runs, found {len(runs)}")

    pages: list[dict[str, Any]] = []
    variants = search_query_variants(parse_request_signature(request))
    for index, run in enumerate(runs):
        run_id = str(run["id"])
        dataset_id = str(run.get("defaultDatasetId") or "")
        dataset_url = f"{config.apify_api_url}/datasets/{quote(dataset_id)}/items?" + urlencode(
            {"clean": "true", "format": "json", "limit": 100}
        )
        rows = provider._json_request("GET", dataset_url, timeout=30)
        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected dataset for existing run {run_id}")
        latest = run
        cost = provider._complete_event_cost(latest, len(rows))
        for attempt in range(10):
            if cost is not None:
                break
            try:
                receipt = provider._json_request(
                    "GET", f"{config.apify_api_url}/actor-runs/{quote(run_id)}", timeout=10,
                )
                value = receipt.get("data") if isinstance(receipt, Mapping) else None
                if isinstance(value, Mapping):
                    latest = dict(value)
                    cost = provider._complete_event_cost(latest, len(rows))
            except ExternalServiceError:
                pass
            if cost is None and attempt < 9:
                time.sleep(3)
        if cost is None:
            try:
                usage_total = float(latest.get("usageTotalUsd"))
            except (TypeError, ValueError):
                usage_total = math.nan
            if (str(latest.get("status") or "").upper() != "SUCCEEDED"
                    or not latest.get("finishedAt") or not math.isfinite(usage_total)
                    or usage_total < 0):
                raise RuntimeError(f"Final billing receipt is unavailable for existing run {run_id}")
            cost = usage_total
        pages.append({
            "query_variant": variants[index],
            "page": 1,
            "requested": len(rows),
            "raw_count": len(rows),
            "actual_cost_usd": cost,
            "run_id": run_id,
            "items": rows,
            "receipt": latest,
        })

    page_index = 0

    def fetch_page(_request, page_number, query, page_size):
        nonlocal page_index
        if page_index >= len(pages):
            return CollectionPage((), exhausted=False, stop_reason="BUDGET_LIMIT")
        page = pages[page_index]
        if page["query_variant"] != query or page["page"] != page_number:
            raise RuntimeError("Recovered run order does not match independent query pagination")
        page["requested"] = page_size
        page_index += 1
        return CollectionPage(
            tuple(page["items"]), page["actual_cost_usd"], False,
            exhausted=len(page["items"]) < page_size,
        )

    result = fill_to_target(
        request,
        fetch_page,
        CollectionSafetyLimits(
            max_raw_pages=10,
            max_raw_listings=1_000,
            max_collection_cost_usd=CAP_USD,
            duplicate_saturation_pages=2,
            page_size=100,
        ),
    )
    metrics = result.metrics.public_dict()
    signature = parse_request_signature(request)
    first_seen: dict[str, str] = {}
    unique_rows: dict[str, Mapping[str, Any]] = {}
    variant_stats = defaultdict(lambda: {"pages_requested": 0, "raw_results": 0, "valid_results": 0})
    for page in pages:
        variant = page["query_variant"]
        variant_stats[variant]["pages_requested"] += 1
        variant_stats[variant]["raw_results"] += page["raw_count"]
        for row in page["items"]:
            identifier = raw_id(row)
            if identifier and identifier not in unique_rows:
                unique_rows[identifier] = row
                first_seen[identifier] = variant

    mismatch = 0
    for listing in normalize_dataset(tuple(unique_rows.values())):
        if not matches_requested_city(listing, request, final=True):
            continue
        compatibility = check_request_compatibility(listing, signature)
        mismatch += int(compatibility.reason == "REQUEST_SKU_MISMATCH")
        if compatibility.matches:
            variant_stats[first_seen[listing.listing_id]]["valid_results"] += 1

    validation = {"ps5_pro": 0, "ps4": 0, "pure_accessory": 0,
                  "duplicates": 0, "wrong_city": 0, "not_in_target": 0}
    listings = []
    valid_ids: set[str] = set()
    for listing in normalize_dataset(result.items):
        sku = normalize_listing_sku(listing)
        compatibility = check_request_compatibility(listing, signature)
        validation["duplicates"] += int(listing.listing_id in valid_ids)
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

    actual_cost = sum(float(page["actual_cost_usd"]) for page in pages)
    if actual_cost > CAP_USD + 1e-9:
        raise RuntimeError(f"Actual Apify cost exceeded hard cap: {actual_cost}")
    raw = int(metrics["raw_received"])
    city = int(metrics["correct_city"])
    family = int(metrics["request_family_matched"])
    valid = int(metrics["valid_target_count"])
    report = {
        **metrics,
        "REQUEST_SKU_MISMATCH_count": mismatch,
        "duplicate_count": metrics["duplicate_rows"],
        "pages_queries_used": [
            {key: page[key] for key in ("query_variant", "page", "requested", "raw_count")}
            for page in pages
        ],
        "query_variants": dict(variant_stats),
        "actual_apify_cost_usd": actual_cost,
        "cost_per_raw_listing": actual_cost / raw if raw else None,
        "cost_per_correct_city_listing": actual_cost / city if city else None,
        "cost_per_valid_family_listing": actual_cost / family if family else None,
        "raw_per_valid_ratio": raw / valid if valid else None,
        "estimated_raw_for_200_valid": math.ceil(200 * raw / valid) if valid else None,
        "estimated_collection_cost_for_200_valid": actual_cost * 200 / valid if valid else None,
        "forecast_note": "Approximate extrapolation from one small live pilot sample.",
        "validation": validation,
        "listings": listings,
        "ai_text_review": False,
        "photo_ai": False,
        "market_final_verification": False,
        "direct_finalist_refresh": False,
        "actor_runs": len(pages),
    }
    write_json(output / "recovered-pages.json", pages)
    write_json(output / "result.json", report)
    print("PILOT_RESULT " + json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: recover_avito_fill_target_pilot.py YYYYMMDDTHHMMSSZ")
    main(sys.argv[1])
