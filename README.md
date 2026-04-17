# PrimeQA v2

**Release Intelligence System for Salesforce.** Test management substrate + AI-first generation, risk scoring, GO/NO-GO decision recommendations.

## Live
https://primeqa-v2-production.up.railway.app — login `admin@primeqa.io` / `changeme123`

## What it does

1. **Connect** Salesforce + Jira + LLM (Anthropic) once per workspace
2. **Create a Release** with decision criteria (pass rate, flakiness cap, critical tests must pass)
3. **Import Jira tickets** as requirements, attach to the release
4. **Metadata refresh** detects schema impacts across your environments
5. **AI generates** structured test cases from requirements, grounded in your org metadata
6. **Risk scoring** ranks impacts and test plan by blast radius + criticality
7. **Test Data Engine** provides reusable templates and generative factories (unique emails, IDs, etc.) per run
8. **BA review** workflow with inline per-step comments; low-confidence AI tests auto-assigned
9. **Execute** tests with adaptive capture, idempotency, and PQA_ naming convention
10. **Cleanup engine** reverse-deletes entities with dependency retry
11. **Decision Engine** evaluates release against criteria → recommends GO / CONDITIONAL_GO / NO_GO with reasoning
12. **Dashboard** shows release health, pass rate by environment, flaky test detection
13. **CI/CD webhook** — GitHub Actions can trigger runs and poll for GO status

## Architecture

7 domain modules with strict boundaries:
- `core/` — tenants, users, auth, environments, connections, groups
- `metadata/` — versioned Salesforce org metadata
- `test_management/` — sections, requirements, test cases, versions, suites, reviews, tags, milestones
- `execution/` — pipeline runs, step executor, cleanup engine, data engine, analytics
- `intelligence/` — failure patterns, causal links, AI generation, risk engine
- `release/` — releases, decisions, decision engine, CI webhooks
- `vector/` — embeddings (pgvector)

3 Railway services from one codebase: `web` (Flask + gunicorn), `worker` (pipeline processor), `scheduler` (reaper/timers).

## Stack
- Python 3.11, Flask, SQLAlchemy, PostgreSQL (pgvector)
- Jinja2 + Tailwind (CDN) + HTMX + vanilla JS
- JWT auth (4 roles: admin/tester/ba/viewer), Fernet encryption for credentials
- Anthropic SDK for AI generation
- Railway for deployment

## Documentation
- `CLAUDE.md` — project context for Claude Code
- `PROJECT_STATE.md` — current build progress and what's live
- `PRIMEQA_ARCHITECTURE_SPEC_v2.2.md` — original architecture spec
- `PRIMEQA_BUILD_PLAN.md` — original build plan

## Local dev
```bash
source venv/bin/activate
python -m primeqa.app        # :5000
```

## Deploy
`git push origin main` — Railway auto-deploys all 3 services.

## Tests
```bash
python tests/test_auth.py && python tests/test_environments.py && \
  python tests/test_metadata.py && python tests/test_management.py && \
  python tests/test_pipeline.py && python tests/test_executor.py && \
  python tests/test_cleanup.py && python tests/test_intelligence.py
```
109 tests across 8 suites, all passing.
