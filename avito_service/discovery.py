"""Visible source findings, kept separate from verified purchase recommendations."""
from __future__ import annotations

from .matching import _ATTRIBUTE_PARAMETERS, _city_key, _model_matches, _requirement_matches, evidence_known
from .models import AnalyzedListing, DiscoveredListing, Recommendation, ReviewVerdict, SearchRequest, Severity
from .ranking import is_customer_safe
from .risk_rules import _is_direct_listing_url


def build_discovered_listings(
    analyzed: tuple[AnalyzedListing, ...],
    request: SearchRequest,
    recommendations: tuple[Recommendation, ...] = (),
) -> tuple[DiscoveredListing, ...]:
    verified = [item for item in recommendations if is_customer_safe(item.analyzed, request)]
    verified = verified[:request.desired_results]
    remaining = max(0, request.desired_results - len(verified))
    seen_ids = {item.analyzed.listing.listing_id for item in verified}
    seen_urls = {item.analyzed.listing.url.split("?", 1)[0].rstrip("/") for item in verified}
    candidates = []
    for item in analyzed:
        listing, review = item.listing, item.ai_review
        url = listing.url.split("?", 1)[0].rstrip("/")
        if (listing.listing_id in seen_ids or url in seen_urls or not listing.title.strip()
                or not _is_direct_listing_url(listing.url, listing.listing_id)):
            continue
        seen_ids.add(listing.listing_id)
        seen_urls.add(url)
        # Unknown price/condition is a review gap, not proof of a different model.
        model = review.identified_model or listing.model
        matched = not evidence_known(model) or _model_matches(request.query, model)
        if request.location.casefold().strip() not in {"", "россия", "вся россия", "rossiya", "russia", "all"}:
            matched = matched and (not evidence_known(listing.location)
                                   or _city_key(listing.location) == _city_key(request.location))
        for expected, actual, field in (
            (request.required_storage, review.storage or listing.storage, "storage"),
            (request.required_sim, review.sim_variant or listing.sim_variant, "sim"),
            (request.required_condition, review.condition or listing.condition, "condition"),
        ):
            matched = matched and _requirement_matches(expected, actual, field=field)
        for name, expected in request.attributes:
            actual = " ".join(listing.parameter(parameter) for parameter in _ATTRIBUTE_PARAMETERS.get(name, (name,)))
            matched = matched and _requirement_matches(expected, actual)
        matched = matched and (not review.text_analyzed or review.matches_request)
        inactive = listing.status.casefold().strip() not in {"active", "", "unknown", "неизвестно", "unconfirmed"}
        critical = any(risk.severity is Severity.CRITICAL for risk in item.deterministic_risks)
        critical = critical or bool(review.defects) or review.verdict is ReviewVerdict.REJECT
        notes = [risk.message for risk in item.deterministic_risks if risk.severity is not Severity.INFO]
        notes.extend((*review.defects, *review.conflicts, *review.price_conditions))
        if not matched:
            notes.append(review.mismatch_reason or "Есть отличия от модели, города или обязательных характеристик запроса.")
        price = listing.acquisition_price or listing.price
        outside_budget = bool(price is not None and (
            (request.price_min is not None and price < request.price_min)
            or (request.price_max is not None and price > request.price_max)
        ))
        if outside_budget:
            notes.append("Цена объявления вне указанного диапазона бюджета.")
        if listing.acquisition_price is None:
            notes.append("Полная цена с обязательными доплатами не подтверждена.")
        if not item.condition_evidence_complete():
            notes.append("Не хватает сведений, чтобы подтвердить состояние товара.")
        if not review.text_analyzed:
            notes.append("Описание не прошло полную AI-проверку.")
        if not review.photos_analyzed:
            notes.append("Фотографии не проверены нейросетью.")
        elif not review.is_complete_for(listing):
            notes.append("Проверка фотографий завершена не полностью.")
        if not listing.is_freshly_verified():
            notes.append("Цена и активность объявления не обновлены перед выдачей.")
        if inactive:
            status, reason = "inactive", "Объявление найдено в источнике, но отмечено неактивным."
        elif not matched or outside_budget:
            status, reason = "mismatch", "Вариант с отличиями от запроса; проверьте характеристики и цену."
        elif critical or review.conflicts or review.price_conditions or any(
            risk.severity is not Severity.INFO for risk in item.deterministic_risks
        ):
            status, reason = "has_risks", "В объявлении есть замечания — изучите их перед обращением к продавцу."
        else:
            status, reason = "needs_review", "Найденное объявление для самостоятельного просмотра; полная проверка не подтверждена."
        card = DiscoveredListing(item, status, (reason,), tuple(dict.fromkeys(notes)))
        priority = (inactive, not listing.is_recently_collected(), bool(critical), not matched, outside_budget,
                    not review.text_analyzed, price is None, price or 10**18,
                    -(listing.seller.rating or 0), listing.listing_id)
        candidates.append((priority, card))
    candidates.sort(key=lambda entry: entry[0])
    return tuple(card for _, card in candidates[:remaining])
