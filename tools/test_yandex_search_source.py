"""Offline regression tests for Yandex Search API XML parsing."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Request  # noqa: E402
from app.product_search import ProductCandidate, classify_candidate  # noqa: E402
from app.request_parser import build_request_search_query  # noqa: E402
from app.sources.yandex_search_source import (  # noqa: E402
    YANDEX_SEARCH_SOURCE,
    _search_xml,
    parse_yandex_xml,
    yandex_region_for_city,
)


XML = b"""<?xml version='1.0'?><yandexsearch><response><results><grouping>
<group><doc><url>https://www.dns-shop.ru/product/abc/</url><domain>dns-shop.ru</domain><headline>Samsung A56 256GB</headline><passages><passage>Price and stock</passage></passages></doc></group>
<group><doc><url>https://market.yandex.ru/product--a56/1</url><title>Galaxy A56</title></doc></group>
</grouping></results></response></yandexsearch>"""


def main() -> int:
    rows = parse_yandex_xml(XML)
    assert len(rows) == 2
    assert rows[0]["source"] == YANDEX_SEARCH_SOURCE
    assert rows[0]["url"].startswith("https://www.dns-shop.ru/")
    assert rows[0]["store"] == "dns-shop.ru"
    assert rows[1]["title"] == "Galaxy A56"
    assert yandex_region_for_city("Ярославль") == "16"
    assert yandex_region_for_city("Москва") == "213"
    assert yandex_region_for_city("Неизвестный город") is None

    calls: dict[str, object] = {}

    class FakeSearch:
        def run(self, *args, **kwargs):
            calls["run"] = (args, kwargs)
            return XML

    class FakeSearchApi:
        def web(self, *args, **kwargs):
            calls["web"] = (args, kwargs)
            return FakeSearch()

    class FakeAIStudio:
        def __init__(self, **kwargs):
            calls["sdk"] = kwargs
            self.search_api = FakeSearchApi()

    fake_module = ModuleType("yandex_ai_studio_sdk")
    fake_module.AIStudio = FakeAIStudio
    fake_settings = SimpleNamespace(
        YANDEX_SEARCH_API_KEY="test-key",
        YANDEX_SEARCH_FOLDER_ID="folder-id",
        YANDEX_SEARCH_MAX_RESULTS=12,
        YANDEX_SEARCH_TIMEOUT_SECONDS=7,
    )
    with patch.dict(sys.modules, {"yandex_ai_studio_sdk": fake_module}), patch(
        "app.sources.yandex_search_source.settings", fake_settings,
    ):
        assert _search_xml("iPhone 17 Pro", region="16") == XML
    assert calls["sdk"] == {"folder_id": "folder-id", "auth": "test-key"}
    assert calls["web"] == (("ru",), {
        "family_mode": "moderate",
        "fix_typo_mode": "on",
        "groups_on_page": 12,
        "group_mode": "deep",
        "docs_in_group": 1,
        "max_passages": 2,
        "localization": "ru",
        "region": "16",
    })
    assert calls["run"] == (("iPhone 17 Pro",), {"format": "xml", "page": 0, "timeout": 7.0})
    req = Request(
        id=1, user_id=1, product="Samsung Galaxy A56", product_name="Samsung Galaxy A56",
        budget="35000", original_query="Samsung Galaxy A56 256GB",
        requirements_json=json.dumps({"storage_gb": 256}),
    )
    accessory = ProductCandidate(title="Аксессуары для Samsung Galaxy A56 256 ГБ", url="https://dns-shop.ru/product/x")
    wrong_memory = ProductCandidate(title="Samsung Galaxy A56 128 ГБ", url="https://dns-shop.ru/product/y")
    assert classify_candidate(accessory, req)[0] == "TRASH"
    assert classify_candidate(wrong_memory, req)[0] == "TRASH"
    gaming = Request(id=2, user_id=1, product="ноутбук", product_name="ноутбук", budget="85000", original_query="ноутбук игровой до 85к", category="laptop")
    weak_laptop = ProductCandidate(title="Игровой ноутбук Intel Celeron N4000", url="https://market.yandex.ru/product--n4000/1")
    assert "игровой" in build_request_search_query(gaming).lower()
    assert classify_candidate(weak_laptop, gaming)[0] == "TRASH"
    print("Yandex Search API XML parser: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
