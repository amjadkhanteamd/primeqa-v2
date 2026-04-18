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
  latency + slow-query log at 800 ms (tunable via `PRIMEQA_SLOW_QUERY_MS`; default sits above Railway's ~400\u2013500 ms RTT), exposed at `/api/_internal/health`)
- UI: pagination / toast / confirm-modal / breadcrumbs components; JS helpers
  (`toast.js`, `confirm.js`, `unsaved_changes.js`); library + detail + edit
  pages rewritten across all entities.

### Run Experience (R1–R7) — shipped

| Phase | Commit | Scope |
|---|---|---|
| R1 | `9045dfc` | Unified Run Wizard (Jira projects/sprints/epics/JQL + suites + sections + requirements + hand-picks), Preflight with per-test metadata skip, live SSE step timeline, log-capture columns on `run_step_results`, Super Admin role bootstrap |
| R2 | `aaa47d1` | Super Admin role gating, cost forecaster (Anthropic pricing table), `tenant_agent_settings` + Agent autonomy settings page, user-cap exclusion for superadmin |
| R3 | `4ede25c` | `meta_sync_status` DAG (objects → {fields, record_types} → {VRs, flows, triggers}), per-category sync + SSE progress, parent-fail cascade, preflight reads per-category health, environment detail with 6 status cards + selective refresh |
| R4 | `452eb33` | `scheduled_runs` table (suites only per Q5), presets + advanced cron toggle, scheduler tick, dead-man's switch, `/runs/scheduled` UI |
| R5 | `479e483` | `agent_fix_attempts` ledger, deterministic triage + LLM proposer, sandbox auto-apply gate at trust-band high, snapshot-based revert, release status respects `agent_verdict_counts` |
| R6 | `82d0a1e` | Notification dispatch stub, flake quarantine with auto-tag, `/runs/:id/rerun-failed`, `/runs/:id/compare` against last green |
| **R7** | **`8316317`** | **Jira ticket picker: searchable multi-select with chips + 8 s TTL cache, dual-mode `/api/jira/search` (HTML fragment + JSON), `POST /api/runs/preview` (read-only resolver reuse), sticky live selection summary bar. Replaces CSV issue-key input.** |

**56 new tests across R1–R7, all passing against Railway DB.** Full plan and
decision ledger in `docs/design/run-experience.md`.

### Post-R7 enhancements — shipped (April 2026)

Continuous UX + infra improvements on top of R1–R7. Each commit is below.

| Commit | Scope |
|---|---|
| `4e268e6` | **Worker executor wiring.** `execute_stage` was a stub flipping every stage to `passed` without running anything — a Run #69 showed "0/0/0 tests" even though it reported complete. Replaced with a dispatcher that routes `execute` → `_run_execute_stage` (resolves test_case_ids, fetches SF OAuth, builds `StepExecutor`, iterates `version.steps`, commits per-TC totals, emits `test_finished` SSE) and `record` → `_run_record_stage` (PipelineService.complete_run / fail_run). |
| `defe03e` | **Stage-sequencing race + atomic rtr + $var fail-fast.** (1) A Railway worker redeploy mid-run left `execute` stuck in `running` while a second worker raced ahead to `record` and called `complete_run` with stale 0/0/0 counts. Fixed by `get_next_pending_stage` now blocking on any running predecessor. (2) Direct ORM mutation on a potentially-expired `RunTestResult` instance was silently dropping writes — switched to `update_result()` re-fetch + commit. (3) Executor now fails fast with `"Unresolved reference variable(s): $foo — no prior step stored them. Available vars: (none). Fix the test case so a prior create step sets \`state_ref\` to the matching $var."` before sending the literal to Salesforce and getting back a cryptic `MALFORMED_ID`. |
| `275d40a` | **`SectionRepository.create_section` is idempotent.** If an active section with the same `(tenant_id, parent_id, name)` exists, it's returned instead of creating a dup. Stopped the bleed of 8× "Regression Tests" root sections that integration tests had accumulated. `scripts/cleanup_test_pollution.sql` is a reviewable, transactional one-shot that soft-deletes the pre-existing pollution. |
| `001a9e7` / `b002f64` / `4119f47` / `09effa1` | **Durable pipeline log (migration 027).** In-process SSE EventBus fires only within one process, so on Railway (split web/worker/scheduler) the run detail page saw "Waiting for steps to start…" for the first 15+ seconds of every run. New `run_events` table is the durable sink: worker writes every milestone (stage transitions, OAuth fetch, test-case resolution, per-step progress), SSE endpoint tails the table every ~1s for cross-service delivery. On connect, last 200 events are backfilled so refresh repopulates the log. Structured hierarchical log panel on `/runs/:id` groups by event kind (run / stage / test / step) with color + icon; download .txt / .json for any run. Scheduler trims to 1000 events/run. `record_event` uses its own `Session(bind=engine)` to avoid closing the caller's scoped session. |
| `a74401a` | **Test-case supersession.** Every click of "Generate test case" on the same requirement was creating a new TC. New model: **one requirement → one active draft per user**. On regenerate, prior own-drafts are soft-deleted and a new version rolls onto the kept TC. Approved / active TCs are immutable and spawn a fresh TC alongside. Version-awareness UI on TC detail: `Draft v3` chip + "updated 2 min ago" relative time + "View history" jump link. |
| `bfd0a48` | **Requirements UX refresh (phases 1–3).** (1) "+ New Requirement" button for manual requirements (no Jira). (2) Shared `components/generate_overlay.html` — rotating spinner labels so Generate doesn't look frozen; ended the double-click → dup-TC pattern. (3) Chip-picker in the Import modal with live HTMX search via `/api/jira/search?conn_id=...` (endpoint extended to accept direct connection id for the import flow); paste fallback for raw keys. |
| `70db511` | **Bulk-generate (phase 4).** Checkbox per requirement + sticky bulk bar. `POST /api/requirements/bulk-generate` runs ≤5 in parallel via `ThreadPoolExecutor` with per-thread `Session(bind=engine)`, hard cap 20 per call. Modal summarises per-row results. |
| `06d4673` | **Multi-TC generation (migration 028).** One click → 3–6 test cases covering `positive / negative_validation / boundary / edge_case / regression`. `TestCaseGenerator.generate_plan()` returns a plan; `generate_test_plan()` creates one `generation_batches` row + N TCs with `coverage_type` + `generation_batch_id`. Batch-wide supersession (all prior own-drafts replaced). Executor name prefix bumped to `PQA_{run_id}_{tc_id}_{logical_id}` to prevent same-requirement TCs colliding on SF name uniqueness. UI: Coverage column on Test Library with colored badges, "AI test plan rationale" callout on requirement detail with cost (superadmin), linked TCs grouped by coverage bucket. Bulk-generate modal reports "Generated 18 test cases across 4 requirements". |

### Self-Validation Suite (`a9da9d3`) — shipped

JSON-driven, workflow-level end-to-end suite that exercises PrimeQA through
its own HTTP surface. Motto: **"run PrimeQA on PrimeQA"** before every deploy.

- `primeqa/system_validation/` — runner + step grammar (http / verify / save /
  login / wait / assert_db) with `$var` substitution, dotted/list-indexed paths,
  `$uuid` for idempotency
- `primeqa/system_validation/suites/primeqa_core.json` — canonical 8-category
  suite (Requirements, Test Library, Run Flow, Jira, **Preview — the canary**,
  Metadata, Agent, UI Navigation)
- `tests/test_system_validation.py` — runner unit tests + drives the canonical
  suite: currently **13 passed, 0 failed, 3 skipped (with documented reasons),
  16 total**
- Design + roadmap in `docs/design/system-validation.md`

---

## Database (63+ tables)

**Core domain** (11): tenants, users, refresh_tokens, environments,
environment_credentials, activity_log, groups, group_members,
group_environments, connections, **tenant_agent_settings**

**Metadata** (8): meta_versions *(+ `delta_since_ts`, background-job
columns)*, meta_objects, meta_fields, meta_validation_rules, meta_flows,
meta_triggers, meta_record_types, **meta_sync_status**

**Test Management** (17): sections, requirements, test_cases *(+
`coverage_type`, `generation_batch_id`)*, test_case_versions, test_suites,
suite_test_cases, ba_reviews, metadata_impacts, tags, test_case_tags,
milestones, milestone_suites, custom_fields, custom_field_values,
step_templates, test_case_parameter_sets, **generation_batches**
*(all with soft-delete columns)*

**Execution** (15): pipeline_runs *(+ `source_refs`, `parent_run_id`)*,
pipeline_stages, run_test_results, run_step_results *(+ 7 log-capture
columns)*, **run_events** *(durable pipeline log, migration 027)*,
run_artifacts, run_created_entities, run_cleanup_attempts,
execution_slots, worker_heartbeats, data_templates, data_factories,
data_snapshots, test_case_data_bindings, test_case_risk_factors

**Intelligence** (6): entity_dependencies, explanation_requests,
failure_patterns, behaviour_facts, step_causal_links, **agent_fix_attempts**

**Release** (6): releases, release_requirements, release_impacts,
release_test_plan_items, release_runs, release_decisions *(+
`agent_verdict_counts`)*

**Runs** (1): **scheduled_runs**

**Vector** (1): embeddings

## Migrations (001–028)
- 001–015: platform, test management, execution, intelligence, release, data engine, risk, step comments, tags/milestones, custom fields
- **016**: Test management soft delete + pg_trgm + composite/partial indexes
- **017**: Super Admin role, `pipeline_runs.source_refs` + `parent_run_id`
- **018**: `run_step_results` log-capture columns (SOQL, LLM prompt/response, http_status, timings, failure_class, correlation_id)
- **019**: `tenant_agent_settings` + `release_decisions.agent_verdict_counts`
- **020**: `meta_sync_status`
- **021**: `scheduled_runs`
- **022**: `agent_fix_attempts`
- **023**: `test_cases.is_quarantined` + quarantine metadata
- **024**: `requirements` unique `(tenant_id, jira_key)` partial index (active rows only) so soft-deleted Jira imports don't block re-import
- **025**: `meta_versions` background-job columns (queued_at, triggered_by, categories_requested, worker_id, heartbeat_at, cancel_requested, parent_meta_version_id) for the new async metadata-sync worker
- **026**: `meta_versions.delta_since_ts` for quick-refresh delta syncs driven by the preview-page drift banner
- **027**: `run_events` durable pipeline log (id, run_id, tenant_id, ts, kind, level, message, context jsonb) + partial index on `(tenant_id, ts desc)` where level in (warn, error)
- **028**: `test_cases.coverage_type` + `test_cases.generation_batch_id`, new `generation_batches` table (model, input/output tokens, cost_usd, explanation, coverage_types[]) for multi-TC test-plan generation

## API Endpoints (~140)

**Run Wizard / Preview / SSE**:
- `GET /api/runs/:id/events` — SSE live step timeline with DB-tail fallback for cross-service delivery (R1 + post-R7 durable-log update)
- **`GET /api/runs/:id/events/download?format=json|txt`** — full event history export (post-R7)
- `GET /api/metadata/:mv/sync-events` — SSE metadata sync progress (R3)
- `GET /api/metadata/:env/sync-status` — per-category status (R3)
- `POST /api/metadata/:env/refresh` — optional `categories[]` selection (R3)
- `GET /api/jira/:conn/projects | /projects/:key/boards | /boards/:id/sprints` — drill-down picker (R1, now Advanced)
- **`GET /api/jira/search?env_id=X|conn_id=Y&q=Z[&format=json]`** — ticket-level search with 8 s TTL cache, HTML fragment or JSON (R7; post-R7 added `conn_id` for the requirements-import chip picker)
- **`POST /api/runs/preview`** — read-only resolver: `{test_case_count, requirement_count, missing_jira_keys, warnings, summary_text, over_soft_cap, over_hard_cap}` (R7)
- `GET /api/_internal/health` — p50/p95 latency + error-rate counters

**Requirements / multi-TC generation** (post-R7):
- **`POST /requirements/new`** — manual (non-Jira) requirement create
- **`POST /requirements/import-jira`** — accepts `jira_keys` (comma/newline list from chip picker) or `jira_key` (single); reports imported / skipped-already-exists / failed
- **`POST /api/requirements/bulk-generate`** — generate plans for N requirements in parallel (cap 5, hard cap 20 per call)

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
- `/requirements` list with **+ New Requirement** (manual create modal), **Import from Jira** chip picker (multi-ticket HTMX search + paste fallback), checkbox multi-select + sticky bulk-generate bar ("Each requirement yields 3–6 test cases — up to 5 in parallel; max 20 per click"). Per-row Generate shows a rotating-label overlay.
- `/runs`, `/runs/new` (Run Wizard), `/runs/new/preview` (Preflight preview),
  `/runs/:id` (live SSE timeline + **durable "Pipeline log" panel** with hierarchical structured events + Download .txt/.json + Agent fixes tab + Compare + Rerun failed)
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

## Tests (~170 passing across 17 suites)
- test_auth (15), test_environments (14), test_metadata (10)
- test_management (23), test_hardening (17)
- test_pipeline (12), test_executor (15), test_cleanup (9)
- test_intelligence (11)
- test_run_experience (14), test_r2_superadmin (7), test_r3_metadata (6),
  test_r4_schedule (7), test_r5_agent (7), test_r6_polish (5)
- **test_r7_jira_picker (10)** — search JQL, TTL cache, dual-mode endpoint, preview
- **test_system_validation (4 + 13 suite outcomes)** — canonical self-validation suite

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
  failure path needs one call into `AgentOrchestrator.handle_failure`.
  The executor itself is now wired end-to-end (post-R7 commit `4e268e6`);
  the agent handoff is the remaining thread.
- **Dynamic test suites**: suites are static lists today. Multi-TC
  generation makes "query-based suites" (e.g. `coverage_type=positive AND
  section=Accounts`) more valuable. Deferred pending usage patterns.
- **Run Wizard coverage filter**: could add "Smoke = positives only"
  toggle now that TCs are tagged with `coverage_type`. Deferred.
- **Parallel TC execution within a run**: serial today. With multi-TC,
  5×-parallel could cut bulk-run time significantly. Deferred; risks
  include API rate limits and cleanup interleaving.
- **Post-generation linter**: the generator sometimes emits `$var`
  references without a matching `state_ref`. The executor fail-fasts
  with a clear message (post-R7 `defe03e`), but a save-time validator
  would catch it before the first run.
- **Email provider** (Q4 deferred): `NOTIFICATIONS_PROVIDER=log` today.
  Flip to SendGrid / SES when chosen.
- **Per-category meta retry without new version** (R3 caveat): retrying a
  failed category currently creates a new `meta_version` rather than
  resuming the partial one. `SyncEngine` already supports resume.
- **Proactive "Suggested runs"** (Q10 deferred): revisit after R5 usage data.
- **Run Preview Refine filter surface** (Q13 deferred): waiting on usage.
- Health check disabled on Railway (was blocking deploys)
- Tests write to shared Railway DB (cleanup between runs is manual);
  `scripts/cleanup_test_pollution.sql` is the reviewable one-shot for
  accumulated duplicate sections / orphan TCs when it matters.
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
