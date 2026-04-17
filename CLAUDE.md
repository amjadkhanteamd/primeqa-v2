# CLAUDE.md — PrimeQA v2

## What is this project?
PrimeQA is a **Release Intelligence System** for Salesforce. It connects to Salesforce orgs, captures versioned metadata, AI-generates test cases from Jira requirements, executes them with reliable test data, and produces ranked risk scores plus GO/NO-GO release recommendations with explainability.

Not a "TestRail replacement" — TestRail parity is the substrate; the category is decision-making for releases.

## Tech stack
- **Backend**: Python 3.11, Flask, SQLAlchemy, PostgreSQL (Railway) with pgvector
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), HTMX, vanilla JS
- **Auth**: JWT (PyJWT) httponly cookies, bcrypt, 4 roles (admin/tester/ba/viewer)
- **Encryption**: Fernet for credentials (cryptography lib)
- **AI**: Anthropic SDK (Claude Opus 4 / Sonnet 4 / Haiku 4 / 3.7 / 3.5)
- **Deployment**: Railway (3 services: web/worker/scheduler), Dockerfile, gunicorn

## Project structure
```
primeqa/                    # Main package
├── app.py                  # Flask entrypoint, registers all blueprints
├── db.py                   # SQLAlchemy engine/session setup
├── views.py                # Server-rendered web UI routes (HTMX-friendly)
├── worker.py               # Background job processor (python -m primeqa.worker)
├── scheduler.py            # Reaper/timer jobs (python -m primeqa.scheduler)
├── core/                   # Tenants, users, auth, envs, connections, groups
├── metadata/               # Salesforce org metadata (versioned)
├── test_management/        # Sections, requirements, test cases, versions,
│                           # suites, reviews, tags, milestones, step_schema
├── execution/              # Pipeline runs, step execution, cleanup,
│                           # data_engine (templates/factories), analytics
├── intelligence/           # Explanations, failure patterns, causal links,
│                           # generation (AI), risk_engine
├── release/                # Release model, decision_engine, CI webhooks
├── vector/                 # Embeddings (pgvector)
└── templates/              # Jinja2 HTML templates
migrations/                 # SQL migration files (001-015)
tests/                      # Integration test files
```

## Architecture rules
- **7 domain modules** with strict boundaries: core, metadata, test_management, execution, intelligence, release, vector
- Each module has: models.py, repository.py, service.py, routes.py
- Cross-domain calls go through service layers, never direct SQL across domains
- All resources are tenant-scoped via `tenant_id`
- Environments scope by group membership (admin sees all)
- Settings pages live under `/settings/*` with a sidebar layout (`settings/base.html`)

## Key commands
```bash
# Run locally
source venv/bin/activate
python -m primeqa.app                    # Flask dev server on :5000

# Run tests
python tests/test_auth.py                # 15 tests
python tests/test_environments.py        # 14 tests
python tests/test_metadata.py            # 10 tests
python tests/test_management.py          # 23 tests
python tests/test_pipeline.py            # 12 tests
python tests/test_executor.py            # 15 tests
python tests/test_cleanup.py             # 9 tests
python tests/test_intelligence.py        # 11 tests

# Deploy
git push origin main                     # Railway auto-deploys 3 services

# Database (migrations)
railway service Postgres
railway run bash -c 'psql "$DATABASE_PUBLIC_URL" -f migrations/015_custom_fields_bulk.sql'
```

## Environment variables
- `DATABASE_URL` — PostgreSQL connection string (Railway auto-provides)
- `JWT_SECRET` — 64-char hex string for JWT signing
- `CREDENTIAL_ENCRYPTION_KEY` — 64-char hex for Fernet encryption
- `WEBHOOK_SECRET` — HMAC key for CI/CD webhooks (optional; no signature check if unset)
- `PORT` — HTTP port (Railway sets, default 5000)
- `FLASK_ENV` — production on Railway

## Database (55+ tables)
PostgreSQL on Railway with pgvector extension. Migrations are plain SQL files run manually via psql. Never mutate an existing migration — always add a new numbered one.

## Conventions
- Repository pattern: all DB queries go through repository classes
- Service pattern: business logic in service classes
- API routes return JSON under `/api/*`
- Web views render templates under `/`
- Tests are integration tests against the real Railway database
- Commit messages: descriptive, prefixed with phase/feature name
- Tenant isolation: every new table includes `tenant_id`, every query filters by it
- AI outputs carry structured reasoning (`parsed_explanation`, `criteria_met`, etc.) for Phase 11 explainability surfacing
- Use summary tables (test_case_risk_factors, etc.) instead of heavy joins for dashboards

## The Release Intelligence Loop

```
Release → Requirements (Jira) → AI-generated Tests
       → Metadata Impacts → Risk Scores → Ranked Test Plan
       → Test Data (templates/factories) → Executions
       → Results → Decision Engine → GO/NO-GO Recommendation
       → Human confirms → CI/CD proceeds
```

Every AI output has reasoning. Every decision is recommendation-only (human confirms).
