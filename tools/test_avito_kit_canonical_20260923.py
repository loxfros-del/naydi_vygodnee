"""Source-grounded kit recovery; canonicalization never fills missing facts.

Known limit, checked offline 2026-09-23: the union of the saved 60-item and
97-item pilots has 130 unique rows, 53 preliminary candidates and 22 known
kits after this recovery. Strict source cohorts still have at most one
independent seller. These extraction fixes do not substantiate three bargains.
"""
import unittest
from dataclasses import replace

from avito_service.normalization import canonical_kit_evidence
from avito_service.models import AIReview, AnalyzedListing
from tools.test_avito_kit_evidence import listing


def key(value):
    return canonical_kit_evidence("описанный комплект: " + value)


class KitCanonicalTests(unittest.TestCase):
    def test_literal_full_original_kit_is_recovered(self):
        candidate = listing("Полный оригинaльный комплeкт, гарантия от магaзинa")
        self.assertEqual(candidate.completeness, "full")
        self.assertEqual(candidate.parts_status, "")

    def test_literal_slash_list_is_recovered_without_guessing_wire_types(self):
        candidate = listing("Корoбкa / Джoйcтик / 3 проводa / дoкумeнты")
        self.assertTrue(candidate.completeness.startswith("описанный комплект:"))
        self.assertIn("3 проводa", candidate.completeness)
        self.assertNotEqual(candidate.completeness, "full")
        self.assertIn("unspecified_cables:3", canonical_kit_evidence(candidate.completeness))

    def test_literal_two_controller_dock_bundle_keeps_boxes(self):
        text = ("Продам приставку PS5 Slim с дисководом, два геймпада, станция зарядки геймпадов. "
                "Игры накачанные, все в ТОП! Состояние идеальное. Приставке полгода, все коробки имеются.")
        candidate = listing(text)
        self.assertIn("два геймпада", candidate.completeness)
        self.assertIn("станция зарядки", candidate.completeness)
        self.assertIn("все коробки имеются", candidate.completeness)
        self.assertNotEqual(candidate.completeness, "full")

    def test_literal_headphones_bundle_is_kept_as_declared(self):
        candidate = listing("Продаётся комплект, приставка два геймпада, наушники и подставка, состояние идеальное, подписка до 06.08.2027")
        self.assertIn("два геймпада", candidate.completeness)
        self.assertIn("наушники", candidate.completeness)
        self.assertIn("06.08.2027", candidate.completeness)
        self.assertNotEqual(candidate.completeness, "full")

    def test_simple_known_components_ignore_order_and_spelling_aliases(self):
        left = "Консоль; два геймпада; кабель HDMI; кабель питания; коробка"
        right = "Кoрoбкa, пpoвoд питaния, 2 контроллера, HDMI-кабель, тушка"
        self.assertEqual(key(left), key(right))
        self.assertTrue(key(left).startswith("components:"))
        base = listing("Комплект: консоль, два геймпада, кабель HDMI, кабель питания, коробка")
        a = replace(base, completeness="описанный комплект: " + left)
        b = replace(base, completeness="описанный комплект: " + right)
        def compared(item):
            return AnalyzedListing(item, (), AIReview(item.listing_id, True, False)).comparable_key()
        self.assertEqual(compared(a), compared(b))
        self.assertNotEqual(compared(a), compared(replace(b, completeness="описанный комплект: консоль, 1 геймпад")))

    def test_unknown_quantity_does_not_become_one(self):
        self.assertNotEqual(key("геймпад"), key("1 геймпад"))
        self.assertNotEqual(key("контроллеры"), key("1 контроллер"))
        self.assertNotEqual(key("два геймпада"), key("1 геймпад"))

    def test_generic_wires_never_equal_a_specific_complete_cable_list(self):
        self.assertNotEqual(key("3 провода"), key("кабель HDMI; кабель питания; кабель зарядки геймпада"))
        self.assertNotEqual(key("провода"), key("3 провода"))

    def test_unknown_extra_preserves_entire_text_instead_of_being_discarded(self):
        plain = key("консоль; 1 геймпад")
        for text in ("консоль; 1 геймпад; телевизор Samsung 55", "консоль; 1 геймпад; подписка до декабря"):
            self.assertNotEqual(key(text), plain)
            self.assertTrue(key(text).startswith("literal:"))

    def test_full_kit_preserves_explicit_tv_headphones_and_multiple_controllers(self):
        for text in (
            "Полный комплект: консоль, телевизор Samsung 55, два геймпада.",
            "Полный комплект. В комплекте наушники.",
            "Полный комплект. Два геймпада.",
            "Полный комплект с двумя контроллерами.",
            "Полный комплект. В комплекте геймпады.",
            "Полный комплект с TV Samsung.",
            "Полный комплект:\n— Консоль\n— Наушники\n— Телевизор Samsung 55",
        ):
            with self.subTest(text=text):
                candidate = listing(text)
                self.assertTrue(candidate.completeness.startswith("full; явно включено:"))
                self.assertNotEqual(canonical_kit_evidence(candidate.completeness), canonical_kit_evidence("full"))

    def test_optional_accessories_do_not_enter_full_kit(self):
        candidate = listing("Полный комплект. Второй геймпад за доплату. Наушники можно купить отдельно.")
        self.assertEqual(candidate.completeness, "full")
        self.assertEqual(listing("Полный оригинальный комплект. Коробки нет.").completeness, "partial")
        catalogue = listing("Полный комплект:\n— Консоль\n— Геймпад\n✅ В продаже наушники\n✅ Ремонт телевизоров")
        self.assertEqual(catalogue.completeness, "full")

    def test_unknown_and_optional_kit_do_not_turn_into_a_declared_bundle(self):
        for text in ("Комплект уточняйте.", "Коробка / геймпад / кабели за доплату.", "Комплектация по запросу."):
            with self.subTest(text=text):
                self.assertEqual(listing(text).completeness, "")


if __name__ == "__main__":
    unittest.main()
