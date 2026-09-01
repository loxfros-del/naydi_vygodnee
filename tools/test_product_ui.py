"""Deterministic checks for onboarding, pricing and safe presentation."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.product_config import (  # noqa: E402
    AUTO_CATEGORIES, SearchMode, WelcomeImageSource, classify_request,
    get_service_packages, resolve_welcome_image,
)
from app.ui_formatters import chunk_html, escape_html, format_pricing, format_progress, source_display_name  # noqa: E402
from app.ui_keyboards import callback_data_values, main_menu_keyboard, category_keyboard  # noqa: E402
from app.ui_texts import (  # noqa: E402
    BTN_COMPARE_LINKS, BTN_MY_REQUESTS, BTN_OPEN_NOVA, BTN_PRICING, BTN_SELECT_PRODUCT, BTN_SUPPORT,
)


class FakeState:
    def __init__(self): self.cleared = False
    async def clear(self): self.cleared = True


class FakeMessage:
    def __init__(self, fail_photo=False):
        self.fail_photo = fail_photo
        self.photos = []
        self.answers = []
        self.from_user = SimpleNamespace(id=7)
    async def answer_photo(self, photo):
        if self.fail_photo:
            raise RuntimeError("photo unavailable")
        self.photos.append(photo)
    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class ProductUITests(unittest.TestCase):
    def test_main_menu_has_all_client_actions(self):
        labels = [button.text for row in main_menu_keyboard().keyboard for button in row]
        for label in (BTN_SELECT_PRODUCT, BTN_COMPARE_LINKS, BTN_MY_REQUESTS, BTN_PRICING, BTN_SUPPORT):
            self.assertIn(label, labels)
        self.assertNotIn("Админ", " ".join(labels))

    def test_miniapp_button_requires_public_https_url(self):
        missing = [button.text for row in main_menu_keyboard(miniapp_url="http://localhost:8080").keyboard for button in row]
        self.assertNotIn(BTN_OPEN_NOVA, missing)

        keyboard = main_menu_keyboard(miniapp_url="https://nova.example/app")
        button = next(button for row in keyboard.keyboard for button in row if button.text == BTN_OPEN_NOVA)
        self.assertEqual(button.web_app.url, "https://nova.example/app")

    def test_free_mode_overrides_old_package_price_variables(self):
        free_packages = get_service_packages()
        self.assertTrue(all(package.price_rub == 0 for package in free_packages))
        self.assertIn("Бесплатно", format_pricing(free_packages))

        packages = get_service_packages({
            "QUICK_SELECTION_PRICE_RUB": "499",
            "DEEP_SELECTION_PRICE_RUB": "",
        })
        self.assertTrue(all(package.price_rub == 0 for package in packages))
        text = format_pricing(packages)
        self.assertIn("Бесплатно", text)
        self.assertNotIn("149–299", text)

    def test_six_supported_categories_and_gate(self):
        self.assertEqual(len(AUTO_CATEGORIES), 6)
        self.assertIs(classify_request("нужен ноутбук").mode, SearchMode.AUTO)
        self.assertIs(classify_request("нужен автомобиль").mode, SearchMode.MANUAL)
        self.assertIs(classify_request("https://ozon.ru/a https://market.yandex.ru/b").mode, SearchMode.LINK_COMPARISON)

    def test_callbacks_fit_telegram_limit(self):
        for value in callback_data_values(category_keyboard()):
            self.assertLessEqual(len(value.encode("utf-8")), 64)

    def test_escape_source_names_and_html_chunking(self):
        self.assertEqual(escape_html("<b>&"), "&lt;b&gt;&amp;")
        self.assertEqual(source_display_name("yandex_market_direct"), "Яндекс Маркет")
        self.assertEqual(source_display_name("dns_shop"), "DNS")
        chunks = chunk_html("<b>" + ("A" * 9000) + "</b>", 3900)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 3900 for chunk in chunks))
        self.assertTrue(all(chunk.startswith("<b>") and chunk.endswith("</b>") for chunk in chunks))
        progress = format_progress("verification", 12)
        self.assertIn("Проверяем модели и цены", progress)
        self.assertNotIn("403", progress)
        self.assertNotIn("score", progress.lower())

    def test_welcome_resolution(self):
        bundled = resolve_welcome_image({})
        self.assertIsNotNone(bundled)
        self.assertEqual(bundled.kind, "local_path")
        self.assertEqual(Path(bundled.value).name, "welcome.png")
        self.assertEqual(resolve_welcome_image({"WELCOME_IMAGE_FILE_ID": "abc"}).value, "abc")
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "welcome.jpg"
            image.write_bytes(b"test")
            resolved = resolve_welcome_image({"WELCOME_IMAGE_PATH": str(image)})
            self.assertEqual(resolved.kind, "local_path")


class OnboardingHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_photo_and_fallback(self):
        from app.handlers import user
        message = FakeMessage()
        with patch.object(user, "resolve_welcome_image", return_value=WelcomeImageSource("file_id", "photo-id")):
            await user._show_home(message, FakeState(), track_start=False)
        self.assertEqual(message.photos, ["photo-id"])
        self.assertTrue(message.answers)

        fallback = FakeMessage(fail_photo=True)
        with patch.object(user, "resolve_welcome_image", return_value=WelcomeImageSource("file_id", "bad")):
            await user._show_home(fallback, FakeState(), track_start=False)
        self.assertEqual(len(fallback.answers), 1)
        self.assertIn("Найди выгоднее", fallback.answers[0][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
