# CLAUDE.md — PrimeQA v2

## What is this project?
PrimeQA is a Salesforce test automation platform. It connects to Salesforce orgs, captures metadata, generates test cases, executes them via REST API, and provides intelligent failure analysis.

## Tech stack
- **Backend**: Python 3.11, Flask, SQLAlchemy, PostgreSQL (Railway)
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), HTMX
- **Auth**: JWT (PyJWT) with bcrypt password hashing, 4 roles (admin/tester/ba/viewer)
- **Encryption**: Fernet symmetric encryption for credentials (cryptography lib)
- **Deployment**: Railway (3 services: web/worker/scheduler), Dockerfile, gunicorn

## Project structure
```
primeqa/                    # Main package
├── app.py                  # Flask entrypoint, registers all blueprints
├── db.py                   # SQLAlchemy engine/session setup
├── views.py                # Server-rendered web UI routes
├── worker.py               # Background job processor (python -m primeqa.worker)
├── scheduler.py            # Reaper/timer jobs (python -m primeqa.scheduler)
├── core/                   # Tenants, users, auth, environments, connections, groups
├── metadata/               # Salesforce org metadata (versioned)
├── test_management/        # Sections, requirements, test cases, suites, reviews
├── execution/              # Pipeline runs, step execution, cleanup
├── intelligence/           # Explanations, failure patterns, causal links
├── vector/                 # Embeddings (pgvector)
└── templates/              # Jinja2 HTML templates
migrations/                 # SQL migration files (001-006)
tests/                      # Integration test files
```

## Architecture rules
- **6 domain modules** with strict boundaries: core, metadata, test_management, execution, intelligence, vector
- Each module has: models.py, repository.py, service.py, routes.py
- Cross-domain calls go through service layers, never direct SQL across domains
- All resources are tenant-scoped via `tenant_id`
- Environments are visible based on group membership (admin sees all)

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
git push origin main                     # Railway auto-deploys

# Database
railway service Postgres
railway run bash -c 'psql "$DATABASE_PUBLIC_URL" -f migrations/006_groups_and_connections.sql'
```

## Environment variables
- `DATABASE_URL` — PostgreSQL connection string (Railway sets automatically)
- `JWT_SECRET` — 64-char hex string for JWT signing
- `CREDENTIAL_ENCRYPTION_KEY` — 64-char hex string for Fernet encryption
- `PORT` — HTTP port (Railway sets, default 5000)
- `FLASK_ENV` — production on Railway

## Database
- 40 tables (36 original + 4 from migration 006)
- PostgreSQL on Railway with pgvector extension
- Migrations are plain SQL files run manually via psql

## Conventions
- Repository pattern: all DB queries go through repository classes
- Service pattern: business logic in service classes
- API routes return JSON under `/api/*`
- Web views render templates under `/`
- Tests are integration tests against the real Railway database
- Commit messages: descriptive, prefixed with step/feature name
