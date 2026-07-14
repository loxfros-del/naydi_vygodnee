from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import unittest


DATASET = Path(__file__).with_name("live_acceptance_cases.json")
EXPECTED_CATEGORIES = {"phone", "laptop", "tv", "headphones", "monitor", "chair"}
REQUIRED_FIELDS = {"id", "query", "category", "budget", "required_specs", "city", "used_allowed"}


class LiveAcceptanceCasesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(DATASET.read_text(encoding="utf-8"))

    def test_dataset_has_exactly_30_cases(self) -> None:
        self.assertIsInstance(self.cases, list)
        self.assertEqual(len(self.cases), 30)

    def test_each_case_has_valid_schema(self) -> None:
        for case in self.cases:
            with self.subTest(case=case.get("id")):
                self.assertIsInstance(case, dict)
                self.assertTrue(REQUIRED_FIELDS.issubset(case))
                self.assertIsInstance(case["id"], str)
                self.assertTrue(case["id"].strip())
                self.assertIsInstance(case["query"], str)
                self.assertGreaterEqual(len(case["query"].split()), 4)
                self.assertIn(case["category"], EXPECTED_CATEGORIES)
                self.assertIsInstance(case["budget"], int)
                self.assertGreater(case["budget"], 0)
                self.assertIsInstance(case["required_specs"], dict)
                self.assertTrue(case["required_specs"])
                self.assertIsInstance(case["city"], str)
                self.assertTrue(case["city"].strip())
                self.assertIs(type(case["used_allowed"]), bool)

    def test_ids_and_queries_are_unique(self) -> None:
        ids = [case["id"] for case in self.cases]
        queries = [case["query"].casefold() for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(queries), len(set(queries)))

    def test_each_category_has_exactly_five_cases(self) -> None:
        counts = Counter(case["category"] for case in self.cases)
        self.assertEqual(set(counts), EXPECTED_CATEGORIES)
        self.assertEqual(counts, Counter({category: 5 for category in EXPECTED_CATEGORIES}))

    def test_each_category_includes_a_used_allowed_case(self) -> None:
        categories_with_used = {
            case["category"] for case in self.cases if case["used_allowed"] is True
        }
        self.assertEqual(categories_with_used, EXPECTED_CATEGORIES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
