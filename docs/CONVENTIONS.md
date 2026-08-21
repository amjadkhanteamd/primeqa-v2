# Plimsol Documentation and Working Conventions

> Product renamed PrimeQA → Plimsol on 2026-06-15 (display text only). Doc
> filenames like `PRIMEQA_PRODUCT_DEFINITION.md` keep their names; the
> `primeqa/` package path is unchanged.

## Phase numbering

Four legitimate phase-numbering schemes coexist. Each describes a
different thing. Use the one appropriate to your context; qualify on
first reference per section if ambiguous.

### Product roadmap phases

Per `PRIMEQA_PRODUCT_DEFINITION.md` §6.3. The customer-visible product
evolution.

As of 2026-06-15 the whole ladder below has shipped — all 8 substrates are
live and the v1 product layer was retired (D-191…D-221). Kept as the
historical phasing record.

- Product-Phase 1: Substrate 1 foundation (shipped)
- Product-Phase 2: Substrate 1 sync layer (shipped)
- Product-Phase 3: Substrate 2 (Test Representation) design (shipped)
- Product-Phase 4: Substrate 3 (Generation Engine) (shipped)
- Product-Phase 5: Substrate 4 (Execution Engine) (shipped)
- Product-Phase 6: Substrate 6 (Observation and Interpretation) — v1 product moment (shipped; decision loop live, D-237)
- Product-Phase 7+: Substrate 5 / 7 / 8 (shipped); hardening + multi-tenant readiness in progress

### Substrate 1 12-week engineering phases

Per D-024. Internal engineering phases for shipping S1.

- S1-Phase 0: scaffolding (shipped)
- S1-Phase 1: edges, derivation, etc. (shipped)
- S1-Phase 2: sync engine (shipped)
- S1-Phase 3: query class (shipped)
- S1-Phase 4: cutover (replaces v2 `meta_*`) — largely complete (D-191…D-221); the `meta_*` physical table drop is the one remaining tail
- S1-Phase 5: hardening

### Architecture 4 rollout phases (legacy)

Per `docs/architecture/archive/ARCHITECTURE_4_NOTE.md`. Paused per
D-003. Referenced for institutional context only.

### v2 prompt-sequence numbering (legacy)

In `CLAUDE.md` and related files, "Prompt 7", "Prompt 11", etc. refer
to numbered prompts in v2's engineering history. These are not phases.
v2 runtime context only.

## Branch conventions

Two patterns coexist depending on what you're working on:

- **v2 runtime work** (Flask routes, HTMX templates, the `core` /
  `release` / `runs` web layer, `meta_*` tables): commit directly to
  `main`. Iteration is fast; `main` is the integration surface.
- **Substrate work** (substrate_1_semantic_org_model and future
  substrates): work on feature branches named
  `phase-N-substrate-M` (e.g., `phase-2-substrate-1-sync`). Merge to
  `main` at phase completion. Discipline is HOLD-and-show before
  commits; per-cycle gates.

## Working agreement (substrate work)

### HOLD-and-show before commits

Implementation work proceeds in cycles:

1. **Survey before implementing.** Probe the actual code surface; do
   not assume function signatures, return shapes, or behavior from a
   context block alone.
2. **Live-probe before assuming.** Use a real SOQL/DB/API probe to
   verify behavior, especially for external systems (Salesforce,
   Voyage, Anthropic) and substrate-1 schema constraints.
3. **HOLD with full report before committing.** Show diffs, test
   results, deviations from spec, cost estimates where applicable.
4. **User reviews and gives explicit GO.** Verbatim commit messages.
5. **Push to feature branch.** Merge to `main` at phase completion
   via PR.

### Pre-flight schema verification

Before running expensive integration tests (>5 min wall-clock),
pre-flight schema-verify any non-trivial queries in new test code via
direct `psql` against current DB state. Cheap to do (~1 second per
query); prevents 10+ minute discovery of column-name typos or schema
mismatches.

Example pattern:

```bash
psql "$LIVE_DATABASE_URL" -c "
  SELECT column_name FROM information_schema.columns
  WHERE table_schema = 'tenant_1'
    AND table_name = 'entities'
    AND column_name IN ('sf_api_name', 'external_id');
"
```

This pattern was added to CONVENTIONS after §23 attempt 3 discovered
a column-name typo in test telemetry only after a 10-minute
integration test run.

### E2E test scenarios cadence

`tests/integration/test_e2e_sync_scenarios.py` is the canonical
formal-scenarios surface for Phase 2 substrate-1 sync layer
(per §25 + §26 corrections-log).

**Cadence: on-demand or nightly. NOT per-PR.**

- Suite wall-clock: ~30-35 min per full run.
- Suite cost: ~$0.07 per run (~$0.033 Anthropic + ~$0.04
  Voyage estimated; Voyage `cost_usd=0.0` per design — embedding
  token-accounting deferred).

When to run:

- Before merging substrate-1 work to `main`.
- Before customer-facing release of substrate-1 capabilities.
- Nightly in CI if a nightly schedule is configured.
- On-demand when investigating a sync-layer issue.

The deep per-phase / per-entity-type / per-edge live tests
(`test_sync_object_phase_live.py`, `test_live_enrichment.py`)
are debugging-grade and run on the same on-demand cadence.

Deferred scenarios catalog (see PARKING_LOT P-010 through
P-014): partial-sync resume, bitemporal historical query,
error-recovery, worker restart mid-drain, cross-tenant
isolation. Each has named revisit triggers.

### Test fixture patterns

- **`load_dotenv(override=True)` in test fixtures.** Default
  `load_dotenv()` respects existing env vars including empty strings
  that may be set by parent shells (e.g., Claude Code's own env).
  Tests own their environment; `override=True` ensures `.env` values
  are applied.
- **Cross-domain SQLAlchemy model registration.** Tests that use
  `Base.metadata` across domains must import all relevant model
  modules before any `create_all` or metadata-dependent operation:

  ```python
  from primeqa.core import models  # noqa: F401
  from primeqa.test_management import models  # noqa: F401
  from primeqa.execution import models  # noqa: F401
  from primeqa.intelligence import models  # noqa: F401
  ```

  The `noqa: F401` is intentional — these imports register tables in
  `Base.metadata` even if the module is otherwise unused.

### Dual migration systems

The repository contains two parallel migration systems covering
different schema domains:

- **`migrations/`** — numbered `.sql` files (001 through 056),
  applied via `psql`. Canonical migration system for the `public`-schema
  v2 runtime tables that survive (`requirements`, `releases`,
  `llm_usage_log`, `generation_quality_signals`, `connections`, etc.).
  The v1 product tables it once managed (`test_cases`,
  `test_case_versions`, `pipeline_runs`, `generation_batches`, …) were
  dropped in migration 053 (D-221).
- **`alembic/`** — Python migrations under `versions/shared/` and
  `versions/tenant/`. Canonical migration system for substrate-1
  (semantic org model) tables: `entities`, `edges`,
  `logical_versions`, detail tables, `ai_enrichment_queue`, etc.

Both systems are active and target different schema domains. When
making schema changes, choose the system appropriate to the table
domain:

- v2 runtime table change → new numbered `.sql` in `migrations/`
- substrate-1 table change → new alembic revision

The dual-system pattern is intentional. The Phase-4 cutover has now
largely run (v1 product tables dropped, migration 053), but `migrations/`
remains live — it still holds the history for the surviving
`public`-schema v2 tables, so it is not yet an archive candidate
(see `PARKING_LOT.md`).

### Migration apply pattern

Substrate-1's alembic env has two branch chains (`shared`, `tenant`).
Use:

```bash
alembic -x mode=tenant -x tenant_id=N upgrade tenant@head
```

Not bare `upgrade head` (ambiguous head selection — fails with
"Multiple head revisions").

For PostgreSQL on macOS Homebrew:

- `pg_ctl start` may need `LC_ALL=C` to avoid "postmaster became
  multithreaded during startup" errors
- `alembic upgrade` needs `LC_ALL=en_US.UTF-8` for UTF-8 handling

## Documentation authority

- **`PLATFORM_VISION.md`** is the architectural authority for
  substrate decomposition per D-001 / D-050.
- **`PRIMEQA_PRODUCT_DEFINITION.md`** describes the product built on
  this architecture.
- **`DECISIONS_LOG.md`** is append-only; decisions are not edited in
  place, only superseded by later decisions. New headings above D-458
  must take an unused number, or carry a continuation marker
  (`(close)` / `CLOSE` / `(design)` / `Result` / `REALIZED` / `(cont.)`)
  when they extend an existing decision — enforced by
  `scripts/check_decision_numbers.py` (runs in the unit gate suite;
  commit-time via `git config core.hooksPath .githooks`, once per clone).
- **`EVOLUTION.md`** (per substrate) is append-only.
- **`PHASE_N_PLAN.md`** documents are locked planning artifacts;
  corrections tracked in `PHASE_N_PLAN_corrections.md`.
- **Substrate specs** (`docs/architecture/substrate_N_*/SPEC.md`) are
  authoritative for that substrate's design.

When in doubt: PLATFORM_VISION wins on architecture, PRODUCT_DEFINITION
wins on product narrative, specific substrate spec wins on that
substrate's behavior.
