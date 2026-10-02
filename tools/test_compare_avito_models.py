"""No paid calls: compare CLI isolation, shared spending and safe saved outputs."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.config import ServiceConfig
from avito_service.errors import ExternalServiceError
from avito_service.models import SearchRequest
from tools import compare_avito_models as cli
from tools.test_avito_model_costs import listing
from tools.test_avito_ai_transport import Stream, event


def answer(*, cost=0.1, estimated=False, model="PlayStation 5 Slim"):
    body = {"text_analyzed": True, "matches_request": True, "identified_model": model,
            "storage": "", "sim_variant": "", "condition": "Хорошее",
            "description_findings": [], "defects": [], "price_conditions": [], "conflicts": [],
            "verdict": "approve", "confidence": 0.95}
    usage = {} if cost is None else {"cost_rub": cost}
    if estimated:
        usage["cost_estimated"] = True
    return {"choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}], "usage": usage}


class CompareAvitoModelsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / "normalized.json"
        self.output = self.root / "summary.json"
        self.write_input()
        self.config = ServiceConfig(ai_api_key="secret-ai-token", apify_token="secret-apify-token",
                                    ai_base_url="https://api.aitunnel.ru/v1", ai_max_cost_rub=50)

    def write_input(self, count=1):
        self.source.write_text(json.dumps({"request": asdict(SearchRequest("PS5")),
            "listings": [asdict(replace(listing(), listing_id=str(1000001 + i))) for i in range(count)]}, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *extra):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cli.main(["--input", str(self.source), "--output", str(self.output),
                             "--model", "qwen3.8-flash", *extra])

    def test_offline_never_reads_config_or_calls_network_and_preserves_source(self):
        before = self.source.read_bytes()
        with patch.object(cli, "load_config", side_effect=AssertionError("must not read config")) as config, \
             patch("avito_service.ai.urlopen", side_effect=AssertionError("must not call network")) as network:
            self.assertEqual(self.run_cli("--model", "gpt-4.1-mini"), 0)
        config.assert_not_called()
        network.assert_not_called()
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "offline_plan")
        self.assertEqual(result["results"], [])
        self.assertEqual(len(result["reservations"]), 2)
        self.assertEqual(before, self.source.read_bytes())

    def test_pilot_array_uses_adjacent_request_and_full_saved_description(self):
        saved = asdict(listing())
        self.source.write_text(json.dumps([saved]), encoding="utf-8")
        self.source.with_name("request.json").write_text(json.dumps(asdict(SearchRequest("PS5"))), encoding="utf-8")
        request, listings = cli.read_input(self.source, 1)
        self.assertEqual(request.query, "PS5")
        self.assertEqual(listings[0].description, saved["description"])
        self.assertEqual(listings[0].parameters, saved["parameters"])
        self.assertEqual(listings[0].images, ())

    def test_excerpt_and_existing_output_fail_before_live_configuration(self):
        self.source.write_text(json.dumps({"request": {"query": "PS5"},
                                         "listings": [{"descriptionExcerpt": "only excerpt"}]}), encoding="utf-8")
        with patch.object(cli, "load_config") as config:
            self.assertEqual(self.run_cli("--live"), 2)
            config.assert_not_called()
        self.write_input()
        self.output.write_text("preserved", encoding="utf-8")
        with patch.object(cli, "load_config") as config:
            self.assertEqual(self.run_cli("--live"), 2)
            config.assert_not_called()
        self.assertEqual(self.output.read_text(), "preserved")

    def test_same_text_for_every_model_no_photos_and_receipts_are_separate(self):
        sent = []
        def transport(reviewer, payload):
            sent.append(payload)
            return answer(cost=0.12)
        with patch.object(cli, "load_config", return_value=self.config), \
             patch.object(OpenAICompatibleReviewer, "_request", new=transport), \
             patch.object(OpenAICompatibleReviewer, "review_photos", side_effect=AssertionError("no photo calls")):
            self.assertEqual(self.run_cli("--live", "--model", "gpt-4.1-mini", "--max-cost-rub", "30"), 0)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]["messages"], sent[1]["messages"])
        self.assertNotIn("image_url", json.dumps(sent))
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["costs"]["reportedRub"], 0.24)
        self.assertEqual(result["costs"]["reservedUnconfirmedRub"], 0)
        self.assertTrue(all(row["review"]["textAnalyzed"] for row in result["results"]))
        self.assertNotIn(self.config.ai_api_key, self.output.read_text(encoding="utf-8"))
        self.assertNotIn(listing().description, self.output.read_text(encoding="utf-8"))

    def test_shared_budget_stops_second_model_before_paid_call(self):
        estimator = OpenAICompatibleReviewer(self.config)
        reserve = estimator.estimate_payload_cost(estimator.build_text_payload(listing(), "qwen3.8-flash", SearchRequest("PS5")))
        with patch.object(cli, "load_config", return_value=self.config), \
             patch.object(OpenAICompatibleReviewer, "_request", return_value=answer(cost=None)) as transport:
            self.assertEqual(self.run_cli("--live", "--model", "gpt-4.1-mini", "--max-cost-rub", str(reserve + .05)), 1)
        self.assertEqual(transport.call_count, 1)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "budget_stopped")
        self.assertEqual(result["costs"]["reportedRub"], 0)
        self.assertGreater(result["costs"]["reservedUnconfirmedRub"], 0)

    def test_unknown_failure_keeps_reserve_and_never_saves_raw_error(self):
        self.write_input(2)
        error = ExternalServiceError("do not log secret-ai-token and seller phone", code="AI_TIMEOUT")
        with patch.object(cli, "load_config", return_value=self.config), \
             patch.object(OpenAICompatibleReviewer, "_request", side_effect=error) as transport:
            self.assertEqual(self.run_cli("--live", "--max-cost-rub", ".8"), 1)
        self.assertEqual(transport.call_count, 1)
        text = self.output.read_text(encoding="utf-8")
        result = json.loads(text)
        self.assertGreater(result["costs"]["reservedUnconfirmedRub"], 0)
        self.assertNotIn("secret-ai-token", text)
        self.assertNotIn("seller phone", text)

    def test_auth_or_quota_skips_remaining_listings_only_for_failed_model(self):
        self.write_input(2)
        for code in ("AI_AUTH", "AI_QUOTA"):
            with self.subTest(code=code):
                self.output = self.root / f"stopped-{code}.json"
                sent = []
                def transport(reviewer, payload):
                    sent.append(payload["model"])
                    if payload["model"] == "gpt-5.6-sol":
                        raise ExternalServiceError("secret-ai-token", code=code)
                    return answer(cost=.05)
                with patch.object(cli, "load_config", return_value=self.config), \
                     patch.object(OpenAICompatibleReviewer, "_request", new=transport):
                    self.assertEqual(self.run_cli("--live", "--model", "gpt-5.6-sol",
                        "--max-cost-rub", "50", "--max-output-tokens", "800"), 1)
                self.assertEqual(sent, ["qwen3.8-flash", "gpt-5.6-sol", "qwen3.8-flash"])
                result = json.loads(self.output.read_text(encoding="utf-8"))
                self.assertEqual(result["status"], "completed_with_errors")
                first_error, skipped = result["results"][1], result["results"][3]
                self.assertEqual(first_error["status"], "error")
                self.assertGreater(first_error["reservedUnconfirmedRub"], 0)
                self.assertEqual(skipped["status"], "skipped")
                self.assertEqual(skipped["errorCode"], code)
                self.assertEqual(skipped["skippedAfterListingId"], first_error["listingId"])
                for field in ("requestCalls", "accountedCostRub", "reportedCostRub", "reservedUnconfirmedRub"):
                    self.assertEqual(skipped[field], 0)
                self.assertNotIn("review", skipped)
                self.assertEqual(result["costs"]["reportedRub"], .1)
                self.assertEqual(result["costs"]["reservedUnconfirmedRub"], first_error["reservedUnconfirmedRub"])
                self.assertEqual(result["costs"]["requestCalls"], 3)
                self.assertNotIn("secret-ai-token", self.output.read_text(encoding="utf-8"))

    def test_transient_failure_does_not_disable_the_model_for_next_listing(self):
        self.write_input(2)
        error = ExternalServiceError("temporarily unavailable", code="AI_TIMEOUT")
        with patch.object(cli, "load_config", return_value=self.config), \
             patch.object(OpenAICompatibleReviewer, "_request", side_effect=[error, answer(cost=.05)]) as transport:
            self.assertEqual(self.run_cli("--live", "--max-cost-rub", "10"), 1)
        self.assertEqual(transport.call_count, 2)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual([row["status"] for row in result["results"]], ["error", "completed"])
        self.assertEqual(result["costs"]["reportedRub"], .05)
        self.assertGreater(result["costs"]["reservedUnconfirmedRub"], 0)

    def test_actual_charge_above_reserve_is_recorded_and_halts_following_calls(self):
        self.write_input(2)
        with patch.object(cli, "load_config", return_value=self.config), \
             patch.object(OpenAICompatibleReviewer, "_request", return_value=answer(cost=99)) as transport:
            self.assertEqual(self.run_cli("--live", "--max-cost-rub", "10"), 1)
        self.assertEqual(transport.call_count, 1)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["costs"]["reportedRub"], 99)
        self.assertTrue(result["costs"]["overBudget"])

    def test_compatibility_retry_reserves_again_from_the_shared_budget(self):
        malformed = {"choices": [{"message": {"content": "{broken"}}], "usage": {"cost_rub": 0.1}}
        with patch.object(cli, "load_config", return_value=self.config), \
             patch.object(OpenAICompatibleReviewer, "_request", side_effect=[malformed, answer(cost=0.2)]) as transport:
            self.assertEqual(self.run_cli("--live", "--max-cost-rub", "10"), 0)
        self.assertEqual(transport.call_count, 2)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["costs"]["reportedRub"], .3)
        self.assertEqual(result["costs"]["requestCalls"], 2)

    def test_unknown_model_and_nonfinite_budget_fail_without_config_or_network(self):
        for flags in (("--model", "invented-model"), ("--max-cost-rub", "nan"), ("--max-cost-rub", "inf")):
            with self.subTest(flags=flags), patch.object(cli, "load_config") as config, \
                 patch("avito_service.ai.urlopen") as network:
                with self.assertRaises(SystemExit):
                    self.run_cli("--live", *flags)
                config.assert_not_called()
                network.assert_not_called()

    def test_non_aitunnel_endpoint_rejected_without_ai_call(self):
        with patch.object(cli, "load_config", return_value=replace(self.config, ai_base_url="https://other.example/v1")), \
             patch.object(OpenAICompatibleReviewer, "_request") as transport:
            self.assertEqual(self.run_cli("--live"), 2)
        transport.assert_not_called()

    def test_strong_model_output_cap_is_only_applied_to_comparison(self):
        with patch.object(cli, "load_config", return_value=self.config), \
             patch.object(OpenAICompatibleReviewer, "_request", return_value=answer()) as transport:
            self.assertEqual(self.run_cli("--live", "--model", "gpt-5.6-sol", "--max-cost-rub", "50", "--max-output-tokens", "800"), 0)
        self.assertEqual(transport.call_count, 2)
        self.assertTrue(all(call.args[0]["max_tokens"] == 800 for call in transport.call_args_list))
        normal = OpenAICompatibleReviewer.build_text_payload(listing(), "gpt-5.6-sol", SearchRequest("PS5"))
        self.assertEqual(normal["max_tokens"], 2400)

    def test_length_termination_never_approves_even_if_the_json_looks_complete(self):
        full_json = answer()["choices"][0]["message"]["content"]
        stream = Stream([event(full_json, "length", usage={"cost_rub": .1})])
        with patch.object(cli, "load_config", return_value=self.config), \
             patch("avito_service.ai.urlopen", return_value=stream) as transport:
            self.assertEqual(self.run_cli("--live", "--max-output-tokens", "800"), 1)
        self.assertEqual(transport.call_count, 1)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["results"][0]["errorCode"], "AI_INVALID_RESPONSE")
        self.assertNotIn("review", result["results"][0])
        self.assertGreater(result["costs"]["reservedUnconfirmedRub"], 0)

    def test_cli_does_not_repeat_http429_but_production_retry_stays_enabled(self):
        with patch.object(cli, "load_config", return_value=self.config), \
             patch("avito_service.ai.urlopen", side_effect=HTTPError("https://api.aitunnel.ru", 429, "limited", {}, None)) as transport:
            self.assertEqual(self.run_cli("--live"), 1)
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(OpenAICompatibleReviewer.retry_rate_limits)

    def test_extracted_fields_redact_configured_secrets(self):
        with patch.object(cli, "load_config", return_value=self.config), \
             patch.object(OpenAICompatibleReviewer, "_request", return_value=answer(model="secret-ai-token")):
            self.run_cli("--live")
        self.assertNotIn("secret-ai-token", self.output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
