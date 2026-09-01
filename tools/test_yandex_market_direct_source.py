from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sources import direct_retail_source as source_module


MARKET_HTML = '''
<article data-auto="searchOrganic">
  <a href="/card/macbook-air-m4-13/123"><img alt="Ноутбук Apple MacBook Air 13 M4 16 ГБ / 256 ГБ SSD"></a>
  <noframes>{"price":{"value":"89 990"},"zoneData":{"price":{"value":"89990"}}}</noframes>
</article>
<article data-auto="searchOrganic">
  <a href="/card/macbook-air-m4-13/456"><img alt="Ноутбук Apple MacBook Air 13 M4 16 ГБ / 512 ГБ SSD"></a>
  <noframes>{"price":{"value":"154037"},"zoneData":{"price":{"value":"116529"}}}</noframes>
</article>
<article data-auto="searchOrganic">
  <a href="/search?text=MacBook"><img alt="Страница поиска"></a>
  <noframes>{"price":{"value":"1"}}</noframes>
</article>
'''


class YandexMarketDirectSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.market = source_module._source_by_key("yandex_market")

    def test_market_url_uses_public_cheapest_sort(self) -> None:
        url = source_module._build_url(self.market, "Apple MacBook Air M4")
        self.assertIn("text=Apple+MacBook+Air+M4", url)
        self.assertIn("how=aprice", url)

    def test_organic_cards_are_direct_and_sorted_by_current_price(self) -> None:
        debug = {
            "source": source_module.YANDEX_MARKET_DIRECT_SOURCE,
            "store": "Yandex Market",
            "url": "https://market.yandex.ru/search?text=MacBook&how=aprice",
            "status": "ok",
            "elapsed": 0.01,
            "status_code": 200,
            "raw_count": 0,
            "candidates_count": 0,
            "error": "",
        }
        with patch.object(source_module, "_fetch_source", return_value=(MARKET_HTML, debug)):
            result = source_module.debug_search_direct_retail_source(
                self.market,
                "Apple MacBook Air M4",
                {"product": "MacBook Air M4"},
            )

        cards = result["candidates"]
        self.assertEqual([item["price"] for item in cards], [89_990, 116_529])
        self.assertTrue(all("market.yandex.ru/card/" in item["url"] for item in cards))
        self.assertEqual(result["candidates_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
