# Run a 30-person real-request search pilot

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must stay current. This document is maintained under `.agent/PLANS.md`.

## Purpose / Big Picture

After this work, the owner can recruit thirty consenting people, process their real product requests through the existing Telegram and browser flow, review Search V2 without changing the result shown to the customer, and generate a privacy-safe readiness report. The report separates completion of the research sample from permission to roll Search V2 out. It cannot claim success merely because thirty requests were submitted.

## Progress

- [x] (2026-08-28 10:49Z) Audited the Telegram bot, shared browser/Mini App, Search V2 shadow snapshots, and current Avito discovery adapter.
- [x] (2026-08-28 10:49Z) Confirmed the pilot design: thirty distinct consenting participants, six categories, five cases per category, and manual verification of every top result.
- [x] (2026-08-28 11:30Z) Added the privacy-safe pilot manifest, channel funnel, validator, aggregate report, CLI, and eight deterministic tests.
- [x] (2026-08-28 11:30Z) Added six-category quotas to shadow rollout aggregation and the reviewer fields to the admin comparison text.
- [x] (2026-08-28 11:30Z) Added the operating playbook for recruitment, channel attribution, consent, review, stopping rules, site/Telegram roles, and Avito handling.
- [x] (2026-08-28 11:30Z) Ran live Yandex checks, 30 golden cases, 447 project tests, parser regression, compileall, report CLI, and diff checks successfully.

## Surprises & Discoveries

- Observation: Telegram and the browser do not need separate search engines.
  Evidence: `web/app.js` and `app/services/telegram_miniapp.py` use the shared web request boundary, while `app/services/search_engine_bridge.py` controls legacy, shadow, canary, and V2 behavior.

- Observation: shadow mode is already safe for customers because legacy remains the visible answer while V2 runs alongside it.
  Evidence: `SearchEngineBridge.execute` returns `legacy_result` in `SearchEngineMode.SHADOW` and stores the V2 comparison separately.

- Observation: the existing Avito adapter is general web discovery, not a public Avito catalogue API.
  Evidence: `app/search_v2/adapters/avito.py` subclasses `SiteExactSearchBridge`; it uses a `site:avito.ru` query and still requires direct-page verification.

- Observation: the published Avito Business API catalogue covers authenticated business operations and account items, but the project has not found a documented endpoint that searches all third-party public buyer listings.
  Evidence: `.agent/avito-integration-execplan.md` records the official Item API and OAuth review. Therefore no scraper, private endpoint, browser cookie, proxy rotation, or CAPTCHA bypass belongs in this pilot.

- Observation: the repository category key for smartphones is `phone`, not `smartphone`.
  Evidence: `app/category_registry.py` and `data/search_quality_cases.json` both use `phone`. The pilot constants and five phone slots use the same key so real shadow snapshots count toward the quota.

- Observation: live source availability is much better than final recommendation readiness.
  Evidence: a bounded Yandex-only run on 2026-08-28 returned ten web documents in all six categories after loading the installed SDK, but only one category produced a recommendation and no category produced a fully verified top result. The system rejected or demoted unsupported rows rather than claiming success.

## Decision Log

- Decision: use Telegram as the operating console for the first pilot and use the website as an acquisition landing page and browser fallback.
  Rationale: the current bot already has requests, admin review and return messaging; the website adds indexable acquisition without creating a second search implementation.
  Date/Author: 2026-08-28 / Codex.

- Decision: a completed sample requires thirty distinct consenting people and exactly five reviewed cases in each of `phone`, `laptop`, `tv`, `headphones`, `monitor`, and `chair`.
  Rationale: thirty requests from one easy category would not test the system described by the product.
  Date/Author: 2026-08-28 / Codex.

- Decision: record only a random pilot reference, acquisition channel, category, booleans, counts and latency in the manifest. Do not record names, Telegram IDs, usernames, phone numbers, query text, titles or URLs.
  Rationale: the aggregate is a quality audit, not a second customer database.
  Date/Author: 2026-08-28 / Codex.

- Decision: sample completion and rollout readiness are different outputs.
  Rationale: thirty finished reviews prove that the test was run; only exact, verified, safe results prove that V2 may become customer-visible.
  Date/Author: 2026-08-28 / Codex.

- Decision: do not parse Avito HTML or automate around access controls. Until Avito grants explicit buyer-search rights, show only externally discovered direct links as manual candidates or a clearly labelled external search link.
  Rationale: this is safer legally and operationally, and avoids false completeness or price claims.
  Date/Author: 2026-08-28 / Codex.

## Outcomes & Retrospective

The pilot stage is implemented. The committed template starts at zero completion and cannot be mistaken for real evidence. The report separates sample completion, a human-assisted readiness gate, and fully automatic rollout readiness. Category quotas and the channel funnel let the owner answer both “does search work?” and “where do qualified people come from?” without exporting request text or personal identifiers.

Live testing showed the current decision clearly: keep Search V2 in shadow. Yandex discovery worked in all six categories, but one recommendation out of six and zero fully verified top results are below the rollout gate. The next evidence must come from the thirty real opt-in requests; the tooling deliberately cannot manufacture them.

## Context and Orientation

The project is a Telegram product-selection bot with a browser interface under `web/`. A “shadow run” means the current legacy search remains visible to the customer while Search V2 executes in parallel only for evaluation. `app/services/search_engine_bridge.py` builds and stores a comparison through `app/search_v2/shadow_compare.py`. The administrator opens that comparison with the `v2compare_<request_id>` callback in `app/handlers/admin.py`.

The pilot manifest will be a JSON file based on `data/real_request_pilot_template.json`. It is not a customer data store. Each row represents one real participant and contains only a random reference such as `P01`, a category, a channel, consent/distinctness confirmations, automated shadow findings, and manual review booleans. `app/search_v2/pilot_sample.py` will validate and aggregate it. `tools/report_real_request_pilot.py` will print the aggregate without request-level identifiers.

The six pilot categories are deliberately fixed. They cover spec-heavy electronics and a furniture category with different matching behavior. A quota is “met” only when five fully reviewed real cases exist in that category.

## Plan of Work

Create `app/search_v2/pilot_sample.py` with constants for the six categories and allowed channels, a manifest validator, and `aggregate_real_request_pilot`. Validation must reject duplicate participant references, unknown categories, non-boolean review fields, invented completion flags, and manifests containing prohibited personal/search fields. The aggregate must omit participant references and emit category/channel counts, incomplete reasons, manual-failure counts, sample completeness, and strict rollout readiness.

Create `data/real_request_pilot_template.json` with thirty uncompleted slots, five per category. Every field that asserts a real action starts as false or null. This makes the template useful without pretending that requests already exist.

Create `tools/report_real_request_pilot.py`. It accepts one manifest path, validates it, prints UTF-8 JSON, exits zero for a valid report, exits two for invalid input, and never writes to a database. Add `tools/test_real_request_pilot.py` for the complete, incomplete, duplicate, invalid-field, unsafe-candidate, manual-failure, privacy and CLI cases.

Extend `app/search_v2/shadow_compare.py` to include only the normalized category in each shadow snapshot and to require category quotas in the thirty-case aggregate. Keep the aggregate free of request IDs, text, titles and URLs. Update `format_shadow_comparison` so the administrator can copy category, exactness, verification, recommendation presence, unsafe-candidate count, status and duration into the pilot manifest. Update `tools/test_search_v2_shadow.py` accordingly.

Create `docs/real_request_pilot.md` as the operator playbook. It must include the consent message, recruitment quotas, channel tags, seven-day operating sequence, reviewer checklist, stop conditions, conversion metrics, and the rule that Avito is manual/external until official public buyer-search permission exists. It must explicitly recommend website acquisition plus Telegram operation, not two independent search implementations.

## Concrete Steps

Run commands from `C:\Users\Пользователь\Documents\naydi_vygodnee`:

    python tools/test_real_request_pilot.py
    python tools/test_search_v2_shadow.py
    python tools/report_real_request_pilot.py data/real_request_pilot_template.json
    python tools/test_alice_parser.py
    python -m compileall .
    git diff --check

The template report should be valid but show `completed_cases: 0`, `sample_complete: false`, and `rollout_ready: false`. A test-only fully populated manifest should show thirty completed cases and five in each required category. It becomes rollout-ready only if all automated and manual safety gates pass.

Observed on 2026-08-28:

    447 tests in 10.340s
    OK

    live Yandex source cases: 6/6
    recommendation cases: 1/6
    fully verified top-result cases: 0/6

    template completed_cases: 0
    template sample_complete: false
    template rollout_ready: false

`python -m compileall` completed with `.venv`, `.git` and `.pytest_cache` excluded. `git diff --check` reported only the repository's existing LF-to-CRLF warnings and no whitespace errors.

## Validation and Acceptance

The pilot tooling is accepted when the untouched template produces a truthful zero-completion report; duplicate participants or personal/query fields are rejected; a thirty-case balanced fixture is complete; one wrong model, broken link, mismatched price, unavailable item, false savings claim, unsafe candidate or system error makes rollout readiness false; and the aggregate contains no participant references, IDs, titles, URLs or query text.

The operating process is accepted when an administrator can run the bot in shadow mode, serve the unchanged legacy result, open the stored Legacy/V2 comparison, manually open the top URL, fill one manifest row, and regenerate the aggregate without touching `.env`, `bot.db`, the SQLite schema, payment or credits.

## Idempotence and Recovery

All new reporting code is read-only. Re-running the tool only reads a JSON file and prints a report. The committed template contains no real data. If a real local manifest is lost, rebuild it from the consent/review log kept by the operator; do not infer missing booleans. If shadow mode causes source load, return the deployment to legacy mode through normal environment configuration; no database rollback or deletion is required.

## Artifacts and Notes

The production manifest must not be committed. The committed template is intentionally incomplete. A valid aggregate has this shape:

    {
      "cases": 30,
      "completed_cases": 30,
      "category_counts": {"phone": 5, "laptop": 5, "tv": 5, "headphones": 5, "monitor": 5, "chair": 5},
      "sample_complete": true,
      "rollout_ready": false
    }

The final value depends on observed results. The tool must never manufacture a passing report.

## Interfaces and Dependencies

`app/search_v2/pilot_sample.py` must expose:

    REQUIRED_CATEGORIES: tuple[str, ...]
    REQUIRED_CASES_PER_CATEGORY: int
    PilotManifestError(ValueError)
    aggregate_real_request_pilot(manifest: dict[str, Any]) -> dict[str, Any]

`tools/report_real_request_pilot.py` depends only on the Python standard library and the project module. No new package is required. `app/search_v2/shadow_compare.py` keeps its current public functions and adds category data without changing the customer-facing search result.

Plan created 2026-08-28: initial pilot design after local architecture and official platform research.

Plan updated 2026-08-28: implementation, live evidence, validation results and final channel/Avito decisions recorded after completion.
