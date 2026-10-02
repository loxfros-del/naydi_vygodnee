"""Directly refresh and classify only saved Avito finalists; never runs a search."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.config import load_config
from avito_service.final_verification import FinalVerificationInput, classify_final_bargain
from avito_service.http_api import build_service
from avito_service.market_engine import build_market_snapshots, normalized_sku, seller_lane
from avito_service.models import AIReview, ReviewVerdict, SellerSummary
from avito_service.normalization import normalize_dataset
from avito_service.risk_rules import evaluate_rules


FINALISTS = ("8139333912", "8318570064", "8374041219")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _image_key(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _review_from_file(path: Path) -> AIReview:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["verdict"] = ReviewVerdict(raw["verdict"])
    for field in ("photo_coverage", "description_findings", "photo_findings", "defects",
                  "price_conditions", "conflicts", "manual_checks"):
        raw[field] = tuple(raw.get(field, ()))
    return AIReview(**raw)


def _photo_review_admissible(item, *, live: bool, text_status: str, sku_exact: bool,
                             photo_status: str, risks) -> bool:
    """Apply listing safety before vision; market sample never participates."""
    return bool(
        live and item.is_freshly_verified() and item.status.casefold() == "active"
        and text_status == "passed" and sku_exact
        and item.effective_price is not None and item.price_confidence in {"exact", "likely"}
        and photo_status == "missing" and item.images
        and not any(risk.severity.value != "info" for risk in risks)
    )


def _sanitized_raw(rows: tuple[dict, ...]) -> list[dict[str, object]]:
    allowed = {
        "id", "avitoId", "title", "url", "price", "currency", "status", "description",
        "images", "imageCount", "parameters", "location", "address", "delivery",
        "deliveryAvailable", "scrapedAt", "collectedAt", "sellerId", "userId",
        "sellerUrl", "sellerProfileUrl", "userType", "seller",
    }
    result = []
    for row in rows:
        clean = {key: value for key, value in row.items() if key in allowed}
        seller = clean.get("seller")
        if isinstance(seller, dict):
            seller_allowed = {
                "id", "sellerId", "userId", "seller_id", "user_id", "userKey", "profileId",
                "url", "profileUrl", "sellerUrl", "profile_url", "name", "sellerType", "isShop",
                "ratingScore", "reviewCount", "memberSince",
            }
            clean["seller"] = {key: value for key, value in seller.items() if key in seller_allowed}
        result.append(clean)
    return result


def _saved_seller_fallbacks(run_dir: Path) -> dict[str, SellerSummary]:
    """Recover non-stable seller labels already stored in the paid run."""
    result: dict[str, SellerSummary] = {}
    paths = [run_dir / "all-decisions.json"]
    paths.extend(sorted(run_dir.parent.glob("*/all-decisions.json"), reverse=True))
    for path in dict.fromkeys(paths):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = raw if isinstance(raw, list) else raw.get("decisions", raw.get("items", ()))
        for row in rows:
            listing = row.get("listing", row) if isinstance(row, dict) else {}
            seller = listing.get("seller", {}) if isinstance(listing, dict) else {}
            identifier = str(listing.get("listing_id", listing.get("id", "")))
            if (identifier in result or not identifier or not isinstance(seller, dict)
                    or not str(seller.get("name", "")).strip()):
                continue
            result[identifier] = SellerSummary(
                name=str(seller.get("name", "")).strip(),
                seller_type=str(seller.get("seller_type", seller.get("sellerType", ""))).strip(),
                rating=seller.get("rating"),
                review_count=seller.get("review_count", seller.get("reviewCount")),
                member_since=str(seller.get("member_since", seller.get("memberSince", ""))).strip(),
                is_shop=bool(seller.get("is_shop", seller.get("isShop", False))),
            )
    return result


def run(run_dir: Path, output_dir: Path, *, live: bool, allow_photo_ai: bool,
        refresh_ids: tuple[str, ...] = FINALISTS, resume_refresh: Path | None = None) -> dict[str, object]:
    config = load_config()
    facts = json.loads((run_dir / "market-facts.json").read_text(encoding="utf-8"))
    saved = normalize_dataset(facts)
    seller_fallbacks = _saved_seller_fallbacks(run_dir)
    saved = tuple(
        replace(item, seller=seller_fallbacks[item.listing_id])
        if not item.seller.name.strip() and item.listing_id in seller_fallbacks else item
        for item in saved
    )
    saved_by_id = {item.listing_id: item for item in saved}
    missing = set(FINALISTS) - saved_by_id.keys()
    if missing:
        raise ValueError(f"Saved finalists missing: {sorted(missing)}")

    # One shared market, excluding all finalists themselves.
    references = tuple(item for item in saved if item.listing_id not in FINALISTS)
    observed_date = max((item.collected_at[:10] for item in references if item.collected_at), default="unknown")
    snapshots = build_market_snapshots(references, city="Ярославль", observed_date=observed_date)
    audit = json.loads((run_dir / "audit.json").read_text(encoding="utf-8"))
    cards = {str(card["listing"]["id"]): card for card in audit.get("adminRecommendations", ())}

    current = {identifier: saved_by_id[identifier] for identifier in FINALISTS}
    apify_cost_usd = 0.0
    apify_cost_estimated = False
    refresh_count = 0
    if live:
        if resume_refresh is not None:
            raw_rows = tuple(json.loads(resume_refresh.read_text(encoding="utf-8")))
            previous_path = output_dir / "final-verification.json"
            if previous_path.exists():
                previous = json.loads(previous_path.read_text(encoding="utf-8"))
                previous_cost = previous.get("cost", {})
                apify_cost_usd = float(previous_cost.get("apify_usd", 0))
                apify_cost_estimated = bool(previous_cost.get("apify_estimated"))
                refresh_count = int(previous.get("direct_refresh_starts", 0))
        else:
            service = build_service(config)
            # One Actor start, only the explicitly selected direct URLs.
            batch = service.provider.refresh(
                tuple(current[identifier] for identifier in refresh_ids),
                deadline_at=time.monotonic() + 180,
                max_charge_usd=min(0.035, 0.007 + len(refresh_ids) * 0.008),
            )
            refresh_count = 1
            apify_cost_usd = batch.apify_cost_usd
            apify_cost_estimated = batch.apify_cost_estimated
            raw_rows = tuple(row for row in batch.items if isinstance(row, dict))
            _write(output_dir / "direct-refresh-facts.json", _sanitized_raw(raw_rows))
        refreshed = {item.listing_id: item for item in normalize_dataset(raw_rows)}
        now = datetime.now(timezone.utc).isoformat()
        for identifier in refresh_ids:
            item = refreshed.get(identifier)
            if item is not None:
                current[identifier] = replace(
                    item,
                    verified_at=now,
                    verification_status="verified" if item.status.casefold() == "active" else "failed",
                )

    results = []
    ai_photo_cost_rub = 0.0
    photo_ai_calls = 0
    for identifier in FINALISTS:
        item = current[identifier]
        original = saved_by_id[identifier]
        card = cards.get(identifier, {})
        analysis = card.get("analysis", {})
        text_status = "passed" if (
            analysis.get("textAnalyzed") and analysis.get("matchesRequest")
            and analysis.get("verdict") == "approve" and not analysis.get("conflicts")
            and not analysis.get("defects") and not analysis.get("priceConditions")
            and not analysis.get("mismatchReason")
        ) else "failed"
        photos_unchanged = (
            tuple(map(_image_key, item.images)) == tuple(map(_image_key, original.images))
            and bool(item.images)
        )
        saved_photo_ok = bool(
            analysis.get("photosAnalyzed") and analysis.get("complete")
            and not analysis.get("defects") and not analysis.get("conflicts")
        )
        photo_status = "reused_unchanged" if saved_photo_ok and photos_unchanged else "missing"

        sku = normalized_sku(item)
        sku_exact = bool(sku and item.price_confidence in {"exact", "likely"})
        risks = evaluate_rules(item)
        snapshot = snapshots.get(sku.key) if sku else None
        lane = seller_lane(item)
        segment = snapshot.segment(lane) if snapshot else None
        market_lane_fallback = False
        if segment is None and snapshot:
            fallback = snapshot.segment("unknown")
            if fallback:
                segment = fallback
                market_lane_fallback = True
        conservative = segment.p25 if segment else None
        market_median = segment.median if segment else None
        # Comparables decide whether a savings label is allowed, not whether a
        # text-safe, exact-price listing may finish its photo safety check.
        photo_admission = _photo_review_admissible(
            item, live=live, text_status=text_status, sku_exact=sku_exact,
            photo_status=photo_status, risks=risks,
        )
        if allow_photo_ai and photo_admission:
            text_path = run_dir / f"review_text_batch-{identifier}.json"
            if text_path.exists():
                text_review = _review_from_file(text_path)
                reviewed = build_service(load_config()).reviewer.review_photos(item, text_review)
                photo_ai_calls += 1
                ai_photo_cost_rub += max(0.0, reviewed.cost_rub - text_review.cost_rub)
                if reviewed.is_complete_for(item) and not reviewed.defects and not reviewed.conflicts:
                    photo_status = "passed"
                else:
                    photo_status = "failed"

        critical = tuple(risk.code for risk in risks if risk.severity.value == "critical")
        manual = []
        if not item.parts_status:
            manual.append("INTERNAL_PARTS_ORIGINALITY_UNKNOWN")
        if not item.repair_status:
            manual.append("REPAIR_HISTORY_UNKNOWN")
        if market_lane_fallback:
            manual.append("MARKET_SELLER_TYPE_UNKNOWN")
        if not item.seller.identity_hash:
            manual.append("FINALIST_STABLE_SELLER_ID_MISSING")

        extra = max(0, item.mandatory_fee_rub or 0)
        if item.delivery_required:
            extra += max(0, item.delivery_cost_rub or 0)
        live_status = (
            "active_price_verified" if live and item.status.casefold() == "active"
            and item.verification_status == "verified" else
            "inactive" if live and item.status.casefold() != "active" else "not_refreshed"
        )
        result = classify_final_bargain(FinalVerificationInput(
            listing_id=identifier,
            advertised_price=item.advertised_price,
            effective_price=item.effective_price,
            conservative_market_price=conservative,
            market_median=market_median,
            comparable_count=segment.listing_count if segment else 0,
            independent_seller_count=segment.seller_count if segment else 0,
            stable_seller_count=segment.stable_seller_count if segment else 0,
            estimated_extra_costs=extra,
            minimum_bargain_percent=config.minimum_bargain_percent,
            minimum_bargain_rub=config.minimum_bargain_rub,
            price_confidence=item.price_confidence,
            sku_exact=sku_exact,
            text_status=text_status,
            photo_status=photo_status,
            live_status=live_status,
            critical_conflicts=critical,
            manual_checks=tuple(manual),
        ))
        payload = result.public_dict()
        payload.update({
            "title": item.title,
            "model": item.price_truth.model,
            "variant": item.price_truth.variant,
            "condition": item.price_truth.condition,
            "location": item.location or item.address,
            "seller": item.seller.public_dict(),
            "price_confidence": item.price_confidence,
            "price_conditions": list(item.price_truth.payment_conditions),
            "market_evidence": {
                "p25": segment.p25 if segment else None,
                "p75": segment.p75 if segment else None,
                "minimum_sane_price": segment.minimum_sane_price if segment else None,
                "comparable_ids": list(segment.listing_ids) if segment else [],
                "comparables": [{
                    "id": comparable.listing_id,
                    "url": comparable.url,
                    "saved_status": comparable.status,
                    "effective_price": comparable.effective_price,
                    "price_confidence": comparable.price_confidence,
                    "condition": comparable.price_truth.condition,
                    "seller": comparable.seller.public_dict(),
                    "price_conditions": list(comparable.price_truth.payment_conditions),
                    "collected_at": comparable.collected_at,
                } for comparable in (
                    saved_by_id[comparable_id]
                    for comparable_id in (segment.listing_ids if segment else ())
                    if comparable_id in saved_by_id
                )],
            },
        })
        results.append(payload)

    report = {
        "source": str(run_dir),
        "mode": "live_direct_refresh" if live else "offline_preflight",
        "mass_searches": 0,
        "direct_refresh_starts": refresh_count,
        "refreshed_listing_count": len(refresh_ids) if live else 0,
        "refreshed_ids": list(refresh_ids) if live else [],
        "photo_ai_calls": photo_ai_calls,
        "minimum_bargain_percent": config.minimum_bargain_percent,
        "minimum_bargain_rub": config.minimum_bargain_rub,
        "cost": {
            "apify_usd": apify_cost_usd,
            "apify_estimated": apify_cost_estimated,
            "ai_photo_rub": round(ai_photo_cost_rub, 4),
            "estimated_total_rub": round(apify_cost_usd * config.usd_rub_rate + ai_photo_cost_rub, 2),
        },
        "results": results,
    }
    _write(output_dir / "final-verification.json", report)
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-photo-ai", action="store_true")
    parser.add_argument("--refresh-id", action="append", choices=FINALISTS)
    parser.add_argument("--resume-refresh", type=Path)
    args = parser.parse_args(argv)
    if args.allow_photo_ai and not args.live:
        parser.error("--allow-photo-ai requires --live")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    refresh_ids = tuple(dict.fromkeys(args.refresh_id or FINALISTS))
    report = run(args.run_dir, args.output_dir, live=args.live,
                 allow_photo_ai=args.allow_photo_ai, refresh_ids=refresh_ids,
                 resume_refresh=args.resume_refresh)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
