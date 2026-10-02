"""Offline tests for persistent, serialized Apify spending reservations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from avito_service.errors import ExternalServiceError
from avito_service.spending import SpendingGuard


class AvitoSpendingTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "spending.json"
        self.guard = SpendingGuard(self.path)
        self.clock = patch("avito_service.spending._now", return_value=datetime(2026, 9, 9, 12, tzinfo=timezone.utc))
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_missing_journal_starts_blank_and_persists_reservations(self):
        self.guard.reserve(0.4)
        reloaded = SpendingGuard(self.path)
        self.assertEqual(reloaded.snapshot()["dailyCommittedUsd"], 0.4)
        with self.assertRaises(ExternalServiceError) as caught:
            reloaded.reserve(0.7)
        self.assertEqual(caught.exception.code, "APIFY_SPEND_LIMIT")
        self.assertFalse(caught.exception.retryable)

    def test_final_settlement_reduces_reserved_cost(self):
        identifier = self.guard.reserve(0.8)
        self.guard.settle(identifier, 0.1, final=True)
        self.guard.reserve(0.9)
        self.assertEqual(self.guard.snapshot()["dailyCommittedUsd"], 1)
        self.assertEqual(self.guard.snapshot()["actualSpendUsd"], 0.1)
        self.assertEqual(self.guard.snapshot()["activeReservationUsd"], 0.9)
        with self.assertRaises(ExternalServiceError):
            self.guard.reserve(0.00001)

    def test_proven_unbilled_reservation_can_be_released_but_actual_cannot(self):
        active = self.guard.reserve(0.4)
        estimate = self.guard.reserve(0.3)
        actual = self.guard.reserve(0.2)
        self.guard.settle(estimate, None, final=False)
        self.guard.settle(actual, 0.1, final=True)
        self.guard.release_unbilled(active)
        self.guard.release_unbilled(estimate)
        self.assertAlmostEqual(self.guard.snapshot()["dailyCommittedUsd"], 0.1)
        with self.assertRaises(ExternalServiceError):
            self.guard.release_unbilled(actual)
        with self.assertRaises(ExternalServiceError):
            self.guard.release_unbilled(active)

    def test_uncertain_billing_and_ambiguous_start_keep_full_reservation(self):
        identifier = self.guard.reserve(0.8)
        self.guard.settle(identifier, 0.005, final=False)
        self.guard.settle(identifier, None, final=False)
        self.assertEqual(self.guard.snapshot()["dailyCommittedUsd"], 0.8)
        self.assertEqual(self.guard.snapshot()["settledEstimateUsd"], 0.8)
        self.assertEqual(self.guard.snapshot()["activeReservationUsd"], 0)
        with self.assertRaises(ExternalServiceError):
            SpendingGuard(self.path).reserve(0.21)

    def test_utc_plus_three_midnight_resets_day_but_not_month(self):
        self.now.return_value = datetime(2026, 9, 9, 20, 59, tzinfo=timezone.utc)
        self.guard.reserve(1)
        self.now.return_value = datetime(2026, 9, 9, 21, 0, tzinfo=timezone.utc)
        self.guard.reserve(1)
        snapshot = self.guard.snapshot()
        self.assertEqual(snapshot["date"], "2026-09-10")
        self.assertEqual(snapshot["dailyCommittedUsd"], 1)
        self.assertEqual(snapshot["monthlyCommittedUsd"], 2)

    def test_calendar_month_limit_and_rollover(self):
        self.guard.record_existing("prior-runs", 17.5, "2026-09-01")
        self.guard.reserve(0.5)
        self.now.return_value = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        with self.assertRaises(ExternalServiceError):
            self.guard.reserve(0.01)
        self.now.return_value = datetime(2026, 9, 30, 21, tzinfo=timezone.utc)
        self.guard.reserve(1)
        self.assertEqual(self.guard.snapshot()["monthlyCommittedUsd"], 1)

    def test_subscription_period_does_not_reset_on_calendar_month_boundary(self):
        guard = SpendingGuard(self.path, billing_cycle_day=9)
        guard.record_existing("previous-spend", 17.5, "2026-09-09")
        self.now.return_value = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        guard.reserve(0.5)
        for date in (datetime(2026, 10, 1, 12, tzinfo=timezone.utc), datetime(2026, 10, 8, 12, tzinfo=timezone.utc)):
            self.now.return_value = date
            with self.assertRaises(ExternalServiceError):
                guard.reserve(0.01)
            snapshot = guard.snapshot()
            self.assertEqual(snapshot["monthlyCommittedUsd"], 18)
            self.assertEqual(snapshot["monthlyPeriodStart"], "2026-09-09")
            self.assertEqual(snapshot["monthlyPeriodEndExclusive"], "2026-10-09")
        self.now.return_value = datetime(2026, 10, 8, 21, tzinfo=timezone.utc)
        guard.reserve(1)
        self.assertEqual(guard.snapshot()["monthlyCommittedUsd"], 1)
        self.assertEqual(guard.snapshot()["monthlyPeriodStart"], "2026-10-09")

    def test_subscription_anchor_clamps_to_short_month(self):
        guard = SpendingGuard(self.path, billing_cycle_day=31)
        guard.record_existing("january-spend", 0.8, "2026-01-31")
        self.now.return_value = datetime(2026, 2, 27, 12, tzinfo=timezone.utc)
        snapshot = guard.snapshot()
        self.assertEqual(snapshot["monthlyCommittedUsd"], 0.8)
        self.assertEqual(snapshot["monthlyPeriodStart"], "2026-01-31")
        self.assertEqual(snapshot["monthlyPeriodEndExclusive"], "2026-02-28")
        self.now.return_value = datetime(2026, 2, 28, 12, tzinfo=timezone.utc)
        snapshot = guard.snapshot()
        self.assertEqual(snapshot["monthlyCommittedUsd"], 0)
        self.assertEqual(snapshot["monthlyPeriodStart"], "2026-02-28")
        self.assertEqual(snapshot["monthlyPeriodEndExclusive"], "2026-03-31")

    def test_invalid_billing_anchor_fails_closed(self):
        for value in (0, 32, -1, True, "9", None):
            with self.subTest(value=value), self.assertRaises(ExternalServiceError):
                SpendingGuard(self.path, billing_cycle_day=value)
        self.assertFalse(self.path.exists())

    def test_concurrent_independent_guards_cannot_over_reserve(self):
        def reserve(_):
            try:
                SpendingGuard(self.path).reserve(0.2)
                return True
            except ExternalServiceError as exc:
                self.assertEqual(exc.code, "APIFY_SPEND_LIMIT")
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(20)))
        self.assertEqual(sum(results), 5)
        self.assertEqual(self.guard.snapshot()["dailyCommittedUsd"], 1)
        self.assertEqual(len(json.loads(self.path.read_text())["records"]), 5)

    def test_separate_processes_share_the_same_spending_ceiling(self):
        code = "from avito_service.spending import SpendingGuard; from avito_service.errors import ExternalServiceError; import sys\ntry:\n SpendingGuard(sys.argv[1]).reserve(0.4)\nexcept ExternalServiceError as exc:\n sys.exit(2 if exc.code == 'APIFY_SPEND_LIMIT' else 3)\n"
        processes = [subprocess.Popen([sys.executable, "-c", code, str(self.path)],
                                      cwd=Path(__file__).resolve().parents[1],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        try:
            codes = []
            for process in processes:
                _, stderr = process.communicate(timeout=15)
                self.assertIn(process.returncode, (0, 2), stderr.decode("utf-8", errors="replace"))
                codes.append(process.returncode)
            self.assertEqual(codes.count(0), 2)
            state = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["records"]), 2)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    @unittest.skipUnless(os.name == "nt", "Windows byte-range lock regression")
    def test_empty_lock_owned_by_another_process_fails_closed_then_recovers(self):
        code = (
            "import msvcrt, sys\n"
            "with open(sys.argv[1], 'a+b') as stream:\n"
            " stream.seek(0)\n"
            " msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)\n"
            " print('LOCKED_EMPTY', flush=True)\n"
            " sys.stdin.readline()\n"
            " stream.seek(0)\n"
            " msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)\n"
        )
        lock_path = self.path.with_name(self.path.name + ".lock")
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(lock_path)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "LOCKED_EMPTY")
            self.assertEqual(lock_path.stat().st_size, 0)
            # Advance only the retry deadline; the child still owns the lock.
            with patch("avito_service.spending.time.monotonic", side_effect=(0.0, 3.0)):
                with self.assertRaises(ExternalServiceError) as caught:
                    self.guard.reserve(0.4)
            self.assertEqual(caught.exception.code, "APIFY_SPEND_LOCKED")
            self.assertFalse(caught.exception.retryable)
            self.assertFalse(self.path.exists())
        finally:
            try:
                _, stderr = process.communicate(input="release\n", timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _, stderr = process.communicate()
        self.assertEqual(process.returncode, 0, stderr)
        self.guard.reserve(0.4)
        self.assertEqual(self.guard.snapshot()["dailyCommittedUsd"], 0.4)
        self.assertEqual(lock_path.stat().st_size, 0)

    def test_corrupt_journal_fails_closed_without_resetting_it(self):
        for content in ("", "broken", '{"schema":1,"records":{"x":{"date":"2026-09-09","reserved_units":-1}}}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(ExternalServiceError) as caught:
                    self.guard.reserve(0.01)
                self.assertEqual(caught.exception.code, "APIFY_SPEND_STATE_INVALID")
                self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_existing_run_seed_is_idempotent_and_counts_today(self):
        entries = [{"run_id": f"run-{index}", "actual_cost_usd": 0.10084, "timestamp": "2026-09-09"} for index in range(6)]
        self.guard.seed_initial_spend(entries)
        self.guard.seed_initial_spend(entries)
        self.assertEqual(self.guard.snapshot()["dailyCommittedUsd"], 0.60504)
        with self.assertRaises(ExternalServiceError):
            self.guard.reserve(0.4)
        self.guard.reserve(0.39496)

    def test_revised_receipt_increases_existing_run_cost_without_double_count(self):
        self.guard.record_existing("same-run", 0.005, "2026-09-09")
        self.guard.record_existing("same-run", 0.13079, "2026-09-09T12:00:00Z")
        self.guard.record_existing("same-run", 0.005, "2026-09-09")
        self.assertEqual(self.guard.snapshot()["dailyCommittedUsd"], 0.13079)

    def test_invalid_amount_or_unknown_reservation_cannot_release_funds(self):
        identifier = self.guard.reserve(0.8)
        for value in (-1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ExternalServiceError):
                self.guard.settle(identifier, value)
        with self.assertRaises(ExternalServiceError):
            self.guard.settle("not-found", 0)
        self.assertEqual(self.guard.snapshot()["dailyCommittedUsd"], 0.8)

    def test_failed_atomic_write_never_returns_a_reservation(self):
        self.guard.reserve(0.2)
        before = self.path.read_bytes()
        with patch("avito_service.spending.os.replace", side_effect=OSError("injected write error")):
            with self.assertRaises(ExternalServiceError):
                self.guard.reserve(0.3)
        self.assertEqual(self.path.read_bytes(), before)

    def test_settlement_above_estimate_is_recorded_and_blocks_future_spend(self):
        identifier = self.guard.reserve(0.5)
        self.guard.settle(identifier, 1.1)
        self.assertEqual(self.guard.snapshot()["dailyCommittedUsd"], 1.1)
        with self.assertRaises(ExternalServiceError):
            self.guard.reserve(0.01)


if __name__ == "__main__":
    unittest.main()
