from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_cache import SearchCache
from app.search_v2.models import SearchRequestV2, SearchResultStatus, SearchResultV2
from app.search_v2.snapshot_store import NAMESPACES, SNAPSHOT_VERSION, SearchV2SnapshotStore


class SearchV2SnapshotStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        cache = SearchCache(Path(self.temp.name) / "search_cache.sqlite3", cache_version=SNAPSHOT_VERSION)
        self.store = SearchV2SnapshotStore(cache)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_all_required_namespaces_are_versioned(self) -> None:
        self.assertEqual(set(NAMESPACES), {"source", "normalized", "result", "shadow"})
        self.assertTrue(all(value.startswith("search_v2:") for value in NAMESPACES.values()))
        self.assertIn("v2", SNAPSHOT_VERSION)

    def test_shadow_round_trip_uses_existing_sqlite_cache(self) -> None:
        payload = {"legacy": {"candidates": 2}, "v2": {"candidates": 3}}
        key = self.store.save_shadow(17, payload)
        self.assertTrue(key)
        self.assertEqual(self.store.load_shadow(17), payload)
        self.assertIsNone(self.store.load_shadow(18))
        self.assertTrue(self.store.cache.path.exists())

    def test_domain_result_round_trip_is_json_safe(self) -> None:
        result = SearchResultV2(
            normalized_request=SearchRequestV2(canonical_model="iPhone 16", model_modifiers=["Pro"]),
            status=SearchResultStatus.SUCCESS,
        )
        self.store.save_result("iphone-pro", result)
        loaded = self.store.load_result("iphone-pro")
        self.assertIsInstance(loaded, dict)
        self.assertEqual(loaded["status"], "SUCCESS")
        self.assertEqual(loaded["normalized_request"]["canonical_model"], "iPhone 16")
        self.assertEqual(loaded["normalized_request"]["model_modifiers"], ["Pro"])

    def test_result_keys_do_not_mix_model_modifiers(self) -> None:
        self.store.save_result("iphone-16", {"model": "iPhone 16"})
        self.store.save_result("iphone-16-pro", {"model": "iPhone 16 Pro"})
        self.assertEqual(self.store.load_result("iphone-16")["model"], "iPhone 16")
        self.assertEqual(self.store.load_result("iphone-16-pro")["model"], "iPhone 16 Pro")
        self.assertNotEqual(
            self.store._key("result", "iphone-16"),
            self.store._key("result", "iphone-16-pro"),
        )


if __name__ == "__main__":
    unittest.main()
