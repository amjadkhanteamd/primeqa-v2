# CLAUDE.md — PrimeQA v2

## What is this project?
PrimeQA is a **Release Intelligence System** for Salesforce. It connects to
Salesforce orgs, captures versioned metadata, AI-generates test cases from
Jira requirements, executes them with reliable test data, produces ranked
risk scores plus GO/NO-GO release recommendations with explainability, and
ships an agent that auto-fixes failures in sandbox (always human-gated on
production).

Not a "TestRail replacement" — TestRail parity is the substrate; the category
is decision-making for releases.

## Tech stack
- **Backend**: Python 3.11, Flask, SQLAlchemy, PostgreSQL (Railway) with pgvector
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), HTMX, vanilla JS, SSE (EventSource)
- **Auth**: JWT (PyJWT) httponly cookies (also accepted for /api/*), bcrypt
- **Roles**: 5 — `viewer`, `ba`, `tester`, `admin`, `superadmin`
  - `superadmin` is god mode per tenant: cost visibility, agent autonomy config, raw LLM prompts, pre-flight override. Implicitly passes every `require_role` check. Excluded from the 20-user cap.
- **Encryption**: Fernet for credentials (cryptography lib)
- **AI**: Anthropic SDK (Claude Opus 4 / Sonnet 4 / Haiku 4 / 3.7 / 3.5)
- **Scheduling**: croniter-based cron; scheduled_runs table fires from the Railway scheduler service
- **Deployment**: Railway (3 services: web/worker/scheduler), Dockerfile, gunicorn

## Project structure
```
primeqa/                       # Main package
├── app.py                     # Flask entrypoint, registers blueprints + observability
├── db.py                      # SQLAlchemy engine/session setup
├── views.py                   # Server-rendered web UI routes (HTMX-friendly)
├── worker.py                  # Background job processor (python -m primeqa.worker)
├── scheduler.py               # Reaper + dead-man's switch + scheduled-runs firer
├── core/                      # Tenants, users, auth, envs, connections, groups,
│                              # agent_settings repo
├── metadata/                  # Salesforce metadata (versioned) + per-category
│                              # SyncEngine (DAG + SSE)
├── test_management/           # Sections, requirements, test cases, versions,
│                              # suites, reviews, tags, milestones, step_schema
├── execution/                 # Pipeline runs, step executor, cleanup engine,
│                              # data_engine, analytics, flake scoring
├── intelligence/              # Explanations, failure patterns, causal links,
│                              # generation (AI), risk_engine, fix-and-rerun agent
│   └── llm/                   # LLM gateway — gateway.py, router.py, provider.py,
│                              # prompts/*, pricing, usage, limits, tiers, feedback,
│                              # feedback_rules (aggregation), redact, dashboard
│                              # (Phases 1–7)
├── release/                   # Release model, decision_engine, CI webhooks
├── runs/                      # Run Wizard, Preflight, SSE streams, cost forecast,
│                              # scheduled runs, Jira search client + cache
├── shared/                    # query_builder, api envelope, observability,
│                              # notifications dispatch
├── system_validation/         # JSON-driven self-validation suite runner + grammar
│   └── suites/primeqa_core.json   # the canonical 8-category E2E suite
├── vector/                    # Embeddings (pgvector)
├── static/                    # Shared JS/CSS (toast, confirm, unsaved-changes)
└── templates/                 # Jinja2 HTML templates
migrations/                    # SQL migration files (001–030)
scripts/                       # One-off operational SQL (data cleanup, etc.)
docs/design/                   # Design docs (run-experience.md covers R1–R7)
tests/                         # Integration test files
```

## Architecture rules

- **7 domain modules** with strict boundaries: core, metadata, test_management, execution, intelligence, release, vector — plus `runs/` (cross-cutting wizard/scheduling) and `shared/` (cross-cutting utilities)
- Each domain module has: models.py, repository.py, service.py, routes.py
- Cross-domain calls go through service layers, never direct SQL across domains
- All resources are tenant-scoped via `tenant_id`
- Environments scope by group membership (admin + superadmin see all)
- Settings pages live under `/settings/*` with a sidebar layout
- **Superadmin is god-mode**: always passes `require_role` / `role_required`, sees cost + raw LLM prompts + agent settings

## Security posture (post-audit 2026-04-19)

- **Login never takes client-supplied `tenant_id`.** `AuthService.login(email, password)` derives tenant from the `users` row (same email can exist in >1 tenant; first active match with correct bcrypt wins). If a caller has legitimate reason to scope to a specific tenant (SSO), pass `tenant_id=` explicitly in the service call — never from user input.
- **CSRF**: double-submit cookie via `primeqa/core/csrf.py`. `/api/*` with `Authorization: Bearer` skips CSRF (Bearer is cross-origin-safe). All HTML POST forms carry `{{ csrf_input | safe }}`. `static/js/csrf.js` auto-injects `X-CSRF-Token` header on same-origin `fetch()` + htmx.
- **JWT**: `core/auth.py require_auth` tolerates missing optional claims (`email`, `role`, `full_name`) — defaults `role='viewer'` on missing. `sub` + `tenant_id` are required; missing = 401. Views.py `get_current_user` has the same tolerance so web pages don't crash on malformed tokens. **Role downgrade** → `AuthService.update_user` revokes all refresh tokens so stale access-tokens expire quickly.
- **Webhook auth** fails closed: `/api/webhooks/ci-trigger` returns 503 `CONFIG_ERROR` when `WEBHOOK_SECRET` env is unset. HMAC signature required otherwise.
- **Global 500 handler**: `app.errorhandler(Exception)` returns envelope (`/api/*`) or minimal HTML (web). Never leaks stack. Server-side full stack still logged.
- **Input validation**: `create_section` length-validates name; `feedback.capture_user_feedback` type-checks verdict; bulk endpoints coerce ids to positive ints before hitting the DB.
- **State machine**: `PipelineRunRepository.update_run_status` enforces valid transitions (terminal → anything raises). Paired with migration 036 CHECK constraints.
- **Unbounded queries**: `core/repository.py list_*` capped at 500 rows. DB-side dashboard queries use CTEs + JOINs to avoid N+1.

## UI component kit (`templates/components/`)

- **`_buttons.html`** — `btn_primary`, `btn_secondary`, `btn_success`, `btn_edit`, `btn_danger_primary`, `btn_danger_link`. One macro per semantic role. Never hardcode `bg-indigo-600` / `bg-red-600` / `bg-gray-600` — import the macro.
- **`_empty_state.html`** — `empty_state(title, description, cta_label, cta_url|cta_onclick, icon, compact)`. One visual for every "no rows" treatment.
- **`_modal.html`** — `modal_shell(id, title, size, describedby)` via `{% call %}`. Produces dialog envelope with close button, overlay click-to-close, and full a11y (`role=dialog`, `aria-modal`, `aria-labelledby`). Paired with `static/js/modal.js` for focus trap + Escape + Tab wrap + return-focus.
- **`breadcrumbs.html`** — `breadcrumbs([(label, href), ...])`. Every detail/edit page should call this.
- **`pagination.html`** — `render_pagination`, `render_search`, `sort_header`, `per_page_selector`, `render_meta_pagination`.
- **`confirm_modal.html`** + `static/js/confirm.js` — attribute-driven `data-confirm`, `data-confirm-form`, `data-confirm-variant`, `data-confirm-type-to`. Never use native `confirm()`.
- **`feedback_modal.html`** + `static/js/tc_feedback.js` — thumbs feedback on AI-generated TCs.
- **`static/js/loading.js`** — global listener that disables submit buttons + adds `aria-busy` during in-flight actions. Opt out via `data-no-loading`.

**Rule**: every new page checks in with the component kit. If you find yourself writing `<button class="rounded-md bg-...">` or `<div ...No X yet...>`, you're doing it wrong.

## Cross-cutting primitives (`primeqa/shared/`)

- `query_builder.ListQuery` — pagination/search/sort/filter with hard 50/page cap and sort-field whitelist
- `api.json_page` / `json_error` — uniform `{data, meta}` + `{error:{code,message}}` envelopes
- `observability` — request timing, SQLAlchemy slow-query log at 800 ms (tunable via `PRIMEQA_SLOW_QUERY_MS`; default threshold sits above Railway's ~400–500 ms RTT floor), counters at `GET /api/_internal/health`
- `notifications` — stable `notify_*` API; log-only provider today (NOTIFICATIONS_PROVIDER env var flips it)

## LLM architecture (`primeqa/intelligence/llm/`)

Single chokepoint for every Anthropic call. Replaces five scattered call
sites that drifted on retry policy, caching, and usage accounting.

- **`gateway.llm_call(task=..., tenant_id=..., api_key=..., context=...)`** is the ONLY allowed entry point. Internal flow: load tenant config (tier → limits, policy) → check rate limits (minute / hour / daily-spend windows) → resolve complexity from prompt module → router picks `[primary, fallback]` chain → build prompt spec → redact PII → provider.invoke with backoff → escalate once on low-confidence if the prompt declares `SUPPORTS_ESCALATION` → record `llm_usage_log` row (always, success or fail) → return `LLMResponse`.
- **Prompts** live one-per-file in `prompts/*`. Each module exposes `VERSION`, `build(context, tenant_id, recent_misses)`, `detect_complexity(context)`, optional `should_escalate(parsed, resp)`. Registry is a flat static dict — no dynamic loading.
- **Router** (`router.py`): `_CHAINS` keyed by task × complexity, with `TenantPolicy` overrides (`always_use_opus`, `allow_haiku`, `force_model`). Chain length caps at 2 — one escalation hop, never more.
- **Tool use**: `test_plan_generation` uses Anthropic `tool_use` API (`submit_test_plan` with strict JSON schema) so parse failures become impossible; escalation triggers on zero TCs / low confidence / tool not called.
- **Prompt caching**: `cache_control: ephemeral` on grammar + metadata blocks. Cache key is per-tenant because metadata text is tenant-unique (correct isolation; no cross-tenant hits).
- **Per-tenant rate limits** (migration 032 + tiers via migration 034): tier preset → override-wins on any non-NULL raw column. Blocked calls write a zero-token `status='rate_limited'` row to `llm_usage_log` and raise `LLMError("rate_limited")`. Three windows: 60 s / 3600 s / UTC-midnight spend.
- **Product tiers** (`tiers.py`): `starter` (30/500/$5), `pro` (100/2000/$25), `enterprise` (None/None/None), `custom` (ignore preset, raw columns only). Tenant switches tier via the superadmin picker on `/settings/llm-usage` — writes to `tenant_agent_settings.llm_tier` + activity_log.
- **Feedback loop** (migration 033, extended in Phase 7): `generation_quality_signals` table. Machine signals: `validation_critical` (validator), `regenerated_soon` (batch supersession), `execution_failed` (worker). **Human signals** (Phase 7): `user_thumbs_up` / `user_thumbs_down` via `POST /api/test-cases/:id/feedback`, `user_edited` on first AI-output edit (10-min dedup bucket), `ba_rejected` on BA review reject. Severity is reason-mapped: `wrong_object_or_field` / `invalid_steps` → high; `redundant` → low. Feedback POST is rate-limited at 5 / TC / user / day — 6th call returns 200 with `throttled:true` (no 429, no visible rejection) so spammers get no feedback signal of their own. Deduped on `(signal_type, rule, object, field, reason)`.
- **Rules aggregation** (`feedback_rules.py`, Phase 7): signals are transformed into a prompt-ready `### Common mistakes to avoid:` block — natural-language imperatives with concrete recent examples, ranked by severity × frequency, top-5. Gateway's auto-load path now calls `feedback_rules.build_rules_block(tenant_id)` instead of passing raw signal dicts. Same aggregator powers the "Top recurring issues" list on the tenant dashboard.
- **Correction rate** (Phase 7): the north-star quality metric. `(user_edited + ba_rejected + user_thumbs_down) / AI-generated TCs in window`. Shown as a hero number on `/settings/my-llm-usage` (with window-over-window delta) and as a column on the superadmin by-tenant table.
- **Dashboards**:
  - **Superadmin** `/settings/llm-usage` — cost (total / by-task / by-model / by-tenant / by-day), efficiency (cache hit rate, avg cost per generation, escalation rate, error rate + top errors), quality proxy (regeneration-within-15min, validation-critical rate, post-gen failure rate), top spenders, **per-tenant tier picker**.
  - **Tenant admin** `/settings/my-llm-usage` — current plan + description, **soft-cap progress bars** (warn at 80%, block at 100%), blocked-calls counter, KPIs (spend / calls / input / output tokens), daily-spend bars, spend-by-feature table, plan comparison table.
- **Providers** (`providers/registry.py`): routes by model-id prefix. `claude-*` → Anthropic, `gpt-*` / `o1-*` → OpenAI stub (raises NotImplementedError today). Cross-vendor fallback chains are architecturally supported — the router just needs both sides present in the registry.
- **PII redaction** (`redact.py`): compiled regexes scrub emails, IPs, SSN-shaped, long digit runs from outbound prompts. Structure-preserving.
- **Migration**: never bypass the gateway. New callers always go through `llm_call()`. Legacy direct-Anthropic paths (`IntelligenceService._call_llm_legacy`) remain only as fallback when no `tenant_id` + `api_key` is available (i.e. system-level calls) and are scheduled for removal once every call site has a tenant context.

## Run event log (cross-service real-time)

In `primeqa/runs/streams.py` + `run_events` table (migration 027):

- Worker/service code calls `emit_stage_started`, `emit_stage_finished`, `emit_test_started`, `emit_test_finished`, `emit_step_started`, `emit_step_finished`, `emit_log`, `emit_run_status`. Each fans out to:
  1. **In-process `EventBus`** (live, same-process delivery)
  2. **`run_events` row** via `record_event()` — durable, cross-service
- `record_event` opens its own `Session(bind=engine)` (not `SessionLocal()`) so closing it does **not** close the caller's scoped session. That was the cause of a DetachedInstanceError fire drill; never regress this.
- SSE endpoint `GET /api/runs/:id/events` interleaves three channels: in-process bus, DB tail every ~1s (cross-service), DB snapshot every 5s. Initial connect backfills the last 200 events so refresh repopulates the log panel.
- Scheduler's `trim_run_events` keeps ≤1000 events per run (runs every 10 min).
- Privacy: API bodies, SOQL, credentials **never** land in events. Full per-step payloads live in `run_step_results` (role-gated).

## AI test-plan generation (multi-TC per requirement)

In `primeqa/intelligence/generation.py` + `primeqa/test_management/service.py`, migration 028:

- One Generate click → **3–6 test cases** covering `positive / negative_validation / boundary / edge_case / regression` (one scenario per TC). Replaces the old "one click = one TC" model which hid coverage gaps.
- `TestCaseGenerator.generate_plan(requirement, meta_version_id)` returns `{test_cases: [...], explanation}`.
- `TestManagementService.generate_test_plan(...)` creates a `generation_batches` row + N `test_cases` rows (each with `coverage_type` + `generation_batch_id`) + N `test_case_versions`.
- **Supersession is batch-wide**: a new Generate soft-deletes all own-user drafts for the requirement (any prior batch); approved/active TCs are immutable.
- Cost per batch is estimated from tokens using a model→$ lookup table and stored on `generation_batches.cost_usd`. Surfaced to superadmin only.
- Executor name prefix is `PQA_{run_id}_{tc_id}_{logical_id}` to prevent 5 TCs from one requirement colliding on Salesforce Name uniqueness. Idempotency key carries `tc_id` too.
- Fail-fast: `StepExecutor.execute_step` raises a clear error when a `$var` reference has no prior `state_ref`; message includes available vars and the fix hint. Catches the most common AI-generator quality bug before hitting Salesforce.

## Static test-case validator (migration 029)

`primeqa/intelligence/validator.py` — catches AI hallucinations **before** a run wastes an API burst.

- `TestCaseValidator(metadata_repo, meta_version_id)` eager-hydrates an object-by-api-name index + `{object_id: {field_api: MetaField}}` nested map. Cheap to construct — one DB round-trip reused across many `.validate()` calls.
- Rules (severity tier drives UI color + preflight block):

  | Severity | Rule | What |
  |---|---|---|
  | `critical` | `object_not_found` | `target_object` missing in metadata |
  | `critical` | `field_not_found` | `field_values` / `assertions` key missing |
  | `critical` | `field_not_createable` | `create` step uses a read-only field |
  | `warning` | `field_not_updateable` | `update` step uses a read-only field |
  | `critical` | `unresolved_state_ref` | `$var` used without a prior `state_ref` |
  | `critical` | `soql_from_object_not_found` | SOQL `FROM` points at a missing object |
  | `critical` | `soql_column_not_found` | SOQL `SELECT` column missing on `FROM` object |
  | `info` | `fields_not_synced` | Object exists but its fields weren't synced; skipping deep checks |

- Fuzzy suggestions via `difflib.get_close_matches(cutoff=0.6)` for field / object / state-ref names — up to 3 candidates per issue. UI renders them as one-click **Apply** buttons.
- Relationship-aware SOQL: `Owner.Id` is valid when `OwnerId` exists as a reference field (same for `__r` → `__c`). Doesn't cry wolf on standard lookups.
- `apply_fix(steps, issue, replacement)` is a pure function returning new steps with the replacement applied. Used by `TestManagementService.apply_validation_fix` to create a new `test_case_version` (generation_method=`'manual'`).

Three integration points:

1. **After generation** — `generate_test_plan` runs the validator on each new version and stores the report in `test_case_versions.validation_report` (JSONB).
2. **Before execution** — worker's `_run_execute_stage` checks the report before each TC. If `status == "critical"` and run config doesn't carry `skip_validation` / `force_run`, the TC is blocked with `failure_type='validation_blocked'` and a clear log event — no SF call wasted. Superadmin override via `config.skip_validation`.
3. **During execution** — existing `$var` fail-fast in `executor.py` remains as the defence-in-depth runtime layer.

APIs: `POST /api/test-cases/:id/revalidate` (optional `{environment_id}`), `POST /api/test-cases/:id/apply-validation-fix` (body `{issue, replacement}`).

UI on test case detail page: red banner for critical, yellow for warnings, green "No issues" on clean. Each issue lists its suggested replacements as Apply buttons that patch the step JSON and reload.

## Context-driven run triggers + rerun / labels / AI failure summary

The Runs tab used to be both the history view AND the primary way to trigger runs (via the Run Wizard). Multi-TC generation made the wizard feel like the wrong starting point — most users want to run the tests that belong to a thing, not recompose the selection. Context-driven triggers live alongside the source.

**Trigger from the source**:
- `POST /requirements/:id/run` — run every active TC linked to a requirement. Per-row button on Requirements list; `▶ Run N test cases` on Requirement detail. Visibility-scoped (private TCs owned by others are excluded).
- `POST /releases/:id/run` — run everything in a Release's test plan. `▶ Run test plan` button in the Test Plan tab header.
- `POST /suites/:id/run` (pre-existing) — run a suite.
- `POST /test-cases/:id/run` (pre-existing) — run a single TC.
- Run Wizard at `/runs/new` is the **advanced** path for mixed-source runs (e.g. Jira tickets + a suite + hand-picked TCs in one run). Runs list now says "Advanced: build run" for it, with a banner that points to the source pages as the primary entry.

**Requirements list state-aware buttons**:
The list handler runs one group-by over TCs per visible requirement and picks the Generate button label from the user's own state:
- No my-draft AND no approved/active → `Generate`
- My draft exists → `Regenerate` (outline style)
- Approved/active TC exists (but no my-draft) → `Generate again`

TC count + coverage chips render inline on each row when tests exist.

**Run detail extras (migration 030)**:
- `pipeline_runs.label` — free-form 100-char tag. Inline debounced editor on run detail; substring-match filter on the run history page (`?label=release-`).
- `↻ Rerun` per-row on failed results → `POST /runs/:id/rerun-one {test_case_id}` queues a single-TC rerun with `parent_run_id` linkage.
- `↻ Rerun verbatim` in the header → `POST /runs/:id/rerun-verbatim` collects every `(test_case_id, test_case_version_id)` from the original and stores `version_pin` in `run.config`. The worker's `_run_execute_stage` reads `run.config.version_pin[str(tc_id)]` before falling back to `current_version_id`. Critical when the TC has been edited since the original run.
- `failure_summary_ai` (superadmin only) — `POST /runs/:id/summarise-failures` prompts the env's LLM with every failed step's error text and caches a 3-6 sentence root-cause summary. `failure_summary_at` + `failure_summary_model` drive the "Regenerate" affordance.

**Run cost panel on /runs/:id (superadmin only)**:
Sums `generation_batches.cost_usd` for the batches that produced this run's TCs, plus agent fix-and-rerun attempt counts (per-attempt token tracking is a future migration). Collapsible panel with a grand total in the summary line. Panel is never rendered for non-superadmin roles.

## The Run Experience (R1–R7 + post-R7 enhancements)

```
1. Run Wizard (/runs/new) — Jira ticket search (chips + live preview) + suites + sections + requirements + hand-picks
2. As the user types in the Jira box, GET /api/jira/search hits the env's
   Jira connection, caches for 8 s, returns an HTMX fragment with status +
   issue type + project; click / Enter adds a chip
3. Every chip change debounced-fires POST /api/runs/preview (read-only
   RunWizardResolver) updating the sticky "N Jira tickets, M suites → K
   test cases" summary bar
4. Clicking Preview goes to /runs/new/preview: Preflight checks
   (credentials, metadata freshness, per-test skip by metadata category,
   size caps 100/500 with superadmin OVERRIDE), cost (superadmin only),
   per-test skip list
5. Queue pipeline_run; worker dispatches stages. Executor runs each TC's
   steps against Salesforce, writing run_test_results + run_step_results +
   run_events rows. SSE interleaves in-process bus + 1s DB event tail +
   5s snapshot poll so the `/runs/:id` log panel shows every milestone
   cross-service (web and worker are separate Railway services).
6. On step failure: AgentOrchestrator triages (pattern DB + taxonomy
   regex), proposes a fix (LLM), gates on env_type != production AND
   confidence ≥ High threshold
7. Auto-apply on sandbox creates new TestCaseVersion, reruns with
   parent_run_id
8. UI shows Agent fixes tab with Accept / Revert / Edit; Pipeline log
   panel supports Download .txt / .json for ticket attachments.
9. Scheduler cron fires scheduled_runs (suites only in v1). Also trims
   run_events to ≤1000 per run and reaps stuck stages (5-min heartbeat).
10. Flake scorer auto-quarantines chronically flipping tests
11. /api/releases/:id/status honors agent_verdict_counts per release
```

Requirements → test cases flow (post-R7):

```
1. Create requirements manually via "+ New Requirement" OR import from Jira
   (chip picker: HTMX live search + multi-select + paste fallback)
2. Click Generate → AI generate_plan returns 3–6 test cases covering
   positive / negative_validation / boundary / edge_case / regression
3. Each TC gets coverage_type + generation_batch_id; batch captures the
   LLM's rationale + token/cost for superadmin audit
4. Supersession: regenerating soft-deletes prior own-drafts for that
   requirement; approved/active TCs spawn a fresh TC alongside
5. Bulk-generate: pick N requirements → POST /api/requirements/bulk-generate
   runs ≤5 in parallel via ThreadPoolExecutor (hard cap 20 per call)
6. Test Library shows a Coverage column with colored badges; Requirement
   detail page groups linked TCs by coverage bucket + shows "AI test plan
   rationale" callout
```

Full decision ledger and architecture in `docs/design/run-experience.md`.

## Self-Validation Suite

PrimeQA runs itself via a JSON-driven E2E suite — the canonical artifact
is at `primeqa/system_validation/suites/primeqa_core.json`. Grammar is
documented in `docs/design/system-validation.md`. Run with:

```bash
python tests/test_system_validation.py
```

The suite covers 8 workflow categories (Requirements, Test Library, Run
Flow, Jira, Preview, Metadata, Agent, UI Nav) and is authorable by
non-engineers or LLMs. Roadmap: ingest the JSON back into the
`test_cases` table so PrimeQA stores and runs tests of itself.

## Key commands
```bash
# Run locally
source venv/bin/activate
python -m primeqa.app                    # Flask dev server on :5000

# Full test suite (integration tests against Railway)
python tests/test_auth.py                # 15
python tests/test_environments.py        # 14
python tests/test_metadata.py            # 10
python tests/test_management.py          # 23
python tests/test_hardening.py           # 17 (A1–A3 test-mgmt hardening)
python tests/test_pipeline.py            # 12
python tests/test_executor.py            # 15
python tests/test_cleanup.py             # 9
python tests/test_intelligence.py        # 11
python tests/test_run_experience.py      # 14 (R1)
python tests/test_r2_superadmin.py       # 7  (R2)
python tests/test_r3_metadata.py         # 6  (R3)
python tests/test_r4_schedule.py         # 7  (R4)
python tests/test_r5_agent.py            # 7  (R5)
python tests/test_r6_polish.py           # 5  (R6)
python tests/test_r7_jira_picker.py      # 10 (R7 Jira chip picker)
python tests/test_system_validation.py   # 4 runner + 13 canonical suite outcomes
python tests/test_llm_architecture.py    # 25 (Phases 1-7 — gateway / tiers / limits / dashboards / feedback loop)
python tests/test_eval_harness.py        # 15 (offline prompt regression harness)
# ~210 total

# Deploy
git push origin main                     # Railway auto-deploys 3 services

# Apply a migration (idempotent since 016)
psql "$DATABASE_URL" -f migrations/030_run_labels_and_failure_summary.sql
```

## Environment variables
- `DATABASE_URL` — PostgreSQL connection string (Railway auto-provides)
- `JWT_SECRET` — 64-char hex string for JWT signing
- `CREDENTIAL_ENCRYPTION_KEY` — 64-char hex for Fernet encryption
- `WEBHOOK_SECRET` — HMAC key for CI/CD webhooks (optional)
- `NOTIFICATIONS_PROVIDER` — `log` (default) / `sendgrid` / `ses` (R6 stub)
- `PORT` — HTTP port (Railway sets, default 5000)
- `FLASK_ENV` — `production` on Railway

## Database (60+ tables)
PostgreSQL on Railway with pgvector extension. Migrations are plain SQL files
run via `psql`. **Never mutate an existing migration** — add a new numbered
one. Migrations 016+ are idempotent (use `ADD COLUMN IF NOT EXISTS` and
`CREATE INDEX IF NOT EXISTS`).

## Conventions
- Repository pattern: all DB queries go through repository classes
- Service pattern: business logic in service classes
- API routes return JSON under `/api/*` using the uniform envelope
- Web views render templates under `/`
- Tests are integration tests against the real Railway database
- Tenant isolation: every new table includes `tenant_id`, every query filters by it
- AI outputs carry structured reasoning so Phase 11 explainability can surface them
- Use summary tables (test_case_risk_factors, etc.) instead of heavy joins for dashboards
- Commit messages are descriptive, prefixed with phase/feature name, signed with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- Every destructive/admin action writes to `activity_log` via the service layer
- All lists paginate with per_page capped at 50 — there are no unbounded list endpoints anymore
- **Section create is idempotent** — `create_section` returns an existing active row if one matches `(tenant_id, parent_id, name)`. Prevents duplicate-tree regrowth from integration tests.
- **AI generation is batch-first** — every Generate click on a requirement produces N TCs in one `generation_batches` row; supersession is batch-wide per `(tenant_id, requirement_id, owner_id)`. Single-TC `generate_test_case()` remains only for explicit re-gen of a specific `test_case_id`.
- **Thread-safety**: SQLAlchemy sessions are **not** shared across threads. Bulk endpoints using `ThreadPoolExecutor` open `Session(bind=engine)` per thread. `record_event()` same pattern — never `SessionLocal()` if the caller is holding a scoped session.
- **Cross-service observability**: worker/service milestones write to the `run_events` DB table in addition to the in-process BUS. Web's SSE endpoint tails the table so Railway's split services still deliver live updates.
- **HTML unicode**: never write `\uXXXX` escapes directly in Jinja/HTML content — HTML doesn't interpret them. Use the actual UTF-8 character or `&#NNNN;` entity. (JS string literals **do** interpret `\uXXXX`; those are fine.)

## The Release Intelligence Loop

```
Release → Requirements (Jira) → AI-generated Tests
       → Metadata Impacts (per-category sync status) → Risk Scores → Ranked Test Plan
       → Test Data (templates/factories) → Executions (live SSE timeline)
       → On failure: agent triage → sandbox auto-apply OR human review queue
       → Results → Decision Engine → GO/NO-GO Recommendation (respects agent verdict flag)
       → Human confirms → CI/CD proceeds
```

Every AI output carries reasoning. Every release decision is recommendation-only (human confirms). Every agent fix is reversible via the before-state snapshot.
