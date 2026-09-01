"""Lossless RawOffer -> normalized Offer boundary for Search Engine V2."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.category_facts import extract_category_facts
from app.category_registry import get_category_spec

from .models import (
    AvailabilityInfo,
    AvailabilityStatus,
    Offer,
    PlatformTrust,
    ProductCondition,
    ProductIdentity,
    RawOffer,
    SearchRequestV2,
    SellerInfo,
    SellerTrust,
    VerificationAccess,
)


_SPACE_RE = re.compile(r"\s+")
_STORAGE_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(тб|tb|гб|gb)(?!\w)", re.I)
_DIAGONAL_RE = re.compile(r"(?<!\d)(\d{2}(?:[.,]\d)?)\s*(?:[\"″]|дюйм|inch)", re.I)
_REFRESH_RE = re.compile(r"(?<!\d)(\d{2,3})\s*(?:гц|hz)(?!\w)", re.I)
_RAM_RE = re.compile(r"(?<!\d)(\d{1,3})\s*(?:гб|gb)\s*(?:ram|озу)", re.I)
_SLASH_CONFIG_RE = re.compile(r"(?<!\d)(\d{1,3})\s*/\s*(\d{3,4})(?:\s*(?:гб|gb))?(?!\d)", re.I)
_COMPACT_MEMORY_CONFIG_RE = re.compile(
    r"(?<!\d)(?P<ram>4|8|16|24|32|64|128)\s*(?:гб|gb)?\s*/\s*"
    r"(?P<storage>128|256|512|1024|2048)(?:\s*(?:гб|gb))?(?!\d)",
    re.I,
)
_LAPTOP_COMPACT_DIAGONAL_RE = re.compile(
    r"(?<!\d)(?P<diagonal>1[3-7](?:[.,]\d)?)\s+"
    r"(?:(?:20\d{2}\s+)?(?:m\d+\s+)?)"
    r"(?:4|8|16|24|32|64)\s*/\s*(?:128|256|512|1024|2048)(?!\d)",
    re.I,
)
_MODEL_CONTEXT_TOKENS = {
    "товар", "ноутбук", "ноут", "laptop", "ультрабук", "смартфон",
    "телефон", "phone", "кофемашина", "кофеварка", "телевизор", "tv",
    "монитор", "наушники", "кресло", "пылесос", "матрас", "кровать",
}
_MODIFIERS = ("pro max", "ultra", "pro", "max", "plus", "mini", "air", "se")
_KNOWN_RETAIL = ("dns", "днс", "citilink", "ситилинк", "mvideo", "м.видео", "мвидео")
_KNOWN_MARKETPLACES = ("ozon", "озон", "wildberries", "вайлдберриз", "yandex market", "яндекс маркет")
_TRACKING_QUERY_KEYS = {"yclid", "gclid", "fbclid", "ref", "referrer", "from"}
_PRODUCT_PATH_MARKERS = ("/product/", "/product-", "/item/", "/goods/", "/offer/")
_SEARCH_PATH_MARKERS = ("/search", "/catalog", "/category", "/listing")
_WILDBERRIES_PRODUCT_PATH_RE = re.compile(r"^/catalog/\d+/detail\.aspx$")
_FACT_INTERNAL_KEYS = {"category", "fact_evidence"}
_DIRECT_METADATA_CONFIGURATION_KEYS = ("ram", "ram_gb", "cpu", "gpu", "color", "size")


def normalize_text(value: Any) -> str:
    text = str(value or "").replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"[^0-9A-Za-zА-Яа-я+./\-\"″]+", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def normalize_key(value: Any) -> str:
    return normalize_text(value).casefold()


def _has_specific_model_identifier(value: Any) -> bool:
    """A family name alone must not become the identity of every SKU in it."""
    return any(any(character.isdigit() for character in token) for token in re.findall(r"[0-9a-zа-я]+", normalize_key(value)))


def _matches_specific_model(requested_model: Any, title: Any) -> bool:
    """Match a named model even when retailers insert harmless words.

    Search titles regularly add a brand, display size or year between model
    words: ``MacBook Air M4`` becomes ``Apple MacBook Air 13 M4``.  Requiring
    one contiguous substring drops those legitimate offers.  The request must
    still include a concrete identifier, which keeps product-family queries
    such as ``DeLonghi Magnifica`` out of this path.
    """
    if not _has_specific_model_identifier(requested_model):
        return False
    requested_tokens = {
        token for token in re.findall(r"[0-9a-zа-я]+", normalize_key(requested_model))
        if token not in _MODEL_CONTEXT_TOKENS
    }
    title_tokens = set(re.findall(r"[0-9a-zа-я]+", normalize_key(title)))
    return bool(requested_tokens and requested_tokens.issubset(title_tokens))


def extract_compact_memory_config(value: Any) -> tuple[int, int] | None:
    """Return ``(RAM GB, storage GB)`` from ``16/256`` or ``16 GB / 256 GB``."""
    match = _COMPACT_MEMORY_CONFIG_RE.search(normalize_text(value))
    if not match:
        return None
    return int(match.group("ram")), int(match.group("storage"))


def canonicalize_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    query = [
        (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, urlencode(query), ""))


def is_product_page_url(value: Any) -> bool:
    url = canonicalize_url(value)
    if not url.startswith(("http://", "https://")):
        return False
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
    if query_keys & {"q", "query", "text", "search", "keyword"}:
        return False
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    # Wildberries product pages are beneath /catalog/, which is otherwise a
    # catalogue path.  Permit only the documented numeric detail route; do
    # not turn arbitrary catalogue/category pages into purchasable offers.
    if (
        (hostname == "wildberries.ru" or hostname.endswith(".wildberries.ru"))
        and bool(_WILDBERRIES_PRODUCT_PATH_RE.fullmatch(path))
    ):
        return True
    if any(marker in path for marker in _PRODUCT_PATH_MARKERS):
        return True
    if any(marker in path for marker in _SEARCH_PATH_MARKERS):
        return False
    # Classified listings and several retailers use a numeric final slug.
    return bool(re.search(r"(?:_|-|/)\d{5,}(?:$|[/?])", path + "/"))


def is_verified_external_product_page(value: Any, metadata: dict[str, Any]) -> bool:
    """Accept an unusual URL only after our direct-page verifier marked it.

    A legacy source may carry optimistic ``product_page_verified`` metadata.
    That alone must never turn a search or catalogue page into a purchasable
    offer.  ``external_page_verified`` is set exclusively after the bounded
    web-page fetcher finds product evidence.
    """
    if not bool(metadata.get("external_page_verified")):
        return False
    url = canonicalize_url(value)
    if not url.startswith(("http://", "https://")):
        return False
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    if any(marker in path for marker in _SEARCH_PATH_MARKERS):
        return False
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
    if query_keys & {"q", "query", "text", "search", "keyword"}:
        return False
    return bool(parsed.netloc and path not in {"", "/"})


def _metadata(raw: RawOffer) -> dict[str, Any]:
    return dict(raw.raw_metadata) if isinstance(raw.raw_metadata, dict) else {}


def _is_present_fact(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fact_evidence(value: Any, *, fallback: str = "metadata") -> dict[str, str]:
    if isinstance(value, dict):
        confidence = str(value.get("confidence") or "unknown")
        evidence = str(value.get("evidence") or fallback)
        return {"confidence": confidence, "evidence": evidence}
    return {"confidence": "unknown", "evidence": fallback}


def _is_title_confirmed(evidence: dict[str, dict[str, str]], key: str) -> bool:
    item = evidence.get(key) or {}
    return item.get("confidence") == "high" and item.get("evidence") == "title"


def _add_title_confirmed_aliases(
    facts: dict[str, Any],
    evidence: dict[str, dict[str, str]],
) -> None:
    """Add matcher aliases only for facts explicitly present in the title.

    The V2 matcher already understands ``ram_gb``/``ssd_gb`` and the legacy
    request parser emits those keys.  Category extraction uses human-readable
    ``ram``/``ssd`` fields, so bridge them only when the offer title supplied
    high-confidence evidence.  Snippets and opaque source metadata must not
    create a new exact match by inference.
    """
    for source_key, target_key in (("ram", "ram_gb"), ("ssd", "ssd_gb")):
        if target_key in facts or not _is_title_confirmed(evidence, source_key):
            continue
        capacity = extract_storage(facts.get(source_key))
        if capacity is None:
            continue
        facts[target_key] = capacity
        evidence[target_key] = dict(evidence[source_key])

    if (
        "automatic" not in facts
        and _is_title_confirmed(evidence, "machine_type")
        and normalize_key(facts.get("machine_type")) == "automatic"
    ):
        facts["automatic"] = True
        evidence["automatic"] = dict(evidence["machine_type"])


def _normalized_offer_facts(
    raw: RawOffer,
    request: SearchRequestV2,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Merge title-confirmed category facts with source-provided structured facts.

    ``extract_category_facts`` distinguishes title from page text.  At the
    V2 boundary we intentionally pass the title only: page snippets are often
    stale or describe a neighbouring SKU and must not satisfy a hard request
    parameter.  Existing source ``structured_facts`` retain their precedence;
    title extraction fills only missing values.
    """
    requested_category = str(request.category or metadata.get("category") or "unknown").strip().casefold()
    category_spec = get_category_spec(requested_category)
    category = category_spec.key if category_spec.key != "unknown" else requested_category
    extracted = extract_category_facts(category, str(raw.title or ""))
    extracted_evidence = extracted.get("fact_evidence")
    evidence: dict[str, dict[str, str]] = {
        str(key): _fact_evidence(value, fallback="title")
        for key, value in (extracted_evidence.items() if isinstance(extracted_evidence, dict) else ())
    }
    facts = {
        str(key): value
        for key, value in extracted.items()
        if key not in _FACT_INTERNAL_KEYS and _is_present_fact(value)
    }

    supplied = metadata.get("structured_facts")
    if isinstance(supplied, dict):
        supplied_evidence = supplied.get("fact_evidence")
        for key, value in supplied.items():
            if key in _FACT_INTERNAL_KEYS or not _is_present_fact(value):
                continue
            normalized_key = str(key)
            facts[normalized_key] = value
            source_evidence = (
                supplied_evidence.get(key)
                if isinstance(supplied_evidence, dict)
                else None
            )
            evidence[normalized_key] = _fact_evidence(source_evidence)

    # Preserve the existing identity contract: explicit metadata can fill a
    # missing structured value, but cannot overwrite one supplied by a source.
    for key in _DIRECT_METADATA_CONFIGURATION_KEYS:
        if key in facts or not _is_present_fact(metadata.get(key)):
            continue
        facts[key] = metadata[key]
        evidence[key] = _fact_evidence(None)

    _add_title_confirmed_aliases(facts, evidence)
    if not facts:
        return {}
    facts["category"] = category
    facts["fact_evidence"] = evidence
    return facts


def _first_present(*values: Any) -> Any:
    return next((value for value in values if _is_present_fact(value)), None)


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def extract_storage(value: Any) -> int | None:
    normalized = normalize_text(value)
    matches = _STORAGE_RE.findall(normalized)
    if not matches:
        slash = _SLASH_CONFIG_RE.search(normalized)
        return int(slash.group(2)) if slash else None
    values: list[int] = []
    for number, unit in matches:
        parsed = float(number.replace(",", "."))
        if unit.casefold() in {"тб", "tb"}:
            parsed *= 1024
        values.append(int(parsed))
    # Storage is normally the largest capacity mentioned; RAM is filtered below.
    ram = _RAM_RE.search(normalize_text(value))
    ram_value = int(ram.group(1)) if ram else None
    candidates = [item for item in values if item != ram_value or len(values) == 1]
    return max(candidates or values)


def extract_modifiers(value: Any) -> list[str]:
    text = f" {normalize_key(value)} "
    # ``Mini LED`` is a display technology, not a ``Mini`` model variant.
    # Treating it as one rejects an otherwise exact monitor/TV whenever the
    # requested model contains another real modifier such as ``Pro``.
    text = re.sub(r"\bmini\s*-?\s*led\b", " ", text)
    result: list[str] = []
    for modifier in _MODIFIERS:
        if f" {modifier} " in text:
            result.extend(modifier.split())
    return list(dict.fromkeys(result))


def normalize_condition(value: Any, title: str = "") -> ProductCondition:
    if isinstance(value, ProductCondition) and value is not ProductCondition.UNKNOWN:
        return value
    text = normalize_key(f"{value or ''} {title}")
    if any(marker in text for marker in ("refurbished", "восстановлен", "renewed")):
        return ProductCondition.REFURBISHED
    if any(marker in text for marker in ("б/у", " бу ", "used", "с пробегом")):
        return ProductCondition.USED
    if any(marker in text for marker in ("новый", "new", "не активирован")):
        return ProductCondition.NEW
    return ProductCondition.UNKNOWN


def extract_phone_variant(value: Any) -> str:
    """Return stable SIM/region markers without guessing an unmentioned version."""
    text = normalize_key(value)
    variants: list[str] = []
    if "esim" in text or "е sim" in text:
        variants.append("esim")
    if re.search(r"(?:dual|две|2)\s*(?:sim|сим)", text):
        variants.append("dual_sim")
    if re.search(r"(?:^|\s)(?:ru|eac|рст|ростест)(?:$|\s)", text):
        variants.append("ru")
    if "global" in text or "глобальн" in text:
        variants.append("global")
    return "|".join(variants)


def normalize_availability(raw: RawOffer) -> AvailabilityInfo:
    metadata = _metadata(raw)
    text = normalize_key(f"{raw.availability_text} {metadata.get('availability', '')}")
    if any(marker in text for marker in ("нет в наличии", "законч", "sold out", "снято", "удалено")):
        status, available = AvailabilityStatus.OUT_OF_STOCK, False
    elif any(marker in text for marker in ("предзаказ", "preorder")):
        status, available = AvailabilityStatus.PREORDER, True
    elif any(marker in text for marker in ("в наличии", "доступен", "available", "доставка")):
        status, available = AvailabilityStatus.IN_STOCK, True
    else:
        status, available = AvailabilityStatus.UNKNOWN, None
    confidence = 0.95 if status is not AvailabilityStatus.UNKNOWN else 0.0
    return AvailabilityInfo(
        status=status,
        available=available,
        delivery=str(raw.delivery or metadata.get("delivery") or ""),
        city=str(raw.city or metadata.get("city") or ""),
        source_text=str(raw.availability_text or ""),
        confidence=confidence,
    )


def platform_trust(platform: str, source: str = "") -> PlatformTrust:
    text = normalize_key(f"{platform} {source}")
    if any(marker in text for marker in _KNOWN_RETAIL) or "official" in text or "официаль" in text:
        return PlatformTrust.HIGH_RETAIL
    if any(marker in text for marker in _KNOWN_MARKETPLACES):
        return PlatformTrust.HIGH_MARKETPLACE
    if "avito" in text or "авито" in text:
        return PlatformTrust.CLASSIFIED
    return PlatformTrust.UNKNOWN


def normalize_seller(raw: RawOffer, trust: PlatformTrust) -> SellerInfo:
    metadata = _metadata(raw)
    rating = _number(metadata.get("seller_rating"))
    reviews_raw = metadata.get("seller_reviews_count", metadata.get("reviews_count"))
    try:
        reviews = int(reviews_raw) if reviews_raw not in (None, "") else None
    except (TypeError, ValueError):
        reviews = None
    official = metadata.get("seller_official")
    seller_type = str(metadata.get("seller_type") or "unknown").casefold()
    verified = metadata.get("seller_verified")
    if trust is PlatformTrust.HIGH_RETAIL and raw.seller_name:
        seller_trust = SellerTrust.HIGH
        verified = True if verified is None else bool(verified)
    elif bool(official) or (rating is not None and rating >= 4.7 and (reviews or 0) >= 100):
        seller_trust = SellerTrust.HIGH
    elif rating is not None and rating >= 4.3 and (reviews or 0) >= 10:
        seller_trust = SellerTrust.MEDIUM
    elif rating is not None and rating < 4.0:
        seller_trust = SellerTrust.LOW
    else:
        seller_trust = SellerTrust.UNKNOWN
    return SellerInfo(
        name=str(raw.seller_name or metadata.get("seller") or ""),
        seller_id=str(metadata.get("seller_id") or ""),
        seller_type=seller_type,
        rating=rating,
        reviews_count=reviews,
        trust=seller_trust,
        official=bool(official) if official is not None else None,
        warranty=str(metadata.get("warranty") or ""),
        return_policy=str(metadata.get("return_policy") or ""),
        city=str(raw.city or metadata.get("seller_city") or ""),
        verified=bool(verified) if verified is not None else None,
        metadata={key: metadata[key] for key in metadata if key.startswith("seller_")},
    )


def build_product_identity(
    raw: RawOffer,
    request: SearchRequestV2,
    facts: dict[str, Any] | None = None,
) -> ProductIdentity:
    metadata = _metadata(raw)
    normalized_facts = facts if isinstance(facts, dict) else {}
    title = normalize_text(raw.title)
    lowered = title.casefold()
    requested_model = normalize_text(request.canonical_model)
    requested_model_is_specific = _has_specific_model_identifier(requested_model)
    model_matched = bool(
        requested_model
        and requested_model_is_specific
        and _matches_specific_model(requested_model, title)
    )
    canonical_model = requested_model if model_matched else str(_first_present(
        metadata.get("canonical_model"), metadata.get("model"), normalized_facts.get("model"), title,
    ) or title)
    compact_memory = extract_compact_memory_config(title)
    storage = _first_present(
        metadata.get("storage_gb"), metadata.get("storage"),
        normalized_facts.get("storage_gb"), normalized_facts.get("storage"),
    )
    extracted_storage = extract_storage(storage)
    # Numeric source facts (for example ``storage_gb: 256``) are already
    # canonical.  ``extract_storage`` intentionally parses textual units, so
    # retain a numeric value rather than dropping it when no unit is present.
    if extracted_storage is not None:
        storage = extracted_storage
    elif isinstance(storage, (int, float)) and not isinstance(storage, bool):
        storage = int(storage)
    else:
        storage = None
    if storage is None:
        storage = extract_storage(title)
    if storage is None and compact_memory:
        storage = compact_memory[1]
    diagonal_match = _DIAGONAL_RE.search(title)
    refresh_match = _REFRESH_RE.search(title)
    diagonal = _first_present(metadata.get("diagonal"), normalized_facts.get("diagonal"))
    if diagonal in (None, "") and diagonal_match:
        diagonal = float(diagonal_match.group(1).replace(",", "."))
    if diagonal in (None, "") and request.category == "laptop":
        compact_diagonal = _LAPTOP_COMPACT_DIAGONAL_RE.search(title)
        if compact_diagonal:
            diagonal = float(compact_diagonal.group("diagonal").replace(",", "."))
    refresh = _first_present(
        metadata.get("refresh_rate"), metadata.get("hz"), normalized_facts.get("refresh_rate"),
    )
    if refresh in (None, "") and refresh_match:
        refresh = int(refresh_match.group(1))
    modifiers = extract_modifiers(title)
    condition = normalize_condition(raw.condition, title)
    key_configuration = {
        key: value for key, value in normalized_facts.items()
        if key not in _FACT_INTERNAL_KEYS
    }
    for key in _DIRECT_METADATA_CONFIGURATION_KEYS:
        if key in metadata and key not in key_configuration:
            key_configuration[key] = metadata[key]
    if compact_memory and "ram_gb" not in key_configuration:
        key_configuration["ram_gb"] = compact_memory[0]
    raw_variant = (
        _first_present(
            metadata.get("region_or_sim_variant"), metadata.get("sim_variant"),
            key_configuration.get("region_or_sim_variant"), key_configuration.get("sim_variant"),
        )
    )
    phone_variant = extract_phone_variant(raw_variant or (title if request.category == "phone" else ""))
    if phone_variant:
        key_configuration.setdefault("sim_variant", phone_variant)
    identity = ProductIdentity(
        category=request.category or str(metadata.get("category") or "unknown"),
        brand=request.brand if request.brand and (normalize_key(request.brand) in lowered or model_matched) else str(_first_present(metadata.get("brand"), normalized_facts.get("brand")) or ""),
        canonical_model=canonical_model,
        modifiers=modifiers,
        storage=storage,
        size=str(_first_present(metadata.get("size"), normalized_facts.get("size")) or "") or None,
        diagonal=diagonal,
        refresh_rate=refresh,
        key_configuration=key_configuration,
        condition=condition,
        region_or_sim_variant=phone_variant,
        identity_confidence=0.9 if model_matched else 0.55,
    )
    from .grouping import canonical_identity_key

    identity.canonical_key = canonical_identity_key(identity)
    return identity


def normalize_raw_offer(raw: RawOffer, request: SearchRequestV2) -> Offer:
    trust = platform_trust(raw.platform, raw.source)
    metadata = _metadata(raw)
    facts = _normalized_offer_facts(raw, request, metadata)
    availability = normalize_availability(raw)
    seller = normalize_seller(raw, trust)
    price = _number(raw.price)
    source_key = normalize_key(raw.source or raw.platform)
    known_source = trust is not PlatformTrust.UNKNOWN
    offer_seed = raw.product_id or raw.url or f"{raw.source}|{raw.title}|{raw.price}"
    offer_id = f"{source_key or 'source'}:{hashlib.sha1(offer_seed.encode('utf-8')).hexdigest()[:16]}"
    access_text = str(metadata.get("verification_access") or "").upper()
    access = VerificationAccess.__members__.get(access_text, VerificationAccess.UNKNOWN)
    canonical_url = canonicalize_url(raw.url)
    if canonical_url and not is_product_page_url(canonical_url) and not is_verified_external_product_page(canonical_url, metadata):
        metadata["not_product_page"] = True
    offer = Offer(
        offer_id=offer_id,
        source=str(raw.source or ""),
        platform=str(raw.platform or raw.source or ""),
        title=normalize_text(raw.title),
        url=canonical_url,
        product_id=str(raw.product_id or ""),
        seller=seller,
        price=price,
        old_price=_number(raw.old_price),
        currency=str(raw.currency or "RUB").upper(),
        availability=availability,
        condition=normalize_condition(raw.condition, raw.title),
        city=str(raw.city or ""),
        delivery=str(raw.delivery or ""),
        image_url=str(raw.image_url or ""),
        facts=facts,
        source_confidence=0.9 if known_source else 0.55,
        product_confidence=0.0,
        price_confidence=float(metadata.get("price_confidence") or (0.9 if price else 0.0)),
        availability_confidence=availability.confidence,
        seller_confidence=0.9 if seller.trust is SellerTrust.HIGH else (0.65 if seller.trust is SellerTrust.MEDIUM else 0.2),
        verification_access=access,
        retrieved_at=raw.retrieved_at,
        raw_metadata=metadata,
        platform_trust=trust,
    )
    offer.identity = build_product_identity(raw, request, facts)
    from .exact_match import apply_exact_match
    from .verification import apply_automatic_verification

    offer = apply_exact_match(request, offer)
    return apply_automatic_verification(offer)


def normalize_offers(raws: Iterable[RawOffer], request: SearchRequestV2) -> list[Offer]:
    return [normalize_raw_offer(raw, request) for raw in raws]


__all__ = [
    "build_product_identity", "canonicalize_url", "extract_modifiers", "extract_phone_variant", "extract_storage", "is_product_page_url", "is_verified_external_product_page", "normalize_availability",
    "normalize_condition", "normalize_key", "normalize_offers", "normalize_raw_offer",
    "normalize_seller", "normalize_text", "platform_trust",
]
