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
| `7044f10` | **Test Library group-by-requirement view.** Flat list was fine for 1 TC/requirement but became a stream with 3–6 TCs/requirement. Default view now groups TCs under their requirement with coverage breakdown chips and a coverage filter. Pagination moved up to "requirements per page" (20 default). Accordion state persists in `localStorage`. Flat toggle available. Requirement sort order: activity / jira_key / name. |
| `dc52a58` | **Suite detail overhaul + bulk-add API.** `/suites/:id` was view-only; the backend add/remove/reorder API existed but was unwired. Now: inline edit (name/type/description), delete from detail, coverage + requirements-covered summary strip, `+ Add test cases` picker modal, ↑/↓ reorder, Remove per row. `POST /api/suites/:id/test-cases/bulk` — tenant-scoped, dedupe-aware, cap 200. |
| `6ff40a4` | **Test Library per-group "+ Add to suite" + requirement sort.** Each accordion header on the grouped view gains an `+ Add to suite` button opening a coverage-chip modal (click chips to include/exclude subsets). Existing suites + `+ Create new suite` inline option. Requirement sort dropdown (activity / jira_key / name). |
| `21287b3` | **Grouped suite picker + suites list metadata + release curation UI.** Suite picker now groups TCs by requirement with group select-all + 3-state indeterminate checkboxes. Suites list shows TC count + coverage chips + requirements covered per row (single JOIN, no N+1). Release detail: `+ Add requirements` button on Requirements tab with search + multi-select + bulk-add, `+ Add test cases` on Test Plan tab with the same grouped-by-requirement picker + priority selector, Remove per row, TC title / status / coverage badges. New bulk endpoints `POST /api/releases/:id/requirements/bulk` and `POST /api/releases/:id/test-plan/bulk`. |
| `ab40337` | **Static test-case validator (migration 029).** New `TestCaseValidator` module catches AI hallucinations before runtime: object-not-found, field-not-found, field-not-createable, unresolved-`$var`, fields-not-synced. Fuzzy `difflib.get_close_matches(cutoff=0.6)` suggestions per issue. Three integration points: (1) after generation — validator runs during `generate_test_plan` and stores a `validation_report` JSONB on each new `test_case_version`; (2) before execution — worker blocks TCs with `status=='critical'` unless `config.skip_validation` / `force_run`; (3) during execution — existing `$var` fail-fast remains. API: `POST /api/test-cases/:id/revalidate`, `POST /api/test-cases/:id/apply-validation-fix`. UI: red/yellow/green banner on TC detail with per-issue Apply buttons that create a new version. |
| `2e10581` | **Validator SOQL parsing.** Extends the validator to parse `query` steps' SOQL strings: `SELECT` column list + `FROM` object. Two new rules (`soql_from_object_not_found`, `soql_column_not_found`). Relationship-aware: `Owner.Id` is valid when `OwnerId` exists as a reference field (`__r`→`__c` for custom lookups). Permissive parser — silent no-op when the SOQL can't be parsed, to avoid false positives blocking valid tests. `apply_fix` extended to rewrite bad FROM or bad SELECT column in the SOQL text (word-boundary-safe). |
| `38e3b46` | **Ship 1 — context-driven Run triggers.** Runs tab stops being the primary entry point; triggers move to the thing you want to run. Per-row `▶ Run` + TC count + coverage chips on the Requirements list (with per-user state-aware `Generate` / `Regenerate` / `Generate again` label). `▶ Run N test cases` on Requirement detail. `▶ Run test plan` on Release detail → Test Plan tab. New routes `POST /requirements/:id/run` + `POST /releases/:id/run`. Wizard demoted to "Advanced: build run" with a tip banner pointing to Requirements / Suites / Releases. |
| `78113a0` | **Ship 2 — AI spend panel on run detail.** Superadmin-only collapsible panel aggregating LLM cost per run: test generation total (from `generation_batches.cost_usd` joined via the run's TCs) + model list + tokens in/out + batch count; agent fix-and-rerun attempt count with a note that per-attempt token tracking is a future migration. Grand total in the summary line. |
| `dad9612` | **Ship 3 + HPV (migration 030).** Four upgrades: (1) per-failed-row `↻ Rerun` button queues a new run for a single TC; (2) `↻ Rerun verbatim` pins each TC to its prior `test_case_version_id` via `run.config.version_pin`, worker honors it ahead of `current_version_id`; (3) inline-edit `label` on run detail (debounced auto-save) + substring label filter on the run history page; (4) `Summarise failures` superadmin-only AI panel — prompts the env's LLM with every failed step's error text, caches result in `pipeline_runs.failure_summary_ai / _at / _model`. All gated behind role + terminal state. |
| `a1d07fe` | **Story view — BA-readable test cases (migration 048).** Adds a human-readable layer over AI-generated TCs: LLM-generated `title` / `description` / `preconditions_narrative` / `expected_outcome` rendered above the mechanical step list. Claude Haiku 4.5, ~800 tokens/TC (~$0.0004). Feature-flagged per tenant via `tenant_agent_settings.llm_enable_story_enrichment` (default off), toggled from superadmin `/settings/llm-usage`. New prompt module `prompts/story_view.py` with task `story_view_generation` routing to Haiku. Enrichment runs inside Prompt 15's atomic transaction via `StoryViewEnricher` — best-effort: LLM failures leave `story_view=NULL` and the render path falls back to the mechanical view (zero-backfill rollout). Shared Jinja macro `components/_tc_body.html` replaces duplicated step rendering across `test_cases/detail.html` and `reviews/detail.html` (three modes: `full`, `review_form`, `review_view`). Superadmin POST `/settings/tenant-tier/<id>` now persists both `llm_tier` and the story flag, logging each change to `activity_log` separately. 7 new tests in `tests/test_story_view.py`. |
| _(pending commit)_ | **Domain Packs — prescriptive knowledge for test_plan_generation (migration 049).** Adds a per-tenant opt-in parallel knowledge channel: markdown-with-YAML-frontmatter files under `salesforce_domain_packs/` describe specific Salesforce domains. When a requirement's text matches a pack's keywords (word-boundary + inflection-aware, via new shared `knowledge._text` module — reused by `detect_complexity` for consistent matching), the pack content is injected as an uncached fourth user-message block in the `test_plan_generation` prompt. Gives Sonnet concrete patterns to follow for domains where Opus was previously needed. **No attribution column**: mirrors the story_view precedent and rides the existing `llm_usage_log.context` JSONB under key `domain_packs_applied`. Feature flag `llm_enable_domain_packs` (default off), toggled via the "Packs" checkbox alongside "Story" in `/settings/llm-usage`. Applies on ALL routed tiers (Sonnet + Opus). v1 keyword-only (object-score path dormant until object extraction lands on requirements). First pack: `case_escalation.md` targeting SQ-205's pattern. 14 new tests in `tests/test_domain_packs.py`. |

### LLM Architecture (Phases 1–6) — shipped

An end-to-end rebuild of how PrimeQA talks to Anthropic. The old world had
five call sites, inconsistent retry behaviour, no caching, no per-tenant
rate limits, and no feedback loop from runtime back into prompts. The new
world has a single chokepoint, a feedback-aware prompt registry, product
tiers, and three superadmin dashboards.

**Core thesis**: one chokepoint makes policy debuggable (one file to edit
for any cross-cutting change); a feedback loop makes generation quality
improve with usage (validator + execution + supersession signals flow
back into the next prompt).

| Phase | Commit | Scope |
|---|---|---|
| 1 | `4cb1367` | **LLMGateway foundation.** `primeqa.intelligence.llm` package — `llm_call()` is the single entry point. `PromptRegistry` (one file per task in `prompts/*`), `ModelRouter` with task × complexity × tenant_policy → chain, `pricing` table, `usage.record()` writes to new `llm_usage_log` table (migration 031). Exponential backoff + jittered retry on 429/529/timeout/network in `provider.py`. Replaces 5 scattered call sites with one. |
| 2 | `b25815a` | **Per-tenant rate limits (migration 032).** `llm_max_calls_per_minute`, `llm_max_calls_per_hour`, `llm_max_spend_per_day_usd`, `llm_always_use_opus`, `llm_allow_haiku` columns on `tenant_agent_settings`. `limits.check()` runs three windowed queries against `llm_usage_log` before the expensive provider call. Blocked calls write a zero-token `status='rate_limited'` row so the dashboard attributes them correctly. NULL = unlimited. |
| 3 | `c29c5e3` | **Superadmin LLM dashboard.** `/settings/llm-usage` — three stacked views (Cost control / Efficiency / Quality proxy). `dashboard.py` queries: total/by-task/by-model/by-tenant/by-day spend, cache hit rate, cost per generation, escalation rate, error rate + top errors, regeneration-within-15min rate, validation-critical rate, post-gen failure rate, top spenders. Per-run LLM cost panel on `/runs/:id` replaced with a `by_task` table sourced from `llm_usage_log` (including cached tokens). |
| 4 | `eb41166` | **Feedback loop (migration 033).** New `generation_quality_signals` table captures: `validation_critical` from the static validator, `regenerated_soon` on batch-wide supersession, `execution_failed` when a TC ends failed/error (carries `failure_summary`). `feedback.capture()` is idempotent (dedup by `(signal_type, rule, object, field)`). `feedback.recent_for_tenant()` auto-loads into the test_plan_generation prompt so the next generation sees what hurt the last one. |
| 5 | `e566ef1` | **Tool use + PII redaction + provider abstraction.** `test_plan_generation` migrated to Anthropic tool_use API (`submit_test_plan` with `_TEST_CASE_SCHEMA` + `_STEP_SCHEMA`) so parse failures become impossible. `redact.py` scrubs obvious PII (emails, IPs, SSN-shaped, long digit runs) before outbound prompts. `ProviderRegistry` routes by model-id prefix (`claude-*` → Anthropic, `gpt-*` / `o1-*` → OpenAI stub raising `NotImplementedError`) — architecture accepts cross-vendor fallback chains when OpenAI ships. |
| 6 | `d2df088` | **Product layer (migration 034): tenant LLM tiers + self-service view.** `llm_tier VARCHAR(20)` column on `tenant_agent_settings` with a CHECK constraint (starter / pro / enterprise / custom). `tiers.py` preset module — named bundles of the 5 caps. `limits.load_tenant_config` now resolves: tier preset → overridden by any non-NULL raw column. New `/settings/my-llm-usage` tenant-admin view with soft-cap progress bars (warn at 80%, block at 100%), current-plan panel, blocked-calls counter, per-feature spend breakdown, and a plan comparison table. Superadmin `/settings/llm-usage` gains a `Plan` column on the by-tenant table with a per-row tier picker (`POST /settings/tenant-tier/<tenant_id>` with activity_log). 14 new tests in `tests/test_llm_architecture.py`. |
| **7** | **`<this commit>`** | **Human feedback closes the loop.** Phase 4 shipped machine signals (validator / execution / supersession); Phase 7 adds the human half. 👍 / 👎 on every AI-generated test case via `POST /api/test-cases/:id/feedback` (anyone with view can submit; no migration — new signal types fit the existing schema). Thumbs-down carries an optional reason from a 4-value enum + free text. Also captures **implicit** signals: `user_edited` when a user edits an AI-generated TC (deduped per 10-min bucket) and `ba_rejected` when BA review rejects (previously a dead constant — now wired). Severity mapping on capture means `recent_for_tenant(min_severity="medium")` finally filters properly (wrong-field → high, redundant → low). **New `feedback_rules.py` aggregation layer** — turns raw signals into a `### Common mistakes to avoid:` block (natural-language imperatives + concrete recent examples, ranked by severity × frequency) that the `test_plan_generation` prompt consumes verbatim. Same aggregator powers the dashboard's top-5 recurring issues. **`correction_rate` is the new north-star metric** — `(user_edited + ba_rejected + user_thumbs_down) / AI-generated TCs` over the window, shown as a hero number on `/settings/my-llm-usage` with window-over-window delta arrow + colour, and as a new column on the superadmin by-tenant table. Rate-limited at 5 signals / TC / user / day (throttled responses return 200 with `throttled:true` so spammers get no visible rejection). 11 new tests (25 total in `tests/test_llm_architecture.py`). |

**Files**:
- `primeqa/intelligence/llm/` — `gateway.py`, `router.py`, `provider.py`, `pricing.py`, `usage.py`, `limits.py`, `dashboard.py`, `feedback.py`, `feedback_rules.py`, `redact.py`, `tiers.py`
- `primeqa/intelligence/llm/prompts/` — registry + per-task modules (test_plan_generation, failure_summary, failure_analysis, agent_fix, connection_test)
- `primeqa/intelligence/llm/providers/` — registry + `anthropic_provider.py` + `openai_provider.py` stub
- `primeqa/templates/settings/llm_usage.html` — superadmin dashboard (now with tier picker)
- `primeqa/templates/settings/my_llm_usage.html` — tenant self-service
- Migrations 031 (llm_usage_log) / 032 (rate limits) / 033 (quality signals) / 034 (tier)

**Call sites migrated to the gateway**:
- `TestCaseGenerator.generate_plan()` — test-plan generation
- `IntelligenceService._call_llm` — taxonomy classification + explanation
- `AgentOrchestrator.propose_fix()` — agent fix proposal
- `runs_summarise_failures` — run failure summary
- `runs_detail` cost panel — now aggregates from `llm_usage_log` joined by `run_id`

**Deliberately deferred** (present in architecture, not built): OpenAI provider implementation, cross-vendor fallback chain routing, offline eval harness, tenant-configurable redaction patterns, signal-driven prompt A/B, standalone signals dashboard.

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

## Database (65+ tables)

**Core domain** (11): tenants, users, refresh_tokens, environments,
environment_credentials, activity_log, groups, group_members,
group_environments, connections, **tenant_agent_settings** *(+ llm_max_calls_per_minute/hour, llm_max_spend_per_day_usd, llm_always_use_opus, llm_allow_haiku, llm_tier, llm_enable_story_enrichment, llm_enable_domain_packs)*

**Metadata** (8): meta_versions *(+ `delta_since_ts`, background-job
columns)*, meta_objects, meta_fields, meta_validation_rules, meta_flows,
meta_triggers, meta_record_types, **meta_sync_status**

**Test Management** (17): sections, requirements, test_cases *(+
`coverage_type`, `generation_batch_id`)*, test_case_versions *(+
`validation_report`, `validated_at`, `validated_against_meta_version_id`, `story_view`)*,
test_suites, suite_test_cases, ba_reviews, metadata_impacts, tags,
test_case_tags, milestones, milestone_suites, custom_fields,
custom_field_values, step_templates, test_case_parameter_sets,
**generation_batches** *(all with soft-delete columns)*

**Execution** (15): pipeline_runs *(+ `source_refs`, `parent_run_id`, `label`, `failure_summary_ai`, `failure_summary_at`, `failure_summary_model`)*,
pipeline_stages, run_test_results, run_step_results *(+ 7 log-capture
columns)*, **run_events** *(durable pipeline log, migration 027)*,
run_artifacts, run_created_entities, run_cleanup_attempts,
execution_slots, worker_heartbeats, data_templates, data_factories,
data_snapshots, test_case_data_bindings, test_case_risk_factors

**Intelligence** (8): entity_dependencies, explanation_requests,
failure_patterns, behaviour_facts, step_causal_links, **agent_fix_attempts**,
**llm_usage_log** *(migration 031 — per-call audit: tenant_id, user_id, task, model, prompt_version, tokens, cached_input_tokens, cost_usd, latency_ms, status, escalated, complexity, request_id, run_id, requirement_id, test_case_id, generation_batch_id, context jsonb)*,
**generation_quality_signals** *(migration 033 — feedback loop: signal_type ∈ {validation_critical, regenerated_soon, execution_failed}, severity, rule, object, field, detail jsonb; dedup-by-identity via trigger)*

**Release** (6): releases, release_requirements, release_impacts,
release_test_plan_items, release_runs, release_decisions *(+
`agent_verdict_counts`)*

**Runs** (1): **scheduled_runs**

**Vector** (1): embeddings

### Destructive audit + remediation — shipped (April 2026)

Full-system beast-mode audit surfaced 15 findings across security, UX,
perf, and data integrity. All 15 shipped across 6 commits.

**Critical (4) — security blockers**:
- **C-1**: login no longer accepts client-supplied `tenant_id` — the
  service now derives tenant from the email row (across-tenant lookup).
  Prevents multi-tenant bypass if the same email exists in >1 tenant.
- **C-2**: `/api/webhooks/ci-trigger` fails closed when `WEBHOOK_SECRET`
  is unset (was silently accepting anyone).
- **C-3**: Global Flask `errorhandler(Exception)` returns envelope
  without stack for `/api/*` and minimal HTML for web routes. Four
  specific 500-prone sites also hardened: sections.create (length),
  feedback.capture_user_feedback (verdict type), test-cases/bulk
  (id coerce), views + core.auth (JWT missing claims → 401 not 500).
- **C-5**: 19 ghost `completed` pipeline_runs with 0 tests cleaned up;
  migration 036 adds CHECK constraints preventing recurrence.

**Major (6)**:
- **M-1**: Optimistic lock on `PATCH /api/releases/:id` via
  `updated_at` token. 409 with `current_updated_at` on conflict.
- **M-2**: Hard 500-row cap on `core/repository.py` unbounded
  `list_*` methods (users, environments, connections, groups).
- **M-3**: `AuthService.update_user` revokes all refresh tokens on
  role change or `is_active=false`. Shortens window for stale JWTs
  post-demotion.
- **M-7**: Home `/` dashboard 15 queries / 4.3s → **6 queries / 2.0s**.
  Seven individual `count(*)` queries collapsed into 1 SELECT with
  scalar subqueries; `analytics.release_health` N+1 eliminated via
  CTE + DISTINCT ON; `overall_stats` 2 queries → 1.
- **M-9**: `PipelineRunRepository.update_run_status` enforces valid
  state transitions (terminal → anything = ValueError). Plus the DB
  CHECK constraint from migration 036.
- **M-10**: Duplicate `Pipeline Test Env` renamed; migration 036 adds
  `UNIQUE (tenant_id, lower(name))` on `environments`.

**Minor (5) — UI polish**:
- **UI-7**: 3 native `confirm()` calls (connections/detail,
  runs/scheduled_list, runs/detail) migrated to `data-confirm` +
  `data-confirm-form` attribute-driven `PrimeQA.confirm()`.
- **UI-12**: 6 hardcoded `bg-gray-600` Edit buttons migrated to new
  `btn_edit()` macro. 3 empty-state holdouts (test_data/list ×2,
  groups/list, runs/compare) migrated to the `empty_state()` macro.
- **UI-13**: 13 existing modals patched in-place with
  `role="dialog" aria-modal="true" aria-labelledby`. New
  `components/_modal.html` (macro) + `static/js/modal.js` (focus trap
  + Escape + Tab wrap + return-focus + body scroll lock) available
  for future modals via `{% call modal_shell(id=..., title=...) %}`.
- **UI-15**: `static/js/loading.js` — global submit + click listener
  that disables buttons during in-flight actions + `aria-busy` +
  optional spinner. Opt-out via `data-no-loading`.

Migrations added: **035** (`run_test_results` FK ON DELETE SET NULL),
**036** (pipeline_runs status CHECK + terminal-completed_at CHECK +
environments UNIQUE), **037** (rtr.failure_type enum expanded),
**038** (worker_heartbeats.died_reason + died_at).

### Worker-death recovery + ghost-rtr healing — shipped (April 2026)

Uncovered by a live run where Salesforce rejected an AI-hallucinated
`Opportunity.Contract_Value__c` field. Worker died mid-execute between
the step-finished event and the rtr status update, which combined with
an ancient CHECK-constraint bug left the rtr in ghost `passed` state
and blocked the feedback loop from learning.

Three bugs converged:
1. Worker write-order: rtr status + feedback signal were written
   AFTER the step loop, so SIGKILL between step-failure and loop-exit
   left the rtr in its initial `passed` state.
2. `run_test_results.failure_type` CHECK only allowed 5 legacy
   values, but worker code had been setting `step_error` and
   `unexpected_error` forever — every write silently rolled back.
3. Scheduler path didn't register ORM mappers for
   `tenants`/`test_cases`/`test_case_versions`, so
   `feedback.capture` calls from healers silently failed FK
   resolution and dropped the signal.

Fixes:
- worker.py writes rtr status + fires feedback.capture IMMEDIATELY
  in-loop on step failure, before any subsequent code path that
  could die.
- Migration 037 expands the failure_type CHECK enum.
- Migration 038 adds died_reason + died_at on worker_heartbeats for
  observability.
- worker.py main loop has SIGTERM/SIGHUP handlers + uncaught-exception
  hook that write died_reason before exit. Structured lifecycle logs
  (`worker_lifecycle=start|stop|sigterm|crash`).
- scheduler.reap_orphan_rtrs (new): self-healing task that runs every
  tick; finds rtrs with status='passed' + failed child step_results,
  reconciles + fires the missed EXECUTION_FAILED signal. 6-hour
  window.
- scheduler.reap_stale_workers: passes `died_reason='heartbeat_timeout'`
  + logs last_run + last_stage at death.
- scripts/backfill_heal_ghost_rtrs_2026_04_19.sql: one-off SQL that
  healed 9 historic ghost rtrs that predated the online healer's
  6-hour window. Ghost count went 9 → 0.

## Migrations (001–049)
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
- **029**: `test_case_versions.validation_report` (JSONB) + `validated_at` + `validated_against_meta_version_id` for the static validator that runs after every generation and before every execution
- **030**: `pipeline_runs.label` + `failure_summary_ai` + `failure_summary_at` + `failure_summary_model` for run tags, AI failure summaries, and filterable run history
- **031**: `llm_usage_log` — per-call ledger feeding the gateway audit trail, superadmin dashboards, and rate-limit windowing. Indexes: `(tenant_id, ts)`, `(task, ts)`, `(run_id) where run_id is not null`, `(user_id, ts)`
- **032**: `tenant_agent_settings.llm_max_calls_per_minute / _hour / _spend_per_day_usd / _always_use_opus / _allow_haiku` — shared-key rate-limit columns. NULL = unlimited (gentle onboarding)
- **033**: `generation_quality_signals` — feedback-loop sink (signal_type, severity, rule, object, field, detail jsonb). Consumed by `feedback.recent_for_tenant()` and auto-loaded into the `test_plan_generation` prompt
- **034**: `tenant_agent_settings.llm_tier VARCHAR(20)` with CHECK `∈ {starter, pro, enterprise, custom}`. Resolves to preset values via `primeqa.intelligence.llm.tiers`; per-tenant column overrides win over the preset. `custom` tier bypasses the preset entirely
- **035**: `run_test_results.test_case_id` / `test_case_version_id` — drop NOT NULL + change FK from restrictive to `ON DELETE SET NULL`. Unblocks hard-purge of soft-deleted TCs while preserving run history.
- **036**: `pipeline_runs_status_ck` CHECK (status enum) + `pipeline_runs_terminal_completed_at_ck` CHECK (terminal → completed_at NOT NULL) + `environments_tenant_name_uk` UNIQUE (tenant_id, lower(name)). Paired with `scripts/audit_cleanup_ghost_runs_2026_04_19.sql` (pre-flight data cleanup).
- **037**: `run_test_results.failure_type` CHECK expanded to include `step_error`, `unexpected_error`, `validation_blocked`. The worker had been writing these values since forever, but the original CHECK only allowed 5 legacy values — every write CHECK-violated silently, rolling back `update_result` and leaving rtrs in ghost `passed` state. Paired with the worker-death recovery fix in `primeqa/worker.py` (in-loop rtr + feedback signal persistence) and the new `reap_orphan_rtrs` scheduler task.
- **038**: `worker_heartbeats.died_reason VARCHAR(255)` + `died_at TIMESTAMPTZ` + partial index. Lets ops distinguish graceful SIGTERM (Railway redeploy) from OOM-kill from uncaught exception. Populated by `worker.py` shutdown hooks (SIGTERM / KeyboardInterrupt / crash) and by `scheduler.reap_stale_workers` (generic `heartbeat_timeout`). Paired with `scripts/backfill_heal_ghost_rtrs_2026_04_19.sql` (9 historic ghost rtrs reconciled).
- **048**: `test_case_versions.story_view JSONB` (nullable) + `tenant_agent_settings.llm_enable_story_enrichment BOOLEAN NOT NULL DEFAULT false`. Backs the BA-readable "story view" layer over AI-generated test cases. NULL `story_view` falls back to the mechanical step view at render time — zero-backfill rollout. The feature flag defaults off per tenant; superadmin toggles it in `/settings/llm-usage`. Enrichment runs inside the Prompt 15 atomic transaction and is best-effort (LLM failures leave `story_view=NULL` without rolling the batch back).
- **049**: `tenant_agent_settings.llm_enable_domain_packs BOOLEAN NOT NULL DEFAULT false`. Per-tenant feature flag for Domain Packs — a parallel knowledge channel that injects long-form prescriptive Salesforce knowledge (markdown files with YAML frontmatter under `salesforce_domain_packs/`) into `test_plan_generation` prompts when the requirement text matches a pack's keywords. **No attribution column** — which packs fired on each call is written into the existing `llm_usage_log.context` JSONB column under key `domain_packs_applied` (mirrors the story_view precedent). Superadmin toggles the flag via the "Packs" checkbox next to "Story" in `/settings/llm-usage`.

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

## Permission Model

The target authorization architecture (superseding the flat-role table above as the
canonical model for new features — the 5 legacy roles still exist at the DB level
and are gradually re-expressed as permission-set bundles).

- **Additive Permission Sets (no deny rules).** A user can hold any number of
  permission sets; effective permissions = **union** of every set granted. No
  deny-overrides-allow logic anywhere — if any set grants a capability, the user
  has it. Keeps the mental model flat and auditable.

- **Two-layer access**: every protected action checks **both**
  1. *User permissions* (does the caller's permission-set union grant this capability?)
  2. *Environment run policies* (is this environment configured to allow runs by
     this user / this role / at this time?)

  Either layer can block. Typical failure mode is a user with `run_tests`
  capability being denied on a production environment whose policy restricts
  runs to Release Owners during a freeze window.

- **Five Base Permission Sets** (shipped as presets; tenants can clone + edit):

  | Set | Purpose |
  |---|---|
  | **Developer** | Author / edit test cases + requirements; trigger runs in dev + sandbox envs; no release decisions. |
  | **Tester** | Full test-management (library, suites, sections); trigger runs anywhere runnable; cannot approve releases. |
  | **Release Owner** | Tester + approve / override release decisions (PENDING → APPROVED / OVERRIDDEN); can schedule runs. |
  | **Admin** | Tenant config: connections, environments, groups, users, purge; does **not** imply test authorship. |
  | **API Access** | Programmatic-only set for CI/webhook callers; narrow run + read scope, no UI. |

- **Ownership on all resources**: every test-management + execution row carries
  `owner_user_id` (authoring user) and pipeline runs additionally carry
  `triggered_by_user_id` (executing user). Visibility scopes off ownership where
  privacy applies (drafts, own-user supersession).

- **Release state on pipeline runs**: `release_state ∈ { PENDING, APPROVED, OVERRIDDEN }`
  on every run that targets a release-gated environment.
  - `PENDING` — run executed, decision not yet made
  - `APPROVED` — Release Owner accepted the GO recommendation
  - `OVERRIDDEN` — Release Owner / superadmin shipped against a NO-GO (logged + audit-flagged)

- **Superadmin** stays a god-mode escape hatch *outside* the permission-set
  union — it always passes `require_role` and is excluded from the 20-user cap.
  New code should prefer permission-set checks; superadmin passes those too.

- **Every new protected endpoint must declare its capability** (e.g.
  `run_tests.trigger`, `release.approve`, `environment.edit`) so the set ↔ action
  mapping stays in one registry and sets can be re-bundled without code edits.

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
- **Post-generation linter**: **shipped** in commits `ab40337` + `2e10581`
  (the TestCaseValidator). Catches object-not-found, field-not-found,
  `$var` without prior `state_ref`, SOQL FROM / SELECT column mismatches,
  with fuzzy suggestions and a one-click Apply button. Still deferred:
  cross-object FLS / PLS permission checks, full SOQL grammar for
  aggregate/TYPEOF/GROUP BY clauses, auto-correct "Fix all".
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
