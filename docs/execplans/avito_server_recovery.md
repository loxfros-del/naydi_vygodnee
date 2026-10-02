# Restore one working Avito Review server

This living plan follows `.agent/PLANS.md`. All paths below are relative to `C:/Users/Пользователь/Documents/naydi_vygodnee`.

## Purpose / Big Picture

The owner must be able to submit a search without reaching an older server with a stale budget. Preserve the description, price, photo and final-page verification sequence. Availability and stock remain disabled. Expand AI resources as authorized on 2026-09-09 without changing verdict requirements.

## Progress

- [x] Confirm multiple Windows processes were listening on port 8091 and terminate only those Avito processes.
- [x] Set Windows exclusive socket ownership and add three real socket regressions.
- [x] Confirm failed attempts created no Apify runs before releasing their local reservations.
- [x] Complete one real HTTP search: 20 market records plus 20 candidates, 72 seconds, no budget error.
- [x] Replay four descriptions from the paid dataset: all text reviews completed; model mismatches and conditional payment prices prevented photo selection.
- [x] Expand production AI resources and matching client/server time limits.
- [x] Correct the warning that treats deliberately rejected candidates as failed AI work.
- [x] Validate offline, restart the single server, and verify final deployed settings and response.

## Surprises & Discoveries

Windows permits multiple listening sockets when SO_REUSEADDR is enabled. Merely starting a new process and reading `/health` did not prove that subsequent user requests would reach it. Four old Avito processes remained until explicit taskkill succeeded. `SO_EXCLUSIVEADDRUSE` must be set before bind, with address and port reuse disabled on Windows.

The live search `HulaZoAYUANgfUgv` completed, with four text reviews but no photo candidates. The public warning compared full photo completion against all 20 records, including deliberate rejections. A second text-only audit confirmed four completed reviews, not a provider outage. Do not relax price or model checks to manufacture results.

## Decision Log

Keep Apify at $1 per search, $3 per Moscow day, and $18 per subscription period beginning on the 9th. AI has a separate 50-ruble per-search allowance, a 90-second request timeout, up to 60 text candidates, 20 photo candidates, and four concurrent photo requests. The worker may run for 360 seconds; the browser waits slightly longer. Explicit test/service configuration remains usable, while the production launcher and pilot command share the expanded profile. Do not edit .env or account settings.

## Context and Orientation

`avito_service/http_api.py` builds the production service and owns its socket. `config.py` defines the shared production profile. `jobs.py` owns worker deadlines; `avito_web/app.js` owns browser waiting. `service.py` performs selection and generates report warnings. `tools/run_avito_pilot.py` must use the same profile. `runtime/avito_spend.json` stores existing charges and reservations; never delete it to reset a limit.

## Plan of Work

First prove that a second server cannot occupy the live port. Then apply expanded limits without altering risk rules, ranking or stock policy. Generate an incomplete-analysis warning only for eligible candidates whose necessary checks failed or remain pending, rather than for items already rejected by completed checks. Validate both rejection and provider-failure cases.

## Concrete Steps

Use the bundled Python executable under `%USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe` with `-B -X utf8`. Run the Avito unittest modules including `tools.test_avito_server_binding`, compile project Python files into a temporary bytecode directory while excluding .venv and runtime dependencies, and run `tools/test_alice_parser.py` with isolated Settings. Run Node `--check avito_web/app.js` and `git diff --check`.

After tests, stop only processes whose command line runs `-m avito_service` on port 8091, confirm they terminated, and start one hidden server outside the tool's restricted network context. Inspect the bound PID, test a second bind is rejected, and submit or replay one bounded request. Reuse already-paid Apify datasets for further AI diagnostics.

## Validation and Acceptance

The port has one owner, a second bind raises OSError, and a real job reaches complete rather than APIFY_SPEND_LIMIT. A completed text rejection is not reported as an AI outage. An actual AI error still produces an incomplete-analysis warning. Photo approval and final freshness remain necessary for customer recommendations. An empty result can be valid when listings fail the unchanged checks.

## Idempotence and Recovery

All source edits are reversible. Do not retry ambiguous paid POST requests. Read the existing Apify run history and final event receipts before releasing reservations. Keep receipts and replay outputs in `runtime/avito_pilot`. Restarting clears in-memory jobs; saved diagnostics remain on disk. Do not terminate unrelated Python processes or modify Telegram files, credentials or databases.

## Outcomes & Retrospective

Completed on 2026-09-09. Final server PID 6308 is the sole listener on 127.0.0.1:8091. An attempted second live bind fails. The served JavaScript contains the new 375000-ms browser wait. Production configuration reports $1 Apify per search, $3/day, $18/period, 50 RUB AI, 90 seconds per AI request, 60 text candidates, 20 photo candidates and concurrency 4. Report allowance is 150 RUB at the configured 100 RUB/USD conversion.

The real HTTP search collected 20 reference records and 20 candidates and reached complete in 72.2 seconds. Apify final receipts total $0.2496; the project journal after settlement totals $0.85464001 for the day. Four candidate descriptions completed AI analysis. Two contained a different model; the others had conditional payment prices. No candidate qualified for photos, and no deal was claimed. The separate diagnostic replay cost 0.22 RUB according to AI Tunnel. The first search's 1.14-RUB AI figure was an estimate, not a confirmed charge. A further offline replay reused those four real answers at zero new API cost and verified that completed rejections no longer emit the unfinished-AI warning.

Validation: 229 Avito tests passed, including three real socket regressions, three warning/selection regressions and production AI-profile coverage. Compileall passed for 207 project Python files with temporary bytecode. The isolated Alice parser passed. Node syntax check and git diff --check passed. No changes were made to stock policy or recommendation criteria.

Changed source files: `avito_service/http_api.py`, `avito_service/config.py`, `avito_service/jobs.py`, `avito_service/service.py`, `avito_web/app.js`, `avito_web/index.html`, and `tools/run_avito_pilot.py`. Tests: `tools/test_avito_server_binding.py`, `tools/test_avito_transport.py`, and `tools/test_avito_collection_quality.py`. Documentation: `docs/avito_pilot.md` and this plan. Earlier user-authorized Apify-cap changes also updated `README.md`.

Receipts and replay artifacts are under `runtime/avito_pilot/exclusive-server-*2026-09-09.json`; `duplicate-server-recovery-2026-09-09.json` records evidence for releasing failed-attempt reservations. The existing `runtime/avito_spend.json` was reconciled through SpendingGuard, not erased. Final server logs are `runtime/avito_review.final.stdout.log` and `.stderr.log`.

Revision note: expanded AI resources at the owner's request and corrected misleading incompletion reporting after a successful real search. Healthy HTTP alone was insufficient evidence; exclusive port ownership plus a paid end-to-end run established the actual fix.
