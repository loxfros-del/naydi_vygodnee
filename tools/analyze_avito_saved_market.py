"""Replay price truth and comparable market from saved Avito data only."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.market_engine import SkuMarketSnapshotCache, build_market_snapshots, normalized_sku, seller_lane
from avito_service.matching import matches_listing_request, matches_requested_city
from avito_service.config import load_config
from avito_service.models import SearchRequest, Severity
from avito_service.normalization import normalize_dataset
from avito_service.risk_rules import evaluate_rules
from avito_service.request_intent import check_request_compatibility, normalize_listing_sku, parse_request_signature


TARGET_IDS = {"7704979247", "8057330597", "8351987913", "4791552729"}
FINALIST_IDS = {"8139333912", "8318570064", "8374041219"}
PREFILTER_CODES = {
    "INVALID_DIRECT_URL", "MISSING_DESCRIPTION", "MISSING_PHOTOS",
    "NON_FINAL_PRICE", "PHOTO_SET_INCOMPLETE",
}


def _base_reasons(listing, request: SearchRequest) -> list[str]:
    reasons: list[str] = []
    if not matches_requested_city(listing, request, final=True):
        return ["WRONG_OR_UNKNOWN_CITY"]
    findings = evaluate_rules(listing)
    reasons.extend(item.code for item in findings if item.severity is Severity.CRITICAL or item.code in PREFILTER_CODES)
    if listing.price_confidence in {"ambiguous", "invalid"}:
        reasons.append(f"PRICE_{listing.price_confidence.upper()}")
    if listing.acquisition_price is None:
        reasons.append("UNKNOWN_EFFECTIVE_PRICE")
    compatibility = check_request_compatibility(listing, request)
    if not compatibility.matches:
        reasons.append(compatibility.reason or "REQUEST_SKU_MISMATCH")
    elif not matches_listing_request(listing, request):
        reasons.append("REQUEST_MISMATCH")
    return list(dict.fromkeys(reasons))


def _paid_condition(value: str) -> bool:
    text = value.casefold().replace("ё", "е")
    return not (
        ("налич" in text or "карт" in text or "qr" in text)
        and not any(word in text for word in ("комисс", "дороже", "нацен", "трейд", "обмен", "кредит"))
    )


def analyze(run_dir: Path, cache_dir: Path) -> dict[str, object]:
    request = SearchRequest(**json.loads((run_dir / "request.json").read_text(encoding="utf-8")))
    raw = json.loads((run_dir / "market-facts.json").read_text(encoding="utf-8"))
    listings = normalize_dataset(raw)
    by_id = {item.listing_id: item for item in listings}
    target_by_id = dict(by_id)
    missing_targets = TARGET_IDS - target_by_id.keys()
    if missing_targets:
        for facts_path in sorted(run_dir.parent.glob("*/market-facts.json"), reverse=True):
            if not missing_targets:
                break
            try:
                saved = normalize_dataset(json.loads(facts_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
            for item in saved:
                if item.listing_id in missing_targets:
                    target_by_id[item.listing_id] = item
                    missing_targets.remove(item.listing_id)
    base_reasons = {item.listing_id: _base_reasons(item, request) for item in listings}

    signature = parse_request_signature(request)
    local = [item for item in listings if matches_requested_city(item, request, final=True)]
    family_matched = []
    valid_target = []
    for item in local:
        compatibility = check_request_compatibility(item, signature)
        sku = compatibility.sku
        if (signature.requested_family == "unknown" and compatibility.matches) or (
            sku is not None and not sku.conflicts and sku.product_type == "console"
            and sku.family == signature.requested_family
        ):
            family_matched.append(item)
        if compatibility.matches:
            valid_target.append(item)
    basic = [item for item in local if not base_reasons[item.listing_id]]
    observed_date = max((item.collected_at[:10] for item in listings if item.collected_at), default="unknown")
    snapshots = build_market_snapshots(
        (item for item in valid_target if item.listing_id not in FINALIST_IDS),
        city=request.location,
        observed_date=observed_date,
    )
    cache = SkuMarketSnapshotCache(cache_dir)
    cache_files = [str(cache.put(snapshot)) for snapshot in snapshots.values() if snapshot.segments]

    text_reviews = {}
    for path in run_dir.glob("review_text_batch-*.json"):
        review = json.loads(path.read_text(encoding="utf-8"))
        text_reviews[str(review.get("listing_id"))] = review
    photo_ids = {
        path.stem.removeprefix("review_photos-")
        for path in run_dir.glob("review_photos-*.json")
    }
    audit_path = run_dir / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    final_cards = {str(card["listing"]["id"]): card for card in audit.get("adminRecommendations", ())}

    text_passed_ids = {
        identifier for identifier, review in text_reviews.items()
        if identifier in base_reasons and not base_reasons[identifier]
        and review.get("text_analyzed") and review.get("matches_request")
        and review.get("verdict") != "reject"
    }
    photo_passed_ids = set()
    data_high_ids = set()
    for identifier in photo_ids & text_passed_ids:
        card = final_cards.get(identifier)
        if not card:
            continue
        analysis = card.get("analysis", {})
        if analysis.get("photosAnalyzed") and not analysis.get("defects") and not analysis.get("conflicts"):
            photo_passed_ids.add(identifier)
        paid_conditions = [value for value in analysis.get("priceConditions", ()) if _paid_condition(value)]
        if (
            identifier in photo_passed_ids
            and analysis.get("complete")
            and analysis.get("verdict") == "approve"
            and float(analysis.get("confidence", 0)) >= .75
            and not paid_conditions
        ):
            data_high_ids.add(identifier)

    confirmed_ids = set()
    bargain_eligible_ids = set()
    closest = []
    config = load_config()
    for identifier in text_passed_ids:
        item = by_id[identifier]
        reasons = []
        if identifier not in photo_ids:
            reasons.append("PHOTO_CHECK_NOT_RUN")
        elif identifier not in photo_passed_ids:
            card = final_cards.get(identifier, {})
            analysis = card.get("analysis", {})
            reasons.extend(f"PHOTO_DEFECT:{value}" for value in analysis.get("defects", ()))
            reasons.extend(f"AI_CONFLICT:{value}" for value in analysis.get("conflicts", ()))
        if identifier not in data_high_ids and not reasons:
            reasons.append("NOT_DATA_HIGH_CONFIDENCE")
        sku = normalized_sku(item)
        lane = seller_lane(item)
        segment = snapshots.get(sku.key).segment(lane) if sku and sku.key in snapshots else None
        if lane == "unknown":
            reasons.append("SELLER_KIND_UNKNOWN")
        if segment is None or segment.seller_count < 2:
            reasons.append(f"INDEPENDENT_SELLERS:{segment.seller_count if segment else 0}/2")
        if segment is None or segment.listing_count < 3:
            reasons.append(f"COMPARABLE_LISTINGS:{segment.listing_count if segment else 0}/3")
        elif item.acquisition_price is None or item.acquisition_price >= segment.p25:
            reasons.append(f"NOT_BELOW_CONSERVATIVE_MARKET:{segment.p25}")
        elif identifier in data_high_ids:
            saving = segment.p25 - item.acquisition_price
            saving_percent = saving / segment.p25 * 100 if segment.p25 else 0
            if saving >= config.minimum_bargain_rub and saving_percent >= config.minimum_bargain_percent:
                bargain_eligible_ids.add(identifier)
            else:
                reasons.append("MINIMUM_BARGAIN_NOT_MET")
        if not item.is_freshly_verified():
            reasons.append("PRICE_AND_ACTIVITY_NOT_REVALIDATED")
        if identifier in bargain_eligible_ids and lane in {"company", "private"} and segment and segment.seller_count >= 2 \
                and item.acquisition_price is not None and item.acquisition_price < segment.p25 \
                and item.is_freshly_verified():
            confirmed_ids.add(identifier)
        closest.append({
            "id": identifier,
            "advertised_price": item.advertised_price,
            "effective_price": item.effective_price,
            "price_confidence": item.price_confidence,
            "reasons": reasons,
        })
    closest.sort(key=lambda value: (len(value["reasons"]), value["id"]))

    targets = []
    for identifier in sorted(TARGET_IDS):
        item = target_by_id.get(identifier)
        if not item:
            targets.append({"id": identifier, "missing": True})
            continue
        truth = item.price_truth
        conflicts = list(truth.conflicts)
        conflicts.extend(finding.code for finding in evaluate_rules(item) if finding.code == "CONDITION_CONFLICT")
        targets.append({
            "id": identifier,
            "advertised_price": truth.advertised_price,
            "effective_price": truth.effective_price,
            "price_confidence": truth.price_confidence,
            "model": truth.model,
            "variant": truth.variant,
            "condition": truth.condition,
            "conflicts": list(dict.fromkeys(conflicts)),
            "payment_conditions": list(truth.payment_conditions),
            "offers": [offer.public_dict() for offer in truth.offers],
        })

    former_finalists = []
    for identifier in sorted(FINALIST_IDS):
        item = by_id.get(identifier)
        if not item:
            continue
        compatibility = check_request_compatibility(item, signature)
        former_finalists.append({
            "listing_id": identifier,
            "title": item.title,
            "normalized_sku": normalize_listing_sku(item).key,
            "request_match": compatibility.matches,
            "rejection_reason": compatibility.reason,
        })
    mismatch_count = sum(
        check_request_compatibility(item, signature).reason == "REQUEST_SKU_MISMATCH"
        for item in local
    )
    family_yield = len(valid_target) / len(raw) if raw else 0
    estimated_raw = math.ceil(request.max_results / family_yield) if family_yield else None
    return {
        "source": str(run_dir / "market-facts.json"),
        "request_signature": {
            "requested_product": signature.requested_product,
            "requested_family": signature.requested_family,
            "requested_form_factor": signature.requested_form_factor,
            "requested_edition": signature.requested_edition,
            "requested_storage": signature.requested_storage,
            "requested_condition": signature.requested_condition,
            "allowed_variants": list(signature.allowed_variants),
            "excluded_variants": list(signature.excluded_variants),
        },
        "targets": targets,
        "funnel": {
            "raw_received": len(raw),
            "unique_received": len(listings),
            "correct_city": len(local),
            "request_family_matched": len(family_matched),
            "valid_target_count": len(valid_target),
            "basic_filters": len(basic),
            "text_check": len(text_passed_ids),
            "photo_check": len(photo_passed_ids),
            "DATA_HIGH_CONFIDENCE": len(data_high_ids),
            "BARGAIN_ELIGIBLE_PRE_REFRESH": len(bargain_eligible_ids),
            "HIGH_CONFIDENCE_BARGAIN": 0,
            "CONFIRMED_BARGAIN": len(confirmed_ids),
        },
        "requested_target": request.max_results,
        "target_filled": len(valid_target) >= request.max_results,
        "shortfall": max(0, request.max_results - len(valid_target)),
        "request_sku_mismatch_count": mismatch_count,
        "historical_offline_raw_estimate_for_target": estimated_raw,
        "estimate_warning": (
            "Historical/offline estimate only; the saved dataset predates corrected city collection "
            "and must not be used as a live cost forecast."
        ),
        "minimum_bargain_percent": config.minimum_bargain_percent,
        "minimum_bargain_rub": config.minimum_bargain_rub,
        "former_finalists": former_finalists,
        "remaining_standard_candidates": [
            {"listing_id": item.listing_id, "title": item.title,
             "normalized_sku": normalize_listing_sku(item).key}
            for item in valid_target if item.listing_id in data_high_ids
        ],
        "data_high_confidence_ids": sorted(data_high_ids),
        "bargain_eligible_ids": sorted(bargain_eligible_ids),
        "confirmed_ids": sorted(confirmed_ids),
        "basic_rejections": dict(Counter(reason for reasons in base_reasons.values() for reason in reasons)),
        "market_snapshots": [snapshot.public_dict() for snapshot in snapshots.values() if snapshot.segments],
        "cache_files": cache_files,
        "closest": closest[:10] if not confirmed_ids else [],
    }


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "runtime" / "avito_market_cache")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = analyze(args.run_dir, args.cache_dir)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
