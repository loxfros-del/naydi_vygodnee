"""Deterministic checks for the NOVA PWA client."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


class WebAppTests(unittest.TestCase):
    def test_required_pwa_files_are_present_and_linked(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
        worker = (WEB / "service-worker.js").read_text(encoding="utf-8")

        for filename in ("index.html", "styles.css", "app.js", "service-worker.js", "icon.svg"):
            self.assertTrue((WEB / filename).is_file(), filename)
        self.assertIn('rel="manifest" href="manifest.webmanifest"', html)
        self.assertEqual(manifest["name"], "NOVA — smart market")
        self.assertEqual(manifest["display"], "standalone")
        self.assertIn('"./app.js?v=13"', worker)
        self.assertIn('"./styles.css?v=13"', worker)
        self.assertIn('nova-web-v13', worker)
        self.assertIn('styles.css?v=13', html)
        self.assertIn('app.js?v=13', html)

    def test_pwa_worker_version_and_offline_cache_match_the_current_assets(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        worker = (WEB / "service-worker.js").read_text(encoding="utf-8")

        self.assertIn('service-worker.js?v=13', script)
        self.assertIn('const CACHE_NAME = "nova-web-v13"', worker)
        self.assertIn('"./styles.css?v=13"', worker)
        self.assertIn('"./app.js?v=13"', worker)
        self.assertIn('caches.open(CACHE_NAME).then((cache) => (', worker)
        self.assertIn('cache.match(event.request)', worker)
        self.assertIn('self.skipWaiting()', worker)
        self.assertIn('self.clients.claim()', worker)
        self.assertIn('styles.css?v=13', html)
        self.assertIn('app.js?v=13', html)

    def test_client_contains_the_agreed_free_mvp(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")
        styles = (WEB / "styles.css").read_text(encoding="utf-8")

        for phrase in (
            "NOVA",
            "Бесплатно",
            "Искать рынок",
            "Рынок целиком",
            "Сохранённое",
            "Какой товар ищем?",
            "data-home-search",
            "prefers-reduced-motion",
        ):
            self.assertIn(phrase, script)
        self.assertIn("--yellow", styles)
        self.assertIn("@media (max-width: 470px)", styles)

    def test_client_uses_the_local_live_search_api(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")

        self.assertIn('fetch("/api/search"', script)
        self.assertIn("Подключитесь к локальному NOVA-серверу", script)
        self.assertIn("localStorage", script)
        self.assertIn("Рынок целиком", script)

    def test_search_errors_preserve_http_details_and_keep_503_out_of_offline_state(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")

        for phrase in (
            "function normaliseSearchError(error)",
            "function renderSearchError(error)",
            'kind: "http"',
            "status: response.status",
            "code: typeof data?.code === \"string\"",
            "message: typeof data?.message === \"string\"",
            'kind: "network"',
            "status: 0",
            'if (error.kind === "network")',
            "Локальный запуск",
            "if (error.status === 503)",
            "Поиск временно недоступен",
            "if (error.status === 400)",
            "Некорректный запрос",
            "if (error.status === 401)",
            "Откройте поиск через Telegram",
        ):
            self.assertIn(phrase, script)

        offline_state = script.split('if (error.kind === "network")', 1)[1].split('if (error.status === 503)', 1)[0]
        unavailable_state = script.split('if (error.status === 503)', 1)[1].split('if (error.status === 400)', 1)[0]
        self.assertIn("python tools/run_nova_web.py", offline_state)
        self.assertNotIn("python tools/run_nova_web.py", unavailable_state)
        self.assertIn("search_backend_unavailable", unavailable_state)
        self.assertIn("python -m pip install -r requirements.txt", unavailable_state)
        self.assertIn('data-action="search">Повторить', script)
        self.assertIn("${actions}", unavailable_state)

    def test_product_wizard_uses_auto_category_and_first_back_goes_home(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")

        for phrase in (
            'category: "manual"',
            "Товар или модель",
            "Бюджет или весь рынок",
            "необязательно",
            "returnView",
            "if (state.step === 1) state.view = state.returnView || \"home\";",
            'replace(/\\D/g, "")',
        ):
            self.assertIn(phrase, script)
        self.assertNotIn("Какой смартфон ищем?", script)
        self.assertNotIn("phoneMemory", script)
        self.assertNotIn('data-action="back" ${state.step === 1 ? "disabled" : ""}', script)

    def test_home_examples_rotate_between_supported_product_categories(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")
        styles = (WEB / "styles.css").read_text(encoding="utf-8")

        for phrase in (
            "const EXAMPLE_QUERY_SETS = [",
            "iPhone 17 Pro",
            "MacBook Air M4",
            "Телевизор Samsung 55",
            "Наушники Sony WH-1000XM5",
            "Монитор LG 27 4K",
            "Офисное кресло Cougar Armor",
            "function renderExampleQueries()",
            "function rotateExampleQueries()",
            "state.exampleSetIndex = (state.exampleSetIndex + 1) % EXAMPLE_QUERY_SETS.length",
            'data-action="rotate-examples"',
            'aria-controls="quick-query-examples"',
            'role="status"',
            "input.value = target.dataset.exampleQuery",
        ):
            self.assertIn(phrase, script)
        self.assertEqual(script.count("examples: ["), 3)
        self.assertIn(".quick-queries__items", styles)
        self.assertIn(".quick-queries__rotate", styles)
        self.assertIn(".quick-queries__status", styles)
        self.assertIn(".quick-queries__rotate span", styles)

    def test_model_clarification_keeps_the_query_escape_inside_its_template(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")

        self.assertIn('escapeHtml(`«${query}»`)} включает разные версии', script)
        self.assertNotIn('escapeHtml(`«${query}»)} включает разные версии', script)

    def test_telegram_mini_app_bridge_keeps_init_data_out_of_search_and_storage(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        styles = (WEB / "styles.css").read_text(encoding="utf-8")

        self.assertIn("telegram-web-app.js", html)
        self.assertIn("telegram-web-app-ready", html)
        self.assertIn('fetch("/api/telegram/session"', script)
        self.assertIn("body: JSON.stringify({ initData })", script)
        self.assertIn('fetch("/api/search"', script)
        self.assertIn("telegramBridge.prepare()", script)
        self.assertIn('window.addEventListener("telegram-web-app-ready"', script)
        self.assertIn("telegramBridge.syncBackButton", script)
        self.assertIn("telegram-context", styles)
        self.assertIn("Браузерный режим", script)
        self.assertIn("telegram-context--browser", styles)
        search_payload = script.split("function searchPayload()", 1)[1].split("async function runSearch", 1)[0]
        self.assertNotIn("initData", search_payload)
        self.assertNotIn("localStorage.setItem(\"nova:telegram", script)

    def test_broad_product_family_requires_a_model_or_article_before_price_comparison(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")

        for phrase in (
            "needsModelClarification",
            "NO_EXACT_MATCH",
            "Это линейка, а не один товар",
            "Добавьте артикул или номер модели",
            "Не сравниваем разные версии товара",
        ):
            self.assertIn(phrase, script)

    def test_timeout_result_has_a_distinct_recovery_state(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")

        for phrase in (
            "sourcesUnavailable",
            "sources_unavailable",
            "поиск не завершён",
            "Часть источников не ответила",
            "Повторить поиск",
            "Изменить поиск",
            "manualYandexSearchUrl",
            "Ручная проверка не становится рекомендацией NOVA",
        ):
            self.assertIn(phrase, script)

    def test_market_trend_is_rendered_only_when_the_api_supplies_a_safe_label(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")
        styles = (WEB / "styles.css").read_text(encoding="utf-8")

        for phrase in (
            "function renderMarketTrend(item)",
            "item?.marketTrend",
            'trend?.direction === "down" || trend?.direction === "up"',
            "if (!direction || !label) return \"\";",
            "result-card__trend",
        ):
            self.assertIn(phrase, script)
        self.assertIn(".result-card__trend", styles)
        self.assertIn(".result-card__trend--down", styles)
        self.assertIn(".result-card__trend--up", styles)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print("test_web_app: OK")
