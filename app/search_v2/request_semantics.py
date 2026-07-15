"""Shared semantics for specific-model and category-level search requests."""
from __future__ import annotations

import re
from typing import Any

from .models import SearchRequestV2


_CATEGORY_STOPWORDS: dict[str, frozenset[str]] = {
    "phone": frozenset({"смартфон", "телефон", "phone", "smartphone"}),
    "laptop": frozenset({"ноутбук", "laptop", "ultrabook", "ультрабук"}),
    "tv": frozenset({"телевизор", "tv", "smart"}),
    "headphones": frozenset({"наушник", "наушники", "headphone", "headphones", "earbuds"}),
    "monitor": frozenset({"монитор", "monitor", "display", "дисплей"}),
    "chair": frozenset({"кресло", "chair", "офисное", "office", "компьютерное"}),
}

_CATEGORY_TITLE_ALIASES: dict[str, tuple[tuple[str, ...], ...]] = {
    "phone": (("смартфон",), ("телефон",), ("iphone",), ("smartphone",)),
    "laptop": (("ноутбук",), ("laptop",), ("ultrabook",), ("ультрабук",)),
    "tv": (("телевизор",), ("tv",)),
    "headphones": (("наушник",), ("наушники",), ("headphone",), ("headphones",), ("earbuds",), ("tws",)),
    "monitor": (("монитор",), ("monitor",), ("display",), ("дисплей",)),
    "chair": (("кресло",), ("chair",), ("стул",)),
}


def semantic_tokens(value: Any) -> list[str]:
    return re.findall(r"[0-9a-zа-я]+", str(value or "").replace("ё", "е").casefold())


def is_generic_request(request: SearchRequestV2) -> bool:
    """Return true for category/spec requests rather than a named product model."""

    if request.brand or request.model_modifiers:
        return False
    category = str(request.category or "").casefold()
    model_tokens = set(semantic_tokens(request.canonical_model))
    stopwords = _CATEGORY_STOPWORDS.get(category, frozenset())
    return not model_tokens or bool(model_tokens & stopwords)


def meaningful_model_tokens(request: SearchRequestV2) -> list[str]:
    """Tokens that remain hard even for a category-level request.

    Example: ``ноутбук Ryzen 5`` keeps ``ryzen`` and ``5`` while dropping only
    the category word ``ноутбук``.
    """

    tokens = semantic_tokens(request.canonical_model)
    if not is_generic_request(request):
        return tokens
    stopwords = _CATEGORY_STOPWORDS.get(str(request.category or "").casefold(), frozenset())
    return [token for token in tokens if token not in stopwords]


def category_title_matches(category: str, title: Any) -> bool:
    tokens = set(semantic_tokens(title))
    aliases = _CATEGORY_TITLE_ALIASES.get(str(category or "").casefold(), ())
    return any(all(token in tokens for token in alias) for alias in aliases)


__all__ = [
    "category_title_matches",
    "is_generic_request",
    "meaningful_model_tokens",
    "semantic_tokens",
]
