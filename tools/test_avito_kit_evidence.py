"""Offline literal regressions for kits lost in real PS5 listing descriptions."""
import unittest

from avito_service.normalization import normalize_listing


def listing(description, **parameters):
    return normalize_listing({
        "id": "12345678", "title": "Sony PlayStation 5 Slim", "price": 48_000,
        "url": "https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/ps5_12345678",
        "description": description, "status": "active", "currency": "RUB",
        "parameters": {"Модель": "PlayStation 5 Slim", "Состояние": "Отличное", **parameters},
    })


class KitEvidenceTests(unittest.TestCase):
    def test_literal_mixed_alphabet_full_kit_from_8313813476(self):
        candidate = listing(
            "Пoлный кoмплeкт состоящий из :\n\n"
            "🟢 Kоpoбкa cо вceй дoкументациeй\n\n"
            "🟢 Opигинaльный Duаlsеnse\n\n"
            "🟢 Sony PS5 slim\n\n"
            "🟢 3 пpовoда для пoдключения Нdmi/Tyрe-s/Питание\n\n"
            "Даем гарантию от нашего магазина."
        )
        self.assertEqual(candidate.completeness, "full")
        self.assertEqual((candidate.repair_status, candidate.parts_status), ("", ""))

    def test_literal_hyphen_list_from_4317674096_preserves_items_without_claiming_full(self):
        candidate = listing(
            "Сocтояние: Б/у\nКoмплeктация:\n"
            "- Конcoль\n- Kонтрoллеp DuаlSеnsе\n- Кaбель HDMI\n"
            "- Кaбель питaния\n- USB-кабeль для зapядки кoнтрoллepa\n\n"
            "Oплатa толькo за нaличный pacчeт."
        )
        self.assertTrue(candidate.completeness.startswith("описанный комплект: "))
        self.assertIn("Кaбель HDMI", candidate.completeness)
        self.assertIn("USB-кабeль", candidate.completeness)
        self.assertNotIn("Oплатa", candidate.completeness)
        self.assertNotEqual(candidate.completeness, "full")

    def test_literal_numbered_list_from_8093982319_stops_before_shop_boilerplate(self):
        candidate = listing(
            "Комплектaция:\n\n1. Сaма консоль\n\n2. Геймпад\n\n"
            "3. Кабель питaния\n\n4. Kaбeль зaрядки геймпада\n\n5. Кaбeль HDMI\n\n"
            "════════════════════════════════\n✅ Ремонт и чистка консолей и геймпадов"
        )
        self.assertIn("Геймпад", candidate.completeness)
        self.assertIn("Кaбeль HDMI", candidate.completeness)
        self.assertNotIn("Ремонт", candidate.completeness)
        self.assertEqual((candidate.repair_status, candidate.parts_status), ("", ""))
        self.assertNotEqual(candidate.completeness, "full")

    def test_missing_box_and_partial_statement_override_full_claims(self):
        for text in (
            "Полный комплект, но без коробки.", "Пoлный кoмплeкт. Коробку потерял.",
            "Полный комплект. Коробки нет.", "Неполный комплект.", "Безкоробки.",
        ):
            with self.subTest(text=text):
                self.assertEqual(listing(text, Комплектация="Полный комплект").completeness, "partial")

    def test_optional_kit_is_not_the_standard_kit(self):
        for text in (
            "Полный комплект за доплату.",
            "Комплектация за дополнительную плату: коробка, геймпад.",
            "В комплекте за доплату коробка и документы.",
            "Полный комплект:\n- Консоль\n- Геймпад за доплату",
        ):
            with self.subTest(text=text):
                self.assertNotEqual(listing(text).completeness, "full")
        self.assertEqual(listing("", Комплектация="Полный комплект за доплату").completeness, "")

    def test_separate_optional_accessory_does_not_erase_an_explicit_standard_full_kit(self):
        self.assertEqual(listing("Полный комплект. Второй геймпад за доплату.").completeness, "full")

    def test_inline_declared_items_are_preserved_without_inventing_parts_history(self):
        candidate = listing("В комплекте два оригинальных беспроводных контроллера. Подписка продается отдельно.")
        self.assertEqual(candidate.completeness, "описанный комплект: два оригинальных беспроводных контроллера")
        self.assertEqual((candidate.parts_status, candidate.repair_status), ("", ""))

    def test_accessory_originality_does_not_establish_console_parts_history(self):
        for text in (
            "В комплекте все оригинальные контроллеры.",
            "Все оригинальные геймпады и кабели.",
            "Консоль не ремонтировалась. Все оригинальные геймпады.",
        ):
            with self.subTest(text=text):
                self.assertEqual(listing(text).parts_status, "")

    def test_accessory_repair_history_does_not_establish_console_history(self):
        for text in (
            "Геймпад не ремонтировался. Консоль в хорошем состоянии.",
            "Оба оригинальных контроллера не ремонтировались.",
            "Контроллер после ремонта. Полный комплект.",
        ):
            with self.subTest(text=text):
                self.assertEqual(listing(text).repair_status, "")

    def test_explicit_device_repair_and_parts_evidence_is_preserved(self):
        for text in (
            "Консоль не ремонтировалась. Все детали оригинальные.",
            "Не ремонтировалась. Всё оригинальное.",
        ):
            with self.subTest(text=text):
                candidate = listing(text)
                self.assertEqual((candidate.repair_status, candidate.parts_status), ("never_repaired", "original"))
        candidate = listing("После ремонта. Все детали оригинальные. Неоригинальный экран.")
        self.assertEqual((candidate.repair_status, candidate.parts_status), ("repaired", "conflicting"))

    def test_denied_or_uncertain_originality_never_becomes_an_original_parts_claim(self):
        for text, expected in (
            ("Не все детали оригинальные.", "non_original"),
            ("Не все оригинальное.", "non_original"),
            ("Возможно, все детали оригинальные.", ""),
            ("Не факт, что комплектующие оригинальные.", ""),
            ("Детали оригинальные?", ""),
            ("Вроде все детали оригинальные.", ""),
            ("Все детали оригинальные. Но насчет оригинальности деталей не уверен.", ""),
            ("Не ремонтировалась. Все детали оригинальные.", "original"),
        ):
            with self.subTest(text=text):
                self.assertEqual(listing(text + " Полный комплект.").parts_status, expected)
        self.assertEqual(listing("Возможно, все детали оригинальные.",
                                **{"Оригинальность деталей": "Все оригинальные"}).parts_status, "")

    def test_unknown_kit_is_not_converted_into_known_evidence(self):
        for text in ("Комплектация: уточняйте", "Комплектация: нет данных", "Без сведений о комплекте."):
            with self.subTest(text=text):
                self.assertEqual(listing(text).completeness, "")

    def test_kit_in_second_sentence_survives_later_optional_subscription(self):
        candidate = listing(
            "Продается игровая приставка Sony PlayStation 5 с дисководом, ревизия 3. "
            "В комплекте два оригинальных беспроводных контроллера. "
            "Корпус без видимых повреждений. Есть подписка, продам отдельно."
        )
        self.assertEqual(candidate.completeness, "описанный комплект: два оригинальных беспроводных контроллера")
        self.assertEqual(candidate.parts_status, "")

    def test_em_dash_kit_bullets_are_preserved(self):
        candidate = listing("Комплектация:\n— Игровая приставка\n— Геймпад 1 шт\n— Кабель HDMI\n— Кабель питания\n════════\nРемонт и чистка")
        self.assertIn("Кабель HDMI", candidate.completeness)
        self.assertIn("Геймпад 1 шт", candidate.completeness)
        self.assertNotIn("Ремонт", candidate.completeness)
        self.assertNotEqual(candidate.completeness, "full")

    def test_explicit_full_kit_inside_bullets_is_recognized_but_partial_and_optional_win(self):
        text = "Что входит в комплект:\n• Консоль\n• Геймпад\n• Полный заводской комплект\n• Заводская упаковка"
        self.assertEqual(listing(text).completeness, "full")
        self.assertEqual(listing(text + "\nКоробки нет.").completeness, "partial")
        self.assertNotEqual(listing(text.replace("Полный заводской комплект", "Полный заводской комплект за доплату")).completeness, "full")

    def test_existing_structured_and_device_only_values_stay_supported(self):
        self.assertEqual(listing("", Комплектация="Коробка, зарядное устройство").completeness, "коробка, зарядное устройство")
        self.assertEqual(listing("Продается только консоль.").completeness, "device_only")


if __name__ == "__main__":
    unittest.main()
