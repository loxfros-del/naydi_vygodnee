# Show real listings when no verified recommendation qualifies

This living plan follows `.agent/PLANS.md`. Workspace: `C:/Users/Пользователь/Documents/naydi_vygodnee`.

## Purpose / Big Picture

The owner explicitly requested on 2026-09-09 that collected Avito listings remain visible even when none passes the complete review. Preserve the description, price, photo and freshness checks for recommendations, while showing real remaining listings with accurate limitations. A returned listing must never acquire a verified or below-market label merely to fill the screen.

## Progress

- [x] Inspect the empty-output cause: strict public recommendation filtering hides all conditional-price, incomplete and mismatched records.
- [x] Add a separate public collection of discovered listings and fill unused result positions.
- [x] Update cards and result counts to distinguish found listings from verified recommendations.
- [x] Reuse collected market records when candidate records are absent, without additional paid requests.
- [x] Add offline regression coverage and replay the previously paid real dataset.
- [x] Run required checks and verify the interface before handing back the VS Code launch.

## Surprises & Discoveries

`rank_listings` intentionally omits some mismatches entirely, and `AnalysisReport._public_recommendations` only returns complete, fresh, condition-supported recommendations. Simply displaying CAUTION from the existing tuple would still hide many collected records and mix verification states. Discovery therefore uses all normalized source records and an independent presentation model.

AI provider failures previously escaped before the already paid source records reached serialization. Text failures now stop further batches and preserve incomplete cards; photo failures preserve preceding successful results and stop further photo batches. A market AI failure or later candidate collection failure returns an honest report from the already collected market. Uncertain charges remain conservative estimates, never released spending reservations. Tests also exposed a conditional-credit price phrase missed by the deterministic rules; the added pattern distinguishes a required credit price from an optional financing offer.

## Decision Log

Keep `recommendations` strict. Add `discoveredListings` containing the remaining real cards, ordered after verified recommendations and limited to the selected three or five total. Every discovered card has a status: needs_review, has_risks, mismatch or inactive; never a below-market claim or savings figures. Prefer matching active listings without critical defects, then alternatives with explicit differences. Deduplicate by real listing identity and require a valid direct Avito URL and a title. Missing source data must remain a truthful empty state, never invented listings. The owner's latest instruction authorizes showing previously excluded records as findings with warnings, not as purchase recommendations.

## Context and Orientation

`models.py` serializes reports and cards. New `discovery.py` selects and explains raw findings. `service.py` has all normalized records and assembles the report, including market records collected separately from the buyer budget. `avito_web/app.js` renders cards and counts. `tools/test_avito_discovery.py` checks discovery behavior without external calls. Existing risk and ranking rules are preserved.

## Plan of Work

First add a typed discovered-listing card and selection helper, then include it in analysis reports without changing recommendation eligibility. Reuse the market already fetched in the same search as a source of alternatives if necessary. Update warning text so it no longer claims that every unverified listing is hidden. The browser renders recommendations first and disclosed findings second, with separate found and verified counts. Preserve actual text/photo completion flags, payment conditions, model differences, inactive state and source timestamps.

## Concrete Steps

Use the bundled Python executable with `-B -X utf8`. Run the Avito unittest suite including discovery and server-binding modules, compile project sources excluding protected .venv and runtime dependencies into temporary bytecode, run the isolated Alice parser, Node --check for app.js, and git diff --check. Replay `runtime/avito_pilot/exclusive-server-candidates-2026-09-09.json` with the saved real AI answers; this costs no additional Apify or AI money. Inspect rendered cards in the browser and ensure labels remain accurate.

## Validation and Acceptance

Twenty real collected records and zero eligible recommendations should produce up to three or five actual discovered cards with working direct links. Partial verified results should appear first, followed by nonduplicate findings. Unknown condition and failed photo work must remain visible as uncertainty. Wrong models and known defects must be clearly disclosed. No raw records means no fabricated cards. Verified-only tests continue to assert that recommendations exclude incomplete records. Discovery tests check the new separate collection instead.

## Idempotence and Recovery

Do not modify .env, databases, Telegram files or tokens. Keep $1/search, $3/day and $18/subscription-period Apify guards and the authorized AI resource limits. Reuse paid datasets for verification. Do not start duplicate background servers on 8091. If the user's VS Code process needs the update, identify it before any restart and provide a clear command handoff.

## Outcomes & Retrospective

Completed on 2026-09-09. Replaying twenty real paid Moscow iPhone 14 records with their saved AI answers now returns three real cards and zero verified recommendations. Previously the same records produced an empty public result. All three cards explicitly have remarks; no fabricated savings or below-market labels were added. No new Apify or AI requests were needed for this verification. This replay proves rendering and retention of real records; it does not claim a newly verified bargain or a fresh Yaroslavl search.

Validation: 245 tests across twelve Avito modules passed, including sixteen discovery regressions and the existing Windows exclusive-port tests. Python compileall checked 212 project source files with temporary bytecode and protected directories excluded; the isolated Alice parser, Node syntax check and git diff --check passed. The root agent used the Codex in-app browser on the isolated offline fixture at 127.0.0.1:8092: the form returned three cards, zero verified recommendations, source prices, correct Moscow city labels and direct Avito links. Expanding a card visibly disclosed payment commissions, unreviewed photos and the absence of a final price/activity refresh. The rendered layout was checked by screenshot. The subordinate Edge browser could not access localhost, so the root performed the actual visual verification.

Changed application files: avito_service/discovery.py (new), models.py, service.py, risk_rules.py and jobs.py; avito_web/app.js, index.html and styles.css. Tests: tools/test_avito_discovery.py (new) and one updated public-output assertion in tools/test_avito_service.py. Instructions/documentation: AGENTS.md, docs/avito_pilot.md and this plan. Local ignored artifacts: runtime/avito_pilot/visible-discovery-replay.py and visible-discovery-replay-result.json.

The user's existing VS Code server needs a restart to load Python changes: stop it with Ctrl+C, run the same avito_service command on port 8091 again, and refresh the page. The diagnostic fixture uses port 8092 only and is stopped after inspection. No user server, secret, database, payment flow or spending limit was changed for this feature.
