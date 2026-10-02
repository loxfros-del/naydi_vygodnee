"""Fail-closed final bargain classification after a direct listing refresh."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FinalStatus(str, Enum):
    CONFIRMED_BARGAIN = "CONFIRMED_BARGAIN"
    HIGH_CONFIDENCE_BARGAIN = "HIGH_CONFIDENCE_BARGAIN"
    EXACT_MATCH = "EXACT_MATCH"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class FinalVerificationInput:
    listing_id: str
    advertised_price: int | None
    effective_price: int | None
    conservative_market_price: int | None
    market_median: int | None
    comparable_count: int
    independent_seller_count: int
    stable_seller_count: int = 0
    estimated_extra_costs: int = 0
    minimum_bargain_percent: float = 0.0
    minimum_bargain_rub: int = 0
    price_confidence: str = "invalid"
    sku_exact: bool = False
    text_status: str = "missing"
    photo_status: str = "missing"
    live_status: str = "not_refreshed"
    critical_conflicts: tuple[str, ...] = ()
    manual_checks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalVerificationResult:
    listing_id: str
    advertised_price: int | None
    effective_price: int | None
    conservative_market_price: int | None
    market_median: int | None
    comparable_count: int
    independent_seller_count: int
    saving_rub: int | None
    saving_percent: float | None
    estimated_extra_costs: int
    conservative_net_saving: int | None
    text_status: str
    photo_status: str
    live_status: str
    final_status: FinalStatus
    blockers: tuple[str, ...]
    manual_checks: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.listing_id,
            "advertised_price": self.advertised_price,
            "effective_price": self.effective_price,
            "conservative_market_price": self.conservative_market_price,
            "market_median": self.market_median,
            "comparable_count": self.comparable_count,
            "independent_seller_count": self.independent_seller_count,
            "saving_rub": self.saving_rub,
            "saving_percent": self.saving_percent,
            "estimated_extra_costs": self.estimated_extra_costs,
            "conservative_net_saving": self.conservative_net_saving,
            "text_status": self.text_status,
            "photo_status": self.photo_status,
            "live_status": self.live_status,
            "final_status": self.final_status.value,
            "blockers": list(self.blockers),
            "manual_checks": list(self.manual_checks),
        }


def classify_final_bargain(value: FinalVerificationInput) -> FinalVerificationResult:
    blockers: list[str] = list(value.critical_conflicts)
    price = value.effective_price
    benchmark = value.conservative_market_price
    sample_ok = value.comparable_count >= 3 and value.independent_seller_count >= 3
    raw_saving = benchmark - price if benchmark is not None and price is not None else None
    raw_saving_percent = round(raw_saving / benchmark * 100, 1) if raw_saving is not None and benchmark else None
    raw_net = raw_saving - max(0, value.estimated_extra_costs) if raw_saving is not None else None
    minimum_met = bool(
        raw_net is not None
        and raw_net >= max(0, value.minimum_bargain_rub)
        and raw_saving_percent is not None
        and raw_saving_percent >= max(0.0, value.minimum_bargain_percent)
        and sample_ok
    )
    saving = raw_saving if sample_ok and minimum_met and raw_saving is not None and raw_saving > 0 else None
    saving_percent = raw_saving_percent if saving is not None else None
    net = raw_net if saving is not None else None

    if price is None or value.price_confidence not in {"exact", "likely"}:
        blockers.append("EFFECTIVE_PRICE_NOT_CONFIRMED")
    if benchmark is None:
        blockers.append("CONSERVATIVE_MARKET_UNAVAILABLE")
    elif raw_saving is not None and raw_saving <= 0:
        blockers.append("NOT_BELOW_CONSERVATIVE_MARKET")
    if raw_net is not None and raw_net <= 0:
        blockers.append("NO_CONSERVATIVE_NET_SAVING")
    if raw_saving is not None and raw_saving > 0 and not minimum_met:
        blockers.append("MINIMUM_BARGAIN_NOT_MET")
    if not value.sku_exact:
        blockers.append("SKU_NOT_EXACT")
    if value.comparable_count < 3:
        blockers.append(f"COMPARABLE_COUNT_{value.comparable_count}_LT_3")
    if value.independent_seller_count < 3:
        blockers.append(f"INDEPENDENT_SELLER_COUNT_{value.independent_seller_count}_LT_3")

    critical = bool(value.critical_conflicts) or not value.sku_exact
    fully_live = value.live_status == "active_price_verified"
    text_ok = value.text_status == "passed"
    photo_ok = value.photo_status in {"passed", "reused_unchanged"}
    bargain_evidence = bool(
        sample_ok and raw_saving is not None and raw_saving > 0
        and raw_net is not None and raw_net > 0 and minimum_met
    )

    if critical:
        status = FinalStatus.REJECT
    elif (price is None or value.price_confidence not in {"exact", "likely"}
          or not fully_live or not text_ok or not photo_ok):
        status = FinalStatus.REVIEW
    elif bargain_evidence:
        if value.stable_seller_count >= 2:
            status = FinalStatus.CONFIRMED_BARGAIN
        else:
            status = FinalStatus.HIGH_CONFIDENCE_BARGAIN
            if value.stable_seller_count < 2:
                blockers.append("STABLE_SELLER_IDS_INCOMPLETE")
    else:
        status = FinalStatus.EXACT_MATCH

    if status is FinalStatus.REVIEW:
        if not fully_live:
            blockers.append("LIVE_PRICE_OR_ACTIVITY_NOT_VERIFIED")
        if not text_ok:
            blockers.append("TEXT_CHECK_INCOMPLETE")
        if not photo_ok:
            blockers.append("PHOTO_CHECK_INCOMPLETE")

    # Non-negotiable safety invariant requested by the owner.
    if status in {FinalStatus.CONFIRMED_BARGAIN, FinalStatus.HIGH_CONFIDENCE_BARGAIN}:
        if price is None or benchmark is None or price >= benchmark:
            status = FinalStatus.REJECT
            blockers.append("BARGAIN_STATUS_PRICE_GUARD")

    return FinalVerificationResult(
        listing_id=value.listing_id,
        advertised_price=value.advertised_price,
        effective_price=price,
        conservative_market_price=benchmark,
        market_median=value.market_median,
        comparable_count=value.comparable_count,
        independent_seller_count=value.independent_seller_count,
        saving_rub=saving,
        saving_percent=saving_percent,
        estimated_extra_costs=max(0, value.estimated_extra_costs),
        conservative_net_saving=net,
        text_status=value.text_status,
        photo_status=value.photo_status,
        live_status=value.live_status,
        final_status=status,
        blockers=tuple(dict.fromkeys(blockers)),
        manual_checks=value.manual_checks,
    )
