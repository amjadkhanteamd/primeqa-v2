# Substrate 1 — Semantic Org Model — Evolution Log

Append-only. One entry per session that made substantive changes to this substrate's docs.

---

## 2026-04-24 — Substrate skeleton created

Initial skeleton. No design decisions yet. Created SPEC.md with section placeholders, BACKGROUND.md explaining why this substrate exists, GLOSSARY.md as empty seed, OPEN_QUESTIONS.md with initial questions.

Decisions recorded in top-level DECISIONS_LOG.md: D-002 (S1 designed first).

---

## 2026-04-24 — Phase 1 design (conceptual shape complete)

Filled in SPEC.md sections 1-8 with substantive content. Decisions D-006 through D-013 recorded.

Conceptual shape locked: per-tenant authoritative model, event-sourced versioning with logical checkpoints, behavior graph with derived edges as invariants, background + on-demand sync, tiered capability model, cross-tenant three-tier policy, diff engine first-class, validation rule formula parsing in Tier 1.

Edge taxonomy aligned to PLATFORM_VISION's 5 archetypes. Caught and corrected an archetype-drift problem mid-session.

---

## 2026-04-25 — Phase 2 design (storage, data model, diff engine, query interface)

Phase 2 complete. Decisions D-014 through D-022 recorded. SPEC.md sections 5-12 filled in.

**Storage backend (D-014):** Postgres with graph-friendly design. Three commitments: edges canonical, traversal SQL-only, optimization in Postgres. Rejected: dedicated graph DB, in-process graph, hybrid in-memory, document DB.

**Tenant isolation (D-015):** Schema-per-tenant (β). Connection resolver with `SET LOCAL search_path` and `SET LOCAL app.tenant_id` for transaction-scoped settings. Defensive layers: pool checkin reset, dev-environment validation, dedicated admin entry points. Pure α (database-per-tenant) deferred to enterprise tier.

**Foundation tables (D-016):** Four canonical tables — logical_versions, entities, edges, change_log. Bitemporal versioning with version_seq (BIGINT) for fast queries plus version_name (VARCHAR) for human use. Defensive tenant_id assertion on canonical tables only. JSONB validation discipline (Pydantic at app layer, jsonb_typeof at DB layer).

**Containment vs edges (D-017):** Containment is column on detail table (authoritative); STRUCTURAL edges are derived projections auto-generated from columns. edge_category classification (STRUCTURAL/CONFIG/PERMISSION/BEHAVIOR). Layout sections are NOT entities — INCLUDES_FIELD edge with structured properties.

**Tier 1 entity types (D-018):** 10 entity types with detail tables. Detail tables don't carry tenant_id (only canonical tables do). Hot/queryable attributes are columns; sparse metadata is JSONB.

**Tier 1 edge types (D-019):** 14 edge types registered in TIER_1_EDGES constant. 8 derived from columns, 6 independently written. Properties schemas are application-layer Pydantic models.

**Permission modeling (D-020):** Grants as edges with property matrix (one edge per (Profile/PermSet, Field) with can_read/can_edit). Effective permissions materialized via materialized view. User assignments synced at higher frequency than structural metadata.

**Diff engine (D-021):** Three primitives — diff_for_entities, diff_impact, diff_window. Direction control (inbound/outbound/both). Edge category filter REQUIRED on impact diff. Raw Change objects (no interpretation layer). Deterministic ordering. Fail-loud on purged versions.

**Query interface (D-022):** Minimal contract with five primitives plus diff. Principles: version-aware access only, centralized traversal, explicit edge filtering, explicit direction, no raw SQL across boundary. `at_seq` required everywhere — no `None` default for current state.

**Refinements to prior decisions:**
- D-007 refined with version_seq + version_name dual identifiers
- D-009 refined with entity-scoped sync schedules (not org-scoped)
- D-012 refined into D-021 with full diff engine spec

**TA pushbacks accepted in Phase 2:**
- Per-type detail tables (Approach B with strict promotion rule)
- Structured version naming with separate version_seq column
- Cardinality enforcement on containment edges
- Edge category classification
- Direction control on diff queries
- Mandatory edge_categories filter on impact diff
- Change log granularity refinement (specific change_type values, changed_field_names array)
- Edge filtering baked into impact traversal CTE
- Deterministic ordering on all diff queries
- Defensive tenant_id only on canonical tables (not detail tables)
- Permission grants as edges with property matrix + materialized effective permissions
- User permission assignments at higher sync frequency

**TA pushbacks rejected/refined:**
- Defensive tenant_id on every detail table — kept on canonical tables only
- Pure α (database-per-tenant) — deferred to enterprise tier, β chosen for current customer profile

**Open for Phase 3 (operational details):**
- Refresh scheduling specifics
- Observability
- Tenant onboarding sequence
- Schema migration at scale
- change_log retention policy

**Open for Substrate 3 design:**
- Per-entity-type query helpers
- Domain shortcuts
- Bulk operations
- Caching strategy

Substrate 1 is design-complete on dimensions that block Substrate 3. Ready for Substrate 3 design when user is ready.

---

## 2026-04-25 — D-023: Implementation begins with change_log + diff_window

Phase 2 design remains locked but is reclassified as implementation-deferred. First milestone is the smallest customer-facing capability v2 cannot deliver: `change_log` table + `diff_window` primitive, shipped in `public` schema using v2 conventions (raw SQL, explicit `tenant_id`, `meta_versions.id` as version anchor, no Alembic, no GUC assertions, no entities/edges).

Structural commitments D-014 through D-022 remain design-locked but their implementation gates on pilot validation of the diff capability. If validation succeeds, they ship as designed. If it fails, `change_log` absorbs into the existing metadata module as a feature, not a substrate.

The forensic codebase report's Phase A (3–4 weeks of foundation plumbing) is bypassed. Phase D's generation-pipeline integration (`generation.py:316`) is also bypassed in this milestone — the first customer-visible surface is the release-detail "Org changes since last green run" panel, which only reads the diff output and does not touch `worker.py` or `generation_jobs.py`.

See D-023.

---

## 2026-06-02 — D-118: Tier-2 implementation begins (slice 1 — standard-field → SVS)

First Tier-2 increment, opening Phase 0 of the program roadmap (the S1-Tier-2 unblocker for Substrate-3 breadth). Note: S1's Phase-2 *implementation* build-arc — D-024's full Phase-2 ship plus the §1–§25 increments — was logged in `PHASE_2_PLAN_corrections.md`, not here; this EVOLUTION log paused at D-023 (the design era). This entry resumes it for the Tier-2 cycle.

**Slice 1 (D-118): standard picklist field → StandardValueSet `HAS_PICKLIST_VALUES` edge.** Resolves corrections-log §22 (the deferred standard-field SVS detection). No Salesforce API exposes the field→SVS link, so it is detected by **content-matching** the field's active describe values against the synced SVS value-sets — **exact set-equality, fail-closed** (0 / ambiguous → no edge). A match populates the existing `field_details.picklist_value_set_entity_id` (via the same `_value_set_external_id` marker the §10 GVS path uses, SVS-prefixed), so the existing `_edges_from_field_row` derivation emits the **already-locked** `HAS_PICKLIST_VALUES` edge — **no new edge type / column / migration** (the D-019 taxonomy and the D-024 design-lock untouched). Exact-match is high-confidence and self-healing (an admin value-edit drops the edge on the next sync); the subset/overlap tolerance is deferred. Unblocks Substrate-3's `value-claim` accepted-values grounding (D-097.2). 12 unit tests; live-sandbox edge-count probe is the slice exit-gate. On `phase-8-substrate-1-tier2`. See D-118 + corrections-log §22.

---

## 2026-06-02 — D-119: query-interface read for value-claim accepted-values (slice 2)

Slice 2 of Phase 0 — chosen over Flow entry-conditions (dormant on this sandbox: zero record-triggered flows) to **finish** what slice 1 started. Slice 1 created the Field → PicklistValueSet edge, but S3 still couldn't *read* a field's accepted values: a PicklistValueSet's PicklistValue children are linked only by the `picklist_value_details.picklist_value_set_entity_id` FK — no containment edge (the D-019 taxonomy is locked at 14) and `get_entities` filters identity columns only.

**Slice 2 (D-119): `SemanticOrgModel.get_picklist_values(pvs_id, at_seq)`.** One additive read primitive — the version-scoped PicklistValue rows of a value set, ordered by `sort_order`, mirroring the existing `get_entity_details` (D-111.1) contract (validated `at_seq`, the `_as_of` window join to `entities`, a trusted-literal table, no caller SQL). It completes the read chain Field →`get_related(HAS_PICKLIST_VALUES)`→ PicklistValueSet →`get_picklist_values`→ the accepted value set. **Not** a new edge (the lock) and **not** `query_entities` (deferred); additive under D-024's "new decisions for matters not covered" carve-out — the same path `get_entity_details` took. This confirms D-097 / D-098's "the §12 query interface exposes no detail-table data" is now fully **stale**. 5 tests (incl. as-of supersession + the field→edge→PVS→values chain); 18 query-interface + 28 semantic / S6-reader green. **Phase boundary:** this is the S1 read; the S3 value-claim *admissibility* consumption ("is V in the accepted set?") lands in Phase 2. On `phase-8-substrate-1-tier2`. See D-119.

---

## 2026-06-02 — D-120: Phase 0 closes (S1 breadth-unblock complete — verified readiness map)

Phase 0's S1-side breadth-unblock is complete. The slice-2/3 surveys found the foundation already covers more than the roadmap assumed (`get_entity_details` from D-111.1, `get_related` returning edge properties, the already-synced permission grant edges), so Phase 0 closes with a **verified readiness map** rather than more Tier-2 slices.

**Ready for Phase 2 (S3 breadth) to emit against:** `value-claim` accepted-values (built this phase — D-118 SVS edge + D-119 `get_picklist_values`); `permission-claim` capability (already groundable — `get_related` returns the synced `GRANTS_FIELD_ACCESS` / `GRANTS_OBJECT_ACCESS` edges' `can_read` / `can_edit`); `config` existence / property (`get_entities` / `get_entity_details`). Three readiness tests pin permission + config — **no new S1 primitive**, verification only.

**Deferred to S1 Phase-3** (post-lock): Flow logic interpretation (dormant on this sandbox — zero record-triggered flows); sharing / OWD / run-as permission + approval-process modeling (beyond the D-024 lock); the D-020 effective-permissions view (Phase-2-scoped); ops (refresh / observability / onboarding — not breadth-blocking). The corresponding S3 kinds (automation-effect, complex permission, integration topology) defer with them.

The branch holds D-118 + D-119 + D-120 (9 commits). **Merge exit-gate:** the live-sandbox probe of the built slices (D-118 edge-count 0→~N; D-119 `get_picklist_values` on a real SVS). On `phase-8-substrate-1-tier2`. See D-120.

---

## 2026-06-07 — D-189: bulk read primitives (the metadata-reader N+1 fix)

Three additive read primitives on `SemanticOrgModel` — `get_entity_details_bulk(entity_type, at_seq)`, `get_related_bulk(edge_types, direction, at_seq)`, `get_picklist_values_bulk(at_seq)` — each the **bulk form** of an already-shipped per-entity read (`get_entity_details` D-111.1, `get_related`, `get_picklist_values` D-119), returning a whole-org-at-a-version result in **one** round-trip. Same contract every prior primitive followed (validated `at_seq`, the `_as_of` window join to `entities`, trusted-registry table names, no caller SQL); `get_related_bulk` is `_related_select` with the near-id predicate dropped and `e.<near> AS near_id` added for grouping, keeping **both** `_as_of('e')` and `_as_of('t')` so a superseded far entity can't leak. Additive under D-024's "matters not covered" carve-out — the exact path `get_entity_details`/`get_picklist_values` took; SPEC §12's "Bulk operations: deferred" line is annotated (read-form realized), not contradicted (a general bulk-query DSL + write bulk stay deferred).

**Why now (cutover):** `metadata/s1_reader.py:hydrate_metadata_s1_reader` built the whole-org reader via per-entity round-trips (per object, per field, per VR, per picklist field) — tens of thousands of sequential queries on a real org. That killed the Step-4a parity runner (`scripts/parity_check.py`) mid-hydration and is a latent blocker for the live `cutover_read_s1` read-path (`test_management/service.py:128`). The rewrite issues ~6 bulk reads and is a **byte-for-byte drop-in** (same dataclasses, sorts, parent-prefix field-name strip, `list[str]` picklist shape, defaults), pinned by a golden-equivalence test (`_legacy_hydrate` vs the new path on a richer seeded org). This is the metadata reader only — S6/S8 per-object readers (bounded N+1) are unchanged; the per-entity primitives stay (consumers + the equivalence reference). On `main` (cutover slice; fixes a production read-path Railway deploys). See D-189.
