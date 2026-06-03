# Greenfield Cutover — SPEC

**Status:** Phase 7 (program) — opened docs-led (D-145). This is the authoritative design for the cutover where the substrate spine (S1–S8) replaces the live v1 product and the v1 `meta_*` tables are dropped. **Design only — the cutover *execution* runs in later phases, gated by the SEQUENCE.**

**Last substantive update:** 2026-06-03 (opened; D-145)

---

## Purpose

The substrate spine (S1 `semantic/` + `sync/`, S2 `test_representation/`, S3 `generation/`, S4 `execution_engine/`, S5 `intelligence/knowledge/`, S6 `interpretation/`, S8 `evolution/`) was built **in parallel** to a live v1 Flask product (`primeqa/intelligence/`, `primeqa/metadata/` + `meta_*`, `primeqa/test_management/`, `primeqa/execution/`, `primeqa/release/`, the Flask views). The **greenfield cutover** is the milestone that flips the product onto the spine and retires v1 — its single irreversible act being the drop of the `meta_*` metadata store.

This document exists because the cutover was, until now, **undesigned** — its intent scattered across D-012 / D-065 / D-074 / D-111 / D-134 — and **not executable as one phase** (verified: `meta_*` is the live store; no production trigger runs the S1 Salesforce→`entities` sync; no `meta_*`→S1 migration; no substrate UI consumers). This SPEC consolidates the scattered dispositions into one map, fixes the migration strategy + schema topology, and (with `SEQUENCE.md`) lays out the **ordered, gated steps with the `meta_*` drop strictly last**. After this, executing the cutover is running gated steps — not re-deriving scope.

**Terminology.** D-012 calls this "the Phase 4 cutover" (the product-roadmap phase where S1 replaces `meta_*`); the engineering-phase counter calls it "the Phase-7 greenfield cutover." **They are the same event.** This doc uses **"the greenfield cutover."**

## 1. The premise (why gated, why docs-first)

Three facts make the cutover a multi-step program with a hard prerequisite and an irreversible tail, not a single change (full rationale: D-145):

- **`meta_*` is live.** The v1 product reads it for generation context, the validator's CRUDQ flags, preflight staleness, and the metadata UI (`primeqa/metadata/`, `primeqa/intelligence/generation.py` + `validator.py`, `primeqa/runs/preflight.py`, `primeqa/views.py`). Dropping it without a proven replacement breaks production.
- **S1 is built but not production-fired.** The sync *writer* exists (`primeqa/sync/` — `SyncEngine.run_sync`, materialize/phases/edge_specs; the worker enriches `entities` with embeddings/`semantic_text`), and the per-tenant substrate schema is **real** (§4). But **no production trigger** invokes the full Salesforce→`entities` materialization, so S1 is not yet the live metadata source.
- **The spine is dormant to the product.** S3/S4 have async job queues but no standing trigger; S6/S8 are write-only with **no UI consumer** (each deferred "to the Phase-7 cutover").

Therefore the **`meta_*` drop is the LAST, gated step** — it never precedes a proven S1-in-prod + a clean parallel-run window (§3, SEQUENCE).

## 2. The disposition map

The authoritative v1→substrate dispositions, consolidated from the logged decisions. **Each row is grounded in its cited entry — this map restates, it does not re-decide.**

| v1 module / tables | Substrate | Disposition | Source |
|---|---|---|---|
| `primeqa/metadata/` + `meta_*` (`meta_versions`, `meta_objects`, `meta_fields`, `meta_validation_rules`, `meta_flows`, `meta_triggers`, `meta_record_types`, `meta_sync_status`) | S1 `semantic/` + `sync/` | **REPLACE** — S1 is greenfield (re-sync from Salesforce, **not** a `meta_*` backfill, §3); `meta_*` dropped in one migration once S1 is the verified production data source | D-012 |
| `test_cases` | S2 `test_representation/` | **ABSORB** → claims / recipes | D-065 |
| `test_case_versions`, `requirements`, `metadata_impacts` | — | **DROP** | D-065 |
| `test_suites`, `sections`, `suite_test_cases`, `ba_reviews` | future "test catalog" + "review workflow" substrates | **MIGRATE — post-cutover** (a deliberate boundary; **not** Phase-7 work) | D-065 |
| `primeqa/intelligence/generation.py` + `validator.py` (metadata context / CRUDQ reads) | S3 `generation/` + S1 reads | **REPLACE** the data-source layer — read S1 entities/attributes, not `meta_*` | D-012 / D-003 |
| `primeqa/execution/` executor + the worker execute-stage | S4 `execution_engine/` | **REPLACE** (F1 metadata-inspection + F3 behavioral-negative live-proven; F2/F4–F7 deferred — **not** cutover-blocking, §5) | D-108 / D-133 |
| v1 failure interpretation + the run-detail surface | S6 `interpretation/` | **REPLACE** + a new **UI consumer** (the S6 read API → a run-detail view; folding S6 verdicts into GO/NO-GO) | D-111 / D-137 |
| `primeqa/intelligence/knowledge/` | S5 → top-level `primeqa/knowledge/` | **RELOCATE** — a pure package move + import updates (incl. whether `llm/feedback_rules.py` moves) | D-134 |
| S3 semantic ledger (`generation_requests` + `generation_outcomes`) | S2 provenance (`get_provenance` / `get_recipe_provenance`) | **RETIRE** at the cutover, once the typed read API ships (`test_provenance` rows already written); `llm_calls` **stays in S3 permanently** | D-074 |
| `primeqa/intelligence/llm/` (gateway + prompts), `primeqa/release/` decision engine, `data_engine`, cleanup | — | **STAY** — operational infrastructure; GO/NO-GO is the integration point for S6 verdicts (future, not a removal) | D-111 |

## 3. Migration strategy — greenfield re-sync, not backfill

**Decided (D-012): the S1 sync engine is greenfield, "not bridged."** S1 is populated by **re-syncing from live Salesforce** (`primeqa/sync/` reading the Tooling/Describe APIs), **not** by transforming `meta_*` rows into `entities`/`edges`. Rationale (D-012 + the readiness audit): a `meta_*`→S1 backfill is lossy (edges must be re-derived from org state anyway) and risks staleness; a fresh sync is authoritative.

The cutover therefore runs a **parallel-run window**:
1. Build + fire the S1-sync **production trigger** (the missing piece) so `entities`/`edges` are populated per tenant from live Salesforce, on a refresh cadence.
2. Switch the v1 read-paths (generation context, validator CRUDQ, preflight) to read S1 **behind flags**, with `meta_*` still populated + readable as the fallback (parallel run).
3. Validate parity over the window (S1-sourced reads vs `meta_*`-sourced reads agree).
4. **Only then** drop `meta_*` in one migration — D-012's "once S1 is verified as the production data source." `meta_*` carries no forward data into S1; it is retired, optionally archived for audit.

The drop is **irreversible**; every step before it is reversible (flip the flag back to `meta_*`). This ordering is the SEQUENCE's spine.

## 4. Schema-topology resolution

**The realized topology — the per-tenant substrate schema is built.** D-015 locked schema-per-tenant; D-023 (2026-04-25) deferred the structural build to a "`change_log` + `diff_window` in `public`" milestone. **That milestone is superseded:** the substrate structural foundation is realized in the **alembic tenant chain** — `entities`, `edges`, `logical_versions`, `field_details`, `picklist_value_details`, `test_claims`, `test_recipes`, `s4_execution_runs`, `s6_interpretations`, `s8_grounding_validity` all live in a **per-tenant schema** (`tenant_<id>`), reached via `get_tenant_connection` (verified: the Phase-5/6 governance tests run against them through `SemanticOrgModel`). D-015 is the realized topology, not an aspiration.

**The cutover's topology decision is therefore narrow:** v1's `public`-schema `meta_*` (+ the other v1 tables, `tenant_id`-scoped) and the per-tenant substrate schemas **coexist** through the parallel-run window; the cutover drops `public.meta_*` (+ the DROP tables, D-065) at the end, leaving the per-tenant substrate schemas as the metadata + test source. v1's non-`meta_*` tables (pipeline_runs, the MIGRATE tables, llm_calls, the gateway tables) are **not** in scope for this drop (§5).

**Dual migration systems** (per CONVENTIONS): `migrations/` (v1 raw-SQL, `public`) and `alembic/` (substrate, per-tenant). Post-cutover, `migrations/` becomes archive-eligible — but only the `meta_*`/DROP migrations are retired by the cutover; the rest of v1's `migrations/` history stays until v1's remaining tables are themselves cut over (post-cutover).

## 5. Non-goals (what the cutover does NOT do)

- **The MIGRATE tables stay out** (`test_suites` / `sections` / `suite_test_cases` / `ba_reviews`) — they move to future "test catalog" + "review workflow" substrates **post-cutover**, not in Phase 7 (D-065: "Migration execution stays post-cutover").
- **`llm_calls` does not migrate** — it stays in S3 permanently as operational observability (D-074).
- **S4 F2 / F4–F7 are not cutover-blocking** — the cutover targets v1 parity for what S4 executes today (F1 inspection + F3 behavioral-negative); positive-CRUD (F2) + UI/event/callout (F4) + capability-matching (F5) + test-data provisioning (F6) + remediation (F7) land post-cutover, gated on their own S3/S1 prerequisites (D-108 / D-133).
- **The S8 mechanics phase is not in scope** — S8 operates read-only (drift verdicts surfaced; no auto-repair); re-grounding orchestration / supersession execution stays deferred (D-144).
- **No back-conversion of v1 assets** — existing v1 `test_cases` are absorbed by re-generating as S3 claims/recipes (or kept read-only), not mechanically converted (D-065).

## 6. The gated sequence

The ordered, entry/exit-gated cutover steps — with the `meta_*` drop strictly last — live in **`SEQUENCE.md`** (authored in slice 2), which also folds every "deferred to the cutover" item across the substrate `DEFERRED_ITEMS.md` into the step it belongs to.

---

## Status

**Phase 7 opened (2026-06-03, D-145).** This SPEC is the consolidated cutover design: the disposition map (§2), the greenfield re-sync migration strategy (§3), the realized per-tenant schema topology (§4), the non-goals (§5). Next (slice 2): `SEQUENCE.md` — the gated steps + the consolidated Phase-7 work-list. The cutover *execution* (the S1-sync prod trigger, the parallel run, the substrate UI consumers, the S5 relocation, the ledger retirement, and the final `meta_*` drop) is **deferred to later phases, gated by this design**.
