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
