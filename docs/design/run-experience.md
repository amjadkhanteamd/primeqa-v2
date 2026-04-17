# Run Experience — Design Doc (R1–R6)

> **Status: ALL SIX PHASES SHIPPED.** Commits: R1 `9045dfc`, R2 `aaa47d1`,
> R3 `4ede25c`, R4 `452eb33`, R5 `479e483`, R6 `82d0a1e`. 46 new tests
> across R1–R6, all passing. See PROJECT_STATE.md for the live feature
> matrix and known follow-ups (executor→agent dispatch wiring, email
> provider, per-category meta retry without new meta_version).

## Context

The current Run flow is the weakest UX story in the app. Users can create a
pipeline run, but the experience is bare: no pre-run preview, no live
step-by-step feedback, no rich failure logs, no scheduling, no agent-assisted
triage. The vision: users pick Jira projects / sprints / suites / hand-picked
tests, see a decision-layer preview (what will run, against which org, with
which LLM, ETA), watch execution stream step-by-step, and when tests fail, an
agent can triage, auto-fix on sandbox (with diff + revert), and rerun. All
actions are audited so we have a corpus for a smarter v2 agent later.

This doc captures every locked decision from the Q1–Q14 discussion, maps them
to a phased rollout (R1–R6), lists schema/files touched per phase, and names
the deferred items so nothing is lost.

---

## Personas and roles

| Role | Exists today? | Purpose |
|---|---|---|
| `viewer` | ✔ | Read-only |
| `ba` | ✔ | Reviews AI-generated tests |
| `tester` | ✔ | Creates/edits tests, runs them |
| `admin` | ✔ | Tenant administration |
| **`superadmin`** | new in R2 | Cross-role "god mode" per tenant; sees cost forecasts + raw LLM prompts; configures agent autonomy; sole override for pre-flight failures and run-size caps |

Super Admin is seeded at onboarding (at least one per tenant) and **does not
count toward the 20-user cap**.

---

## Locked decisions

### Architecture

| ID | Decision |
|---|---|
| Q1 | Jira Kanban selection: epic+status picker + JQL "Advanced" toggle |
| Q5 | **Scheduling v1 = Test Suites only**. Jira sprints, mixed selections, and hand-picks are Run-Now only. Revisit after usage data. |
| Q6 | Run-size caps: soft **100**, hard **500**, same for sandbox and production. Super Admin can exceed the hard cap with typed `OVERRIDE` confirmation. |
| Q7 | **No `RunPlan` table.** Provenance lives in `pipeline_runs.source_refs JSONB`. "Rerun" on the history page re-POSTs the same payload. |
| Q8 | Agent rollback: **full before-state JSON snapshot** per fix attempt. Revert overwrites with the snapshot. |
| Q9 | Cron UX: presets dropdown (Hourly / Daily at X / Weekdays at X / Weekly / Custom) with **Advanced** toggle exposing a raw cron textarea. Bidirectional: preset ↔ cron translator. |
| Q11 | Metadata sync is a DAG: `objects → {fields, record_types} → {validation_rules, flows, triggers}`. On parent failure, **dependents skip with `skipped_parent_failed`**; siblings of the failed category still run. |

### Policy / behaviour

| ID | Decision |
|---|---|
| Q2 | Scheduled production runs **allowed**. Agent auto-apply remains blocked on production regardless of environment type. |
| Q3 | `/api/releases/:id/status` verdict: **per-release flag `agent_verdict_counts`**, default **post-agent**. Pre-agent verdict available in UI as "original result." |
| Q12 | Agent trust bands: default **High ≥ 0.85**, **Medium 0.60–0.85**, **Low < 0.60**. **Super Admin configurable** per tenant in agent settings. |
| Q14 | No named run presets. Rerun off history instead. |

### Pre-Q decisions (carry forward)

- Jira fetch **on demand** (no TTL). Session-scoped cache so back/forward is instant.
- Metadata partial state: **per-test skip** at pre-flight (`referenced_entities` check). Never silent failure; row status `skipped_metadata_stale`.
- Agent rerun = **new `pipeline_run` with `parent_run_id`**. Max 3 attempts per test case per parent run.
- Pre-flight override: **Super Admin only**, typed `OVERRIDE` confirmation.
- Super Admin bootstrap: migration 017 promotes `admin@primeqa.io` to superadmin on the existing tenant; new tenants get one seeded during onboarding.
- Log retention: **90 days** on bulky payloads; structured rows forever.
- Storage: **Postgres only** (JSONB + large-text columns). No S3 in v1.
- Agent sandbox: auto-apply only when `confidence ≥ High threshold` AND `env.env_type != 'production'`.
- Agent production: always human-gated, never auto-apply.

### Deferred (with revisit trigger)

| ID | Deferred | Trigger to revisit |
|---|---|---|
| Q4 | Email provider (SendGrid / SES / SMTP / stub) | Before R6 ships notifications |
| Q10 | Proactive "Suggested runs" (Jira-change-based nudges) | After R5 agent loop lands + usage data |
| Q13 | Run Preview "Refine" filter surface | After we see what users actually want to filter on |

---

## Schema changes (sequential migrations)

| # | Migration | Phase | Adds |
|---|---|---|---|
| 017 | `superadmin_role_and_source_refs.sql` | R1 + R2 | `users.role` CHECK adds `'superadmin'`; promotes `admin@primeqa.io`; `pipeline_runs.source_refs JSONB` for rich provenance; `pipeline_runs.parent_run_id` for rerun lineage |
| 018 | `agent_settings_and_pipeline_run_logs.sql` | R1 | `run_step_logs(step_id, request_body JSONB, response_body JSONB, soql_queries JSONB, llm_prompt TEXT, llm_response TEXT, http_status, timings_ms JSONB, created_at)` with 90-day retention trigger; `tenant_agent_settings(tenant_id, trust_threshold_high NUMERIC, trust_threshold_medium NUMERIC, agent_enabled BOOL)` |
| 019 | `meta_sync_status.sql` | R3 | `meta_sync_status(id, meta_version_id, category, status, items_count, started_at, completed_at, error_message, retry_count)`; CHECK on `category` and `status` |
| 020 | `scheduled_runs.sql` | R4 | `scheduled_runs(id, tenant_id, suite_id, env_id, llm_connection_id, cron_expr, next_fire_at, last_fired_at, enabled, max_silence_hours, created_by, created_at)` |
| 021 | `agent_fix_attempts.sql` | R5 | `agent_fix_attempts(id, run_id, test_case_id, step_id, failure_class, pattern_id, root_cause_summary TEXT, confidence NUMERIC, proposed_fix_type, before_state JSONB, auto_applied BOOL, rerun_run_id, rerun_outcome, user_decision, created_at, decided_at)`; `release_decisions.agent_verdict_counts BOOL DEFAULT true` |

---

## Phased rollout

### R1 — Unified Run Wizard + Preview + Live Logs (4–5 days)

**Goal**: replace the current "bare POST" run trigger with a guided wizard that
ends in a decision-layer preview, then streams execution step-by-step.

Files:
- `primeqa/runs/wizard.py` (new service) — unifies source resolution (Jira project/sprint/epic/JQL/issue, suite, section, hand-picked test_case_ids) into a flat deduplicated `test_case_id` list with structured `source_refs` provenance.
- `primeqa/runs/preflight.py` (new) — credential check, metadata freshness, `referenced_entities` validation, prod-safety, run-size caps, LLM reachability.
- `primeqa/runs/streams.py` (new) — SSE endpoint `GET /api/runs/:id/events` emitting `step_started` / `step_finished` / `run_finished` events. Backed by in-process pubsub (Redis later if fanout needed).
- `primeqa/execution/executor.py` — capture request/response/SOQL/LLM prompt+response into `run_step_logs` per step.
- `primeqa/templates/runs/wizard.html` (new), `wizard/preview.html` (new), `runs/detail.html` (rewrite for live timeline + per-step tabs).
- `primeqa/views.py` — `/runs/new` GET renders wizard; POST validates + redirects to `/runs/:id` which subscribes to SSE.

Decisions realised: Q1, Q6, Q7 (`source_refs` JSONB), Q11 (pre-flight), pre-Q per-test metadata skip.

Acceptance:
1. Tester picks Jira project → scrum sprint → 18 issues → wizard expands to 52 test cases, shows preview: org / LLM / meta version / 52 tests / ETA 4m.
2. Pre-flight catches expired credentials → blocks run with clear error.
3. Run page renders `[✓] Create Account … [✓] Verify … [✗] Update Stage` in real time via SSE.
4. Click a failed step → tabs Request / Response / SOQL / LLM show captured payloads.
5. Run-size of 600 → blocked with Super Admin override dialog.
6. Kanban project path: pick project → pick epic → pick status → see issues; JQL toggle works.

### R2 — Super Admin role + cost visibility + agent autonomy config (1–2 days)

**Goal**: give `admin@primeqa.io` god-mode and gate cost/agent controls.

Files:
- `migrations/017_superadmin_role_and_source_refs.sql` — role constraint, promote existing admin, source_refs + parent_run_id columns.
- `primeqa/core/auth.py` — `require_role` treats `superadmin` as implicit pass.
- `primeqa/runs/cost.py` (new) — Anthropic token-cost forecast + SF API call estimate. Per-model pricing lives in `config/llm_pricing.yaml`.
- `primeqa/templates/settings/agent.html` (new) — Super-Admin-only page for trust bands + agent_enabled toggle per env.
- `primeqa/templates/runs/wizard/preview.html` — adds cost block only when `user.role == 'superadmin'`.
- `primeqa/core/repository.UserRepository` — user count query excludes `superadmin` from 20-user cap.

Decisions realised: Q2, Q6 override, Q12, pre-Q Super Admin bootstrap.

Acceptance:
1. `admin@primeqa.io` logs in after migration → sees cost + trust-band settings.
2. `tester@…` logs in → never sees cost; `Agent settings` page returns 403.
3. Super Admin tweaks trust threshold to 0.9; new agent fixes below 0.9 no longer auto-apply.

### R3 — Metadata refresh redesign (2–3 days)

**Goal**: replace the current "refresh everything in one transaction, cross your fingers" with a per-category DAG, independent commits, SSE progress, retryable categories.

Files:
- `migrations/019_meta_sync_status.sql`.
- `primeqa/metadata/sync_engine.py` (new) — orchestrates the DAG, commits per category, writes `meta_sync_status` rows.
- `primeqa/metadata/sf_limits.py` (new) — pre-check `/services/data/vXX/limits`; exponential backoff on `Sforce-Limit-Info`.
- `primeqa/metadata/routes.py` — `POST /api/metadata/sync` accepts `categories` array + SSE endpoint `GET /api/metadata/sync/:meta_version_id/events`.
- `primeqa/templates/settings/environments/metadata.html` — six-checkbox UI, per-category status card, "Retry this category" button.
- Pre-flight (from R1) consumes `meta_sync_status` to decide per-test skip.

Decisions realised: Q11, pre-Q metadata partial state.

Acceptance:
1. Pick only `objects` + `fields` → sync succeeds, VR/flow/trigger stay `skipped_parent_failed` with no error.
2. Force SF 503 mid-sync → only that category flips to `failed`; retry button works without redoing the completed categories.
3. Copy in a run that references a `validation_rule` while VRs are unsynced → that test gets `skipped_metadata_stale`; other tests in the run execute.

### R4 — Full-cron scheduling (suites only) (2 days)

**Goal**: let users attach cron schedules to test suites, with dead-man's-switch alerting.

Files:
- `migrations/020_scheduled_runs.sql`.
- `primeqa/scheduler.py` — extends existing scheduler service to poll `scheduled_runs` where `next_fire_at <= now AND enabled`; creates a `pipeline_run` and advances `next_fire_at`.
- `primeqa/runs/cron_helpers.py` (new) — uses `croniter`; `preset_to_cron` and `cron_to_preset` translators.
- `primeqa/templates/runs/scheduled_list.html`, `scheduled_new.html`, `scheduled_detail.html`.
- `primeqa/views.py` — `/runs/scheduled` routes.
- Dead-man's switch: scheduler daemon writes `last_fired_at`; a daily job emails (or logs, pre-R6) super admins if any schedule's `last_fired_at + max_silence_hours < now`.

Decisions realised: Q2, Q5, Q9.

Acceptance:
1. Pick suite "Smoke — UAT", pick preset "Daily at 2am" → stored as `0 2 * * *`, scheduled_run row created, `/runs/scheduled` lists it.
2. Advanced toggle → type `0 */4 * * 1-5` → preset label shows "Custom"; still saves; still fires.
3. Kill the scheduler for > `max_silence_hours` → dead-man's switch fires on next startup.

### R5 — Agent fix-and-rerun loop (5–7 days)

**Goal**: on run failures, a classifier+LLM agent triages, proposes a fix, (optionally) auto-applies on sandbox with a diff visible, reruns, and records the whole decision.

Files:
- `migrations/021_agent_fix_attempts.sql`.
- `primeqa/intelligence/triage.py` (new) — deterministic classifier: matches `run_step_logs.first_error_line` against existing `failure_patterns` regex; sets `failure_class` and skips LLM if confident.
- `primeqa/intelligence/fix_proposer.py` (new) — LLM agent, reads failed step + relevant logs + metadata diff + recent similar failures; outputs `proposed_fix_type`, `before_state`, `after_state`, `root_cause_summary`, `confidence`.
- `primeqa/intelligence/fix_applier.py` (new) — gates on env_type + confidence + trust band + agent_enabled; writes `agent_fix_attempts`; for `edit_step` / `regenerate_test` / `update_template`, applies and creates a new pipeline_run with `parent_run_id` and `fix_attempt_number`.
- `primeqa/execution/executor.py` — on test failure, calls triage; on return, calls fix_proposer asynchronously (doesn't block the rest of the run).
- `primeqa/templates/runs/detail.html` — new "Agent fixes" tab; diff viewer; `Accept` / `Revert` / `Edit` buttons; trust band label with confidence %.
- `primeqa/release/routes.py` — `/api/releases/:id/status` respects `release_decisions.agent_verdict_counts`.
- Rerun cap: 3 attempts per test case per parent-run lineage.

Decisions realised: Q3, Q8 (snapshot revert), Q12 (trust bands), pre-Q agent rules (≥High on sandbox, prod always gated, 3-attempt cap).

Acceptance:
1. Failure → classifier matches "VR required field missing" pattern → no LLM call → UI shows class badge.
2. Unmatched failure → LLM proposes `edit_step` with confidence 0.91 on sandbox → auto-applies → reruns → passes → Agent fixes tab shows green diff.
3. Same failure on production env → proposer runs but applier creates a BA-review task instead.
4. User clicks **Revert** on a bad fix → before_state snapshot restored → rerun regresses → audit row shows `user_decision='reverted'`.
5. CI polling `/api/releases/:id/status` gets post-agent verdict; UI shows "original result: 2 failed / after-agent: 0 failed."

### R6 — Polish (3–4 days)

**Goal**: long-tail UX that elevates the experience.

In scope:
- **Email notifications** (requires Q4 decision before start). Targets: run_complete / run_failed / agent_fix_applied / scheduled_run_dead.
- **Flake quarantine**: test flagged after N toggles in a rolling window; excluded from `release_decisions` gating; visible in flaky-tests panel.
- **Rerun subset**: select failed tests on a run detail → "Rerun these N" creates a new pipeline_run seeded with the same `source_refs` but filtered to those test_case_ids.
- **Comparison view**: "this run vs last successful" — highlight flipped tests and which step flipped them.

Decisions realised: Q4 (to be unblocked first).

---

## Cross-cutting concerns

- **Observability**: the existing `primeqa/shared/observability.py` stays authoritative. Agent actions emit `X-Correlation-ID`-tagged logs so Anthropic / Railway / SF logs can be cross-referenced per run.
- **Permissions**: everything role-gated via `require_role`. Super Admin gets implicit pass.
- **Retention**: `run_step_logs` gets a daily cron that deletes rows older than 90 days (preserves the structured `run_step_results` summary row forever). Trigger lives in the scheduler.
- **Performance**: `run_step_logs` JSONB columns use `jsonb_path_ops` GIN indexes for searchable fields only (`first_error_line`, `failure_class`). No index on full request/response bodies.
- **Testing**: each phase adds tests under `tests/test_runs_*.py`. Final release-flow demo verifies end-to-end (wizard → preview → run → failure → agent auto-fix → rerun → CI sees post-agent verdict).

---

## Out of scope (explicit)

- CSRF protection (inherited from earlier deferral)
- Rate limiting
- S3-compatible blob storage (Q: postgres only)
- Run templates / named presets (Q14)
- RunPlan table (Q7)
- Multi-org matrix runs
- Branch/PR linkage from CI
- Cost caps / budgets
- Replay mode (re-run from stored logs without SF)
- Suggested runs / proactive Jira-change nudges (Q10)
- Run Preview "Refine" filter surface (Q13)
- Email provider decision (Q4 — must land before R6 start)

---

## Verification

Per-phase acceptance lists above. End-to-end demo that ties it all together:

1. Super Admin opens Run Wizard → picks Jira Project X → current active sprint → 18 issues → preview shows 52 tests / UAT / claude-sonnet-4 / ETA 4m / cost $1.20.
2. Pre-flight: 1 VR reference is stale → 1 test flagged `skipped_metadata_stale` with "Refresh validation_rules" link.
3. User clicks **Run** → live SSE timeline streams 51 steps (1 skipped).
4. 3 tests fail → triage classifies 1 as "transient", 2 go to LLM proposer.
5. Proposer returns confidence 0.92 for one (auto-applies edit_step on sandbox, reruns, passes) and 0.74 for the other (queued as BA review, user sees "Review recommended" band).
6. Run detail shows: 49 passed / 1 skipped / 1 agent-fixed / 1 pending review.
7. CI polls `/api/releases/42/status` → gets post-agent verdict (50 passed, 1 skipped, 1 pending) because `agent_verdict_counts=true`.
8. User clicks **Revert** on the auto-fix (for audit exercise) → snapshot restored → rerun regresses → audit log records the decision.
9. User saves the smoke suite on a nightly `0 2 * * *` schedule; scheduler fires next night.
10. Metadata refresh: user picks `validation_rules` only → VRs sync; other categories skipped deliberately; pre-flight warnings on next run disappear for VR-referencing tests.

Every step above exercises a locked decision and maps back to the phase that delivered it.
