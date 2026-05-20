# Substrate-3 Phase 2 — Forensic Grounding Report

Read-only forensic pass over `primeqa-v2` (main @ `baaaee8`, Phase 1 design merged) to ground Substrate-3 (Generation Engine) implementation. Investigation only — no tracked-file edits. This file is the single permitted write and is untracked.

**Date:** 2026-05-20
**Method:** `git`, directory listing, `rg`/`grep`, targeted `Read`, plus two Explore sub-agents (salvage inventory; tech-stack wiring). Direct verification of all load-bearing claims (S1 query surface, S2 surface, claim-body inventory, DB convention).

---

## Findings summary

**Greenfield-vs-evolve verdict: HYBRID — greenfield S3 core, salvage/adapt infrastructure.** The S3 core engines (orchestration runtime, three-tool surface, governance / admissibility / decomposition / refusal engines, generation ledger tables) are greenfield — no S3 code exists. Infrastructure is reuse/adapt: the Anthropic LLM gateway already does tool-use and is extensible; the S2 Coordinator + `identity_hash` + body registry are the write target and ship complete; the Salesforce client, Voyage embeddings, and the schema-per-tenant session layer are reusable. One core read surface is also greenfield by necessity: **S1's designed query API is not shipped** (see below).

**First-slice readiness (configuration / positive / metadata-inspection per D-079): BLOCKED as specified.** Two blockers:
- **HARD BLOCKER — S2 ships no configuration claim bodies.** `primeqa/test_representation/models/claims/` contains only `data_behavior/`. The configuration archetype's claim kinds (`existence-claim`, `property-claim`, `metadata-relationship-claim`) have no shipped Pydantic body and are not in the registry. S3 cannot emit a configuration claim through `write_claim` until S2 ships those bodies. The only archetype with shipped claim bodies today is **data-behavior**.
- **SOFT BLOCKER (all archetypes) — S1 has no consumer query API.** The designed primitives (`TraversalSpec`, `Change`, `DiffEngine.diff_for_entities`) do not exist in code. S3's admissibility engine must read S1 by querying the materialized `entities` / `edges` / `logical_versions` tables directly (via the `primeqa/semantic/connection.py` schema-per-tenant session), coupling S3 to S1's table schema. Mitigable but greenfield.

**PRECONDITIONS drift:** one material drift — §1.4 names three query primitives S3 "has access to," but none are shipped (PRECONDITIONS itself appended the "not yet fully shipped … deferred per D-023" caveat, so the drift is soft). Everything else verifies as MATCH.

**Recommended unblock path (decision input, not a decision):** either (a) pivot the first vertical slice to **data-behavior** (claim bodies shipped; pairs with a `DataRecipeBody` or `MetadataRecipeBody`), or (b) treat "ship configuration claim bodies" as a substrate-2 prerequisite work item before the configuration-first slice. Independently, a thin S1 read-adapter over the materialized tables is needed for any archetype.

---

## 1. Repo structure

- **Monolith, not microservices.** No `primeqa-api / primeqa-generation / primeqa-retrieval / primeqa-learning / primeqa-worker` split exists (`ls primeqa-*` → no matches). Single `primeqa/` package; `Dockerfile` is one Python 3.11 + gunicorn image; Railway runs web/worker/scheduler off the same image (per CLAUDE.md). The "microservices layout" the task asked about is **not present**.
- **Substrate-1 (Semantic Org Model):** `primeqa/semantic/` (connection, derivation, edges, entity_attributes, normalization, semantic_text) + `primeqa/sync/` (engine, materialize, phases, fk_assertion, edge_specs, detail_mappers, …). `semantic/` is the model + read helpers; `sync/` is the SF→model materialization (write) path.
- **Substrate-2 (Test Representation):** `primeqa/test_representation/` — coordinator.py, identity_hash.py, canonicalization.py, canonicalizers/, authority.py, coverage.py, errors.py, models/, models_db.py. Fully shipped.
- **Substrate-3:** no code scaffold (`primeqa/test_generation`, `primeqa/generation`, `primeqa/substrate_3*` → none). Design docs only, at `docs/architecture/substrate_3_generation/`.
- **Migrations: dual system.** `migrations/*.sql` (plain idempotent SQL, 001–049) for the v2 runtime; `alembic/` (env.py + versions/, with shared vs tenant branches) for the substrates. Detail in §6.
- **Tests:** monolithic `tests/test_*.py` (integration-style against a real PG) + `tests/integration/test_representation/` (substrate-2 suite, local-PG with per-test rollback). No substrate-3 tests.
- **Other top-level:** `salesforce_domain_packs/`, `salesforce_knowledge/`, `PRIMEQA_ARCHITECTURE_SPEC_v2.2.md`, `docs/architecture/` (substrate SPECs + DECISIONS_LOG + archive).

## 2. Shipped S1 query surface

The grounding surface S3's admissibility engine must call. **Verdict: the designed query API is NOT shipped; only a direct-table read path exists.**

- **Designed primitives absent.** `rg "TraversalSpec|DiffEngine|diff_for_entities|class Change"` across `primeqa/` returns **zero matches**. PRECONDITIONS §1.4's three primitives are design-only.
- **What IS shipped in `primeqa/semantic/`:**
  - Materialized data lives in tables — `entities` (with an `attributes` JSONB column carrying sparse per-type metadata), `edges`, and `logical_versions` (the versioning table; `derivation.py` reads/writes `version_seq` and `version_name` there).
  - `entity_attributes.py` — typed sparse-attribute schemas for Object / Field / RecordType / Layout / PicklistValue / Profile (the shape of `entities.attributes`), plus `get_entity_metadata(entity_type) -> EntityTypeMetadata`. This is the closest thing to a typed read helper.
  - `derivation.py` — `edges_for_entity(entity_id, conn)` (public) and `_read_entity_row` / `_read_detail_row` (private); oriented at the derivation/materialization path, not a consumer query API. `verify_derivation_integrity(conn)` exists.
  - `edges.py` — `TIER_1_EDGES` registry (14 edge types); `entity_attributes` mirrors the same registry-driven pattern.
  - `connection.py` — the tenant session layer (schema-per-tenant; §6).
- **How a caller grounds today (the only available path):** open a tenant-scoped connection via `semantic/connection.py` (sets `search_path` to `tenant_<id>`), then SQL/SQLAlchemy-query `entities` / `edges` / `logical_versions` directly, using `entity_attributes` to interpret the JSONB. Version-awareness is available (the `logical_versions` table + `version_seq`), but there is **no clean, version-parameterized query function** — the caller assembles queries against the physical schema.
- **Implication for S3:** the admissibility read path is effectively greenfield. S3 either builds a thin read-adapter over S1's materialized tables (couples to S1 table schema) or S1 ships the query primitives first. This is the load-bearing §2 finding.

## 3. Shipped S2 surface

What S3 writes through, and the identity primitive its replay controller consumes. **Verdict: fully shipped and ready — except archetype claim-body coverage (see §8).**

- **Coordinator — `SemanticTransactionCoordinator` (coordinator.py), 22 public methods confirmed**, matching the designed 5 interface groups:
  - **Write (7):** `write_claim`, `write_recipe`, `promote_claim_to_approved`, `promote_recipe_to_approved`, `deprecate_claim`, `deprecate_recipe`, `change_recipe_priority`.
  - **Read (5):** `get_latest_claim`, `get_claim_version`, `list_active_recipes`, `get_recipe_latest`, `get_recipe_version`.
  - **Discovery (3):** `query_equivalent_claims`, `list_tests_affected_by_entity`, `list_tests_by_requirement`.
  - **Resolution (3):** `get_current_approved_claim`, `get_test_runtime_status`, `select_recipe_for_execution`.
  - **Boundary (4):** `report_run_outcome`, `get_recipe_runtime_state`, `link_requirement`, `unlink_requirement`.
- **(a) Read the taxonomy:** via the body registry — `default_registry`, `get_body_model(kind, version)`, `get_latest_body_version(kind)`. Importing `primeqa.test_representation` populates the registry (16 `(kind, body_schema_version)` pairs). Archetype grouping is exposed via per-archetype unions (`DataBehaviorClaimBody`) and the cross-archetype `ClaimBody` union.
- **(b) Persist a claim + recipe:** `coord.write_claim(...)` → `WriteClaimResult` (carries `was_noop`, `identity_hash`, status), `coord.write_recipe(...)` → `WriteRecipeResult`. Authority enforced (`actor='s3'` writes land `draft` / `generated_unapproved`; `AuthorityViolationError` on semantic divergence).
- **(c) `identity_hash` (D-059):** lives in `primeqa/test_representation/identity_hash.py` — `compute_identity_hash(...)` + `IDENTITY_HASH_VERSION` constant. Computed over the canonical serialization (`canonicalization.py` → `canonical_serialize` / `canonicalize`, with custom canonicalizers registered for state-transition + automation-effect). This is the exact primitive S3's replay controller and dedup path (`query_equivalent_claims`) consume.
- **Tables (models_db.py):** `TestClaim`, `TestRecipe`, `TestProvenance`, `TestClaimCoverage`, `TestRecipeRuntimeState`, `TestRequirementLink` — all on the project-wide `Base`.
- **References:** `IdentityBearingRef`, `PinnedRef`, `LogicalRef`, `OperationalRef`, `is_identity_bearing` — all exported.
- **Reserved, NOT shipped:** `get_provenance` / `get_recipe_provenance` / `surface_unblessed_transition` are absent from the Coordinator (consistent with SPEC §10.2 and the PRECONDITIONS §2.1 correction). S3's generation ledger cannot lean on S2 provenance reads.

## 4. PRECONDITIONS.md verification

Each assertion in `docs/architecture/substrate_3_generation/PRECONDITIONS.md` against shipped code:

- **§1.1 — 11 Tier 1 entity types (source `sync/fk_assertion.py ENTITY_ORDER`):** MATCH. (Authoritative registry present; the 11-vs-12 / FlowDefinition nuance was already reconciled at the get_provenance-correction cycle.)
- **§1.2 — 14 Tier 1 edge types (source `semantic/edges.py TIER_1_EDGES`):** MATCH. `TIER_1_EDGES` present; `REFERENCES` defined but unpopulated.
- **§1.3 — deferred items (`REFERENCES` empty; standard-field `StandardValueSet` detection deferred; no separate FlowDefinition entity):** MATCH. Consistent with shipped reality.
- **§1.4 — three query primitives (`TraversalSpec` / `Change` / `DiffEngine.diff_for_entities`), "version-aware":** **DRIFT (soft, highest-value).** The text asserts S3 "has access to three core query primitives," but none exist in code. PRECONDITIONS' own trailing caveat ("the consumer-facing query surface is not yet fully shipped … deferred per D-023") makes this a *flagged* drift rather than a false claim — but for implementation it means the S1 admissibility read path is unbuilt. Versioning *infrastructure* (`logical_versions` / `version_seq`) IS shipped; the *query API* over it is not.
- **§2.1 — Coordinator surfaces (`write_claim`, `write_recipe`, `query_equivalent_claims`, `get_latest_claim`, `list_active_recipes`; `get_provenance`/`get_recipe_provenance` reserved):** MATCH. All five consumed methods are present; the two provenance reads are correctly stated as not-shipped.
- **§2.2 — 16 registered kinds (4 data-behavior claim kinds + 5 trigger + 5 recipe + execution-environment + conditions):** MATCH. Registry holds exactly this. (The load-bearing consequence — that *only* data-behavior claim bodies ship — is accurate here but its slice impact is drawn out in §8.)
- **§2.3 — authority constraints (`AuthorityViolationError`, `was_noop` same-hash no-op, draft-on-write):** MATCH. `AuthorityViolationError` exported; `WriteClaimResult.was_noop` present; authority helpers shipped.
- **§2.4 — reference discipline (`IdentityBearingRef`, `OperationalRef`/`PinnedRef`/`LogicalRef`, C1-B rename stability):** MATCH. All exported; canonicalizers shipped.
- **§3 — downstream substrates (S4/S6/S8) absent:** MATCH (no such modules). Unverifiable only in the sense that "absent" is confirmed by absence.

Net: PRECONDITIONS is accurate against shipped S2 and the S1 registries. The single material gap is §1.4 — the S1 query API is design-only.

## 5. v1 / Architecture-4 / utility salvage inventory

(Sub-agent findings, spot-verified.)

- **v1 tool-use test-plan generation** — `primeqa/intelligence/generation.py` (`TestCaseGenerator.generate_plan`) + `primeqa/intelligence/llm/prompts/test_plan_generation.py` (`submit_test_plan` tool, strict JSON schema, escalation). Targets the v2.2 collapsed `test_case` shape, not substrate-2 bodies. **Verdict: ADAPT** — the *gateway tool-use plumbing* is directly reusable; the generation logic/shape is superseded by the S3 design. The orchestration is greenfield; the LLM dispatch underneath is salvage.
- **Architecture-4 paused system** — `docs/architecture/archive/ARCHITECTURE_4_NOTE.md` (+ `docs/architecture/ARCHITECTURE_4_SPEC.md`). Multi-turn tool-use generation, paused. **Verdict: REFERENCE ONLY** — carry-forward principles (scenario-binds-execution, state-handed-out-not-invented, strict-validation-over-silent-recovery) align with S3 Guardrails; do not port the orchestration.
- **Salesforce client** — `primeqa/integrations/sf_client.py` (httpx; REST + Tooling API; refresh-token auth with transparent re-auth; ret/backoff). **Verdict: REUSE** — callable directly for any live metadata lookup; not needed if S3 reads only the materialized S1 model.
- **Voyage embeddings (`voyage-3`, 1024-dim)** — `primeqa/intelligence/embeddings.py` (httpx; `embed_batch`). Used by enrichment, not generation. **Verdict: REUSE IF NEEDED** (e.g., retrieval/scoping); no generation coupling.
- **Anthropic Claude client + tool-use** — `primeqa/intelligence/llm/gateway.py` (`llm_call(...)` chokepoint) + `provider.py` (passes `tools` + `tool_choice` to `client.messages.create`, returns parsed `tool_input`/`tool_name`) + `router.py` (model chains) + `providers/registry.py`. **Tool-use is shipped and extensible** — a new caller declares a prompt module with `tools=[...]` + `force_tool_name` and the gateway wires it. Models: `OPUS="claude-opus-4-7"`, `SONNET="claude-sonnet-4-5-20250929"`, `HAIKU="claude-haiku-4-5-20251001"`. **Verdict: REUSE** — this is the substrate for S3's three-tool surface (`propose_semantic_intent` / `select_canonical` / `emit_outcome`). (Note: D-091's "Sonnet 4.7" default doesn't match the shipped `sonnet-4-5` string — operational config, trivially updated.)
- **Cloudinary / Playwright evidence capture** — **NOT FOUND** (Playwright appears only as a docstring reference in `models/recipes/ui_recipe.py`; Cloudinary absent). Greenfield if S3/S4 needs evidence capture (not an S3-design surface).
- **`primeqa-generation` service skeleton** — **NOT FOUND** (monolith). Any service split is greenfield scaffolding.

## 6. Tech-stack wiring confirmation

- **Flask:** `primeqa/app.py` — `create_app()` factory; blueprints registered per domain (core, metadata, test_management, execution, intelligence, release, views); CSRF + observability middleware; model imports at startup to hydrate the SQLAlchemy registry.
- **Postgres + pgvector + the DB-convention split (important):**
  - **v2 runtime** — `primeqa/db.py`: `create_engine` + `scoped_session`; tenant isolation via explicit `tenant_id` columns in a single `public` schema (application-enforced); no `SET LOCAL search_path`.
  - **Substrates (S1, S2)** — `primeqa/semantic/connection.py`: **schema-per-tenant**, `SET LOCAL search_path TO "tenant_<id>", public` + `SET LOCAL app.tenant_id = '<id>'` (transaction-scoped), with `validate_search_path_takes_effect`. The substrate-2 test conftest uses the same `search_path = "tenant_1", public` connect-event pattern.
  - **Consequence for S3:** as a substrate, S3 follows the **substrate convention** (`semantic/connection.py`-style schema-per-tenant session, `SET LOCAL search_path`), NOT the v2-runtime `tenant_id`-column convention. The two coexist; do not conflate them.
- **DB versioning conventions:** S1 uses `logical_versions` + `version_seq` (event-sourced logical versioning) and bitemporal supersession for entities (e.g., Flow). S2 uses `version_seq` per-test with effective-time supersession (D-057) and provenance rows. S3's new tables (`generation_outcomes`, `llm_calls`) should follow the substrate pattern (tenant schema; semantic ledger vs operational telemetry separation per D-074/D-087).
- **Migrations:** new substrate tables go in **Alembic** (the substrate path), specifically the **tenant branch** (`alembic/versions/` tenant head) — not the `migrations/*.sql` v2-runtime path. (Alembic two-branch: `shared@head` / `tenant@head`, per the substrate-2 setup.)
- **Anthropic chokepoint + models:** single entry `gateway.llm_call`; models in `router.py` (opus-4-7 / sonnet-4-5 / haiku-4-5); per-tenant API key from encrypted connection config. Tool-use supported (see §5).
- **CI:** **NOT FOUND** — no `.github/workflows/`. Deployment is Railway auto-deploy on push-to-main; tests are integration-style run manually/locally against a real PG. S3 would have no CI gate unless one is added.

## 7. Greenfield-vs-evolve assessment

Per-component recommendation with evidence:

- **Orchestration runtime (per-request phase pipeline)** — **GREENFIELD.** No S3 code exists; the design (D-085) is a substrate not present in v1/v2.
- **Three-tool surface (`propose_semantic_intent` / `select_canonical` / `emit_outcome`)** — **GREENFIELD on top of SALVAGE.** The tool *definitions* and substrate-side orchestration are new; the *LLM tool-use dispatch* reuses `intelligence/llm/gateway.py` + `provider.py` (already does tool-use). Build three prompt modules; reuse the gateway.
- **Governance / admissibility / decomposition / refusal engines** — **GREENFIELD.** Core S3 logic; nothing equivalent exists. Admissibility additionally depends on a new S1 read-adapter (§2).
- **Generation ledger (`generation_outcomes`, `llm_calls`)** — **GREENFIELD tables**, following S2's two-surface discipline (D-074) and the substrate DB convention (§6). `llm_calls` is conceptually adjacent to the existing `llm_usage_log` (v2 runtime) but should be substrate-scoped, not reuse the v2 table.
- **S1 admissibility read-adapter** — **GREENFIELD (forced).** S1's query API isn't shipped (§2); S3 must query materialized `entities`/`edges`/`logical_versions` via `semantic/connection.py`.
- **S2 write surface (Coordinator, body registry, `identity_hash`, references)** — **REUSE AS-IS.** Fully shipped; it is the write target and identity primitive. No adaptation needed (pending archetype claim-body coverage, §8).
- **LLM gateway + tool-use** — **REUSE.** Extensible chokepoint; production-tested.
- **Salesforce client** — **REUSE** (if live metadata reads are needed).
- **Voyage embeddings** — **REUSE IF NEEDED.**
- **DB session layer** — **ADAPT/REUSE** the `semantic/connection.py` schema-per-tenant pattern; do not reuse `db.py`'s tenant_id-column model.
- **Alembic tenant-branch migrations** — **REUSE** the mechanism for S3's tables.
- **v1 generation logic / Architecture-4** — **ADAPT (plumbing) / REFERENCE (principles).**

The verdict matches the Phase-1 expectation (core greenfield, utilities salvage), with one addition the design's PRECONDITIONS under-weighted: the **S1 read surface is also greenfield**, because the query API was never shipped.

## 8. First-slice readiness

Proposed slice: **configuration archetype, positive claims, metadata-inspection recipe** (D-079: S1-direct admissibility, metadata-inspection dominant).

- **S1 query surface this slice needs:** configuration admissibility is S1-direct — existence-claim ("does this entity exist at version V"), property-claim ("is this modeled property set as asserted"), metadata-relationship-claim ("does this edge exist"). These map to point/edge lookups against `entities` / `edges` / `logical_versions`.
  - **Shipped?** The underlying tables and `entity_attributes` helpers are shipped and directly queryable; **no query API** is. Configuration's S1-direct checks are the *simplest* to satisfy with a thin direct-table read-adapter — but that adapter does not yet exist. **Status: buildable with a small greenfield read-adapter; not shipped as-is.**
- **S2 emission surface this slice needs:** `write_claim` with a configuration-archetype claim body (existence/property/metadata-relationship), `write_recipe` with `MetadataRecipeBody`, plus `query_equivalent_claims` + `identity_hash` for dedup.
  - **`write_claim` / `write_recipe` / `query_equivalent_claims` / `identity_hash`:** SHIPPED ✓.
  - **`MetadataRecipeBody`:** SHIPPED ✓ (exported from the recipes package).
  - **Configuration claim body:** **NOT SHIPPED ❌.** `models/claims/` has only `data_behavior/`. There is no `existence-claim` / `property-claim` / `metadata-relationship-claim` Pydantic body and no registry entry. `write_claim` has nothing to accept for a configuration claim.
- **Blockers (explicit):**
  1. **HARD — configuration claim bodies absent in S2.** The specified configuration-first slice cannot emit its claim. Clear by either: (a) **ship configuration-archetype claim bodies in substrate-2** (a substrate-2 work item: 3 claim_kinds + registry entries + canonicalization), then build the S3 slice; or (b) **pivot the first slice to data-behavior**, whose claim bodies (`value-claim`, `state-transition-claim`, `automation-effect-claim`, `prohibition-claim`) and recipe bodies (`DataRecipeBody`) ship today — at the cost of D-078's heavier admissibility (validation-rule Layer 1, etc.).
  2. **SOFT — S1 read-adapter unbuilt (all archetypes).** Build a thin version-aware read over the materialized S1 tables via `semantic/connection.py`. Smallest for configuration (point/edge existence + property reads).
- **Net:** the configuration-first slice is **not buildable end-to-end as-is** — blocker (1) is the gate. The fastest path to a buildable first slice is data-behavior (S2 bodies present) **or** a small substrate-2 cycle to ship configuration claim bodies. The S1 read-adapter is required either way and is modest for configuration.

---

## Appendix — verification commands run (read-only)

- `git fetch/checkout/pull/log/status` — confirmed main @ `baaaee8`, clean.
- `ls primeqa/`, `ls primeqa/semantic/`, `ls primeqa/sync/`, `ls primeqa/test_representation/`, `ls primeqa/test_representation/models/claims/{,data_behavior/}`.
- `rg "TraversalSpec|DiffEngine|diff_for_entities|class Change" primeqa/` → none.
- `rg "^\s{4}def [a-z]" coordinator.py` → 22 public methods.
- `rg "search_path|SET LOCAL" primeqa/semantic/connection.py` → schema-per-tenant confirmed.
- `Read` of PRECONDITIONS.md and `test_representation/__init__.py`.
- Two Explore sub-agents (salvage inventory; tech-stack wiring), spot-verified against direct reads.
- "Not found" recorded explicitly for: microservices split, S1 query API, Cloudinary/Playwright, `.github/workflows/`, S3 code scaffold, configuration claim bodies.
