from __future__ import annotations

import os
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.search_v2.feature_flags import SearchEngineMode, get_search_engine_mode
from app.search_v2.models import (
    ExactMatchResult, Offer, Recommendation, RecommendationRole, SearchResultStatus,
    SearchResultV2, VerificationState,
)
from app.search_v2.shadow_compare import build_shadow_comparison, format_shadow_comparison
from app.search_v2.snapshot_store import SNAPSHOT_VERSION, SearchV2SnapshotStore
from app.search_cache import SearchCache
from app.services.search_engine_bridge import SearchEngineBridge, persist_v2_result


@dataclass
class FakeOffer:
    offer_id: str
    title: str
    price: int | None
    source: str
    url: str
    exact_match: str


@dataclass
class FakeResult:
    status: str = "PARTIAL_SUCCESS"
    normalized_offers: list[FakeOffer] = field(default_factory=list)
    rejected_offers: list[FakeOffer] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    source_attempts: list[dict] = field(default_factory=list)
    duration: int = 140
    errors: list[str] = field(default_factory=list)


class FeatureFlagTests(unittest.TestCase):
    def test_default_and_invalid_values_are_legacy(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_search_engine_mode(), SearchEngineMode.LEGACY)
        self.assertEqual(get_search_engine_mode(""), SearchEngineMode.LEGACY)
        self.assertEqual(get_search_engine_mode("production"), SearchEngineMode.LEGACY)

    def test_modes_are_case_and_whitespace_tolerant(self) -> None:
        self.assertEqual(get_search_engine_mode(" Shadow "), SearchEngineMode.SHADOW)
        self.assertEqual(get_search_engine_mode("V2"), SearchEngineMode.V2)
        self.assertEqual(get_search_engine_mode("legacy"), SearchEngineMode.LEGACY)

    def test_telegram_admin_uses_only_the_bridge_boundary(self) -> None:
        handler = (ROOT / "app" / "handlers" / "admin.py").read_text(encoding="utf-8")
        keyboard = (ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")
        self.assertIn("from app.services.search_engine_bridge import", handler)
        self.assertIn("result = await run_search_for_request(req)", handler)
        self.assertNotIn("to_thread(run_product_search, req)", handler)
        self.assertIn('F.data.startswith("v2compare_")', handler)
        self.assertIn('callback_data=f"v2compare_{req_id}"', keyboard)
        self.assertIn("load_shadow_comparison(req_id)", handler)


class ShadowComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.legacy = [{"id": 1, "title": "Legacy", "price": 82_000, "source": "generic", "url": "https://l/1"}]
        self.v2 = FakeResult(
            normalized_offers=[FakeOffer("o1", "iPhone 16 Pro 256", 79_990, "ozon", "https://o/1", "EXACT")],
            rejected_offers=[FakeOffer("o2", "iPhone 16 128", 60_000, "market", "https://m/2", "REQUIRED_SPEC_MISMATCH")],
            recommendations=[{"role": "BEST_OVERALL", "offer_id": "o1"}],
            source_attempts=[{"source": "ozon", "status": "SUCCESS", "duration_ms": 80}],
        )

    def test_snapshot_contains_both_engines_and_delta(self) -> None:
        legacy_result = {"success": True, "found": 1}
        snapshot = build_shadow_comparison(
            request_id=7,
            legacy_result=legacy_result,
            legacy_candidates=self.legacy,
            v2_result=self.v2,
        )
        self.assertEqual(snapshot["schema"], "search_v2:shadow:1")
        self.assertEqual(snapshot["legacy"]["result"], legacy_result)
        self.assertEqual(len(snapshot["legacy"]["candidates"]), 1)
        self.assertEqual(len(snapshot["v2"]["candidates"]), 1)
        self.assertEqual(snapshot["v2"]["exact_count"], 1)
        self.assertEqual(snapshot["v2"]["wrong_product_count"], 1)
        self.assertEqual(snapshot["delta"]["candidate_count"], 0)
        self.assertEqual(snapshot["delta"]["minimum_price"], 79_990)
        self.assertEqual(snapshot["delta"]["legacy_minimum_price"], 82_000)

    def test_input_payload_is_not_mutated(self) -> None:
        legacy_result = {"success": True, "nested": {"value": 1}}
        original = {"success": True, "nested": {"value": 1}}
        build_shadow_comparison(
            request_id=8,
            legacy_result=legacy_result,
            legacy_candidates=self.legacy,
            v2_result=self.v2,
        )
        self.assertEqual(legacy_result, original)
        self.assertEqual(self.v2.status, "PARTIAL_SUCCESS")
        self.assertEqual(len(self.v2.normalized_offers), 1)

    def test_admin_format_is_compact_and_has_no_client_diagnostics_contract(self) -> None:
        snapshot = build_shadow_comparison(
            request_id=9,
            legacy_result={"success": True},
            legacy_candidates=self.legacy,
            v2_result=self.v2,
        )
        text = format_shadow_comparison(snapshot)
        self.assertIn("Legacy ↔ V2", text)
        self.assertIn("V2 exact: 1", text)
        self.assertIn("ozon: SUCCESS", text)
        self.assertLess(len(text), 1000)


class FakeV2Service:
    def __init__(self, result: SearchResultV2 | None = None, error: Exception | None = None) -> None:
        self.result = result or SearchResultV2(status=SearchResultStatus.SUCCESS)
        self.error = error
        self.calls = 0

    async def search(self, _request, *, on_snapshot=None):
        self.calls += 1
        if self.error:
            raise self.error
        if on_snapshot is not None:
            await on_snapshot("discovery", {"completed_sources": ["fake"], "duration": 0.001})
        return self.result


class SearchEngineBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        cache = SearchCache(Path(self.temp.name) / "shadow.sqlite3", cache_version=SNAPSHOT_VERSION)
        self.store = SearchV2SnapshotStore(cache)
        self.request = {"id": 42, "product": "iPhone 16 Pro 256"}

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_shadow_returns_legacy_unchanged_and_saves_v2(self) -> None:
        legacy_payload = {"success": True, "found": 1, "message": "legacy"}
        v2 = SearchResultV2(
            status=SearchResultStatus.SUCCESS,
            normalized_offers=[Offer(offer_id="v2-1", title="Exact", price=79_990, exact_match=ExactMatchResult.EXACT)],
            duration=15,
        )
        writes: list[object] = []
        service = FakeV2Service(v2)
        bridge = SearchEngineBridge(
            legacy_search=lambda _request: legacy_payload,
            v2_service=service,
            candidate_loader=lambda _request_id: [{"id": 1, "title": "Legacy", "price": 82_000}],
            v2_writer=lambda *_args: writes.append(_args),
            snapshot_store=self.store,
        )
        execution = await bridge.execute(self.request, mode="shadow")
        self.assertEqual(execution.client_result, legacy_payload)
        self.assertIs(execution.client_result, legacy_payload)
        self.assertEqual(execution.mode, SearchEngineMode.SHADOW)
        self.assertEqual(service.calls, 1)
        self.assertEqual(writes, [])
        saved = self.store.load_shadow(42)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["v2"]["exact_count"], 1)
        self.assertEqual(saved["legacy"]["result"], legacy_payload)
        partial = self.store.load("normalized", "request:42:partial:1")
        self.assertEqual(partial["stage"], "discovery")
        self.assertEqual(partial["snapshot"]["completed_sources"], ["fake"])

    async def test_shadow_v2_error_never_changes_legacy_result(self) -> None:
        service = FakeV2Service(error=RuntimeError("blocked test source"))
        bridge = SearchEngineBridge(
            legacy_search=lambda _request: {"success": True, "message": "legacy survives"},
            v2_service=service,
            candidate_loader=lambda _request_id: [],
            snapshot_store=self.store,
        )
        execution = await bridge.execute(self.request, mode=SearchEngineMode.SHADOW)
        self.assertTrue(execution.client_result["success"])
        self.assertEqual(execution.client_result["message"], "legacy survives")
        self.assertIsNone(execution.v2_result)
        self.assertEqual(execution.shadow_snapshot["v2"]["status"], "ERROR")
        self.assertIn("blocked test source", self.store.load_shadow(42)["v2"]["errors"][0])

    async def test_v2_success_does_not_call_legacy_and_calls_writer(self) -> None:
        counters = {"legacy": 0, "writer": 0}
        offer = Offer(offer_id="o", title="Exact", exact_match=ExactMatchResult.EXACT)
        result = SearchResultV2(status=SearchResultStatus.MANUAL_REVIEW_REQUIRED, normalized_offers=[offer])

        def legacy(_request):
            counters["legacy"] += 1
            return {"success": True}

        def writer(_request, written_result):
            counters["writer"] += 1
            self.assertIs(written_result, result)

        execution = await SearchEngineBridge(
            legacy_search=legacy, v2_service=FakeV2Service(result), v2_writer=writer,
            candidate_loader=lambda _request_id: [], snapshot_store=self.store,
        ).execute(self.request, mode="v2")
        self.assertEqual(counters["legacy"], 0)
        self.assertEqual(counters["writer"], 1)
        self.assertFalse(execution.used_legacy_fallback)
        self.assertEqual(execution.client_result["found"], 1)
        self.assertIn("Search V2", execution.client_result["message"])

    async def test_v2_system_error_falls_back_to_legacy(self) -> None:
        for status in (SearchResultStatus.ERROR, SearchResultStatus.TIMEOUT):
            with self.subTest(status=status):
                execution = await SearchEngineBridge(
                    legacy_search=lambda _request: {"success": True, "message": "fallback"},
                    v2_service=FakeV2Service(SearchResultV2(status=status)),
                    candidate_loader=lambda _request_id: [], snapshot_store=self.store,
                ).execute(self.request, mode="v2")
                self.assertTrue(execution.used_legacy_fallback)
                self.assertEqual(execution.client_result["message"], "fallback")
                self.assertEqual(execution.v2_result.status, status)


class V2PersistenceCompatibilityTests(unittest.TestCase):
    def test_v2_rows_use_existing_schema_and_are_idempotent(self) -> None:
        from app import db
        from app.verification_state import resolve_final_presentation

        with tempfile.TemporaryDirectory() as temp:
            old_path = db.settings.DB_PATH
            try:
                db.settings.DB_PATH = str(Path(temp) / "v2-bridge.sqlite3")
                db.init_db()
                request_id = db.create_request(user_id=1, username="test", product="iPhone 16 Pro")
                automatic = VerificationState(
                    model_verified=True, link_verified=True, price_verified=True,
                    availability_verified=True, seller_verified=True,
                )
                offer = Offer(
                    offer_id="v2-offer", source="dns", platform="DNS", title="iPhone 16 Pro 256 ГБ",
                    url="https://dns-shop.ru/product/123456/", price=79_000,
                    exact_match=ExactMatchResult.EXACT,
                    automatic_verification=automatic,
                    final_verification=automatic,
                )
                result = SearchResultV2(
                    status=SearchResultStatus.SUCCESS,
                    normalized_offers=[offer],
                    recommendations=[Recommendation(
                        role=RecommendationRole.BEST_OVERALL,
                        offer_id=offer.offer_id,
                        offer=offer,
                        score=90,
                    )],
                )
                request = db.Request(id=request_id, user_id=1, product="iPhone 16 Pro")
                self.assertEqual(persist_v2_result(request, result), 1)
                rows = [item for item in db.get_search_results(request_id) if item.origin == "v2"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].status, "CANDIDATE")
                self.assertIn("BEST_OVERALL", rows[0].admin_note)
                facts = json.loads(rows[0].facts_json)
                self.assertEqual(facts["search_engine"], "v2")
                self.assertEqual(facts["v2_recommendation_role"], "BEST_OVERALL")
                self.assertTrue(resolve_final_presentation(facts)["presentation_ready"])
                self.assertIn("search_v2_offer", facts)

                self.assertEqual(persist_v2_result(request, result), 1)
                rows = [item for item in db.get_search_results(request_id) if item.origin == "v2"]
                self.assertEqual(len(rows), 1)
            finally:
                db.settings.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
