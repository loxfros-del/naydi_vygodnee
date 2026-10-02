# Validate Avito Review against live Starter data

This living ExecPlan follows `.agent/PLANS.md`. Workspace: `C:/Users/Пользователь/Documents/naydi_vygodnee`. The user activated Apify Starter on 2026-09-09 and authorized the three-model pilot, subsequent fixes and transfer of listing text/photos to the configured AI Tunnel for this pilot. Do not edit `.env`, `.venv`, databases, Telegram configuration, payments, `main.py` or `app` code. Use the normal Avito configuration loader for existing API credentials; never display them or put them in artifacts.

## Purpose / Big Picture

Obtain real exact-model listings in Moscow, repair demonstrated integration defects, measure actual spending and establish whether the evidence supports a trustworthy recommendation. Missing evidence is a valid result. Neither paid collection nor a successful AI response proves a below-market, physically sound purchase.

## Progress

- [x] 2026-09-09: Confirm Starter activation and configured API readiness without disclosing credentials.
- [x] Add explicit `--live` runner with $0.30 shared collection allowance and 45 RUB pilot report allowance.
- [x] Recover the existing first successful actor dataset after download timeout without repeating its paid start.
- [x] Complete three 32-record model probes: iPhone 13, 14 and 15. Verify six final run receipts: $0.60504 total, 75 unique listings among 96 records.
- [x] Correct gzip/download recovery, ambiguous POST retry, late billing, city/address normalization, last-item stock and charging-circuit defect handling.
- [x] Persist pre-start spending reservations across processes and restarts. Seed six prior runs. Enforce $1/day and $18 per Starter period anchored to day 9, plus $0.30 per search.
- [x] Repair AI Tunnel SSE completion; retain usage or an explicitly marked conservative estimate. Change default AI timeout from 18 to 45 seconds within the existing worker deadline.
- [x] Verify two real AI cases: memory conflict (0.06 RUB) and text plus nine photos (0.47 RUB). Independently inspect two source photos in browser.
- [x] Run 216 Avito tests, compile 206 Python files safely, run Alice parser with isolated defaults, check JavaScript syntax and diff whitespace.
- [x] Start the updated local service on 127.0.0.1:8091 and verify its ready panel without starting another paid search.
- [x] Record outcomes and changed files in `docs/avito_pilot_results_2026-09-09.md`.

## Surprises & Discoveries

The first SUCCEEDED actor response reported only its $0.005 start; completed event billing later reached $0.13079 for 21 records. Reusing the apparent remainder would overspend the intended allowance. GET recovery is repeatable; a timed-out POST can still have started a paid actor and must not be automatically repeated.

Source address strings split listings from one city into separate market groups, and real stock used “В наличии: последний товар”. Both shapes now normalize correctly. Explicit other cities stay rejected; missing stock, repairs or parts history remain unknown. A real description hid “отклонения в работе цепи заряда” and an individually calculated price; regression checks now reject it.

The configured Qwen route sometimes completed its streamed choice but did not close the connection or send DONE. A strict SSE parser now recognizes finish_reason=stop, waits at most 0.5 seconds for usage, and rejects incomplete/length/error streams. The full conflict review later completed in 14.92 seconds and the text-plus-photo review in 15.50 seconds with a 45-second request ceiling; an earlier 18-second attempt timed out. Latency remains variable.

A calendar-month spending reset would allow another allowance on October 1 before Starter renews October 9. Production now passes billing_cycle_day=9 to SpendingGuard. Generic guard callers default to day 1; the production factory and pilot share the day-9 guard. The current interval is September 9 through October 8 inclusive.

## Decision Log

Use Moscow, 128 GB and SIM + eSIM as explicit pilot requirements; states are excellent for iPhone 13/15 and new for iPhone 14. These narrow probes are not a claim that no good Avito listings exist. Keep market collection free of buyer price limits and candidate collection within the scenario budget.

Use a local JSON spending ledger with atomic writes and a kernel file lock. Reserve before every production paid start; incomplete billing and ambiguous errors retain the reservation. Release the unused part only after a complete receipt. Corrupt or unavailable state blocks new charges. Limit changes are local code, not modifications to account billing or secrets. Manual console runs and other projects are outside this ledger.

Only the configured AI Tunnel host uses streaming; other compatible hosts retain their previous transport. Keep the user-selected model and API key restrictions. For this Qwen route use the documented reasoning control with effort none. A missing billing trailer receives a marked budget estimate, never silent zero cost. Text and photo parsing still enforce the existing strict output contracts.

After reading the actual photos, prohibit inferring original hardware, original packaging, perfect pixels or hidden functionality from appearance alone. Two successful examples demonstrate integration, not general model accuracy. No messages were sent to sellers, and no goods or further subscriptions were purchased.

## Outcomes & Retrospective

The three-model pilot and demonstrated integration fixes are complete. Collection cost is known and protected against repeated starts. AI now returns useful completed text and photo analyses; both inspected listings retained caution. No candidate in the three candidate groups met all exact request conditions, and no below-market physically verified purchase was established. Consequently finalist re-collection was not run. A further quality evaluation can reuse saved records without more Apify spending.

## Context and Orientation

`tools/run_avito_pilot.py` loads configuration, calls `build_service` and uses a diagnostic provider subclass. `avito_service/apify.py` starts and recovers actor runs; `spending.py` controls shared reservations in `runtime/avito_spend.json`. `http_api.py` installs the guard and shared $0.30 search cap. `ai.py` builds evidence prompts, handles responses and preserves cost estimates. Normalization, matching, risks and market cache keep evidence comparable. Runtime diagnostics contain listing evidence and must remain local and ignored by Git.

## Plan of Work / Milestones

The acquisition milestone recovered an already paid dataset, inspected actual fields and completed two other model probes. Its acceptance is 96 records with real identifiers and final run receipts. The integration milestone repaired reproduced failures and added offline regressions. The budget milestone added reservations shared by multiple processes and aligned monthly accounting with the actual subscription period. The AI milestone checked a known text conflict and a full photo set, then independently sampled source images. Each milestone is recorded in the progress section and final report.

## Concrete Steps

Use the bundled runtime from the project directory: `C:/Users/Пользователь/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe` with `-B -X utf8`. Running `tools/run_avito_pilot.py` without `--live` only checks readiness. `--live` deliberately uses paid APIs; `--collect-only` avoids AI, and `--resume-market` reuses a matching stored paid dataset.

Run the ten Avito unittest modules listed in the final report. Expected current result: 216 tests pass. Compile project Python files with `compileall.compile_file`, excluding forbidden environments/databases/runtime directories and redirecting bytecode to a temporary directory. Run `tools/test_alice_parser.py` with existing dependencies in `runtime/qa_deps` and AST-derived literal Settings defaults injected as `app.config`, so application credentials are not loaded. Run `node --check avito_web/app.js` and `git diff --check`.

Start the independent UI with `python -B -m avito_service --host 127.0.0.1 --port 8091`; it displays readiness without initiating collection. It remains separate from Telegram and payments.

## Validation and Acceptance

Regressions cover explicit denials versus defects, real city/stock fields, preserved run identity, gzip and bounded GET retries, no POST retry, delayed billing, reservation release versus uncertainty, concurrent processes, corrupt ledger rejection and subscription-boundary rollover. AI regressions cover SSE chunk boundaries, stop without EOF, usage trailers, 429 deadlines, malformed/incomplete streams and estimated-cost propagation. The final price label still requires ten other comparable sellers and a fresh finalist check. Photo inspection is not physical diagnostics.

## Idempotence and Recovery

Read-only dataset/run recovery does not start actors. Repeating `--live` may charge again and must remain deliberate. Runtime outputs have unique directories. The spending ledger survives process restarts; never delete it to bypass a limit. Its six seed records are idempotent by run ID. Ambiguous new runs retain a conservative reservation pending reconciliation. Existing market analysis is cached for 15 minutes and cannot replace finalist availability checks.

## Artifacts and Notes

Local evidence: `runtime/avito_pilot/billing-2026-09-09.json`, `summary-2026-09-09.json`, phase directories `20260909T122612Z`, `20260909T123838Z`, `20260909T124005Z`, `20260909T124229Z`, plus `ai-validation-conflict-45s.json` and `ai-validation-photos-45s.json`. Three successful AI calls report 0.53 RUB; prior timed-out calls may also have been billed, so do not describe 0.53 as all diagnostic AI spending. User account statistics are a separate source of actual account totals.

## Interfaces and Dependencies

No new project dependency is required. SpendingGuard exposes reserve, settle, record_existing, seed_initial_spend and snapshot; construction accepts daily_limit_usd, monthly_limit_usd and billing_cycle_day. The production provider accepts an injected guard; isolated mock providers may omit it. The existing user-selected actor and model are retained. AIReview.cost_estimated and CostSummary.ai_cost_estimated distinguish estimates in owner-facing cost output.

Revision 2026-09-09: completed the live pilot, aligned spending with the paid billing period, repaired observed source/transport defects and recorded bounded AI validation rather than claiming a fully proven purchase recommendation.
