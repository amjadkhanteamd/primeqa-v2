# PrimeQA v2 — Project State

## Deployment
- **Live URL**: https://primeqa-v2-production.up.railway.app
- **Login**: `admin@primeqa.io` / `changeme123`
- **Railway project**: giving-enthusiasm
- **Region**: europe-west4
- **Services**: primeqa-v2 (web), worker, scheduler, Postgres

## Product Positioning
**Release Intelligence System** — not a TestRail replacement. TestRail parity is the substrate; the category-defining layer is release-level risk scoring, AI-first test generation, data-driven execution, and GO/NO-GO decision recommendations with explainability.

---

## Build Progress

### Completed Initial Steps (Foundation: 7–16)

| Step | Feature | Tests |
|------|---------|-------|
| 7 | Domain scaffold — 6 modules, 36 models | import OK |
| 8 | Auth — JWT, bcrypt, roles, 20-user limit | 15/15 |
| 9 | Environments — CRUD, Fernet encryption | 14/14 |
| 10 | Metadata refresh — SF fetch, diffing, archival | 10/10 |
| 11 | Test management — sections, requirements, suites, reviews | 23/23 |
| 12 | Pipeline — worker, slots, queue priority, reaper | 12/12 |
| 13 | Execution engine — adaptive capture, idempotency, PQA_ naming | 15/15 |
| 14 | Cleanup — reverse deletion, production safety | 9/9 |
| 15 | Intelligence — patterns, causal links, decay | 11/11 |
| 16 | Web UI — dashboard, runs, test library, reviews, admin | renders |
| — | Railway deployment — Dockerfile, 3 services | deployed |
| — | Admin setup — connections, groups, env visibility | 8 sub-phases |
| — | Settings redesign — sidebar, edit/delete, toasts, modals | done |
| — | Connections–Environments wiring | done |

### Release Intelligence Phases (0–13)

| Phase | Feature | Status |
|-------|---------|--------|
| 0 | **Release model** — 6 tables, tabs UI (Changes, Impacts, Plan, Runs, Decision) | ✅ |
| 1 | **AI Generation** — one-click from Jira requirement via LLM connection | ✅ |
| 2 | **Structured Step Editor** — 7 action types, metadata autocomplete, validator | ✅ |
| 3 | **Test Data Engine** — templates, factories (UUID/email/phone/...), snapshots | ✅ |
| 4 | **Multi-point Run Triggering** — from TC, suite, release, multi-select + run history panel | ✅ |
| 5 | **Risk & Prioritization** — impact scoring (0-100), critical entity detection, ranked test plan | ✅ |
| 6 | **BA Review with step comments** — inline per-step comments, auto-review on low-confidence AI | ✅ |
| 7 | **Tags, Milestones, Suites UI** — org layers with nav integration | ✅ |
| 8 | **Custom fields, templates, parametrization, bulk ops** — schema + bulk API | ✅ |
| 9 | **Impact Auto-Regeneration** — "AI Regenerate" button reuses generator with diff context | ✅ |
| 10 | **Decision Engine** — GO/CONDITIONAL_GO/NO_GO with reasoning, criteria-based | ✅ |
| 11 | **Explainability** — reasoning surfaced inline (AI recommendation banner + detail tab) | ✅ |
| 12 | **Dashboards** — pass rate by env, flaky tests, release health | ✅ |
| 13 | **CI/CD Integration** — HMAC-signed webhook + public status endpoint | ✅ |

---

## Database (55 tables)

**Core domain** (10): tenants, users, refresh_tokens, environments, environment_credentials, activity_log, groups, group_members, group_environments, connections

**Metadata** (7): meta_versions, meta_objects, meta_fields, meta_validation_rules, meta_flows, meta_triggers, meta_record_types

**Test Management** (15): sections, requirements, test_cases, test_case_versions, test_suites, suite_test_cases, ba_reviews, metadata_impacts, tags, test_case_tags, milestones, milestone_suites, custom_fields, custom_field_values, step_templates, test_case_parameter_sets

**Execution** (14): pipeline_runs, pipeline_stages, run_test_results, run_step_results, run_artifacts, run_created_entities, run_cleanup_attempts, execution_slots, worker_heartbeats, data_templates, data_factories, data_snapshots, test_case_data_bindings, test_case_risk_factors

**Intelligence** (5): entity_dependencies, explanation_requests, failure_patterns, behaviour_facts, step_causal_links

**Release** (6): releases, release_requirements, release_impacts, release_test_plan_items, release_runs, release_decisions

**Vector** (1): embeddings

## Migrations (001–015)
- 001–006: Initial platform + groups/connections
- 007: Environments link to connections
- 008: Jira + LLM connections on environments
- 009: Release model
- 010: Test Data Engine
- 011: Expanded run source_types (test_cases, release)
- 012: Risk engine (test_case_risk_factors)
- 013: BA review step_comments
- 014: Tags + Milestones
- 015: Custom fields + parameter sets

## API Endpoints (~100)
- **Auth**: login, refresh, logout, me, users CRUD
- **Core**: environments, connections, groups — CRUD + test
- **Metadata**: refresh, current, diff, impacts
- **Test Management**: sections, requirements, test cases, versions, suites, reviews, tags
- **AI Generation**: POST /api/test-cases/generate
- **Step Schema**: GET /api/step-schema, /metadata/<env>/objects, /fields
- **Bulk Ops**: POST /api/test-cases/bulk (move/tag/set_status/add_to_suite)
- **Execution**: runs CRUD, cancel, queue, slots, results, cleanup
- **Intelligence**: dependencies, explanations, patterns, causal links, facts
- **Data Engine**: templates, factories, preview
- **Release**: CRUD, requirements, test-plan, score-risks, evaluate-decision, finalize
- **CI/CD**: POST /api/webhooks/ci-trigger (HMAC signed), GET /api/releases/<id>/status

## Web UI Pages (~35)
- `/login` — auth
- `/` — dashboard with release health, pass rate, flaky tests
- `/releases` list, new, detail (tabs)
- `/requirements` list with Jira import + AI generate
- `/runs`, `/runs/new`, `/runs/<id>`
- `/test-cases`, `/test-cases/<id>` with Run button + run history, `/test-cases/<id>/edit` step editor
- `/suites`, `/suites/<id>` with Run Suite button
- `/milestones`
- `/reviews`, `/reviews/<id>` with step comments
- `/impacts` with AI Regenerate
- `/settings` — sidebar layout
  - General, Connections, Environments, Groups, Users, Test Data

## Tests (109 passing across 8 suites)
- test_auth (15), test_environments (14), test_metadata (10), test_management (23)
- test_pipeline (12), test_executor (15), test_cleanup (9), test_intelligence (11)

---

## The Release Intelligence Flow (end-state)

```
1. PM creates Release "Sprint 42" with decision criteria
2. Import Jira tickets → Requirements → Attach to Release
3. Metadata refresh detects Impacts → linked to Release
4. "Score Risks" ranks impacts + test plan by blast radius + criticality
5. "AI Generate" produces test cases from requirements with confidence score
6. Low-confidence AI tests auto-assigned for BA review with step comments
7. Tester runs test plan (from release, suite, or individual TC)
8. Test Data Engine resolves {{template.X}} and {{factory.Y}} references
9. Execution engine runs with adaptive capture + idempotency + PQA_ naming
10. Cleanup engine reverse-deletes entities, respects production safety
11. "Evaluate GO/NO-GO" runs Decision Engine → AI recommendation with reasoning
12. Human reviews reasoning, confirms final decision
13. CI/CD webhook triggers runs, polls /api/releases/<id>/status for gate
14. Dashboard shows: pass rate trends, flaky tests, release health
```

## Known Limitations / Future Work
- Health check disabled on Railway (was blocking deploys)
- Tests write to shared Railway DB (need cleanup between runs)
- No automated test runner (tests run manually)
- Custom fields UI not built yet (schema + API only)
- Step template UI not built yet (schema + API only)
- Parameter sets UI not built yet (schema + backend support only)
- Metadata diff viewer for impacts is basic (no side-by-side yet)
- No test result artifact viewer (screenshots schema exists, no UI)
- Analytics scheduler job not yet wired (runs on-demand via dashboard query)

## Environment Variables Required
| Variable | Purpose | Set by |
|----------|---------|--------|
| `DATABASE_URL` | PostgreSQL connection | Railway |
| `JWT_SECRET` | JWT signing (64-char hex) | Manual |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet for credentials (64-char hex) | Manual |
| `WEBHOOK_SECRET` | CI/CD HMAC signing | Optional |
| `PORT` | HTTP port | Railway |
| `FLASK_ENV` | `production` | Manual |
