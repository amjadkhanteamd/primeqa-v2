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
migrations/                    # SQL migration files (001–023)
docs/design/                   # Design docs (run-experience.md covers R1–R6)
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
- `observability` — request timing, SQLAlchemy slow-query log at 800 ms (tunable via `PRIMEQA_SLOW_QUERY_MS`; default threshold sits above Railway's ~400\u2013500 ms RTT floor), counters at `GET /api/_internal/health`
- `notifications` — stable `notify_*` API; log-only provider today (NOTIFICATIONS_PROVIDER env var flips it)

## The Run Experience (R1–R7 shipped)

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
5. Queue pipeline_run; SSE streams step_started / step_finished /
   run_status events
6. On step failure: AgentOrchestrator triages (pattern DB + taxonomy
   regex), proposes a fix (LLM), gates on env_type != production AND
   confidence ≥ High threshold
7. Auto-apply on sandbox creates new TestCaseVersion, reruns with
   parent_run_id
8. UI shows Agent fixes tab with Accept / Revert / Edit
9. Scheduler cron fires scheduled_runs (suites only in v1)
10. Flake scorer auto-quarantines chronically flipping tests
11. /api/releases/:id/status honors agent_verdict_counts per release
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
psql "$DATABASE_URL" -f migrations/023_flake_quarantine.sql
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
