#!/usr/bin/env python3
"""Offline regression for used-condition qualifiers blocking valid photo review."""
from __future__ import annotations

from dataclasses import replace
import unittest

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.matching import canonical_evidence, evidence_conflicts, matches_listing_request
from avito_service.models import AIReview, AnalyzedListing, ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_listing
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService


GOOD_ALIASES = (
    "Хорошее", "Хорошее состояние", "Хорошее (Б/у)", "Б/у, хорошее состояние",
    "Хорошее, бывший в употреблении", "Состояние хорошее (бу)",
    "В хорошем состоянии, б/у", "Good (used)", "used good", "Good, pre-owned",
)
EXCELLENT_ALIASES = (
    "Отличное", "Отличное состояние", "Отличное (Б/у)",
    "Отличное, бывший в употреблении", "Состояние отличное (бу)",
    "В отличном состоянии, б/у", "Excellent (used)", "Как новый (б/у)",
)


def observed_ps5():
    """Sanitized source facts from cached listing 4317674096, collected 2026-09-13.

    The price/title/condition/review mismatch are observed data. Descriptions,
    images and seller data are harmless test replacements, not live evidence.
    """
    listing = normalize_listing({
        "id": "4317674096", "title": "Sony PlayStation 5, 825 гб", "price": 57_999,
        "url": "https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/ps5_4317674096",
        "status": "active", "currency": "RUB", "location": "yaroslavl",
        "description": "Описание консоли для автономной проверки алгоритма.",
        "images": ["https://01.img.avito.st/offline-ps5-fixture.jpg"],
        "parameters": {"Модель": "PlayStation 5", "Состояние": "Хорошее", "Встроенная память, ГБ": "825"},
    })
    review = AIReview(
        listing_id=listing.listing_id, text_analyzed=True, photos_analyzed=False,
        identified_model="Sony PlayStation 5", storage="825 ГБ", condition="Хорошее (Б/у)",
        matches_request=True, verdict=ReviewVerdict.APPROVE, confidence=0.95,
    )
    return listing, review


class ConditionAliasTests(unittest.TestCase):
    def test_sealed_new_is_the_same_grade_but_never_used_or_uncertain(self):
        _, source_review = observed_ps5()
        text_review = replace(source_review, condition="Новое")
        for value in ("Новое (запечатанное)", "Новый, запечатанный", "Новая (запечатанная)", "new sealed"):
            with self.subTest(value=value):
                self.assertEqual(canonical_evidence("condition", value), "new")
                self.assertFalse(evidence_conflicts("condition", "Новое", value))
                self.assertTrue(evidence_conflicts("condition", "Хорошее", value))
                self.assertTrue(evidence_conflicts("condition", "Отличное", value))
                merged = OpenAICompatibleReviewer._merge_reviews(text_review,
                    replace(text_review, condition=value, photos_analyzed=True, photo_coverage=(1,)))
                self.assertEqual(merged.verdict, ReviewVerdict.APPROVE)
                self.assertEqual(merged.conflicts, ())
        for value in ("Не новое, запечатанное", "Новое запечатанное б/у", "Возможно новое запечатанное", "Запечатанное"):
            self.assertNotEqual(canonical_evidence("condition", value), "new")

    def test_common_english_condition_labels_match_russian_grades(self):
        aliases = {
            "new": ("Brand New", "factory-sealed", "New in box", "Новое в упаковке"),
            "excellent": ("Excellent Condition", "Like New", "Отличное состояние"),
            "good": ("Good Condition", "Very Good", "very good condition", "Хорошее состояние"),
        }
        for expected, values in aliases.items():
            for value in values:
                with self.subTest(value=value):
                    self.assertEqual(canonical_evidence("condition", value), expected)

    def test_visual_observation_is_not_a_grade_conflict_or_grade_confirmation(self):
        listing, text_review = observed_ps5()
        for observation in ("Внешне без видимых дефектов", "Видимых дефектов не обнаружено", "Без видимых повреждений"):
            with self.subTest(observation=observation):
                photo_review = replace(text_review, condition=observation, photos_analyzed=True,
                                       photo_coverage=(1,), photo_findings=(observation,))
                merged = OpenAICompatibleReviewer._merge_reviews(text_review, photo_review)
                self.assertEqual(merged.verdict, ReviewVerdict.APPROVE)
                self.assertEqual(merged.conflicts, ())
                self.assertEqual(merged.condition, text_review.condition)
                self.assertIn(observation, merged.photo_findings)
                self.assertNotIn(canonical_evidence("condition", observation), {"good", "excellent", "new", "used"})
                for grade in ("Хорошее", "Отличное", "Новое"):
                    self.assertFalse(matches_listing_request(listing,
                        SearchRequest("PlayStation 5", required_condition=grade),
                        identified_model=text_review.identified_model, condition=observation, final=True))
        # Extra text can carry a real grade/defect and is not silently discarded.
        for observation in ("Хорошее, внешне без видимых дефектов", "Без видимых дефектов, но разбит экран"):
            self.assertTrue(evidence_conflicts("condition", "Отличное", observation))

    def test_photo_prompt_distinguishes_observations_from_grade_and_catalog_memory(self):
        prompt = OpenAICompatibleReviewer._photo_system_prompt()
        self.assertIn("condition оставь пустым", prompt)
        self.assertIn("записывай в photo_findings", prompt)
        self.assertIn("несуществовании", prompt)
        self.assertIn("поддельных отзывах", prompt)
        self.assertIn("конкретные видимые несоответствия", prompt)

    def test_used_qualifier_preserves_the_explicit_condition_grade(self):
        for expected, aliases in (("good", GOOD_ALIASES), ("excellent", EXCELLENT_ALIASES)):
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(canonical_evidence("condition", alias), expected)
                    self.assertFalse(evidence_conflicts("condition", aliases[0], alias))

    def test_unknown_used_grade_remains_used_and_does_not_become_good(self):
        for value in ("Б/у", "бу", "used", "бывший в употреблении", "бывшая в употреблении", "pre-owned"):
            with self.subTest(value=value):
                self.assertEqual(canonical_evidence("condition", value), "used")
                self.assertTrue(evidence_conflicts("condition", value, "Хорошее"))

    def test_good_and_excellent_are_not_merged(self):
        for good in GOOD_ALIASES:
            for excellent in EXCELLENT_ALIASES:
                with self.subTest(good=good, excellent=excellent):
                    self.assertTrue(evidence_conflicts("condition", good, excellent))

    def test_new_and_used_are_not_merged(self):
        for value in (*GOOD_ALIASES, *EXCELLENT_ALIASES, "Б/у", "used"):
            with self.subTest(value=value):
                self.assertTrue(evidence_conflicts("condition", "Новое", value))

    def test_contradictory_or_negative_phrases_do_not_turn_into_a_clean_grade(self):
        for value in (
            "Новое (б/у)", "Новый, бывший в употреблении", "Не хорошее (б/у)",
            "Хорошее или отличное, б/у", "Хорошее, но с дефектом, б/у",
            "Отличное, восстановленное, б/у", "Не б/у, хорошее",
        ):
            with self.subTest(value=value):
                self.assertNotIn(canonical_evidence("condition", value), {"good", "excellent", "new", "used"})

    def test_uncertain_condition_is_not_accepted_as_confirmed(self):
        listing, review = observed_ps5()
        request = SearchRequest("PlayStation 5", required_condition="Хорошее")
        self.assertFalse(matches_listing_request(
            listing, request, identified_model=review.identified_model,
            condition="Возможно хорошее (б/у)", final=True,
        ))

    def test_observed_ps5_grade_qualifier_no_longer_blocks_exact_match(self):
        listing, review = observed_ps5()
        self.assertTrue(matches_listing_request(
            listing, SearchRequest("PlayStation 5", location="Ярославль"),
            identified_model=review.identified_model, storage=review.storage,
            condition=review.condition, final=True,
        ))

    def test_observed_ps5_can_reach_photo_stage_without_a_market(self):
        listing, review = observed_ps5()
        service = AvitoAnalysisService(provider=object(), reviewer=object())
        request = SearchRequest("PlayStation 5", location="Ярославль", desired_results=3)
        risks = {listing.listing_id: evaluate_rules(listing)}
        result = service._photo_candidate_ids((listing,), risks, {listing.listing_id: review}, 3, request)
        self.assertEqual(result, (listing.listing_id,))
        self.assertFalse(review.is_complete_for(listing))
        self.assertFalse(AnalyzedListing(listing, risks[listing.listing_id], review).condition_evidence_complete())

    def test_actual_condition_conflict_still_blocks_photo_stage(self):
        listing, review = observed_ps5()
        service = AvitoAnalysisService(provider=object(), reviewer=object())
        request = SearchRequest("PlayStation 5", location="Ярославль")
        risks = {listing.listing_id: evaluate_rules(listing)}
        for changed_review in (
            replace(review, condition="Отличное (б/у)"),
            replace(review, condition="Новое"),
            replace(review, conflicts=("Реальное противоречие состояния.",)),
            replace(review, verdict=ReviewVerdict.CAUTION),
        ):
            with self.subTest(condition=changed_review.condition, verdict=changed_review.verdict):
                self.assertEqual(service._photo_candidate_ids(
                    (listing,), risks, {listing.listing_id: changed_review}, 3, request,
                ), ())


if __name__ == "__main__":
    unittest.main()
