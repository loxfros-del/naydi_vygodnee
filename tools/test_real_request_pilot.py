from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.pilot_sample import (
    PILOT_SCHEMA,
    PilotManifestError,
    REQUIRED_CATEGORIES,
    aggregate_real_request_pilot,
)
from tools.report_real_request_pilot import main as report_main


def _case(participant_ref: str, category: str, *, channel: str = "telegram_direct") -> dict:
    return {
        "participant_ref": participant_ref,
        "category": category,
        "channel": channel,
        "consent_confirmed": True,
        "distinct_participant_confirmed": True,
        "real_request_confirmed": True,
        "automated": {
            "top1_exact": True,
            "top1_verified": True,
            "recommendation_created": True,
            "unsafe_candidate_count": 0,
            "system_error": False,
            "duration_ms": 1200,
        },
        "manual_review": {
            "model_matches": True,
            "link_opens": True,
            "price_matches": True,
            "availability_matches": True,
            "condition_clear": True,
            "false_savings_claim": False,
        },
    }


def _complete_manifest() -> dict:
    cases = []
    index = 1
    for category in REQUIRED_CATEGORIES:
        for _ in range(5):
            cases.append(_case(f"P{index:02d}", category))
            index += 1
    return {
        "schema": PILOT_SCHEMA,
        "pilot_name": "test-pilot",
        "channel_funnel": {
            "telegram_direct": {"invitations": 40, "starts": 30},
            "telegram_channel": {"invitations": 0, "starts": 0},
            "website": {"invitations": 0, "starts": 0},
            "referral": {"invitations": 0, "starts": 0},
            "offline": {"invitations": 0, "starts": 0},
        },
        "cases": cases,
    }


class RealRequestPilotTests(unittest.TestCase):
    def test_balanced_complete_sample_is_ready(self) -> None:
        report = aggregate_real_request_pilot(_complete_manifest())
        self.assertEqual(report["cases"], 30)
        self.assertEqual(report["completed_cases"], 30)
        self.assertTrue(report["quotas_met"])
        self.assertTrue(report["sample_complete"])
        self.assertTrue(report["assisted_flow_ready"])
        self.assertTrue(report["rollout_ready"])
        self.assertTrue(all(value == 5 for value in report["category_counts"].values()))
        self.assertEqual(report["channel_funnel"]["telegram_direct"]["start_rate_percent"], 75.0)
        self.assertEqual(report["channel_funnel"]["telegram_direct"]["qualified_rate_percent"], 75.0)

    def test_incomplete_template_is_truthful(self) -> None:
        manifest = _complete_manifest()
        for case in manifest["cases"]:
            case["channel"] = None
            case["consent_confirmed"] = False
            case["distinct_participant_confirmed"] = False
            case["real_request_confirmed"] = False
            case["automated"] = {key: None for key in case["automated"]}
            case["manual_review"] = {key: None for key in case["manual_review"]}
        report = aggregate_real_request_pilot(manifest)
        self.assertEqual(report["completed_cases"], 0)
        self.assertEqual(report["incomplete_cases"], 30)
        self.assertFalse(report["sample_complete"])
        self.assertFalse(report["rollout_ready"])

    def test_assisted_flow_and_automatic_rollout_are_separate(self) -> None:
        manifest = _complete_manifest()
        for case in manifest["cases"]:
            case["automated"]["top1_verified"] = False
        report = aggregate_real_request_pilot(manifest)
        self.assertTrue(report["sample_complete"])
        self.assertTrue(report["assisted_flow_ready"])
        self.assertFalse(report["rollout_ready"])
        self.assertEqual(report["automated_failure_counts"]["top1_verified"], 30)

    def test_one_manual_or_safety_failure_blocks_readiness(self) -> None:
        manual = _complete_manifest()
        manual["cases"][0]["manual_review"]["price_matches"] = False
        report = aggregate_real_request_pilot(manual)
        self.assertTrue(report["sample_complete"])
        self.assertFalse(report["assisted_flow_ready"])
        self.assertEqual(report["manual_failure_counts"]["price_matches"], 1)

        unsafe = _complete_manifest()
        unsafe["cases"][0]["automated"]["unsafe_candidate_count"] = 1
        self.assertFalse(aggregate_real_request_pilot(unsafe)["assisted_flow_ready"])

    def test_duplicate_and_prohibited_fields_are_rejected(self) -> None:
        duplicate = _complete_manifest()
        duplicate["cases"][1]["participant_ref"] = duplicate["cases"][0]["participant_ref"]
        with self.assertRaises(PilotManifestError):
            aggregate_real_request_pilot(duplicate)

        personal = _complete_manifest()
        personal["cases"][0]["telegram_id"] = 123
        with self.assertRaisesRegex(PilotManifestError, "prohibited"):
            aggregate_real_request_pilot(personal)

    def test_invalid_types_are_rejected(self) -> None:
        manifest = _complete_manifest()
        manifest["cases"][0]["automated"]["top1_exact"] = "yes"
        with self.assertRaises(PilotManifestError):
            aggregate_real_request_pilot(manifest)

        impossible_funnel = _complete_manifest()
        impossible_funnel["channel_funnel"]["telegram_direct"]["starts"] = 29
        with self.assertRaises(PilotManifestError):
            aggregate_real_request_pilot(impossible_funnel)

    def test_report_contains_no_row_identifiers_or_search_data(self) -> None:
        manifest = _complete_manifest()
        report_text = json.dumps(aggregate_real_request_pilot(manifest), ensure_ascii=False)
        self.assertNotIn("P01", report_text)
        for forbidden in ("participant_ref", "request_id", "title", "url", "query"):
            self.assertNotIn(forbidden, report_text)

    def test_cli_outputs_report_and_rejects_bad_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.json"
            bad = Path(tmp) / "bad.json"
            good.write_text(json.dumps(_complete_manifest()), encoding="utf-8")
            bad.write_text("{bad", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(report_main([str(good)]), 0)
            self.assertTrue(json.loads(output.getvalue())["sample_complete"])
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(report_main([str(bad)]), 2)
            self.assertIn("Invalid pilot manifest", error.getvalue())


if __name__ == "__main__":
    unittest.main()
