"""Lossless RawOffer -> normalized Offer boundary for Search Engine V2."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
from .request_semantics import is_generic_request


_SPACE_RE = re.compile(r"\s+")
_STORAGE_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(тб|tb|гб|gb)(?!\w)", re.I)
_DIAGONAL_RE = re.compile(r"(?<!\d)(\d{2}(?:[.,]\d)?)\s*(?:[\"″]|дюйм|inch)", re.I)
_REFRESH_RE = re.compile(r"(?<!\d)(\d{2,3})\s*(?:гц|hz)(?!\w)", re.I)
_RAM_SUFFIX_RE = re.compile(r"(?<!\d)(\d{1,3})\s*(?:гб|gb)\s*(?:ram|озу)\b", re.I)
_RAM_PREFIX_RE = re.compile(r"(?:ram|озу)\s*[-:]?\s*(\d{1,3})\s*(?:гб|gb)?", re.I)
_SSD_PREFIX_RE = re.compile(r"\bssd\s*[-:]?\s*(\d{3,4})\s*(?:гб|gb)?", re.I)
_SSD_SUFFIX_RE = re.compile(r"(?<!\d)(\d{3,4})\s*(?:гб|gb)?\s*ssd\b", re.I)
_SLASH_CONFIG_RE = re.compile(r"(?<!\d)(\d{1,3})\s*/\s*(\d{3,4})(?:\s*(?:гб|gb))?(?!\d)", re.I)
_MODIFIERS = ("pro max", "ultra", "pro", "max", "plus", "mini", "air", "se")
_KNOWN_RETAIL = ("dns", "днс", "citilink", "ситилинк", "mvideo", "м.видео", "мвидео")
_KNOWN_MARKETPLACES = ("ozon", "озон", "wildberries", "вайлдберриз", "yandex market", "яндекс маркет")
_TRACKING_QUERY_KEYS = {"yclid", "gclid", "fbclid", "ref", "referrer", "from"}
_PRODUCT_PATH_MARKERS = ("/product/", "/product-", "/item/", "/goods/", "/offer/")
_SEARCH_PATH_MARKERS = ("/search", "/catalog", "/category", "/listing")


def normalize_text(value: Any) -> str:
    text = str(value or "").replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"[^0-9A-Za-zА-Яа-я+./\-\"″]+", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def normalize_key(value: Any) -> str:
    return normalize_text(value).casefold()


def canonicalize_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
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
    if any(marker in path for marker in _PRODUCT_PATH_MARKERS):
        return True
    if any(marker in path for marker in _SEARCH_PATH_MARKERS):
        return False
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
    if query_keys & {"q", "query", "text", "search", "keyword"}:
        return False
    # Classified listings and several retailers use a numeric final slug.
    return bool(re.search(r"(?:_|-|/)\d{5,}(?:$|[/?])", path + "/"))


def _metadata(raw: RawOffer) -> dict[str, Any]:
    return dict(raw.raw_metadata) if isinstance(raw.raw_metadata, dict) else {}


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def extract_ram(value: Any) -> int | None:
    normalized = normalize_text(value)
    match = _RAM_SUFFIX_RE.search(normalized) or _RAM_PREFIX_RE.search(normalized)
    if match:
        return int(match.group(1))
    slash = _SLASH_CONFIG_RE.search(normalized)
    return int(slash.group(1)) if slash else None


def extract_storage(value: Any) -> int | None:
    normalized = normalize_text(value)
    explicit = _SSD_PREFIX_RE.search(normalized) or _SSD_SUFFIX_RE.search(normalized)
    if explicit:
        return int(explicit.group(1))
    slash = _SLASH_CONFIG_RE.search(normalized)
    if slash:
        return int(slash.group(2))
    matches = _STORAGE_RE.findall(normalized)
    if not matches:
        return None
    values: list[int] = []
    for number, unit in matches:
        parsed = float(number.replace(",", "."))
        if unit.casefold() in {"тб", "tb"}:
            parsed *= 1024
        values.append(int(parsed))
    # Storage is normally the largest capacity mentioned; explicit RAM is excluded.
    ram_value = extract_ram(normalized)
    candidates = [item for item in values if item != ram_value or len(values) == 1]
    return max(candidates or values)


def extract_resolution(value: Any) -> str | None:
    text = normalize_key(value).replace(" ", "")
    if any(marker in text for marker in ("3840x2160", "2160p", "4k", "uhd")):
        return "4K"
    if any(marker in text for marker in ("2560x1440", "1440p", "qhd", "2k")):
        return "QHD"
    if any(marker in text for marker in ("1920x1080", "1080p", "fullhd", "fhd")):
        return "FHD"
    return None


def extract_features(value: Any) -> list[str]:
    text = normalize_key(value)
    features: list[str] = []
    if re.search(r"(?:\banc\b|активн\w*\s+шумоподав)", text, re.I):
        features.append("ANC")
    if re.search(r"\btws\b|true\s+wireless", text, re.I):
        features.append("TWS")
    return features


def extract_cpu_family(value: Any) -> str | None:
    text = normalize_key(value)
    match = re.search(r"\b(?:amd\s+)?(ryzen\s+[3579])\b", text, re.I)
    if match:
        return " ".join(match.group(1).split()).title()
    match = re.search(r"\b(?:intel\s+)?(?:core\s+)?(i[3579])\b", text, re.I)
    return match.group(1).upper() if match else None


def extract_modifiers(value: Any) -> list[str]:
    text = f" {normalize_key(value)} "
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


def _canonical_model_from_title(value: Any) -> str:
    title = normalize_text(value)
    candidate = re.split(
        r"\s+(?:купить|отзывы?|характеристики|вопросы?|подробное\s+описание)\b",
        title,
        maxsplit=1,
        flags=re.I,
    )[0]
    return candidate.strip(" -–—")[:180] or title[:180]


def _offer_evidence(raw: RawOffer, metadata: dict[str, Any]) -> str:
    structured = metadata.get("structured_facts") or {}
    if isinstance(structured, dict):
        structured_text = " ".join(str(value) for value in structured.values())
    else:
        structured_text = str(structured)
    return normalize_text(
        " ".join(
            str(item or "")
            for item in (
                raw.title,
                metadata.get("snippet"),
                metadata.get("body"),
                metadata.get("description"),
                structured_text,
            )
        )
    )


def build_product_identity(raw: RawOffer, request: SearchRequestV2) -> ProductIdentity:
    metadata = _metadata(raw)
    title = normalize_text(raw.title)
    evidence = _offer_evidence(raw, metadata)
    lowered = evidence.casefold()
    requested_model = normalize_text(request.canonical_model)
    generic_request = is_generic_request(request)
    model_matched = bool(
        requested_model
        and not generic_request
        and normalize_key(requested_model) in lowered
    )
    metadata_model = str(metadata.get("canonical_model") or metadata.get("model") or "").strip()
    if generic_request and normalize_key(metadata_model) == normalize_key(requested_model):
        metadata_model = ""
    canonical_model = requested_model if model_matched else (metadata_model or _canonical_model_from_title(title))

    storage = metadata.get("storage_gb", metadata.get("storage"))
    storage = extract_storage(storage) if storage not in (None, "") else extract_storage(evidence)
    diagonal_match = _DIAGONAL_RE.search(evidence)
    refresh_match = _REFRESH_RE.search(evidence)
    diagonal = metadata.get("diagonal")
    if diagonal in (None, "") and diagonal_match:
        diagonal = float(diagonal_match.group(1).replace(",", "."))
    refresh = metadata.get("refresh_rate", metadata.get("hz"))
    if refresh in (None, "") and refresh_match:
        refresh = int(refresh_match.group(1))

    modifiers = extract_modifiers(evidence)
    condition = normalize_condition(raw.condition, evidence)
    key_configuration = dict(metadata.get("structured_facts") or {})
    for key in (
        "ram",
        "ram_gb",
        "ssd_gb",
        "cpu",
        "cpu_family",
        "gpu",
        "color",
        "size",
        "resolution",
        "features",
    ):
        if key in metadata and key not in key_configuration:
            key_configuration[key] = metadata[key]

    ram_gb = extract_ram(evidence)
    ssd_gb = extract_storage(evidence)
    resolution = extract_resolution(evidence)
    features = extract_features(evidence)
    cpu_family = extract_cpu_family(evidence)
    if ram_gb is not None:
        key_configuration.setdefault("ram_gb", ram_gb)
    if ssd_gb is not None:
        key_configuration.setdefault("ssd_gb", ssd_gb)
    if resolution:
        key_configuration.setdefault("resolution", resolution)
    if features:
        current_features = key_configuration.get("features") or []
        if not isinstance(current_features, (list, tuple, set, frozenset)):
            current_features = [current_features]
        key_configuration["features"] = list(dict.fromkeys([*current_features, *features]))
    if cpu_family:
        key_configuration.setdefault("cpu_family", cpu_family)

    identity_confidence = 0.9 if model_matched else (0.7 if metadata_model else 0.6)
    identity = ProductIdentity(
        category=request.category or str(metadata.get("category") or "unknown"),
        brand=(
            request.brand
            if request.brand and (normalize_key(request.brand) in lowered or model_matched)
            else str(metadata.get("brand") or "")
        ),
        canonical_model=canonical_model,
        modifiers=modifiers,
        storage=storage,
        size=str(metadata.get("size") or "") or None,
        diagonal=diagonal,
        refresh_rate=refresh,
        key_configuration=key_configuration,
        condition=condition,
        region_or_sim_variant=str(metadata.get("region_or_sim_variant") or metadata.get("sim_variant") or ""),
        identity_confidence=identity_confidence,
    )
    from .grouping import canonical_identity_key

    identity.canonical_key = canonical_identity_key(identity)
    return identity


def normalize_raw_offer(raw: RawOffer, request: SearchRequestV2) -> Offer:
    trust = platform_trust(raw.platform, raw.source)
    metadata = _metadata(raw)
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
    if canonical_url and not is_product_page_url(canonical_url):
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
        facts=dict(metadata.get("structured_facts") or {}),
        source_confidence=0.9 if known_source else 0.55,
        product_confidence=0.0,
        price_confidence=float(metadata.get("price_confidence") or (0.9 if price else 0.0)),
        availability_confidence=availability.confidence,
        seller_confidence=(
            0.9
            if seller.trust is SellerTrust.HIGH
            else (0.65 if seller.trust is SellerTrust.MEDIUM else 0.2)
        ),
        verification_access=access,
        retrieved_at=raw.retrieved_at,
        raw_metadata=metadata,
        platform_trust=trust,
    )
    offer.identity = build_product_identity(raw, request)
    from .exact_match import apply_exact_match
    from .verification import apply_automatic_verification

    offer = apply_exact_match(request, offer)
    return apply_automatic_verification(offer)


def normalize_offers(raws: Iterable[RawOffer], request: SearchRequestV2) -> list[Offer]:
    return [normalize_raw_offer(raw, request) for raw in raws]


__all__ = [
    "build_product_identity",
    "canonicalize_url",
    "extract_cpu_family",
    "extract_features",
    "extract_modifiers",
    "extract_ram",
    "extract_resolution",
    "extract_storage",
    "is_product_page_url",
    "normalize_availability",
    "normalize_condition",
    "normalize_key",
    "normalize_offers",
    "normalize_raw_offer",
    "normalize_seller",
    "normalize_text",
    "platform_trust",
]
