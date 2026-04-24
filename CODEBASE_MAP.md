# Codebase Map

Generated 2026-04-20. One line per file: path / what it does / what it depends on.

---

## primeqa/ (root)

- `__init__.py` — package marker, empty — deps: none
- `app.py` — Flask entrypoint; loads .env, registers every blueprint, installs CSRF + observability + global 500 handler — deps: flask, primeqa.db, primeqa.core.csrf, primeqa.shared.observability, every domain's routes module
- `db.py` — SQLAlchemy engine + scoped `SessionLocal` factory; `init_db(url)` called once at startup — deps: sqlalchemy
- `views.py` — single large blueprint for every server-rendered HTML route (runs, requirements, test cases, releases, settings, connections, environments) — deps: flask, primeqa.core.auth, every domain's service + repository
- `worker.py` — background worker entrypoint (`python -m primeqa.worker`); 5s poll loop driving pipeline runs + metadata syncs; SIGTERM-aware died_reason tracking — deps: primeqa.execution.service, primeqa.metadata.worker_runner, primeqa.intelligence.llm.feedback, primeqa.runs.streams
- `scheduler.py` — reaper + cron process (`python -m primeqa.scheduler`); 60s tick running 8 maintenance jobs (stuck stages, stuck slots, stale workers, fire scheduled runs, dead-man's switch, stalled metadata jobs, orphan rtrs, run_events trim) — deps: primeqa.execution.repository, primeqa.metadata.worker_runner, primeqa.runs.schedule

## primeqa/core/ (tenants, users, auth, envs, connections)

- `__init__.py` — package marker — deps: none
- `models.py` — SQLAlchemy models: Tenant, User, Group, Environment, Connection, RefreshToken, ActivityLog, EnvironmentCredential, TenantAgentSettings — deps: primeqa.db.Base
- `repository.py` — CRUD repos for every core table; `ConnectionRepository.get_connection_decrypted` handles per-type sensitive-field decryption — deps: primeqa.core.models, primeqa.core.crypto
- `service.py` — AuthService (login, refresh, user CRUD), ConnectionService (test_connection end-to-end), EnvironmentService — deps: repository, crypto, bcrypt, PyJWT, requests
- `routes.py` — `/api/auth/*` JSON endpoints (login, refresh, logout, me, user CRUD) — deps: core.service, flask
- `auth.py` — `require_auth` + `require_role` decorators + `get_current_user` helper; JWT validation with tolerant optional-claim handling — deps: PyJWT, flask
- `crypto.py` — Fernet symmetric encryption for stored credentials; dual-key decrypt (`CREDENTIAL_ENCRYPTION_KEY` primary + `_OLD` fallback) for zero-downtime rotation — deps: cryptography.Fernet
- `csrf.py` — double-submit cookie CSRF protection; `install(app)` wires before_request + after_request; exempts `/api/*` Bearer-auth paths — deps: flask, secrets
- `agent_settings.py` — TenantAgentSettings repo + defaults (agent autonomy, LLM tier overrides) — deps: primeqa.core.models

## primeqa/metadata/ (Salesforce metadata, versioned, per-category sync)

- `__init__.py` — package marker — deps: none
- `models.py` — MetaVersion, MetaObject, MetaField, MetaValidationRule, MetaFlow, MetaTrigger, MetaSyncStatus — deps: primeqa.db.Base
- `repository.py` — metadata CRUD; `get_objects/_fields/_validation_rules` now order-by api_name for prompt-cache stability — deps: metadata.models
- `service.py` — MetadataService: full + delta sync, per-category DAG orchestration — deps: repository, sync_engine, requests (for SF API)
- `sync_engine.py` — per-category DAG runner; objects → {fields,record_types} → {validation_rules,flows,triggers}; emits events via primeqa.runs.streams — deps: metadata.service, primeqa.runs.streams
- `worker_runner.py` — worker-side claim pattern for meta_versions (SELECT FOR UPDATE SKIP LOCKED); runs claimed sync + heartbeat daemon; scheduler-side `reap_stalled_jobs` — deps: metadata.service, metadata.models, sqlalchemy
- `routes.py` — `/api/environments/:id/metadata/*` endpoints: queue sync, get status, list categories — deps: metadata.service

## primeqa/test_management/ (requirements, test cases, suites, reviews)

- `__init__.py` — package marker — deps: none
- `models.py` — Section, Requirement, TestCase, TestCaseVersion, TestSuite, SuiteTestCase, BAReview, MetadataImpact, Tag, Milestone, GenerationBatch — deps: primeqa.db.Base
- `repository.py` — CRUD for every test-management table; `soft_delete_test_case` cascades to release_test_plan_items; delegates list_* to `shared.query_builder.ListQuery` — deps: test_management.models, primeqa.shared.query_builder
- `service.py` — TestManagementService: requirement CRUD, generate_test_plan (batch creation + supersession + validator + attach_batch + feedback signals), BA review flow — deps: repository, primeqa.intelligence.generation, primeqa.intelligence.validator, primeqa.intelligence.llm.feedback
- `routes.py` — `/api/sections/*`, `/api/requirements/*`, `/api/test-cases/*`, `/api/suites/*`, `/api/reviews/*`, `/api/requirements/bulk-generate` — deps: test_management.service
- `step_schema.py` — canonical step grammar (create/update/query/verify/delete/convert/wait + expect_fail flag); StepValidator for form-side shape checks — deps: none

## primeqa/execution/ (pipeline runs, step execution, cleanup)

- `__init__.py` — package marker — deps: none
- `models.py` — PipelineRun, PipelineStage, RunTestResult, RunStepResult, RunArtifact, RunCreatedEntity, RunCleanupAttempt, RunEvent, ExecutionSlot, WorkerHeartbeat — deps: primeqa.db.Base
- `repository.py` — CRUD for every execution table; `PipelineRunRepository.update_run_status` enforces valid state transitions; `WorkerHeartbeatRepository.mark_dead` preserves first-set died_reason — deps: execution.models
- `service.py` — PipelineService: create_run, stage DAG orchestration, acquire/release slot, fail_run — deps: repository, primeqa.runs.streams
- `executor.py` — StepExecutor: runs one step against Salesforce; `$foo.Id` ref resolution (field_values + SOQL); expect_fail inversion; idempotency + lineage capture — deps: execution.models, execution.idempotency, primeqa.runs.streams, simple_salesforce-style client
- `cleanup.py` — reverse-order entity deletion, dependency-chain resolution, retry with production-safety guards — deps: execution.models, execution.repository, SF client
- `idempotency.py` — per-run idempotency keys, creation fingerprints, trigger-created entity detection — deps: execution.models, hashlib
- `data_engine.py` — templates + factories for reliable unique test data (addresses flakiness at its root) — deps: execution.models
- `analytics.py` — pass rate, flakiness, trend aggregations for dashboards — deps: execution.models, sqlalchemy.func
- `flake.py` — R6 flake scoring + auto-quarantine helpers — deps: execution.models
- `routes.py` — `/api/runs/*`, `/api/runs/:id/events` (SSE), cancel, rerun, label endpoints — deps: execution.service, primeqa.runs.streams

## primeqa/intelligence/ (AI layer: generation, agent, risk, validator)

- `__init__.py` — package marker — deps: none
- `models.py` — AgentFixAttempt, LLMUsageLog, GenerationQualitySignal, FailurePattern, CausalLink — deps: primeqa.db.Base
- `repository.py` — intelligence-domain CRUD — deps: intelligence.models
- `service.py` — IntelligenceService: explanations, failure patterns, causal links; legacy direct-Anthropic fallback — deps: repository, primeqa.intelligence.llm.gateway
- `generation.py` — TestCaseGenerator.generate_plan: calls llm_call(test_plan_generation), normalises coverage, returns plan dict with cost + usage_log_ids for back-attribution — deps: intelligence.llm.gateway, metadata.repository
- `validator.py` — TestCaseValidator: object/field existence, createability, unresolved state_ref (with `.Id` tolerance), SOQL column checks, fuzzy suggestions — deps: metadata.repository, difflib
- `agent.py` — R5 agent: failure triage (pattern DB + taxonomy regex), fix proposal via agent_fix prompt, sandbox auto-apply gated on trust band, rerun + audit — deps: intelligence.llm.gateway, intelligence.repository, execution.repository
- `risk_engine.py` — risk scoring + test prioritization; factors: blast radius, entity criticality, historical failure rate, business priority — deps: metadata.repository, intelligence.repository
- `routes.py` — `/api/agent-fixes/*`, `/api/failure-summary/*`, `/api/feedback`, `/api/risk/*` — deps: intelligence.service, intelligence.agent

## primeqa/intelligence/llm/ (single chokepoint for every Anthropic call)

- `__init__.py` — re-exports `llm_call`, `LLMError`, `LLMResponse` — deps: gateway
- `gateway.py` — `llm_call(task, tenant_id, api_key, ...)` entry point: rate limits → complexity detect → chain select → PII redact → provider.invoke with chain traversal → usage.record per attempt → LLMResponse(usage_log_id, usage_log_ids) — deps: router, provider, usage, limits, redact, prompts.registry, feedback_rules
- `router.py` — `_CHAINS` table per task × complexity → [models]; `select_chain` with TenantPolicy overrides (always_use_opus, allow_haiku); OPUS/SONNET/HAIKU constants — deps: none
- `provider.py` — thin wrapper over anthropic SDK; backoff for 429/529, timeout retry, status classification; emits ProviderResponse — deps: anthropic
- `pricing.py` — MODEL_PRICING dict; `compute_cost_usd(model, in, out, cached_in, cache_write)` with cache read/write multipliers — deps: decimal
- `usage.py` — `record(...)` inserts llm_usage_log row (own Session, fire-and-forget, returns id); `attach_batch(log_id, batch_id)` back-links post-call — deps: intelligence.models, primeqa.db.engine
- `limits.py` — per-tenant rate check: minute / hour / daily-spend windows via llm_usage_log sum; returns RateCheck(allowed, reason, retry_after) — deps: intelligence.models, tiers
- `tiers.py` — product-tier bundles (starter/pro/enterprise/custom) → preset limits; override-wins logic — deps: none
- `redact.py` — regex-based PII scrub (emails, IPs, SSN, long digit runs) preserving message structure — deps: re
- `feedback.py` — FeedbackCollector: capture(signal_type, severity, detail, ttl); signal-type constants (execution_failed, validation_critical, user_edited, ba_rejected, user_thumbs_up/down, regenerated_soon) — deps: intelligence.models, primeqa.db.engine
- `feedback_rules.py` — aggregate raw signals → prompt-ready "Common mistakes to avoid" block; tenant-level rule ranking by severity × frequency — deps: intelligence.models, feedback
- `dashboard.py` — SQL queries for `/settings/llm-usage` superadmin dashboard: cost control, efficiency, quality proxy views — deps: intelligence.models, sqlalchemy.text

## primeqa/intelligence/llm/prompts/ (one module per task)

- `__init__.py` — package marker — deps: none
- `base.py` — PromptSpec dataclass (messages, system, tools, max_tokens, parse, force_tool_name) — deps: dataclasses
- `registry.py` — flat `{task → module}` dict; `get_prompt(task)` lookup — deps: each prompt module
- `test_plan_generation.py` — main prompt; v3 w/ expect_fail guidance, tool_use `submit_test_plan` with strict schema, cache_control on grammar + metadata blocks, detect_complexity (word-boundary kw matching); SUPPORTS_ESCALATION=True — deps: base, json
- `agent_fix.py` — R5 agent fix proposal prompt; returns valid step JSON for auto-apply — deps: base
- `failure_analysis.py` — failure root-cause diagnosis (Sonnet-default) — deps: base
- `failure_summary.py` — run-level failure summary for the detail panel (Haiku) — deps: base
- `connection_test.py` — 10-token "ping" used by Test Connection button (Haiku) — deps: base

## primeqa/intelligence/llm/providers/ (cross-vendor abstraction)

- `__init__.py` — re-exports get_provider_for_model — deps: registry
- `registry.py` — prefix-match routing: `claude-*` → AnthropicProvider, `gpt-*`/`o1-*` → OpenAIProvider — deps: anthropic_provider, openai_provider
- `anthropic_provider.py` — thin adapter over provider.invoke() — deps: primeqa.intelligence.llm.provider
- `openai_provider.py` — stub; raises NotImplementedError, architecture slot only — deps: none

## primeqa/intelligence/llm/eval/ (offline prompt regression harness)

- `__init__.py` — package marker — deps: none
- `__main__.py` — CLI: `python -m primeqa.intelligence.llm.eval <task> [--live/--dry] [--verbose]` — deps: runner, scorer
- `runner.py` — load fixtures, build spec, optionally invoke Anthropic, collect results — deps: primeqa.intelligence.llm.gateway, fixtures
- `scorer.py` — per-task check functions; returns list of CheckResult (named pass/fail) rather than a single score — deps: json

## primeqa/release/ (releases, risk-scored test plans, decision engine)

- `__init__.py` — package marker — deps: none
- `models.py` — Release, ReleaseRequirement, ReleaseTestPlanItem, ReleaseImpact, ReleaseRun, ReleaseDecision — deps: primeqa.db.Base
- `repository.py` — release CRUD + test_plan_item + run linkage — deps: release.models
- `service.py` — ReleaseService: detail, enrich_test_plan, add/remove requirements, bulk attach, refresh_test_plan_from_requirements (heal stale plans after regen supersession) — deps: repository, test_management.models
- `routes.py` — `/api/releases/*`: CRUD, requirements attach, test-plan bulk + single add + refresh-from-requirements, evaluate-decision, status — deps: release.service, release.decision_engine
- `decision_engine.py` — GO/NO-GO recommendation: evaluates release against decision_criteria, produces reasoning; recommendation-only (human confirms) — deps: release.repository, intelligence.repository

## primeqa/runs/ (cross-cutting: Wizard, preflight, SSE, cost, scheduled runs)

- `__init__.py` — package marker — deps: none
- `wizard.py` — RunWizardResolver: mixed selection (Jira tickets + suites + sections + hand-picks) → flat tc_ids + source_refs — deps: test_management.repository, metadata.repository
- `preflight.py` — pre-run checks (credentials, metadata freshness, size caps, per-test skip by metadata category) → PreflightReport — deps: core.repository, metadata.repository, test_management.repository
- `cost.py` — per-run cost forecast (tokens + USD + SF API calls estimate); superadmin-only — deps: none
- `streams.py` — in-process EventBus for pub/sub + `record_event()` persister (own session) + SSE endpoint helper; emit_stage_*, emit_test_*, emit_step_*, emit_log, emit_run_status — deps: execution.models, primeqa.db.engine, threading
- `schedule.py` — ScheduledRun model + croniter-based `next_fire_at` + dead-man's-switch + `fire_due_schedules(db)` — deps: croniter, execution.service

## primeqa/shared/ (cross-cutting utilities)

- `__init__.py` — package marker — deps: none
- `api.py` — `json_page` / `json_error` envelope; ConflictError, NotFoundError, ValidationError, BulkLimitError — deps: flask
- `query_builder.py` — ListQuery: filter + search + sort + pagination with hard per_page cap 50 + sort-field whitelist — deps: sqlalchemy
- `observability.py` — `install(app)`: request timing + slow-query log at 800ms; in-process counters exposed at `/api/_internal/health` — deps: flask, sqlalchemy.event
- `notifications.py` — log-only stub for notify_*; swap to SendGrid/SES/SMTP via NOTIFICATIONS_PROVIDER env — deps: logging

## primeqa/vector/ (pgvector embeddings)

- `__init__.py` — package marker — deps: none
- `models.py` — Embedding (pgvector column) — deps: primeqa.db.Base, pgvector.sqlalchemy
- `repository.py` — RAG search scoped by tenant + environment — deps: vector.models
- `service.py` — embedding CRUD + RAG search business logic — deps: vector.repository

## primeqa/system_validation/ (self-validation suite)

- `__init__.py` — package marker — deps: none
- `runner.py` — JSON-driven self-validation suite runner (Flask test client OR live HTTP); step grammar covers 8 workflow categories — deps: flask.testing, requests
- `suites/primeqa_core.json` — canonical 8-category E2E suite definition (not Python) — deps: none

## tests/ (integration tests against Railway DB)

- `__init__.py` — package marker — deps: none
- `_ux_audit.py` — renders every primary page and greps HTML for expected CRUD affordances (create btn, row click-through, delete, search, pagination, sort, trash view, breadcrumbs, filter sidebar, generate) — deps: primeqa.app
- `test_auth.py` — 15 tests for AuthService: login, refresh, me, user CRUD, tester role blocks, 20-user limit, logout revokes tokens — deps: primeqa.app
- `test_environments.py` — 14 tests for environment CRUD + credentials encryption + group scoping — deps: primeqa.app
- `test_metadata.py` — 10 tests for metadata sync DAG + versioning — deps: primeqa.app
- `test_management.py` — 23 tests for sections/requirements/test cases/suites/reviews lifecycle + visibility — deps: primeqa.app
- `test_hardening.py` — 17 tests for A1-A3 hardening: query_builder caps, optimistic locking, soft delete, bulk caps — deps: primeqa.app
- `test_pipeline.py` — 12 tests for pipeline_run creation + stage orchestration + slot acquisition — deps: primeqa.app
- `test_executor.py` — 15 tests for StepExecutor: SF client mock, create/update/query/verify/delete, state_ref resolution, error paths — deps: primeqa.app
- `test_cleanup.py` — 9 tests for reverse-order cleanup + lineage + retry — deps: primeqa.app
- `test_intelligence.py` — 11 tests for failure patterns, causal links, explanations — deps: primeqa.app
- `test_run_experience.py` — 14 tests for R1 Run Wizard resolver + preflight — deps: primeqa.app
- `test_r2_superadmin.py` — 7 tests for R2 superadmin god-mode (cost visibility, agent config, preflight override) — deps: primeqa.app
- `test_r3_metadata.py` — 6 tests for R3 metadata sync UX — deps: primeqa.app
- `test_r4_schedule.py` — 7 tests for R4 scheduled runs + cron parsing + dead-man's switch — deps: primeqa.app, croniter
- `test_r5_agent.py` — 7 tests for R5 agent triage + auto-apply + rerun — deps: primeqa.app
- `test_r6_polish.py` — 5 tests for R6 notifications stub + flake quarantine — deps: primeqa.app
- `test_r7_jira_picker.py` — 10 tests for R7 Jira chip picker: search + cache + mixed selection — deps: primeqa.app
- `test_system_validation.py` — 4 runner tests + 13 canonical suite outcomes — deps: primeqa.system_validation.runner
- `test_llm_architecture.py` — 25 tests for Phases 1-7 LLM gateway: router, provider, pricing, usage, limits, tiers, feedback, feedback_rules, dashboards — deps: primeqa.app
- `test_eval_harness.py` — 15 tests for offline prompt regression harness (dry mode) — deps: primeqa.intelligence.llm.eval.runner

## scripts/ (one-off operational helpers)

- `probe_llm_models.py` — probe Anthropic /v1/models + live 5-token call against candidate model ids for every tenant's LLM connection — deps: primeqa.db, primeqa.core.repository, anthropic
- `rotate_credential_encryption_key.py` — re-encrypt every stored credential under a new CREDENTIAL_ENCRYPTION_KEY (reads OLD_KEY+NEW_KEY env vars) — deps: primeqa.db, primeqa.core.crypto, primeqa.core.models, primeqa.release.models
- `revalidate_test_cases.py` — refresh cached validation_report on existing TestCaseVersions after a validator logic change — deps: primeqa.db, primeqa.intelligence.validator, primeqa.metadata.repository
- `audit_cleanup_2026_04_19.sql` — one-off SQL: delete polluted user test fixtures + "Cleanup Test" TCs left by failing test runs — deps: none
- `audit_cleanup_ghost_runs_2026_04_19.sql` — one-off SQL: cancel 19 ghost completed runs from pre-heartbeat era — deps: none
- `backfill_heal_ghost_rtrs_2026_04_19.sql` — one-off SQL: reconcile 9 run_test_results stuck at "passed" despite failed step children — deps: none
- `backfill_llm_usage_batch_link_2026_04_19.sql` — one-off SQL: back-link orphan test_plan_generation usage rows to their generation_batches by timestamp window — deps: none
- `backfill_llm_usage_log.sql` — one-off SQL: backfill cost_usd / prompt_version on pre-migration-031 rows — deps: none
- `cleanup_test_pollution.sql` — one-off SQL: remove test-fixture users + SQ-207 style tenant-state residue — deps: none

## migrations/ (plain SQL, applied via psql; never mutate an existing one)

- `001_core_platform.sql` — tenants, users, refresh_tokens, activity_log, groups; core auth tables
- `002_relational_metadata.sql` — meta_versions, meta_objects, meta_fields, meta_validation_rules, meta_flows, meta_triggers
- `003_test_management.sql` — sections, requirements, test_cases, test_case_versions, test_suites, suite_test_cases, ba_reviews
- `004_execution_engine.sql` — pipeline_runs, pipeline_stages, run_test_results, run_step_results, run_artifacts, run_created_entities, run_cleanup_attempts, execution_slots, worker_heartbeats
- `005_intelligence_and_vector.sql` — agent_fix_attempts, failure_patterns, causal_links, explanations, embeddings (pgvector)
- `006_groups_and_connections.sql` — group_members, environments, connections
- `007_link_environments_to_connections.sql` — environments.connection_id FK
- `008_add_jira_llm_to_environments.sql` — environments.jira_connection_id + llm_connection_id
- `009_releases.sql` — releases, release_requirements, release_test_plan_items, release_impacts, release_runs, release_decisions
- `010_test_data_engine.sql` — test_data_templates, test_data_factories, data_snapshots
- `011_run_source_types.sql` — pipeline_runs.source_type CHECK expansion
- `012_risk_engine.sql` — test_case_risk_factors summary table
- `013_review_step_comments.sql` — ba_reviews.step_comments JSON column
- `014_tags_milestones.sql` — tags, milestones tables
- `015_custom_fields_bulk.sql` — bulk custom-field operations schema
- `016_soft_delete_and_indexes.sql` — deleted_at/deleted_by columns + pg_trgm + partial + gin_trgm_ops indexes (first hardening migration)
- `017_superadmin_and_run_provenance.sql` — users.role='superadmin' + pipeline_runs.triggered_by provenance fields
- `018_run_step_log_capture.sql` — run_step_results extended payload columns
- `019_agent_settings.sql` — tenant_agent_settings table
- `020_meta_sync_status.sql` — meta_sync_status per-category rows
- `021_scheduled_runs.sql` — scheduled_runs cron table
- `022_agent_fix_attempts.sql` — agent_fix_attempts schema expansion
- `023_flake_quarantine.sql` — test_cases.flake_score + is_quarantined
- `024_requirements_jira_key_partial_unique.sql` — partial unique on jira_key WHERE deleted_at IS NULL
- `025_meta_sync_as_background_job.sql` — meta_versions.status + worker_id + heartbeat_at (queue pattern)
- `026_meta_versions_delta_sync.sql` — delta-sync provenance columns
- `027_run_events.sql` — run_events durable timeline table
- `028_coverage_types_and_generation_batches.sql` — test_cases.coverage_type + generation_batches table + test_cases.generation_batch_id
- `029_test_case_version_validation.sql` — test_case_versions.validation_report JSONB
- `030_run_labels_and_failure_summary.sql` — pipeline_runs.label + failure_summary_ai columns
- `031_llm_usage_log.sql` — llm_usage_log table (per-call audit)
- `032_tenant_llm_limits.sql` — tenant_agent_settings rate-limit columns
- `033_generation_quality_signals.sql` — generation_quality_signals table (feedback loop)
- `034_tenant_llm_tier.sql` — tenant_agent_settings.llm_tier column (starter/pro/enterprise/custom)
- `035_run_test_results_tc_set_null.sql` — FK change: run_test_results.test_case_id ON DELETE SET NULL
- `036_pipeline_run_integrity.sql` — pipeline_runs CHECK constraints (valid status transitions at DB level)
- `037_rtr_failure_type_enum.sql` — run_test_results.failure_type CHECK expansion (step_error, unexpected_error, validation_blocked)
- `038_worker_died_reason.sql` — worker_heartbeats.died_reason + died_at columns

## Notable cross-cutting relationships

- Every domain's `models.py` is registered at app startup in `primeqa/app.py` (eager imports) so SQLAlchemy mappers resolve before any query fires
- `primeqa.runs.streams.record_event()` opens its own `Session(bind=engine)` — never shares the caller's scoped session (prevents DetachedInstanceError)
- `primeqa.intelligence.llm.usage.record()` follows the same own-session pattern
- Worker + scheduler + web all import the same domain models but run as separate processes; Postgres is the shared substrate
- `primeqa/views.py` is the monolithic web-UI module; `primeqa/*/routes.py` are JSON API modules — both register as Flask blueprints in app.py
