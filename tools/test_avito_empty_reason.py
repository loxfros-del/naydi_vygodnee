"""Offline pipeline checks for truthful explanations of missing seller facts."""
from dataclasses import replace
import unittest

from tools.test_avito_collection_quality import EvidenceReviewer, service
from tools.test_avito_strict_results import console_row, search_request


def incomplete_console(identifier=10000000):
    value = console_row(identifier, 30_000)
    value["description"] = "Консоль в отличном состоянии. Полный комплект. Цена окончательная."
    value["parameters"].pop("Ремонт")
    value["parameters"].pop("Оригинальность деталей")
    return value


class EmptyReasonTests(unittest.TestCase):
    def test_unknown_kit_gets_a_specific_non_diagnostic_reason(self):
        value = incomplete_console()
        value["description"] = "Консоль в отличном состоянии. Цена окончательная."
        value["parameters"].update({"Комплектация": ""})
        result = service(None).analyze_dataset([value], search_request()).public_dict()
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["discoveredListings"], [])
        self.assertIn("Не хватает сведений продавца", result["emptyReason"])
        self.assertIn("комплект — 1", result["emptyReason"])
        self.assertNotIn("история ремонта", result["emptyReason"])
        self.assertNotIn("оригинальность деталей", result["emptyReason"])
        self.assertIn("не означает, что товар неисправен", result["emptyReason"])
        self.assertNotIn("10000000", result["emptyReason"])
        self.assertEqual(result["warnings"], [result["emptyReason"]])

    def test_unknown_kit_on_new_device_does_not_claim_missing_repair_history(self):
        value = incomplete_console()
        value["description"] = "Новая консоль. Цена окончательная."
        value["parameters"].update({"Состояние": "Новое", "Комплектация": ""})
        result = service(None).analyze_dataset([value], search_request()).public_dict()
        self.assertIn("комплект — 1", result["emptyReason"])
        self.assertNotIn("история ремонта", result["emptyReason"])

    def test_other_approved_candidate_with_complete_condition_preserves_generic_reason(self):
        result = service(None).analyze_dataset(
            [incomplete_console(), console_row(10000001, 31_000)], search_request(),
        ).public_dict()
        self.assertNotIn("Не хватает сведений продавца", result["emptyReason"])
        self.assertEqual(result["recommendations"], [])

    def test_text_error_is_reported_as_unfinished_analysis_not_missing_seller_facts(self):
        class FailingReviewer(EvidenceReviewer):
            def review_text(self, listing, search=None):
                return replace(super().review_text(listing, search), text_analyzed=False, error="offline failure")

        result = service(None, FailingReviewer()).analyze_dataset(
            [incomplete_console()], search_request(),
        ).public_dict()
        self.assertIn("Проверка подходящих объявлений не завершена", result["emptyReason"])
        self.assertNotIn("Не хватает сведений продавца", result["emptyReason"])


if __name__ == "__main__":
    unittest.main()
