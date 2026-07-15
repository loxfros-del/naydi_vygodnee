"""One-shot wiring of the structured shopping source into production/shadow V2."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "app" / "services" / "search_engine_bridge.py"
SMOKE = ROOT / "tools" / "search_v2_live_smoke.py"
TRIGGER = ROOT / ".ci" / "apply-search-v2-shopping-discovery"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"Shopping discovery anchor not found: {label}")


def main() -> int:
    bridge = BRIDGE.read_text(encoding="utf-8")
    bridge = replace_once(
        bridge,
        '''    return SearchServiceV2(page_verifier=verify_offer_page, page_verification_limit=4)''',
        '''    return SearchServiceV2(
        page_verifier=verify_offer_page,
        page_verification_limit=4,
        discovery_sources=("shopping_search", "ozon", "avito"),
    )''',
        "production bridge",
    )
    BRIDGE.write_text(bridge, encoding="utf-8")

    smoke = SMOKE.read_text(encoding="utf-8")
    smoke = replace_once(
        smoke,
        '''        page_verification_limit=4,
    )''',
        '''        page_verification_limit=4,
        discovery_sources=("shopping_search", "ozon", "avito"),
    )''',
        "live smoke",
    )
    SMOKE.write_text(smoke, encoding="utf-8")

    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
