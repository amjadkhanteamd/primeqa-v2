# Plimsol (PrimeQA v2)

**Release Intelligence System for Salesforce.** Connect a Salesforce org,
capture its metadata as a versioned semantic model, AI-generate grounded test
cases from Jira requirements, execute them against the org with real evidence,
interpret failures into causes, and produce GO / NO-GO release recommendations
with explainability.

> **Naming**: the product display name is **Plimsol** (renamed 2026-06-15). The
> Python package stays `primeqa` and the JS namespace stays `window.PrimeQA` —
> all `primeqa/` paths below are current, only the product name changed.

## Live
https://primeqa-v2-production.up.railway.app — login `admin@primeqa.io` / `changeme123`

## Architecture — the S1–S8 substrate

The live engine is an eight-substrate decomposition. Each substrate is a
self-contained package with its own design SPEC under
`docs/architecture/substrate_*/`.

| # | Substrate | Package | Role |
|---|---|---|---|
| S1 | Semantic org model | `primeqa/semantic` + `primeqa/sync` | Salesforce metadata captured as a bitemporal behavior graph (entities + edges), schema-per-tenant |
| S2 | Test representation | `primeqa/test_representation` | Canonical claims + recipes — the shared test-case representation |
| S3 | Generation | `primeqa/generation` | AI generates claims/recipes from Jira requirements, grounded in S1, ground-or-refuse |
| S4 | Execution engine | `primeqa/execution_engine` | Runs recipes against Salesforce, captures grounded evidence, provisioning + cleanup, async job queue |
| S5 | Knowledge | `primeqa/knowledge` | Domain packs + learned rules + system-rules channel that sharpen generation |
| S6 | Interpretation | `primeqa/interpretation` | Deterministic-first verdicts + cause attribution over the captured evidence |
| S7 | Conversation | `primeqa/conversation` | Grounded-or-refuse Q&A surface (`/ask`) |
| S8 | Evolution | `primeqa/evolution` | Grounding-validity (intact / drifted / broken) as the org changes |

The release decision is computed by `primeqa/intelligence/substrate_decision.py`
(GO / CONDITIONAL_GO / NO_GO with per-requirement explainability). The full loop
runs daily against the live env-59 org.

Cross-cutting packages:
- `core/` — tenants, users, auth, environments, connections, groups, agent settings
- `intelligence/llm/` — the single `llm_call()` gateway (router, providers, prompts, tiers, per-tenant rate limits, feedback, usage, dashboards, PII redaction)
- `release/` — releases, decisions, decision engine, CI webhooks (`/api/releases/:id/status`, token-gated)
- `runs/` — run wizard surfaces, Jira client + TTL cache, scheduling
- `shared/` — ListQuery, API envelope, observability, notifications
- `system_validation/` — JSON-driven self-validation suite (Plimsol tests Plimsol)
- `metadata_bridge/` — S1 read-bridge / parity harness from the v1 cutover

> **v1 retired (D-191…D-221).** The original v1 product layer — the
> `pipeline_runs` Run Wizard, `test_case_versions` multi-TC generator, the
> static validator, the story-view enricher, the Test Data Engine, and the v1
> fix-and-rerun agent — was removed and its 20 product tables dropped in
> migration 053 (plus the four `data_*` tables in 056). The substrate above is
> the live engine. See `docs/architecture/greenfield_cutover/` for the cutover
> record.

3 Railway services from one codebase: `web` (Flask + gunicorn), `worker`
(substrate job consumer), `scheduler` (reaper + dead-man's switch + cron firer).

## Roles
`viewer`, `ba`, `tester`, `admin`, `superadmin` (god mode — cost visibility,
agent autonomy config, raw LLM prompts, pre-flight override; seeded one per
tenant, excluded from the 20-user cap).

## Stack
- Python 3.11, Flask, SQLAlchemy, PostgreSQL (pgvector)
- Jinja2 + Tailwind (CDN) + HTMX + vanilla JS + SSE
- JWT auth (5 roles), Fernet encryption for credentials
- Anthropic SDK for AI generation, interpretation, and conversation
- croniter for schedule parsing
- Railway for deployment

## Documentation
- `CLAUDE.md` — project context for Claude Code / AI agents (current substrate state)
- `docs/architecture/PLATFORM_VISION.md` — the canonical 8-substrate architecture
- `docs/architecture/substrate_*/SPEC.md` — per-substrate design-of-record (S1–S8)
- `docs/architecture/DECISIONS_LOG.md` — append-only decision ledger (canonical state)
- `docs/product/PRIMEQA_PRODUCT_DEFINITION.md` — product description
- `docs/CONVENTIONS.md` — phase numbering, branch conventions, substrate working agreement
- `docs/design/system-validation.md` — self-validation step grammar + canonical suite
- `docs/archive/` — historical snapshots (v1 architecture spec, run-experience design, build plans)

## Local dev
```bash
source venv/bin/activate
python -m primeqa.app        # :5000
```

## Deploy
`git push origin main` — Railway auto-deploys all 3 services.

## Tests
Integration tests run against the Railway database. The live corpus is the
substrate suites plus the surviving cross-cutting suites:
```bash
for t in test_auth test_environments test_management test_r2_superadmin \
         test_system_validation test_llm_architecture test_eval_harness \
         test_s4_execution_jobs test_s5_knowledge_contract \
         test_s7_conversation_contract test_s7_conversation_intent \
         test_s7_conversation_assembler test_s7_conversation_answerer \
         test_knowledge_architecture test_knowledge_console \
         test_interpretation_phrasing test_results_page test_browse_drill \
         test_run_tests_page test_data_injection test_quarantine_page \
         test_permission_enforcement test_permission_sets test_tenant_isolation \
         test_release_audit test_sf_access_attribution test_scheduler_resilience; do
  python tests/$t.py
done
# (run `ls tests/test_*.py` for the full current list)
```
