"""Strict output contracts never force positive findings or weaken retries."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.config import ServiceConfig
from avito_service.errors import ExternalServiceError
from avito_service.models import ReviewVerdict
from tools.test_avito_model_costs import listing
from tools.test_avito_text_retry import incomplete, response, valid


class StructuredOutputTests(unittest.TestCase):
    def setUp(self):
        self.item = listing()
        self.items = (self.item, replace(self.item, listing_id="1000002"))
        self.text = OpenAICompatibleReviewer.parse_text_review(self.item, json.dumps(valid(self.item)))

    def payloads(self, model):
        return (
            OpenAICompatibleReviewer.build_text_payload(self.item, model),
            OpenAICompatibleReviewer.build_text_batch_payload(self.items, model),
            OpenAICompatibleReviewer.build_photo_payload(self.item, model, self.text),
        )

    def assert_closed(self, schema):
        if schema["type"] == "object":
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
            self.assertIs(schema["additionalProperties"], False)
            for value in schema["properties"].values():
                self.assert_closed(value)
        elif schema["type"] == "array":
            self.assert_closed(schema["items"])

    def test_every_stage_has_closed_complete_schema_with_negative_booleans_allowed(self):
        for model in ("gpt-4.1-mini", "openai/gpt-4.1-mini", "gpt-4.1", "openai/gpt-4.1"):
            for stage, payload in enumerate(self.payloads(model)):
                with self.subTest(model=model, stage=stage):
                    fmt = payload["response_format"]
                    self.assertEqual(fmt["type"], "json_schema")
                    self.assertIs(fmt["json_schema"]["strict"], True)
                    schema = fmt["json_schema"]["schema"]
                    self.assert_closed(schema)
                    fields = schema["properties"]
                    if stage == 1:
                        fields = fields["reviews"]["items"]["properties"]
                        self.assertEqual(fields["listing_id"], {"type": "string"})
                    expected = set(OpenAICompatibleReviewer._text_output_contract())
                    if stage == 1:
                        expected.add("listing_id")
                    if stage == 2:
                        expected.update((
                            "photos_analyzed", "photo_coverage", "photo_findings",
                            "photo_condition_evidence", "photo_completeness_evidence",
                        ))
                        self.assertEqual(fields["photo_coverage"], {"type": "array", "items": {"type": "integer"}})
                        self.assertEqual(fields["photos_analyzed"], {"type": "boolean"})
                    self.assertEqual(set(fields), expected)
                    self.assertEqual(fields["text_analyzed"], {"type": "boolean"})
                    self.assertEqual(fields["matches_request"], {"type": "boolean"})
                    self.assertEqual(fields["verdict"]["enum"], ["approve", "caution", "reject"])

    def test_other_models_keep_existing_json_object_contract(self):
        for model in ("qwen3.8-flash", "qwen/qwen3.8-flash", "gemini-2.5-flash", "gpt-5.6-sol", "custom-model"):
            for payload in self.payloads(model):
                self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_malformed_json_retry_keeps_strict_schema_in_every_stage(self):
        bad = {"choices": [{"message": {"content": "{invalid"}}], "usage": {"cost_rub": .2}}
        for stage in ("text", "batch", "photo"):
            with self.subTest(stage=stage):
                reviewer = OpenAICompatibleReviewer(ServiceConfig(ai_model="gpt-4.1-mini"))
                sent = []
                def transport(payload):
                    sent.append(deepcopy(payload))
                    return bad
                with patch.object(reviewer, "_request", side_effect=transport):
                    try:
                        if stage == "text":
                            reviewer.review_text(self.item)
                        elif stage == "batch":
                            reviewer.review_text_batch(self.items)
                        else:
                            reviewer.review_photos(self.item, self.text)
                    except ExternalServiceError:
                        pass
                self.assertEqual(len(sent), 6 if stage == "batch" else 2)
                self.assertTrue(all(
                    payload["response_format"] == sent[0]["response_format"]
                    for payload in sent
                ))
                self.assertEqual(sent[-1]["response_format"]["type"], "json_schema")

    def test_strict_schema_does_not_promote_incomplete_or_negative_answers(self):
        reviewer = OpenAICompatibleReviewer(ServiceConfig(ai_model="gpt-4.1-mini"))
        for body in (incomplete(self.item), valid(self.item, text_analyzed=False, matches_request=False)):
            with patch.object(reviewer, "_request", side_effect=[response(body), response(body, .3)]):
                result = reviewer.review_text(self.item)
            self.assertFalse(result.text_analyzed)
            self.assertTrue(result.error)
            self.assertEqual(result.verdict, ReviewVerdict.CAUTION)
            self.assertAlmostEqual(result.cost_rub, .5)
        negative = valid(self.item, matches_request=False, verdict="reject", mismatch_reason="PS4, а не PS5")
        with patch.object(reviewer, "_request", return_value=response(negative)) as request:
            result = reviewer.review_text(self.item)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(result.matches_request)
        self.assertEqual(result.verdict, ReviewVerdict.REJECT)
        photo = {**negative, "photos_analyzed": False, "photo_coverage": [], "photo_findings": []}
        parsed = reviewer.parse_review(self.item, json.dumps(photo))
        self.assertFalse(parsed.photos_analyzed)
        self.assertFalse(parsed.matches_request)
        self.assertTrue(parsed.error)


if __name__ == "__main__":
    unittest.main()
