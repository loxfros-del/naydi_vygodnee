"""Namespaced V2 snapshots stored in the existing diagnostic SearchCache."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.search_cache import SearchCache

from .serialization import to_jsonable


SNAPSHOT_VERSION = "search-v2-snapshot-1"
NAMESPACES = {
    "source": "search_v2:source",
    "normalized": "search_v2:normalized",
    "result": "search_v2:result",
    "shadow": "search_v2:shadow",
}


class SearchV2SnapshotStore:
    def __init__(self, cache: SearchCache | None = None, *, path: str | Path | None = None) -> None:
        self.cache = cache or SearchCache(path=path, cache_version=SNAPSHOT_VERSION)

    def _key(self, namespace: str, identity: str) -> str:
        stage = NAMESPACES.get(namespace, namespace)
        return self.cache.make_cache_key(
            stage=stage,
            source="search_v2",
            query=str(identity),
            cache_version=SNAPSHOT_VERSION,
        )

    def save(
        self,
        namespace: str,
        identity: str,
        payload: Any,
        *,
        status: str = "SUCCESS",
        ttl_seconds: int | None = None,
        duration_ms: int | None = None,
        error_text: str = "",
    ) -> str:
        stage = NAMESPACES.get(namespace, namespace)
        key = self._key(namespace, identity)
        value = to_jsonable(payload)
        if not isinstance(value, dict):
            value = {"value": value}
        self.cache.put(
            cache_key=key,
            stage=stage,
            query=str(identity),
            source="search_v2",
            payload=value,
            status=status,
            ttl_seconds=ttl_seconds,
            duration_ms=duration_ms,
            error_text=error_text,
            cache_version=SNAPSHOT_VERSION,
        )
        return key

    def load(self, namespace: str, identity: str) -> dict[str, Any] | None:
        lookup = self.cache.get(self._key(namespace, identity), cache_version=SNAPSHOT_VERSION)
        return lookup.payload if lookup.state == "HIT" else None

    def save_shadow(self, request_id: int | str, payload: Any, **kwargs: Any) -> str:
        return self.save("shadow", f"request:{request_id}", payload, **kwargs)

    def load_shadow(self, request_id: int | str) -> dict[str, Any] | None:
        return self.load("shadow", f"request:{request_id}")

    def save_result(self, request_key: str, payload: Any, **kwargs: Any) -> str:
        return self.save("result", request_key, payload, **kwargs)

    def load_result(self, request_key: str) -> dict[str, Any] | None:
        return self.load("result", request_key)


__all__ = ["NAMESPACES", "SNAPSHOT_VERSION", "SearchV2SnapshotStore"]
