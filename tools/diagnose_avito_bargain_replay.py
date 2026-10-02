"""Offline bargain-gate replay from saved listing snapshots and text summaries.

This tool never constructs a network client and never calls text or photo AI.
The historical text replay stores decision summaries rather than full AIReview
payloads, so missing structured review fields are explicitly reconstructed from
saved listing facts and disclosed in the output.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from avito_service.models import AnalyzedListing, ReviewVerdict, SearchRequest
from avito_service.ranking import is_market_reference, merge_market_evidence
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService
from tools.replay_avito_text_ai import _saved_items


def _reconstruct_current_review(item: AnalyzedListing, decision: dict[str, Any]) -> AnalyzedListing:
    """Apply saved decision summary fields without inventing an AI response."""
    listing, old = item.listing, item.ai_review
    reason = str(decision.get("primary_reason") or "")
    changes: dict[str, Any] = {
        "text_analyzed": bool(decision.get("text_analyzed")),
        "verdict": ReviewVerdict(str(decision.get("verdict") or "caution")),
        "confidence": float(decision.get("confidence") or 0.0),
        "matches_request": bool(decision.get("matches_request")),
        "error": "",
        "photos_analyzed": False,
        "photo_coverage": (),
        "photo_condition_evidence": "",
        "photo_completeness_evidence": "",
        "identified_model": old.identified_model or listing.parameter("Модель") or listing.title,
        "storage": old.storage or listing.storage,
        "condition": old.condition or listing.condition,
        "defects": (),
        "conflicts": (),
        "price_conditions": (),
    }
    if reason in {"PAYMENT_SURCHARGE", "AI_PRICE_CONDITION"}:
        changes["price_conditions"] = (reason,)
    elif reason in {"HARDWARE_DEFECT", "NAND_DEFECT"}:
        changes["defects"] = (reason,)
    elif reason == "AI_REQUEST_MISMATCH":
        changes["matches_request"] = False
    # Never fabricate a pass: only the saved verdict and listing facts enter the gate.
    return replace(item, ai_review=replace(old, **changes))


def replay(snapshot_path: Path, text_report_path: Path, request: SearchRequest) -> dict[str, Any]:
    from datetime import datetime, timezone

    saved = _saved_items(snapshot_path, replayed_at=datetime.now(timezone.utc).isoformat())
    report = json.loads(text_report_path.read_text(encoding="utf-8"))
    decisions = {
        str(row["listing_id"]): row
        for row in report.get("text_decisions", ())
        if isinstance(row, dict) and row.get("listing_id")
    }
    listed_safe_ids = tuple(str(item) for item in report.get("photo_eligible_before_bargain_ids", ()))
    by_id = {item.listing.listing_id: item for item in saved}
    missing = [listing_id for listing_id in listed_safe_ids if listing_id not in by_id]
    targets = tuple(by_id[item] for item in listed_safe_ids if item in by_id)
    current = {
        item.listing.listing_id: _reconstruct_current_review(item, decisions[item.listing.listing_id])
        if item.listing.listing_id in decisions else item
        for item in targets
    }
    listings = tuple(item.listing for item in targets)
    risks = {item.listing.listing_id: evaluate_rules(item.listing) for item in targets}
    reviews = {listing_id: item.ai_review for listing_id, item in current.items()}
    market = merge_market_evidence(
        tuple(AnalyzedListing(item.listing, risks[item.listing.listing_id], reviews[item.listing.listing_id])
              for item in targets),
        saved,
    )
    references = [item for item in market if is_market_reference(item, request)]

    class OfflineOnlyReviewer:
        pass

    service = AvitoAnalysisService(None, OfflineOnlyReviewer())
    diagnostics: list[dict[str, Any]] = []
    photo_ids = service._photo_candidate_ids(
        listings, risks, reviews, len(listings), request, saved,
        require_bargain_evidence=True, bargain_diagnostics=diagnostics,
    )
    for item in diagnostics:
        source_decision = decisions.get(item["listing_id"], {})
        item["saved_text_state"] = source_decision.get("state")
        item["saved_text_reason"] = source_decision.get("primary_reason")
        item["photo_ai_attempted"] = False
        item["photo_ai_completed"] = False
        item["saved_photo_evidence_available"] = False
        item["final_revalidated"] = False
        item["final_status"] = (
            "rejected" if item["photo_admission_status"] == "fail" else "pending"
        )
        item["final_reason_code"] = (
            item["photo_admission_reason_code"]
            if item["photo_admission_status"] == "fail"
            else "OFFLINE_PHOTO_EVIDENCE_NOT_SAVED"
        )
        if item["photo_admission_status"] != "fail":
            # A terminal diagnostic outcome, not a customer rejection: no
            # historical photo decision exists from which to infer safety.
            item["final_status"] = "blocked_offline"

    fail_reasons = Counter(
        item["reason_code"] for item in diagnostics if item["status"] == "fail"
    )
    market_seller_ids = sorted({item.listing.seller.identity_hash for item in references
                                if item.listing.seller.identity_hash})
    seller_labels = {identity: f"seller_{index + 1}"
                     for index, identity in enumerate(market_seller_ids)}
    market_reference_rows = []
    market_reference_groups: dict[str, list[dict[str, Any]]] = {}
    for item in references:
        key = item.comparable_key(pickup_only=request.pickup_only)
        row = {
            "listing_id": item.listing.listing_id,
            "sku": key[1],
            "model_source": item.ai_review.identified_model or item.listing.model,
            "storage": key[2],
            "storage_source": item.ai_review.storage or item.listing.storage,
            "condition": key[4],
            "condition_source": item.ai_review.condition or item.listing.condition,
            "market_lane": item.market_lane(),
            "seller_kind": item.listing.seller.kind,
            "price": item.listing.price,
            "full_price": item.listing.acquisition_price,
            "kit_known": bool(item.listing.completeness),
            "kit_class": (
                "unknown" if key[9] == "unknown" else
                "normalized_components" if key[9].startswith("components:") else
                "unstructured"
            ),
            "kit_signature": hashlib.sha256(key[9].encode("utf-8")).hexdigest(),
            "seller_group": seller_labels.get(item.listing.seller.identity_hash, "seller_unknown"),
        }
        market_reference_rows.append(row)
        market_reference_groups.setdefault(row["seller_group"], []).append(row)
    funnel = {
        "text_safe_from_saved_report": len(listed_safe_ids),
        "text_safe_reconstructed": len(diagnostics),
        "bargain_evaluated": sum(item["status"] in {"pass", "fail"} for item in diagnostics),
        "bargain_pass": sum(item["status"] == "pass" for item in diagnostics),
        "bargain_fail": sum(item["status"] == "fail" for item in diagnostics),
        "photo_ai_eligible": len(photo_ids),
        "photo_ai_attempted": 0,
        "photo_ai_completed": 0,
        "final_revalidated": 0,
        "final": 0,
        "bargain": 0,
        "exact_match": 0,
        "rejected": sum(item["final_status"] == "rejected" for item in diagnostics),
        "pending": sum(item["final_status"] == "pending" for item in diagnostics),
        "blocked_offline": sum(item["final_status"] == "blocked_offline" for item in diagnostics),
        "offline_block_reasons": dict(Counter(
            item["final_reason_code"] for item in diagnostics
            if item["final_status"] == "blocked_offline"
        )),
        "fail_reasons": dict(fail_reasons),
    }
    return {
        "mode": "offline_saved_replay",
        "request": {
            "query": request.query,
            "location": request.location,
            "category": request.category,
            "pickup_only": request.pickup_only,
            "mode": request.mode,
        },
        "snapshot": str(snapshot_path),
        "text_report": str(text_report_path),
        "snapshot_listing_count": len(saved),
        "saved_replay_text_count": report.get("expected"),
        "market_reference_count": len(references),
        "market_reference_seller_count": len(market_seller_ids),
        "market_references": market_reference_rows,
        "market_reference_seller_groups": market_reference_groups,
        "funnel": funnel,
        "photo_ai_eligible_ids": list(photo_ids),
        "unmatched_saved_candidate_ids": missing,
        "ai_review_payload_reconstructed_from_listing_facts": True,
        "historical_report_omitted_ai_model_storage_condition_payload": True,
        "live_apify_calls": 0,
        "text_ai_calls": 0,
        "photo_ai_calls": 0,
        "saved_photo_evidence_available": False,
        "saved_final_revalidation_evidence_available": False,
        "bargain_decisions": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--text-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--query", default="ps5")
    parser.add_argument("--location", default="Ярославль")
    parser.add_argument("--category", default="gaming")
    parser.add_argument("--pickup-only", action="store_true", default=True)
    args = parser.parse_args()
    request = SearchRequest(
        args.query, location=args.location, category=args.category, mode="bargain",
        max_results=200, desired_results=3, pickup_only=args.pickup_only,
    )
    result = replay(args.snapshot, args.text_report, request)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
