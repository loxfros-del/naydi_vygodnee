"""Read-only reservation audit never turns a date/cap coincidence into a receipt."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.audit_avito_reservations import audit


class ReservationAuditTests(unittest.TestCase):
    def test_same_day_and_cap_remain_unproven_and_ledger_is_untouched(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reservation = "reservation:" + "a" * 32
            ledger = root / "avito_spend.json"
            ledger.write_text(json.dumps({"schema": 1, "records": {reservation: {
                "date": "2026-09-27", "reserved_units": 80400000, "settled_units": None,
            }}}), encoding="utf-8")
            original = ledger.read_bytes()
            (root / "trace.json").write_text(json.dumps({"collection": {"apify_runs": [{
                "run_id": "A" * 17, "status": "SUCCEEDED", "started_at": "2026-09-27T12:00:00Z",
                "actual_cost_usd": 0.4, "requested_max_total_charge_usd": 0.804,
            }]}, "unrelated": {"status": {"nested": True}}}), encoding="utf-8")
            result = audit(root, ledger)
            self.assertEqual(ledger.read_bytes(), original)
            self.assertTrue(result["ledger_unchanged_during_audit"])
            self.assertEqual(result["ledger_writes"], 0)
            self.assertEqual(result["pending_reserved_usd"], 0.804)
            candidate = result["reservations"][0]
            self.assertFalse(candidate["safe_to_reduce"])
            self.assertEqual(candidate["candidate_runs"][0]["evidence_grade"], "same_day_and_cap_only")

    def test_scanning_is_restricted_to_supported_artifacts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "avito_spend.json"
            ledger.write_text('{"schema":1,"records":{}}', encoding="utf-8")
            for name in (".env", "bot.db", "unsafe.log"):
                (root / name).write_bytes(b"not an allowed artifact")
            for name in (".venv", "qa_parser_deps"):
                (root / name).mkdir()
                (root / name / "ignored.json").write_text("invalid", encoding="utf-8")
            (root / "receipt.json").write_text("[]", encoding="utf-8")
            result = audit(root, ledger)
            self.assertEqual(result["files_scanned"], 1)
            self.assertEqual(result["read_failures"], [])
            self.assertEqual(result["network_requests"], 0)


if __name__ == "__main__":
    unittest.main()
