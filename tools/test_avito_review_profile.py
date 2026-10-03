"""Owner-selected model is persistent, explicit environment overrides stay usable."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from avito_service.config import ServiceConfig, expanded_review_config, load_config


class ReviewProfileTests(unittest.TestCase):
    def config(self, local=None, env=None, profile=None):
        local = local or {"AVITO_AI_BASE_URL": "https://api.aitunnel.ru/v1",
                          "AVITO_AI_MODEL": "qwen3.8-flash", "AVITO_AI_API_KEY": "test-key"}
        with patch("avito_service.config._read_local_env", return_value=local), patch.dict("os.environ", env or {}, clear=True):
            if profile is None:
                return load_config()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.json"
                path.write_text(json.dumps(profile), encoding="utf-8")
                with patch("avito_service.config.REVIEW_PROFILE_PATH", path):
                    return load_config()

    def test_owner_profile_replaces_stale_model_and_expands_analysis(self):
        config = self.config()
        self.assertEqual(config.ai_model, "gpt-4.1-mini")
        self.assertEqual(config.ai_proxy_mode, "direct")
        self.assertEqual(config.ai_api_key, "test-key")
        self.assertEqual(config.ai_text_batch_size, 5)
        self.assertEqual(config.ai_text_max_listings, 200)
        self.assertEqual(config.apify_max_charge_usd, 2)
        self.assertEqual(config.ai_max_cost_rub, 50)

    def test_process_overrides_profile_including_lower_budget(self):
        config = self.config(env={"AVITO_AI_MODEL": "chosen-model", "AVITO_APIFY_MAX_CHARGE_USD": "0.5",
                                  "AVITO_AI_MAX_COST_RUB": "10", "AVITO_REPORT_MAX_COST_RUB": "25"})
        self.assertEqual(config.ai_model, "chosen-model")
        self.assertEqual(config.apify_max_charge_usd, .5)
        self.assertEqual(config.ai_max_cost_rub, 10)
        self.assertEqual(expanded_review_config(config).ai_max_cost_rub, 10)
        from avito_service.http_api import build_service
        current = build_service(expanded_review_config(config))
        self.assertEqual(current.report_max_cost_rub, 25)
        self.assertEqual(current.provider.config.report_max_cost_rub, 25)

    def test_other_provider_is_not_given_an_aitunnel_model(self):
        config = self.config(env={"AVITO_AI_BASE_URL": "https://example.org/v1"})
        self.assertEqual(config.ai_model, "qwen3.8-flash")
        self.assertEqual(config.apify_max_charge_usd, 1)

    def test_profile_can_be_disabled_without_editing_secret_file(self):
        self.assertEqual(self.config(env={"AVITO_REVIEW_PROFILE": "off"}).ai_model, "qwen3.8-flash")

    def test_proxy_mode_is_scoped_to_profile_and_can_be_overridden(self):
        self.assertEqual(ServiceConfig().ai_proxy_mode, "system")
        self.assertEqual(self.config(env={"AVITO_AI_BASE_URL": "https://example.org/v1"}).ai_proxy_mode, "system")
        self.assertEqual(self.config(env={"AVITO_REVIEW_PROFILE": "off"}).ai_proxy_mode, "system")
        self.assertEqual(self.config(env={"AVITO_AI_PROXY_MODE": "system"}).ai_proxy_mode, "system")
        self.assertEqual(self.config(env={"AVITO_AI_PROXY_MODE": " DIRECT "}).ai_proxy_mode, "direct")

    def test_invalid_proxy_mode_fails_without_silent_route_change(self):
        for value in ("", "fallback", "disabled", "http://127.0.0.1:10809"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config(env={"AVITO_AI_PROXY_MODE": value})
        with self.assertRaises(ValueError):
            self.config(profile={"schema": 1, "provider": "aitunnel",
                                 "settings": {"AVITO_AI_PROXY_MODE": "fallback"}})

    def test_minimum_bargain_thresholds_are_configurable(self):
        config = self.config(env={
            "AVITO_MINIMUM_BARGAIN_PERCENT": "10.5",
            "AVITO_MINIMUM_BARGAIN_RUB": "5000",
        })
        self.assertEqual(config.minimum_bargain_percent, 10.5)
        self.assertEqual(config.minimum_bargain_rub, 5_000)

    def test_profile_cannot_redirect_credentials_or_change_keys(self):
        for name in ("AVITO_AI_API_KEY", "AVITO_AI_BASE_URL", "APIFY_TOKEN", "APIFY_API_URL"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.config(profile={"schema": 1, "provider": "aitunnel", "settings": {name: "bad"}})

    def test_malformed_profile_cannot_silently_restore_old_model(self):
        with self.assertRaises(ValueError):
            self.config(profile={"schema": 1, "provider": "aitunnel", "settings": {"AVITO_AI_MODEL": 7}})


if __name__ == "__main__":
    unittest.main()
