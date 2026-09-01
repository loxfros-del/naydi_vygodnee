"""Safe, local HTTP-facing adapter for the NOVA web prototype.

This module deliberately exposes a small client contract.  It does not know
about Telegram, does not persist requests, and never returns adapter errors or
internal ranking data to a browser.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from app.category_registry import CATEGORY_SPECS
from app.search_v2.adapters import YandexWebDiscoveryAdapter
from app.search_v2.cache import MemorySourceCache
from app.search_v2.external_page_verifier import (
    ExternalProductPageVerifier,
    verify_external_offer_if_needed,
)
from app.search_v2.market_history import MarketHistoryStore, MarketLane, canonical_hash
from app.search_v2.models import ExactMatchResult, Offer, ProductCondition, SearchResultStatus, SearchResultV2
from app.search_v2.normalization import is_product_page_url
from app.search_v2.orchestrator import SearchSourceOrchestrator
from app.search_v2.service import SearchServiceV2
from app.search_v2.source_registry import build_default_registry
from app.services.web_request_service import InMemorySearchRequestService
from app.sources.yandex_search_source import yandex_search_runtime_status


_MAX_TEXT = 300
_MAX_CITY = 80
_PHONE_MEMORY = {"128", "256", "512", "1024"}
_PHONE_VARIANTS = {
    "esim": "eSIM",
    "dual_sim": "Dual SIM",
    "ru": "RU / EAC",
    "global": "Global",
}
_PHONE_CONDITIONS = {"new", "any"}
_MANUAL_CATEGORY_ALIASES = {"", "manual", "product", "товар"}
_ROLE_LABELS = {
    "BEST_OVERALL": "Лучший выбор",
    "CHEAP_WITH_RISK": "Дешевле, но с нюансами",
    "RELIABLE": "Надёжный вариант",
}
_TECHNICAL_MARKERS = (
    "traceback", "adapter", "captcha", "timeout", "database", "token", "secret",
    "authorization", "cookie", "http", "exception", "score", "балл",
)


def _laptop_configuration_key(offer: Offer) -> tuple[str, str, str] | None:
    """Return a safe comparison key for an unselected laptop configuration."""
    identity = getattr(offer, "identity", None)
    if str(getattr(identity, "category", "") or "").casefold() != "laptop":
        return None
    configuration = getattr(identity, "key_configuration", {}) or {}
    ram = configuration.get("ram_gb", configuration.get("ram")) if isinstance(configuration, dict) else None
    storage = getattr(identity, "storage", None)
    diagonal = getattr(identity, "diagonal", None)
    if ram in (None, "") and storage in (None, ""):
        return None
    # Some retailers omit 13/13.6 in one title.  RAM+SSD is still a more
    # useful de-duplication key than showing two cards for the same selected
    # configuration, while the full title remains visible for the user.
    return (str(ram or "unknown"), str(storage or "unknown"), "")


def _laptop_configuration_cards(
    result: SearchResultV2,
    *,
    market_covered: bool,
    request: Any = None,
    market_history_store: Any = None,
) -> list[dict[str, Any]]:
    """Show the cheapest direct card for each laptop configuration.

    When RAM/SSD were not selected by the user, configurations are not one
    comparable market.  The normal recommender deliberately chooses one offer;
    this browser-only view makes the alternatives visible without giving them
    a misleading "best" role.
    """
    request = result.normalized_request
    if not request or request.category != "laptop":
        return []
    if any(key in request.required_specs for key in ("ram", "ram_gb", "storage", "storage_gb")):
        return []
    candidates = [
        offer for offer in result.normalized_offers
        if (
            getattr(offer, "exact_match", ExactMatchResult.UNKNOWN) is ExactMatchResult.COMPATIBLE_VARIANT
            and bool(getattr(offer, "price", None) and getattr(offer, "price", 0) > 0)
            and is_product_page_url(str(getattr(offer, "url", "") or ""))
            and _laptop_configuration_key(offer) is not None
        )
    ]
    by_configuration: dict[tuple[str, str, str], Offer] = {}
    for offer in sorted(candidates, key=lambda item: float(getattr(item, "price", 0) or 10**18)):
        key = _laptop_configuration_key(offer)
        if key is not None:
            by_configuration.setdefault(key, offer)
    cards: list[dict[str, Any]] = []
    for offer in list(by_configuration.values())[:3]:
        item = SimpleNamespace(
            offer=offer,
            role="CONFIGURATION_VARIANT",
            reasons=["Модель совпадает, конфигурация не выбрана."],
            checks=[],
            savings_evidence=None,
        )
        card = _card(
            item,
            market_covered=market_covered,
            request=request,
            market_history_store=market_history_store,
        )
        if card:
            cards.append(card)
    return cards


def _text(value: Any, *, limit: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_text(value: Any, *, limit: int = _MAX_TEXT) -> str:
    text = _text(value, limit=limit)
    return "" if any(marker in text.casefold() for marker in _TECHNICAL_MARKERS) else text


def _money(value: Any, currency: str = "RUB") -> str:
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return "Цена уточняется"
    suffix = "₽" if str(currency or "RUB").upper() in {"RUB", "RUR", "₽"} else _safe_text(currency, limit=8)
    return f"{amount:,}".replace(",", " ") + f" {suffix}"


def normalize_web_search_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate browser fields and map them onto the existing request format."""
    if not isinstance(payload, Mapping):
        raise ValueError("Нужна заявка в формате JSON.")
    raw_category = _text(payload.get("category"), limit=40).casefold()
    category = "manual" if raw_category in _MANUAL_CATEGORY_ALIASES else raw_category
    if category != "manual" and category not in CATEGORY_SPECS and category != "smartphones":
        raise ValueError("Укажите товар из поддерживаемой категории или оставьте категорию автоопределяемой.")
    product = _text(payload.get("product"))
    if len(product) < 2:
        raise ValueError("Укажите товар или модель.")
    budget = _text(payload.get("budget"), limit=20)
    budget_digits = "".join(character for character in budget if character.isdigit())
    result = {
        "category": category,
        "product_name": product,
        "original_query": product,
        "budget": budget_digits,
        "city": _text(payload.get("city"), limit=_MAX_CITY),
        "priority": _text(payload.get("priority"), limit=30).casefold() or "balance",
    }
    # Old saved phone drafts remain usable, but the generic web form deliberately
    # sends no phone-only filters.  The request parser can then infer the product
    # category and any written condition or configuration from the user's text.
    if category == "smartphones":
        memory = _text(payload.get("phoneMemory"), limit=8)
        variant = _text(payload.get("phoneVariant"), limit=20).casefold()
        condition = _text(payload.get("phoneCondition"), limit=12).casefold() or "new"
        if memory not in _PHONE_MEMORY:
            memory = ""
        if variant not in _PHONE_VARIANTS:
            variant = ""
        if condition not in _PHONE_CONDITIONS:
            condition = "new"
        result.update({
            "condition": condition,
            "storage_gb": int(memory) if memory else "",
            "category_details": {
                "phone_memory": f"{memory} GB" if memory else "",
                "phone_sim_region": _PHONE_VARIANTS.get(variant, ""),
            },
        })
    elif category == "manual":
        # The common parser identifies the category from the text.  Retaining
        # the same text as the model keeps a specific product name in the
        # query plan and exact-match gate instead of searching only "laptop"
        # or "coffee machine".
        result["model"] = product
    return result


def _web_page_verifier(
    offer: Offer,
    verifier: ExternalProductPageVerifier,
) -> Offer | Awaitable[Offer] | None:
    """Fail closed for web links and suspiciously cheap direct cards."""
    return verify_external_offer_if_needed(offer, verifier)


def _web_market_coverage(result: SearchResultV2) -> int:
    offers = list(result.normalized_offers or ())
    if not offers:
        offers = [item.offer for item in result.recommendations if getattr(item, "offer", None) is not None]
    return len({
        str(offer.source or offer.platform).casefold()
        for offer in offers
        if (
            offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
            and bool(offer.price and offer.price > 0)
            and str(offer.source or offer.platform).strip()
        )
    })


def _web_search_state(result: SearchResultV2, cards: list[dict[str, Any]]) -> str:
    """Expose a small UX state without leaking source errors to the browser."""
    if cards:
        return "results"
    if result.status is SearchResultStatus.UNSUPPORTED_CATEGORY:
        return "unsupported_category"
    attempts = list(getattr(result, "source_attempts", ()) or ())
    has_timeout = result.status is SearchResultStatus.TIMEOUT or any(
        str(getattr(getattr(attempt, "status", ""), "value", getattr(attempt, "status", ""))).upper() == "TIMEOUT"
        for attempt in attempts
    )
    # An incomplete scan must not be rendered as proof that the model has no
    # offers. This also covers a completed source mixed with a timed-out one
    # when none of the returned rows survived the exact-match gate.
    if has_timeout:
        return "sources_unavailable"
    return "no_exact_match"


def build_web_service() -> SearchServiceV2:
    """Build a bounded search service that never opens the SQLite cache."""
    registry = build_default_registry()
    if "yandex_web" not in registry:
        registry.register(YandexWebDiscoveryAdapter())
    orchestrator = SearchSourceOrchestrator(
        registry=registry,
        cache=MemorySourceCache(),
        max_concurrency=3,
        per_source_timeout=8.0,
        case_timeout=22.0,
        result_limit=8,
    )
    page_verifier = ExternalProductPageVerifier(timeout=6.0)
    return SearchServiceV2(
        orchestrator=orchestrator,
        # Anonymous daily aggregates live in their own optional SQLite file.
        # They never share the bot database or influence this response's TOP-1.
        market_history_store=MarketHistoryStore(),
        page_verifier=lambda offer: _web_page_verifier(offer, page_verifier),
        # Wildberries is a separate early discovery source: adding it to the
        # marketplace stage would exceed the three-adapter cap and silently
        # leave it out behind Market/Ozon/Avito.
        web_discovery_sources=("yandex_web", "wildberries"),
        generic_sources=("generic_search",),
        overall_timeout=25.0,
        page_verification_timeout=6.0,
    )


def web_search_health() -> dict[str, str]:
    """Expose only a safe, actionable Yandex readiness state to localhost."""
    return {"yandexWeb": yandex_search_runtime_status()}


def _offer_facts(offer: Any) -> list[str]:
    """Return concise facts that are safe to show in a browser card.

    Product identity fields are part of the normalized offer contract.  For
    category facts we additionally require high-confidence extraction before
    exposing a value.  This deliberately avoids showing raw source metadata,
    evidence strings, page text, URLs, or adapter diagnostics to a client.
    """
    identity = getattr(offer, "identity", None)
    if identity is None:
        return []
    category = str(getattr(identity, "category", "") or "").casefold()
    configuration = getattr(identity, "key_configuration", {}) or {}
    configuration = configuration if isinstance(configuration, dict) else {}
    fact_values = getattr(offer, "facts", {}) or {}
    fact_values = fact_values if isinstance(fact_values, dict) else {}
    evidence = fact_values.get("fact_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}

    def add(items: list[str], value: str) -> None:
        if value and value not in items:
            items.append(value)

    def confirmed_fact(*keys: str) -> Any:
        """Read only a high-confidence normalized fact, never raw metadata."""
        for key in keys:
            item_evidence = evidence.get(key)
            if not isinstance(item_evidence, dict):
                continue
            if str(item_evidence.get("confidence") or "").casefold() != "high":
                continue
            value = fact_values.get(key)
            if value not in (None, "", [], {}):
                return value
        return None

    def identity_or_fact(*keys: str) -> Any:
        for key in keys:
            value = configuration.get(key)
            if value not in (None, "", [], {}):
                return value
            value = confirmed_fact(key)
            if value not in (None, "", [], {}):
                return value
        return None

    def numeric(value: Any) -> str:
        try:
            number = float(str(value).replace(",", ".").replace(" ", ""))
        except (TypeError, ValueError):
            return ""
        if not 0 < number < 10_000:
            return ""
        return str(int(number)) if number.is_integer() else f"{number:g}"

    def diagonal_label(value: Any) -> str:
        value_text = numeric(value)
        return f"{value_text}″" if value_text else ""

    def capacity_label(value: Any, *, prefix: str = "") -> str:
        text = _safe_text(value, limit=20).casefold().replace(" ", "")
        match = re.fullmatch(r"(\d{1,4})(gb|гб|tb|тб)?", text)
        if not match:
            return ""
        amount, unit = match.groups()
        unit_label = "TB" if unit in {"tb", "тб"} else "GB"
        return f"{prefix}{amount} {unit_label}".strip()

    def cpu_label(value: Any) -> str:
        text = _safe_text(value, limit=36)
        if not text or not re.fullmatch(r"[A-Za-zА-Яа-я0-9 .+\-]{2,36}", text):
            return ""
        return text

    def resolution_label(value: Any) -> str:
        normalized = _safe_text(value, limit=20).casefold().replace(" ", "")
        labels = {
            "4k": "4K", "uhd": "4K", "3840x2160": "4K",
            "qhd": "QHD", "wqhd": "QHD", "2k": "QHD", "2560x1440": "QHD",
            "fullhd": "Full HD", "fhd": "Full HD", "1080p": "Full HD", "1920x1080": "Full HD",
        }
        return labels.get(normalized, "")

    def refresh_label(value: Any) -> str:
        value_text = numeric(value)
        return f"{value_text} Гц" if value_text else ""

    def panel_label(value: Any) -> str:
        normalized = _safe_text(value, limit=20).casefold().replace(" ", "")
        return {
            "oled": "OLED", "qled": "QLED", "miniled": "Mini LED",
            "ips": "IPS", "va": "VA", "tn": "TN",
        }.get(normalized, "")

    facts: list[str] = []
    if category == "laptop":
        add(facts, diagonal_label(getattr(identity, "diagonal", None)))
        add(facts, capacity_label(identity_or_fact("ram_gb", "ram"), prefix="RAM "))
        ssd = confirmed_fact("ssd", "ssd_gb")
        add(facts, capacity_label(ssd, prefix="SSD ") if ssd is not None else capacity_label(getattr(identity, "storage", None)))
        # CPU is never copied from source metadata: only title/high-evidence
        # normalized facts may be shown in the browser.
        add(facts, cpu_label(confirmed_fact("cpu")))
        return facts[:4]

    if category in {"tv", "monitor"}:
        add(facts, diagonal_label(getattr(identity, "diagonal", None)))
        add(facts, resolution_label(identity_or_fact("resolution")))
        add(facts, refresh_label(getattr(identity, "refresh_rate", None) or identity_or_fact("refresh_rate")))
        add(facts, panel_label(identity_or_fact("panel", "matrix_type")))
        return facts[:4]

    if category == "coffee_machine":
        machine_type = _safe_text(identity_or_fact("machine_type"), limit=20).casefold()
        add(facts, {
            "automatic": "Автоматическая",
            "espresso": "Рожковая",
            "capsule": "Капсульная",
        }.get(machine_type, ""))
        cappuccinator = identity_or_fact("cappuccinator")
        if cappuccinator is True or str(cappuccinator).casefold() in {"true", "yes", "1"}:
            add(facts, "Капучинатор")
        return facts[:4]

    if category == "phone":
        condition = str(getattr(offer, "condition", ProductCondition.UNKNOWN) or "unknown").casefold()
        condition_labels = {
            ProductCondition.NEW.value: "Новый",
            ProductCondition.USED.value: "Б/у",
            ProductCondition.REFURBISHED.value: "Восстановленный",
        }
        add(facts, condition_labels.get(condition, ""))
        add(facts, capacity_label(getattr(identity, "storage", None)))
        variant_labels = {
            "esim": "eSIM",
            "dual_sim": "Dual SIM",
            "ru": "RU / EAC",
            "global": "Global",
        }
        variant = str(getattr(identity, "region_or_sim_variant", "") or "").casefold()
        labels = [variant_labels[item] for item in variant.split("|") if item in variant_labels]
        if labels:
            add(facts, " · ".join(labels))
        return facts[:4]

    return facts[:4]


def _trend_identity(offer: Any) -> dict[str, Any] | None:
    """Rebuild only the hash input used by the anonymous history store."""
    identity = getattr(offer, "identity", None)
    category = str(getattr(identity, "category", "") or "").strip()
    model = str(getattr(identity, "canonical_model", "") or "").strip()
    if identity is None or not category or not model:
        return None
    configuration = getattr(identity, "key_configuration", {}) or {}
    return {
        "category": category,
        "brand": str(getattr(identity, "brand", "") or ""),
        "model": model,
        "modifiers": list(getattr(identity, "modifiers", ()) or ()),
        "storage": getattr(identity, "storage", None),
        "size": getattr(identity, "size", None),
        "diagonal": getattr(identity, "diagonal", None),
        "refresh_rate": getattr(identity, "refresh_rate", None),
        "configuration": dict(configuration) if isinstance(configuration, Mapping) else {},
        "condition": str(getattr(getattr(identity, "condition", None), "value", getattr(identity, "condition", "unknown")) or "unknown"),
        "region_or_sim_variant": str(getattr(identity, "region_or_sim_variant", "") or ""),
    }


def _trend_lane(offer: Any) -> MarketLane | None:
    """Keep the browser trend in the same non-interchangeable market lane."""
    identity = getattr(offer, "identity", None)
    condition = getattr(offer, "condition", ProductCondition.UNKNOWN)
    condition_value = str(getattr(condition, "value", condition) or "unknown").casefold()
    if condition_value == ProductCondition.UNKNOWN.value:
        identity_condition = getattr(identity, "condition", ProductCondition.UNKNOWN)
        condition_value = str(getattr(identity_condition, "value", identity_condition) or "unknown").casefold()
    if condition_value == ProductCondition.REFURBISHED.value:
        return MarketLane.REFURBISHED
    if condition_value == ProductCondition.USED.value:
        return MarketLane.USED
    if condition_value != ProductCondition.NEW.value:
        return None
    trust = str(getattr(getattr(offer, "platform_trust", None), "value", getattr(offer, "platform_trust", "")) or "").upper()
    if trust == "HIGH_RETAIL":
        return MarketLane.NEW_RETAIL
    if trust == "HIGH_MARKETPLACE":
        return MarketLane.NEW_MARKETPLACE
    seller = getattr(offer, "seller", None)
    seller_type = str(getattr(seller, "seller_type", "") or "").casefold()
    if trust == "CLASSIFIED" or seller_type == "private":
        return MarketLane.NEW_PRIVATE
    return None


def _trend_scope(request: Any, offer: Any) -> dict[str, str]:
    """Hash locality before it reaches the history query or a response."""
    availability = getattr(offer, "availability", None)
    seller = getattr(offer, "seller", None)
    city = str(
        getattr(request, "city", "")
        or getattr(offer, "city", "")
        or getattr(availability, "city", "")
        or getattr(seller, "city", "")
        or ""
    ).strip()
    delivery = str(getattr(offer, "delivery", "") or getattr(availability, "delivery", "") or "").strip()
    return {
        "city_hash": canonical_hash(city or "unknown", namespace="market-city"),
        "coverage": "local" if city else ("delivery" if delivery else "unknown"),
        "currency": str(getattr(offer, "currency", "RUB") or "RUB").upper(),
    }


def _market_trend(offer: Any, request: Any, store: Any) -> dict[str, str] | None:
    """Return a compact label; trend lookup cannot affect a live result."""
    if (
        store is None
        or not callable(getattr(store, "trend", None))
        or getattr(offer, "exact_match", ExactMatchResult.UNKNOWN)
        not in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
        or not is_product_page_url(str(getattr(offer, "url", "") or ""))
    ):
        return None
    identity = _trend_identity(offer)
    lane = _trend_lane(offer)
    if identity is None or lane is None:
        return None
    try:
        trend = store.trend(
            canonical_identity=identity,
            scope=_trend_scope(request, offer),
            lane=lane,
        )
    except Exception:
        return None
    direction = str(getattr(trend, "direction", "") or "").casefold()
    try:
        percent = float(getattr(trend, "percent_change", 0))
        days = int(getattr(trend, "observed_days", 0))
    except (TypeError, ValueError):
        return None
    if direction not in {"up", "down"} or not 0 < percent < 100 or days < 3:
        return None
    movement = "снизилась" if direction == "down" else "выросла"
    return {
        "direction": direction,
        "label": f"За {days} дней медиана {movement} на {percent:g}%",
    }


def _card(
    recommendation: Any,
    *,
    market_covered: bool,
    request: Any = None,
    market_history_store: Any = None,
) -> dict[str, Any] | None:
    offer = getattr(recommendation, "offer", None)
    if offer is None:
        return None
    title = _safe_text(getattr(offer, "title", ""))
    if not title:
        return None
    link = str(getattr(offer, "url", "") or "")
    safe_link = link if is_product_page_url(link) else ""
    role = str(getattr(getattr(recommendation, "role", ""), "value", getattr(recommendation, "role", ""))).upper()
    reasons = [_safe_text(item) for item in list(getattr(recommendation, "reasons", []) or [])]
    checks = [_safe_text(item) for item in list(getattr(recommendation, "checks", []) or [])]
    evidence = getattr(recommendation, "savings_evidence", None)
    role_label = _ROLE_LABELS.get(role, _ROLE_LABELS["BEST_OVERALL"])
    if getattr(offer, "exact_match", ExactMatchResult.UNKNOWN) is ExactMatchResult.COMPATIBLE_VARIANT:
        role_label = "Вариант конфигурации"
    elif role == "BEST_OVERALL" and not market_covered:
        role_label = "Точный вариант"
    card = {
        "role": role,
        "roleLabel": role_label,
        "title": title,
        "price": _money(getattr(offer, "price", None), getattr(offer, "currency", "RUB")),
        "store": _safe_text(getattr(offer, "platform", ""), limit=80),
        "seller": _safe_text(getattr(getattr(offer, "seller", None), "name", ""), limit=80),
        "url": safe_link,
        "facts": _offer_facts(offer),
        "reason": next((item for item in reasons if item), "Цена и конфигурация проверены."),
        "checks": [item for item in checks if item][:2],
        "saving": _safe_text(getattr(evidence, "client_reason", ""), limit=140) if evidence else "",
    }
    if trend := _market_trend(offer, request, market_history_store):
        card["marketTrend"] = trend
    return card


def build_web_response(
    result: SearchResultV2,
    *,
    market_history_store: Any = None,
) -> dict[str, Any]:
    """Return only client-safe, actionable cards and a safe status message."""
    source_count = _web_market_coverage(result)
    market_covered = source_count >= 2
    items = sorted(
        result.recommendations,
        key=lambda item: float(getattr(getattr(item, "offer", None), "price", 0) or 10**18),
    )
    request = result.normalized_request
    visible_offers = [getattr(item, "offer", None) for item in items[:3]]
    configurations_unselected = bool(visible_offers) and all(
        getattr(offer, "exact_match", ExactMatchResult.UNKNOWN) is ExactMatchResult.COMPATIBLE_VARIANT
        for offer in visible_offers
        if offer is not None
    )
    cards = _laptop_configuration_cards(
        result,
        market_covered=market_covered,
        request=request,
        market_history_store=market_history_store,
    ) if configurations_unselected else []
    if not cards:
        cards = [
            card for item in items[:3]
            if (card := _card(
                item,
                market_covered=market_covered,
                request=request,
                market_history_store=market_history_store,
            ))
        ]
    search_state = _web_search_state(result, cards)
    if cards and market_covered and configurations_unselected:
        message = "Нашли варианты одной модели с разной конфигурацией. Сравнивайте память, экран и цену."
    elif cards and market_covered:
        message = "Нашли точные варианты. Сравнивайте цену и условия спокойно."
    elif cards and configurations_unselected:
        coverage = "один источник" if source_count == 1 else "недостаточно независимых источников"
        message = f"Нашли варианты модели с разной конфигурацией, но рынок пока подтверждён только через {coverage}. Продолжаем искать шире."
    elif cards:
        coverage = "один источник" if source_count == 1 else "недостаточно независимых источников"
        message = f"Нашли точный вариант, но рынок пока подтверждён только через {coverage}. Продолжаем искать шире."
    elif search_state == "unsupported_category":
        message = "Пока NOVA не распознала этот тип товара. Укажите модель и категорию, например «кофемашина DeLonghi»."
    elif search_state == "sources_unavailable":
        message = "Поиск не завершился: часть источников не ответила вовремя. Отсутствие карточек не означает, что товара нет."
    else:
        message = "Точных предложений пока нет. Проверьте модель или ослабьте версию и память."
    return {
        "status": result.status.value,
        "searchState": search_state,
        "message": message,
        "query": {
            "model": _safe_text(getattr(request, "canonical_model", ""), limit=120),
            "budget": getattr(request, "budget", None),
            "city": _safe_text(getattr(request, "city", ""), limit=_MAX_CITY),
        },
        "recommendations": cards,
        "coverage": {
            "pricedExactSources": source_count,
            "marketCovered": market_covered,
        },
    }


async def search_web(
    payload: Mapping[str, Any],
    service: SearchServiceV2 | None = None,
    request_service: InMemorySearchRequestService | None = None,
) -> dict[str, Any]:
    active_service = service or (None if request_service is not None else build_web_service())
    boundary = request_service or InMemorySearchRequestService(
        search_service=active_service,
        request_normalizer=normalize_web_search_request,
    )
    result = await boundary.search(payload)
    return build_web_response(
        result,
        market_history_store=getattr(active_service, "market_history_store", None),
    )


async def search_telegram_miniapp(
    payload: Mapping[str, Any],
    *,
    validator: Any,
    service: SearchServiceV2 | None = None,
    request_service: InMemorySearchRequestService | None = None,
) -> dict[str, Any]:
    """Run the same in-memory search after Telegram validates ``initData``.

    The accepted transport shape is intentionally explicit:
    ``{"initData": "...", "search": {<same fields as /api/search>}}``.
    Authentication happens before normalisation, so an arbitrary browser cannot
    use the Telegram-only route and no Telegram identity ever reaches SQLite.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("Нужен JSON-запрос.")
    validator.validate(payload.get("initData"))
    search_payload = payload.get("search")
    if not isinstance(search_payload, Mapping):
        raise ValueError("Нужны параметры поиска Mini App.")
    return await search_web(search_payload, service=service, request_service=request_service)


__all__ = [
    "build_web_response", "build_web_service", "normalize_web_search_request", "search_telegram_miniapp",
    "search_web", "web_search_health",
]
