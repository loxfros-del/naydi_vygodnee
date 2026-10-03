"""Continue a saved PS5 pilot candidate through photo AI and final URL refresh."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
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


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-output", type=Path, required=True)
    parser.add_argument("--listing-id", required=True)
    parser.add_argument("--authorized-apify-budget-usd", type=float)
    parser.add_argument("--ai-proxy-mode", choices=("system", "direct"),
                        help="Choose one AI route before sending; never fall back after a POST")
    parser.add_argument("--live", action="store_true", help="Send one photo request, then refresh only if safe")
    parser.add_argument("--confirm-photo-resend", action="store_true",
                        help="Use only after the owner explicitly confirms resending these photos")
    args = parser.parse_args(argv)
    if args.live and not args.confirm_photo_resend:
        parser.error("Live requires separate owner confirmation of this photo resend.")
    if args.live and (args.authorized_apify_budget_usd is None
                     or not math.isfinite(args.authorized_apify_budget_usd)
                     or not 0 < args.authorized_apify_budget_usd <= 8):
        parser.error("Live requires the remaining authorized Apify budget, at most $8.")
    source = args.source_output.resolve()
    listing = load_listing(source / "candidates-normalized.json", args.listing_id)
    request = SearchRequest("PS5", location="Ярославль", category="gaming",
                            max_results=200, desired_results=3, pickup_only=True)
    text_review = reconcile_ps5_family_mismatch(
        listing, load_text_review(source / "all-decisions.json", args.listing_id), request,
    )
    config = expanded_review_config(load_config())
    if args.ai_proxy_mode is not None:
        config = replace(config, ai_proxy_mode=args.ai_proxy_mode)
    service = build_service(config)
    risks = evaluate_rules(listing)
    text_gate = evaluate_verification(listing, risks, text_review, request, stage="text")
    payload = service.reviewer.build_photo_payload(listing, config.ai_model, text_review)
    reserve = service.reviewer.estimate_payload_cost(payload)
    ai_limit = min(config.ai_max_cost_rub, 50.0)
    plan = {
        "listing_id": listing.listing_id, "saved_price_rub": listing.price,
        "photo_count": len(listing.images), "model": config.ai_model,
        "ai_proxy_mode": config.ai_proxy_mode,
        "text_gate": text_gate.state.value, "text_reason": text_gate.primary_reason,
        "ai_reserve_rub": reserve, "ai_limit_rub": ai_limit,
        "final_refresh_max_usd": min(config.apify_max_charge_usd, 0.5),
        "automatic_paid_retries": False, "live": args.live,
    }
    print("RECHECK_PLAN " + json.dumps(plan, ensure_ascii=True), flush=True)
    if not args.live:
        return
    if (text_gate.state is VerificationState.FAIL or text_gate.next_stage != "photo_ai"
            or text_review.error or not listing.images or reserve > ai_limit):
        raise ValueError("Candidate text/photos/budget preflight failed before any paid call.")
    # Read the current account immediately before any spending. Do not change
    # its hard limit. The caller supplies the *remaining* authorized allowance.
    guard, account = account_bounded_spending_guard(
        config, service.provider.spending_guard, args.authorized_apify_budget_usd,
        ZenStudioProvider(config),
    )
    output = ROOT / "runtime" / "avito_pilot" / (
        "recheck-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    output.mkdir(parents=True, exist_ok=False)
    service.provider = PilotProvider(config, output, spending_guard=guard)
    print("RECHECK_BUDGET " + json.dumps(account), flush=True)
    print("RECHECK_OUTPUT " + json.dumps(str(output), ensure_ascii=True), flush=True)
    result = {"listing_id": args.listing_id, "photo_attempted": False,
              "final_refresh_attempted": False, "finalists": [], "plan": plan,
              "account": account}
    write_json(output / "plan.json", plan)
    try:
        service.reviewer.begin_budget(ai_limit)
        service.reviewer.retry_rate_limits = False
        deadline = time.monotonic() + 240
        service.reviewer.set_deadline(deadline)
        result["photo_attempted"] = True
        # Persist before POST: even process termination must leave a conservative
        # liability, never an empty result that looks like a free failure.
        result["photo_cost"] = {"reservation_state": "retained_uncertain",
                                "accounted_cost_rub": reserve, "cost_estimated": True}
        write_json(output / "result.json", result)
        try:
            photo_review = service.reviewer.review_photos(listing, text_review, allow_retry=False)
        except AvitoServiceError as exc:
            diagnostics = getattr(exc, "diagnostics", {}) or {}
            result["blocker"] = getattr(exc, "code", "PHOTO_PROVIDER_ERROR")
            result["photo_error"] = {
                key: diagnostics.get(key) for key in (
                    "http_status", "provider_error_code", "transport_error_type",
                    "transport_phase", "transport_errno", "transport_winerror", "tls_reason",
                    "transport_elapsed_seconds", "transport_timeout_seconds", "transport_proxy_mode",
                    "request_outcome", "reservation_rub", "reservation_blocked",
                    "reservation_state", "accounted_cost_rub", "cost_estimated",
                ) if key in diagnostics
            }
            for key in ("reservation_state", "accounted_cost_rub", "cost_estimated"):
                if key in diagnostics:
                    result["photo_cost"][key] = diagnostics[key]
            return
        write_json(output / "photo-review.json", asdict(photo_review))
        result["photo_cost"] = {
            "reservation_state": "settled_estimate" if photo_review.cost_estimated else "settled_actual",
            "accounted_cost_rub": photo_review.cost_rub, "cost_estimated": photo_review.cost_estimated,
        }
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
        write_json(output / "result.json", result)
        checked, apify_cost, estimated = service._verify_finalists(
            (analyzed,), request, deadline_at=deadline,
            allowance_usd=min(plan["final_refresh_max_usd"], account["new_spend_ceiling_usd"]),
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
    except Exception as exc:
        result["blocker"] = getattr(exc, "code", "RECHECK_ERROR")
        result["error_type"] = type(exc).__name__
        raise
    finally:
        result["ai_budget"] = service.reviewer.budget_snapshot()
        write_json(output / "result.json", result)
        print("RECHECK_RESULT " + json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
