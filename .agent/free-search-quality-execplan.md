# Make the free search prove real savings

This ExecPlan is a living document. The sections Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective must be kept up to date as work proceeds. It follows .agent/PLANS.md.

## Purpose / Big Picture

The free launch succeeds only if a person can ask for an item and quickly receive a choice they trust: the exact product they asked for, currently available from a suitable seller, at a price that is demonstrably better than the usual price. The client should see a concrete result such as “экономия 5 000 ₽ относительно типичной цены 63 990 ₽”, not an internal score, a vague percentage, or a long unranked list of links.

After this work, every displayed saving will be backed by a stored set of comparable current offers. If that evidence is insufficient, the product must honestly say “цена проверена”, rather than inventing a market comparison. A person can verify the result in the Telegram bot now and in the PWA after the API milestone.

## Progress

- [x] (2026-08-13 08:00Z) Audited the V2 search pipeline, market-analysis module, recommendation selector, safe presentation module, and deterministic tests.
- [x] (2026-08-13 08:45Z) Replaced the V2 client reason “итоговый балл” and a raw percentage with a ruble saving relative to the median of three or more verified comparable offers.
- [x] (2026-08-13 09:30Z) Made smartphones the reference category: the web wizard records model, memory, SIM/region and condition; V2 rejects a wrong memory, model or SIM version and keeps different versions in separate price groups.
- [x] (2026-08-13 09:45Z) Extracted savings into `SavingsEvidence`: the selected offer is excluded from its baseline and the claim requires three other current, independent direct offers from at least two sources.
- [x] (2026-08-26) Added 30 golden end-to-end search cases: five each for phones, laptops, TVs, headphones, monitors, and chairs. Every cheaper wrong variant is rejected before grouping and TOP-1.
- [x] (2026-08-26) Made source capabilities explicit, added bounded Yandex web discovery and shared direct-page verification, and validated adapter/planner behavior separately.
- [x] (2026-08-26) Added shadow diagnostics, an anonymized 30-case rollout gate, deterministic canary routing, and instant legacy fallback/rollback.
- [x] (2026-08-26) Ran a no-database live Yandex V2 smoke: 10 discoveries in 1.7 seconds, two exact direct pages with verified prices, and one recommendation; seller/stock uncertainty correctly remained manual.
- [ ] Collect a real, consented 30-request shadow sample before changing the production flag; this is an operational rollout gate, not a code task.
- [x] (2026-08-26) Exposed client-safe savings evidence and evidence-gated market trends through the API/PWA without raw diagnostics.

## Surprises & Discoveries

- Observation: the V2 pipeline already groups offers by product configuration, computes a median from comparable offers, applies risks, ranks offers, and selects up to three roles.
  Evidence: app/search_v2/service.py calls group_offers, analyze_product_groups, apply_risks, rank_offers and select_recommendations.
- Observation: the V2 market baseline already rejects out-of-stock offers, non-exact models and weak price evidence.
  Evidence: app/search_v2/market_analysis.py function is_comparable_offer requires an exact or compatible variant, an available offer and price confidence at least 0.6 or verified price.
- Observation: client presentation previously received an internal numeric score as a reason, while the market deviation was only a percent.
  Evidence: app/search_v2/recommendations.py before the 2026-08-13 change emitted “итоговый балл” and “цена X% ниже медианы”.
- Observation: V2 is present beside legacy and is not the production default.
  Evidence: docs/search_v2_architecture.md states that the default production mode is legacy and the bridge owns the engine choice.
- Observation: the web wizard used a generic product step and its first “Назад” button was disabled, while the V2 category registry calls the phone category `phone` and the product wizard calls it `smartphones`.
  Evidence: web/app.js and app/search_v2/request_normalizer.py inspected on 2026-08-13.
- Observation: PWA cache-first delivery kept an old web shell after the local files changed.
  Evidence: browser check initially showed the former 200 ₽ content although localhost served the new free app.js; the service worker is now versioned and network-first for same-origin requests.
- Observation: the generic modifier extractor treated “Mini LED” as a competing `Mini` model variant and rejected an exact Xiaomi G Pro 27i monitor.
  Evidence: the 30-case golden matrix found the regression; `tools/test_search_v2_normalization.py` now proves display technology cannot create a model conflict.
- Observation: unknown retailer URL shapes from Yandex were marked non-product before the direct-page verifier could inspect them.
  Evidence: the first live V2 smoke retained zero offers; after allowing only verifier-required sources through the bounded verifier, the same bounded check retained two exact page/price-verified offers and produced one recommendation.

## Decision Log

- Decision: define product success as a proven saving on the same purchasable item, not merely the lowest visible price.
  Rationale: a lower price is worthless if it belongs to another memory size, condition, region, unavailable listing or unverifiable seller.
  Date/Author: 2026-08-13 / Codex.
- Decision: show a saving only when the comparison pool is sufficiently strong; otherwise show verified facts without a saving claim.
  Rationale: this prevents false promises on sparse, mixed or stale markets.
  Date/Author: 2026-08-13 / Codex.
- Decision: retain exactly three customer roles at most: best choice, cheaper option with an explained compromise, and reliable alternative.
  Rationale: more options shift the analysis work back to the customer and weaken the central promise.
  Date/Author: 2026-08-13 / Codex.
- Decision: improve V2 in shadow mode before replacing legacy delivery.
  Rationale: the existing bot must continue to deliver reviewed results while the new engine is measured on identical requests.
  Date/Author: 2026-08-13 / Codex.
- Decision: use smartphones as the first quality reference rather than letting a lower-priced 128 GB, Pro Max or another SIM/region version compete with the requested phone.
  Rationale: these variants look similar in a search result but are different purchasable products; the customer must never be shown a false saving from a cheaper configuration.
  Date/Author: 2026-08-13 / Codex.
- Decision: a first-step back action returns to the screen that opened the wizard, not unconditionally to the home screen.
  Rationale: a person who started a new request from “Мои заявки” expects to return there.
  Date/Author: 2026-08-13 / Codex.
- Decision: verify no more than six best direct-page candidates per search, after hard mismatch and non-product URL rejection.
  Rationale: Yandex and search-card discovery must improve coverage without opening an unbounded number of pages or spending verification budget on obviously wrong products.
  Date/Author: 2026-08-26 / Codex.
- Decision: production rollout requires a 30-case shadow aggregate with exact and fully verified TOP-1 in every case, no unsafe candidate, and no system error.
  Rationale: canary percentage is an operational safety control, not evidence that the engine is ready.
  Date/Author: 2026-08-26 / Codex.

## Outcomes & Retrospective

The implementation is now covered by a 30-case, six-category golden matrix, honest source capabilities, bounded Yandex discovery, direct-page proof, shadow comparison, and deterministic canary rollback. Client savings remain evidence-gated and the API/PWA expose no internal score. Production remains on its existing mode until a real consented shadow sample passes the new aggregate rollout gate.

## Context and Orientation

The current Telegram MVP accepts six automatic categories: smartphones, laptops, televisions, headphones, monitors and office chairs. Product configuration lives in app/product_config.py. The current user path and safety rules are in docs/product_flow.md. Do not change main.py, .env, bot.db, SQLite schema, payment history or credits for this work.

Search Engine V2 is a domain layer isolated in app/search_v2. An Offer represents one seller’s offer. ProductIdentity describes its model and configuration. ProductGroup is one identity with offers from different sellers. A comparable offer is an offer that matches the requested model and configuration, is in stock, and has usable price evidence. MarketStats holds minimum, median and maximum price figures for a ProductGroup. A recommendation is a selected offer with one of three client roles. The bridge in app/services/search_engine_bridge.py selects between the legacy and V2 engines and is the only runtime boundary allowed to switch production behavior.

The legacy market-analysis module in app/market_analysis.py contains similar concepts for existing data. Do not combine its loose legacy rows with V2 Offer objects. Each calculation must stay within one ProductGroup and one engine representation.

## Plan of Work

### Milestone 1: Make every saving claim auditable

Create app/search_v2/savings.py with a frozen SavingsEvidence value containing baseline price, selected price, saving rubles, saving percent, comparable offer count, source count and an explanation safe for clients. Its constructor accepts one selected Offer and the offers of the same ProductGroup. It rejects the claim unless the selected offer is comparable, the selected offer has a current direct link, and at least three other comparable offers from at least two sources remain after removing duplicate seller and URL identities. The reference prices must have been retrieved within the freshness window chosen by the source policy.

Use the median of those reference prices, not the price of the selected offer, as the baseline. This prevents a cheap offer from making its own baseline cheaper. Round saving rubles only at presentation time. Show a saving only when it is greater than zero. The client-safe explanation has the form “экономия 5 000 ₽ относительно типичной цены 63 990 ₽”; it must never mention score, source adapter, confidence code or raw error.

Replace the temporary calculation in app/search_v2/recommendations.py with this value. Keep the exact-model explanation and role-specific explanation after the saving line. Update app/search_v2/presentation.py only if it needs a separate visible “Выгода” line; no Telegram handler may calculate savings itself.

Write tools/test_search_v2_savings.py. It must prove that different storage, condition or model cannot form one baseline; duplicate seller URLs do not increase evidence; two sources or fewer suppress the claim; a stale offer suppresses the claim; and four independent comparable offers generate the exact expected ruble saving. Extend tools/test_search_v2_recommendations.py to prove no reason exposes internal score.

### Milestone 2: Measure whether search finds the right item

Create data/search_quality_cases.json and tools/test_search_quality_cases.py. Each fixture contains a normalized user request, accepted exact identities, required attributes, unacceptable variants, a set of deterministic offers and the expected best role. Start with at least five fixtures for each of the six supported categories. Include product names where the only difference is storage, display size, generation, condition, region, warranty or bundle.

The test feeds fixtures through request normalization, exact matching, grouping, market analysis, risk evaluation, ranking and selection without network calls. It records exact-match precision, rejected wrong-configuration count, top-one correctness, count of valid client roles and saving-evidence coverage. A fixture fails if a wrong model becomes BEST, an unavailable listing becomes a recommendation, a non-comparable price contributes to saving, or more than three offers are returned.

Initial acceptance thresholds are: every golden case rejects its listed wrong variants; 100 percent of BEST selections have exact model and configuration; 100 percent have price, direct link and availability verification; and every displayed saving has SavingsEvidence. The test report must name every failed case so improvements are driven by examples, not intuition.

### Milestone 3: Broaden evidence safely

The current adapters are in app/search_v2/adapters and their contract is documented in docs/search_v2_source_contract.md. Add one source at a time. An adapter only returns raw facts; it never selects a role or assigns trust. For each source, add fixtures covering an exact product, a wrong configuration, unavailable listing, discount with hidden condition, missing seller facts and a blocked response. The orchestrator must tolerate a failed source and retain partial results without fabricating facts.

Prioritize sources that provide a current direct product page, item price, availability, seller identity and canonical model data. Do not add a source merely because it returns many links. A source becomes eligible for recommendations only after its deterministic adapter tests, exact-match fixtures and a limited live smoke pass.

### Milestone 4: Prove the engine against the current path

Run V2 through the existing shadow comparison path for a fixed sample of real, consented requests across all six categories. Save only safe snapshots already supported by the V2 snapshot store. For each case compare: exact product selection, usable direct link, verified price, number of source failures, speed, market evidence and the human reviewer’s chosen item. Do not turn on V2 globally while any observed wrong-model or false-saving regression remains unresolved.

When V2 wins or matches the legacy result for the agreed sample and meets the golden suite thresholds, enable it through the bridge feature flag for a small percentage of new requests. Keep a one-click rollback to legacy in the same bridge. Record the decision and evidence in this plan.

### Milestone 5: Make the value visible in the product

When the API milestone begins, return only the client-safe recommendation cards and SavingsEvidence explanation. The PWA result card should visually prioritize: item, current price, “экономия N ₽” when evidence exists, store and one concise reason. Risks remain secondary checks, not the headline. If no saving evidence exists, the PWA must say “цена проверена” and not imply a discount.

## Concrete Steps

All commands run from C:\Users\Пользователь\Documents\naydi_vygodnee.

For the current completed increment run:

    python tools/test_search_v2_recommendations.py
    python tools/test_search_v2_presentation.py
    python tools/test_product_ui.py
    python tools/test_web_app.py

For the next milestone run after adding the domain calculation:

    python tools/test_search_v2_savings.py
    python tools/test_search_v2_recommendations.py
    python tools/test_search_v2_market_analysis.py
    python tools/test_search_quality_cases.py

Before any change to the Gemini or Alice parser, run:

    python tools/test_alice_parser.py

At every completed search milestone run:

    python -m compileall .

## Validation and Acceptance

A human reviewer must be able to take a winning card and answer three questions from saved evidence: Is it exactly the requested configuration? Is the listed price and availability currently verified? What independent comparable offers establish the stated saving? If any answer is missing, the client cannot see “экономия N ₽”.

The user-visible acceptance scenario is: request a particular configuration with a budget, receive no more than three cards, see the best card’s current price and a ruble saving only when evidence exists, open a direct item link, and understand in one sentence why it is the best purchase. The result does not expose score, confidence, adapter names, captcha, raw source errors, rejected offers or internal diagnostics.

## Idempotence and Recovery

All quality tests use fixture data or temporary snapshots and must not open or modify bot.db. New calculations are pure functions. Feature-flag rollout is reversible by selecting legacy in the bridge; do not delete existing search data or rewrite SQLite rows. A failed source adapter is disabled independently and must not block other sources from producing a partial, honestly labelled result.

## Artifacts and Notes

The first completed V2 saving test proves this output:

    economy 10 000 ₽ relative to typical price 80 000 ₽

from three verified offers priced 70 000 ₽, 80 000 ₽ and 90 000 ₽. This is a transitional proof. The final SavingsEvidence milestone tightens the baseline by excluding the selected 70 000 ₽ offer and requiring three independent reference offers.

## Interfaces and Dependencies

Milestone 1 introduces:

    app.search_v2.savings.SavingsEvidence
    app.search_v2.savings.build_savings_evidence(selected: Offer, group: ProductGroup) -> SavingsEvidence | None

SavingsEvidence must include:

    selected_price: float
    baseline_price: float
    saving_rub: int
    saving_percent: float
    comparable_offer_count: int
    source_count: int
    client_reason: str

The existing app.search_v2.market_analysis.is_comparable_offer remains the first eligibility gate. The new module adds independent-reference, duplicate and freshness constraints; it does not weaken exact-match or verification rules. app.search_v2.recommendations.select_recommendations remains the only selector of roles. app.search_v2.presentation.format_client_recommendation remains the only V2 formatter for client text.

Change 2026-08-13: created after the product decision to make search free and to prioritize proven ruble savings over pricing or internal search diagnostics.
