"""Explicit adapter registry; no discovery by imports or network side effects."""
from __future__ import annotations

from collections import OrderedDict
from typing import Iterable, Iterator

from .adapters import (
    AvitoAdapter,
    CitilinkAdapter,
    DnsAdapter,
    GenericExactSearchAdapter,
    MVideoAdapter,
    OzonAdapter,
    SourceAdapter,
    WildberriesAdapterV2,
    YandexMarketAdapter,
    YandexWebDiscoveryAdapter,
)


SOURCE_ALIASES = {
    "yandex": "yandex_market",
    "yandex_market_search": "yandex_market",
    "yandex_market_direct": "yandex_market",
    "market.yandex.ru": "yandex_market",
    "ozon_search": "ozon",
    "ozon.ru": "ozon",
    "avito_search": "avito",
    "avito.ru": "avito",
    "wb": "wildberries",
    "wildberries.ru": "wildberries",
    "dns_search": "dns",
    "dns_direct": "dns",
    "dns-shop.ru": "dns",
    "citilink_search": "citilink",
    "citilink_direct": "citilink",
    "citilink.ru": "citilink",
    "mvideo_search": "mvideo",
    "mvideo_direct": "mvideo",
    "mvideo.ru": "mvideo",
    "generic": "generic_exact",
    "generic_search": "generic_exact",
    "generic_web": "generic_exact",
    "yandex_web": "yandex_web",
    "yandex_search": "yandex_web",
}


def canonical_source_name(value: object) -> str:
    name = str(value or "").strip().casefold()
    return SOURCE_ALIASES.get(name, name)


class SourceRegistry:
    def __init__(self, adapters: Iterable[SourceAdapter] = ()) -> None:
        self._adapters: OrderedDict[str, SourceAdapter] = OrderedDict()
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SourceAdapter, *, replace: bool = False) -> None:
        name = canonical_source_name(getattr(adapter, "name", ""))
        if not name:
            raise ValueError("adapter name is required")
        if name in self._adapters and not replace:
            raise ValueError(f"adapter already registered: {name}")
        self._adapters[name] = adapter

    def unregister(self, name: str) -> SourceAdapter | None:
        return self._adapters.pop(canonical_source_name(name), None)

    def get(self, name: object) -> SourceAdapter | None:
        return self._adapters.get(canonical_source_name(name))

    def require(self, name: object) -> SourceAdapter:
        adapter = self.get(name)
        if adapter is None:
            raise KeyError(f"unknown Search V2 source: {name}")
        return adapter

    def names(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def values(self) -> tuple[SourceAdapter, ...]:
        return tuple(self._adapters.values())

    def __contains__(self, name: object) -> bool:
        return canonical_source_name(name) in self._adapters

    def __len__(self) -> int:
        return len(self._adapters)

    def __iter__(self) -> Iterator[SourceAdapter]:
        return iter(self._adapters.values())


def build_default_registry(*, include_optional: bool = True) -> SourceRegistry:
    adapters: list[SourceAdapter] = [
        YandexMarketAdapter(),
        OzonAdapter(),
        AvitoAdapter(),
        DnsAdapter(),
        YandexWebDiscoveryAdapter(),
        GenericExactSearchAdapter(),
    ]
    if include_optional:
        adapters[3:3] = [WildberriesAdapterV2()]
        adapters[-1:-1] = [CitilinkAdapter(), MVideoAdapter()]
    return SourceRegistry(adapters)


__all__ = ["SOURCE_ALIASES", "SourceRegistry", "build_default_registry", "canonical_source_name"]
