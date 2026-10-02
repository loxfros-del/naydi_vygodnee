"""Offline regressions for model-specific reserves and unspecified PS5 variants."""
from __future__ import annotations

from dataclasses import replace
import math
import unittest

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.config import ServiceConfig
from avito_service.errors import ConfigurationError
from avito_service.models import AIReview, SearchRequest
from avito_service.normalization import normalize_listing


def listing():
    return normalize_listing({"id": "1000001", "title": "PlayStation 5 Slim",
        "url": "https://www.avito.ru/yaroslavl/igry/ps5_1000001", "price": 35000,
        "description": "Продаю PS5 Slim с приводом. Хорошее состояние. Без ремонта.",
        "parameters": {"Модель": "PlayStation 5 Slim", "Состояние": "Хорошее"},
        "images": ["https://10.img.avito.st/photo1.jpg"]})


class AvitoModelCostsTests(unittest.TestCase):
    def setUp(self):
        self.reviewer = OpenAICompatibleReviewer(ServiceConfig(ai_max_cost_rub=50))

    def payload(self, model):
        return self.reviewer.build_text_payload(listing(), model, SearchRequest("PS5"))

    def test_known_models_without_receipt_are_not_charged_the_whole_budget(self):
        costs = []
        for model in ("qwen3.8-flash", "gpt-4.1-mini", "gemini-2.5-flash"):
            with self.subTest(model=model):
                response = {"usage": {}}
                self.reviewer._ensure_stream_cost(response, self.payload(model))
                usage = response["usage"]
                self.assertTrue(usage["cost_estimated"])
                self.assertGreater(usage["cost_rub"], 0)
                self.assertLess(usage["cost_rub"], 50)
                costs.append(usage["cost_rub"])
        self.assertLess(costs[0], costs[1])
        self.assertLess(costs[1], costs[2])

    def test_provider_aliases_have_same_budget_as_their_canonical_ids(self):
        for alias, model in (("qwen/qwen3.8-flash", "qwen3.8-flash"),
                             ("openai/gpt-4.1-mini", "gpt-4.1-mini"),
                             ("google/gemini-2.5-flash", "gemini-2.5-flash"),
                             ("openai/gpt-5.6-sol", "gpt-5.6-sol")):
            self.assertEqual(self.reviewer.estimate_payload_cost(self.payload(alias)),
                             self.reviewer.estimate_payload_cost(self.payload(model)))

    def test_actual_receipt_including_zero_is_never_replaced_by_estimate(self):
        for price in (0, 0.015, 71.32):
            response = {"usage": {"cost_rub": price, "prompt_tokens": 321}}
            self.reviewer._ensure_stream_cost(response, self.payload("gemini-2.5-flash"))
            self.assertEqual(response["usage"], {"cost_rub": price, "prompt_tokens": 321})

    def test_invalid_receipts_cannot_make_the_review_free(self):
        for value in (True, False, "NaN", math.inf, -1, None, "free"):
            response = {"usage": {"cost_rub": value}}
            self.reviewer._ensure_stream_cost(response, self.payload("gpt-4.1-mini"))
            self.assertGreater(response["usage"]["cost_rub"], 0)
            self.assertTrue(response["usage"]["cost_estimated"])

    def test_confirmed_token_counts_replace_worst_case_photo_reserve_but_not_receipt(self):
        payload = self.reviewer.build_photo_payload(replace(listing(), images=listing().images * 5),
                    "qwen3.8-flash", AIReview(listing().listing_id, True, False))
        response = {"usage": {"prompt_tokens": 10_000, "completion_tokens": 1000}}
        self.reviewer._ensure_stream_cost(response, payload)
        self.assertEqual(response["usage"]["cost_rub"], .5)
        self.assertTrue(response["usage"]["cost_estimated"])
        self.assertEqual(response["usage"]["cost_estimate_basis"], "reported_tokens")
        self.assertLess(.5, self.reviewer.estimate_payload_cost(payload))

    def test_reasoning_is_already_in_completion_tokens_and_not_counted_twice(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 2000,
                 "completion_tokens_details": {"reasoning_tokens": 1500}}
        response = {"usage": usage}
        self.reviewer._ensure_stream_cost(response, self.payload("gpt-5.6-sol"))
        self.assertEqual(usage["cost_rub"], 30.3)
        self.assertTrue(usage["cost_estimated"])

    def test_incomplete_or_invalid_token_counts_keep_payload_reserve(self):
        payload = self.payload("qwen3.8-flash")
        for usage in ({"prompt_tokens": 100}, {"completion_tokens": 100},
                      {"prompt_tokens": True, "completion_tokens": 10},
                      {"prompt_tokens": 100, "completion_tokens": math.nan},
                      {"prompt_tokens": -1, "completion_tokens": 100},
                      {"prompt_tokens": 0, "completion_tokens": 0},
                      {"prompt_tokens": 100, "completion_tokens": 10,
                       "completion_tokens_details": {"reasoning_tokens": 11}}):
            self.reviewer._ensure_stream_cost({"usage": usage}, payload)
            self.assertEqual(usage["cost_rub"], self.reviewer.estimate_payload_cost(payload))
            self.assertNotIn("cost_estimate_basis", usage)

    def test_unknown_tariff_keeps_full_budget_and_similar_name_does_not_match(self):
        for model in ("gpt-4.1-mini-new", "fake/qwen3.8-flash", "unknown", ""):
            self.assertEqual(self.reviewer.estimate_payload_cost(self.payload(model)), 50)

    def test_invalid_unknown_model_budget_is_rejected_instead_of_zero(self):
        for budget in (0, -1, math.nan, math.inf):
            reviewer = OpenAICompatibleReviewer(ServiceConfig(ai_max_cost_rub=budget))
            with self.assertRaises(ConfigurationError):
                reviewer.estimate_payload_cost(self.payload("unknown"))

    def test_multiple_photos_and_output_cap_increase_budget(self):
        original = listing()
        review = AIReview(original.listing_id, True, False)
        for model in ("qwen3.8-flash", "gpt-4.1-mini", "gemini-2.5-flash"):
            one = self.reviewer.build_photo_payload(original, model, review)
            more = self.reviewer.build_photo_payload(replace(original, images=original.images * 3), model, review)
            self.assertGreater(self.reviewer.estimate_payload_cost(more), self.reviewer.estimate_payload_cost(one))
            large = {**one, "max_tokens": 8000}
            self.assertGreater(self.reviewer.estimate_payload_cost(large), self.reviewer.estimate_payload_cost(one))
            self.assertEqual(one["messages"][1]["content"][-1]["image_url"]["detail"], "high")

    def test_bare_ps5_rule_and_explicit_version_reach_both_text_stages(self):
        for request in (SearchRequest("PS5"), SearchRequest("PS5 Slim", attributes=(("Привод", "Есть"),))):
            for payload in (self.reviewer.build_text_payload(listing(), "qwen3.8-flash", request),
                            self.reviewer.build_text_batch_payload((listing(),), "qwen3.8-flash", request)):
                prompt = payload["messages"][0]["content"]
                self.assertIn("Неуказанная модификация не обязательна", prompt)
                self.assertIn("извлеки фактическую версию", prompt)
                self.assertIn("но никогда PS4", prompt)
                self.assertIn(request.query, payload["messages"][1]["content"])
                if request.attributes:
                    self.assertIn('"Привод": "Есть"', payload["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
