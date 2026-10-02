"""Offline regressions for nominal capacities in PS5 text/photo evidence."""
from dataclasses import replace
import unittest

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.matching import canonical_evidence, evidence_conflicts, matches_listing_request
from avito_service.models import AIReview, AnalyzedListing, ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_listing


def ps5():
    listing = normalize_listing({
        "id": "12345678", "title": "Sony PlayStation 5 Slim", "price": 48_000,
        "url": "https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/ps5_12345678",
        "description": "Полный комплект.", "status": "active", "currency": "RUB",
        "parameters": {"Модель": "PlayStation 5 Slim", "Состояние": "Отличное", "Встроенная память": "1000 GB"},
    })
    review = AIReview(
        listing_id=listing.listing_id, text_analyzed=True, photos_analyzed=False,
        identified_model="PlayStation 5 Slim", storage="1000 GB", condition="Отличное",
        matches_request=True, verdict=ReviewVerdict.APPROVE, confidence=0.95,
    )
    return listing, review


class StorageEvidenceTests(unittest.TestCase):
    def test_actor_unit_in_parameter_name_is_read_without_ai_guess(self):
        listing, review = ps5()
        listing = replace(listing, parameters={"Модель": "PlayStation 5 Slim", "Встроенная память, ГБ": "1000", "Состояние": "Отличное"})
        self.assertEqual(canonical_evidence("storage", listing.storage), "1000 gb")
        self.assertTrue(matches_listing_request(
            listing, SearchRequest("PlayStation 5 Slim", required_storage="1 TB"),
            identified_model=review.identified_model, storage="1TB", condition=review.condition, final=True,
        ))
        conflicting = replace(listing, parameters={**listing.parameters, "Встроенная память": "825 GB"})
        self.assertFalse(matches_listing_request(
            conflicting, SearchRequest("PlayStation 5 Slim"),
            identified_model=review.identified_model, storage="1TB", condition=review.condition, final=True,
        ))

    def test_bluray_alias_is_limited_to_playstation_console_identity(self):
        for model in ("PlayStation 5 Slim", "PS5", "PS4"):
            self.assertEqual(canonical_evidence("model", model + " Blu-Ray Edition"),
                             canonical_evidence("model", model + " с дисководом"))
        self.assertNotEqual(canonical_evidence("model", "Blu-Ray плеер Sony"),
                            canonical_evidence("model", "Disc плеер Sony"))

    def test_nominal_terabyte_equals_thousand_gigabytes(self):
        for value in ("1TB", "1 TB", "1 тб", "1000GB", "1000 ГБ", "1.0 TB", "1,0 ТБ"):
            with self.subTest(value=value):
                self.assertEqual(canonical_evidence("storage", value), "1000 gb")
                self.assertFalse(evidence_conflicts("storage", "1000 GB", value))

    def test_gigabyte_case_language_and_spacing_aliases_are_identical(self):
        for value in ("825 GB", "825 Gb", "825 gb", "825 ГБ", "825 Гб", "825 гб", "825 G B", "825 г б"):
            with self.subTest(value=value):
                self.assertEqual(canonical_evidence("storage", value), "825 gb")
                self.assertFalse(evidence_conflicts("storage", "825 GB", value))

    def test_decimal_capacity_is_not_magnified_by_punctuation_removal(self):
        for value in ("0.5 TB", "0,5 ТБ", "500GB"):
            with self.subTest(value=value):
                self.assertEqual(canonical_evidence("storage", value), "500 gb")
                self.assertTrue(evidence_conflicts("storage", value, "5 TB"))

    def test_distinct_capacities_and_explicit_binary_units_stay_distinct(self):
        for value in ("825 GB", "1024 GB", "2 TB", "1 TiB", "1000 GiB", "1 TB + 256 GB"):
            with self.subTest(value=value):
                self.assertTrue(evidence_conflicts("storage", "1 TB", value))

    def test_actual_text_photo_pair_no_longer_creates_false_conflict(self):
        _, text = ps5()
        merged = OpenAICompatibleReviewer._merge_reviews(text, replace(text, storage="1TB", photos_analyzed=True))
        self.assertEqual(merged.verdict, ReviewVerdict.APPROVE)
        self.assertEqual(merged.conflicts, ())

    def test_real_storage_conflict_still_downgrades_review(self):
        _, text = ps5()
        merged = OpenAICompatibleReviewer._merge_reviews(text, replace(text, storage="825 GB", photos_analyzed=True))
        self.assertEqual(merged.verdict, ReviewVerdict.CAUTION)
        self.assertEqual(len(merged.conflicts), 1)

    def test_exact_request_and_market_key_share_nominal_unit_rule(self):
        listing, review = ps5()
        request = SearchRequest("PlayStation 5 Slim", required_storage="1 TB")
        self.assertTrue(matches_listing_request(
            listing, request, identified_model=review.identified_model,
            storage=review.storage, condition=review.condition, final=True,
        ))
        gigabytes = AnalyzedListing(listing, (), review)
        terabyte = replace(gigabytes, ai_review=replace(review, storage="1 TB"))
        self.assertEqual(gigabytes.comparable_key(), terabyte.comparable_key())
        # Correct units do not invent missing repair/parts evidence. Unknown
        # history is allowed only with the mandatory disclosure, per owner.
        self.assertTrue(terabyte.condition_evidence_complete())
        self.assertEqual(len(terabyte.history_warnings()), 2)
        self.assertEqual((listing.repair_status, listing.parts_status), ("", ""))


if __name__ == "__main__":
    unittest.main()
