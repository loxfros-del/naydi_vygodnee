"""Canonicalization/dedupe с сохранением size/storage/condition variants."""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.category_registry import detect_category, normalize_text
from app.request_parser import parse_request_details


_TRACKING_PREFIXES = ("utm_", "gclid", "yclid", "from", "ref", "erid")


def _value(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def normalized_url(url: str) -> str:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", urlencode(query), ""))


def source_product_id(candidate: Any) -> str:
    raw = _value(candidate, "raw", {}) or {}
    if isinstance(raw, dict):
        for key in ("id", "product_id", "sku", "offer_id", "nmId"):
            if raw.get(key) not in (None, ""):
                return f"{_value(candidate, 'source', '')}:{raw[key]}"
    url = str(_value(candidate, "url", "") or "")
    path = urlparse(url).path
    patterns = (
        r"/catalog/(\d+)/detail",
        r"/product/(?:[^/]*-)?(\d{5,})(?:/|$)",
        r"/items?/(\d{5,})(?:/|$)",
        r"/(\d{7,})(?:/|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, path, re.IGNORECASE)
        if match:
            return f"{_value(candidate, 'source', '')}:{match.group(1)}"
    return ""


def _facts(candidate: Any) -> dict[str, Any]:
    facts = _value(candidate, "product_facts", {}) or _value(candidate, "facts", {}) or {}
    return facts if isinstance(facts, dict) else {}


def canonical_identity(candidate: Any) -> str:
    title = str(_value(candidate, "title", "") or "")
    facts = _facts(candidate)
    details = parse_request_details(title)
    category = str(facts.get("category") or details.get("category") or detect_category(title))
    brand = str(facts.get("brand") or details.get("brand") or "")
    model = str(facts.get("model") or facts.get("model_key") or details.get("model") or "")
    if not model:
        return ""
    variant_parts = [
        str(facts.get("storage_gb") or facts.get("storage") or facts.get("memory") or details.get("storage_gb") or ""),
        str(facts.get("size") or details.get("size") or ""),
        str(facts.get("diagonal") or details.get("diagonal") or ""),
        str(facts.get("ram") or details.get("ram_gb") or ""),
        str(facts.get("ssd") or details.get("ssd_gb") or ""),
        str(facts.get("condition") or details.get("condition") or "any"),
    ]
    normalized_parts = [normalize_text(value) for value in (category, brand, model, *variant_parts)]
    return "|".join(normalized_parts)


def title_fingerprint(candidate: Any) -> str:
    title = normalize_text(str(_value(candidate, "title", "") or ""))
    words = re.findall(r"[a-zа-я0-9]+", title)
    stop = {"купить", "цена", "доставка", "скидка", "акция", "в", "с", "и", "для"}
    filtered = [word for word in words if word not in stop]
    return " ".join(filtered[:12]) if len(filtered) >= 3 else ""


def candidate_keys(candidate: Any) -> tuple[str, ...]:
    keys: list[str] = []
    url_key = normalized_url(str(_value(candidate, "url", "") or ""))
    if url_key:
        keys.append(f"url:{url_key}")
    product_id = source_product_id(candidate)
    if product_id:
        keys.append(f"id:{product_id}")
    identity = canonical_identity(candidate)
    if identity:
        keys.append(f"identity:{identity}")
    else:
        fingerprint = title_fingerprint(candidate)
        if fingerprint:
            keys.append(f"title:{_value(candidate, 'source', '')}:{fingerprint}")
    return tuple(keys)


def dedupe_candidates(
    candidates: Iterable[Any],
    *,
    sort_key: Callable[[Any], Any],
) -> list[Any]:
    ranked = sorted(candidates, key=sort_key)
    seen: set[str] = set()
    result: list[Any] = []
    for candidate in ranked:
        keys = set(candidate_keys(candidate))
        if keys and keys & seen:
            continue
        result.append(candidate)
        seen.update(keys)
    return result


def diversify_top_sources(
    candidates: list[Any],
    *,
    tier: Callable[[Any], int],
    limit: int = 3,
) -> list[Any]:
    """Мягко повышает source diversity, не смешивая status tiers."""
    if len(candidates) < 2 or limit < 2:
        return list(candidates)
    pool = list(candidates)
    result: list[Any] = [pool.pop(0)]
    used = {str(_value(result[0], "source", "") or "")}
    while pool and len(result) < limit:
        target_tier = tier(pool[0])
        replacement = next((
            index for index, item in enumerate(pool)
            if tier(item) == target_tier and str(_value(item, "source", "") or "") not in used
        ), 0)
        selected = pool.pop(replacement)
        result.append(selected)
        used.add(str(_value(selected, "source", "") or ""))
    return result + pool
