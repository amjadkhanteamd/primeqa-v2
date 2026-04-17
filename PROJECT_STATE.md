# PrimeQA v2 — Project State

## Deployment
- **Live URL**: https://primeqa-v2-production.up.railway.app
- **Login**: `admin@primeqa.io` / `changeme123` *(now `superadmin` role after migration 017)*
- **Railway project**: giving-enthusiasm
- **Region**: europe-west4
- **Services**: primeqa-v2 (web), worker, scheduler, Postgres

## Product Positioning
**Release Intelligence System** — TestRail/Provar parity as substrate; the
category-defining layer is release-level risk scoring, AI-first test
generation, data-driven execution, GO/NO-GO decision recommendations with
explainability, agent-assisted fix-and-rerun, and per-category metadata
observability.

---

## Build Progress

### Foundation (steps 7–16) and initial Release Intelligence (phases 0–13) — shipped earlier

All 14 phases of the original Release Intelligence plan are live. See commit
history before `9a236b1` for the day-one scaffold through to the decision
engine, explainability, dashboards, and CI/CD integration.

### Test Management Hardening (A1–A3) — shipped `9a67d69`

Production-grade UX across every test-management entity:
- Centralised `ListQuery` (search / sort / filter / pagination, hard cap 50)
- Uniform API envelope `{data, meta}` + structured errors `{error:{code,message}}`
- Soft delete + admin-only permanent purge across TC / requirement / suite /
  review / impact / section
- Optimistic locking (409 on version mismatch) + constructor DI refactor that
  fixed a latent `TestManagementService.__init__` bug
- Bulk-op safeguards (100-item cap, typed-DELETE confirmation)
- `primeqa/shared/` package: `query_builder`, `api`, `observability` (p50/p95
  latency + slow-query log at 300 ms, exposed at `/api/_internal/health`)
- UI: pagination / toast / confirm-modal / breadcrumbs components; JS helpers
  (`toast.js`, `confirm.js`, `unsaved_changes.js`); library + detail + edit
  pages rewritten across all entities.

### Run Experience (R1–R6) — shipped

| Phase | Commit | Scope |
|---|---|---|
| R1 | `9045dfc` | Unified Run Wizard (Jira projects/sprints/epics/JQL + suites + sections + requirements + hand-picks), Preflight with per-test metadata skip, live SSE step timeline, log-capture columns on `run_step_results`, Super Admin role bootstrap |
| R2 | `aaa47d1` | Super Admin role gating, cost forecaster (Anthropic pricing table), `tenant_agent_settings` + Agent autonomy settings page, user-cap exclusion for superadmin |
| R3 | `4ede25c` | `meta_sync_status` DAG (objects → {fields, record_types} → {VRs, flows, triggers}), per-category sync + SSE progress, parent-fail cascade, preflight reads per-category health, environment detail with 6 status cards + selective refresh |
| R4 | `452eb33` | `scheduled_runs` table (suites only per Q5), presets + advanced cron toggle, scheduler tick, dead-man's switch, `/runs/scheduled` UI |
| R5 | `479e483` | `agent_fix_attempts` ledger, deterministic triage + LLM proposer, sandbox auto-apply gate at trust-band high, snapshot-based revert, release status respects `agent_verdict_counts` |
| R6 | `82d0a1e` | Notification dispatch stub, flake quarantine with auto-tag, `/runs/:id/rerun-failed`, `/runs/:id/compare` against last green |

**46 new tests across R1–R6, all passing against Railway DB.** Full plan and
decision ledger in `docs/design/run-experience.md`.

---

## Database (60+ tables)

**Core domain** (11): tenants, users, refresh_tokens, environments,
environment_credentials, activity_log, groups, group_members,
group_environments, connections, **tenant_agent_settings**

**Metadata** (8): meta_versions, meta_objects, meta_fields,
meta_validation_rules, meta_flows, meta_triggers, meta_record_types,
**meta_sync_status**

**Test Management** (16): sections, requirements, test_cases,
test_case_versions, test_suites, suite_test_cases, ba_reviews,
metadata_impacts, tags, test_case_tags, milestones, milestone_suites,
custom_fields, custom_field_values, step_templates,
test_case_parameter_sets *(all with soft-delete columns)*

**Execution** (14): pipeline_runs *(+ `source_refs`, `parent_run_id`)*,
pipeline_stages, run_test_results, run_step_results *(+ 7 log-capture
columns)*, run_artifacts, run_created_entities, run_cleanup_attempts,
execution_slots, worker_heartbeats, data_templates, data_factories,
data_snapshots, test_case_data_bindings, test_case_risk_factors

**Intelligence** (6): entity_dependencies, explanation_requests,
failure_patterns, behaviour_facts, step_causal_links, **agent_fix_attempts**

**Release** (6): releases, release_requirements, release_impacts,
release_test_plan_items, release_runs, release_decisions *(+
`agent_verdict_counts`)*

**Runs** (1): **scheduled_runs**

**Vector** (1): embeddings

## Migrations (001–023)
- 001–015: platform, test management, execution, intelligence, release, data engine, risk, step comments, tags/milestones, custom fields
- **016**: Test management soft delete + pg_trgm + composite/partial indexes
- **017**: Super Admin role, `pipeline_runs.source_refs` + `parent_run_id`
- **018**: `run_step_results` log-capture columns (SOQL, LLM prompt/response, http_status, timings, failure_class, correlation_id)
- **019**: `tenant_agent_settings` + `release_decisions.agent_verdict_counts`
- **020**: `meta_sync_status`
- **021**: `scheduled_runs`
- **022**: `agent_fix_attempts`
- **023**: `test_cases.is_quarantined` + quarantine metadata

## API Endpoints (~130)

**Run Wizard / Preview / SSE**:
- `GET /api/runs/:id/events` — SSE live step timeline (R1)
- `GET /api/metadata/:mv/sync-events` — SSE metadata sync progress (R3)
- `GET /api/metadata/:env/sync-status` — per-category status (R3)
- `POST /api/metadata/:env/refresh` — optional `categories[]` selection (R3)
- `GET /api/jira/:conn/projects | /projects/:key/boards | /boards/:id/sprints` — Jira picker (R1)
- `GET /api/_internal/health` — p50/p95 latency + error-rate counters

**Scheduled runs**: `/runs/scheduled` + CRUD views, scheduler-driven fire

**Agent**:
- `POST /runs/agent-fixes/:id/accept` — user accepts an auto-applied fix
- `POST /runs/agent-fixes/:id/revert` — restore before-state snapshot
- `POST /runs/:id/rerun-failed` — re-execute only the failed tests
- `GET /runs/:id/compare` — diff vs last green run

**Plus ~100 endpoints from earlier phases** (auth, core, metadata, test
management, AI generation, bulk ops, execution, intelligence, data engine,
release, CI/CD).

## Web UI Pages (~45)

- `/login` — auth
- `/` — dashboard (release health, pass rate, flaky tests)
- `/releases` list, new, detail (tabs: Changes, Impacts, Plan, Runs, Decision)
- `/requirements` list with Jira import + AI generate
- `/runs`, `/runs/new` (Run Wizard), `/runs/new/preview` (Preflight preview),
  `/runs/:id` (live SSE timeline + Agent fixes tab + Compare + Rerun failed)
- **`/runs/scheduled`, `/runs/scheduled/new`, `/runs/scheduled/:id/edit`** (R4)
- `/test-cases`, `/test-cases/:id`, `/test-cases/:id/edit`
- `/suites`, `/suites/:id`
- `/milestones`
- `/reviews`, `/reviews/:id`
- `/impacts` with AI Regenerate
- `/settings` — sidebar
  - General, Connections, Environments *(with per-category metadata sync)*,
    Groups, Users, Test Data, **Agent autonomy** *(Super Admin only)*

## Roles

| Role | Added | Permissions |
|---|---|---|
| `viewer` | baseline | Read-only |
| `ba` | baseline | + review workflow |
| `tester` | baseline | + create/edit test cases, trigger runs |
| `admin` | baseline | + connections, environments, groups, users, purge |
| **`superadmin`** | R2 | god-mode: cost visibility, agent autonomy config, raw LLM prompts, pre-flight override, excluded from 20-user cap. Seeded per tenant (`admin@primeqa.io` promoted on migration 017). |

## Tests (~155 passing across 12 suites)
- test_auth (15), test_environments (14), test_metadata (10)
- test_management (23), test_hardening (17)
- test_pipeline (12), test_executor (15), test_cleanup (9)
- test_intelligence (11)
- **test_run_experience (14), test_r2_superadmin (7), test_r3_metadata (6),
  test_r4_schedule (7), test_r5_agent (7), test_r6_polish (5)**

---

## The End-to-End Release Intelligence Flow

```
1. PM creates Release "Sprint 42" with decision criteria
2. Import Jira tickets → Requirements → Attach to Release
3. Metadata refresh (per-category, resumable) → Impacts linked
4. "Score Risks" ranks impacts + test plan by blast radius + criticality
5. "AI Generate" produces test cases with confidence score
6. Low-confidence tests auto-assigned for BA review
7. Tester opens Run Wizard → picks Jira project/sprint + suites + hand-picks
8. Preview shows pre-flight checks, ETA, cost (super admin), skipped tests
9. Executor runs; SSE streams step-by-step to the UI
10. Test Data Engine resolves {{template.X}} / {{factory.Y}}
11. On failure: agent triages (regex taxonomy + failure_patterns), proposes
    a fix; if confidence ≥ 0.85 and env is sandbox → auto-apply + rerun
12. User accepts or reverts the fix (snapshot-based) from the run detail
13. Cleanup engine reverse-deletes entities
14. "Evaluate GO/NO-GO" runs Decision Engine → recommendation + reasoning
15. CI/CD webhook triggers runs; /api/releases/:id/status respects the
    per-release `agent_verdict_counts` flag for pre- vs post-agent verdict
16. Scheduler cron fires nightly suite runs against whichever env
17. Dashboard shows pass rate trends, flaky tests, release health;
    auto-quarantine flags chronically flaky tests
```

## Known Limitations / Follow-Ups
- **Executor → agent dispatch wiring** (R5 caveat): the `execute_step`
  failure path needs one call into `AgentOrchestrator.handle_failure`. All
  primitives are in place.
- **Email provider** (Q4 deferred): `NOTIFICATIONS_PROVIDER=log` today.
  Flip to SendGrid / SES when chosen.
- **Per-category meta retry without new version** (R3 caveat): retrying a
  failed category currently creates a new `meta_version` rather than
  resuming the partial one. `SyncEngine` already supports resume.
- **Proactive "Suggested runs"** (Q10 deferred): revisit after R5 usage data.
- **Run Preview Refine filter surface** (Q13 deferred): waiting on usage.
- Health check disabled on Railway (was blocking deploys)
- Tests write to shared Railway DB (cleanup between runs is manual)
- Custom fields / step templates / parameter sets have schema + API, no UI
- Metadata diff viewer is still text-diff, no side-by-side
- No test-result artifact viewer (screenshots schema exists, no UI)

## Environment Variables Required

| Variable | Purpose | Set by |
|----------|---------|--------|
| `DATABASE_URL` | PostgreSQL connection | Railway |
| `JWT_SECRET` | JWT signing (64-char hex) | Manual |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet for credentials (64-char hex) | Manual |
| `WEBHOOK_SECRET` | CI/CD HMAC signing | Optional |
| `NOTIFICATIONS_PROVIDER` | `log` (default) / `sendgrid` / `ses` | Optional |
| `PORT` | HTTP port | Railway |
| `FLASK_ENV` | `production` | Manual |
