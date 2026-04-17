# PrimeQA v2

**Release Intelligence System for Salesforce.** Test management substrate +
AI-first test generation, per-category metadata observability, risk scoring,
agent-assisted fix-and-rerun, and GO/NO-GO decision recommendations with
explainability.

## Live
https://primeqa-v2-production.up.railway.app — login `admin@primeqa.io` / `changeme123`

## What it does

1. **Connect** Salesforce + Jira + LLM (Anthropic) once per workspace
2. **Create a Release** with decision criteria (pass rate, flakiness cap, critical tests must pass)
3. **Import Jira tickets** as requirements, attach to the release
4. **Metadata refresh** per-category (objects → fields/record_types → validation_rules/flows/triggers) with SSE live progress; partial failures don't block healthy categories
5. **AI generates** structured test cases from requirements, grounded in your org metadata
6. **Risk scoring** ranks impacts and test plan by blast radius + criticality
7. **Test Data Engine** provides reusable templates and generative factories
8. **BA review** workflow with inline per-step comments; low-confidence AI tests auto-assigned
9. **Unified Run Wizard**: mix Jira projects/sprints/epics/JQL with PrimeQA suites/sections/requirements/hand-picked tests in a single run
10. **Pre-flight checks** before queuing: credentials, metadata freshness, per-test metadata-stale skip, run-size caps, prod-safety
11. **Live execution** — SSE-powered timeline updates per step; per-step request/response, SOQL, LLM payload capture
12. **Scheduled runs** — full cron (presets + advanced) for test suites with dead-man's-switch alerting
13. **Fix-and-rerun agent** — on failure, triages the error, proposes a fix, auto-applies on sandbox at high confidence (production is always human-gated). Snapshot-based revert.
14. **Cleanup engine** reverse-deletes entities with dependency retry
15. **Decision Engine** evaluates release against criteria → recommends GO / CONDITIONAL_GO / NO_GO with reasoning; per-release flag controls whether CI sees pre-agent or post-agent verdict
16. **Dashboard** shows release health, pass rate by environment, flaky test detection + auto-quarantine
17. **Rerun subset** and **compare to last green** on every run
18. **CI/CD webhook** — GitHub Actions can trigger runs and poll `/api/releases/:id/status`

## Architecture

7 domain modules with strict boundaries:
- `core/` — tenants, users, auth, environments, connections, groups, agent settings
- `metadata/` — versioned Salesforce org metadata with per-category sync DAG
- `test_management/` — sections, requirements, test cases, versions, suites, reviews, tags, milestones
- `execution/` — pipeline runs, step executor, cleanup, data engine, analytics, flake scoring
- `intelligence/` — failure patterns, causal links, AI generation, risk engine, fix-and-rerun agent
- `release/` — releases, decisions, decision engine, CI webhooks
- `vector/` — embeddings (pgvector)

Plus cross-cutting:
- `runs/` — Run Wizard, Preflight, SSE streams, cost forecaster, scheduled runs
- `shared/` — ListQuery, API envelope, observability, notifications

3 Railway services from one codebase: `web` (Flask + gunicorn), `worker`
(pipeline processor), `scheduler` (reaper + dead-man's switch + cron firer).

## Roles
`viewer`, `ba`, `tester`, `admin`, `superadmin` (god mode — cost visibility,
agent autonomy config, raw LLM prompts, pre-flight override; seeded one per
tenant, excluded from the 20-user cap).

## Stack
- Python 3.11, Flask, SQLAlchemy, PostgreSQL (pgvector)
- Jinja2 + Tailwind (CDN) + HTMX + vanilla JS + SSE
- JWT auth (5 roles), Fernet encryption for credentials
- Anthropic SDK for AI generation and fix-and-rerun agent
- croniter for schedule parsing
- Railway for deployment

## Documentation
- `CLAUDE.md` — project context for Claude Code / AI agents
- `PROJECT_STATE.md` — current build progress, database tables, endpoints, pages
- `docs/design/run-experience.md` — R1–R6 design doc with Q1–Q14 decision ledger
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
# ~155 tests across 15 integration suites, all passing against Railway
for t in test_auth test_environments test_metadata test_management test_hardening \
         test_pipeline test_executor test_cleanup test_intelligence \
         test_run_experience test_r2_superadmin test_r3_metadata \
         test_r4_schedule test_r5_agent test_r6_polish; do
  python tests/$t.py
done
```
