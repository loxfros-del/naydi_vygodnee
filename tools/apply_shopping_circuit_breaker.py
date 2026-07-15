"""One-shot wiring of the process-local shopping provider circuit breaker."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app" / "search_v2" / "adapters" / "shopping_search.py"
TRIGGER = ROOT / ".ci" / "apply-shopping-circuit-breaker"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"shopping circuit anchor not found: {label}")


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''from .base import LegacyBridgeAdapter, SourceCapabilities, SourceContext
from ..models import SearchRequestV2''',
        '''from .base import LegacyBridgeAdapter, SourceCapabilities, SourceContext, SourceResult, make_attempt
from ..circuit_breaker import shopping_provider_circuit
from ..models import SearchRequestV2, SourceStatus''',
        "imports",
    )
    text = replace_once(
        text,
        '''    final_status = "rate_limited" if any(":rate_limited:" in item for item in failures) else "empty"''',
        '''    final_status = (
        "rate_limited"
        if failures and all(":rate_limited:" in item for item in failures)
        else "empty"
    )''',
        "aggregate status",
    )
    text = replace_once(
        text,
        '''class ShoppingSearchAdapter(LegacyBridgeAdapter):
    name = "shopping_search"
    platform = "Google Shopping"
    version = "structured-shopping-3"''',
        '''def _circuit_ttl(error: str) -> float:
    normalized = str(error or "").casefold()
    if "used all of the searches for the month" in normalized or "monthly" in normalized and "quota" in normalized:
        # Process restart and quota preflight will clear/re-evaluate this state.
        return 31 * 24 * 60 * 60
    return 15 * 60


class ShoppingSearchAdapter(LegacyBridgeAdapter):
    name = "shopping_search"
    platform = "Google Shopping"
    version = "structured-shopping-4"''',
        "class header",
    )
    text = replace_once(
        text,
        '''    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or shopping_google_legacy_search)''',
        '''    async def search(self, request, source_query, context) -> SourceResult:
        snapshot = shopping_provider_circuit.snapshot(self.name)
        if snapshot.open:
            error = f"provider circuit open: {snapshot.reason or 'rate limited'}"
            attempt = make_attempt(
                source=self.name,
                query=str(getattr(source_query, "query", "") or ""),
                status=SourceStatus.RATE_LIMITED,
                duration=0.0,
                error=error,
                tier=getattr(source_query, "tier", None),
            )
            return SourceResult(
                status=SourceStatus.RATE_LIMITED,
                attempts=(attempt,),
                error=error,
                rate_limit_info={"circuit_open": True, "reason": snapshot.reason},
            )

        result = await super().search(request, source_query, context)
        if result.status is SourceStatus.RATE_LIMITED:
            shopping_provider_circuit.open(
                self.name,
                seconds=_circuit_ttl(result.error),
                reason=result.error or "structured shopping provider rate limited",
            )
        elif result.status in {SourceStatus.SUCCESS, SourceStatus.PARTIAL_SUCCESS}:
            shopping_provider_circuit.reset(self.name)
        return result

    def __init__(self, search_callable=None) -> None:
        super().__init__(search_callable or shopping_google_legacy_search)''',
        "adapter search",
    )
    TARGET.write_text(text, encoding="utf-8")
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
