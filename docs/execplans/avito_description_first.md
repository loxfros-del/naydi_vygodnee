# Description and price before photos, without stock gating

This living ExecPlan follows `.agent/PLANS.md`. Workspace: `C:/Users/Пользователь/Documents/naydi_vygodnee`. On 2026-09-09 the user explicitly requested complete removal of the Avito stock check, then description and price analysis followed by photos, and a better model only if Qwen does not cope. This instruction supersedes the earlier stock requirement for the separate Avito service. Preserve Telegram, `.env`, `.venv`, databases, payments and `main.py`.

## Purpose / Big Picture

Missing or negative warehouse metadata must no longer reject a potentially suitable listing. The service should read product evidence, evaluate the full price, compare like-for-like market records, and spend on photos of promising candidates. It still reports actual defects, model mismatches and non-final prices; an active page is not described as verified physical stock.

## Progress

- [x] Identify all stock gates, AI inputs, freshness claims and obsolete tests.
- [x] Remove stock risks and omit warehouse parameters from analysis and refresh evidence comparison; invalidate previous market cache keys.
- [x] Update UI and operating instructions to describe price and page activity accurately.
- [x] Validate the complete offline pipeline with missing, negative and conditional stock, including market references, parameter-only changes and refresh.
- [x] Run two bounded Qwen batches of six controlled text cases. Final batch passes all six including clean stock-less listings, real defects, memory mismatch, trade-in pricing and seller instructions. Research official alternative model cards.
- [x] Run all 222 Avito tests, compile 206 Python files safely, run Alice parser with isolated defaults, check JavaScript and whitespace. Start hidden local Avito service and verify HTTP readiness.

## Surprises & Discoveries

The old check affected both candidate analysis and market references. Removing only the first filter would leave later caution/rejection paths. Explicit stock fields embedded in parameters also had to be excluded from cached evidence and finalist refresh comparisons. AI also received stock and could reintroduce the rejected criterion. Additionally, actual AI responses put ordinary final prices into price_conditions, which is a blocking list. The prompt now reserves that list for real price restrictions and mandatory or unknown charges.

## Decision Log

Remove both STOCK_UNCONFIRMED and OUT_OF_STOCK from the separate Avito pipeline, not just the missing-value case. Keep listing page activity and fresh price checks because the user removed warehouse verification, not exact-price updates. Retain raw stock in normalized diagnostics for compatibility but do not use or display it in selection. Do not automatically buy or switch models: compare observed failures and costs first, respecting the existing key's allowlist.

## Outcomes & Retrospective

Completed. Stock metadata no longer blocks analysis or ranking and cannot invalidate a finalist on its own. Six controlled text cases passed after prompt refinement. An initial explanation mixed up 38000 and 40000 RUB even though its caution decision was correct; the AI input now omits buyer budget and directs arithmetic/budget/market comparisons to code. The final repeat correctly preserves product matching while reporting trade-in price restrictions. Keep Qwen: these checks do not justify a paid model switch. Prior text-plus-nine-photo validation remains limited evidence, not general accuracy or physical diagnostics. No Apify calls were made for this change.

## Context and Orientation

`risk_rules.py`, `service.py` and `ranking.py` contained stock rejection paths. `models.py` now exposes product-only analysis_parameters, used by AI and cached/refresh comparisons. `ai.py` defines description/price and photo stages. `market_cache.py` separates the new policy from historical snapshots. `avito_web/app.js` and `index.html` state what was actually refreshed. Tests are in the existing Avito unittest modules.

## Plan of Work / Milestones

First change stock behavior throughout collection analysis, comparisons and output. Next prove the full fixture pipeline admits otherwise valid listings without stock while rejecting real defects and removed pages. Finally evaluate Qwen on known clean/defective/conflicting/conditional-price examples, consult official alternative-model capabilities and tariffs, and keep the selected model unless there is evidence for replacement.

## Concrete Steps

Use the bundled Python at `C:/Users/Пользователь/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe` with `-B -X utf8`. Run all ten existing Avito unittest modules. Compile project Python files with compileall while excluding `.venv`, runtime and protected directories; put bytecode in a temporary directory. Run `tools/test_alice_parser.py` with isolated literal Settings defaults and existing `runtime/qa_deps`, then `node --check avito_web/app.js` and `git diff --check`. Restart only the identified local Avito service on 127.0.0.1:8091.

## Validation and Acceptance

A fixture with absent stock reaches text, price comparison, photos and refreshed recommendations when its other evidence is valid. Changing stock alone does not invalidate a finalist or its cached AI review. No stock risk appears in market reference selection or AI payloads. Removed pages and described hardware failures still fail. Model evaluation reports the exact sample size, failures and cost; account restrictions are not bypassed.

## Idempotence and Recovery

Offline tests and saved-data replay do not spend Apify money. Persistent spending limits remain $0.30 per search, $1 per day and $18 per subscription period beginning on day 9. Failed model calls are bounded; secrets and account settings stay unchanged. Old pilot reports are historical evidence, not current stock policy.

## Artifacts and Interfaces

Changed files and checks will be recorded below at completion. No new dependencies are needed. The first full final check encountered one transient Windows 10053 connection abort in the existing foreign-origin HTTP test; that isolated test and the subsequent full 222-test run passed without weakening the check. The external AI endpoint remains the existing api.aitunnel.ru service and only the configured model is callable with the existing key.

Revision 2026-09-09: created for the user's explicit change to stock policy and conditional model-selection request.


## Completed changes and evidence

Changed implementation: `avito_service/risk_rules.py`, `ranking.py`, `service.py`, `ai.py`, `models.py`, `market_cache.py`. Changed UI: `avito_web/app.js`, `index.html`. Updated policy/docs: `AGENTS.md`, `README.md`, `docs/avito_pilot.md`, this plan. Updated tests: `tools/test_avito_evidence.py`, `test_avito_live_fields.py`, `test_avito_service.py`, `test_avito_collection_quality.py`. Other Avito modules were exercised but not edited by this change.

Validation modules: tools.test_avito_service, tools.test_avito_evidence, tools.test_avito_market_quality, tools.test_avito_ops_quality, tools.test_avito_collection_quality, tools.test_avito_market_cache, tools.test_avito_live_fields, tools.test_avito_transport, tools.test_avito_spending, tools.test_avito_ai_transport. All 222 passed. Safe compileall passed for 206 Python files; Alice parser, node --check and git diff --check passed.

Controlled evaluation evidence is local and ignored: `runtime/avito_pilot/ai-no-stock-quality-20260909T134124Z.json` (six cases, 0.24 RUB reported actual cost, 34.95 seconds) and `ai-no-stock-quality-20260909T134432Z.json` (six cases, 23.66 seconds, 1.05 RUB conservative reservation because the usage trailer was missing; not a claimed actual charge). The second run confirms price restrictions separately from product matching. There were no new Apify runs, and its recorded project spend remains $0.60504.

Model comparison used official [Qwen](https://aitunnel.ru/models/qwen3-8-flash), [Gemini 3.1 Pro](https://aitunnel.ru/models/gemini-3-1-pro-preview) and [Claude Sonnet 5](https://aitunnel.ru/models/claude-sonnet-5) cards. All advertise vision and structured output. Published input/output rates per million tokens: Qwen 30/94 RUB; Gemini from 400/2400 with higher route rates also listed; Claude 400–440/2000–2200 RUB. Illustrative 10000 input plus 2000 output tokens are 0.49 RUB on Qwen versus at least about 8–8.8 RUB on these alternatives, excluding any additional image token volume. There is no measured superiority on this task yet. Gemini is the first comparison candidate if Qwen has repeated dangerous errors; the current key allows only Qwen and was not modified.

Revision 2026-09-09 completion: all stock gates removed, price arithmetic assigned to deterministic code, final controlled Qwen run passed, production checks and local readiness verified. No new subscription, seller message or account-permission change was made.
