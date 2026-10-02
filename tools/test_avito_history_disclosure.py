"""Unknown history requires disclosure and never substitutes for known history."""
from dataclasses import replace
import unittest

from avito_service.models import Recommendation, RecommendationRole, ReviewVerdict
from avito_service.ranking import is_customer_safe, rank_listings
from tools.test_avito_collection_quality import service
from tools.test_avito_market_quality import REQUEST, analyzed_item
from tools.test_avito_strict_results import provider, search_request


WARNINGS = (
    "История ремонта неизвестна — уточните при осмотре.",
    "Оригинальность внутренних деталей не подтверждена.",
)


def unknown_history(item):
    return replace(item, listing=replace(item.listing, repair_status="", parts_status=""))


def remove_source_history(row):
    row["description"] = "Консоль в отличном состоянии. Полный комплект. Цена окончательная."
    row["parameters"].pop("Ремонт", None)
    row["parameters"].pop("Оригинальность деталей", None)


class HistoryDisclosureTests(unittest.TestCase):
    def test_actual_pipeline_requires_photos_refresh_and_three_unknown_history_references(self):
        source = provider()
        source.market_rows = source.market_rows[:3]
        for row in (*source.market_rows, *source.candidate_rows):
            remove_source_history(row)
        engine = service(source)
        report = engine.search(search_request())
        result = report.public_dict()
        self.assertEqual(result["visibleCount"], 1)
        card = result["recommendations"][0]
        self.assertEqual(card["historyWarnings"], list(WARNINGS))
        self.assertTrue(set(WARNINGS).issubset(card["risks"]))
        self.assertEqual(card["comparableSellerCount"], 3)
        self.assertTrue(card["belowComparables"])
        self.assertFalse(card["belowMarket"])
        self.assertEqual(card["listing"]["verificationStatus"], "verified")
        self.assertEqual(card["listing"]["conditionEvidence"]["repairStatus"], "unknown")
        self.assertEqual(card["listing"]["conditionEvidence"]["partsStatus"], "unknown")
        self.assertIn("10000000", engine.reviewer.photo_calls)
        self.assertEqual(len(source.refresh_calls), 1)
        self.assertEqual(result["emptyReason"], "")

    def test_manual_recommendation_cannot_drop_mandatory_history_disclosure(self):
        item = unknown_history(analyzed_item())
        card = Recommendation(RecommendationRole.BACKUP, item, risks=()).public_dict()
        self.assertEqual(card["risks"], [])
        self.assertEqual(card["historyWarnings"], list(WARNINGS))

    def test_known_and_unknown_histories_never_share_a_comparison_group(self):
        candidate = unknown_history(analyzed_item(price=40_000))
        known_refs = tuple(analyzed_item(1000010 + index, price=50_000) for index in range(3))
        result = rank_listings((candidate,), REQUEST, market_analyzed=known_refs)[0]
        self.assertEqual(result.comparable_seller_count, 0)
        self.assertFalse(result.below_comparables)
        mixed_refs = tuple(unknown_history(item) for item in known_refs[:2]) + known_refs[2:]
        result = rank_listings((candidate,), REQUEST, market_analyzed=mixed_refs)[0]
        self.assertEqual(result.comparable_seller_count, 2)
        self.assertFalse(result.below_comparables)

    def test_explicit_unknown_aliases_keep_the_same_unknown_group(self):
        item = unknown_history(analyzed_item())
        for value in ("unknown", "неизвестно", "не указано"):
            other = replace(item, listing=replace(item.listing, repair_status=value, parts_status=value))
            self.assertEqual(other.comparable_key(), item.comparable_key())
            self.assertEqual(other.history_warnings(), WARNINGS)

    def test_unknown_history_does_not_bypass_missing_condition_kit_battery_or_verification(self):
        item = unknown_history(analyzed_item())
        self.assertTrue(is_customer_safe(item, REQUEST))
        for changes in (
            {"battery_health_percent": None}, {"completeness": ""},
            {"verified_at": ""}, {"repair_status": "conflicting"},
            {"parts_status": "conflicting"}, {"repair_status": "seller_guess"},
            {"parts_status": "seller_guess"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(is_customer_safe(replace(item, listing=replace(item.listing, **changes)), REQUEST))
        for changes in (
            {"photos_analyzed": False}, {"condition": "Неизвестно"},
            {"defects": ("Не работает дисковод",)}, {"conflicts": ("На фото другая модель",)},
            {"verdict": ReviewVerdict.CAUTION},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(is_customer_safe(replace(item, ai_review=replace(item.ai_review, **changes)), REQUEST))

    def test_known_repair_and_nonoriginal_parts_are_preserved_and_defects_still_block(self):
        original = analyzed_item()
        changed = replace(original, listing=replace(original.listing, repair_status="repaired", parts_status="non_original"))
        self.assertEqual(changed.history_warnings(), ())
        self.assertNotEqual(changed.comparable_key(), original.comparable_key())
        self.assertNotEqual(changed.comparable_key(), unknown_history(original).comparable_key())
        defective = replace(changed, ai_review=replace(changed.ai_review, defects=("После ремонта не работает камера",)))
        self.assertFalse(is_customer_safe(defective, REQUEST))
        card = Recommendation(RecommendationRole.CAUTION, defective).public_dict()
        self.assertEqual(card["listing"]["conditionEvidence"]["repairStatus"], "repaired")
        self.assertEqual(card["listing"]["conditionEvidence"]["partsStatus"], "non_original")

    def test_new_goods_do_not_get_used_history_warnings(self):
        item = unknown_history(analyzed_item())
        item = replace(item, ai_review=replace(item.ai_review, condition="Новое"))
        self.assertEqual(item.history_warnings(), ())


if __name__ == "__main__":
    unittest.main()
