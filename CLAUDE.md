# CLAUDE.md — Plimsol (PrimeQA v2)

> **Naming**: the product display name is **Plimsol** (renamed 2026-06-15). The
> Python package stays `primeqa` and the JS namespace stays `window.PrimeQA` —
> every `primeqa/` path in this doc is current; only the product name changed.
>
> **Live engine**: the engine is the **S1–S8 substrate** (`primeqa/semantic`,
> `sync`, `test_representation`, `generation`, `execution_engine`, `knowledge`,
> `interpretation`, `conversation`, `evolution`). The original **v1 product
> layer was retired (D-191…D-221)** and its 20 product tables dropped in
> migration 053 (`pipeline_runs`, `test_cases`, `test_case_versions`,
> `generation_batches`, `ba_reviews`, `run_events`, `test_suites`,
> `scheduled_runs`, …); the four `data_*` Test-Data tables dropped in 056
> (D-243). Sections below that still describe v1 surfaces are marked.

## Branch conventions

Two patterns coexist depending on what you're working on:

- **v2 runtime work** (Flask routes, HTMX templates, the `core` / `release` / `runs` web layer, `meta_*` tables): commit directly to `main`. Iteration is fast; `main` is the integration surface.
- **Substrate work** (substrate_1_semantic_org_model and the other S1–S8 substrates): feature branches named `phase-N-substrate-M` (e.g. `phase-2-substrate-1-sync`). Merge to `main` at phase completion. HOLD-and-show discipline before commits.

See `docs/CONVENTIONS.md` for the full working agreement.

## Working agreement (v2 runtime work)

**For v2 runtime work: always commit directly to `main`. Do not create feature branches for this codebase.**

Why: Railway auto-deploys on push-to-main. Feature branches on this repo create a disconnect where the branch ships green locally but production keeps running the old code because the merge never happens. Commit-then-push-main is the canonical workflow — every commit goes through the same continuous-deploy path, so what you verified locally is what ships. No exceptions: even when the work is big enough that a branch feels safer, the right answer is a clean commit on `main` (or a series of small commits) so deploy state stays synchronised with source state.

If you find yourself on a worktree pointing at a non-`main` branch, the correct next step is to switch to the `main` worktree (or create one) and commit there directly.

(Substrate work follows a different pattern — feature branches per phase, HOLD-and-show before commits. See `docs/CONVENTIONS.md`.)

## Working rules for Claude Code

### Ground truth & no hallucination
- **Read before you assert.** Never state what a file, function, schema, or query does without opening it this session — no claims about code you haven't read.
- **The code is ground truth.** If a spec, comment, doc, or prompt conflicts with what the code actually does, the code wins — surface the discrepancy, don't reinterpret to fit.
- **Never invent identifiers** — file paths, symbols, table/column names, API fields, commit hashes, config keys. Grep for the real one; if it doesn't exist, say so plainly.
- **Mark verified vs. assumed.** If you didn't check it, say so. Never present an inference as a confirmed fact.
- **Runtime claims require a run.** For any behavior ("does this query return X?", "does this rule fire?"), run it and report the *observed* result, not the expected. Never report a test outcome you didn't actually run.
- **Canonical state** lives in each SPEC's `Status:` line + `DECISIONS_LOG.md` — not in any single roadmap/state doc. The live retrieval discriminator is `content_type` (flat varchar), not `doc_type`.

### How to work
- **Root cause only — no workarounds.** Diagnose the actual mechanism before any fix; address the cause, never mask the symptom. If the real fix is large, HOLD and say so rather than patching around it.
- **Smallest correct change.** Solve the task and nothing more — don't refactor or widen scope uninvited. Note adjacent work and HOLD instead of folding it in.
- **HOLD when the premise breaks.** If a task's assumption is false against the code (shape differs, a dependency is missing, the spec was stale), stop and report the gap with options + a lean. Don't improvise to make a broken premise work.
- **Respect the architecture.** Don't cross substrate boundaries (S1–S8) or contradict a logged decision (`DECISIONS_LOG`) to get something working — if a task seems to need it, HOLD and flag it.
- **Surface real forks.** At any decision with architectural consequences, present the options, the tradeoff, and your lean — don't silently pick a path.
- **Show your verification.** Before a HOLD, show what proves it — test output, the diff, the grep result — so it's checkable, not asserted.

### Commits & branches
- HOLD before every commit; commit only on an explicit GO.
- One cohesive commit per logical unit; **design commit separate from impl commit**. No micro-commits mid-cycle.
- Author `AK <amjad.khan@teamd.co.in>`; **zero `Co-Authored-By`** (verify 0). `--no-verify` is prohibited.
- `DECISIONS_LOG.md` is **append-only** — never edit prior entries.
- Branch strategy follows **Branch conventions** above (v2 runtime → `main`; substrate work → a `phase-N-substrate-M` branch off `main`). On a feature branch, accumulate commits and push only at milestones (phase close / merge-readiness) or when asked.
- A merge gate means a real green run — never merge on a red or unverified suite.

## What is this project?
Plimsol is a **Release Intelligence System** for Salesforce. It connects to
Salesforce orgs, captures their metadata as a versioned semantic model,
AI-generates grounded test cases from Jira requirements, executes them against
the org and captures real evidence, interprets failures into causes, and
produces GO/NO-GO release recommendations with explainability. A fix-proposal
agent suggests repairs (human-gated; flag-gated auto-apply on sandbox).

Not a "TestRail replacement" — TestRail parity is the substrate; the category
is decision-making for releases.

## Tech stack
- **Backend**: Python 3.11, Flask, SQLAlchemy, PostgreSQL (Railway) with pgvector
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), HTMX, vanilla JS, SSE (EventSource)
- **Auth**: JWT (PyJWT) httponly cookies (also accepted for /api/*), bcrypt
- **Roles**: 5 — `viewer`, `ba`, `tester`, `admin`, `superadmin`
  - `superadmin` is god mode per tenant: cost visibility, agent autonomy config, raw LLM prompts, pre-flight override. Implicitly passes every `require_role` check. Excluded from the 20-user cap.
- **Encryption**: Fernet for credentials (cryptography lib)
- **AI**: Anthropic SDK (Claude Opus 4 / Sonnet 4 / Haiku 4 / 3.7 / 3.5)
- **Scheduling**: croniter-based cron; the Railway scheduler service fires the substrate job queues (`s1_sync_jobs`, `s4_execution_jobs`) and runs reaper / dead-man's-switch ticks
- **Deployment**: Railway (3 services: web/worker/scheduler), Dockerfile, gunicorn

## Project structure
```
primeqa/                       # Main package
├── app.py                     # Flask entrypoint, registers blueprints + observability
├── db.py                      # SQLAlchemy engine/session setup
├── views.py                   # Server-rendered web UI routes (HTMX-friendly)
├── worker.py                  # Background job consumer (python -m primeqa.worker)
├── scheduler.py               # Reaper + dead-man's switch + job-queue firer
│
│   # ── The live engine: S1–S8 substrate ──────────────────────────────
├── semantic/                  # S1: semantic org model — entities/edges, bitemporal
├── sync/                      # S1: live sync engine (engine/materialize/batching)
├── test_representation/       # S2: canonical claims + recipes
├── generation/                # S3: AI generation (run/intake/governance/persistence)
├── execution_engine/          # S4: run recipes vs Salesforce, capture grounded evidence
├── knowledge/                 # S5: domain packs + learned rules + system-rules channel
├── interpretation/            # S6: deterministic-first verdicts + cause attribution
├── conversation/              # S7: grounded-or-refuse Q&A (/ask)
├── evolution/                 # S8: grounding-validity (intact/drifted/broken) + repair
│
│   # ── Cross-cutting ─────────────────────────────────────────────────
├── core/                      # Tenants, users, auth, envs, connections, groups, agent_settings
├── intelligence/              # substrate_decision (GO/NO-GO), substrate_insights, llm/ gateway
│   └── llm/                   # LLM gateway — gateway.py, router.py, provider.py,
│                              # prompts/*, pricing, usage, limits, tiers, feedback,
│                              # feedback_rules (aggregation), redact, dashboard
├── release/                   # Release model, decision_engine, CI webhooks
├── runs/                      # Run surfaces, Jira search client + cache, scheduling helpers
├── integrations/              # sf_client (Salesforce REST/Metadata), sf_constants
├── metadata_bridge/           # S1 read-bridge + parity harness (from the v1 cutover)
├── shared/                    # query_builder, api envelope, observability, notifications
├── system_validation/         # JSON-driven self-validation suite runner + grammar
│   └── suites/primeqa_core.json   # the canonical 8-category E2E suite
├── vector/                    # Embeddings (pgvector)
├── static/                    # Shared JS/CSS (toast, confirm, unsaved-changes)
└── templates/                 # Jinja2 HTML templates
│
│   # ── Legacy modules (still present, mostly gutted post-v1-retirement) ─
├── metadata/                  # v1 metadata module — superseded by semantic/ + sync/
├── execution/                 # v1 execution module — routes.py survives (release-status etc.)
└── test_management/           # v1 test-mgmt — service.py survives; product tables dropped (053)

migrations/                    # Public/v2 SQL migrations (001–056), idempotent since 016
alembic/                       # Substrate schema-per-tenant migrations (per-tenant DDL)
scripts/                       # One-off operational SQL (data cleanup, eval harnesses)
docs/architecture/             # Substrate SPEC/EVOLUTION + PLATFORM_VISION + DECISIONS_LOG
tests/                         # Integration tests (run against the Railway database)
```

## Architecture rules

- **The live engine is the 8-substrate decomposition** (S1–S8), each a self-contained package with strict boundaries — never cross a substrate boundary to get something working (HOLD + flag instead). Cross-cutting modules: `core`, `intelligence` (decision + llm gateway), `release`, `runs`, `integrations`, `metadata_bridge`, `shared`, `system_validation`, `vector`.
- Each module has: models.py, repository.py, service.py, routes.py (substrate packages vary — e.g. `run.py`, `intake.py`, `result_store.py`).
- Cross-domain calls go through service layers, never direct SQL across domains
- All resources are tenant-scoped via `tenant_id`
- Environments scope by group membership (admin + superadmin see all)
- Settings pages live under `/settings/*` with a sidebar layout
- **Superadmin is god-mode**: always passes `require_role` / `role_required`, sees cost + raw LLM prompts + agent settings

## Permission Model

Canonical authorization model for the platform. Every new feature — API
endpoint, UI action, scheduled job, agent decision — must resolve against
this model. When in doubt, default to denial + log a rationale.

- **Additive Permission Sets, no deny rules.** Authorization resolves as
  the **union** of every set granted to the caller. No set can subtract
  from another set's grants. Forbid a capability by not granting it, not
  by layering a deny.
- **Two-layer access**: every authorization check composes **user
  permissions** (who the caller is) AND **environment run policies**
  (what the target env will accept — e.g. production blocks agent
  auto-apply per Q2). Both must allow the action; either can veto.
- **Five Base Permission Sets** (all other custom sets derive from
  these):
  - **Developer** — author / edit test cases, run sandbox pipelines,
    view own runs
  - **Tester** — run pipelines against assigned envs, triage failures,
    accept/revert agent fixes on sandbox
  - **Release Owner** — create/manage releases, approve agent fixes on
    production candidates, finalize GO/NO-GO decisions
  - **Admin** — tenant admin: users, groups, connections, envs;
    cannot override production agent auto-apply
  - **API Access** — programmatic token holder; equivalent to Developer
    scope unless explicitly extended. Token-scoped, never interactive.
- **Ownership on all resources**: every row carries
  `owner_user_id` (who owns the resource) and — on execution rows —
  `triggered_by_user_id` (who kicked off the run). Ownership is a
  separate axis from role. "Own" views always scope by the caller's
  user id.
- **Release state on runs**: runs inherit a
  release-lifecycle state independent of their execution status —
  `PENDING` / `APPROVED` / `OVERRIDDEN`. Agent auto-apply + production
  deploys gate on this state; a Release Owner's approval flips
  PENDING→APPROVED, and Admin OVERRIDDEN is audited separately.
- **Superadmin stays god-mode** for cross-tenant ops (cost, raw LLM
  prompts, agent settings override, pre-flight override). Superadmin
  bypass is intentionally simple and outside the permission-set union;
  its use is always logged to `activity_log`.
- **Every new endpoint / action** must map to (a) the union of Base
  Permission Sets that grant it and (b) the env run-policy flags it
  needs. If either mapping is unclear, surface the question in the PR
  before shipping.

## Security posture (post-audit 2026-04-19)

- **Login never takes client-supplied `tenant_id`.** `AuthService.login(email, password)` derives tenant from the `users` row (same email can exist in >1 tenant; first active match with correct bcrypt wins). If a caller has legitimate reason to scope to a specific tenant (SSO), pass `tenant_id=` explicitly in the service call — never from user input.
- **CSRF**: double-submit cookie via `primeqa/core/csrf.py`. `/api/*` with `Authorization: Bearer` skips CSRF (Bearer is cross-origin-safe). All HTML POST forms carry `{{ csrf_input | safe }}`. `static/js/csrf.js` auto-injects `X-CSRF-Token` header on same-origin `fetch()` + htmx.
- **JWT**: `core/auth.py require_auth` tolerates missing optional claims (`email`, `role`, `full_name`) — defaults `role='viewer'` on missing. `sub` + `tenant_id` are required; missing = 401. Views.py `get_current_user` has the same tolerance so web pages don't crash on malformed tokens. **Role downgrade** → `AuthService.update_user` revokes all refresh tokens so stale access-tokens expire quickly.
- **Webhook auth** fails closed: `/api/webhooks/ci-trigger` returns 503 `CONFIG_ERROR` when `WEBHOOK_SECRET` env is unset. HMAC signature required otherwise.
- **Public release-status endpoint is token-gated** (migration 055): `GET /api/releases/:id/status` requires a per-release opaque poll token (`status_poll_token_hash`, sha256); no token / wrong token → 404. Mint + revoke are Release-Owner/admin, tenant-scoped.
- **Global 500 handler**: `app.errorhandler(Exception)` returns envelope (`/api/*`) or minimal HTML (web). Never leaks stack. Server-side full stack still logged.
- **Input validation**: `create_section` length-validates name; `feedback.capture_user_feedback` type-checks verdict; bulk endpoints coerce ids to positive ints before hitting the DB.
- **Tenant isolation**: cross-tenant write/read paths in the release + permission + connection layers are tenant-scoped (release decisions filter by `release_id`; `check_environment_policy` takes `tenant_id`; see `tests/test_tenant_isolation.py`).
- **Unbounded queries**: `core/repository.py list_*` capped at 500 rows. DB-side dashboard queries use CTEs + JOINs to avoid N+1.

## UI component kit (`templates/components/`)

- **`_buttons.html`** — `btn_primary`, `btn_secondary`, `btn_success`, `btn_edit`, `btn_danger_primary`, `btn_danger_link`. One macro per semantic role. Never hardcode `bg-indigo-600` / `bg-red-600` / `bg-gray-600` — import the macro.
- **`_empty_state.html`** — `empty_state(title, description, cta_label, cta_url|cta_onclick, icon, compact)`. One visual for every "no rows" treatment.
- **`_modal.html`** — `modal_shell(id, title, size, describedby)` via `{% call %}`. Produces dialog envelope with close button, overlay click-to-close, and full a11y (`role=dialog`, `aria-modal`, `aria-labelledby`). Paired with `static/js/modal.js` for focus trap + Escape + Tab wrap + return-focus.
- **`breadcrumbs.html`** — `breadcrumbs([(label, href), ...])`. Every detail/edit page should call this.
- **`pagination.html`** — `render_pagination`, `render_search`, `sort_header`, `per_page_selector`, `render_meta_pagination`.
- **`confirm_modal.html`** + `static/js/confirm.js` — attribute-driven `data-confirm`, `data-confirm-form`, `data-confirm-variant`, `data-confirm-type-to`. Never use native `confirm()`.
- **`feedback_modal.html`** + `static/js/tc_feedback.js` — thumbs feedback on AI-generated TCs.
- **`static/js/loading.js`** — global listener that disables submit buttons + adds `aria-busy` during in-flight actions. Opt out via `data-no-loading`.

**Rule**: every new page checks in with the component kit. If you find yourself writing `<button class="rounded-md bg-...">` or `<div ...No X yet...>`, you're doing it wrong.

## Cross-cutting primitives (`primeqa/shared/`)

- `query_builder.ListQuery` — pagination/search/sort/filter with hard 50/page cap and sort-field whitelist
- `api.json_page` / `json_error` — uniform `{data, meta}` + `{error:{code,message}}` envelopes
- `observability` — request timing, SQLAlchemy slow-query log at 800 ms (tunable via `PRIMEQA_SLOW_QUERY_MS`; default threshold sits above Railway's ~400–500 ms RTT floor), counters at `GET /api/_internal/health`
- `notifications` — stable `notify_*` API with REAL email providers (D-200): `log` (default) / `smtp` / `sendgrid` via `NOTIFICATIONS_PROVIDER`, all best-effort (never raise). Wired: `notify_release_decision` (decision_composer) + `notify_substrate_run_failed` (S4 execution consumer, unattended runs, D-234). Real email needs the provider's env config (e.g. `SENDGRID_API_KEY` + `SMTP_FROM`); unset → logs a warning + skips.

## LLM architecture (`primeqa/intelligence/llm/`)

Single chokepoint for every Anthropic call. Replaces five scattered call
sites that drifted on retry policy, caching, and usage accounting. The
gateway core is live cross-cutting infra (`llm_usage_log` survived the v1
retirement). **Note**: integration points that wrote to v1 tables — the
story-view enricher (→ `test_case_versions.story_view`), the static
validator's machine signals, the `generation_batches` cost rows — retired
with the v1 product layer (migration 053); the gateway itself remains the
live entry point, now driven by S3 generation / S6 interpretation / S7
conversation.

- **`gateway.llm_call(task=..., tenant_id=..., api_key=..., context=...)`** is the ONLY allowed entry point. Internal flow: load tenant config (tier → limits, policy) → check rate limits (minute / hour / daily-spend windows) → resolve complexity from prompt module → router picks `[primary, fallback]` chain → build prompt spec → redact PII → provider.invoke with backoff → escalate once on low-confidence if the prompt declares `SUPPORTS_ESCALATION` → record `llm_usage_log` row (always, success or fail) → return `LLMResponse`.
- **Prompts** live one-per-file in `prompts/*`. Each module exposes `VERSION`, `build(context, tenant_id, recent_misses)`, `detect_complexity(context)`, optional `should_escalate(parsed, resp)`. Registry is a flat static dict — no dynamic loading.
- **Router** (`router.py`): `_CHAINS` keyed by task × complexity, with `TenantPolicy` overrides (`always_use_opus`, `allow_haiku`). Chain length caps at 2 — one escalation hop, never more.
- **Tool use**: `test_plan_generation` uses Anthropic `tool_use` API (`submit_test_plan` with strict JSON schema) so parse failures become impossible; escalation triggers on zero TCs / low confidence / tool not called.
- **Prompt caching**: `cache_control: ephemeral` on grammar + metadata blocks. Cache key is per-tenant because metadata text is tenant-unique (correct isolation; no cross-tenant hits).
- **Per-tenant rate limits** (migration 032 + tiers via migration 034): tier preset → override-wins on any non-NULL raw column. Blocked calls write a zero-token `status='rate_limited'` row to `llm_usage_log` and raise `LLMError("rate_limited")`. Three windows: 60 s / 3600 s / UTC-midnight spend.
- **Product tiers** (`tiers.py`): `starter` (30/500/$5), `pro` (100/2000/$25), `enterprise` (None/None/None), `custom` (ignore preset, raw columns only). Tenant switches tier via the superadmin picker on `/settings/llm-usage` — writes to `tenant_agent_settings.llm_tier` + activity_log.
- **Feedback loop** (migration 033): `generation_quality_signals` table + `feedback_rules.py` aggregate signals into a prompt-ready `### Common mistakes to avoid:` block — natural-language imperatives ranked by severity × frequency, top-5. (Signal sources that wrote to v1 tables — validator-critical, ba_rejected — retired with the v1 layer; the aggregation surface remains.)
- **Domain Packs / knowledge channel**: the pack + rules mechanism lives in **S5** (`primeqa/knowledge/` — `domain_packs.py`, `domain_pack_provider.py`, `provider.py`, `feedback_rules.py` stays in `intelligence/llm/`). Its original v1 consumer (`generation.py`) retired with v1; S3 generation carries a `domain_pack_refs` slot (`generation/protocol.py`). Whether the S5→S3 consumption is fully wired is design-tracked in `docs/architecture/substrate_5_knowledge/SPEC.md` + DEFERRED_ITEMS (the seam was "settled as not wired" per D-156). Flag `llm_enable_domain_packs`; attribution rides `llm_usage_log.context->'domain_packs_applied'`.
- **Dashboards**: superadmin `/settings/llm-usage` (cost by task/model/tenant/day, efficiency, top spenders, per-tenant tier picker) + tenant admin `/settings/my-llm-usage` (plan, soft-cap progress bars, blocked-calls counter, spend-by-feature).
- **Providers** (`providers/registry.py`): routes by model-id prefix. `claude-*` → Anthropic, `gpt-*` / `o1-*` → OpenAI stub (raises NotImplementedError today). Cross-vendor fallback chains are architecturally supported — the router just needs both sides present in the registry.
- **PII redaction** (`redact.py`): compiled regexes scrub emails, IPs, SSN-shaped, long digit runs from outbound prompts. Structure-preserving.
- **Migration**: never bypass the gateway. New callers always go through `llm_call()`. Legacy direct-Anthropic paths remain only as fallback when no `tenant_id` + `api_key` is available (i.e. system-level calls).

## The live engine — S1–S8 substrate

The release-intelligence loop runs on an eight-substrate decomposition. Each
substrate is a self-contained package with its own design SPEC under
`docs/architecture/substrate_*/SPEC.md` (the canonical, current design-of-record).

| # | Substrate | Package | Role |
|---|---|---|---|
| S1 | Semantic org model | `semantic` + `sync` | Salesforce metadata as a bitemporal behavior graph (entities + edges); live sync engine. Schema-per-tenant. |
| S2 | Test representation | `test_representation` | Canonical claims + recipes — the shared test-case representation |
| S3 | Generation | `generation` | AI generates claims/recipes from Jira requirements, grounded in S1, ground-or-refuse |
| S4 | Execution engine | `execution_engine` | Runs recipes against Salesforce, captures grounded `RunEvidence`, provisioning + reverse-order cleanup, async job queue |
| S5 | Knowledge | `knowledge` | Domain packs + learned rules + system-rules channel that sharpen generation |
| S6 | Interpretation | `interpretation` | Deterministic-first verdicts + cause attribution over the captured evidence |
| S7 | Conversation | `conversation` | Grounded-or-refuse Q&A surface (`/ask`), LLM-free retrieval + bounded assembly |
| S8 | Evolution | `evolution` | Grounding-validity (intact / drifted / broken) as the org changes, + repair suggestions |

- **Decision**: `primeqa/intelligence/substrate_decision.py` → GO / CONDITIONAL_GO / NO_GO with per-requirement explainability (D-237). Wired to releases; surfaced on `/releases/<id>?tab=decision`.
- **Async execution**: S1 sync and S4 execution run through DB-backed job queues (`s1_sync_jobs`, `s4_execution_jobs`) consumed by the worker; the scheduler runs reaper + dead-man's-switch ticks.
- **Run-time test-data injection** (D-235) lives in S4 field-overrides (positive-only, sync UI). The v1 Test Data Engine (templates/factories) was removed (D-243).
- **Fix-proposal agent** (D-236): the LLM proposes repairs with confidence + rationale + diff; flag-gated auto-apply on sandbox only (`evolution/repair.py`).
- **Live proof**: the full loop runs daily against the live **env-59** org (`docs/design/flagship-live-proof.md`); the canonical HIGH-tier requirement is **SQ-205** (Service Cloud case escalation).

The architecture is documented in `docs/architecture/PLATFORM_VISION.md`
(the 8-substrate vision) and the per-substrate SPEC/EVOLUTION/DEFERRED docs.
The append-only `docs/architecture/DECISIONS_LOG.md` is the canonical decision
ledger.

> **Retired with v1 (D-191…D-221, migration 053):** the `pipeline_runs` Run
> Wizard + SSE `run_events` log, the multi-TC generator over
> `test_case_versions`/`generation_batches`, the static validator
> (`validator.py`), the story-view enricher (`enrichment.py`), the v1
> fix-and-rerun `AgentOrchestrator`, and BA reviews (`ba_reviews`). The retired
> v1 Run-experience design lives in `docs/archive/design/run-experience.md`.

## Self-Validation Suite

Plimsol runs itself via a JSON-driven E2E suite — the canonical artifact
is at `primeqa/system_validation/suites/primeqa_core.json` (now named
"Plimsol Core Validation"). Grammar is documented in
`docs/design/system-validation.md`. Run with:

```bash
python tests/test_system_validation.py
```

The suite covers 8 workflow categories (Requirements, Test Library, Run
Flow, Jira, Preview, Metadata, Agent, UI Nav) and is authorable by
non-engineers or LLMs.

## Key commands
```bash
# Run locally
source venv/bin/activate
python -m primeqa.app                    # Flask dev server on :5000

# Integration tests (against the Railway database) — current corpus
python tests/test_auth.py                # auth
python tests/test_environments.py        # environments
python tests/test_system_validation.py   # runner + canonical suite outcomes
python tests/test_llm_architecture.py    # gateway / tiers / limits / dashboards / feedback
python tests/test_eval_harness.py        # offline prompt regression harness
python tests/test_run_tests_page.py      # substrate /run page
python tests/test_s4_execution_jobs.py   # S4 async execution queue
python tests/test_s7_conversation_contract.py   # S7 conversation
python tests/test_tenant_isolation.py    # cross-tenant isolation
# (run `ls tests/test_*.py` for the full current list)

# Deploy
git push origin main                     # Railway auto-deploys 3 services

# Apply a public migration (idempotent since 016)
psql "$DATABASE_URL" -f migrations/056_drop_dead_test_data_tables.sql
```

## Environment variables
- `DATABASE_URL` — PostgreSQL connection string (Railway auto-provides)
- `JWT_SECRET` — 64-char hex string for JWT signing
- `CREDENTIAL_ENCRYPTION_KEY` — 64-char hex for Fernet encryption
- `WEBHOOK_SECRET` — HMAC key for CI/CD webhooks (optional)
- `NOTIFICATIONS_PROVIDER` — `log` (default) / `smtp` / `sendgrid` (real, D-200; no SES). `smtp` needs `SMTP_HOST` (+ `SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_STARTTLS`); `sendgrid` needs `SENDGRID_API_KEY`. Both use `SMTP_FROM` as the sender.
- `PORT` — HTTP port (Railway sets, default 5000)
- `FLASK_ENV` — `production` on Railway

## Database
PostgreSQL on Railway with pgvector. **Two migration systems coexist:**
- **Public/v2 tables** — numbered plain-SQL files in `migrations/` (001–056), run via `psql`. **Never mutate an existing migration** — add a new numbered one. Migrations 016+ are idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).
- **Substrate tables** — schema-per-tenant DDL via `alembic` (the substrate uses one schema per tenant; v2 product tables stay in `public` keyed by `tenant_id`).

## Conventions
- Repository pattern: all DB queries go through repository classes
- Service pattern: business logic in service classes
- API routes return JSON under `/api/*` using the uniform envelope
- Web views render templates under `/`
- Tests are integration tests against the real Railway database
- Tenant isolation: every new table includes `tenant_id` (public) or lives in the tenant's schema (substrate); every query scopes by it
- AI outputs carry structured reasoning so the decision layer can surface explainability
- Commit messages are descriptive, prefixed with phase/feature name. **Author `AK <amjad.khan@teamd.co.in>`; no `Co-Authored-By` trailer** (see _Working rules → Commits & branches_)
- Every destructive/admin action writes to `activity_log` via the service layer
- All lists paginate with per_page capped at 50 — there are no unbounded list endpoints anymore
- **Section create is idempotent** — `create_section` returns an existing active row if one matches `(tenant_id, parent_id, name)`. Prevents duplicate-tree regrowth from integration tests.
- **Thread-safety**: SQLAlchemy sessions are **not** shared across threads. Bulk endpoints / per-thread work open `Session(bind=engine)` per thread — never share a scoped session across threads.
- **HTML unicode**: never write `\uXXXX` escapes directly in Jinja/HTML content — HTML doesn't interpret them. Use the actual UTF-8 character or `&#NNNN;` entity. (JS string literals **do** interpret `\uXXXX`; those are fine.)

## The Release Intelligence Loop

```
Release → Requirements (Jira)
       → S1 semantic org model (per-category metadata sync)
       → S3 AI-generated claims/recipes (grounded in S1, ground-or-refuse)
       → S4 execution against Salesforce → grounded RunEvidence (+ provisioning/cleanup)
       → S6 interpretation → verdict + cause attribution
       → S8 grounding-validity (intact/drifted/broken as the org changes)
       → Decision Engine (substrate_decision.py) → GO/NO-GO Recommendation (explainable)
       → S8 fix-proposal agent (human-gated; flag-gated sandbox auto-apply)
       → Human confirms → CI/CD proceeds
```

Every AI output carries reasoning. Every release decision is recommendation-only (human confirms). Every agent fix is reversible via the before-state snapshot.
