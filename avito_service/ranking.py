"""Comparable-only market evidence and fail-closed role assignment."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import math
from statistics import median

from .models import (
    AnalyzedListing, Recommendation, RecommendationRole, ReviewVerdict, Severity,
    SearchRequest,
)
from .matching import matches_listing_request


_REJECT_CODES = {
    "LISTING_INACTIVE", "INVALID_DIRECT_URL", "PHOTO_SET_INCOMPLETE",
    "NAND_DEFECT", "HARDWARE_DEFECT", "CONDITION_CONFLICT",
}
_CAUTION_CODES = {
    "MISSING_DESCRIPTION", "MISSING_PHOTOS", "NON_FINAL_PRICE", "ACTIVATION_CONFLICT",
}
_MIN_MARKET_SELLERS = 10
MIN_COMPARABLE_SELLERS = 3
MarketEvidence = tuple[int | None, int | None, float | None, int, int]


def _risk_codes(item: AnalyzedListing) -> set[str]:
    return {risk.code for risk in item.deterministic_risks}


def _seller_key(item: AnalyzedListing) -> str | None:
    # Display names are neither unique nor stable evidence of independence.
    return item.listing.seller.identity_hash or None


def independent_market_representatives(
    candidate: AnalyzedListing,
    references: tuple[AnalyzedListing, ...] | list[AnalyzedListing],
) -> tuple[AnalyzedListing, ...]:
    """Choose one real, stable-price observation per independent seller.

    References are expected to have already passed market-safety checks and
    exact comparable-key grouping. Unknown candidate identity cannot prove
    that any reference is external to the candidate's own seller.
    """
    own_seller = _seller_key(candidate)
    if not own_seller:
        return ()
    seller_listings: dict[str, list[AnalyzedListing]] = defaultdict(list)
    for reference in references:
        seller = _seller_key(reference)
        if (
            seller and seller != own_seller
            and reference.listing.listing_id != candidate.listing.listing_id
        ):
            seller_listings[seller].append(reference)
    # Keep a real middle listing for traceability; duplicate ads do not weight
    # the market, and no synthetic within-seller average is invented.
    representatives = [
        sorted(values, key=lambda value: (
            value.listing.acquisition_price or 0,
            value.listing.listing_id,
        ))[(len(values) - 1) // 2]
        for values in seller_listings.values()
    ]
    return tuple(sorted(
        representatives,
        key=lambda item: (item.listing.acquisition_price or 0, item.listing.listing_id),
    ))


def _matches_request(item: AnalyzedListing, request: SearchRequest) -> bool:
    listing = item.listing
    review = item.ai_review
    if not review.matches_request:
        return False
    total = listing.acquisition_price
    if total is not None and (
        (request.price_min is not None and total < request.price_min)
        or (request.price_max is not None and total > request.price_max)
    ):
        return False
    return matches_listing_request(
        listing,
        request,
        identified_model=review.identified_model,
        storage=review.storage,
        sim_variant=review.sim_variant,
        condition=review.condition,
        final=True,
    )


def _base_role(
    item: AnalyzedListing, request: SearchRequest, *, require_freshness: bool = True
) -> RecommendationRole | None:
    codes = _risk_codes(item)
    if codes & _REJECT_CODES or item.ai_review.verdict is ReviewVerdict.REJECT:
        return RecommendationRole.REJECTED
    if item.listing.acquisition_price is None:
        return RecommendationRole.CAUTION
    if not _matches_request(item, request):
        return None
    if (
        not item.ai_review.is_complete_for(item.listing)
        or item.listing.acquisition_price is None
        or item.listing.currency.upper() != "RUB"
        or not item.condition_evidence_complete()
        or (require_freshness and not item.listing.is_freshly_verified())
        or any(risk.severity is Severity.WARNING for risk in item.deterministic_risks)
        or bool(item.ai_review.conflicts)
        or bool(item.ai_review.defects)
        or bool(item.ai_review.price_conditions)
        or item.ai_review.verdict is ReviewVerdict.CAUTION
        or item.ai_review.confidence < 0.5
    ):
        return RecommendationRole.CAUTION
    return RecommendationRole.BACKUP


def is_customer_safe(
    item: AnalyzedListing, request: SearchRequest, *, require_freshness: bool = True
) -> bool:
    """Return whether a complete text-and-photo review may count toward the quota."""
    return bool(
        _base_role(item, request, require_freshness=require_freshness) is RecommendationRole.BACKUP
        and item.ai_review.is_complete_for(item.listing)
    )


def is_market_reference(item: AnalyzedListing, request: SearchRequest) -> bool:
    """Use clean text evidence for market price, without requiring a vision call."""
    review = item.ai_review
    return bool(
        item.listing.acquisition_price is not None
        and item.listing.currency.upper() == "RUB"
        and item.listing.is_recently_collected()
        and item.condition_evidence_complete()
        and item.configuration_evidence_complete()
        and item.listing.location
        and (request.pickup_only or item.listing.delivery)
        and review.text_analyzed
        and review.matches_request
        and review.verdict is ReviewVerdict.APPROVE
        and review.confidence >= 0.5
        and not review.defects
        and not review.conflicts
        and not review.price_conditions
        and not any(risk.severity is not Severity.INFO for risk in item.deterministic_risks)
        and _matches_request(item, request)
    )


def merge_market_evidence(
    analyzed: tuple[AnalyzedListing, ...],
    market_analyzed: tuple[AnalyzedListing, ...] | None,
) -> tuple[AnalyzedListing, ...]:
    """Retain paid text evidence while newer observations invalidate old approvals.

    Candidate text/vision findings supersede the market copy of the same ad.
    An unreviewed unchanged copy may reuse an already fresh market review;
    it never refreshes the market timestamp or erases later negative findings.
    """
    combined = {item.listing.listing_id: item for item in (market_analyzed or ())}

    def facts(item: AnalyzedListing):
        # Time/verification metadata do not change product evidence. Warehouse
        # metadata is deliberately irrelevant to this standalone Avito flow.
        return replace(item.listing, collected_at="", verified_at="", verification_status="",
                       stock="", parameters=item.listing.analysis_parameters)

    for candidate in analyzed:
        identifier = candidate.listing.listing_id
        previous = combined.get(identifier)
        review = candidate.ai_review
        observed_risk = (
            any(risk.severity is not Severity.INFO for risk in candidate.deterministic_risks)
            or review.verdict is ReviewVerdict.REJECT
            or review.photos_analyzed or bool(review.defects or review.conflicts or review.price_conditions)
        )
        if (previous is None or review.text_analyzed or observed_risk
                or not previous.listing.is_recently_collected() or facts(candidate) != facts(previous)):
            combined[identifier] = candidate
    return tuple(combined.values())


def _is_below_market(evidence: MarketEvidence | None) -> bool:
    return bool(_is_below_comparables(evidence) and evidence[3] >= _MIN_MARKET_SELLERS
                and evidence[4] >= _MIN_MARKET_SELLERS)


def _is_below_comparables(evidence: MarketEvidence | None) -> bool:
    if evidence is None:
        return False
    _, saving_amount, saving_percent, comparable_count, seller_count = evidence
    return bool(
        saving_amount is not None and saving_percent is not None and saving_amount > 0
        and saving_percent >= 0
        and comparable_count >= MIN_COMPARABLE_SELLERS
        and seller_count >= MIN_COMPARABLE_SELLERS
    )


def _quality(item: AnalyzedListing) -> float:
    seller = item.listing.seller
    rating = seller.rating or 0.0
    reviews = seller.review_count or 0
    return item.ai_review.confidence * 20 + rating * 4 + math.log10(reviews + 1) * 3


def _priority_key(
    item: AnalyzedListing,
    request: SearchRequest,
    evidence: dict[str, MarketEvidence],
) -> tuple[float, float, float]:
    price = float(item.listing.acquisition_price or 10**18)
    saving = evidence.get(item.listing.listing_id, (0, 0, 0.0, 0, 0))[2] or 0.0
    quality = _quality(item)
    if request.priority == "quality":
        return quality, saving, -price
    if request.priority == "budget":
        return -price, saving, quality
    return saving, quality, -price


def rank_listings(
    analyzed: tuple[AnalyzedListing, ...],
    request: SearchRequest,
    *,
    market_analyzed: tuple[AnalyzedListing, ...] | None = None,
    require_freshness: bool = True,
) -> tuple[Recommendation, ...]:
    # Pre-refresh planning uses the same market rules without pretending that
    # the listing was refreshed. Public serialization always checks freshness.
    roles = {item.listing.listing_id: _base_role(item, request, require_freshness=require_freshness)
             for item in analyzed}
    market_request = replace(request, price_min=None, price_max=None)
    market_groups: dict[tuple[str, ...], list[AnalyzedListing]] = defaultdict(list)
    references = merge_market_evidence(analyzed, market_analyzed)
    seen_references: set[str] = set()
    for item in references:
        if item.listing.listing_id in seen_references:
            continue
        seen_references.add(item.listing.listing_id)
        if is_market_reference(item, market_request):
            market_groups[item.comparable_key(pickup_only=request.pickup_only)].append(item)

    evidence: dict[str, MarketEvidence] = {}
    evidence_details: dict[str, tuple[int | None, int | None, str, tuple[dict[str, object], ...]]] = {}
    for candidate in analyzed:
        own_seller = _seller_key(candidate)
        if (not own_seller or not candidate.condition_evidence_complete()
                or not candidate.configuration_evidence_complete()):
            continue
        representatives = independent_market_representatives(
            candidate,
            market_groups.get(candidate.comparable_key(pickup_only=request.pickup_only), []),
        )
        independent_prices = [item.listing.acquisition_price for item in representatives if item.listing.acquisition_price is not None]
        if not independent_prices or candidate.listing.acquisition_price is None:
            continue
        seller_count = len(representatives)
        market_median = int(median(independent_prices)) if seller_count >= 3 else None
        saving_amount = market_median - candidate.listing.acquisition_price if market_median else None
        saving_percent = round(saving_amount / market_median * 100, 1) if market_median else None
        evidence[candidate.listing.listing_id] = (
            market_median, saving_amount, saving_percent, seller_count, seller_count,
        )
        links = tuple({
            "url": item.listing.url,
            "price": item.listing.acquisition_price,
            "listingPrice": item.listing.price,
            "priceBasis": item.listing.price_basis,
            "seller": item.listing.seller.name,
            "observedAt": item.listing.collected_at,
        } for item in sorted(representatives, key=lambda value: value.listing.acquisition_price or 0))
        evidence_details[candidate.listing.listing_id] = (
            min(independent_prices) if seller_count >= 3 else None,
            max(independent_prices) if seller_count >= 3 else None,
            "moderate" if seller_count >= _MIN_MARKET_SELLERS else "limited" if seller_count >= 3 else "insufficient", links,
        )

    eligible = [item for item in analyzed if roles[item.listing.listing_id] is RecommendationRole.BACKUP]
    below_market = [
        item for item in eligible
        if _is_below_comparables(evidence.get(item.listing.listing_id))
    ]
    if request.mode == "find" and eligible:
        # Exact-search priorities apply to every safe match. A comparison sample
        # for a different variant must not outrank the client's cheapest match.
        top = max(eligible, key=lambda item: _priority_key(item, request, evidence))
        roles[top.listing.listing_id] = RecommendationRole.TOP
    elif below_market:
        top = max(below_market, key=lambda item: _priority_key(item, request, evidence))
        roles[top.listing.listing_id] = RecommendationRole.TOP
    elif eligible:
        # Exact-match admission survives missing or non-positive market evidence.
        # The market proof only decides whether the card may claim savings.
        top = max(eligible, key=lambda item: _priority_key(item, request, evidence))
        roles[top.listing.listing_id] = RecommendationRole.TOP
    else:
        top = None

    remaining = [item for item in eligible if item is not top]
    backup = max(remaining, key=_quality, default=None)
    if backup is None and top is None:
        backup = max(eligible, key=_quality, default=None)
    if backup is not None:
        roles[backup.listing.listing_id] = RecommendationRole.BACKUP

    selected_prices = [
        item.listing.acquisition_price for item in (top, backup)
        if item is not None and item.listing.acquisition_price is not None
    ]
    budget_ceiling = min(selected_prices) if selected_prices else None
    budget_pool = [
        item for item in remaining
        if item is not backup and item.listing.acquisition_price is not None
        and budget_ceiling is not None and item.listing.acquisition_price < budget_ceiling
    ]
    budget = min(budget_pool, key=lambda item: item.listing.acquisition_price or 10**18, default=None)
    if budget is not None:
        roles[budget.listing.listing_id] = RecommendationRole.BUDGET

    result: list[Recommendation] = []
    for item in analyzed:
        role = roles[item.listing.listing_id]
        if role is None:
            continue
        market_median, saving_amount, saving_percent, comparable_count, seller_count = evidence.get(
            item.listing.listing_id, (None, None, None, 0, 0)
        )
        reasons: list[str] = []
        risks = [risk.message for risk in item.deterministic_risks if risk.severity is not Severity.INFO]
        risks.extend(item.ai_review.defects)
        risks.extend(item.ai_review.conflicts)
        risks.extend(item.ai_review.price_conditions)
        risks.extend(item.history_warnings())
        confirmed_bargain = (role in {RecommendationRole.TOP, RecommendationRole.BACKUP, RecommendationRole.BUDGET}
                             and _is_below_market(evidence.get(item.listing.listing_id)))
        comparable_saving = (role in {RecommendationRole.TOP, RecommendationRole.BACKUP, RecommendationRole.BUDGET}
                              and _is_below_comparables(evidence.get(item.listing.listing_id)))
        market_min, market_max, market_confidence, market_links = evidence_details.get(
            item.listing.listing_id, (None, None, "insufficient", ())
        )
        if market_confidence == "insufficient":
            risks.append(
                f"Малая выборка: {seller_count} внешних продавцов; оценка рынка предварительная."
                if seller_count else "Недостаточно сопоставимых свежих предложений для оценки рынка."
            )
        elif market_confidence == "limited":
            risks.append(f"Небольшая выборка: {seller_count} независимых продавцов; разница относится к найденным аналогам.")
        if not item.condition_evidence_complete():
            risks.append("В объявлении недостаточно данных о состоянии, деталях или комплекте.")
        if not item.configuration_evidence_complete():
            risks.append("Основная конфигурация указана не полностью; сравнение с рынком не подтверждено.")
        if not item.listing.is_freshly_verified():
            risks.append("Цена и активность объявления не обновлены перед выдачей.")
        if item.listing.acquisition_price is None:
            risks.append("Неизвестна обязательная комиссия или стоимость обязательной доставки.")
        if role is RecommendationRole.TOP:
            if request.mode == "bargain":
                if confirmed_bargain:
                    reasons.append(
                        f"Разница {saving_amount:,} ₽ ({saving_percent:.1f}%): цена ниже медианы выборки "
                        f"{market_median:,} ₽ для той же конфигурации."
                        .replace(",", " ")
                    )
                else:
                    reasons.append("Точное совпадение по запросу. Выгода не подтверждена сопоставимым рынком.")
            elif request.priority == "quality":
                reasons.append("Лучший по данным объявления, фотографиям и сведениям о продавце.")
            elif request.priority == "budget":
                reasons.append("Самый доступный подходящий вариант после анализа объявления и фотографий.")
            else:
                reasons.append("Лучший баланс точности, состояния, продавца и цены.")
        elif role is RecommendationRole.BACKUP:
            if confirmed_bargain:
                reasons.append(
                    f"Цена ниже медианы сопоставимой выборки на {saving_amount:,} ₽ ({saving_percent:.1f}%)."
                    .replace(",", " ")
                )
            else:
                reasons.append("Запасной вариант после анализа описания и всех фото.")
        elif role is RecommendationRole.BUDGET:
            if confirmed_bargain:
                reasons.append(
                    f"Доступный вариант: разница с медианой выборки {saving_amount:,} ₽."
                    .replace(",", " ")
                )
            else:
                reasons.append("Самый доступный из оставшихся вариантов, прошедших анализ объявления.")
        elif role is RecommendationRole.CAUTION:
            risks.append("Нельзя рекомендовать без устранения отмеченных противоречий или пробелов.")
        elif role is RecommendationRole.REJECTED:
            risks.append("Обнаружен критический дефект или несоответствие.")
        result.append(Recommendation(
            role=role,
            analyzed=item,
            reasons=tuple(dict.fromkeys(reasons)),
            risks=tuple(dict.fromkeys(risks)),
            market_median=market_median,
            saving_amount=saving_amount,
            saving_percent=saving_percent,
            comparable_count=comparable_count,
            comparable_seller_count=seller_count,
            below_market=confirmed_bargain,
            below_comparables=comparable_saving,
            market_min=market_min,
            market_max=market_max,
            market_confidence=market_confidence,
            market_evidence=market_links,
        ))

    order = {
        RecommendationRole.TOP: 0,
        RecommendationRole.BACKUP: 1,
        RecommendationRole.BUDGET: 2,
        RecommendationRole.CAUTION: 3,
        RecommendationRole.REJECTED: 4,
    }
    return tuple(sorted(result, key=lambda item: (order[item.role], item.analyzed.listing.acquisition_price or 10**18)))
