# Make NOVA a live web search for supported products

This ExecPlan is a living document. Its Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective sections must be updated whenever work advances. It follows `.agent/PLANS.md`.

## Purpose / Big Picture

The person using the yellow-and-black web app will see the product name NOVA, understand the value of the search before starting it, describe a product, optionally leave the budget blank, and receive the safe client cards returned by the existing Search Engine V2. The home page will become a full-width, animated product experience rather than a small static panel: its main question is “Какой товар ищем?”, it explains exact matching and market comparison, and its controls respond clearly on hover and click. The page will no longer pretend that local drafts are search results: when the local NOVA server is started, its search button calls a same-origin HTTP endpoint and renders only actual engine recommendations. A person can prove the connection by starting the server, opening `http://localhost:8080`, submitting a supported product such as `MacBook Air M4`, and seeing either live cards or an honest no-results state.

## Progress

- [x] (2026-08-13 10:00Z) Audited the static PWA, SearchServiceV2, source orchestrator, exact-phone checks, and the existing cache behavior.
- [x] (2026-08-13 10:15Z) Renamed the PWA to NOVA, added market and saved navigation, and made the budget optional with the visible value “Рынок целиком”.
- [x] (2026-08-13 10:25Z) Added `app/web_api.py` and `tools/run_nova_web.py`: a localhost-only JSON boundary over a memory-cached SearchServiceV2.
- [x] (2026-08-13 10:30Z) Replaced fake result cards with live cards, loading, no-result and server-unavailable states in the PWA.
- [x] (2026-08-13 10:40Z) Added API and PWA tests; browser-tested a blank-budget iPhone 16 Pro 256 GB eSIM request against live Search V2 output.
- [x] (2026-08-13 11:00Z) Recompose the NOVA home page into a large desktop-first phone-search experience with explanatory sections and accessible motion.
- [x] (2026-08-13 11:00Z) Replace the generic “Искать смартфон” call to action with the explicit question “Какой смартфон ищем?” and carry its text into the phone wizard.
- [x] (2026-08-13 11:00Z) Browser-check the redesigned landing page and rerun static PWA and project checks.
- [x] (2026-08-13 11:20Z) Expand phone-search coverage when a single expensive source is insufficient, and remove the English `new` token from Russian shopping queries.
- [x] (2026-08-13 11:20Z) Verify discovered external product pages before accepting their price, then prevent the web UI from calling a one-source result the best price in the market.
- [x] (2026-08-13 11:20Z) Add deterministic regressions for the broad fallback, page-price verification, and one-source client wording; run all Search V2 checks.
- [x] (2026-08-13 12:00Z) Make Yandex Search API a first-class web-discovery stage, update its SDK contract and region handling, and add a local readiness diagnostic that never exposes credentials.
- [x] (2026-08-13 12:20Z) Replace the web-only phone gate with a product request mode that lets the existing category parser recognise supported products from the entered text.
- [x] (2026-08-13 12:20Z) Remove phone-only wording and configuration steps from the PWA; keep an honest unsupported-category response for product types the engine does not yet recognise.
- [x] (2026-08-13 13:15Z) Prevent a broad product-family name from treating different article numbers as one exact comparable item.
- [x] (2026-08-13 13:35Z) Add Wildberries to the bounded web search path and validate its direct product URL before it can influence a result.
- [x] (2026-08-13 13:45Z) Keep laptop screen, RAM and SSD configurations separate; an unselected configuration is a compatible variant, not an exact item.
- [x] (2026-08-13 14:20Z) Add a read-only Telegram Mini App session validator and shared in-memory search boundary without changing bot handlers, `main.py`, SQLite or the database schema.
- [ ] (2026-08-13 13:50Z) Restore the authorised Yandex Web runtime in the selected deployment environment so verified external retailers can participate in live search.

## Surprises & Discoveries

- Observation: the current PWA is entirely static and deliberately contains no HTTP call.
  Evidence: `web/app.js` only reads and writes browser local storage; `web/README.md` starts `python -m http.server`.
- Observation: `SearchServiceV2` can search without changing `main.py`, but its default constructor opens `data/search_cache.sqlite3`.
  Evidence: `app/search_v2/service.py` creates a SQLiteSourceCache from `app/search_cache.DEFAULT_CACHE_PATH`.
- Observation: a blank `SearchRequestV2.budget` already means a market-wide search; the engine filters to in-budget results only when `budget` is present.
  Evidence: `app/search_v2/service.py` sets `include_over_budget = bool(normalized_request.budget)` and applies the filter inside `if normalized_request.budget`.
- Observation: the live search initially labelled a more expensive risky alternative “Дешевле, но с нюансами”.
  Evidence: a blank-budget iPhone 16 Pro 256 GB eSIM run returned 89 143 ₽ as BEST and 95 644 ₽ in the cheap role because the selector admitted every risky offer.
- Observation: the first NOVA landing page uses a maximum content width of 1180 px and allocates its visual emphasis to a mostly empty “Бесплатно” card.
  Evidence: `web/styles.css` sets `.app-shell` to `1180px`; the customer review and supplied 2048 px screenshot show excessive side space and no visible proof of the search process.
- Observation: a live iPhone 17 Pro 256 GB eSIM query can return a 150 217 ₽ Yandex Market card while the customer's Yandex shopping view contains a 75 399 ₽ external-store card.
  Evidence: the V2 query planner emits `Apple iPhone 17 Pro 256 ГБ eSIM new Ярославль`; a focused audit found the current Market adapter returns only its narrow search list, while the generic web fallback does not run after one exact Market hit.
- Observation: the current browser API labels any first recommendation “Лучший выбор”, even if it comes from only one source.
  Evidence: `app/web_api.py` maps `BEST_OVERALL` directly to that label and the response contains no market-coverage count.
- Observation: Yandex Search API was configured but unable to run under the active interpreter because `yandex_ai_studio_sdk` is absent.
  Evidence: the safe local `/api/health` response is `{"yandexWeb":"sdk_missing"}`; no credential values were read or emitted.
- Observation: the “Search indexes” UI in AI Studio is an uploaded-data/RAG feature, not a live web-shopping crawler.
  Evidence: its creation dialog requests uploaded files/chunks, while Yandex Search API is the documented service for the Yandex web index.
- Observation: Search Engine V2 already recognises twelve product categories, but the local web API rejected every category other than `smartphones` before it reached that parser.
  Evidence: `app/category_registry.py` contains laptop, TV, headphones, monitor, household-appliance and furniture specifications; `app/web_api.py` raised a phone-only validation error.
- Observation: a search for the DeLonghi Magnifica family treats `ECAM220.22.GB` and `ECAM21.117.SB` as exact until the request carries a concrete model identifier.
  Evidence: both unrelated ECAM offers inherited the family name as their identity; the lower 27 344 ₽ Wildberries article was never queried by the web service.
- Observation: the active local NOVA runtime reports `yandexWeb: sdk_missing`, so external Yandex Web discovery is safely skipped even though the adapter is wired into the search stage.
  Evidence: `GET /api/health` returns the safe readiness state without exposing configuration; a live MacBook query therefore used the narrow marketplace sources only.
- Observation: the current localhost search endpoint is safe for browser use but cannot identify a Telegram Mini App session.
  Evidence: before this milestone `tools/run_nova_web.py` accepted only `/api/search` and `app/web_api.py` deliberately knew nothing about Telegram.

## Decision Log

- Decision: use NOVA as the product name and retain the N mark.
  Rationale: it is a short English name that reads clearly in the existing visual system and implies a new view of the market.
  Date/Author: 2026-08-13 / Codex.
- Decision: provide a separate local server rather than altering `main.py` or exposing the Telegram bot as a web server.
  Rationale: it preserves the bot runtime and database schema while giving the PWA an explicit, testable boundary.
  Date/Author: 2026-08-13 / Codex.
- Decision: instantiate SearchServiceV2 with an in-memory source cache in the web server.
  Rationale: the web prototype must not modify any SQLite files; cache loss on server restart is safe for this local development stage.
  Date/Author: 2026-08-13 / Codex.
- Decision: show a blank budget as “Рынок целиком”, not as an error or an invented limit.
  Rationale: the central promise is finding the best real price even when the person has no predetermined maximum.
  Date/Author: 2026-08-13 / Codex.
- Decision: `CHEAP_WITH_RISK` must be strictly cheaper than the selected best result; a higher-priced risky offer is omitted from that role.
  Rationale: the visible role is a commercial claim and cannot contradict the price on the card.
  Date/Author: 2026-08-13 / Codex.
- Decision: keep the present product path explicitly focused on smartphones, because only that live backend flow is ready; make the entry point ask which smartphone is wanted instead of advertising a generic or static “search smartphone” action.
  Rationale: it gives the customer an immediately understandable next step without claiming that unfinished categories are live.
  Date/Author: 2026-08-13 / Codex.
- Decision: use CSS-native visual motion and browser-native `IntersectionObserver` reveal states rather than downloaded animation assets.
  Rationale: this keeps the localhost PWA lightweight, works offline once cached, and can be fully disabled through `prefers-reduced-motion`.
  Date/Author: 2026-08-13 / Codex.
- Decision: treat fewer than two independent priced exact sources as insufficient market coverage and always run broad external discovery before presenting the web result.
  Rationale: one expensive marketplace page cannot establish the lower edge of a market; broad discovery is necessary to find independent retailers such as the one visible in the customer's search.
  Date/Author: 2026-08-13 / Codex.
- Decision: an external page may contribute a price only after a bounded direct-page verification proves it is a product page and obtains the price from structured page data or a conservative price extractor.
  Rationale: a search-result snippet can be stale, refer to an installment payment, or point to an article; a lower price must not become a customer recommendation merely because it appears in a search snippet.
  Date/Author: 2026-08-13 / Codex.
- Decision: run Yandex web discovery as its own first stage in the NOVA web service, rather than hiding it behind marketplace coverage or generic fallback.
  Rationale: the user’s missing 75k store was visible through Yandex web/shopping discovery but absent from the narrow Market adapter; every discovered link remains fail-closed until its product page confirms the price.
  Date/Author: 2026-08-13 / Codex.
- Decision: do not create an AI Studio search index for live prices.
  Rationale: it would index manually uploaded/static documents and become stale; later it may help with a model-alias or trusted-seller knowledge base, but it is not a source of current offers.
  Date/Author: 2026-08-13 / Codex.
- Decision: the PWA sends `category=manual` for a product query, lets the established parser infer one of its supported categories, and does not send phone memory/SIM fields unless a future category-specific flow needs them.
  Rationale: a generic landing page must not pretend all products are phones, while automatic recognition keeps the established category-specific search rules and retains an honest refusal for unsupported goods.
  Date/Author: 2026-08-13 / Codex.
- Decision: a family name without a concrete numeric model/article identifier is a clarification state, not an exact product comparison.
  Rationale: `Magnifica Start ECAM220.22.GB` and `Magnifica S ECAM21.117.SB` are different machines, so their prices must not be shown as a cheaper or more expensive version of one item.
  Date/Author: 2026-08-13 / Codex.
- Decision: when a laptop request does not name RAM or SSD, label each found memory option as a configuration variant and expose its screen/RAM/SSD facts.
  Rationale: a 16/256 MacBook and a 16/512 MacBook are valid alternatives of the same generation but not the same price-comparison object.
  Date/Author: 2026-08-13 / Codex.
- Decision: validate `Telegram.WebApp.initData` server-side with the official HMAC construction, but keep the search request in memory and do not create a normal bot request from the web transport.
  Rationale: this proves that a Mini App is opened by a Telegram user without exposing the bot token, user ID or creating duplicate SQLite applications; the existing bot workflow remains unchanged.
  Date/Author: 2026-08-13 / Codex.

## Outcomes & Retrospective

NOVA is now a working local web product: a browser can request a supported product with no budget and receive actual Search V2 cards. The boundary does not modify Telegram or SQLite files, filters technical errors, scores and raw source diagnostics, and sends Yandex web discovery through direct-page price proof. The server also accepts a signed Mini App session at `/api/telegram/session`; its shared in-memory request service is available for authenticated Mini App search without calling handlers or writing the database. The current local environment still needs the declared `yandex-ai-studio-sdk` dependency installed before the configured Yandex source can make real requests; `/api/health` safely reports this as `sdk_missing`. The remaining product work is source-quality expansion, more category-specific forms where warranted, and a production-grade public deployment.

## Context and Orientation

`web/index.html`, `web/styles.css`, and `web/app.js` are the installable browser application. Its saved drafts are local browser data. `app/search_v2/service.py` is the async Search Engine V2 entry point. It uses source adapters to collect offers, rejects wrong configurations, groups equivalent products and chooses at most three recommendations. `app/search_v2/presentation.py` formats safe cards but the web endpoint needs structured JSON rather than Telegram text.

The new `app/web_api.py` will be a narrow adapter. It accepts only a small JSON request shape, calls SearchServiceV2, and emits titles, prices, direct links, client reasons, visible checks and search status. It must never return source exceptions, raw offers, scores, environment values or database details. `tools/run_nova_web.py` will be a local HTTP server that serves `web/` and delegates `/api/search` to this adapter. It is not a production deployment service.

## Plan of Work

First update the PWA copy and metadata from “найди выгоднее” to NOVA. Expand the bottom navigation to Home, Search, Market and Saved, and make the result page visually emphasize the price, ruble savings and direct link. Keep the interface Russian apart from the brand. Make the budget input optional. When it is empty, show “Рынок целиком” in the summary and send no budget field.

Then redesign `web/app.js` and `web/styles.css` without changing the API boundary. `renderHome()` will use a full-width hero with an actual form labelled “Какой смартфон ищем?”, quick examples, and a non-numeric visual illustration of the matching process. Submitting the form will prefill the existing phone-model wizard. Under the hero, add spacious sections that explain exact configuration, market comparison, and what a real result contains. Add anchors for these sections in the header and a final search call to action. Use semantic buttons and forms so every highlighted interaction remains keyboard reachable.

In `web/styles.css`, widen the desktop shell to approximately 1500 px, make the header stable during a long landing-page scroll, and add one coherent motion language: 180–240 ms hover feedback, a calm scanning line inside the visual illustration, and in-view appearance for explanatory blocks. Provide a `prefers-reduced-motion` rule that turns all nonessential animation and transform transitions off. Do not display invented live offers, fabricated prices, or fabricated savings in the illustration. Bump the PWA cache and shell query-string versions after changing the static files.

For the price-quality milestone, change `app/search_v2/query_planner.py` so a Russian shopping query uses the user-facing word `новый` while preserving the normalized hard condition token `new` for validation. In `app/search_v2/service.py`, count unique sources that produced a priced exact or compatible offer after discovery and anchor stages. If that count is below two, run the configured generic sources even when one exact result exists. The standard discovery result remains bounded; this is a second narrow fallback stage, not an unbounded crawler.

Add a web-only optional Yandex web-discovery adapter and page verifier to the service created by `app/web_api.py`. The adapter may return direct external product links only; the verifier must fetch at most a small bounded number of candidate pages with the existing HTTP timeout/rate-limit policy, reject non-product pages, and accept a price only when structured page data or safe visible price evidence supports it. No database, Telegram runtime, secret, or `.env` change is permitted. If the optional Yandex API is disabled or generic discovery is unavailable, that source returns empty and the web response says so by coverage, not by a false market claim.

Update `app/web_api.py` to calculate a client-safe count of independent priced exact sources. With one source, map “Лучший выбор” to a neutral exact-option label and tell the person that NOVA has not yet confirmed the whole market. With two or more sources, keep the normal recommendation wording. Sort safe web cards so a verified lower-price alternative is visible before a more expensive alternative; keep all source diagnostics internal.

Create `app/web_api.py`. Its `normalize_web_search_request(payload)` function validates scalar client fields, rejects oversized or malformed input, and maps phone fields into the existing legacy request shape: `category`, `product_name`, `budget`, `city`, `condition`, `priority`, `storage_gb`, and `category_details`. `search_web(payload, service=None)` asynchronously calls an injected or default memory-cached SearchServiceV2. `build_web_response(result)` serializes no more than three selected recommendations using safe role labels and `SavingsEvidence`; a response with no recommendations uses an honest user-facing message.

Create `tools/run_nova_web.py` with `ThreadingHTTPServer`. It must serve the `web` directory, expose `GET /api/health`, expose `POST /api/search`, restrict request bodies to 16 KiB, set JSON UTF-8 headers and never serve arbitrary paths outside `web`. Its V2 service must use `MemorySourceCache`, bounded source timeouts and no SQLite cache.

Update `web/app.js` to send the structured draft to `/api/search`, disable the search button while waiting, and render structured response cards. If opened through the old static server, it must say that the NOVA server needs to be started instead of showing demo results. Keep drafts as a local convenience, but use “Искать рынок” as the primary post-summary action. Version the service-worker cache after each web shell change.

Add `tools/test_web_api.py` using injected deterministic SearchResultV2 objects. It must prove that a no-budget phone request has `budget is None`, that exact phone fields arrive in the normalizer, that the returned object has only safe card fields and a saving reason, and that raw errors and scores never leak. Extend `tools/test_web_app.py` for NOVA, optional budget and live-search UI. Browser-check the no-budget field, the return navigation and the start state.

## Concrete Steps

All commands run from `C:\Users\Пользователь\Documents\naydi_vygodnee`.

Start the live local server after implementation:

    python tools/run_nova_web.py

Open `http://localhost:8080/api/health`; expect JSON with `{"status":"ok","service":"nova-web"}`. Open `http://localhost:8080`, complete a phone request without budget and choose “Искать рынок”. The page must show a loading state, then live cards or the honest no-results message. It must not show a payment prompt or a fixed invented price.

Run:

    python tools/test_web_api.py
    python tools/test_web_app.py
    python tools/test_search_v2_smartphones.py
    python tools/test_search_v2_savings.py
    python -m compileall .
    python tools/test_alice_parser.py

For price coverage, run the deterministic Search V2 tests. A fixture with one exact expensive marketplace offer and one exact lower external offer must call the generic fallback and retain both; a generic URL with an HTML `Product` JSON-LD price must receive that price only after verification. A one-source web response must not contain “Лучший выбор”.

For the visual milestone, open `http://localhost:8080` at a wide desktop viewport. The first fold must use most of the available width, show “Какой смартфон ищем?” instead of “Искать смартфон”, and reveal a working text field. Enter `iPhone 17 Pro`, press the primary button, and verify that the phone-model field in the next step contains the same text. Scroll the page: the “Как NOVA ищет” and configuration explanation sections must appear, and the motion must remain calm. Toggle the operating system reduced-motion preference if available; decorative scanning and reveal motion must stop.

## Validation and Acceptance

The acceptance scenario is a smartphone request for `iPhone 16 Pro`, `256 GB`, `eSIM`, “Только новый”, city `Москва`, no budget. The landing page begins with the concrete question “Какой смартфон ищем?” and carries an entered value into the wizard. It occupies the wide screen with useful explanatory content rather than leaving a blank right-hand card. The summary shows “Рынок целиком”. Search calls the local endpoint and returns no more than three cards that contain an item, price, store, direct link and safe explanation. It runs external discovery when initial exact coverage is only one source, and it never turns an unverified search snippet into a price. A one-source result is called an exact option, not the best market price. “Экономия N ₽” appears only when a `SavingsEvidence` object exists. A 128 GB, Pro Max, Dual SIM or used offer is not represented as the same item by the engine. If no live source succeeds, the user sees a clear retry/no-results state rather than a demo item.

## Idempotence and Recovery

The server process keeps only memory cache and browser drafts; it does not create or change `bot.db`, `*.db`, Telegram state or the production bot. Stop it with Ctrl+C. Restarting it is safe. If a source times out, retry the search; the endpoint returns a safe generic status. The static PWA still opens, but tells the person to start `tools/run_nova_web.py` before live searching.

## Artifacts and Notes

The web server intentionally remains localhost-only. A production release needs authentication, rate limiting, abuse controls, a source-policy review and a persistent API deployment before it can be made public.

## Interfaces and Dependencies

At completion the interfaces are:

    async def search_web(payload: Mapping[str, Any], service: SearchServiceV2 | None = None) -> dict[str, Any]
    def normalize_web_search_request(payload: Mapping[str, Any]) -> dict[str, Any]
    def build_web_response(result: SearchResultV2) -> dict[str, Any]

`tools/run_nova_web.py` serves:

    GET /api/health -> 200 JSON
    POST /api/search with an object body -> 200 JSON safe result, or 400 JSON validation error
    POST /api/telegram/session with signed Telegram initData -> 200 JSON session confirmation, or 401 JSON error
    POST /api/miniapp/search with signed Telegram initData plus search fields -> 200 JSON safe result, or 401 JSON error

Change 2026-08-13: created after the product direction changed from a static free PWA to NOVA, a local live interface for the existing search engine. Updated after live browser validation and the correction of the misleading expensive “cheap” role. Updated again after the customer reported that the landing page felt too small and visually static; the plan now specifies the full home-page visual milestone and a phone-first entry experience. Updated after the customer proved a 75 399 ₽ external iPhone result that the narrow Market-only scan missed; the plan now specifies broad fallback coverage, verified external prices, and honest one-source wording.

Change 2026-08-13: added a minimally scoped Telegram Mini App transport boundary. It validates signed init data, confirms a session without exposing an ID, and can run the existing non-persistent web search; it intentionally does not create or alter Telegram bot applications until a separately authorised persistent product flow is needed.
