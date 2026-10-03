"""Offline regressions for paid-pilot artifacts when Windows stdout is limited."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from avito_service.config import ServiceConfig
from avito_service.models import AnalysisReport, CostSummary, PipelineAudit
from tools import run_avito_pilot


class PilotOutputTests(unittest.TestCase):
    def test_json_write_flushes_before_atomic_replace_and_keeps_old_on_failure(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "reserved.json"
            run_avito_pilot.write_json(path, {"reservation_rub": 5})
            with patch.object(run_avito_pilot.os, "fsync", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    run_avito_pilot.write_json(path, {"reservation_rub": 10})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"reservation_rub": 5})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            run_avito_pilot.write_json(path, {"reservation_rub": 10})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"reservation_rub": 10})

    def _run_offline(self, output_stream):
        config = ServiceConfig(
            apify_token="fixture", ai_api_key="fixture", ai_base_url="https://fixture.invalid",
            ai_model="fixture", usd_rub_rate=100,
        )
        report = AnalysisReport(
            query="PS5", location="Ярославль", collected_count=36, analyzed_count=1,
            outcome="SEARCH_INCOMPLETE", empty_reason="Фото → страница: проверка не завершена",
            admin_warnings=("Статус → ожидается 📷",),
            costs=CostSummary(apify_cost_usd=0.8, ai_cost_rub=3, estimated_total_rub=83),
            pipeline=PipelineAudit(
                raw_collected_count=199, deduplicated_count=145, collected_count=36,
                deterministic_eligible_count=25, text_completed_count=25,
                photo_attempted_count=4, photo_completed_count=1,
            ),
        )
        calls = []

        def search(request, *, deadline_seconds, progress):
            calls.append(request.query)
            progress("photo", "Проверка → 📷", 75)
            return report

        service = SimpleNamespace(
            provider=SimpleNamespace(config=config, spending_guard=None), search=search,
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(run_avito_pilot, "ROOT", root),
                patch.object(run_avito_pilot, "load_config", return_value=config),
                patch.object(run_avito_pilot, "build_service", return_value=service),
                patch.object(run_avito_pilot, "PilotProvider"),
                patch.object(run_avito_pilot, "closest_failures", return_value=[{
                    "id": "8125174775", "reasons": ["Фото → 📷: не проверено"],
                }]),
                redirect_stdout(output_stream),
            ):
                run_avito_pilot.main([
                    "--live", "--query", "PS5", "--location", "Ярославль",
                    "--category", "gaming", "--condition", "", "--pickup-only",
                ])
            output = next((root / "runtime" / "avito_pilot").iterdir())
            saved_report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            saved_result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            trace_path = next((root / "runtime" / "avito_pilot_traces").glob("*.json"))
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual(calls, ["PS5"])
            self.assertFalse((output / "failure.json").exists())
            self.assertEqual(saved_report["outcome"], "SEARCH_INCOMPLETE")
            self.assertEqual(saved_report["emptyReason"], report.empty_reason)
            self.assertEqual(saved_result["visible"], 0)
            self.assertEqual(saved_result["costs"]["aiCostRub"], 3)
            self.assertEqual(saved_result["warnings"], list(report.admin_warnings))
            self.assertEqual(trace["status"], "empty")
            self.assertEqual(trace["result"]["outcome"], "SEARCH_INCOMPLETE")
            self.assertEqual(trace["result"]["final_results"], 0)
            self.assertEqual(trace["collection"]["unique_listing_ids"], 145)
            self.assertEqual(trace["verification"]["photo_completed"], 1)
            self.assertEqual(trace["cost"]["ai_rub"], 3)

    def test_windows_legacy_encoding_preserves_report_trace_and_unicode_files(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1251", errors="strict")
        self._run_offline(stream)
        output = buffer.getvalue().decode("cp1251")
        self.assertIn("PILOT_RESULT", output)
        self.assertIn("\\u2192", output)
        self.assertIn("\\U0001f4f7", output)

    def test_disconnected_console_does_not_lose_result_or_repeat_search(self):
        class DisconnectedConsole:
            encoding = "utf-8"

            def write(self, value):
                raise BrokenPipeError("Console disconnected")

            def flush(self):
                pass

        self._run_offline(DisconnectedConsole())


if __name__ == "__main__":
    unittest.main()
