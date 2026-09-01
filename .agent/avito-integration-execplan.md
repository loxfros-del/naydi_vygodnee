# Safely connect Avito offers to NOVA

This ExecPlan is a living document. Its Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective sections must be updated whenever work advances. It follows `.agent/PLANS.md`.

## Purpose / Big Picture

After this work, NOVA will be able to show qualifying Avito listings alongside retail offers without pretending that a private used listing is the same thing as a new item sold by a store. A person searching in a city will see an Avito card only when its product model, city, condition, direct listing URL, price, and seller facts have passed the defined checks. The card will always say that it is an Avito listing, whether it is new or used, and what must be checked before payment. A new retail offer remains the reference price for “below market”; an Avito used offer is compared only with other used listings of the same model.

The browser and Telegram Mini App will continue to call the same Search V2 service. They will not have separate Avito implementations. The implementation must use an officially authorised Avito data interface if one is available for the required buyer-search scope. It must not bypass anti-bot protection, use cookies from a user browser, rotate proxies, or scrape hidden endpoints.

## Progress

- [x] (2026-08-13 16:55Z) Audited the existing legacy and Search V2 Avito paths.
- [ ] Confirm the exact official Avito API product, agreement and permissions that allow public buyer listing search for this project.
- [ ] Build the isolated official Avito source client and a feature-gated Search V2 adapter.
- [ ] Add listing verification, separate new/used market comparisons, and customer wording.
- [ ] Connect the shared results to the browser and Telegram Mini App, then run a controlled pilot.

## Surprises & Discoveries

- Observation: Avito is already present in Search V2, but it is not an Avito API integration.
  Evidence: `app/search_v2/adapters/avito.py` subclasses `SiteExactSearchBridge`, which appends `site:avito.ru` and calls `GenericSearchAdapter` through `generic_web_legacy_search` in `app/search_v2/adapters/base.py`.

- Observation: the web search already preserves a city only for Avito queries, while national stores omit it.
  Evidence: `app/search_v2/query_planner.py` appends `request.city` only when `source == "avito"`.

- Observation: the system already treats Avito as a classified marketplace and adds risks for private sellers, few reviews and new accounts.
  Evidence: `app/search_v2/risk_engine.py` identifies classified/Avito offers and adds `PRIVATE_SELLER`, `FEW_SELLER_REVIEWS`, and `NEW_SELLER_ACCOUNT` risks.

- Observation: automatic browser verification is deliberately disabled for legacy Avito rows.
  Evidence: `app/playwright_verifier.py` has `max_candidates: 0` for `avito_search`; therefore present results require manual-safe handling rather than a claim of page verification.

- Observation: Search V2 currently ranks Avito inside the same source budget as retail sources.
  Evidence: `app/search_v2/source_policy.py` includes `avito` in `DISCOVERY_CORE`, which is capped to three adapters per search.

- Observation: public Avito Business API documentation confirms OAuth and account-item APIs, but does not document a buyer-search endpoint for all active third-party listings.
  Evidence: [Item API catalogue](https://developers.avito.ru/api-catalog/item/documentation), [OAuth documentation](https://developers.avito.ru/api-catalog/auth/documentation), and [Public API rules](https://www.avito.ru/legal/rules/public-api/). This must be confirmed in writing with Avito before implementation.

## Decision Log

- Decision: do not attempt to obtain Avito data by scraping, CAPTCHA bypass, private endpoints, browser cookies, or proxy rotation.
  Rationale: this is unstable, violates the desired trust model, and could expose users or the project to blocks and account risk.
  Date/Author: 2026-08-13 / Codex.

- Decision: keep Avito as a separate market lane: `new-professional`, `new-private`, and `used` must never be mixed into one market median or saving claim.
  Rationale: a 70,000 ₽ used phone is not a lower market price for a new phone. The buyer needs both price and condition to make an honest decision.
  Date/Author: 2026-08-13 / Codex.

- Decision: the official adapter is optional and feature-gated until Avito confirms required API rights.
  Rationale: typical seller APIs may expose only the project owner’s own listings/messages, not a public buyer-search catalogue. We must prove rights before writing credentials or enabling live traffic.
  Date/Author: 2026-08-13 / Codex.

- Decision: browser NOVA and Telegram Mini App consume the same `SearchServiceV2` response.
  Rationale: two search engines would drift in matching, risk assessment and customer promises.
  Date/Author: 2026-08-13 / Codex.

- Decision: until Avito grants buyer-search access, ship only an explicit "Искать на Авито" deep link and do not ingest Avito listings into recommendations.
  Rationale: it is useful to the buyer immediately, does not claim unavailable coverage, and avoids unapproved collection of marketplace data.
  Date/Author: 2026-08-13 / Codex.

## Outcomes & Retrospective

Not implemented yet. The current system can discover Avito links through web search, but that is discovery only. It must not be presented as a complete Avito catalogue or as a verified lowest price. This plan adds a controlled path from legal data access to safe customer cards.

## Context and Orientation

NOVA has two interfaces: the browser PWA in `web/` served by `tools/run_nova_web.py`, and the Telegram bot/Mini App. Both should invoke `app/search_v2/service.py` through the request boundary in `app/web_api.py` or `app/services/web_request_service.py`.

An adapter is a small module that obtains offers from one source and returns `RawOffer` objects. It must not decide whether an offer is good. The existing conversion in `app/search_v2/normalization.py`, exact-match gate in `app/search_v2/exact_match.py`, risk engine in `app/search_v2/risk_engine.py`, grouping in `app/search_v2/grouping.py`, and recommendation selector in `app/search_v2/recommendations.py` make those decisions after the adapter returns data.

The current `AvitoAdapter` in `app/search_v2/adapters/avito.py` is a discovery bridge, not an official API client. It can find a direct-looking link using general web search but often lacks price, seller rating, condition, delivery, listing freshness and reliable availability. Keep it enabled only as `avito_web_discovery` during migration, never as an authoritative API source.

Create a new source name `avito_official` rather than replacing `avito` in place. This lets the project compare the old discovery route and the new authorised route in logs and tests. Once the official route is proven, the old bridge can be demoted to fallback discovery or disabled.

## Plan of Work

### Milestone 1 — Confirm legal data access before code

Create an Avito developer/business application and obtain written confirmation that its API agreement permits searching public listings for a buyer-assistance product. The currently documented Item API concerns authenticated account inventory; it must not be assumed to search the whole marketplace. Record the exact endpoint, allowed categories, pagination limit, rate limit, regional parameter, fields supplied, and whether the licence allows storing prices and images. Do not put credentials into source code, screenshots, tests or this plan. Add only names for future environment variables to `.env.example` after confirmation: `AVITO_API_ENABLED`, `AVITO_CLIENT_ID`, `AVITO_CLIENT_SECRET`, `AVITO_TIMEOUT_SECONDS`, and `AVITO_MAX_RESULTS`.

Acceptance is a short internal record with an endpoint example using a non-secret test account and a written answer to this question: “Can this project search public listings beyond its own seller account?” If the answer is no, stop here: retain only the existing link discovery path, add the explicit deep link in Milestone 1a, and show it as manual verification. Do not implement automation around an unauthorised endpoint.

### Milestone 1a — Useful, no-data Avito link

Before API approval, add a browser/Mini App button labelled `Искать на Авито`. It opens Avito with the user's exact product query and city where a safe public URL can be formed. It is a convenience link, not a NOVA result: do not read back listings, do not show a price, do not say "ниже рынка", and do not collect login, contact, cookie, or account information. Add a browser UI test confirming that the link is visibly marked as external and has no recommendation role.

### Milestone 2 — Isolated official API client

Create `app/sources/avito_official_source.py`. It will own OAuth token acquisition, expiry-aware in-memory token caching, one bounded request method, rate-limit handling, and conversion-free JSON validation. “In-memory” means the token disappears when the process stops; do not store it in SQLite.

The source client returns a small transport structure with only fields permitted by the contract: listing ID, title, direct URL, price/currency, city or region ID, category, condition, seller type, seller name/ID where permitted, rating/review count where permitted, account age or verification where permitted, delivery text, published/updated timestamp, image URL, and source retrieval time. It must reject malformed rows individually, preserve valid rows, respect `Retry-After`, and map 401, 403, 429, timeout, and bad JSON to existing `SourceStatus` values. It must never retry a blocked response aggressively.

Add `tools/test_avito_official_source.py` with mocked HTTP responses. Cover token reuse, expired token refresh, 401 once then re-authentication, 429 without retry loop, pagination cap, malformed listing isolation, and no credential text in error data.

### Milestone 3 — Search V2 adapter and query contract

Create `app/search_v2/adapters/avito_official.py`. It implements `SourceAdapter` directly, has `name = "avito_official"`, `platform = "Avito"`, and `SourceCapabilities(kind="structured", structured_endpoint=True, supports_city=True, supports_condition=True)`. It receives the existing `SearchRequestV2` and `SourceQuery`, passes an actual Avito region/city parameter rather than appending a city into arbitrary text, and returns only `RawOffer` records from the official client.

Extend `app/search_v2/source_registry.py` with `avito_official` only when `AVITO_API_ENABLED` and complete non-empty credentials are present. Keep `avito` registered as web discovery for the transition. Update `app/search_v2/source_policy.py` so official Avito gets a distinct priority stage after the two national retail anchors for new-product searches, and can be first-class for a request explicitly marked used. The existing three-adapter cap must be retained; make the policy choose sources based on condition rather than adding an unbounded fourth request.

Extend `app/search_v2/query_planner.py` to carry structured city information in `SourceContext.metadata["city"]` or a dedicated data field for `avito_official`; do not inject the city into the query text for this source once the API supplies a real region parameter. If the user’s city cannot map to an official region, return `EMPTY`/a client-safe “город не сопоставлен” state, not an all-Russia claim.

Add deterministic adapter tests using a fake Avito source response. Prove that a URL such as `https://www.avito.ru/yaroslavl/telefony/iphone_16_123456789` is accepted only as a direct listing, a search/category URL is rejected, and a city mismatch is rejected before recommendation.

### Milestone 4 — Exact match, market lanes, and fraud-safe risks

Extend `ProductIdentity`/offer metadata only if required fields are absent. Record `condition`, `seller_type`, `seller rating/reviews`, `seller verification`, `listing age`, `delivery`, `city`, and an `avito_listing_id`. Do not infer a fact that the API did not provide.

In `app/search_v2/exact_match.py`, require concrete model plus every user-selected storage/RAM/SIM/condition field. An Avito listing with missing condition remains `GENERIC_MATCH`/manual check; it cannot become a new-item exact match. Existing `USED_ITEM` and `REFURBISHED_ITEM` risk flags remain mandatory.

In grouping/market-stat logic, add an explicit lane key: `condition + seller_kind`. The reference price for a new professional Avito listing may be compared with other new professional offers of the same model; used listings form their own lane. A saving label requires at least three priced comparable listings from at least two independent sources in that same lane. If unavailable, show “цена ниже среди найденных объявлений” only when this statement is true and include “не является ценой нового рынка.”

Expand `app/search_v2/risk_engine.py` with bounded rules: private seller; seller score/reviews below threshold; account younger than the agreed threshold; no delivery; prepaid-only terms if supplied by the API; unusually low price measured only within the matching lane; and stale listing. A high-risk listing can be returned as `CHEAP_WITH_RISK` or manual candidate, never as a blanket `BEST_OVERALL` for a new retail request.

Add unit tests in `tools/test_search_v2_risks.py`, `tools/test_search_v2_exact_match.py`, and `tools/test_search_v2_recommendations.py`. Required cases: same model/new/professional; same model/used/private; wrong storage; wrong city; too-cheap private listing; stale listing; only one Avito row; and safe professional listing with multiple comparable offers.

### Milestone 5 — Browser, Mini App, and Telegram result presentation

Update `app/web_api.py` so a direct Avito card includes only client-safe fields and a visible source/condition label. In `web/app.js`, distinguish “Новый у продавца на Авито”, “Б/у на Авито”, and “Нужно проверить”. Do not reuse “Лучший выбор” or “ниже рынка” for a used/private offer without lane evidence. Make “Открыть объявление” a normal external link; no checkout, payment, contact data, or message automation is handled by NOVA.

Use the existing Mini App session and search routes in `tools/run_nova_web.py` and `app/services/telegram_miniapp.py`; do not add a Telegram-specific Avito scraper. In the bot result formatter, use the same source and risk labels. Add browser/API tests proving that internal errors, OAuth failures, seller IDs, access tokens and raw API diagnostics never reach a user response.

### Milestone 6 — Controlled rollout and measurement

Deploy with `AVITO_API_ENABLED=false` first. Run a shadow mode that executes authorised requests for a small opt-in cohort but does not alter customer recommendations; log only aggregate status, category, city match, number of listings, price range, exact-match result, risk codes, and latency. Do not log message text, tokens, phone numbers or private seller contact data.

Promote to visible cards only after a review sample verifies direct listing URLs, city accuracy, exact model matching, condition separation, and no false “below market” labels. Start with smartphones and laptops, then add categories after ten or more manually reviewed searches per category. Define rollback as setting `AVITO_API_ENABLED=false`; the existing browser and Telegram searches continue without Avito official results.

## Concrete Steps

Run all commands from `C:\Users\Пользователь\Documents\naydi_vygodnee` after each milestone:

    python tools/test_avito_official_source.py
    python -m unittest tools.test_search_v2_adapters tools.test_search_v2_query_planner tools.test_search_v2_exact_match tools.test_search_v2_risks tools.test_search_v2_recommendations tools.test_search_v2_end_to_end
    python tools/test_web_api.py
    python tools/test_telegram_miniapp.py
    python tools/test_alice_parser.py
    python -m compileall app web tools
    git diff --check

For a local manual check after test fixtures are in place, start the browser server with `python tools/run_nova_web.py`, open `http://localhost:8080`, choose a specific product and city, and confirm that a used Avito card says “Б/у на Авито” and does not have a new-market saving label. Then open the same Mini App route through Telegram after a public HTTPS deployment and confirm the card text and links are identical.

## Validation and Acceptance

The feature is accepted only when all of the following are observable:

- An authorised official response produces a direct Avito card with the correct city, condition, direct URL and no exposed credentials.
- A search URL, category URL, malformed response, wrong city, wrong model, or missing condition cannot become a purchasable recommendation.
- A used private 70,000 ₽ phone is not described as “below the new market” next to a new 100,000 ₽ retail phone.
- A new professional Avito listing may be shown only after exact-model and condition checks; it includes seller/guarantee caveats when those facts are absent.
- Disabling the feature flag removes official Avito cards without breaking normal Yandex Market, Ozon, browser, bot, or Mini App searches.
- All listed tests pass, `git diff --check` is clean, and `python tools/test_alice_parser.py` passes after every parser-related edit.

## Idempotence and Recovery

The source client must make no database migrations and must cache OAuth tokens only in memory. Repeating tests must use mocked HTTP and leave no remote listings changed. If a source is rate-limited, blocked, or loses authorisation, return a bounded status and retain the non-Avito search result. To roll back production behavior, set `AVITO_API_ENABLED=false` and restart the application; do not delete data or modify the SQLite schema.

## Artifacts and Notes

The existing discovery adapter remains useful as a manual discovery path during legal/API review:

    app/search_v2/adapters/avito.py
    class AvitoAdapter(SiteExactSearchBridge)

The future official adapter must be deliberately separate:

    app/search_v2/adapters/avito_official.py
    class AvitoOfficialAdapter(SourceAdapter)

The public client contract must never include raw errors, OAuth tokens, seller contact information, cookies, score internals, or unverified snippet prices.

## Interfaces and Dependencies

`app/sources/avito_official_source.py` must expose a narrow asynchronous interface similar to:

    class AvitoOfficialClient:
        async def search_listings(self, *, query: str, city_id: str, condition: str | None, limit: int) -> SourceResultPayload: ...

`app/search_v2/adapters/avito_official.py` must expose:

    class AvitoOfficialAdapter(SourceAdapter):
        name = "avito_official"
        platform = "Avito"
        capabilities = SourceCapabilities(
            kind="structured",
            structured_endpoint=True,
            supports_city=True,
            supports_condition=True,
        )

`SourceResultPayload` is an internal dataclass containing only validated listing rows, source status, bounded error category, response duration, and optional retry delay. It must not contain secret values. The adapter maps those rows into `RawOffer`; all matching and ranking remains downstream in the existing Search V2 pipeline.

Plan created 2026-08-13: initial safe design and implementation sequence for Avito.
