"""Deterministic checks for the separate benchmark cache database."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
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
from app.db import Request
from app.product_search import (
    ProductCandidate,
    SearchCollection,
    _apply_verification,
    collect_product_candidates,
    verification_candidate_limit,
)
from tools.search_benchmark import (
    CACHE_SOURCE,
    CACHE_STAGE,
    BenchmarkCase,
    _cache_source_event,
    _make_request,
    _print_case_result,
    _snapshot_key,
    preferred_snapshot,
    snapshot_or_load,
    status_after_case_timeout,
)


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

    def put_snapshot(self, status: str, *, raw_count: int = 0) -> tuple[Request, dict[str, object], str]:
        request, parsed = _make_request("Нужна кофемашина с капучинатором до 35к", 1)
        key = _snapshot_key(self.cache, request, parsed, status)
        raw_candidates = [
            {"title": f"Кофемашина {index}", "source": "test_source", "url": f"https://example.test/product/{index}"}
            for index in range(raw_count)
        ]
        self.cache.put(
            cache_key=key,
            stage=CACHE_STAGE,
            query=request.clean_search_query or request.original_query,
            source=f"{CACHE_SOURCE}:{status.lower()}",
            payload={
                "snapshot_status": status,
                "raw_candidates": raw_candidates,
                "candidates": raw_candidates,
                "parsed": parsed,
                "category": "unknown",
                "verify_stats": {},
                "quality_stats": {},
                "attempts": [],
            },
            status=status,
        )
        return request, parsed, key

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

    def test_23_source_result_is_cached_before_case_finishes(self) -> None:
        request, _ = _make_request("Нужна кофемашина с капучинатором до 35к", 1)
        attempt = SimpleNamespace(
            source="test_source",
            query=request.clean_search_query,
            status="OK",
            found_count=1,
            kept_count=1,
            error_text="",
            duration_ms=12,
        )
        candidate = ProductCandidate(
            title="Кофемашина Test 100",
            url="https://example.test/product/coffee-100",
            source="test_source",
            price=30_000,
        )
        _cache_source_event(self.cache, request, "unknown", attempt, [candidate])
        key = self.cache.make_cache_key(
            stage="source",
            source="test_source",
            query=request.clean_search_query,
            category="unknown",
            city=request.city,
            budget=request.budget,
            use_case=request.use_case or request.purpose,
            is_used_allowed=request.is_used_allowed,
        )
        self.assertEqual(self.cache.get(key).state, "HIT")
        self.assertEqual(len(self.cache.get(key).payload["candidates"]), 1)

    def test_24_case_timeout_does_not_delete_source_cache(self) -> None:
        self.test_23_source_result_is_cached_before_case_finishes()
        self.put_snapshot("CASE_TIMEOUT")
        stats = self.cache.cache_stats()
        self.assertEqual(stats["source_cache_entries"], 1)
        self.assertEqual(stats["benchmark_snapshots"], 1)

    def test_25_one_success_source_and_timeouts_is_partial(self) -> None:
        snapshot = {"raw_candidates": [{"title": "Кофемашина"}], "failed_sources": ["a", "b"]}
        self.assertEqual(status_after_case_timeout(snapshot), "PARTIAL_SUCCESS")

    def test_26_zero_success_sources_is_case_timeout(self) -> None:
        self.assertEqual(status_after_case_timeout({"raw_candidates": []}), "CASE_TIMEOUT")

    def test_27_cached_prefers_success_over_timeout(self) -> None:
        request, parsed, _ = self.put_snapshot("SUCCESS", raw_count=1)
        self.put_snapshot("CASE_TIMEOUT")
        status, lookup, _ = preferred_snapshot(self.cache, request, parsed)
        self.assertEqual(status, "SUCCESS")
        self.assertEqual(lookup.state, "HIT")

    def test_28_cached_prefers_partial_over_timeout(self) -> None:
        request, parsed, _ = self.put_snapshot("PARTIAL_SUCCESS", raw_count=1)
        self.put_snapshot("CASE_TIMEOUT")
        status, _, _ = preferred_snapshot(self.cache, request, parsed)
        self.assertEqual(status, "PARTIAL_SUCCESS")

    def test_29_new_timeout_does_not_override_success(self) -> None:
        request, parsed, _ = self.put_snapshot("SUCCESS", raw_count=1)
        self.put_snapshot("CASE_TIMEOUT")
        status, lookup, _ = preferred_snapshot(self.cache, request, parsed)
        self.assertEqual(status, "SUCCESS")
        self.assertEqual(lookup.payload["snapshot_status"], "SUCCESS")

    def test_30_fast_verification_limit(self) -> None:
        self.assertEqual(verification_candidate_limit("fast"), 3)
        self.assertEqual(verification_candidate_limit("none"), 0)

    def test_31_verification_none_does_not_call_page_verifier(self) -> None:
        request = Request(id=0, user_id=0, product="кофемашина", product_name="кофемашина", budget="35000")
        collection = SearchCollection(candidates=[
            ProductCandidate(
                title="Кофемашина Test 100",
                url="https://example.test/product/coffee-100",
                source="direct_store",
                price=30_000,
                quality="OK",
                status="CANDIDATE",
            )
        ])
        with patch("app.product_search.verify_candidates") as verifier:
            _apply_verification(collection, request, verification_mode="none")
        verifier.assert_not_called()
        self.assertEqual(collection.candidates[0].verify_status, "NEED_MANUAL_CHECK")

    def test_32_source_budget_timeout_does_not_stop_other_sources(self) -> None:
        request, _ = _make_request("Нужна кофемашина с капучинатором до 35к", 1)
        events: list[str] = []
        with patch("app.product_search._is_generic_enabled", return_value=True), patch(
            "app.product_search._is_source_enabled", return_value=True
        ):
            collect_product_candidates(
                request,
                verification_mode="none",
                source_timeout_seconds=0,
                source_observer=lambda attempt, _: events.append(attempt.status),
            )
        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(all(status == "SOURCE_TIMEOUT" for status in events))

    def test_33_negative_cache_does_not_call_auto_loader(self) -> None:
        key = self.key(query="blocked source")
        self.put(key, status="HTTP 429")
        calls: list[str] = []
        state, _, _ = snapshot_or_load(
            self.cache,
            key,
            mode="auto",
            loader=lambda: calls.append("network") or {},
        )
        self.assertEqual(state, "CACHE_HIT")
        self.assertEqual(calls, [])

    def test_34_cache_stats_group_runs_and_statuses(self) -> None:
        self.put_snapshot("SUCCESS", raw_count=1)
        self.put_snapshot("CASE_TIMEOUT")
        run_success = self.cache.start_benchmark_run(mode="live", total_cases=1)
        run_partial = self.cache.start_benchmark_run(mode="live", total_cases=1)
        for run_id, status in ((run_success, "SUCCESS"), (run_partial, "PARTIAL_SUCCESS")):
            self.cache.save_benchmark_case(
                run_id=run_id,
                case_id=status.lower(),
                query="test",
                started_at=1,
                finished_at=2,
                duration_ms=1,
                status=status,
            )
        stats = self.cache.cache_stats()
        self.assertEqual(stats["unique_run_ids"], 2)
        self.assertEqual(stats["benchmark_case_statuses"]["SUCCESS"], 1)
        self.assertEqual(stats["benchmark_case_statuses"]["PARTIAL_SUCCESS"], 1)
        self.assertEqual(len(stats["benchmark_cases_by_run"]), 2)

    def test_35_interrupted_run_keeps_partial_case(self) -> None:
        run_id = self.cache.start_benchmark_run(mode="live", total_cases=1)
        self.cache.save_benchmark_case(
            run_id=run_id,
            case_id="partial",
            query="test",
            started_at=1,
            finished_at=2,
            duration_ms=1,
            status="PARTIAL_SUCCESS",
            result={"saved_for_admin": 1},
        )
        self.cache.finish_benchmark_run(run_id, summary={"completed": 1}, interrupted=True)
        self.assertEqual(self.cache.get_benchmark_case(run_id, "partial")["status"], "PARTIAL_SUCCESS")
        self.assertEqual(self.cache.get_benchmark_run(run_id)["interrupted"], 1)

    def test_36_resume_continues_partial_case(self) -> None:
        run_id = self.cache.start_benchmark_run(mode="live", total_cases=1)
        self.cache.save_benchmark_case(
            run_id=run_id,
            case_id="partial",
            query="test",
            started_at=1,
            finished_at=2,
            duration_ms=1,
            status="PARTIAL_SUCCESS",
        )
        self.assertNotIn("partial", self.cache.completed_case_ids(run_id))

    def test_37_progress_prints_request_flush(self) -> None:
        result = {
            "case_id": "test",
            "saved_for_admin": 0,
            "cache_age_seconds": 0,
            "parsed_product_name": "кофемашина",
            "category": "unknown",
            "VERIFIED_GOOD": 0,
            "VERIFIED_OK": 0,
            "NEED_MANUAL_CHECK": 0,
            "VERIFY_BLOCKED": 0,
            "PRICE_MISSING saved": 0,
            "snapshot_status": "PARTIAL_SUCCESS",
            "completed_sources": ["test"],
            "failed_sources": [],
            "candidate_count": 1,
            "is_partial": True,
            "top": [],
        }
        with patch("builtins.print") as printer:
            _print_case_result(1, 1, "PARTIAL_SUCCESS", 10, result)
        self.assertTrue(printer.call_args_list)
        self.assertTrue(all(call.kwargs.get("flush") is True for call in printer.call_args_list))


if __name__ == "__main__":
    unittest.main(verbosity=2)
