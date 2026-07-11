"""Deterministic checks for the separate benchmark cache database."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_cache import (
    CACHE_VERSION,
    DEFAULT_EMPTY_TTL_SECONDS,
    DEFAULT_NEGATIVE_TTL_SECONDS,
    DEFAULT_SUCCESS_TTL_SECONDS,
    SearchCache,
)
from tools.search_benchmark import snapshot_or_load


class SearchCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "search_cache.sqlite3"
        self.cache = SearchCache(self.path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def key(self, **overrides: object) -> str:
        context: dict[str, object] = {
            "stage": "source",
            "source": "test_source",
            "query": "iPhone 17 Pro",
            "category": "phone",
            "city": "Москва",
            "budget": "100000",
            "use_case": "photo",
            "is_used_allowed": False,
        }
        context.update(overrides)
        return self.cache.make_cache_key(**context)

    def put(self, key: str | None = None, **overrides: object) -> str:
        result_key = key or self.key()
        status = str(overrides.pop("status", "OK"))
        self.cache.put(
            cache_key=result_key,
            stage="source",
            query="iPhone 17 Pro",
            source="test_source",
            payload={"value": "тест"},
            status=status,
            **overrides,
        )
        return result_key

    def expire(self, key: str) -> None:
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE cache_entries SET expires_at = 1 WHERE cache_key = ?", (key,))
        conn.commit()
        conn.close()

    def test_01_stable_key_for_normalized_requests(self) -> None:
        self.assertEqual(self.key(query=" iPhone 17   Pro "), self.key(query="iphone 17 pro"))

    def test_02_model_modifiers_change_key(self) -> None:
        self.assertNotEqual(self.key(query="iPhone 17 Pro"), self.key(query="iPhone 17e"))

    def test_03_city_changes_key(self) -> None:
        self.assertNotEqual(self.key(city="Москва"), self.key(city="Ярославль"))

    def test_04_budget_changes_key(self) -> None:
        self.assertNotEqual(self.key(budget="100к"), self.key(budget="90000"))

    def test_05_cache_hit(self) -> None:
        key = self.put()
        lookup = self.cache.get(key)
        self.assertEqual(lookup.state, "HIT")
        self.assertEqual(lookup.payload, {"value": "тест"})

    def test_06_cache_miss(self) -> None:
        self.assertEqual(self.cache.get(self.key()).state, "CACHE_MISS")

    def test_07_expired_entry_is_not_returned(self) -> None:
        key = self.put()
        self.expire(key)
        self.assertEqual(self.cache.get(key).state, "CACHE_EXPIRED")

    def test_08_cache_version_mismatch_is_not_returned(self) -> None:
        key = self.put()
        self.assertEqual(self.cache.get(key, cache_version="next-version").state, "CACHE_VERSION_MISMATCH")

    def test_09_success_ttl(self) -> None:
        key = self.put()
        lookup = self.cache.get(key)
        self.assertEqual(lookup.expires_at - lookup.created_at, DEFAULT_SUCCESS_TTL_SECONDS)

    def test_10_negative_cache_ttls(self) -> None:
        empty_key = self.key(query="empty")
        error_key = self.key(query="error")
        self.put(empty_key, status="EMPTY")
        self.put(error_key, status="HTTP 429")
        empty = self.cache.get(empty_key)
        error = self.cache.get(error_key)
        self.assertEqual(empty.expires_at - empty.created_at, DEFAULT_EMPTY_TTL_SECONDS)
        self.assertEqual(error.expires_at - error.created_at, DEFAULT_NEGATIVE_TTL_SECONDS)

    def test_11_corrupt_json_does_not_raise(self) -> None:
        key = self.put()
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE cache_entries SET payload_json = '{bad' WHERE cache_key = ?", (key,))
        conn.commit()
        conn.close()
        self.assertEqual(self.cache.get(key).state, "CACHE_CORRUPT")

    def test_12_purge_expired(self) -> None:
        key = self.put()
        self.expire(key)
        self.assertEqual(self.cache.purge_expired(), 1)
        self.assertEqual(self.cache.get(key).state, "CACHE_MISS")

    def test_13_benchmark_case_is_saved(self) -> None:
        run_id = self.cache.start_benchmark_run(mode="auto", total_cases=1)
        self.cache.save_benchmark_case(
            run_id=run_id,
            case_id="phone",
            query="Нужен телефон",
            started_at=10,
            finished_at=11,
            duration_ms=1000,
            status="DONE",
            result={"saved_for_admin": 2},
        )
        case = self.cache.get_benchmark_case(run_id, "phone")
        self.assertEqual(case["result"]["saved_for_admin"], 2)

    def test_14_interrupted_run_is_saved(self) -> None:
        run_id = self.cache.start_benchmark_run(mode="live", total_cases=3)
        self.cache.finish_benchmark_run(run_id, summary={"completed": 1}, interrupted=True)
        run = self.cache.get_benchmark_run(run_id)
        self.assertEqual(run["interrupted"], 1)
        self.assertEqual(run["summary"]["completed"], 1)

    def test_15_resume_skips_completed_cases(self) -> None:
        run_id = self.cache.start_benchmark_run(mode="auto", total_cases=2)
        self.cache.save_benchmark_case(
            run_id=run_id,
            case_id="done",
            query="готово",
            started_at=1,
            finished_at=2,
            duration_ms=1,
            status="DONE",
        )
        self.assertEqual(self.cache.completed_case_ids(run_id), {"done"})

    def test_16_sqlite_reopens(self) -> None:
        key = self.put()
        fresh_instance = SearchCache(self.path)
        self.assertEqual(fresh_instance.get(key).state, "HIT")

    def test_17_put_updates_existing_key(self) -> None:
        key = self.put()
        self.cache.put(
            cache_key=key,
            stage="source",
            query="iPhone 17 Pro",
            source="test_source",
            payload={"value": "updated"},
            status="OK",
        )
        self.assertEqual(self.cache.get(key).payload, {"value": "updated"})
        self.assertEqual(self.cache.cache_stats()["entries"], 1)

    def test_18_unicode_payload_round_trips(self) -> None:
        key = self.put()
        self.assertEqual(self.cache.get(key).payload["value"], "тест")

    def test_19_secrets_are_not_stored(self) -> None:
        key = self.key(query="private")
        self.cache.put(
            cache_key=key,
            stage="source",
            query="private",
            source="test_source",
            payload={"token": "secret-value", "nested": {"api_key": "secret", "safe": "ok"}},
            status="OK",
        )
        conn = sqlite3.connect(self.path)
        raw = conn.execute("SELECT payload_json FROM cache_entries WHERE cache_key = ?", (key,)).fetchone()[0]
        conn.close()
        self.assertNotIn("secret-value", raw)
        self.assertEqual(self.cache.get(key).payload, {"nested": {"safe": "ok"}})

    def test_20_cached_mode_does_not_call_loader(self) -> None:
        key = self.put()
        calls: list[str] = []

        def loader() -> dict[str, object]:
            calls.append("network")
            return {"unexpected": True}

        state, payload, _ = snapshot_or_load(self.cache, key, mode="cached", loader=loader)
        self.assertEqual(state, "CACHE_HIT")
        self.assertEqual(payload, {"value": "тест"})
        self.assertEqual(calls, [])

    def test_21_corrupt_database_is_recreated(self) -> None:
        corrupt_path = Path(self.temp_dir.name) / "corrupt.sqlite3"
        corrupt_path.write_text("not sqlite", encoding="utf-8")
        cache = SearchCache(corrupt_path)
        self.assertEqual(cache.get(cache.make_cache_key(stage="x", source="x", query="x")).state, "CACHE_MISS")
        self.assertTrue(list(corrupt_path.parent.glob("corrupt.sqlite3.corrupt-*")))

    def test_22_timeout_snapshot_uses_negative_ttl(self) -> None:
        key = self.key(query="slow source")
        self.put(key, status="CASE_TIMEOUT")
        lookup = self.cache.get(key)
        self.assertEqual(lookup.expires_at - lookup.created_at, DEFAULT_NEGATIVE_TTL_SECONDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
