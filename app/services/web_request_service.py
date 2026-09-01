"""Shared, non-persistent request boundary for NOVA web and Telegram Mini App.

The existing ``RequestService`` intentionally creates a SQLite-backed client
request.  The browser search is still a read-only, in-memory flow, so it uses
this small boundary instead of calling either the Telegram handler or the DB
writer.  Both web transports therefore validate the same payload and call the
same injected Search V2 service.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any


RequestNormalizer = Callable[[Mapping[str, Any]], dict[str, Any]]
SearchCallable = Callable[[dict[str, Any]], Awaitable[Any]]


class InMemorySearchRequestService:
    """Normalize a web payload once, then execute the supplied live search.

    This class owns no database connection, no snapshot store and no Telegram
    update.  It is intentionally narrow so the local site and Mini App cannot
    accidentally create duplicate Telegram/SQLite requests.
    """

    def __init__(
        self,
        *,
        search_service: Any,
        request_normalizer: RequestNormalizer,
    ) -> None:
        if search_service is None or not callable(getattr(search_service, "search", None)):
            raise ValueError("search_service with async search() is required")
        self._search_service = search_service
        self._request_normalizer = request_normalizer

    async def search(self, payload: Mapping[str, Any]) -> Any:
        request = self._request_normalizer(payload)
        return await self._search_service.search(request)


__all__ = ["InMemorySearchRequestService"]
