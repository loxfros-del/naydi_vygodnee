"""Regression for a live AI error about the unspecified PS5 family."""
import unittest

from avito_service.models import AIReview, ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_dataset
from avito_service.service import reconcile_ps5_family_mismatch


class PS5FamilyReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.listing = normalize_dataset([{
            "id": "8125174775",
            "title": "Sony PlayStation 5 Slim 1 TB",
            "url": "https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/ps5_slim_8125174775",
            "status": "active",
            "price": 48000,
            "description": "Sony PlayStation 5 Slim 1 TB, полный комплект.",
            "images": ["https://img.avito.st/console.jpg"],
            "address": "Ярославль",
            "parameters": {"Модель": "PlayStation 5 Slim", "Состояние": "Отличное"},
        }])[0]
        self.review = AIReview(
            listing_id=self.listing.listing_id, text_analyzed=True, photos_analyzed=False,
            identified_model="PlayStation 5 Slim", condition="Отличное",
            matches_request=False,
            mismatch_reason="Модель указана как PlayStation 5 Slim, которая не входит в стандартный запрос PS5 без уточнения Slim",
            verdict=ReviewVerdict.REJECT, confidence=0.9,
        )

    def test_generic_ps5_accepts_known_false_slim_rejection(self):
        corrected = reconcile_ps5_family_mismatch(self.listing, self.review, SearchRequest("PS5"))
        self.assertTrue(corrected.matches_request)
        self.assertEqual(corrected.verdict, ReviewVerdict.APPROVE)
        self.assertEqual(corrected.mismatch_reason, "")

    def test_does_not_override_explicit_variant_or_other_safety_issue(self):
        for request in (SearchRequest("PS5 Pro"), SearchRequest("PS5 Slim")):
            self.assertIs(reconcile_ps5_family_mismatch(self.listing, self.review, request), self.review)
        unsafe = self.review.__class__(**{
            **{field: getattr(self.review, field) for field in self.review.__dataclass_fields__},
            "defects": ("Повреждён HDMI-разъём",),
        })
        self.assertIs(reconcile_ps5_family_mismatch(self.listing, unsafe, SearchRequest("PS5")), unsafe)


if __name__ == "__main__":
    unittest.main()
