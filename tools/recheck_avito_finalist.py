"""Continue a saved PS5 pilot candidate through photo AI and final URL refresh."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.config import expanded_review_config, load_config
from avito_service.errors import AvitoServiceError
from avito_service.http_api import build_service
from avito_service.models import (
    AIReview, AnalyzedListing, NormalizedListing, ReviewVerdict, SearchRequest, SellerSummary,
)
from avito_service.ranking import is_customer_safe, rank_listings
from avito_service.risk_rules import evaluate_rules
from avito_service.service import reconcile_ps5_family_mismatch
from avito_service.verification import VerificationState, evaluate_verification
from tools.run_avito_pilot import PilotProvider, account_bounded_spending_guard, write_json
from avito_service.apify import ZenStudioProvider


def load_listing(path: Path, listing_id: str) -> NormalizedListing:
    rows = json.loads(path.read_text(encoding="utf-8"))
    record = next((row for row in rows if row["listing_id"] == listing_id), None)
    if record is None:
        raise ValueError("Объявление отсутствует в сохранённом пакете.")
    data = dict(record)
    data["seller"] = SellerSummary(**data["seller"])
    data["images"] = tuple(data["images"])
    data["badges"] = tuple(data["badges"])
    return NormalizedListing(**data)


def load_text_review(path: Path, listing_id: str) -> AIReview:
    rows = json.loads(path.read_text(encoding="utf-8"))
    card = next((row for row in rows if row["listing"]["id"] == listing_id), None)
    if card is None:
        raise ValueError("Текстовая проверка объявления не сохранена.")
    source = card["analysis"]
    if not source["textAnalyzed"] or source["photosAnalyzed"]:
        raise ValueError("Для продолжения нужен сохранённый текстовый этап без готового фотоэтапа.")
    mapping = {
        "listing_id": listing_id, "text_analyzed": True, "photos_analyzed": False,
        "identified_model": source["identifiedModel"], "storage": source["storage"],
        "sim_variant": source["simVariant"], "condition": source["condition"],
        "matches_request": source["matchesRequest"], "mismatch_reason": source["mismatchReason"],
        "verdict": ReviewVerdict(source["verdict"]), "confidence": source["confidence"],
        "error": source["error"],
    }
    for key, field_name in (
        ("descriptionFindings", "description_findings"), ("defects", "defects"),
        ("priceConditions", "price_conditions"), ("conflicts", "conflicts"),
        ("manualChecks", "manual_checks"),
    ):
        mapping[field_name] = tuple(source.get(key) or ())
    return AIReview(**mapping)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-output", type=Path, required=True)
    parser.add_argument("--listing-id", required=True)
    parser.add_argument("--authorized-apify-budget-usd", type=float, required=True)
    args = parser.parse_args()
    source = args.source_output.resolve()
    listing = load_listing(source / "candidates-normalized.json", args.listing_id)
    request = SearchRequest("PS5", location="Ярославль", category="gaming",
                            max_results=200, desired_results=3, pickup_only=True)
    text_review = reconcile_ps5_family_mismatch(
        listing, load_text_review(source / "all-decisions.json", args.listing_id), request,
    )
    config = expanded_review_config(load_config())
    service = build_service(config)
    guard, account = account_bounded_spending_guard(
        config, service.provider.spending_guard, args.authorized_apify_budget_usd,
        ZenStudioProvider(config),
    )
    output = ROOT / "runtime" / "avito_pilot" / (
        "recheck-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True, exist_ok=False)
    service.provider = PilotProvider(config, output, spending_guard=guard)
    print("RECHECK_BUDGET " + json.dumps(account), flush=True)
    print("RECHECK_OUTPUT " + str(output), flush=True)
    result = {"listing_id": args.listing_id, "photo_attempted": False,
              "final_refresh_attempted": False, "finalists": []}
    try:
        risks = evaluate_rules(listing)
        text_gate = evaluate_verification(listing, risks, text_review, request, stage="text")
        if text_gate.state is VerificationState.FAIL:
            result["blocker"] = text_gate.primary_reason
            return
        service.reviewer.begin_budget(min(config.ai_max_cost_rub, 50.0))
        deadline = time.monotonic() + 240
        service.reviewer.set_deadline(deadline)
        result["photo_attempted"] = True
        try:
            photo_review = service.reviewer.review_photos(listing, text_review)
        except AvitoServiceError as exc:
            diagnostics = getattr(exc, "diagnostics", {}) or {}
            result["blocker"] = getattr(exc, "code", "PHOTO_PROVIDER_ERROR")
            result["photo_error"] = {
                key: diagnostics.get(key) for key in (
                    "http_status", "provider_error_code", "transport_error_type",
                    "reservation_state", "accounted_cost_rub", "cost_estimated",
                ) if key in diagnostics
            }
            return
        result["photo"] = {
            "complete": photo_review.is_complete_for(listing),
            "coverage": len(photo_review.photo_coverage), "expected": len(listing.images),
            "verdict": photo_review.verdict.value, "error": photo_review.error,
            "cost_rub": photo_review.cost_rub,
        }
        analyzed = AnalyzedListing(listing, risks, photo_review)
        gate = evaluate_verification(listing, risks, photo_review, request, stage="final")
        if gate.state is not VerificationState.PASS or not is_customer_safe(
            analyzed, request, require_freshness=False,
        ):
            result["blocker"] = gate.primary_reason or "SAFETY_GATE"
            return
        result["final_refresh_attempted"] = True
        checked, apify_cost, estimated = service._verify_finalists(
            (analyzed,), request, deadline_at=deadline, allowance_usd=0.5,
            progress=None,
        )
        result["final_refresh"] = {"cost_usd": apify_cost, "estimated": estimated,
                                   "status": checked[0].listing.verification_status}
        result["finalists"] = [
            item.public_dict() for item in rank_listings(checked, request)
            if is_customer_safe(item.analyzed, request)
        ]
        if not result["finalists"]:
            result["blocker"] = "FINAL_REFRESH_OR_SAFETY"
    finally:
        write_json(output / "result.json", result)
        print("RECHECK_RESULT " + json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
