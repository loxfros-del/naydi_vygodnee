# Make Avito Review recommendations evidence-based

This is a living ExecPlan maintained under `.agent/PLANS.md`. Work is isolated to `avito_service`, `avito_web`, documentation and tests. Never read or modify project `.env`, `.venv`, databases, `main.py`, Telegram payments or `app` implementation.

## Purpose / Big Picture

An owner can evaluate an Avito request using a separately collected comparison market, inspect the evidence behind the price estimate, and return only candidates whose price and active availability were refreshed after AI analysis. A customer sees precisely what was checked in the description and photos, rather than an unsupported promise of physical condition. The existing Apify provider remains the source; this change does not purchase subscriptions or run paid calls.

## Progress

- [x] (2026-09-07) Reproduced defects in negative phrases, unknown stock, exact matching and comparison sampling; reviewed existing isolated service and current Apify pricing.
- [x] (2026-09-07) Correct deterministic evidence and AI text/photo conflicts, with regressions; exact model and configuration matching covers optional Samsung prefixes and MacBook query specifications.
- [x] (2026-09-07) Add category evidence, stable seller identity, external comparison references and honest uncertainty. Unknown mandatory costs cannot become a bargain.
- [x] (2026-09-07) Separate market collection from budget candidates, cache snapshots, refresh finalists and account for all collection costs.
- [x] (2026-09-07) Correct job deadlines, owner authorization and customer evidence display.
- [x] (2026-09-07) Integrate 150 passing regression tests, required parser check, compilation and local HTTP/UI validation.
- [x] (2026-09-07) Document pilot procedure and remaining live validation in `docs/avito_pilot.md` and README.

## Surprises & Discoveries

The previous 36 pipeline tests passed while “без трещин” was rejected and “под заказ” could pass stock rules. Three prices formerly included the candidate itself. Price-bounded discovery and a cheap-candidate shortlist also supplied the entire market estimate. Dataset timestamps were not checked before customer delivery. The UI exposed administrative data through unauthenticated job results and discarded work at a fixed 45 seconds.

## Decision Log

On 2026-09-07 the user authorized fixing the reviewed Avito defects and implementing the proposed process. Keep the current isolated service and make additive changes. Existing local working changes belong to the user and must be preserved.

Use a broad reference search without the client's price bounds, plus budget-limited discovery, with one total collection allowance. Keep a bounded cache of market snapshots for similar requests. Recheck selected listings by direct URL through the same provider; a cached AI result never counts as an availability check. Refresh must return source observations at most 60 seconds old; reference freshness and verified report eligibility expire after 15 minutes. Imported data alone does not become freshly verified customer output.

Require at least three external independent sellers for a preliminary price comparison, excluding the candidate's seller; ten are required for a below-market claim. Display insufficient sample confidence below ten sellers. Unknown used-condition evidence cannot establish equivalent condition. The owner can still inspect excluded candidates. Treat prices as asking prices, not completed transactions. Compare acquisition prices including mandatory fees and required delivery; optional unknown delivery is excluded only with an explicit pickup basis in the report.

Keep 45 seconds as a UI target; allow a bounded 180-second job to finish without discarding results at the target. Protect owner data and imports using process environment tokens and require access credentials when binding publicly. No new credentials are written to the project environment file.

## Outcomes & Retrospective

The revised pipeline passes 150 offline regression tests, including the final correction that a budget alternative must cost less than TOP and BACKUP. The Alice parser passes with isolated default configuration and temporary QA dependencies, without loading the project environment file. Compilation succeeds for 200 project Python sources, with protected and dependency directories excluded and bytecode in temporary storage. Node syntax and Git whitespace checks pass. A local browser fixture returns three recommendations with ten independent references, restores a completed report on reload, supports authenticated owner diagnostics, and fits a 390-pixel mobile viewport without horizontal overflow. Live Apify quality, paid account eligibility, seller confirmations and the twenty-request real purchase pilot remain unverified; no paid calls or purchase occurred.

## Context and Orientation

`avito_service/apify.py` collects raw dictionaries from Zen Studio. `normalization.py` converts them to typed listings from `models.py`. `risk_rules.py` and `matching.py` run local checks, `ai.py` reviews description and photos, `service.py` controls costs and stages, and `ranking.py` selects recommendations. `http_api.py` exposes jobs from `jobs.py`; `avito_web` is the browser client. Existing `tools/test_avito_service.py` uses fake providers and reviewers. New tests cover corrected evidence, market, collection and authorization contracts without paid traffic.

## Plan of Work

Milestone one makes negative defect statements safe without ignoring affirmative defects, requires positive availability, and checks exact requested models and required fields on final admission. Independently detected text/photo contradictions produce a caution result.

Milestone two extends normalized evidence with hidden seller identity, location, delivery, category-specific condition fields and fresh verification metadata. Ranking accepts a separate `market_analyzed` pool, computes one price per external seller, and returns the range, sample size, confidence and direct source links.

Milestone three adds bounded provider methods for broad references, budget candidates and direct-URL refresh. The service spends one allowance across those calls, caches reference snapshots and analysis, then repeats safety checks after refresh. Changed description, configuration or photographs invalidate prior AI approval; changed prices are ranked again. No refresh method or incomplete refresh leaves a caution item.

Milestone four changes authenticated owner APIs and queued work, preserves progress beyond 45 seconds, and displays condition evidence and price-comparison limitations. Unsupported claims of complete physical inspection are removed.

## Concrete Steps

Run from `C:\Users\Пользователь\Documents\naydi_vygodnee`. The available Python executable is `C:\Users\Пользователь\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`; use `-B -X utf8` for tests. Execute the Avito test modules with `unittest`. Execute `tools/test_alice_parser.py` using the same runtime. Compile source while excluding the explicitly protected `.venv` and routing bytecode to temporary storage; this is the safe equivalent of the repository's `python -m compileall .` check. Use the bundled Node executable for `--check avito_web/app.js`. Test HTTP with explicit dummy configuration, local fixtures and a temporary support inbox so no real configuration is loaded.

## Validation and Acceptance

“Без трещин, всё работает” must pass the defect rule while “есть трещина” fails. Unknown stock and old imported snapshots must never become live customer recommendations. A 128 GB alternative cannot satisfy a strict 256 GB request. Contradictory text/photo identities must be flagged. A below-budget candidate can be compared with more expensive external sellers without letting its own seller set its market price. Missing used-condition fields must disable a bargain claim. A finalist sold or changed during refresh must disappear or require new analysis. All provider calls must share the allowance; cached market data must expire. An unauthenticated public client must not access owner costs or start unlimited paid calls. A task completing after 45 seconds but before its real deadline must remain available.

## Idempotence and Recovery

All tests use temporary directories and fake providers. No database migration or credential mutation occurs. Re-running tests is safe. Cache records are expendable, versioned JSON; corrupt or expired records become cache misses. Changes can be reverted file by file without changing Telegram state.

## Artifacts and Notes

The final response must list changed file groups and exact verification outcomes, distinguish offline checks from real searches, and answer whether the user should buy Starter for the subsequent live pilot. The public Apify price checked on 2026-09-07 is $19/month including $19 prepaid usage; Zen's Free limit is ten total runs. No payment was authorized or made by the agent.

## Interfaces and Dependencies

Use Python's standard library only. Add `rank_listings(analyzed, request, *, market_analyzed=None)` and `is_customer_safe(item, request, *, require_freshness=True)`. Listings gain a method to check their verification timestamp. Provider collection returns `CollectionBatch`; refresh returns the same contract with only requested direct listing IDs. `make_server` accepts optional owner and access tokens, or reads the corresponding process environment variables. Keep tokens out of public payloads and URLs.

Revision 2026-09-07: created this execution plan from the reviewed defects and the user's authorization; fixes and live validation are tracked separately.

Revision 2026-09-07, completion: implementation and offline acceptance complete. First three live requests and the subsequent twenty-request pilot remain operational work after account activation. Temporary browser fixture server was stopped after QA.

## Changed files and executed checks

Production changes: `avito_service/ai.py`, `avito_service/apify.py`, `avito_service/config.py`, `avito_service/http_api.py`, `avito_service/jobs.py`, `avito_service/market_cache.py` (new), `avito_service/matching.py`, `avito_service/models.py`, `avito_service/normalization.py`, `avito_service/ranking.py`, `avito_service/risk_rules.py`, `avito_service/service.py`; `avito_web/app.js`, `avito_web/index.html`, `avito_web/styles.css`.

Documentation changes: `README.md`, this execution plan, and `docs/avito_pilot.md` (new). Existing `.gitignore` and unrelated untracked files were preserved. Temporary parser dependencies were placed under ignored `runtime/qa_deps`; no project dependency or environment files were changed.

Regression files executed together: `tools/test_avito_service.py`, `tools/test_avito_evidence.py`, `tools/test_avito_market_quality.py`, `tools/test_avito_ops_quality.py`, `tools/test_avito_collection_quality.py`, `tools/test_avito_market_cache.py` (150 tests, all passing). Also executed `tools/test_alice_parser.py` with isolated defaults (all passing), source compilation (200 Python files), Node `--check avito_web/app.js`, and `git diff --check`. Browser checks use offline providers only; test image URLs are placeholders, not retrieved listings.
