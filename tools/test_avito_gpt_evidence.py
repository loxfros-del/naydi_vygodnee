"""Real migration response variants must preserve comparability and disclosure."""
from dataclasses import replace
import unittest

from avito_service.matching import canonical_evidence, matches_listing_request, storage_with_source_unit
from avito_service.models import AIReview, AnalyzedListing, SearchRequest
from tools.test_avito_kit_evidence import listing


class GPTEvidenceTests(unittest.TestCase):
    def item(self, condition="Отличное", sim=""):
        source = listing("Полный комплект. Состояние отличное.")
        review = AIReview(source.listing_id, True, False, condition=condition, sim_variant=sim)
        return AnalyzedListing(source, (), review)

    def test_english_used_grade_has_same_market_lane_and_unknown_history_warning(self):
        for english, russian in (("Excellent", "Отличное"), ("Good", "Хорошее")):
            left, right = self.item(english), self.item(russian)
            self.assertEqual(left.market_lane(), "used")
            self.assertEqual(left.comparable_key(), right.comparable_key())
            self.assertEqual(left.history_warnings(), right.history_warnings())
            self.assertTrue(left.history_warnings())
        self.assertNotEqual(self.item("Good").comparable_key(), self.item("Excellent").comparable_key())

    def test_new_is_not_the_used_market(self):
        self.assertEqual(self.item("New").market_lane(), self.item("Новое").market_lane())
        self.assertNotEqual(self.item("New").market_lane(), self.item("Excellent").market_lane())

    def test_non_applicable_sim_labels_do_not_split_console_peers(self):
        for label in ("N/A", "none", "not applicable", "не применимо", "нет", ""):
            with self.subTest(label=label):
                self.assertEqual(canonical_evidence("sim", label), "")
                self.assertEqual(self.item(sim=label).comparable_key(), self.item(sim="").comparable_key())
        self.assertNotEqual(canonical_evidence("sim", "none"), canonical_evidence("sim", "SIM + eSIM"))

    def test_bare_storage_number_uses_only_matching_explicit_source_unit(self):
        item = self.item()
        source = replace(item.listing, parameters={"Модель": "PlayStation 5 Slim", "Встроенная память, ГБ": "1000"})
        self.assertEqual(storage_with_source_unit(source, "1000"), "1000 gb")
        self.assertEqual(storage_with_source_unit(source, "825"), "825")
        self.assertEqual(storage_with_source_unit(source, "1"), "1")
        self.assertEqual(storage_with_source_unit(replace(source, parameters={}), "1000"), "1000")
        no_location = SearchRequest("PS5", required_storage="1 TB")
        self.assertTrue(matches_listing_request(source, no_location, storage="1000", final=True))
        self.assertFalse(matches_listing_request(source, no_location, storage="825", final=True))
        left = replace(item, listing=source, ai_review=replace(item.ai_review, storage="1000"))
        right = replace(item, listing=source, ai_review=replace(item.ai_review, storage="1 TB"))
        self.assertEqual(left.comparable_key(), right.comparable_key())


if __name__ == "__main__":
    unittest.main()
