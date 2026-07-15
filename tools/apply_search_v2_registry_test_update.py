"""One-shot update of registry expectations after adding structured shopping."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "test_search_v2_adapters.py"
TRIGGER = ROOT / ".ci" / "apply-search-v2-registry-test-update"
SELF = Path(__file__).resolve()

REPLACEMENTS = (
    (
        "    OzonAdapter,\n    WildberriesAdapterV2,",
        "    OzonAdapter,\n    ShoppingSearchAdapter,\n    WildberriesAdapterV2,",
    ),
    (
        '        self.assertEqual(GenericExactSearchAdapter().capabilities.kind, "fallback")',
        '        self.assertTrue(ShoppingSearchAdapter().capabilities.structured_endpoint)\n        self.assertEqual(GenericExactSearchAdapter().capabilities.kind, "fallback")',
    ),
    (
        "        self.assertEqual(len(registry), 8)",
        "        self.assertEqual(len(registry), 9)",
    ),
    (
        '        self.assertEqual(canonical_source_name("market.yandex.ru"), "yandex_market")',
        '        self.assertEqual(canonical_source_name("market.yandex.ru"), "yandex_market")\n        self.assertEqual(canonical_source_name("serpapi_google_shopping"), "shopping_search")',
    ),
    (
        '        self.assertEqual(no_optional.names(), ("yandex_market", "ozon", "avito", "dns", "generic_exact"))',
        '        self.assertEqual(no_optional.names(), ("shopping_search", "yandex_market", "ozon", "avito", "dns", "generic_exact"))',
    ),
)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    for old, new in REPLACEMENTS:
        if old in text:
            text = text.replace(old, new, 1)
        elif new not in text:
            raise RuntimeError(f"registry test anchor not found: {old[:80]}")
    TARGET.write_text(text, encoding="utf-8")
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
