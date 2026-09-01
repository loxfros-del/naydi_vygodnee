# Connect NOVA web search to Telegram safely

This ExecPlan is a living document. It follows `.agent/PLANS.md`; its Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective sections must be maintained while work proceeds.

## Purpose / Big Picture

After this work, a person can open NOVA in an ordinary browser or inside Telegram. In Telegram, the page recognises the Mini App context without exposing its signed launch data, asks the local NOVA server to validate that launch data, and then uses the same product-search request shape as the browser. The browser remains a first-class surface: it can still search locally, show source coverage honestly, and verify the visual flow. Search quality expands only through verified direct product pages and exact or compatible configurations; a phrase such as “ниже рынка” is shown only when independent current offers prove it.

## Progress

- [x] (2026-08-13 14:10Z) Audited the current browser PWA, localhost server, Telegram bot request flow, Search Engine V2 and source adapters.
- [x] (2026-08-13 14:15Z) Browser-tested the NOVA flow from home search through the summary for `MacBook Air M4` with no budget.
- [x] (2026-08-13 15:38Z) Added a safe Telegram Mini App session endpoint that validates signed launch data server-side and never stores or returns it.
- [x] (2026-08-13 15:40Z) Added the Mini App UI bridge with Telegram-safe areas, back button, theme support and a full browser fallback.
- [x] (2026-08-13 15:45Z) Expanded bounded discovery and exact technical configuration handling without accepting unverified external prices.
- [x] (2026-08-13 16:12Z) Added a conservative generic route for clearly named technical models such as `Canon EOS R50`; vague requests, accessories and repair remain unsupported.
- [x] (2026-08-13 15:52Z) Browser-tested ordinary-browser fallback and ran deterministic regressions.

## Surprises & Discoveries

- Observation: the current `tools/run_nova_web.py` server is explicitly localhost-only and does not persist a web request or start the Telegram bot.
  Evidence: it builds `SearchServiceV2` with `MemorySourceCache` and exposes only `/api/health` and `/api/search`.
- Observation: the bot already routes requests through `RequestService` and `SearchEngineBridge`, while the NOVA web endpoint currently invokes Search V2 directly.
  Evidence: `app/services/product_services.py` owns `RequestService`; `app/services/search_engine_bridge.py` owns bridge execution.
- Observation: the active local browser completed the product wizard for `MacBook Air M4`, then its live request exceeded the browser-control tool's default wait window.
  Evidence: the UI reached “Искать рынок”; the later automation call timed out while the server allows a bounded multi-source search.
- Observation: the local health endpoint reports `yandexWeb: sdk_missing`.
  Evidence: `GET /api/health` reports this safe readiness state, so broad external Yandex discovery cannot currently yield direct external product cards in this runtime.
- Observation: a single marketplace price is insufficient to establish a market low.
  Evidence: the search service now continues to bounded wide discovery until it sees priced comparable offers from at least two sources; external cards remain fail-closed until their direct product page is verified.

## Decision Log

- Decision: use a separate `POST /api/telegram/session` handshake containing Telegram `initData`; do not attach it to every search request and do not put it in browser storage.
  Rationale: signed launch data is sensitive. A short validation exchange gives the UI only a safe display context and preserves the existing browser search endpoint.
  Date/Author: 2026-08-13 / Codex.
- Decision: keep `tools/run_nova_web.py` as a local development server and avoid database/schema or `main.py` changes in this milestone.
  Rationale: the project rules prohibit those changes without an explicit production deployment decision. The Mini App interface can be built and tested locally before public hosting is chosen.
  Date/Author: 2026-08-13 / Codex.
- Decision: preserve a browser fallback when Telegram is absent.
  Rationale: the user explicitly wants the browser, and it is also the reliable surface for visual and live-search testing.
  Date/Author: 2026-08-13 / Codex.
- Decision: do not promise “absolutely any technology” or “below market” without evidence.
  Rationale: categories without model extraction, sources blocked by rate limits, and unverified snippets must be labelled honestly rather than converted into a recommendation.
  Date/Author: 2026-08-13 / Codex.

## Outcomes & Retrospective

Work in progress. The finished result will connect a Telegram Mini App session to the NOVA server without treating the browser as secondary. It will still need a public HTTPS deployment and a configured Telegram bot domain before Telegram can open it for real users. It will also need an authorised Yandex Search API runtime to discover and verify external stores beyond the marketplace adapters.

Completed 2026-08-13: NOVA now has a validated Mini App session handshake, a nonpersistent shared search boundary, a public-HTTPS-only launch button in the Telegram menu, and a browser UI that continues to work without Telegram. Search now treats direct external pages as a discover-and-verify path, keeps laptop configurations distinct, translates Russian condition tokens for search, and continues limited broad discovery when coverage has only one priced comparable source. The ordinary browser page was checked for its product prompt and horizontal-overflow regression; the automated test suite passed. Public deployment remains intentionally outside this local change because no HTTPS domain was supplied.

The technical-product route accepts only a recognised brand plus a concrete model code. It covers named devices outside the original category list without claiming support for arbitrary goods; cables, vague product descriptions and repair services still stop before network search.

## Context and Orientation

`web/index.html`, `web/app.js`, `web/styles.css`, and `web/service-worker.js` form the NOVA browser interface. `tools/run_nova_web.py` is its localhost HTTP server. `app/web_api.py` validates a small request payload, starts Search Engine V2 using only memory cache, and returns safe result cards. `app/services/product_services.py` and `app/services/search_engine_bridge.py` are the existing application-level Telegram request/search path; they must not be called from browser JavaScript.

A Telegram Mini App is a web page opened by a Telegram client. Telegram provides signed `initData` to its JavaScript bridge. The server must validate that signature using the bot secret before it treats the page as belonging to a Telegram user. The browser must never show, persist, log, or return the `initData` value.

Search Engine V2 lives under `app/search_v2/`. Its source adapters gather raw offers, normalisation extracts a product identity, exact matching rejects wrong configurations, and the page verifier proves a direct external page before it can contribute a price. An external result may be lower than a market median only if independent verified comparable offers establish that evidence.

## Plan of Work

First add a small application service for the Mini App session. It accepts one short `initData` string, parses and validates Telegram's HMAC signature using a bot token that is already available only through existing server configuration, rejects expired or malformed values, and returns a minimal profile such as `displayName`. It retains no session record. If configuration is unavailable locally, it returns a safe disabled state and the browser remains usable. The HTTP server exposes this service only through `POST /api/telegram/session`, with the same JSON size and no-store constraints as search.

Then update the browser UI. When `window.Telegram.WebApp` is present, it expands the Telegram viewport, sends the bridge's `initData` once to the session endpoint, and renders a small “Открыто в Telegram” context only after server confirmation. It never adds `initData` to local storage, URL parameters, result payloads, or error messages. If the handshake fails, it communicates that the person can continue in browser mode. The normal search form and response rendering remain the same in both modes.

Next improve the supported-technology boundary. Use an explicit generic technical category only when the parser has enough product-model evidence to create a safe comparable identity. Do not turn an arbitrary consumer-good phrase into an exact match. When initial covered sources are weak or unusually expensive, run the bounded broad discovery stage; accept external results only after direct-page validation. Maintain category-specific configuration facts such as screen, RAM, SSD, storage, condition, region and model number so near matches remain separate. Do not scrape protected pages, fake browser sessions, or bypass source rate limits.

Finally run deterministic tests, restart only the local NOVA process, and test the real page with the browser controller. Test the ordinary browser scenario fully. For the Mini App scenario, test the graceful non-Telegram fallback locally and use test fixtures for signed-session validation; real Telegram opening awaits deployment to an HTTPS domain attached to the bot.

## Concrete Steps

All commands run from `C:\Users\Пользователь\Documents\naydi_vygodnee`.

Run the deterministic checks:

    python tools/test_web_api.py
    python tools/test_web_app.py
    python tools/test_search_v2_normalization.py
    python tools/test_search_v2_exact_match.py
    python tools/test_search_v2_end_to_end.py
    python tools/test_alice_parser.py
    python -m compileall app web tools

Start or restart local NOVA only after confirming that port 8080 belongs to `tools/run_nova_web.py`:

    python tools/run_nova_web.py

Open `http://localhost:8080`. Enter `MacBook Air M4`, leave budget empty, select a priority, and start the market search. Expected behavior: cards show distinct RAM/SSD/display configurations, one source never claims the whole market, and no price is called savings unless the result contains evidence. Outside Telegram, the UI does not fail or require a token.

## Validation and Acceptance

The API test proves malformed or unsigned Telegram session data is rejected without leaking it; a valid deterministic fixture produces only a safe display name and platform marker. Browser UI tests prove `initData` is never written to local storage and browser mode remains available.

The product-search tests prove an article number distinguishes products, a laptop configuration without requested RAM/SSD is a compatible variant rather than exact, and a verified external direct page can affect a comparison while a failed/blocked page cannot.

Acceptance requires `GET /api/health` to return 200, ordinary browser NOVA to complete a product search without a Telegram context, and the page to visibly explain partial coverage rather than presenting a one-source price as the best market result.

## Idempotence and Recovery

All new session work is stateless. Restarting the local server clears only memory cache and cannot change Telegram updates, bot database, payments, credits, or schema. If Telegram settings are not configured, the session endpoint returns a safe unavailable result and the normal browser search continues. If a source is blocked or rate-limited, it supplies no price; retry later or use another verified source.

## Artifacts and Notes

Expected local health response:

    {"status":"ok","service":"nova-web","yandexWeb":"sdk_missing"}

The `sdk_missing` value is a readiness signal only. It is not surfaced as a secret or used to fabricate broad market coverage.

## Interfaces and Dependencies

The completed server contract includes:

    POST /api/telegram/session
    {"initData":"<Telegram signed launch data>"}
    -> {"telegram":true,"displayName":"..."}

or a safe 400/503 response with a customer-safe message. The handler must enforce the existing 16 KiB request limit and `Cache-Control: no-store`.

The browser contract uses `window.Telegram?.WebApp` only when it exists. It calls `ready()` / viewport expansion defensively and uses only a one-time `fetch('/api/telegram/session', ...)` with `initData`. It must not require Telegram for `POST /api/search`.

Change 2026-08-13: created because the project direction expanded from a browser-only NOVA prototype to a Telegram Mini App plus a browser-tested product-search surface.
