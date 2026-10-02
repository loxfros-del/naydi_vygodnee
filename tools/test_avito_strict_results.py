"""Replay the PS5/PS4 incident through the real pipeline and local HTTP API."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from threading import Thread
import time
import unittest
from urllib.request import Request, urlopen

from avito_service.config import ServiceConfig
from avito_service.http_api import make_server
from avito_service.models import SearchRequest
from tools.test_avito_collection_quality import EvidenceProvider, EvidenceReviewer, row, service


def console_row(identifier, price, *, model="PlayStation 5", title=None):
    item = row(identifier, price)
    item.update(title=title or f"Sony {model}", location="Ярославль",
                url=f"https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/console_{identifier}",
                description="Консоль исправна. Не ремонтировалась. Оригинальные детали. Полный комплект. Цена окончательная.")
    item["parameters"] = {"Модель": model, "Состояние": "Отличное", "Комплектация": "Полный комплект",
                          "Ремонт": "Не ремонтировался", "Оригинальность деталей": "Оригинальные"}
    return item


def search_request(mode="bargain"):
    return SearchRequest("PlayStation 5", location="Ярославль", category="gaming", mode=mode,
                         price_max=35_000, desired_results=3)


def provider(*, wrong=False, price=30_000):
    source = EvidenceProvider()
    source.market_rows = [console_row(20000000 + i, 40_000 + i * 100) for i in range(10)]
    source.candidate_rows = [console_row(10000000, price)]
    if wrong:
        source.candidate_rows = [console_row(10000000 + i, price + i * 100, model="PlayStation 4 Slim",
                                title="Sony Playstation 4 slim PS4 0,5-1Тб прошита + игры") for i in range(3)]
    return source


class StrictResultTests(unittest.TestCase):
    def test_screenshot_ps4_results_never_fill_ps5_public_output(self):
        source, reviewer = provider(wrong=True), EvidenceReviewer()
        report = service(source, reviewer).search(search_request())
        result = report.public_dict()
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["discoveredListings"], [])
        self.assertEqual(result["visibleCount"], 0)
        self.assertEqual(result["resultPolicy"], "verified_exact_matches_with_optional_savings")
        self.assertNotIn("Playstation 4", json.dumps(result, ensure_ascii=False))
        self.assertTrue(result["emptyReason"])
        self.assertLessEqual(len(result["warnings"]), 1)
        self.assertFalse(any(identifier.startswith("100") for identifier, _ in reviewer.text_calls))
        self.assertEqual(reviewer.photo_calls, [])
        self.assertEqual(source.refresh_calls, [])
        self.assertTrue(report.public_dict(include_admin=True)["adminDiscoveredListings"])

    def test_one_real_verified_bargain_is_shown_without_padding_to_three(self):
        source = provider()
        report = service(source).search(search_request())
        result = report.public_dict()
        self.assertEqual(result["visibleCount"], 1)
        self.assertEqual(result["requestedResults"], 3)
        self.assertEqual(result["discoveredListings"], [])
        card = result["recommendations"][0]
        self.assertEqual(card["listing"]["id"], "10000000")
        self.assertTrue(card["belowMarket"])
        self.assertEqual(card["comparableSellerCount"], 10)
        self.assertEqual(card["listing"]["verificationStatus"], "verified")
        self.assertEqual(len(source.refresh_calls), 1)

    def test_bargain_mode_shows_exact_match_without_zero_or_two_comparables(self):
        for comparable_count in (0, 2):
            with self.subTest(comparable_count=comparable_count):
                source = provider()
                source.market_rows = source.market_rows[:comparable_count]
                reviewer = EvidenceReviewer()
                result = service(source, reviewer).search(search_request()).public_dict()
                self.assertEqual(result["visibleCount"], 1)
                self.assertEqual(result["discoveredListings"], [])
                card = result["recommendations"][0]
                self.assertFalse(card["belowComparables"])
                self.assertIsNone(card["savingAmount"])
                self.assertIn("Точное совпадение", card["reasons"][0])
                self.assertEqual(reviewer.photo_calls, ["10000000"])
                self.assertEqual(source.refresh_calls[0][0], ("10000000",))

    def test_refresh_failure_or_price_change_cannot_leave_a_bargain(self):
        for failure in ("unavailable", "price", "model"):
            source = provider()
            if failure == "unavailable":
                source.refresh_error = True
            elif failure == "price":
                source.refresh_changes["10000000"] = {"price": 50_000}
            else:
                source.refresh_changes["10000000"] = {"title": "Sony PlayStation 4"}
            result = service(source).search(search_request()).public_dict()
            self.assertEqual(result["recommendations"], [])
            self.assertEqual(result["discoveredListings"], [])

    def test_final_serialization_rechecks_query_even_if_old_role_was_top(self):
        report = service(provider()).search(search_request())
        changed = replace(report, query="PlayStation 4", request=replace(search_request(), query="PlayStation 4"))
        self.assertEqual(changed.public_dict()["recommendations"], [])

    def test_reuse_paid_matching_market_row_requires_full_photo_and_refresh(self):
        class ReusableProvider(EvidenceProvider):
            def refresh(self, listings, **kwargs):
                previous = self.candidate_rows
                try:
                    self.candidate_rows = [*previous, *self.market_rows]
                    return super().refresh(listings, **kwargs)
                finally:
                    self.candidate_rows = previous

        source = ReusableProvider()
        source.market_rows = [console_row(20000000, 30_000), *[console_row(20000001 + i, 40_000 + i * 100) for i in range(10)]]
        source.candidate_rows = []
        reviewer = EvidenceReviewer()
        result = service(source, reviewer).search(search_request()).public_dict()
        self.assertEqual([card["listing"]["id"] for card in result["recommendations"]], ["20000000"])
        self.assertIn("20000000", reviewer.photo_calls)
        self.assertEqual(len(source.market_calls), 1)
        self.assertEqual(len(source.candidate_calls), 1)
        self.assertEqual(source.refresh_calls[0][0], ("20000000",))

    def test_real_http_endpoint_rejects_ps4_and_keeps_verified_ps5(self):
        for wrong, count in ((True, 0), (False, 1)):
            with self.subTest(wrong=wrong), tempfile.TemporaryDirectory() as temporary:
                engine = service(provider(wrong=wrong))
                server = make_server("127.0.0.1", 0, service=engine,
                                     config=ServiceConfig(apify_token="offline-test", ai_api_key="offline-test",
                                                          ai_base_url="https://example.invalid", ai_model="fixture"),
                                     support_path=Path(temporary) / "support.jsonl", owner_token="", access_token="")
                worker = Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    base = f"http://127.0.0.1:{server.server_address[1]}"
                    payload = {"query": "PlayStation 5", "location": "Ярославль", "category": "gaming",
                               "mode": "bargain", "desiredResults": 3, "priceMax": 35000}
                    call = Request(base + "/api/avito/search", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                    with urlopen(call, timeout=5) as response:
                        job = json.load(response)
                    deadline = time.monotonic() + 5
                    while job["state"] == "running" and time.monotonic() < deadline:
                        time.sleep(0.01)
                        with urlopen(base + "/api/avito/jobs/" + job["jobId"], timeout=5) as response:
                            job = json.load(response)
                    self.assertEqual(job["state"], "complete", job)
                    result = job["result"]
                    self.assertEqual(result["visibleCount"], count)
                    self.assertEqual(result["discoveredListings"], [])
                    self.assertNotIn("adminDiscoveredListings", result)
                    self.assertEqual(result["resultPolicy"], "verified_exact_matches_with_optional_savings")
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
