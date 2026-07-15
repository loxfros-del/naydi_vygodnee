"""One-shot integration of bounded product-page verification into Search V2."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "app" / "search_v2" / "service.py"
BRIDGE = ROOT / "app" / "services" / "search_engine_bridge.py"
SMOKE = ROOT / "tools" / "search_v2_live_smoke.py"
TRIGGER = ROOT / ".ci" / "apply-search-v2-page-verification"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"Page-verification integration anchor not found: {label}")


def patch_service() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import asyncio\nfrom dataclasses import replace\nimport re",
        "import asyncio\nfrom dataclasses import replace\nimport inspect\nimport re",
        "inspect import",
    )
    text = replace_once(
        text,
        '''        page_verification_timeout: float = 8.0,
    ) -> None:''',
        '''        page_verification_timeout: float = 8.0,
        page_verification_limit: int = 4,
    ) -> None:''',
        "constructor limit",
    )
    text = replace_once(
        text,
        '''        self.page_verification_timeout = max(0.1, float(page_verification_timeout))''',
        '''        self.page_verification_timeout = max(0.1, float(page_verification_timeout))
        self.page_verification_limit = max(0, min(int(page_verification_limit or 0), 8))''',
        "limit assignment",
    )
    text = replace_once(
        text,
        '''        if self.page_verifier and offers:
            offers = await verify_offer_pages(
                offers,
                self.page_verifier,
                max_concurrency=2,
                timeout=self.page_verification_timeout,
            )''',
        '''        if self.page_verifier and offers and self.page_verification_limit:
            # Only exact product pages that still lack a price are opened. This
            # keeps page verification bounded and avoids wasting network calls
            # on hard mismatches, search pages or already priced offers.
            eligible = [
                offer for offer in offers
                if offer.exact_match in {ExactMatchResult.EXACT, ExactMatchResult.COMPATIBLE_VARIANT}
                and bool(offer.url)
                and not bool(offer.raw_metadata.get("not_product_page"))
                and not offer.price
            ]
            remaining = max(0.0, deadline - time.monotonic())
            time_budget_limit = int((remaining * 2) // self.page_verification_timeout) if remaining else 0
            selected = eligible[: min(self.page_verification_limit, max(0, time_budget_limit))]

            async def bound_page_verifier(offer: Offer) -> Any:
                verifier = self.page_verifier
                if verifier is None:
                    return None
                try:
                    parameters = inspect.signature(verifier).parameters
                    accepts_request = len(parameters) >= 2
                except (TypeError, ValueError):
                    accepts_request = False
                result = verifier(offer, normalized_request) if accepts_request else verifier(offer)
                if inspect.isawaitable(result):
                    return await result
                return result

            if selected:
                verified = await verify_offer_pages(
                    selected,
                    bound_page_verifier,
                    max_concurrency=2,
                    timeout=self.page_verification_timeout,
                )
                by_verified_id = {offer.offer_id: offer for offer in verified}
                offers = [by_verified_id.get(offer.offer_id, offer) for offer in offers]''',
        "bounded verification block",
    )
    SERVICE.write_text(text, encoding="utf-8")


def patch_bridge() -> None:
    text = BRIDGE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''def _default_v2_service() -> Any:
    from app.search_v2.service import SearchServiceV2

    return SearchServiceV2()''',
        '''def _default_v2_service() -> Any:
    from app.search_v2.page_verifier import verify_offer_page
    from app.search_v2.service import SearchServiceV2

    return SearchServiceV2(page_verifier=verify_offer_page, page_verification_limit=4)''',
        "production bridge",
    )
    BRIDGE.write_text(text, encoding="utf-8")


def patch_smoke() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''from app.search_v2.orchestrator import SearchSourceOrchestrator
from app.search_v2.query_planner import QueryPlannerV2''',
        '''from app.search_v2.orchestrator import SearchSourceOrchestrator
from app.search_v2.page_verifier import verify_offer_page
from app.search_v2.query_planner import QueryPlannerV2''',
        "smoke import",
    )
    text = replace_once(
        text,
        '''        overall_timeout=args.case_timeout,
        page_verifier=None,
    )''',
        '''        overall_timeout=args.case_timeout,
        page_verifier=verify_offer_page,
        page_verification_timeout=min(6.0, args.source_timeout),
        page_verification_limit=4,
    )''',
        "smoke verifier",
    )
    SMOKE.write_text(text, encoding="utf-8")


def main() -> int:
    patch_service()
    patch_bridge()
    patch_smoke()
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
