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
3. **Requirements**: create manually ("+ New Requirement") or bulk-import from Jira via a chip picker (HTMX live search + multi-select + paste fallback)
4. **Metadata refresh** per-category (objects → fields/record_types → validation_rules/flows/triggers) with SSE live progress; partial failures don't block healthy categories
5. **AI generates a test plan** — one Generate click produces 3–6 independent test cases covering positive / negative_validation / boundary / edge_case / regression, grounded in your org metadata. Batch-wide supersession keeps the library tidy; rationale + cost stored per batch.
6. **Bulk generate** — pick N requirements, fire up to 5 parallel generations (hard cap 20); each row reports test_case_count + coverage breakdown
7. **Risk scoring** ranks impacts and test plan by blast radius + criticality
8. **Test Data Engine** provides reusable templates and generative factories
9. **BA review** workflow with inline per-step comments; low-confidence AI tests auto-assigned
10. **Unified Run Wizard** with **searchable Jira ticket picker** — type-to-search with chips, HTMX-driven, 8 s TTL cache, status/type/project badges; plus PrimeQA suites/sections/requirements/hand-picked tests in a single run
11. **Live selection summary** — sticky pill showing "N Jira tickets, M suites → K test cases" that updates via `POST /api/runs/preview` on every chip change
12. **Pre-flight checks** before queuing: credentials, metadata freshness, per-test metadata-stale skip, run-size caps, prod-safety
13. **Live execution** — SSE-powered timeline updates per step, **plus a durable hierarchical "Pipeline log" panel** backed by `run_events` that survives page refresh and works across Railway's split web/worker services. Download any run's log as .txt or .json for tickets.
14. **Static validation after generation** — every generated test case is validated against the org's metadata before you see it: object-not-found, field-not-found, unresolved `$var`, SOQL `FROM` / `SELECT` column mismatches. Fuzzy suggestions + one-click **Apply** button per issue. Critical issues block execution automatically (superadmin override available). Runtime fail-fast stays as a defence-in-depth layer.
15. **Context-driven run triggers** — run from the source: per-requirement `▶ Run`, per-release `▶ Run test plan`, per-suite / per-TC buttons, with state-aware `Generate / Regenerate / Generate again` labels on requirements. Runs tab becomes history + live detail only; the wizard is reserved for mixed-source runs.
16. **LLM architecture** — single `llm_call()` chokepoint routes every Anthropic call through one module (`primeqa.intelligence.llm`). Per-tenant rate limits (calls-per-minute / hour / daily-spend), Anthropic `tool_use` for the test-plan generator (parse failures become impossible), prompt caching with per-tenant isolation, PII redaction, and a **closed-loop feedback system**: machine signals (validator-critical / regenerated-soon / execution-failed) + human signals (👍 / 👎 + optional reason on every AI-generated TC, implicit `user_edited` on first AI-output edit, `ba_rejected` on BA review reject) are aggregated into a "Common mistakes to avoid" rules block that the next generation prompt consumes verbatim. Tenants see a **correction-rate** north-star number (AI-generated TCs that needed human correction) on `/settings/my-llm-usage`, plus top-5 recurring issues grouped across all signal types. Product tiers (`starter` / `pro` / `enterprise` / `custom`) set sensible defaults; superadmins track cost / efficiency / quality proxies at `/settings/llm-usage` and change tenant tiers inline.
17. **Per-run AI spend + label + AI failure summary** (superadmin) — run detail shows aggregated LLM cost (test-gen batches + agent fixes) with models and tokens; free-form inline-editable label with substring filter on history; on-demand "Summarise failures" cached on the run.
17. **Rerun granularity** — rerun all failed tests, or a single failed test, or the whole run *verbatim* with pinned test-case versions (the worker honors `run.config.version_pin` ahead of `current_version_id`).
15. **Scheduled runs** — full cron (presets + advanced) for test suites with dead-man's-switch alerting
16. **Fix-and-rerun agent** — on failure, triages the error, proposes a fix, auto-applies on sandbox at high confidence (production is always human-gated). Snapshot-based revert.
17. **Cleanup engine** reverse-deletes entities with dependency retry
18. **Decision Engine** evaluates release against criteria → recommends GO / CONDITIONAL_GO / NO_GO with reasoning; per-release flag controls whether CI sees pre-agent or post-agent verdict
19. **Dashboard** shows release health, pass rate by environment, flaky test detection + auto-quarantine
20. **Rerun subset** and **compare to last green** on every run
21. **CI/CD webhook** — GitHub Actions can trigger runs and poll `/api/releases/:id/status`

## Architecture

7 domain modules with strict boundaries:
- `core/` — tenants, users, auth, environments, connections, groups, agent settings
- `metadata/` — versioned Salesforce org metadata with per-category sync DAG
- `test_management/` — sections, requirements, test cases, versions, suites, reviews, tags, milestones
- `execution/` — pipeline runs, step executor, cleanup, data engine, analytics, flake scoring
- `intelligence/` — failure patterns, causal links, AI generation, risk engine, fix-and-rerun agent, **llm/ gateway** (router, providers, prompts, tiers, feedback, usage, dashboards)
- `release/` — releases, decisions, decision engine, CI webhooks
- `vector/` — embeddings (pgvector)

Plus cross-cutting:
- `runs/` — Run Wizard, Preflight, SSE streams, cost forecaster, scheduled runs, Jira client + TTL cache
- `shared/` — ListQuery, API envelope, observability, notifications
- `system_validation/` — JSON-driven self-validation suite (PrimeQA tests PrimeQA)

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
- `docs/design/run-experience.md` — R1–R7 design doc with Q1–Q14 decision ledger
- `docs/design/system-validation.md` — self-validation step grammar + canonical suite roadmap
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
# ~195 tests across 17 integration suites, all passing against Railway
for t in test_auth test_environments test_metadata test_management test_hardening \
         test_pipeline test_executor test_cleanup test_intelligence \
         test_run_experience test_r2_superadmin test_r3_metadata \
         test_r4_schedule test_r5_agent test_r6_polish test_r7_jira_picker \
         test_system_validation test_llm_architecture; do
  python tests/$t.py
done
```
