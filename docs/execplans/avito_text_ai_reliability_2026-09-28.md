# Make Avito text-AI batches observable and recoverable

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document follows `.agent/PLANS.md` from the repository root.

## Purpose / Big Picture

The Avito search currently collects and filters valid listings but can lose an entire text-AI batch when the provider omits one result, returns malformed structured output, times out, or cannot reserve budget. After this change, valid results from a partial batch survive, only unresolved listing IDs are retried, repeated failures split into smaller batches down to a single listing, and every attempt records safe structured diagnostics without prompts or listing text. A local replay command will exercise saved PS5 data using text AI only and will never call Apify.

## Progress

- [x] (2026-09-28) Read `.agent/PLANS.md`, inspected current live traces, AI transport, batch parser, service stop behavior, and budget guard.
- [x] (2026-09-28) Added bounded packet-level result, transport, mapping, and reservation telemetry without prompts or raw model output.
- [x] (2026-09-28) Implemented strict ID mapping and partial salvage with bounded recursive splitting.
- [x] (2026-09-28) Separated actual, estimate, and active reservation accounting; completed calls release unused reserve while uncertain calls remain conservative.
- [x] (2026-09-28) Added a text-only saved-data replay tool that cannot construct or call an Apify provider.
- [x] (2026-09-28) Added regressions for partial JSON, missing/duplicate/unknown IDs, invalid items, splitting, salvage, retry limits, single-item telemetry, transport classification, and reservation release.
- [x] (2026-09-28) Ran focused tests (59 passed), all Avito tests (522 passed), `compileall`, and the required Alice parser test.
- [x] (2026-09-28) Ran text-only replay against saved PS5 listings: 17 expected, 17 complete, 0 failed, 11 text/photo-stage eligible, 0 after the separate bargain-evidence gate, and 0 Apify runs.

## Surprises & Discoveries

- Observation: `SearchTrace.ai.text.errors` counts failed listings, not provider requests, and the trace loses the exception code and parsing reason.
  Evidence: the live PS5 repeat showed one recorded call, three errors, and roughly 99 seconds in text AI, but no batch-level failure record.
- Observation: `AvitoAnalysisService` stops all remaining text work when one batch has zero completed reviews.
  Evidence: `avito_service/service.py` sets `stopped_for_provider` when `completed == 0` and marks every remaining listing incomplete.
- Observation: `parse_text_batch_reviews` creates incomplete reviews for missing IDs but silently ignores unknown and duplicate IDs, so the caller cannot distinguish a partial salvage from a clean response.
  Evidence: `avito_service/ai.py` stores parsed reviews in a dictionary and fills absent expected IDs with `incomplete_review`.
- Observation: the live failures include at least two modes. A fast failed batch was charged mostly as an estimate, while later batches spent about 90–100 seconds before returning incomplete.
  Evidence: batch logs and traces under `runtime/avito_live_batches/20260928T113148Z/traces`.
- Observation: the recovered text-only replay reproduced transport instability, not a schema failure: two batch attempts failed after about five seconds with no HTTP response and one two-item attempt timed out after 90 seconds; every retry returned HTTP 200 with `finish_reason=stop` and exact ID coverage.
  Evidence: `runtime/avito_text_replays/20260928T121707Z.json` records 17/17 complete, 12 retried, seven packets, no parse/schema errors, and no missing/duplicate IDs after retries.
- Observation: a subsequent replay completed all three five-item packets and the final two-item packet on the first attempt.
  Evidence: `runtime/avito_text_replays/20260928T122220Z.json` records 17/17 complete, zero retries, HTTP 200 for all four packets, and exact result counts 5/5, 5/5, 5/5, and 2/2.
- Observation: the final zero at the photo boundary is no longer caused by text AI. Eleven reviews reach `NEEDS_EVIDENCE -> photo_ai`; the separate bargain-evidence gate removes them from the saved snapshot replay.
  Evidence: the final replay reports `photo_eligible_before_bargain=11`, `photo_eligible=0`, and text decisions of 11 `NEEDS_EVIDENCE` plus six safe failures.
- Observation: high estimate totals were not an active-reservation leak. Successful calls with provider receipts settle to actual, missing receipts settle to an estimate, uncertain transport failures retain their estimate, and active reservations return to zero.
  Evidence: the two replay reports end with `active_reservations=0`; the recovered run committed 19.88 rubles including uncertain attempts, while the clean run committed 8.56 rubles.

## Decision Log

- Decision: Preserve every safety gate and represent unresolved AI work as incomplete, never as approval or rejection.
  Rationale: The user explicitly prohibited weakening safety, and unknown/missing structured results are lack of evidence.
  Date/Author: 2026-09-28, Codex.
- Decision: Keep the configured model, route, spending limits, market cache, photo rules, and Apify code unchanged.
  Rationale: The task localizes the problem to text AI and forbids unrelated changes.
  Date/Author: 2026-09-28, Codex.
- Decision: Make retries bounded by a small attempt count and a split depth terminating at single listings.
  Rationale: This salvages good results without loops or uncontrolled spend.
  Date/Author: 2026-09-28, Codex.
- Decision: Build replay so it reads saved normalized listings and invokes only the reviewer and local eligibility logic, never `build_service` or `ZenStudioProvider`.
  Rationale: This makes the no-Apify guarantee structural rather than dependent on a flag.
  Date/Author: 2026-09-28, Codex.
- Decision: Keep text batch size at five.
  Rationale: three five-item packets completed cleanly in the final replay, while the reproduced 90-second timeout occurred on a two-item packet. The evidence points to intermittent transport behavior, not payload size.
  Date/Author: 2026-09-28, Codex.
- Decision: Keep the current model and route.
  Rationale: bounded retry recovered every transient failure, and the next replay completed 17/17 without retry. There is no route-specific correctness evidence justifying a model change.
  Date/Author: 2026-09-28, Codex.

## Outcomes & Retrospective

The text pipeline now retains valid partial results, retries only unresolved IDs, splits repeated failures down to singles, and records safe packet diagnostics. Saved PS5 text replay completes 17/17; 11 are eligible to proceed to photo based on text safety alone. The remaining `0` after bargain evidence is outside the repaired text transport/parser path. Focused tests pass 59/59, the full Avito suite passes 522/522, compileall passes, and the Alice parser test passes. No Apify or live pilot was run.

## Context and Orientation

`avito_service/ai.py` contains `OpenAICompatibleReviewer`, the OpenAI-compatible transport, strict response schemas, text batch parser, request budget reservations, and photo review. `avito_service/service.py` selects text candidates, consults the in-memory AI cache, sends batches, and then computes photo-eligible candidates. `avito_service/telemetry.py` writes safe pilot traces. `avito_service/models.py` defines immutable listing and review records. Tests are executable Python scripts under `tools/`, especially `tools/test_avito_ai_transport.py`, `tools/test_avito_ai_budget.py`, `tools/test_avito_text_retry.py`, and `tools/test_avito_pilot_telemetry.py`.

A packet means one HTTP request to the text-AI provider containing one or more listings. Partial salvage means keeping valid reviews from that response while retrying only expected IDs that are missing, duplicated, invalid, or incomplete. A reservation is the conservative amount temporarily committed before a paid AI request. Actual cost is provider-reported spend; estimate is a fallback used only when billing is absent or the request outcome is uncertain.

The saved live data is under `runtime/avito_live_batches/20260928T113148Z/market_cache`. The replay must not print descriptions, prompts, raw model output, seller details, or credentials. Listing IDs are explicitly authorized telemetry fields for this owner-only diagnostic.

## Plan of Work

First, introduce a structured batch parse result in `avito_service/ai.py`. It will contain reviews keyed to expected listing IDs plus returned count, missing IDs, duplicate IDs, unknown IDs, invalid item count, and a safe parse/schema error. Strict mapping requires exactly one valid result per expected ID; anything else remains incomplete and eligible for retry. Existing public parser behavior will remain available where tests depend on it, while the reviewer uses the richer result.

Second, add a safe packet event callback to `OpenAICompatibleReviewer`. Each attempt event will carry listing IDs, batch size, attempt number, model and route hostname/path, duration, provider error code, HTTP status when known, parse/schema error, expected and returned counts, missing and duplicate IDs, finish reason, and budget/reservation state. The transport will attach safe diagnostic metadata to `ExternalServiceError`; it will never attach raw payloads or model text. `SearchTrace` will store these events in a bounded `ai.text.packets` array.

Third, replace all-or-nothing batch retry with bounded partial salvage. One provider response may contribute every valid review. Only unresolved IDs are retried. If the unresolved group still fails, it splits approximately in half until single listings. Each listing has a finite attempt limit, and budget/deadline errors stop further paid work while leaving unresolved listings incomplete. Completed reviews are cached normally; incomplete reviews are not.

Fourth, verify reservation lifecycle in `OpenAICompatibleReviewer._request`. A request with a completed response and known actual cost releases unused reserve immediately. A completed response with provider token counts but missing cost retains only the calculated estimate, not the original worst-case reservation. A transport failure without any receipt remains conservatively charged because duplicate billing is possible. Tests will prove each branch.

Fifth, add `tools/replay_avito_text_ai.py`. It will load normalized listings from an explicitly supplied saved market-cache JSON, construct no Apify objects, select a bounded set using the existing deterministic rules, run text review and photo-eligibility selection, and write a safe JSON report with expected, complete, retried, split, failed, and photo-eligible counts plus packet diagnostics. A `--dry-run` mode will validate and count inputs without network. The live AI replay will be run only after unit tests pass and will use the existing hard AI budget.

Finally, run focused and full regression checks. If replay proves batches of five are unstable while smaller split packets succeed, lower the configured text batch size to three in `avito_service/review_profile.json`; otherwise retain five. Do not change the model or route without packet evidence proving a route-specific failure.

## Concrete Steps

Work from `C:\Users\Пользователь\Documents\naydi_vygodnee`.

Inspect and edit only the text-AI, telemetry, replay, test, and plan files described above. Use `apply_patch` for edits.

Run focused tests after each milestone:

    C:\Users\Пользователь\AppData\Local\Programs\Python\Python313\python.exe tools\test_avito_ai_transport.py
    C:\Users\Пользователь\AppData\Local\Programs\Python\Python313\python.exe tools\test_avito_ai_budget.py
    C:\Users\Пользователь\AppData\Local\Programs\Python\Python313\python.exe tools\test_avito_text_retry.py
    C:\Users\Пользователь\AppData\Local\Programs\Python\Python313\python.exe tools\test_avito_pilot_telemetry.py

Run the no-network replay validation:

    C:\Users\Пользователь\AppData\Local\Programs\Python\Python313\python.exe tools\replay_avito_text_ai.py --input runtime\avito_live_batches\20260928T113148Z\market_cache\<ps5-cache>.json --query ps5 --location Ярославль --pickup-only --dry-run

After focused checks pass, run:

    C:\Users\Пользователь\AppData\Local\Programs\Python\Python313\python.exe -m unittest discover -s tools -p test_avito_*.py
    C:\Users\Пользователь\AppData\Local\Programs\Python\Python313\python.exe -m compileall .
    C:\Users\Пользователь\AppData\Local\Programs\Python\Python313\python.exe tools\test_alice_parser.py

The text-only live replay, if reached, uses the same replay command without `--dry-run`. It must not import or instantiate `avito_service.apify`, `ZenStudioProvider`, `build_service`, or `AvitoAnalysisService.search`.

## Validation and Acceptance

All new parser tests must show that four valid items survive when a fifth item is invalid, missing, or duplicated. Split tests must show the unresolved group moves from five to two and three and eventually to individual requests, with no listing exceeding its retry limit. Budget tests must show unused reserve released after known-cost success and token-based estimates, while unknown transport failures retain their conservative reservation.

Packet telemetry must contain safe metadata and must not contain description text, prompts, raw response text, API keys, authorization headers, seller names, or image URLs. Existing traces and public API responses must remain backward compatible.

The saved PS5 replay is accepted when its report states exactly how many listings were expected, completed, retried, split, failed, and photo eligible, and when no Apify run is created and `runtime/avito_spend.json` is unchanged.

## Idempotence and Recovery

All unit tests and dry-run replay are repeatable and do not call external services. The text-only replay may incur AI cost but is bounded by the existing hard AI budget and does not call Apify. If interrupted, rerun it with a new output path; it does not mutate saved input. No database, `.env`, token, Telegram configuration, `main.py`, market cache, safety rule, photo rule, model, route, or spend limit is edited.

## Artifacts and Notes

The baseline live evidence is stored under:

    runtime/avito_live_batches/20260928T113148Z/traces

The strongest baseline signals are PS5 repeat `5/5` followed by `0/3` after about 90 seconds, and iPhone `0/4` after about 100 seconds. These prove that one provider failure currently poisons the remaining stage, but they do not yet reveal its exact code.

## Interfaces and Dependencies

Use only the Python standard library and existing project modules. No dependency installation is needed.

`OpenAICompatibleReviewer` will expose a callback setter or constructor argument for safe packet events. A packet event is a plain mapping suitable for JSON serialization. `SearchTrace` will expose a method that appends bounded packet events under `ai.text.packets`.

The rich parser result will be an internal immutable dataclass in `avito_service/ai.py`; it will never contain raw response text. The replay report will be JSON and contain counts, IDs, timings, safe error metadata, and photo eligibility only.

Revision note (2026-09-28): Initial plan created after examining the four live-pilot traces and current batch, transport, telemetry, and budget code. The plan deliberately excludes Apify and all live search execution.
