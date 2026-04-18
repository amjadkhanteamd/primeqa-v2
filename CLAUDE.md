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
migrations/                    # SQL migration files (001–028)
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

## Cross-cutting primitives (`primeqa/shared/`)

- `query_builder.ListQuery` — pagination/search/sort/filter with hard 50/page cap and sort-field whitelist
- `api.json_page` / `json_error` — uniform `{data, meta}` + `{error:{code,message}}` envelopes
- `observability` — request timing, SQLAlchemy slow-query log at 800 ms (tunable via `PRIMEQA_SLOW_QUERY_MS`; default threshold sits above Railway's ~400–500 ms RTT floor), counters at `GET /api/_internal/health`
- `notifications` — stable `notify_*` API; log-only provider today (NOTIFICATIONS_PROVIDER env var flips it)

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
# ~170 total

# Deploy
git push origin main                     # Railway auto-deploys 3 services

# Apply a migration (idempotent since 016)
psql "$DATABASE_URL" -f migrations/028_coverage_types_and_generation_batches.sql
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
