# PrimeQA Architecture Decisions Log

Append-only record of architectural decisions. Each decision has a monotonic ID.

**Format per entry:**

```
## D-NNN — <One-line decision>

**Date:** YYYY-MM-DD
**Substrates affected:** [S1, S2, ...]
**Status:** active | superseded-by-D-NNN | reversed

**Decision:** What we decided.

**Rationale:** Why. 2-4 sentences.

**Alternatives considered:**
- Alternative A — rejected because...
- Alternative B — rejected because...

**References:** Links to SPEC sections, external docs, prior decisions.
```

---

## D-001 — PrimeQA architecture is decomposed into 8 substrates

**Date:** 2026-04-24
**Substrates affected:** [all]
**Status:** active

**Decision:** The platform is architected as 8 substrates: Semantic Org Model, Test Representation, Generation Engine, Execution Engine, Knowledge System, Observation and Interpretation, Conversation and Control, Evolution Engine.

**Rationale:** Building toward a "Claude Code for QA" vision requires general capabilities (substrates) rather than a tool built around a specific feature. Substrates change slowly; features accumulate on top. This decomposition lets us build layer by layer without rewrites. The 8 substrates are documented in PLATFORM_VISION.md.

**Alternatives considered:**
- Feature-driven architecture — rejected because it couples substrate-level capabilities to specific feature surfaces.
- Monolithic design — rejected because a 5-year vision of this scope cannot be built without clean separation of concerns.

**References:** PLATFORM_VISION.md

---

## D-002 — Substrate 1 (Semantic Org Model) is designed first

**Date:** 2026-04-24
**Substrates affected:** [S1]
**Status:** active

**Decision:** Substrate 1 is designed before any other substrate gets a full spec. All other substrates depend on it directly or transitively.

**Rationale:** The Semantic Org Model is the foundation for everything else: generation reasons against it, execution interprets tests through it, evolution detects changes in it, interpretation explains failures via it. Designing any other substrate first would force assumptions about the org model that would either constrain S1 or require rework downstream.

**Alternatives considered:**
- Design Substrate 3 first — rejected because S3 decisions would pre-constrain S1 in ways we can't predict.
- Design multiple substrates in parallel — rejected because they share S1 as a dependency.

**References:** PLATFORM_VISION.md §"Design Order"

---

## D-003 — Architecture 4 (tool-use test plan generation) is paused

**Date:** 2026-04-24
**Substrates affected:** [S3]
**Status:** active

**Decision:** The Architecture 4 spec (v1 through v4) is paused pending Substrate 1 design. A4's design assumptions about metadata access and test structure pre-date the substrate decomposition and need to be re-examined against a proper Semantic Org Model.

**Rationale:** A4 was designed as a standalone generation architecture before we realized the broader platform vision. On review (both Claude Code sanity check and external TA critique), A4 conflates validation with execution, is narrow to Archetype A (data behavior), and doesn't fit the multi-archetype product scope. A4's useful principles (scenario binding, state discipline, strict > convenient) will likely carry forward into the eventual Generation Engine design, but the spec as written is not implemented.

**Alternatives considered:**
- Ship A4 as planned — rejected because it optimizes a narrow slice and doesn't serve the platform vision.
- Ship A4-lite — rejected because this still precedes Substrate 1 design.

**References:** archive/ARCHITECTURE_4_NOTE.md

---

## D-004 — Documentation system lives in /docs/architecture, markdown-only, session-end commits

**Date:** 2026-04-24
**Substrates affected:** [all]
**Status:** active

**Decision:** Architecture documentation lives in the primeqa-v2 repository under `docs/architecture/`. Markdown only, with Mermaid diagrams embedded where useful. Every substrate design session ends with a git commit updating the relevant doc files.

**Rationale:** Without a persistent documentation system, multi-week design work loses context across sessions.

**Alternatives considered:**
- Documentation in Confluence or external wiki — rejected because it separates docs from code.
- Documentation produced once at end of design — rejected because multi-week work without continuous documentation loses context.

**References:** README.md

---

## D-005 — Hybrid authorship: Claude produces design docs, Claude Code produces implementation docs

**Date:** 2026-04-24
**Substrates affected:** [all]
**Status:** active

**Decision:** Design documents are authored by Claude in design sessions with the user. Implementation documents are authored by Claude Code after implementation.

**Rationale:** Claude engages in architectural reasoning. Claude Code has codebase context and implementation realism. Hybrid authorship triangulates the two.

**References:** README.md §"Who produces what"

---

## D-006 — Per-tenant authoritative semantic org model

**Date:** 2026-04-24
**Substrates affected:** [S1, S5]
**Status:** active

**Decision:** Each tenant has its own authoritative semantic org model. No tenant data crosses tenant boundaries within Substrate 1. Cross-tenant learning is a structurally separate layer that consumes from many tenants but stores only abstractions, never tenant data.

**Rationale:** Per-tenant authoritative models are simpler to design, simpler for compliance, and aligned with how customers expect their org data to be handled. The trade-off — no "free" cross-tenant insights at startup — is acceptable, mitigated by Domain Packs (Substrate 5) providing prescriptive knowledge that applies broadly.

**Alternatives considered:**
- Shared model with tenant_id filtering — rejected because it makes tenant deletion fragile and complicates compliance.
- Cross-tenant learning baked into Substrate 1 — rejected because it conflates two distinct capabilities.

**References:** PLATFORM_VISION.md §"Substrate 1", substrate_1_semantic_org_model/SPEC.md

---

## D-007 — Versioning is event-sourced with logical checkpoints

**Date:** 2026-04-24
**Substrates affected:** [S1, S3, S4, S6, S8]
**Status:** active

**Decision:** Substrate 1 is versioned as an event-sourced model. Logical version markers are placed at coarse-grained boundaries (deploys, sandbox refreshes, sync milestones, manual checkpoints). Test runs, generated test cases, and execution results all bind to a specific logical version.

**Refinement (Phase 2, D-016):** Versions are identified by both `version_name` (human-readable) and `version_seq` (BIGINT, monotonic per tenant). Queries use seq for performance; humans see the name.

**Rationale:** Snapshot-every-change doesn't scale; current-mutable-state-only destroys explainability. Event sourcing with logical checkpoints gives both — low storage cost and stable read views.

**Alternatives considered:**
- Snapshot-based versioning — rejected; storage cost prohibitive.
- Single mutable model — rejected; loses explainability.

**References:** substrate_1_semantic_org_model/SPEC.md §"Versioning"

---

## D-008 — Behavior graph with derived edges; edges are invariants, not features

**Date:** 2026-04-24
**Substrates affected:** [S1]
**Status:** active

**Decision:** The semantic org model is a behavior graph, not a metadata cache. It stores derived edges computed at sync time. Edges represent invariants the system must reason about, not features the system supports.

**Rationale:** Storing only raw metadata forces every consumer to recompute derived relationships. Computing edges at sync time means impact analysis, behavior reasoning, and explainability become first-class.

**References:** substrate_1_semantic_org_model/SPEC.md §"Derived Edges"

---

## D-009 — Sync strategy: background + on-demand; entity-scoped schedules

**Date:** 2026-04-24
**Substrates affected:** [S1]
**Status:** active

**Decision:** Substrate 1 sync runs in two modes: periodic background sync keeps the model warm, and on-demand sync of specific slices runs before critical operations. Event-driven sync (Salesforce CDC, Platform Events) is deferred indefinitely.

**Refinement (Phase 2, D-020):** Sync is entity-scoped, not org-scoped. Structural metadata (Objects, Fields, Layouts, Profiles, ValidationRules, Flows) syncs at one cadence. Operational data (Users, PermissionSetAssignments) syncs at higher frequency since it changes daily.

**Rationale:** Event-driven sync sounds elegant but Salesforce CDC is incomplete and the infrastructure overhead is enormous. Background-and-on-demand achieves 90% of the benefit at 10% of the cost. Entity-scoped scheduling lets operational data stay fresh without forcing full-org syncs.

**References:** substrate_1_semantic_org_model/SPEC.md §"Sync Strategy"

---

## D-010 — Tiered modeling with explicit capability_level exposure

**Date:** 2026-04-24
**Substrates affected:** [S1, S3, S4, S6]
**Status:** active

**Decision:** The semantic org model evolves in tiers. Tier 1 covers structural facts plus validation rule formula parsing. Tier 2 covers behavior interpretation. Tier 3 covers deep semantics (Apex analysis).

The model exposes a `capability_level` (TIER_1 | TIER_2 | TIER_3) so consumers know what they can rely on.

**Rationale:** Building the entire behavior graph at once is multi-month work. Tiering lets us ship a useful Substrate 1 progressively. Validation rule formula parsing was promoted to Tier 1 because without it, Substrate 3 generates tests that randomly fail validation.

**References:** substrate_1_semantic_org_model/SPEC.md §"Tiered Capability Model"

---

## D-011 — Cross-tenant boundary three-tier policy

**Date:** 2026-04-24
**Substrates affected:** [S1, S5]
**Status:** active

**Decision:** Cross-tenant data sharing is governed by a three-tier policy:
- Tier 1 (raw data) — STRICTLY PRIVATE
- Tier 2 (derived patterns) — SAFE TO SHARE
- Tier 3 (aggregated statistics) — SAFE TO SHARE

Reconstructable tenant logic is forbidden, even when "anonymized."

**Rationale:** "Anonymized" is treacherous, especially in Salesforce where formula structure encodes business rules. The bright line "patterns and statistics yes, examples no" is enforceable.

**References:** substrate_1_semantic_org_model/SPEC.md §"Cross-Tenant Boundary"

---

## D-012 — Diff engine is first-class in Substrate 1

**Date:** 2026-04-24
**Substrates affected:** [S1, S6, S8]
**Status:** active

**Decision:** Substrate 1 includes a diff engine as a first-class subsystem.

**Refinement (Phase 2, D-021):** Three query types — entity-scoped, impact, time-window — with direction control, mandatory edge category filter, raw Change output, deterministic ordering, fail-loud on purged versions.

**Rationale:** Diff is the engine of explainability. Substrate 6 and Substrate 8 both depend on it. Without making it first-class, both substrates would reinvent it incompatibly.

**References:** substrate_1_semantic_org_model/SPEC.md §"Diff Engine"

---

## D-013 — Validation rule formula parsing is Tier 1

**Date:** 2026-04-24
**Substrates affected:** [S1, S3, S4, S6]
**Status:** active

**Decision:** Validation rule formula parsing — extracting fields referenced and conditions asserted — is Tier 1.

**Rationale:** A Tier 1 model that knows validation rules exist but doesn't know what they check is too thin to be useful.

**References:** substrate_1_semantic_org_model/SPEC.md §"Tiered Capability Model"

---

## D-014 — Storage backend: Postgres with graph-friendly design

**Date:** 2026-04-25
**Substrates affected:** [S1]
**Status:** active

**Decision:** Storage backend is PostgreSQL. The model is structured as a true graph using two canonical patterns: an `entities` table holding all nodes with type discriminators, and an `edges` table holding all derived relationships uniformly with `edge_type` discriminator and version bounds.

Three commitments are part of this decision:

1. **Edges are canonical.** Every derived relationship lives in the `edges` table. New edge types add new `edge_type` values, never new tables.
2. **Traversal is SQL-only.** Consumers never pull entities into application memory to traverse them. Recursive CTEs or stored procedures handle traversal at the database layer.
3. **Optimization stays in Postgres.** Hot queries get materialized views or denormalized columns within Postgres. No in-memory caches at the application layer.

**Rationale:** Postgres handles target queries within acceptable performance bounds. Operating it is a known quantity. The graph-friendly design (canonical edges, SQL-only traversal, in-database optimization) prevents drift toward speculative complexity.

**Alternatives considered:**
- Dedicated graph database (Neo4j, FalkorDB) — rejected; operational cost not warranted for a solo founder; talent pool small.
- In-process graph (NetworkX) — rejected; doesn't scale, doesn't handle multi-process concurrency.
- Hybrid (Postgres + in-memory graph) — rejected; cache invalidation problem, two abstractions to maintain, speculative complexity.
- Document database (MongoDB) — rejected; wrong shape for relational/graph data.

**References:** substrate_1_semantic_org_model/SPEC.md §"Storage Backend"

---

## D-015 — Schema-per-tenant isolation with safe connection resolver

**Date:** 2026-04-25
**Substrates affected:** [S1]
**Status:** active

**Decision:** Per-tenant isolation uses Postgres schemas (Option β). One database, one schema per tenant (`tenant_<integer_id>`), plus a `shared` schema for cross-tenant control-plane data.

Connection access happens through a canonical resolver, `get_tenant_connection(tenant_id)`, which:
- Takes tenant_id as explicit parameter (works in any context)
- Sets search_path via `SET LOCAL` inside a transaction (transaction-scoped, automatic reset)
- Sets `app.tenant_id` via `SET LOCAL` for defensive assertion
- Has connection pool checkin hooks that reset search_path defensively
- Validates search_path took effect in development environment
- Is the only sanctioned entry point for tenant-scoped queries

Flask `g` integration is a thin wrapper for request handlers. Workers, scripts, and admin tools use the canonical resolver directly with explicit `tenant_id`.

Admin operations have dedicated entry points: `admin_iterate_all_tenants()` for cross-tenant work, `admin_run_in_shared_schema()` for control-plane operations.

Migration framework (Alembic) is configured per-schema with `version_table_schema` set to the tenant's schema.

`SET LOCAL` works correctly under PgBouncer transaction-mode pooling, so future migration to a connection multiplexer requires no code changes.

**Rationale:** β provides genuine isolation with manageable ops. Schema-per-tenant scales to thousands of tenants on one Postgres instance. Pure α (database-per-tenant) is deferred to enterprise tier when paying customer demands it.

**Alternatives considered:**
- Option α (database-per-tenant) — deferred to enterprise tier; ops cost not warranted for current customer profile.
- Option γ (row-level isolation with tenant_id) — rejected; security risk too high.
- Option β-α hybrid built upfront — rejected; speculative complexity.

**References:** substrate_1_semantic_org_model/SPEC.md §"Connection Resolver"

---

## D-016 — Canonical foundation tables: logical_versions, entities, edges, change_log

**Date:** 2026-04-25
**Substrates affected:** [S1]
**Status:** active

**Decision:** Four canonical tables form the foundation of S1's data model:

- `logical_versions` — version markers (version_seq BIGSERIAL PK, version_name UNIQUE, version_type, parent_version_seq)
- `entities` — all nodes (UUID PK, entity_type, sf_id, sf_api_name, attributes JSONB, valid_from_seq, valid_to_seq, tenant_id assertion)
- `edges` — all derived relationships (UUID PK, source_entity_id, target_entity_id, edge_type, edge_category, properties JSONB, valid_from_seq, valid_to_seq, tenant_id assertion)
- `change_log` — event source (BIGSERIAL PK, change_type, target_table, target_id, before_state JSONB, after_state JSONB, changed_field_names TEXT[], version_seq, tenant_id assertion)

Bitemporal versioning uses `valid_from_seq` and `valid_to_seq` (BIGINT, references `logical_versions(version_seq)`). Currently-valid rows have `valid_to_seq IS NULL`.

Defensive `tenant_id` columns on canonical tables only (NOT on detail tables). Set via `current_setting('app.tenant_id')::INT` default; CHECK constraint validates equality. Acts as assertion, not access control. Detail tables don't carry it.

JSONB validation discipline: application-layer Pydantic schemas validate `attributes` and `properties` JSONB. DB-level CHECK constraints enforce `jsonb_typeof = 'object'` only. Promotion rule: if a JSONB attribute is queried, filtered, or joined, it must be promoted to a column.

**Rationale:** version_seq (integer) replaces string-based version names in queries for fast comparisons. Bitemporal columns enable point-in-time queries directly without rebuilding from event log. Defensive tenant_id on canonical tables provides isolation safety net without adding noise to detail tables.

**Alternatives considered:**
- VARCHAR version names in queries — rejected; slow string comparison, fragile sorting.
- Snapshot-based versioning — rejected; storage cost prohibitive.
- Event-sourced rebuild on every query — rejected; too slow for hot path.
- tenant_id on every detail table — rejected; noise without proportional protection.
- DB-level JSONB schema validation — rejected; brittle, defer to application layer.

**References:** substrate_1_semantic_org_model/SPEC.md §"Foundation Tables"

---

## D-017 — Containment-vs-edge rule and edge_category classification

**Date:** 2026-04-25
**Substrates affected:** [S1]
**Status:** active

**Decision:** 

**Containment rule:** Containment relationships are stored as columns on detail tables (authoritative source of truth). Edges of category STRUCTURAL/BELONGS_TO are derived projections — automatically generated from columns, never independently written. This applies to: Field → Object, RecordType → Object, ValidationRule → Object, Layout → Object, Flow → Object (trigger), User → Profile.

**Layout structure rule:** Layouts model field placement as edges (`Layout INCLUDES_FIELD Field`) with structured properties (section_name, section_order, row, column, is_required, is_readonly). Sections are not entities. Properties schema is application-layer enforced via Pydantic.

**Edge category classification:** Every edge has an `edge_category` discriminator with four values:
- STRUCTURAL — containment and object-to-object relationships
- CONFIG — layouts, picklists, layout assignments
- PERMISSION — access grants, inheritance, user assignments
- BEHAVIOR — triggers, rule applications, formula references

Categories enable filtered traversal and category-scoped queries.

**Containment cardinality:** Containment edges have `UNIQUE (source_entity_id, edge_type, valid_from_seq) WHERE edge_category = 'STRUCTURAL'` to prevent duplicate BELONGS_TO entries.

**Rationale:** Column-only fails graph traversal needs. Edge-only forces simple lookups through unnecessary joins. Hybrid (column for identity, edge for traversal) gives both. Categories enable bounded traversal during impact analysis.

**Alternatives considered:**
- Pure column-only — rejected; loses uniform traversal.
- Pure edge-only — rejected; constant tax on simple lookups.
- Sections as separate entities — rejected; over-modeling presentation artifacts.
- Layout structure in JSONB — rejected; loses queryability.

**References:** substrate_1_semantic_org_model/SPEC.md §"Containment vs Edges"

---

## D-018 — 10 Tier 1 entity types with detail tables

**Date:** 2026-04-25
**Substrates affected:** [S1]
**Status:** active

**Decision:** Tier 1 captures 10 entity types, each with a corresponding detail table for hot/queryable attributes:

1. Object → `object_details`
2. Field → `field_details`
3. RecordType → `record_type_details`
4. Layout → `layout_details`
5. ValidationRule → `validation_rule_details` + `validation_rule_field_refs` (hot reference table)
6. Flow → `flow_details` (existence + trigger only at Tier 1)
7. Profile → `profile_details`
8. PermissionSet → `permission_set_details`
9. User → `user_details`
10. PicklistValueSet → `picklist_value_details`

Detail tables follow the rule: hot/queryable attributes are columns; sparse/lightweight metadata is JSONB on the entities row. Detail tables do NOT carry `tenant_id` (only canonical tables do).

`validation_rule_field_refs` is a separate hot table powering "which validation rules reference field X" without JSONB containment queries.

`flow_details` reserves columns for Tier 2 (`parsed_logic JSONB`, `interpreted_at_capability_level`) — populated NULL at Tier 1, filled when Tier 2 capability ships.

**Rationale:** Salesforce metadata structure is stable enough to commit to specific columns for hot attributes. JSONB-only would make critical queries (find all currency fields, find all active validation rules) slow. Detail tables per type prevent pollution of any single table while keeping the entity-edge canonical structure clean.

**Alternatives considered:**
- All attributes in JSONB — rejected; queries become ugly, indexes weak.
- Single mega-table with all attributes — rejected; sparse columns, schema confusion.
- One table per Salesforce metadata type (broader than needed) — rejected; over-fragmentation.

**References:** substrate_1_semantic_org_model/SPEC.md §"Entity Detail Tables"

---

## D-019 — 14 Tier 1 edge types with category, type constraints, properties schemas

**Date:** 2026-04-25
**Substrates affected:** [S1]
**Status:** active

**Decision:** Tier 1 ships with 14 edge types, registered in a code-level constant `TIER_1_EDGES` mapping edge_type → metadata (category, source/target entity types, properties schema name, derived-from-column flag):

**STRUCTURAL (2):**
- BELONGS_TO (derived from column)
- HAS_RELATIONSHIP_TO (derived from `field_details.references_object_entity_id`)

**CONFIG (4):**
- INCLUDES_FIELD (Layout → Field; independently written; properties: section_name, section_order, row, column, is_required, is_readonly)
- ASSIGNED_TO_PROFILE_RECORDTYPE (Layout → Profile; independently written; properties: record_type_entity_id, is_default)
- CONSTRAINS_PICKLIST_VALUES (RecordType → PicklistValueSet; derived from column)
- HAS_PICKLIST_VALUES (Field → PicklistValueSet; derived from column)

**PERMISSION (5):**
- GRANTS_OBJECT_ACCESS (Profile/PermissionSet → Object; properties: can_create, can_read, can_edit, can_delete, can_view_all, can_modify_all)
- GRANTS_FIELD_ACCESS (Profile/PermissionSet → Field; properties: can_read, can_edit)
- INHERITS_PERMISSION_SET (PermissionSet → PermissionSet; for permission set groups)
- HAS_PROFILE (User → Profile; derived from column)
- HAS_PERMISSION_SET (User → PermissionSet; properties: assigned_at, assigned_by_user_entity_id, expiration_date)

**BEHAVIOR (3):**
- TRIGGERS_ON (Flow → Object; derived from column; properties: trigger_type, condition_text)
- APPLIES_TO (ValidationRule → Object; derived from column)
- REFERENCES (ValidationRule → Field; derived from `validation_rule_field_refs`; properties: reference_type, is_priorvalue, is_ischanged, is_isnew)

8 of 14 edges are derived from columns (auto-generated alongside their source row). 6 are independently written.

**Rationale:** A single registry of edge types prevents type-system drift. The derived-from-column distinction enforces D-017's rule. Properties schemas are named for application-layer Pydantic enforcement.

**References:** substrate_1_semantic_org_model/SPEC.md §"Edge Types"

---

## D-020 — Permission grants as edges with property matrix; effective permissions materialized; user assignments at higher sync frequency

**Date:** 2026-04-25
**Substrates affected:** [S1, S4]
**Status:** active

**Decision:**

**Storage:** Permission grants stored as edges with property matrix. One edge per (Profile/PermissionSet, Field) with properties capturing all access flags (can_read, can_edit). Not separate edges per access type.

**Effective permission materialization:** A materialized view `effective_field_permissions` computes per-(User, Field) effective access by aggregating Profile + assigned PermissionSets + inherited PermissionSets, taking most-permissive. Refreshed after sync or via `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

**Materialized view caveat:** Reflects "current state as of last refresh." Not version-aware. For "as-of-version-V" permission queries, consumers query underlying tables (slower) or accept the materialized view's freshness window.

**Sync frequency:** User assignments (HAS_PERMISSION_SET edges) sync at higher frequency than structural metadata. Sync is entity-scoped, not org-scoped — different entity types have different schedules.

**Rationale:** Field-level permissions for a typical org produce ~250K edges. Acceptable in indexed Postgres. Effective permission computation across inheritance chains is expensive on every query — materialization makes the hot path fast. User assignments change daily and warrant their own sync cadence.

**Alternatives considered:**
- Store only deviations from default — rejected; absence-means-default semantics cause bugs.
- Compute effective permissions on-demand — rejected; too slow for hot path.
- User assignments as Tier 2 — rejected; blocks permission test execution at Tier 1.

**References:** substrate_1_semantic_org_model/SPEC.md §"Permission Modeling"

---

## D-021 — Diff engine: three query types, direction control, mandatory edge category filter

**Date:** 2026-04-25
**Substrates affected:** [S1, S6, S8]
**Status:** active

**Decision:** The diff engine exposes three query primitives:

**diff_for_entities(entity_ids, from_seq, to_seq, traversal=None):**
- Direct changes to named entities and their edges
- Optional `traversal` (TraversalSpec) extends to neighbors via direction (inbound/outbound/both/none), max_depth, edge_categories, edge_types

**diff_impact(changed_entity_id, at_seq, direction='inbound', max_depth=3, edge_categories):**
- Returns entities affected by a change, traversing in the given direction
- `edge_categories` is REQUIRED (no None default) — caller declares intent
- Default direction is 'inbound' (who depends on this entity)

**diff_window(from_seq, to_seq, entity_types=None, change_types=None, limit=1000, offset=0):**
- All changes between two versions, paginated
- Deterministic ordering: ORDER BY version_seq, target_table, target_id, id

**Output:** Raw structured `Change` objects. No interpretation layer (Substrate 6's job). Each Change carries change_type, before_state, after_state, changed_field_names, version_seq, sync_run_id.

**change_log granularity:** change_type values are granular — entity_created, entity_field_modified, entity_attributes_modified, entity_deleted, edge_created, edge_properties_modified, edge_deleted, detail_field_modified, detail_added, detail_removed. Plus `changed_field_names TEXT[]` column with GIN index for targeted queries.

**Purged versions:** Diff queries against purged versions raise `VersionNotFoundError`. No silent fallback. (Phase 1 decision; versions not currently purged but contract is set for future.)

**Performance contract (initial targets):**
- Entity-scoped diff for 10 entities across 1000 version_seq range: <100ms
- Impact diff at depth 3 on org with 50K entities: <500ms
- Time-window diff returning 1000 changes: <200ms

**Rationale:** Three query shapes are fundamentally different (bounded entity scope vs unbounded impact traversal vs version-range scan). Direction control prevents conflating "what depends on me" with "what I depend on." Mandatory edge_categories prevents uncontrolled traversal exploding through STRUCTURAL noise.

**Alternatives considered:**
- Single unified diff query — rejected; query shapes too different.
- Optional edge category filter — rejected; uncontrolled traversal causes performance and semantic problems.
- Interpreted diff output — rejected; couples diff to interpretation logic; raw is a cleaner boundary.
- Silent fallback on purged versions — rejected; produces wrong answers.

**References:** substrate_1_semantic_org_model/SPEC.md §"Diff Engine"

---

## D-022 — Query interface: minimal contract with enforced invariants

**Date:** 2026-04-25
**Substrates affected:** [S1, S3, S4, S6, S8]
**Status:** active

**Decision:** Substrate 1 exposes a minimal query interface to consuming substrates. The interface enforces invariants now; full ergonomics emerge during Substrate 3 design.

**Principles (non-negotiable):**

1. **Version-aware access only.** Every primitive takes `at_seq` (point-in-time) or `(from_seq, to_seq)` (range). Calls without version context fail at the API boundary. No `at_seq=None` for "current" — consumers call `model.current_version_seq()` first, then pass it.

2. **Centralized edge traversal.** No consumer writes recursive CTEs. The `traverse()` primitive is the only way to walk the graph multi-hop.

3. **Explicit edge filtering.** `edge_categories` is required on traversal calls. No hidden defaults.

4. **Explicit direction.** `inbound | outbound | both` declared per call.

5. **No raw SQL across the boundary.** Consumers do not access `entities`, `edges`, `change_log`, or detail tables directly.

**Five primitives:**

```python
class SemanticOrgModel:
    def __init__(self, conn: Connection): ...
    
    def get_entities(self, entity_type, at_seq, filters=None) -> list[Entity]: ...
    def get_related(self, entity_id, edge_types, direction, at_seq) -> list[RelatedEntity]: ...
    def traverse(self, start_ids, edge_categories, direction, max_depth, at_seq, edge_types=None) -> list[TraversedEntity]: ...
    def query_entities(self, entity_type, at_seq, conditions) -> list[Entity]: ...
    
    # Diff primitives (D-021)
    def diff_for_entities(self, ...) -> DiffResult: ...
    def diff_impact(self, ...) -> ImpactResult: ...
    def diff_window(self, ...) -> list[Change]: ...
```

**What's NOT designed:** Per-entity-type helpers, domain shortcuts, query DSL, caching strategy, bulk operations. These emerge during Substrate 3 design when real query patterns surface.

**Rationale:** The interface enforces invariants (version correctness, traversal consistency, edge filter discipline, abstraction boundary). Full ergonomics designed speculatively would overfit to imagined use cases. Minimal contract now plus evolution with Substrate 3 prevents both extremes.

**Alternatives considered:**
- Full repository-pattern API — rejected; overkill at our scale; speculative.
- Direct SQL via connection — rejected; loses abstraction boundary.
- `at_seq=None` for current — rejected; hidden default contradicts version-awareness principle.

**References:** substrate_1_semantic_org_model/SPEC.md §"Query Interface"

## D-023 — Substrate 1 implementation begins with change_log + diff_window in `public` schema; D-014–D-022 structural commitments deferred pending pilot validation

**Date:** 2026-04-25
**Substrates affected:** [S1]
**Status:** active

**Decision:** Substrate 1 implementation does not begin with the structural foundation (schema-per-tenant, entities/edges, logical_versions, query interface). It begins with the smallest customer-facing capability that v2's existing `meta_*` schema cannot deliver: **change_log + diff_window**, shipped in the `public` schema using v2 conventions (raw SQL migrations, explicit tenant_id columns, integer primary keys, no GUC-based assertions, no Alembic).

The Phase 2 structural decisions remain design-locked but are reclassified as **implementation-deferred**:

- D-014 (canonical edges) — design-locked, no entities/edges built
- D-015 (schema-per-tenant) — design-locked, no schema infrastructure built
- D-016 (logical_versions, UUID target_ids, GUC tenant assertion) — design-locked; first implementation uses `meta_versions.id` as version anchor and `BIGINT` target_ids
- D-017–D-019 (containment rules, edge taxonomy) — design-locked, no graph layer
- D-020 (effective permissions materialized view) — design-locked
- D-021 (diff engine) — partially implemented at Tier 0: `diff_window` only, against `change_log` alone
- D-022 (query interface) — design-locked, no `SemanticOrgModel` class yet

The behavioural commitments from D-021 ARE in scope for D-023: deterministic ordering, raw `Change` objects, fail-loud on missing versions, paginated output.

**Sequence (revised from forensic report's 13–20 weeks):**

- Week 1: `change_log` table; shadow writes hooked into `MetadataRepository.store_*`; no readers
- Week 2: `diff_window` primitive; admin-only `GET /api/admin/diff` endpoint
- Week 3: customer-visible "Org changes since last green run" panel on release detail, behind per-tenant feature flag (`diff_panel_enabled`)

The `_build_metadata_context` swap point (`generation.py:316`) identified by the forensic report is NOT touched in this phase. Generation pipeline integration is deferred to keep `worker.py` and `generation_jobs.py` (the tightly-coupled glue) untouched until pilot validation.

**Rationale:** v2's existing capabilities cover most of S1's claimed use cases (object/field lookup, validation rule references, SOQL parsing in `TestCaseValidator`). The single capability v2 cannot deliver is diff and impact analysis. Building that on the existing schema lets pilot customers validate whether diff is the killer feature *before* paying the 3–5 month structural-foundation cost the forensic report estimated. If pilot validation succeeds, D-014–D-022 ship as designed. If it fails, `change_log` absorbs into the existing metadata module as a feature, not a substrate.

**Alternatives considered:**

- Phase A foundation work as designed (Alembic + schema-per-tenant + connection resolver + admin entry points) — rejected; 3–4 weeks of plumbing before any customer-facing capability, with the structural decisions still unvalidated against pilot needs.
- Greenfield rewrite parallel to v2 — rejected; throws away 211 commits of production hardening (audit fixes, worker-death recovery, durable run_events, LLM gateway with feedback loop, validator, domain packs, story view).
- Full Phase B implementation in `public` schema (entities + edges + change_log together) — rejected; commits to D-018/D-019's entity/edge taxonomy before a single query has surfaced from S3.

**References:** `substrate_1_semantic_org_model/SPEC.md` §11 (Diff Engine); forensic codebase report (chat history, 2026-04-25).

---

## D-024 — Substrate 1 ships full Phase 2 SPEC; D-023 superseded; design locked for 12 weeks

**Date:** 2026-04-27
**Substrates affected:** [S1, all downstream substrates]
**Status:** active
**Supersedes:** D-023 (partially — historical record retained)

**Decision:** Substrate 1 ships as a complete implementation of the Phase 2 SPEC, not a Tier 0 scaffold. All structural commitments D-014 through D-022 move from "implementation-deferred" to "in active implementation." The decision space is **locked** for 12 weeks: from 2026-04-27 through approximately 2026-07-20, the SPEC is treated as immutable. No SPEC revisions, no scope reductions, no "let's just ship a quick win" deviations during this window. At end of week 12, a full re-evaluation is permitted based on what was learned.

**Greenfield commitment (Flavour 3):**

- v2's `meta_*` tables (`meta_versions`, `meta_objects`, `meta_fields`, `meta_validation_rules`, `meta_flows`, `meta_triggers`, `meta_record_types`, `meta_sync_status`) are deprecated. They will be dropped in a single migration during Phase 4 cutover (week 8-10) once S1 is verified as the production data source.
- v2's `MetadataRepository` and `MetadataSyncEngine` are likewise deprecated. The new Substrate 1 sync engine (`primeqa/semantic/sync.py`) is greenfield, not bridged.
- The change_log scaffold from D-023 (migration 050) is reverted before Phase 0 begins.

**Survivor list from v2 (kept and reused):**

- LLM gateway (`intelligence/llm/`) — router, feedback loop, tier optimisation, prompt cache
- Executor (`execution/executor.py`) — self-contained Salesforce execution
- Static validator logic (`intelligence/validator.py`) — kept; data-source layer rewritten in Phase 4 to read S1 instead of `meta_*`
- run_events SSE stream — durable log, worker recovery
- Domain packs — customer customisation surface
- Salesforce client — auth, retry, rate limiting (reused by S1's new sync engine)
- Worker — refactored in Phase 4 to call S1 sync; structural changes minimal
- Test management, runs, releases data — kept; this is customer data and works

**Phase plan (12 weeks):**

| Phase | Weeks | Scope |
|-------|-------|-------|
| Phase 0 | 1 | Alembic introduction, schema-per-tenant scaffolding (`shared` schema, tenant provisioning, `get_tenant_connection` resolver, pool checkin hook), `logical_versions` + `entities` + `change_log` tables in per-tenant schemas |
| Phase 1 | 2-3 | `edges` table, 14 Tier 1 edge types, 10 detail tables, containment-as-column derivation logic, Pydantic validators |
| Phase 2 | 4-5 | New `primeqa/semantic/sync.py` (greenfield, reuses `SalesforceClient`), `effective_field_permissions` materialized view, `sync_run_id` correlation |
| Phase 3 | 6-7 | `SemanticOrgModel` query class (5 query primitives + 3 diff primitives), performance validation, admin diff endpoint |
| Phase 4 | 8-10 | Cutover: generation, validator, linter switched to read S1; `meta_*` dropped; worker refactored; `_build_metadata_context` rewritten on `SemanticOrgModel.get_entities()` |
| Phase 5 | 11-12 | Hardening, observability per SPEC §13, change_log retention policy, first pilot tenant onboarded |

**Schema-per-tenant decision (D-015) — affirmed.** Despite earlier pushback that row-level scoping with `FORCE ROW LEVEL SECURITY` would be cheaper, full SPEC means full SPEC. Schema-per-tenant ships from day one with the GUC-asserted CHECK constraints, the `SET LOCAL` connection resolver, and the Alembic-per-schema migration tooling. The operational cost is accepted as the cost of doing it right.

**Lock terms:**

The lock is the load-bearing commitment of D-024. During weeks 1-12:

- The SPEC is not edited except for genuine errata (e.g., a typo in a column name, a contradictory constraint discovered during implementation). Errata edits require a corresponding DECISIONS_LOG entry explaining what changed and why.
- Scope is not reduced. If a phase runs long, it runs long; we do not cut features to compress timeline.
- Pilot timing pressure does not reopen the lock. If a customer asks for a demo before week 12, they see what's built so far without altering the plan.
- New decisions (D-025 onward) may be added for matters not covered by D-014 through D-024, but they may not contradict locked decisions.

**End-of-lock review:**

At week 12, the following are evaluated:

- Did Substrate 1 ship as designed? What deviated, and why?
- Does the diff capability resonate with pilot customers, or is the killer feature elsewhere?
- Are the substrate framings (S2-S8) still the right architecture given what was learned?
- Is schema-per-tenant proving its operational cost, or should we reconsider?

If the review is favourable, S2 design begins. If not, the lock-and-build cycle is the lesson, not the architecture — we re-plan.

**Rationale:** Three earlier conversations produced three different week-1 plans within two days. This is not a healthy decision process. The lock exists to give the build phase the stability it needs. Founders iterating on architecture mid-build is the most common cause of delivery failure on platform-grade systems; D-024 buys 12 weeks of freedom from that failure mode.

**References:** SPEC.md (entire document, version locked at 2026-04-27); DECISIONS_LOG entries D-014 through D-023; forensic codebase report (chat history, 2026-04-25).

---

## D-025 — Detail tables: per-entity-version rows, hot columns + JSONB attributes split, Pydantic schemas in entity_attributes.py

**Date:** 2026-04-27
**Substrates affected:** [S1]
**Status:** active

**Context:** D-018 specified 10 detail tables but did not lock their column-level DDL or their lifecycle relative to bitemporal entity versioning. SPEC §9 reserved that for "IMPLEMENTATION.md or migration files." This decision locks the patterns that govern all 10 detail tables of Phase 1, starting with `object_details`.

**Decision:**

**(1) Detail tables are per-entity-version, joined by `entity_id`.**

A detail table has one row per entity-row in `entities`. When an entity is superseded (a new version of the same Salesforce object/field/etc creates a new entities row with new `valid_from_seq`), a new detail-table row is inserted for that new entity_id. Old detail rows linger, paired with their (now superseded) entity rows.

```sql
object_details.entity_id UUID PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE
```

The `entity_id` is the PRIMARY KEY of each detail table — one detail row per entity row. Detail tables do NOT have their own `valid_from_seq` / `valid_to_seq`. Bitemporality flows through the entities row.

Rejected: current-state-only (in-place update) — would lose explainability for "what was Object's keyPrefix at version V" without log replay. Rejected: independent bitemporal columns on detail tables — double bitemporality (entities + details) creates four-way version logic that nothing requires.

The per-entity-version model produces detail change events (`detail_field_modified`, `detail_added`, `detail_removed` from SPEC §11) cleanly: each entity supersession is a new detail row, change_log records the diff between old and new detail row contents.

**(2) Detail columns capture hot attributes only; entities.attributes JSONB carries sparse metadata.**

Per D-018: "Hot/queryable attributes are columns; sparse/lightweight metadata is JSONB on the entities row." This decision operationalizes the rule.

A column is "hot" if generation/validation/diff queries filter, group, sort, or join by it across entities. A JSONB attribute is "sparse" if it's accessed by name from a single entity but not queried across the population.

For Object specifically, the hot columns are:
- `key_prefix` (3-character prefix used by diff queries to identify standard vs custom)
- `is_custom`
- `is_queryable`, `is_createable`, `is_updateable`, `is_deletable` (generation needs to know valid CRUD operations)

Sparse attributes living in `entities.attributes` for Object:
- `is_searchable`, `is_layoutable`, `is_mergeable`, `is_replicable`
- `is_retrievable`, `is_undeletable`
- `is_feed_enabled`, `is_history_tracked`
- `plural_label`, `description`

Promotion rule: if a JSONB attribute starts being queried, filtered, or joined by application code, it is promoted to a column in a follow-up migration. Application code does not query JSONB by attribute name in hot paths.

Future detail tables follow the same split. Each detail-table migration documents which Salesforce metadata fields are hot columns vs JSONB attributes, with rationale.

**(3) Pydantic schemas for entity attributes live in `primeqa/semantic/entity_attributes.py`, one class per entity_type.**

Parallel to `primeqa/semantic/edges.py` (which holds 14 edge schemas plus a registry). The new file holds one Pydantic v2 class per entity_type:

```python
class ObjectAttributes(_EntityAttributes): ...
class FieldAttributes(_EntityAttributes): ...
# ... one per entity_type as detail tables ship
```

A `validate_entity_attributes(entity_type, attrs_dict)` helper mirrors `validate_edge_properties`: parse through Pydantic, return JSON-serializable dict ready for `entities.attributes` INSERT. Strict mode (`extra='forbid'`, `frozen=True`) for boundary discipline per D-016.

Phase 1 grows the file incrementally as detail tables ship. Phase 2 sync engine uses the validators at the write boundary.

**Rationale:** Three architectural choices that propagate across all 10 detail tables. Locking them now (rather than deciding ad-hoc per detail table) keeps the 10 migrations consistent and makes the cross-tenant pattern reviewable. None of these contradict D-014–D-024; they fill in a gap explicitly left by SPEC §9.

**Alternatives considered and rejected:**

- Detail tables as views over entities — rejected; SPEC §5.2 commitment 1 ("edges canonical, traversal SQL-only") implies hot data is materially stored, not computed at query time.
- Single `details` table with TEXT discriminator — rejected; D-018's per-type table choice is explicitly to prevent column pollution.
- All attributes in JSONB, no detail tables — rejected by D-018.
- Pydantic schemas inline in each migration — rejected; Pydantic schemas should be importable by the sync engine and query layer, not buried in migration files.

**References:** SPEC §6.5/§9 (detail tables), D-016 (JSONB validation discipline), D-018 (10 detail tables), D-019 (edge registry pattern this mirrors).

---

---

## D-026 — Hot reference table pattern (Phase 1)

**Date:** 2026-04-28
**Status:** Active
**Phase:** 1

When a 1:many relationship needs first-class queryable representation per row (rather than being collapsed into a JSONB array on a parent detail row), use a hot reference table — a junction table outside the D-025 detail-table family.

**Pattern characteristics:**

- Composite primary key naming the relationship dimensions (no surrogate UUID id)
- Asymmetric `ON DELETE` behavior: CASCADE on the "rule" side (the entity whose deletion logically removes all its references), no CASCADE on the "referenced" side (deleting a referenced entity while a rule still points to it should fail loudly so the rule can be fixed first)
- DB CHECK constraints when the table has an enum-typed column (mirroring edge property schema enums where applicable)
- One reverse-lookup index for impact analysis ("which rules reference X")
- No Pydantic schema; row construction handled by the sync engine using DB constraints for validation

**When to use:**

- Cardinality is genuinely 1:many or many:many between entity types
- Each row needs to be queryable and indexable individually
- The relationship is part of an entity's lifecycle (CASCADE makes sense on at least one side)

**When NOT to use:**

- 1:1 cardinality (a column on the relevant detail table is correct — see HAS_PICKLIST_VALUES sourced from `field_details.picklist_value_set_entity_id`)
- The relationship doesn't need per-row queryability (an array in JSONB attributes may be sufficient)

**Phase 1 instances:**

- `validation_rule_field_refs` — REFERENCES edge source (validation_rule → field, with `reference_type` discriminator)
- `record_type_picklist_value_grants` — CONSTRAINS_PICKLIST_VALUES edge source (record_type → picklist_value)

Both implemented per migrations `20260427_0120` and `20260427_0140` respectively.

**Related decisions:** D-018 (named these tables when cataloging Phase 1 schema), D-019 (REFERENCES and CONSTRAINS_PICKLIST_VALUES edge types these tables source).

---

## D-027 — Tier 2 reservation pattern (Phase 1)

**Date:** 2026-04-28
**Status:** Active
**Phase:** 1

When a detail table will be populated by both Tier 1 and Tier 2 sync code, reserve Tier 2 columns nullable in Tier 1 schema rather than waiting for a Tier 2 migration. Tier 1 sync writes NULL or 'tier_1' in the capability_level column; Tier 2 sync upgrades the same row in place.

**Schema shape (from `flow_details`, the only Phase 1 detail table using this pattern):**

```sql
parsed_logic JSONB,                          -- Tier 2 populates
interpreted_at_capability_level VARCHAR(10), -- 'tier_1' or 'tier_2'
CONSTRAINT _capability_level_known CHECK (
    interpreted_at_capability_level IS NULL
    OR interpreted_at_capability_level IN ('tier_1', 'tier_2')
)
```

**Why reserve nullable now:**

- Avoids a future schema migration that would lock production tables for the column add
- Lets Tier 2 sync code be deployed without coordinating a schema change
- Tier 1 testing exercises the full schema today (proven: smoke tests in `flow_details` migration write to `parsed_logic` and validate the CHECK constraint on `interpreted_at_capability_level`, end-to-end before any Tier 2 sync code exists)

**When to apply:**

- The detail table represents an entity whose capability tier will increase (parsing depth, derivation depth, etc.)
- The Tier 2 columns can reasonably be nullable (Tier 1 rows have NULL, Tier 2 rows have populated values)
- The CHECK enum on capability_level enforces the valid set at DB level

**When not to apply:**

- The detail table is fully Tier 1 (no Tier 2 plans for that entity type)
- Tier 2 would require fundamentally different relationships (new FKs, new tables) that can't be reserved nullable

**Phase 1 reference:** SPEC §9 explicitly calls this out for `flow_details`. Future detail tables should consider this pattern when their capability tier is expected to grow.

**Related decisions:** D-024 (12-week design lock — Tier 2 work is explicitly inside Phase 1's scope through reservation, not deferred to a separate phase).

---

## D-028 — `validate_edge_properties` JSON serialization behavior (Phase 1)

**Date:** 2026-04-28
**Status:** Active
**Phase:** 1

`validate_edge_properties(edge_type, properties)` from `primeqa/semantic/edges.py` returns properties in their JSONB-serialized form, not as native Python objects.

**Implications for callers:**

- UUID property values come back as strings, not `uuid.UUID` instances. This is correct because the dict is destined for a JSONB column, where strings are the canonical representation.
- Propertyless edges raise `ValueError` (not `pydantic.ValidationError`) when given non-empty properties. This is a deliberate distinction: propertyless edges don't have a Pydantic schema to violate, so the rejection happens at a different layer.

**Why this matters:**

- Test code asserting on returned properties must compare via `str()` or expect the serialized form, not the input form (caught during 10A test development).
- Sync engine code constructing edge dicts must accept that the validated dict is "as good as written to DB" — no further serialization step needed before INSERT.
- Phase 2 sync engine code should not assume `validate_edge_properties` performs identity transformation; it serializes.

**Caught during:** Test suite development (Phase 1 step 10A). Initial test assertions failed because they compared a returned UUID-shaped string against a `uuid.UUID` input.

**Related modules:** `primeqa/semantic/edges.py` (`validate_edge_properties`), `primeqa/semantic/derivation.py` (consumer that relies on this serialization for `INSERT INTO edges ... CAST(:p AS JSONB)`).

---

Format note: Major architectural decisions get full entries with context, alternatives, and consequences. Routine mechanical decisions (column additions, naming, etc.) get concise entries.

---

## D-029 — Generation/execution split

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2 (foundational; applies to all subsequent phases)

**Context.** PrimeQA's QA workflow has two structurally different metadata needs. When a QA writes test cases from a JIRA ticket, they reference *requirements* — what the application should do. When they execute those tests, the org's actual current metadata becomes relevant. Conflating these into a single "always-current mirror of all connected orgs" produces the heavy per-connection metadata cache architecture that Provar and similar tools struggle with operationally.

**Decision.** The normative semantic model (Substrate 1) serves test generation and is org-agnostic at the conceptual level. Per-org metadata access for test execution is a separate concern, deferred to Substrate 3 / 4 work. Phase 2 builds only the generation-side substrate.

**Alternatives considered.**
- Per-connection metadata caches (Provar-style): rejected as architecturally heavy, validated as painful by Provar's own published optimization work.
- Continuous sync from all connected orgs into a unified model: rejected because mixing metadata from production and sandboxes corrupts the "what is true" question.

**Consequences.** Phase 2 sync is much lighter than originally framed. Per-org execution-time concerns (describe API at runtime, locator resolution, etc.) become Substrate 3 work. The substrate is positioned to serve both test authoring (Substrate 2) and failure attribution (Substrate 4) without org-binding.

**Cross-references.** Product doc §4.1, §4.2.

---

## D-030 — Sync is per-(org, run); model is shared across orgs

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2

**Context.** Customers connect multiple orgs to a tenant: one production, multiple sandboxes, scratch orgs. Question: which org does sync pull from, and how does the model represent metadata that may differ across them?

**Decision.** Phase 2 supports syncing from any registered org into the canonical normative model. Initial seed sync (typically from a customer-recommended base org during onboarding) populates the model. Subsequent syncs from other registered orgs (developer sandboxes, UAT) update the model in place. The model is a single canonical picture; per-entity provenance (`last_synced_from_org_id`, see D-040) tracks which org each entity was most recently sourced from.

**Alternatives considered.**
- Single-source-of-truth org with `is_seed_source` flag: rejected because real workflows require multiple orgs to update the model over time (developer testing against sandbox, QA lead testing against UAT).
- Per-org metadata storage with `org_id` on every entity: rejected because it creates duplication across orgs and complicates the single-canonical-truth principle. Multi-org diffing is out of scope per Phase 2 boundaries.
- Model as union of all seen orgs: rejected because it creates frankenstate metadata that doesn't represent any actual reality.

**Consequences.** Single-release-context model. The model represents whichever org most recently synced. Multi-release support (parallel branches of metadata) is explicitly deferred (D-041). The `release_label` column on `connected_orgs` is the future-extensibility hook.

**Cross-references.** Product doc §4.2; D-040; D-041.

---

## D-031 — `entity_origin` column on entities

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2

**Decision.** Add `entity_origin VARCHAR(20) NOT NULL DEFAULT 'sync'` to `entities`, with CHECK constraint allowing `'sync' | 'requirements' | 'manual_curation'`. Phase 2 only writes `'sync'`. Other values are reserved for Phase 3+ paths (requirements-doc ingestion, manual curation UI).

**Rationale.** Forward-compatibility hook prevents schema migration when Phase 3 adds non-sync sources of truth.

---

## D-032 — Hash-based diffing on `entities.last_seed_hash`

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2

**Decision.** Add `last_seed_hash VARCHAR(64)` to `entities` storing SHA-256 hex of the entity's normalized content (per D-035). On subsequent syncs, compare the current hash to the stored hash to detect changes. CHECK constraint: `last_seed_hash` is non-NULL only when `entity_origin = 'sync'`.

**Rationale.** Hash-based diffing is robust against Salesforce metadata oddities (presentation reordering, internal ID drift) provided normalization is correct. Constraining to sync-sourced entities keeps the column meaningful — requirements-sourced and manually-curated entities have no remote authority to compare against.

---

## D-033 — On-demand sync only in Phase 2

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2

**Decision.** Sync is user-triggered. No cron, no Salesforce streaming API, no polling. A future phase may add scheduled-fallback syncs once the on-demand path is operationally solid.

**Rationale.** Continuous sync compounds cost and complexity (Salesforce API quota, sync conflict resolution, partial-failure handling) for a feature whose value is unclear pre-customer-validation. On-demand sync covers the real workflow: developer or QA syncs the org they're about to test against, before testing.

---

## D-034 — OAuth tokens stored plaintext in Phase 2; encryption is Phase 5

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2 (with Phase 5 commitment)

**Context.** `connected_orgs.oauth_access_token` and `oauth_refresh_token` are sensitive credentials. Encryption-at-rest is standard practice. Question: do we encrypt now or defer?

**Decision.** Tokens are stored plaintext in Phase 2 with `# TODO Phase 5: encrypt at rest` comments at storage boundaries. Encryption-at-rest is committed as Phase 5 hardening work. **No production org may be connected until Phase 5 ships.** Phases 2-4 testing is sandbox-only.

**Alternatives considered.**
- Encrypt now (AES + Railway env var key): rejected as premature. Encryption strategy depends on key rotation, key recovery, multi-tenant key isolation, and integration with other secrets infrastructure (audit logging, request signing) that doesn't exist yet. Building the encryption layer now means rebuilding it in Phase 5 once those dependencies exist.
- Defer to Phase 6+: rejected because Phase 5 is the natural hardening phase and we want customer production connection gated on the encryption work landing.

**Consequences.** Constrains Phases 2-4 to sandbox testing. Makes Phase 5 hardening work concretely scoped (encryption is part of it). Trade-off accepted because we have no production traffic and no real production tokens at risk during Phases 2-4.

**Cross-references.** Product doc §6.4 (v1 supports sandbox connections only); §6.5 (production connection in v1+ is post-v1 work).

---

## D-035 — Mandatory normalization before hashing

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2

**Decision.** Per-entity-type `normalize_*` functions in `primeqa/semantic/normalization.py` produce canonical, stable dict representations of each entity before hashing. Functions sort collections without semantic order (layout sections by position, picklist values by api_name), strip Salesforce-internal IDs that change without semantic meaning, fix attribute ordering, and drop volatile timestamps.

**Rationale.** Without normalization, Salesforce describe API output ordering and serialization variance produce phantom hash changes on every sync. The hash-based diffing strategy (D-032) is broken in practice without this discipline. Normalization functions are independently unit-tested with table-driven cases covering known phantom-change scenarios.

**Cross-references.** D-032.

---

## D-036 — Sync atomicity: all-or-nothing for structural; partial-success for AI primitives

**Status:** Locked
**Date:** 2026-04-30
**Phase:** 2

**Context.** A sync run touches many entities: structural writes (entities, detail rows, edges, derivations) and AI primitive writes (embeddings, summaries). Question: does the whole run commit-or-rollback together, or are the layers separable?

**Decision.** Two-phase atomicity. Structural sync is one Postgres transaction — all-or-nothing. The model never enters an inconsistent structural state. AI primitive generation is a second transaction that begins after structural commit. If AI primitive generation fails (LLM rate limit, embedding API down, individual summary failure), the structural commit holds and `sync_runs.status` is set to `'partial_success'` with `summaries_failed` counter populated. A subsequent sync run will fill in missing AI primitives.

**Alternatives considered.**
- Strict all-or-nothing across both layers: rejected because LLM API failures are common enough that strict atomicity would frequently roll back valid structural work for transient AI issues.
- Independent layers, no atomicity: rejected because structural writes need transactional integrity (entity + detail row + edges must commit together).

**Consequences.** AI primitive failures degrade gracefully (entities still queryable structurally, just without retrieval enrichment). Substrate 4 attribution that depends on summaries can fall back to raw error visibility for entities whose summaries failed. The `partial_success` status surfaces the issue without blocking the workflow.

**Cross-references.** D-048; product rule 5 (graceful fallback over hallucination).

---

## D-037 — Strict entity-type ordering during sync

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2

**Decision.** Sync writes entities in dependency order so each entity's parents exist before it does:

```
Object
  → PicklistValueSet
    → PicklistValue
    → Field, RecordType, Layout, ValidationRule
  → Profile
  → PermissionSet
  → User
  → Flow
```

`derivation.supersede_and_derive` is called per entity after its detail row is written. Hot reference table rows are written between the parent entity's detail row and `supersede_and_derive`.

**Rationale.** FK dependencies require parents-first ordering. PicklistValueSet must precede Field because picklist Fields reference PVS; PicklistValue must follow PVS for its own FK. Without strict ordering, derivation produces incomplete edges or sync transactions fail on FK violations.

---

## D-038 — Withdrawn

**Status:** Withdrawn 2026-04-30
**Original decision:** `is_seed_source` change protection trigger on `connected_orgs`.

The `is_seed_source` flag itself was removed when sync architecture simplified to "any registered org can sync into the model" (D-030). The protective trigger has no invariant left to protect and was removed before any implementation work. ID retired; not reused.

---

## D-039 — Single `mv_active_graph` materialized view

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2

**Context.** Substrate 2 (test generation) and Substrate 4 (attribution) read from a denormalized projection of the active model. Question: one matview covering entities + edges + AI primitives, or separate matviews per concern?

**Decision.** Single matview `mv_active_graph`. Includes active entities (`valid_to_seq IS NULL`), active edges, hot detail-table columns (LEFT JOIN per entity type), full `attributes` JSONB, AI primitive columns (semantic_text, embedding, embedding_model). Excludes superseded rows, raw bitemporal columns, hot reference table rows (accessed via edges they produce), change log, raw OAuth tokens. Refreshed via `REFRESH MATERIALIZED VIEW CONCURRENTLY` at the end of each successful sync run; concurrent refresh requires the unique index on `entity_id`.

**Alternatives considered.**
- Separate `mv_active_entities` and `mv_active_edges`: rejected as premature decomposition. Single matview is simpler to operate and refresh; refactor only if Phase 3 query patterns reveal a real need.
- Lean matview without JSONB: rejected because attribute-filter queries are common (e.g., "find all required Fields") and forcing JOINs back to `entities` defeats the matview's purpose.

**Consequences.** All consumers see the same shape. Concurrent refresh keeps reads available during sync. Schema simplicity at the cost of some duplication (the JSONB attributes appear both in `entities` and the matview).

---

## D-040 — Per-entity sync provenance via `last_synced_from_org_id`

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2

**Decision.** Add `last_synced_from_org_id UUID REFERENCES connected_orgs(id)` to `entities`, with CHECK constraint requiring `entity_origin = 'sync'` for non-NULL values. Updated by sync to reflect the org each entity was most recently sourced from.

**Rationale.** With multiple orgs syncing into the canonical model over time (D-030), per-entity provenance tells you what the model represents right now. A QA can answer "this test asserts Account.Industry exists; the model says it was last synced from UAT 30 minutes ago." Substrate 4 attribution uses this for confidence indication.

**Cross-references.** D-030.

---

## D-041 — Multi-release deferred indefinitely

**Status:** Locked
**Date:** 2026-04-28
**Phase:** 2 (deferral; future implementation undated)

**Context.** Enterprise customers run multiple Salesforce releases simultaneously: production at release N, UAT at N+1, dev sandboxes at various N+1/N+2 feature branches. A release-aware metadata model would represent these as parallel branches with merge testing capability.

**Decision.** Phase 2 (and the path to v1) ships single-release-context only. The model represents whichever release was most recently synced. Customers with multi-release setups can use Phase 2 with the limitation that the model represents one release at a time. True multi-release support — parallel metadata branches, branch-aware queries, merge testing — is deferred until a real customer drives the requirements.

**Alternatives considered.**
- Add `release_id` to every entity now (Option A from earlier discussion): rejected as substantial schema expansion for a feature with no current customer driving it. Risk of designing the wrong abstraction without real workflow data.
- Per-release schema (Option B): rejected as duplicating shared concepts across orgs and complicating cross-release queries.
- Releases as edge-association (Option C): rejected as awkward fit for non-linear branching reality.

**Consequences.** `connected_orgs.release_label VARCHAR(100)` (free-form text) is the future-extensibility hook — customers can tag their topology for visibility, but Phase 2 does not consume it for logic. When a customer drives multi-release work, the seam exists for retrofit. Decision revisitable when concrete customer requirements arrive.

**Cross-references.** Product doc §5.4, §6.5.

---

## D-042 — pgvector for embedding storage

**Status:** Locked
**Date:** 2026-04-30
**Phase:** 2

**Decision.** Use the `pgvector` Postgres extension for embedding column storage and similarity search. Embedding columns typed `VECTOR(1536)`. Indexed via `ivfflat` with `vector_cosine_ops` for cosine similarity search.

**Rationale.** pgvector is mature, available on Railway's Postgres, and keeps the entire data model in one database (no separate vector store like Pinecone or Qdrant). Single-database simplicity matters disproportionately for a small team — fewer operational concerns, fewer integration boundaries, transactional consistency between metadata and embeddings.

**Alternatives considered.**
- Standalone vector store (Pinecone, Qdrant, Weaviate): rejected as operational overhead. Embedding-database consistency would require dual-write patterns.
- pgvector with `hnsw` index: deferred (D-related O-8). hnsw is faster at query time but has more parameters to tune. Switch later if query latency requires it; migration is straightforward.

**Consequences.** Embeddings co-located with entities. Queries can JOIN structural data and similarity search in one statement. ivfflat `lists = 100` is conservative for tenants up to ~100K entities.

---

## D-043 — OpenAI `text-embedding-3-small` for entity embeddings

**Status:** Locked
**Date:** 2026-04-30
**Phase:** 2

**Decision.** Use OpenAI's `text-embedding-3-small` model for all entity embeddings. 1536-dimensional output. Tracked per-row in `entities.embedding_model` as `'openai/text-embedding-3-small'` for forward-compat.

**Alternatives considered.**
- `text-embedding-3-large` (3072-dim): rejected on cost/storage. ~6x cost per call, doubles storage, marginal quality difference for structured Salesforce metadata text.
- Voyage AI `voyage-3`: competitive quality, less ecosystem support. No compelling reason to take on the integration.
- Self-hosted (sentence-transformers): rejected as operational overhead disproportionate to a small team. Quality difference vs. text-embedding-3-small is meaningful for retrieval.

**Consequences.** Embedding cost is essentially zero at our expected scale (~$0.50/sync for 50K entities). Vendor lock-in to OpenAI for embeddings is real but mitigated by the `embedding_model` column — a future model swap is a re-embedding migration, not a schema change.

---

## D-044 — Anthropic Claude Haiku 4.5 for plain-English summaries

**Status:** Locked
**Date:** 2026-04-30
**Phase:** 2

**Decision.** Generate plain-English summaries of validation rule formulas and flow logic via Anthropic Claude Haiku 4.5. Summary target length ~100-150 tokens. Stored on detail tables (per D-045) with `summary_model VARCHAR(50)` capturing the model identifier and `summary_prompt_version VARCHAR(20)` capturing the prompt version.

**Alternatives considered.**
- Claude Sonnet 4.6: rejected on cost grounds for this volume. Summaries are short and structurally bounded; quality difference unlikely to justify Sonnet's cost premium. We can selectively upgrade specific failing cases to Sonnet later if Haiku quality proves insufficient.
- OpenAI GPT-4: comparable quality, no compelling reason to add a third LLM provider. Sticking with Anthropic for all generative work simplifies key management and prompt versioning.

**Consequences.** Cost is bookable per customer (~$30-50 for initial seed sync of a 50K-entity org, ~$1-5 per delta sync). Re-summarization on prompt-version change is a separate manual operation, not part of normal sync.

---

## D-045 — Summaries stored as columns on detail tables, not a separate `entity_interpretations` table

**Status:** Locked
**Date:** 2026-04-30
**Phase:** 2

**Decision.** Plain-English summaries live on `validation_rule_details.plain_english_summary` and `flow_details.plain_english_summary`, with metadata columns (`summary_model`, `summary_prompt_version`, `summary_generated_at`) alongside. No separate `entity_interpretations` table.

**Rationale.** Earlier design considered a polymorphic `entity_interpretations` table with multiple `interpretation_type` values per entity, confidence scores, and rich structured semantic extraction. Rejected as over-engineered: only two entity types contain natural-language semantics that warrant summarization, and one summary per entity is the expected cardinality. The simpler column-per-detail-table approach matches what we actually need.

**Consequences.** When future entity types need summarization (Phase 3+ might surface this for Apex or layout description text), they get a column on their own detail table, not a row in a generic interpretations table. Slightly more migration work per addition; significantly simpler queries.

---

## D-046 — AI for translation, not invention

**Status:** Locked
**Date:** 2026-04-30
**Phase:** 2 (foundational principle; applies to all AI usage in PrimeQA)

**Context.** Throughout the AI-first design discussion, the question of LLM hallucination came up repeatedly. A QA who trusts a hallucinated explanation makes wrong release decisions; this is the most expensive failure mode (product doc §5.1). Architectural defense matters more than monitoring.

**Decision.** AI is used to translate structured technical context into natural-language explanations and to retrieve semantically relevant entities. AI does not invent structural facts about the org. Specifically:

- Object existence, field types, relationships, picklist values, validation rule formulas, flow definitions: come from Salesforce describe and tooling APIs, parsed deterministically, written through Pydantic-validated boundaries. The LLM does not get to invent or alter these.
- Summaries: bounded LLM outputs grounded in the structural source content. The summary is not the source of truth; the underlying formula or flow definition remains the truth. Summary failures degrade gracefully (NULL summary, falls back to raw content).
- Future LLM uses (Substrate 2 generation, Substrate 4 explanation): outputs constrained via schema-enforced LLM calls. LLM cannot reference entities that don't exist in the model. Schema validation rejects hallucinated entity references at the boundary.

**Consequences.** This principle shapes every AI integration in the product. It is the architectural defense against confident wrongness. It also constrains what AI does — we deliberately decline to ask AI questions whose answers it would have to invent.

**Cross-references.** Product doc §4.7 rule 7 ("AI for translation, not invention"); product doc §5.1 (most expensive failure mode); D-048.

---

## D-047 — Re-embed and re-summarize only on hash change

**Status:** Locked
**Date:** 2026-04-30
**Phase:** 2

**Decision.** Embeddings and summaries are regenerated only when an entity's `last_seed_hash` differs from its prior value. Unchanged entities skip both operations.

**Rationale.** Cost discipline. Initial seed sync of a 50K-entity org generates all embeddings and summaries once. Subsequent delta syncs touch only the small subset of entities that changed. This keeps per-sync cost bounded (~$1-5) regardless of org size.

**Consequences.** Cross-entity context changes (e.g., parent Object's label changes, affecting Field's semantic context) do not trigger re-embedding of children. Acceptable staleness in exchange for cost predictability. If retrieval quality suffers from this, a manual full-re-embed operation can be run (separate from sync). Prompt version changes that warrant re-summarization are a manual operation, not automatic.

---

## D-048 — Graceful fallback for AI primitive failures

**Status:** Locked
**Date:** 2026-04-30
**Phase:** 2

**Decision.** When AI primitive generation fails for an entity (LLM rate limit, embedding API timeout, individual summary returns malformed output, etc.), the failure is logged but does not crash sync. The entity is committed structurally (per D-036's two-phase atomicity) without its embedding or summary. The sync run is marked `partial_success` with the relevant counter (`embeddings_failed` or `summaries_failed`) incremented. A subsequent sync run will re-attempt the failed primitive.

**Rationale.** The architectural posture of graceful fallback over hallucination (product rule 5) requires that AI failures degrade rather than block. A failed summary is recoverable; a hallucinated summary is a trust-eroding event in the wild.

**Consequences.** Substrate 4 attribution must handle entities with NULL summaries by falling back to raw content. The UI must surface entities with missing AI primitives clearly (not silently treat them as fully-enriched). Trade-off: some entities take multiple sync runs to fully enrich; acceptable cost for the architectural defense.

**Cross-references.** D-036; D-046; product rule 5.

---

## D-049 — Embedding provider: Voyage AI `voyage-3` via raw httpx, 1024 dim

**Status:** Locked
**Date:** 2026-05-14
**Phase:** 2 (§23 enrichment-worker cycle)
**Supersedes:** the informal "D-043" — OpenAI `text-embedding-3-small` @ 1536 dim, never formally ratified, only ever a parenthetical "currently…" in `PRIMEQA_PRODUCT_DEFINITION.md`.

**Decision.** The §23 enrichment worker generates embeddings via the Voyage AI API — model `voyage-3`, 1024 dimensions — over raw `httpx` HTTP calls. No vendor SDK; the client (`primeqa/intelligence/embeddings.py`) matches the `sf_client.py` pattern. The API key is read from the `VOYAGE_API_KEY` environment variable. `entities.embedding` and the detail-table `summary_embedding` columns are sized `vector(1024)` (migration `20260514_0010`, down from the 1536 the never-ratified OpenAI choice implied).

**Rationale.**

1. **Environment portability — the deciding factor.** A local `sentence-transformers` model (the highest-quality, zero-API-cost option) is not installable on the Intel-macOS dev box: PyTorch dropped x86_64-macOS wheels after `torch 2.2.2`, and `torch 2.2.2` is itself incompatible with the project's NumPy 2.x and `transformers` 5.x. An HTTP embedding API runs *identically* on the Intel-Mac dev box, on Linux/Railway production, and in CI — preserving the "what you verify locally is what ships" parity (CLAUDE.md working agreement) that a local PyTorch model would break on this hardware.

2. **Quality.** `voyage-3` is competitive with state-of-the-art retrieval embedding models on public benchmarks, and Voyage is Anthropic's recommended embedding partner — a coherent pairing with the Anthropic LLM gateway already in production.

3. **Operational simplicity.** No ~1 GB model weights to ship, no PyTorch dependency, no ~2 GB worker memory footprint, no GPU question. The worker container stays small. Raw `httpx` (already a dependency) — no SDK to track.

4. **Cost.** Negligible at the scale we expect — a ~5,900-entity sandbox sync embeds for roughly $0.02; a 50K-entity org for ~$1-2 per full sync. Embeddings remain "sub-cent per entity."

**Alternatives considered.**

- *OpenAI `text-embedding-3-small`* (the informal D-043) — needs a vendor account unrelated to the Anthropic ecosystem, and the project has no per-tenant LLM-credential storage to hold the key.
- *Local `sentence-transformers` + Snowflake `arctic-embed-l-v2.0`* — best quality, zero external API; rejected because PyTorch wheels are unavailable on the Intel-macOS dev environment, which would break local↔prod parity.
- *`model2vec`* (torch-free local, static embeddings) — installs cleanly everywhere, but static embeddings are a meaningful retrieval-quality step down and the dimension drops to ~256.
- *Pinning an old PyTorch stack* (`torch 2.2.2` + `numpy<2` + `transformers<5`) — would hold production back to a 2024-era dependency set to accommodate one Intel dev box; the wrong trade.

**Consequences.** A `VOYAGE_API_KEY` must be provisioned in every environment that runs the enrichment worker (documented in `.env.example`). Embedding calls don't flow through the message gateway's `llm_call()`, so `limits.record_embedding_usage()` logs one `llm_usage_log` row per Voyage batch to keep embeddings visible to the per-tenant rate-limit windows. Voyage's free tier (3 RPM / 10K TPM) is enough for development; production needs at least the first paid tier. If PrimeQA moves to Apple-Silicon dev hardware a local `sentence-transformers` model becomes viable again and this decision is worth revisiting; likewise if Anthropic ships a first-party embedding API, or if a downstream substrate surfaces retrieval-quality issues that point at the model choice.

**Cross-references.** D-046 (semantic_text is the deterministic embedding input); D-048 (graceful fallback for AI-primitive failures); `PHASE_2_PLAN_corrections.md` §23; P8 precursor commit `3aa2b8f`.

> **Note on prior informal D-043.** The pre-D-049 choice of OpenAI
> `text-embedding-3-small` (1536 dim) was recorded in
> `docs/architecture/substrate_1_semantic_org_model/PHASE_2_PLAN.md:468`
> as informal D-043, not in this DECISIONS_LOG. PHASE_2_PLAN is a
> locked planning artifact and is not edited; this D-049 entry
> supersedes the planning-time choice. See
> `PHASE_2_PLAN_corrections.md` §23 for the resolution narrative.

---

## D-050 — 8-substrate model is architectural authority; PRIMEQA_PRODUCT_DEFINITION's 4-substrate framing is product narrative

**Date:** 2026-05-14
**Substrates affected:** [all]
**Status:** active

**Decision.** Per D-001, PrimeQA's architecture is decomposed into 8
substrates: S1 Semantic Org Model, S2 Test Representation, S3
Generation Engine, S4 Execution Engine, S5 Knowledge System, S6
Observation and Interpretation, S7 Conversation and Control, S8
Evolution Engine. This 8-substrate framing is authoritative for all
architectural decisions, substrate spec naming, and phase planning.

`PRIMEQA_PRODUCT_DEFINITION.md` previously used a 4-substrate framing
that collapsed S2+S3 into "Test Generation" and omitted S5, S7, S8.
That framing was a product narrative written without a DECISIONS_LOG
entry overriding D-001. It is reconciled in this cycle:
PRODUCT_DEFINITION updated to use 8-substrate framing throughout.

**Rationale.** Two competing substrate framings caused real confusion
at Phase 3 (Substrate 2 — Test Representation) design kickoff.
PLATFORM_VISION's 8-substrate decomposition is the more architecturally
precise framing — Test Representation as data structure separate from
Generation Engine as AI pipeline is a meaningful split. Forcing both
docs to use the same framing eliminates ambiguity for current and
future contributors.

**Alternatives considered.**

- *Override D-001 to adopt 4-substrate model* — rejected;
  PLATFORM_VISION's decomposition is the more precise framing and
  overriding D-001 would compress architectural distinctions that
  matter (S2 data structure vs S3 generation, S5 knowledge
  cross-cutting).
- *Keep both framings and document the mapping* — rejected as ongoing
  drift surface; pick one and use it.

**References.**

- D-001 (original 8-substrate decision)
- `PLATFORM_VISION.md` §"The Eight Substrates"
- `PRIMEQA_PRODUCT_DEFINITION.md` (after this commit's updates)
- `docs/CONVENTIONS.md` §"Documentation authority"

---

*End of Phase 2 additions.*


## Phase 3 additions

## D-051 — Test case as identity-bearing claim with replaceable recipes (resolves S2-Q-001)

**Date:** 2026-05-16
**Substrates affected:** [S2, with downstream consequences for S3, S4, S6, S8]
**Status:** active

**Decision.** A PrimeQA test case is fundamentally a structured
claim — an *asserted system truth* scoped by the *semantic
conditions* under which it should hold, realized through *one or
more replaceable executable recipes*. A test case decomposes into
five layers, two of which (asserted truth and semantic conditions)
are identity-bearing; the other three (execution realization,
execution environment, provenance) are not. Coverage is derived
from the claim, not authored separately.

| Layer                  | Identity-bearing? |
| ---------------------- | :---------------: |
| Asserted system truth  | YES               |
| Semantic conditions    | YES               |
| Execution realization  | NO                |
| Execution environment  | NO                |
| Provenance             | NO                |

Discipline rule for the semantic-vs-operational boundary: *if a
value or entity is referenced inside the claim, it is semantic and
identity-bearing; otherwise it is operational and lives in the
recipe.* Claim structure is intentionally constrained — S2 is not
a general system-specification language; claims express what QA
tests actually need to assert, bounded by human legibility, machine
queryability, and archetype coherence. Canonical claim units lean
atomic; aggregation of multiple atomic claims under a user-facing
test envelope is permitted, with the structural shape of that
aggregation pending S2-Q-003.

**Rationale.** Per the TA pushback on the original four-candidate
framing (scenario / semantic slice / execution recipe / LLM
transcript), the missing fifth candidate — assertion / invariant /
expected truth-condition — turned out to be the architecturally
load-bearing one. The asserted truth outlives recipes, UI changes,
and generation regenerations; it is what S6 must map failures back
to, what S8 must preserve when rewriting tests autonomously, and
what humans engage with day-to-day. Refinement during the design
conversation separated semantic conditions (identity-bearing, part
of the claim) from execution context (operational, part of the
recipe), which produced the five-layer model. This is cleaner than
the alternatives because it places identity in the smallest stable
unit that carries meaning, while giving S8 (Evolution) wide
autonomous latitude over operational layers without crossing the
human-authority boundary.

**Alternatives considered.**

- *Execution recipe as root (v2.2 status quo).* Rejected. Recipes
  are brittle; the same assertion can be tested by many recipes,
  and recipes don't generalize across the five archetypes —
  configuration, permission, UI, and integration tests don't fit
  a CRUD-step shape.
- *Coverage / semantic slice as root.* Rejected. Collapses test
  meaning into structural footprint; two semantically distinct
  tests with identical S1 coverage would falsely share identity.
- *LLM-generation transcript as root.* Rejected. This is provenance,
  not identity. Regeneration of the same JIRA ticket producing the
  same claim should not create a new test.
- *Scenario as root (A4-style).* Rejected as final framing.
  "Scenario" overloaded two distinct concerns — identity-bearing
  semantic conditions and operational execution context — under
  one term. Pulling these apart into separate layers gives a
  cleaner cut.
- *(claim, scenario) tuple as identity.* Rejected. Same overload
  problem: the "scenario" half dissolves into identity-bearing and
  non-identity-bearing parts on closer examination.

**Downstream consequences.**

- *S2-Q-002 (commonality across archetypes).* The five-layer model
  is structurally uniform across all five archetypes; only the
  *form* of claim and recipe varies per archetype.
- *S2-Q-004 (S1 references).* References inside the claim are
  intent-bearing and lean toward pinning; references inside the
  recipe are operational and lean toward logical resolution. Final
  shape pending S2-Q-004.
- *S2-Q-006 (authority over mutation).* S8 has autonomous authority
  over the three non-identity-bearing layers; changes to either
  identity-bearing layer require human authority.

**References.**

- `substrate_2_test_representation/SPEC.md` §2
- `substrate_2_test_representation/BACKGROUND.md` (architectural
  ambition framing; human-legibility principle)
- `substrate_2_test_representation/OPEN_QUESTIONS.md` S2-Q-002,
  S2-Q-003, S2-Q-004, S2-Q-006 (downstream questions whose
  resolutions this decision constrains)
- `archive/ARCHITECTURE_4_NOTE.md` (scenario-binds-execution
  principle; partially absorbed as semantic conditions)
- `PRIMEQA_PRODUCT_DEFINITION.md` §4.3 (S2's six concerns: intent,
  coverage, relationships, execution history, assumptions,
  provenance)

---


## D-052 — Three orthogonal discriminators with archetype-specific semantic forms (resolves S2-Q-002)

**Date:** 2026-05-16
**Substrates affected:** [S2, with consequences for S3, S4]
**Status:** active

**Decision.** A PrimeQA test case is classified along three
orthogonal discriminators — `archetype` (5 values: data_behavior,
configuration, permission, ui, integration), `claim_kind` (multiple
per archetype, taxonomy seeded but not locked), and `recipe_kind`
(taxonomy deferred to S2-Q-003). The five-layer model from D-051
is structurally uniform across all five archetypes; within each
layer the boundary between common and archetype-specific falls
inside the layer: a uniform discriminator-bearing envelope holds
an archetype-specific *semantic form*. Coverage is fully derived
from claim references.

Guardrail: **archetypes are classifications, not storage
partitions.** The discriminators name conceptual categories; they
do not entail per-archetype tables or migrations. Storage
realization is fully S2-Q-003.

Sharpening: the execution-environment layer models *capability
assumptions* (what the recipe requires to be available in order
to run) — not merely setup payloads. This is what makes recipe
selection meaningful: S4 matches the available environment against
each recipe's capability assumptions when picking among multiple
recipes for the same claim. SPEC §2's table row description for
execution environment updated in the same commit for consistency.

**Rationale.** The TA pushback in S2-Q-002 surfaced two distinct
layers — structural commonality (schema-level) and semantic
commonality (conceptual). D-051 established semantic commonality
at the level of the five-layer model. What remained open was where
the line falls within each layer between uniform and archetype-
specific. The orthogonal-three-discriminator framing puts the line
in the right place: uniformity at the envelope and discriminator
level, archetype-specific at the semantic form level. Treating
archetype, claim_kind, and recipe_kind as independent axes (rather
than nested) preserves forward compatibility — a future recipe_kind
for an existing claim_kind requires no schema change. The
capability-assumption sharpening of execution environment
strengthens the recipe-selection model and aligns the layer with
its actual operational role.

**Alternatives considered.**

- *Per-archetype tables (storage = classification).* Rejected.
  Confuses conceptual classification with storage layout;
  precludes cross-archetype queries; would require migration when
  archetypes evolve.
- *Nested discriminators (archetype determines claim_kind which
  determines recipe_kind).* Rejected. Couples axes that can vary
  independently in practice — e.g., a permission capability-claim
  can be realized by a run-as recipe OR a metadata-inspection
  recipe; recipe_kind is independent of claim_kind.
- *Single discriminator (archetype only).* Rejected. Collapses
  meaningful distinctions: a "data-behavior test" can carry many
  different claim-kinds and many different recipe-kinds;
  flattening these into one axis loses structure that downstream
  substrates (S4 executor selection, S6 attribution, S8 evolution)
  need.
- *Execution environment as bare setup payload.* Rejected. Fails
  to capture what makes multi-recipe-per-claim meaningful;
  obscures the recipe-selection semantic.

**Downstream consequences.**

- *S2-Q-003 (data model):* Lock the claim_kind taxonomy, design
  the recipe_kind taxonomy, realize the uniform envelope plus
  per-archetype semantic forms in concrete storage shapes.
- *S2-Q-007 (execution-history boundary):* The capability-
  assumption model interacts with environment-availability
  metadata; the S2/S4 boundary on this is partially open.
- *S4 design (future substrate):* Recipe selection becomes a
  capability-assumption-matching problem rather than a generic
  "pick a recipe" problem.

**References.**

- `substrate_2_test_representation/SPEC.md` §3
- `substrate_2_test_representation/SPEC.md` §2 (five-layer model
  from D-051; execution-environment table row sharpened in this
  commit for consistency with §3)
- D-051 (the structural-and-semantic-uniform baseline this
  decision builds on)
- `PLATFORM_VISION.md` §"Product Scope" (the five-archetype
  product scope)
- `substrate_2_test_representation/OPEN_QUESTIONS.md` S2-Q-003
  (locks the seeded claim-kind taxonomy; designs the recipe-kind
  taxonomy; chooses storage realization)

---


## D-053 — Claim-kind taxonomy locked (16 kinds, 5 archetypes) [S2-Q-003 sub-cycle 1]

**Date:** 2026-05-16
**Substrates affected:** [S2, with consequences for S3, S4, S6]
**Status:** active

**Decision.** The claim-kind taxonomy seeded in the §3 first draft
is locked at 16 kinds across 5 archetypes:

| Archetype | Locked claim-kinds | Count |
|---|---|---|
| `data_behavior` | `value-claim`, `state-transition-claim`, `automation-effect-claim`, `prohibition-claim` | 4 |
| `configuration` | `existence-claim`, `property-claim`, `metadata-relationship-claim` | 3 |
| `permission` | `capability-claim`, `sharing-rule-claim` | 2 |
| `ui` | `element-state-claim`, `navigation-claim`, `layout-claim` | 3 |
| `integration` | `platform-event-claim`, `outbound-message-claim`, `callout-claim`, `inbound-effect-claim` | 4 |

A second guardrail is established alongside D-052's "archetypes
are classifications, not storage partitions":

**Claim-kinds model semantic forms, not implementation primitives.**
A new claim-kind is warranted when it names a different *kind of
truth being asserted* (different semantic form). A new claim-kind
is *not* warranted when it names a different *Salesforce mechanism*
that realizes the same semantic. Validation-rule-firing and
flow-firing are the same semantic (an automation produced an
effect); they share `automation-effect-claim` with the primitive
captured in a sub-discriminator. Platform-event vs
outbound-message vs callout differ in semantic form (different
payload structures and observables) and get separate claim-kinds.

**Rationale.** The §3 first-draft seeds (per D-052) gave a
starting taxonomy. Locking required walking each archetype and
applying merge/split/rename moves under two principles:
mechanism-vs-semantic distinction (the new guardrail above), and
TA pushback on specific items. Notable moves:

- *Data-behavior:* merged `vr-firing-claim` + `flow-effect-claim`
  into `automation-effect-claim` (same semantic, mechanism as
  sub-discriminator). Merged `deletion-blocked-claim` +
  `duplicate-prevention-claim` into `prohibition-claim` (renamed
  from `operation-blocked-claim` per TA invariant-orientation
  pushback — "operation O is prohibited under conditions C" reads
  as an invariant, where "operation was blocked" reads
  procedurally).
- *Configuration:* absorbed `activation-claim` into
  `property-claim` ("active" is a property value, not a separate
  semantic).
- *Permission:* kept Option B (capability-claim and
  sharing-rule-claim distinct) per TA confirmation.
  Sharing-rule's rule-mechanism structure is genuinely distinct
  from outcome-capability — testing the rule itself is a
  different assertion from testing what a user can do.
- *UI:* absorbed `element-visibility-claim` into
  `element-state-claim` (visibility is a property of element
  state).
- *Integration:* kept all four kinds distinct per TA
  split-pushback — the three outbound effect kinds
  (platform-event, outbound-message, callout) have different
  semantic forms (different payload structures and inspection
  mechanisms), not merely different implementation primitives.

Articulated the state-transition vs automation-effect distinction
explicitly: state-transition asserts the resulting end state
(mechanism-agnostic); automation-effect asserts a specific
automation firing and its side effects (mechanism-specific).
Concrete dividing test: would the test still mean the same thing
under a different implementing primitive? Yes →
state-transition-claim. No → automation-effect-claim.

**Alternatives considered.**

- *Option A on permission (single capability-claim absorbing all
  permission variants).* Rejected per TA pushback. Sharing-rule's
  rule-mechanism structure is structurally distinct from
  outcome-capability; collapsing them loses the rule-level test.
- *Aggressive integration consolidation (`outbound-effect-claim`
  covering platform-events, outbound-messages, callouts).*
  Rejected per TA pushback. The three differ in semantic form
  (payload structures, observables), which is exactly the kind
  of distinction that warrants separate claim-kinds under the
  new guardrail.
- *`operation-blocked-claim` as procedural name.* Rejected per
  TA invariant-orientation pushback. Renamed `prohibition-claim`.
- *Cross-archetype name reuse (e.g., `existence-claim` in both
  configuration and data-behavior).* Considered and not done in
  this lock. The D-052 discriminator framing permits it, but no
  data-behavior use case currently needs `existence-claim` —
  record-existence is expressible via combinations of other
  kinds (e.g., `prohibition-claim` of create + `value-claim` of
  post-state). Reserved as a possible future addition without
  schema change.

**Downstream consequences.**

- *S2-Q-003 sub-cycle 2 (recipe-kind taxonomy):* Can now be
  designed with concrete claim-kinds to attach recipe-kinds to.
- *S2-Q-003 sub-cycle 3 (storage realization):* Has 16
  discriminator values to plan around. The merge/split moves in
  this lock determine which sub-discriminators (e.g.,
  automation-primitive within `automation-effect-claim`) become
  part of the JSONB body.
- *S3 (Generation, future substrate):* Schema-enforcement of LLM
  output against S2 will validate that generated claims match
  one of the 16 kinds with a valid sub-discriminator combination.
- *Future taxonomy proposals:* Constrained by the
  semantic-vs-implementation-primitive guardrail.

**References.**

- `substrate_2_test_representation/SPEC.md` §3 (claim-kind
  taxonomy section, replaced from seeded to locked in this
  commit; second guardrail subsection added)
- D-052 (the three-discriminator framing and the first
  archetype-classification guardrail this builds on)
- `substrate_2_test_representation/OPEN_QUESTIONS.md` S2-Q-003
  (the multi-sub-cycle data-model question, of which this is
  sub-cycle 1)

---


## D-054 — Recipe-kind taxonomy locked (5 kinds, observability-domain only) [S2-Q-003 sub-cycle 2]

**Date:** 2026-05-17
**Substrates affected:** [S2, with consequences for S3, S4]
**Status:** active

**Decision.** The recipe-kind taxonomy is locked at 5 kinds:

| Recipe-kind | Observability domain |
|---|---|
| `data-recipe` | Record-level operations via data API (broader than CRUD) |
| `metadata-recipe` | Metadata-level operations (with `metadata-read` and `metadata-write` sub-discriminator modes) |
| `ui-recipe` | Browser-driven Lightning UI interaction |
| `event-subscription-recipe` | Salesforce-defined event payload observation |
| `callout-intercept-recipe` | Salesforce-initiated HTTP callout observation |

A third guardrail is established alongside D-052's "archetypes are
classifications, not storage partitions" and D-053's "claim-kinds
model semantic forms, not implementation primitives":

**Recipe-kinds classify observability semantics only.** A
recipe-kind names what a procedure observes and how it asserts —
not what triggers the scenario being tested. Triggering actions
are a separate classification axis, tracked as S2-Q-011.

**Rationale.** The sub-cycle 2 design conversation surfaced two
related questions: (1) what set of observability domains exists
in Salesforce testing, and (2) is the triggering-action of a test
part of the recipe or separate from it. The first question
produced the 5 kinds. The second question was resolved by adopting
Option B from the design conversation: inbound-injection is not a
recipe-kind; it belongs to a separate trigger-kind classification.
This preserves "one observability domain per recipe-kind" as a
clean structural rule.

Key moves during the design conversation:

- `crud-recipe` renamed `data-recipe`. The CRUD framing was
  leaking implementation-primitive vocabulary into the kind name.
  `data-recipe` is broader (covers queries, aggregates, record
  actions, composite operations, anonymous-Apex-over-data) and
  matches the observability-domain pattern of the other kinds.
- `metadata-recipe` clarified with named sub-discriminator modes:
  `metadata-read` (non-destructive query) and `metadata-write`
  (destructive deploy). These have meaningfully different
  capability assumptions and risk profiles. v1 expected to be
  `metadata-read`-only; `metadata-write` covers configuration
  tests asserting "deploying X changes behavior Y."
- `event-subscription-recipe` vs `callout-intercept-recipe` split
  justified on semantic-vocabulary grounds (Salesforce-event
  payload structure vs HTTP-request structure), not on transport
  grounds. Event-subscription assertions reference event-defined
  fields; callout assertions reference HTTP request shape.
  Different assertion vocabularies, different semantic forms,
  separate kinds.
- Inbound-injection considered as a 6th recipe-kind (composite
  push-plus-observe) and rejected per Option B selection. Inbound
  injection is a causal-initiation pattern, not an observability
  pattern; classifying it as a recipe-kind would dissolve the
  one-domain-per-kind rule. Tests of `inbound-effect-claim` use
  existing recipe-kinds for observation; the inbound payload is
  a trigger-kind concern.

**Alternatives considered.**

- *Option A: inbound-injection as composite recipe-kind* (push
  payload + observe internal effect under one kind). Rejected per
  Option B selection. Composite recipe-kinds violate the
  one-domain-per-kind pattern.
- *Option C: split inbound-injection by channel*
  (`inbound-rest-recipe`, `inbound-soap-recipe`,
  `inbound-email-recipe`). Rejected per Option B selection.
  Inbound channel splits would proliferate kinds without
  resolving the underlying issue that inbound injection is a
  trigger, not an observability domain.
- *Apex as its own recipe-kind.* Rejected. Apex is an execution
  mechanism that can wrap data, metadata, or computation
  operations; the observability pattern depends on what the Apex
  does, not on Apex itself. Apex becomes a sub-discriminator on
  `data-recipe` and `metadata-recipe` where applicable.
- *Run-as as its own recipe-kind.* Rejected. Run-as is an
  identity context that modifies how `data-recipe`, `ui-recipe`,
  or `metadata-recipe` execute. The observability pattern doesn't
  change; only the user identity does. Run-as becomes a
  sub-discriminator plus a capability assumption.

**Downstream consequences.**

- *S2-Q-003 sub-cycle 3 (storage realization):* Has 5 recipe-kind
  discriminator values to plan around, plus the sub-discriminator
  patterns within each.
- *S2-Q-011 (trigger-kind classification):* Now a tracked open
  question. Triggering actions including inbound injection,
  internal data mutation, UI actions, time-based triggers, and
  configuration changes need their own taxonomic treatment.
- *S4 (Execution, future substrate):* Recipe-kind dispatches to
  a specific executor; sub-discriminators tune the execution
  within that kind. S4 will need a recipe-selection algorithm
  that matches recipe capability assumptions against environment
  availability.

**References.**

- `substrate_2_test_representation/SPEC.md` §3 (Recipe-kind
  taxonomy subsection and observability-purity guardrail added
  in this commit)
- D-051, D-052, D-053 (the foundation this builds on)
- `substrate_2_test_representation/OPEN_QUESTIONS.md` S2-Q-003
  sub-cycle 2 (closed by this decision) and S2-Q-011 (newly
  opened parallel question)

---


## D-055 — Trigger-kind taxonomy locked + four-discriminator extension + six-layer model amendment [S2-Q-011]

**Date:** 2026-05-17
**Substrates affected:** [S2, with consequences for S3, S4, S6, S8]
**Status:** active

**Decision.** Five interrelated architectural commitments:

1. **Fourth orthogonal discriminator added.** `trigger_kind` joins
   `archetype`, `claim_kind`, and `recipe_kind` as a fourth
   independent classification axis. Extends D-052's three-discriminator
   framing.

2. **Six-layer structural model.** The five-layer model from D-051
   is extended to six layers by adding a "Causal initiation" layer
   for trigger realization. The existing five layers retain their
   roles and identity properties; the new layer is non-identity-bearing
   by default.

3. **Terminology supersession.** "Execution realization" (D-051) is
   renamed to "Observation realization" per the recipe-kind purity
   scope from D-054. This better reflects that the layer scopes
   observation, not arbitrary execution. The term may be further
   refined in future cycles. `SPEC.md` §2 is updated in this commit
   for consistency.

4. **Trigger-kind taxonomy locked at five kinds:**

   | Trigger-kind | Plane | Causal initiation domain |
   |---|---|---|
   | `inbound-trigger` | runtime | External system pushes payload into Salesforce |
   | `data-mutation-trigger` | runtime | DML on records inside Salesforce |
   | `ui-trigger` | runtime | User-driven UI action |
   | `time-trigger` | runtime | Salesforce mechanisms firing because elapsed-time predicates were met |
   | `configuration-trigger` | **model** | Metadata deploy as causal initiation; mutates org model |

5. **Two new guardrails added** to §3's existing three:
   - *Trigger-kind purity:* trigger-kinds classify causal-initiation
     semantics only, not observation or implementation technology.
   - *Trigger-vs-recipe orthogonality:* trigger-kind and recipe-kind
     classify different aspects of operational realization and must
     not be conflated.

**Runtime-plane vs model-plane distinction.** Four trigger-kinds
(`inbound-trigger`, `data-mutation-trigger`, `ui-trigger`,
`time-trigger`) operate at the runtime plane — they cause behavior
within the existing org model. `configuration-trigger` operates at
the model plane — it mutates the org model itself. This is a
structural distinction, not a label: configuration-trigger tests
carry test-runtime risk (can break unrelated tests by changing
shared rules), require shared-org coordination, and are
architecturally adjacent to S8 (Evolution) work since they test
the platform's response to its own configuration changes.

**Trigger-kind identity nuance.** Trigger-kind is operational by
default and not identity-bearing. However, D-051's discipline rule
applies: if the trigger mechanism itself is semantically asserted
in the claim ("when external system sends via synchronous REST,
the response includes outcome X within 5 seconds"), the mechanism
becomes part of semantic conditions and IS identity-bearing.
Operational by default, semantic by assertion.

**Time-trigger narrowed semantics.** During design discussion the
question arose whether time-trigger should encompass general
"system progression semantics" (including retry queues, async
chains, batch windows). Decision: narrow scope to Salesforce
mechanisms firing because elapsed-time predicates were met
(scheduled flows, scheduled batch Apex, time-based workflow actions,
time-dependent field updates). General async / retry / queue
semantics are downstream behaviors observed by recipes, not
triggers themselves.

**One primary trigger per test (default).** A test has one primary
trigger most directly tied to the claim's WHEN; other causal-looking
actions are setup. Multi-primary scenarios are possible but rare
and usually indicate decomposition into multiple tests.

**Rationale.** The sub-cycle 2 (recipe-kind) resolution established
recipe-kind purity (observability semantics only). This left
causal-initiation patterns without a first-class home, which would
have either dissolved them into recipe-kinds (violating purity) or
buried them in JSONB (losing structural clarity). Promoting
trigger-kind to a fourth orthogonal discriminator gives them
first-class structural treatment while preserving the purity rule.
The six-layer model amendment follows directly from the new
discriminator — trigger realization needs a structural home, and
folding it into "execution realization" would have silently
re-violated D-054's purity scope.

The TA pushback during S2-Q-011 design surfaced several refinements
integrated into this lock: time-trigger narrowed scope, elevated
treatment of configuration-trigger's cross-plane semantics
(structural distinction, not just a label), trigger identity
nuance (operational-by-default vs identity-bearing-if-asserted),
"observation realization" terminology rename, and the
trigger-vs-recipe orthogonality guardrail.

**Alternatives considered.**

- *Trigger-kind as sub-discriminator of recipe-kind.* Rejected.
  Couples two independent classification axes; collapses cases
  where the same trigger has multiple recipes (data-mutation
  observed by both data-recipe and ui-recipe).
- *Keep five-layer model and fold trigger into "execution
  realization."* Rejected. D-054 already scoped that layer to
  observation-only under the recipe-kind purity rule. Folding
  trigger in would silently re-violate that scope.
- *Configuration-trigger removed from taxonomy.* Rejected. It's
  rare in v1 but architecturally real for tests asserting
  "deploying X causes behavior Y." Removed-then-re-added later
  would be more disruptive than including it with explicit
  cross-plane treatment now.
- *Time-trigger broadened to "system progression semantics"*
  (covering retry queues, async chains). Rejected. Overloads the
  kind and conflates triggers with downstream behaviors observed
  by recipes. Narrower scope is more honest.
- *"Observation realization" kept as "execution realization."*
  Rejected. The term mismatches D-054's purity scope. Worth the
  D-051 terminology supersession; future further rename remains
  open.

**Downstream consequences.**

- *S2-Q-003 sub-cycle 3 (storage realization):* Has four
  discriminator columns to plan around (archetype, claim_kind,
  trigger_kind, recipe_kind) plus sub-discriminator patterns per
  kind. The six-layer model means storage may need a separate
  trigger-body shape alongside recipe-body shape.
- *S2-Q-004 (S1 references):* Trigger bodies will hold S1 entity
  references too (the entity being mutated, the user identity for
  ui-trigger, etc.). Same reproducibility-vs-evolvability tension
  applies; trigger references probably follow the recipe-reference
  pattern (logical) rather than the claim-reference pattern
  (pinned).
- *S4 (Execution, future substrate):* Recipe selection becomes
  capability-matching against environment; trigger selection has
  the same structure. S4 needs both a trigger-executor and a
  recipe-executor (or one executor handling both).
- *S6 (Interpretation):* Failure attribution may differ for
  trigger failures (the trigger itself didn't fire correctly) vs
  recipe failures (the observation failed). Useful structural
  distinction.

**References.**

- `substrate_2_test_representation/SPEC.md` §3 (Trigger-kind
  taxonomy subsection, four-discriminator definition, six-layer
  model, two new guardrails, four-axes summary table — all added
  or updated in this commit)
- `substrate_2_test_representation/SPEC.md` §2 (one-row table
  description updated for terminology consistency)
- D-051 (the five-layer model this amends; "execution realization"
  terminology superseded)
- D-052 (the three-discriminator framing this extends to four)
- D-053 (the claim-kind taxonomy parallel)
- D-054 (the recipe-kind taxonomy parallel; recipe-kind purity
  rule)
- `substrate_2_test_representation/OPEN_QUESTIONS.md` S2-Q-011
  (closed by this decision)

---


## D-056 — Storage realization for substrate-2: four-table layout with Pattern D [S2-Q-003 sub-cycle 3]

**Date:** 2026-05-17
**Substrates affected:** [S2, with consequences for S3, S4, S6, S8]
**Status:** active

**Decision.** Substrate-2 uses a four-table layout:

| Table | Role | Discriminators (canonical authority) |
|---|---|---|
| `test_claims` | Identity-bearing semantic content | `archetype`, `claim_kind` |
| `test_recipes` | First-class operational entities | `trigger_kind`, `recipe_kind` |
| `test_provenance` | Append-only history (polymorphic) | event_kind |
| `test_claim_coverage` | Semantic linkage layer (S2↔S1) | (entity_type, reference_kind) |

Storage pattern is **Pattern D**: envelope + JSONB bodies per
layer + selected typed columns for hot paths (discriminators,
identity_hash, status, version columns).

**Two new guardrails added** to §3:

- *Sixth — Semantic-vs-operational lifecycle distinction.*
  Identity-bearing layers and operational layers have distinct
  lifecycle semantics. Changes to identity-bearing layers require
  human authority and invalidate approval; changes to operational
  layers can be S8-autonomous and preserve approval. Storage shape
  and authority model consistently honor this.
- *Seventh — Continuity triad.* Stable identifiers, identity_hash,
  and version_seq model three distinct continuities (organizational,
  semantic, supersession-order). Most systems collapse these into
  "latest version"; the substrate models them separately so each
  can evolve independently.

**Key structural decisions.**

- *Claim/recipe split.* Honors D-051's identity model — claim is
  identity, recipes are replaceable. Recipes are first-class
  operational entities with their own lifecycle, not "child details
  of claims." S8 can rewrite a recipe without touching its parent
  claim.
- *Discriminator placement.* `archetype` + `claim_kind` on
  `test_claims` (what's asserted); `trigger_kind` + `recipe_kind`
  on `test_recipes` (operational realization). Different recipes
  for the same claim can carry different trigger-kind /
  recipe-kind combinations.
- *Row discriminator as canonical authority.* Row-level discriminator
  columns are source of truth. JSONB body's `kind` field is
  redundant self-description for export/debug; row wins on
  disagreement. Write-time validation enforces consistency.
- *JSONB body conventions.* Top-level `body_schema_version` (int,
  per-body-kind trajectory) + `kind` (redundant self-description).
  Body shape per (row discriminator, schema version) defined by
  Pydantic models locked in sub-cycle 5.
- *Semantic linkage layer.* `test_claim_coverage` is first-class
  architectural concern (S2↔S1 bridge for S6 attribution, S8
  evolution detection, coverage discovery), not merely a
  denormalized cache. App-level derivation by S3 / S8 writers.
- *Polymorphic provenance.* Single `test_provenance` table with
  nullable `claim_test_id` and `recipe_id` (CHECK constraint:
  exactly one set). Event-kind discriminator distinguishes
  claim-level from recipe-level events.

**Rationale.** Sub-cycle 3 design considered four candidate
patterns:
- Pattern A (single envelope + JSONB) — conceptually correct but
  no hot-path optimization.
- Pattern B (envelope + per-archetype tables) — directly violates
  D-052's "archetypes are classifications, not storage partitions"
  guardrail.
- Pattern C (envelope + per-kind tables) — even worse guardrail
  violation; combinatorial schema migrations.
- Pattern D (envelope + JSONB + hot-path typed columns) — Pattern
  A plus pragmatic denormalizations; selected.

The claim/recipe split addresses D-051's "one or more executable
recipes" structurally — multiple recipes per claim becomes a
clean FK relationship, recipes are queryable on their own, and
the identity model is honored at storage level.

TA refinements during sub-cycle 3 design integrated:

- Semantic linkage framing for coverage (elevates it from
  denormalized cache to first-class architectural layer)
- Recipe-level provenance (polymorphic event table)
- `body_schema_version` discipline in JSONB bodies (preserves
  forward-compatibility for body shape evolution)
- Forward-compatibility marker for `semantic_conditions`
  graphification
- Recipes as first-class operational entities (explicit framing)
- Row discriminator as canonical authority (`body_schema_version`
  rename from `schema_version` for precision; row > body on
  disagreement)
- Reserved room for operational linkage layer (recipe-derived,
  parallel to semantic linkage)
- Semantic-vs-operational lifecycle guardrail (sixth)
- Continuity triad guardrail (seventh)

**Alternatives considered.**

- *Single envelope, all six layers in one row.* Rejected. Forces
  multiple recipes per claim into either multiple rows per test
  (identity is wrong) or recipes-as-JSONB-array (loses queryability
  and per-recipe state).
- *Per-archetype detail tables (Pattern B).* Rejected. Directly
  violates D-052.
- *Per-kind detail tables (Pattern C).* Rejected. Even more
  severe guardrail violation; combinatorial schema migrations on
  new kinds.
- *Coverage as materialized view.* Rejected. Refresh strategy
  needed; staleness possible; loses the "semantic linkage layer"
  architectural framing.
- *DB-trigger-driven coverage updates.* Rejected. Trigger logic
  must parse JSONB; brittle to body schema changes; harder to test.
  App-level (S3 / S8 writers) preferred.

**Downstream consequences.**

- *S2-Q-003 sub-cycle 4 (identity-hash mechanics):* Operates on
  `test_claims.asserted_truth` + `test_claims.semantic_conditions`
  JSONB; canonicalization policy is governance-critical for
  approval invalidation and S8 authority boundary.
- *S2-Q-003 sub-cycle 5 (Pydantic validation):* Implements body
  validation per (row discriminator, `body_schema_version`)
  dispatch; enforces row-vs-body consistency at write time.
- *S2-Q-004 (S1 references):* Reference shape lives inside JSONB
  bodies; pinned-vs-logical resolution affects body content and
  coverage derivation.
- *S2-Q-005 (versioning):* Resolved jointly with this decision as
  D-057.
- *S3 (Generation):* Writes `test_claims` + `test_recipes` +
  `test_claim_coverage` together; validates against Pydantic
  models per discriminator.
- *S6 (Interpretation):* Reads `test_claim_coverage` for failure
  attribution; reads `test_provenance` for historical context.
- *S8 (Evolution):* Reads `test_claim_coverage` for affected-test
  detection on S1 entity changes; writes new `test_recipes`
  rows on autonomous rewrites; writes `test_provenance` rows for
  audit trail. Cannot write `test_claims` (semantic content)
  without human authority.

**References.**

- `substrate_2_test_representation/SPEC.md` §4 (data model
  substantive content)
- `substrate_2_test_representation/SPEC.md` §3 (two new guardrails
  added: semantic-vs-operational lifecycle; continuity triad)
- D-051 (identity model; claim/recipe split derives from this)
- D-052 (archetype classification guardrail; honored by Pattern D)
- D-053, D-054, D-055 (taxonomies that occupy the discriminator
  columns)
- D-057 (versioning model; jointly resolved)

---


## D-057 — Lifecycle and versioning model: effective-time supersession with version_seq canonical authority [S2-Q-005]

**Date:** 2026-05-17
**Substrates affected:** [S2, with consequences for S3, S4, S6, S8]
**Status:** active

**Decision.** Substrate-2 uses **effective-time supersession** as
its versioning model. Single time dimension (effective time only;
no separate transaction-time tracking). Applied uniformly to
`test_claims` and `test_recipes`; `test_provenance` is naturally
append-only (degenerate-supersession); `test_claim_coverage` is
current-only.

**The term "bitemporal" is intentionally avoided.** True bitemporal
tracks both valid time and transaction time. What this decision
implements is single-dimension effective-time supersession. The
"bitemporal" label is reserved should we ever add transaction-time
tracking.

**Invariant hierarchy.** `version_seq` defines supersession truth;
`valid_to` is denormalized convenience. In any operational scenario
where they appear to disagree (partial migrations, repair operations,
backfills, replay imports), **`version_seq` wins**. The "exactly
one NULL `valid_to` per identifier" invariant may be temporarily
violated during specific operations while `version_seq` correctness
is preserved; reconciliation re-derives `valid_to` from `version_seq`.

**`identity_hash` semantics.** `identity_hash` is the **semantic
equivalence fingerprint**. NOT a unique identifier (that's
`test_id`). NOT a primary or unique key — multiple rows may share
it. The hash fingerprints the test's *meaning*, not its history
or identity-as-row. Across a test's version timeline: operational
edits preserve hash (test means the same); semantic edits change
hash (test means something different now; approval invalidated).

**Canonicalization policy is governance-critical.** Sub-cycle 4's
scope is not "compute a hash" but "define governance policy for
what counts as semantic vs operational edit." The canonicalization
rules determine:
- Approval state lifecycle (when QA re-approval is needed)
- Semantic equivalence reasoning (are two tests "the same")
- S8's autonomous-rewrite authority boundary (S8 can do anything
  that preserves hash; cannot do anything that changes hash
  without escalating to human authority)

**Recipe-to-claim FK semantics.** Default is **logical resolution**:
recipe's `claim_test_id` references the claim's `test_id` (not a
specific version). Recipes follow claim evolution. Optional pinning
via nullable `claim_version_seq` for reproducibility-critical
contexts (historical replay, audit).

**Coverage rederivation.** Current-only. When a new claim version
supersedes its predecessor, coverage rows are deleted and rederived
for the new version. Historical coverage is reconstructible from
claim history but not pre-stored.

**Approval state lifecycle.** Dual-tracked: current state on the
`test_claims.status` column (O(1) query); history in
`test_provenance` events. Invalidation triggers on `identity_hash`
change between versions. S8 autonomous rewrites cannot trigger
invalidation because S8 only operates on operational layers.

**Archival policy.** **None in v1, because semantic lineage
continuity is currently more valuable than retention optimization.**
This is an architectural commitment, not a cost-driven decision. If
retention costs ever pressure this commitment, archival becomes a
substrate-level decision requiring explicit re-evaluation of the
lineage guarantee.

**Replay modes supported by storage shape** (replay engine
downstream in S4):
- *Historical replay* — pinned references; resolve other refs at
  historical S1 state.
- *Semantic replay* — logical references; resolve to current S1.

**Continuity triad (cross-reference to D-056 / §3 seventh
guardrail).** This decision's `version_seq` is the supersession-order
anchor; `identity_hash` is the semantic-equivalence anchor; stable
identifiers (`test_id`, `recipe_id`) are the organizational-continuity
anchor. The three model distinct continuities and evolve
independently.

**Rationale.** Three candidate models considered:
- *Bitemporal supersession (like S1).* Architecturally consistent
  with S1; cross-substrate queries natural; pinned references
  align cleanly; full historical reconstruction; recipe rewrites
  are first-class history events.
- *Version-immutable aggregate (like v2.2).* Familiar from v2.2;
  "current is one row per entity" simpler; strong immutability
  on history.
- *Hybrid.* Bitemporal for claims + version-immutable for recipes,
  or other splits.

Effective-time supersession selected. Aligns with S1 (architectural
consistency); S8 evolution naturally fits supersession (each rewrite
= new row); pinned references align; full historical reconstruction
queryable; no JOIN required on every read. Hybrid rejected — two
patterns in one substrate add cognitive load for marginal benefit.
Version-immutable rejected — pattern diverges from S1, requires
JOINs, and the v2.2 muscle-memory benefit is sunk (Phase 3 is
rebuild anyway).

The "bitemporal" terminology refinement landed during TA pushback:
what we implement is single-dimension effective-time supersession,
not true bitemporal. The terminology change matters because future
engineers expecting transaction-time queries under "bitemporal"
would be misled.

TA refinements integrated:
- "Bitemporal" → "effective-time supersession" terminology
- `version_seq` canonical over `valid_to` (invariant hierarchy
  formalized; valid_to is denormalized convenience)
- `identity_hash` as semantic equivalence fingerprint (sharper
  framing than "tracks semantic identity")
- Canonicalization as governance-critical (elevates sub-cycle 4
  scope)
- Archival rationale reframed: lineage continuity > retention
  optimization (architectural commitment, not cost-driven)
- Replay-mode distinction reserved (historical vs semantic);
  replay-sensitive recipe selection reserved for future
- Version-granular provenance reserved for future
- "Resolution policy" terminology direction acknowledged but not
  adopted; "logical" / "pinned" remain everyday vocabulary
- Continuity triad guardrail (seventh guardrail in §3, cross-ref'd
  from this decision)

**Alternatives considered.**

- *Version-immutable aggregate.* Rejected. Pattern diverges from
  S1; requires JOINs; pinned-reference alignment is awkward.
- *Hybrid (bitemporal claims + version-immutable recipes).*
  Rejected. Two patterns in one substrate add cognitive load.
- *Pinned-by-default recipe FK.* Rejected. Logical-by-default
  matches the common case (recipes follow claim evolution
  automatically); pinning is opt-in for the reproducibility-critical
  minority.
- *Coverage versioning (history retained).* Rejected. Coverage is
  derived; reconstructible from claim history if needed; pre-storing
  history of derived data adds storage and consistency cost without
  proportional value.
- *Limited archival (e.g., after N years, retire old versions).*
  Rejected for v1. Lineage continuity is currently the
  architectural priority; archival would require deliberate
  re-evaluation if retention costs ever pressure it.
- *"Bitemporal" terminology retained.* Rejected per TA pushback.
  Precision matters: what we implement is single-dimension; the
  bitemporal label is reserved for future true-bitemporal
  escalation.

**Downstream consequences.**

- *S2-Q-003 sub-cycle 4 (identity-hash mechanics):* Now framed
  as governance-critical scope, not implementation detail.
  Canonicalization rules govern approval invalidation and S8
  authority.
- *S2-Q-004 (S1 references):* Reference shape interacts with
  versioning. Logical references resolve through S1's query
  interface; pinned references reference specific S1 version_seq
  values. The pinned-vs-logical S2-Q-004 design will integrate
  with this versioning model.
- *S2-Q-006 (mutation paths and authority):* Authority boundary
  is now mechanically anchored — S8 can autonomously do anything
  that preserves `identity_hash`; cannot do anything that changes
  it. Approval invalidation triggers on hash change.
- *S2-Q-007 (execution-history boundary):* Execution events
  reference recipe `(recipe_id, version_seq)` — replay can
  reconstruct exact recipe used.
- *S4 (Execution, future substrate):* Recipe selection happens
  against current recipes by default; replay engine supports both
  historical and semantic modes per §6.8.
- *S6 (Interpretation):* Failure attribution can trace through
  version history; "this test failed because S8 rewrote the
  recipe last week" is queryable.
- *S8 (Evolution):* Mechanically bounded by `identity_hash`
  preservation. Cannot edit asserted_truth or semantic_conditions
  (would change hash). Can rewrite causal_initiation,
  observation_realization, execution_environment freely (preserve
  hash). Each rewrite produces new recipe row + provenance event.

**References.**

- `substrate_2_test_representation/SPEC.md` §6 (lifecycle and
  versioning substantive content)
- `substrate_2_test_representation/SPEC.md` §3 (continuity triad
  guardrail, cross-ref'd)
- D-051 (identity model; identity_hash semantics anchored here)
- D-056 (storage realization; jointly resolved)
- `substrate_1_semantic_org_model/SPEC.md` (S1's bitemporal
  pattern; S2's effective-time supersession is the single-dimension
  analog)

---


## D-058 — Reference model: hybrid by layer with ontology-enforcement validation [S2-Q-004]

**Date:** 2026-05-17
**Substrates affected:** [S2, with consequences for S3, S4, S6, S8, and S1's bitemporal interface]
**Status:** active

**Decision.** Substrate-2 uses **hybrid-by-layer** reference
resolution:

- **Identity-bearing layers** (asserted_truth, semantic_conditions):
  pinned references **required**. Pinned ref = entity_id + version_seq.
- **Operational layers** (causal_initiation, observation_realization,
  execution_environment): logical references **default**; pinned
  references **allowed as opt-in** for specific reproducibility needs.

**Reference shapes.** Both kinds are typed JSON objects inside JSONB
bodies, with `ref_kind` discriminator. Pinned shape carries
`entity_id` + `version_seq` + informational `external_id`. Logical
shape carries `external_id` only. Resolution semantics: pinned →
S1 bitemporal history at the specified version_seq; logical → S1
current state via (entity_type, external_id).

**Identity_hash canonicalization.** Pinned references contribute
`entity_id` only to the hash; `version_seq` is operational metadata
and excluded. Consequence: S8 may bump `version_seq` forward on
pinned references when the entity evolved compatibly (operational
edit, hash preserved). S8 must escalate to human authority when
the evolution materially changes meaning (semantic edit, hash
changes, approval invalidated). Full canonicalization mechanics
deferred to S2-Q-003 sub-cycle 4.

**Coverage derivation.** `test_claim_coverage` extracts pinned
references from identity-bearing layers only. Operational layer
references are NOT in coverage; they are operational dependencies,
not semantic content. Forward-compat: future operational linkage
layer (per D-056's marker) handles operational dependencies
separately.

**Cross-layer validation is ontology enforcement.** Identity-bearing
layers reject logical references at write time as substrate-level
ontology violations, not implementation routine. Relaxing this
rule would compromise `identity_hash` semantics, blur the
semantic-vs-operational lifecycle distinction (sixth guardrail),
and undermine S8's authority boundary. Validation lives at the
substrate level; changing it is an architectural decision, not a
Pydantic refactor.

**Semantic replay with S8-blessed transitions.** Semantic replay
(per §6.8) follows pinned references forward to current
`version_seq` of the same `entity_id` **only via S8-blessed
transitions** — entity evolutions S8 has validated as semantically
equivalent (those preserving `identity_hash`). Unblessed
transitions surface for human review rather than silent
forward-resolution. This makes semantic-replay's "follow forward"
a deliberate capability anchored on S8's tracking, not a default
behavior.

**external_id drift is multi-mode.** Logical references face
several drift modes — rename, move, replace, namespace shift,
inheritance change, metadata-resolution quirks. S8's drift
detection must handle each mode explicitly. Single-mode framing
(e.g., "deletion-then-recreation") is insufficient.

**Weighted semantic linkage reservation.** `test_claim_coverage`
today uses binary `reference_kind` (subject / condition). Future
may need richer weighting (load-bearing vs supporting refs).
Storage shape preserves room for this evolution without
foreclosing the path.

**Rationale.** The reference design has the deepest pressure of
any decision in S2 so far. Four candidate models considered (per
OPEN_QUESTIONS.md S2-Q-004): pinned everywhere, logical
everywhere, hybrid by reference kind (direct vs traversal-derived),
both with explicit conversion. The hybrid-by-layer cut along the
existing semantic-vs-operational lifecycle boundary was selected
because:

- It maps directly onto the sixth guardrail rather than
  introducing new conceptual machinery
- It realizes D-051's directional note as a structural rule
- It aligns with D-057's mention of pinned/logical coexistence
- It avoids the fuzzy direct-vs-traversal-derived detection
  problem (no detection required; layer is the row column)
- It cleanly integrates with sub-cycle 4 (identity_hash
  canonicalizes pinned refs from identity-bearing layers only)

The "operational layers allow opt-in pinned" refinement was
critical: strict layer enforcement would have rejected legitimate
reproducibility cases (regression tests that explicitly pin
operational entity versions). The refined rule preserves intent
(logical-default communicates the expectation) while accommodating
real authoring needs.

The semantic-replay forward-resolution discipline (S8-blessed
transitions only) was the second critical refinement. The
naive "follow forward" behavior would have created silent semantic
drift during replay; conditioning forward-resolution on S8's
blessing makes the behavior deliberate.

TA refinements integrated:

- Semantic replay must not casually "follow forward" — S8-blessed
  transitions only
- Operational layers default to logical, allow pinned opt-in (not
  strict reject)
- external_id drift is multi-mode (rename / move / replace /
  namespace / inheritance / metadata quirks)
- Cross-layer validation as ontology enforcement (substrate
  commitment, not implementation routine)
- Weighted semantic linkage reservation for future

**Alternatives considered.**

- *Pinned everywhere.* Rejected. Forces S8 mass-rewrites on every
  org change; operational dependencies become brittle to entity
  evolution they shouldn't track. Conflicts with the
  semantic-vs-operational lifecycle distinction.
- *Logical everywhere.* Rejected. Identity-bearing references
  drift silently when entity meaning changes; identity_hash
  becomes ambiguous (does it include resolved content or just
  logical identifier?); historical replay becomes best-effort.
- *Hybrid by reference kind (direct vs traversal-derived).*
  Rejected. The distinction is fuzzy in practice (is the parent
  object in "Account.AccountNumber" direct or traversal-derived?);
  implementation requires detection logic; neither robust nor
  cleanly mappable to existing guardrails.
- *Both with explicit conversion.* Rejected. Storage doubles
  (every reference carries both forms); authoring complexity
  rises; requires the resolution-policy framework that D-057
  reserved for the future. Premature.
- *Strict cross-layer enforcement* (operational layers reject
  pinned). Rejected per TA pushback. Strict rejection would block
  legitimate reproducibility cases.
- *Semantic replay with transparent forward-resolution.* Rejected
  per TA pushback. Forward-resolution must be S8-blessed to
  preserve semantic continuity.

**Downstream consequences.**

- *S2-Q-003 sub-cycle 4 (identity-hash mechanics):* Reference
  canonicalization is constrained — `entity_id` from pinned refs
  in identity-bearing layers only. Sub-cycle 4 builds on this.
- *S2-Q-003 sub-cycle 5 (Pydantic validation):* Reference shape
  validation per (`ref_kind`, layer). Cross-layer rule enforced
  at write time as ontology check.
- *S2-Q-006 (mutation paths and authority):* S8's authority is
  precisely scoped — can bump `version_seq` forward on pinned
  refs when entity evolution is blessed; must escalate when not.
  Anchored mechanically in `identity_hash` preservation.
- *S2-Q-007 (execution-history boundary):* Execution events
  capture which reference resolution mode (historical / semantic)
  was used and the resolved entity versions.
- *S3 (Generation, future substrate):* Generator must know
  current S1 version_seq at authoring time for pinned references
  in identity-bearing layers. Creates coupling between S3 and S1.
- *S4 (Execution, future substrate):* Replay engine implements
  the historical / semantic / S8-blessed-transition semantics
  per §6.8.
- *S6 (Interpretation):* Failure attribution can distinguish
  drift-induced failures (logical refs that resolved to changed
  entities) from execution failures (pinned refs that resolved
  correctly but produced unexpected results).
- *S8 (Evolution):* Multi-mode drift detection (six modes
  enumerated); semantic-blessing tracking (which transitions
  preserved meaning, which didn't); pinned-reference forward
  updates as autonomous when blessed.

**References.**

- `substrate_2_test_representation/SPEC.md` §5 (References to S1
  entities, substantive content added in this commit)
- `substrate_2_test_representation/SPEC.md` §6.8 (Replay modes,
  refined in this commit for S8-blessed semantic forward-resolution)
- D-051 (claim-references-lean-pinned, recipe-references-lean-logical
  directional note realized as structural rule)
- D-056 (storage realization; reference shape lives in JSONB
  bodies)
- D-057 (versioning model; pinned-vs-logical coexistence
  presupposed)
- `substrate_1_semantic_org_model/SPEC.md` (S1's bitemporal
  history; pinned references resolve against this)

---


## D-059 — Identity-hash mechanics + governance contract [S2-Q-003 sub-cycle 4]

**Date:** 2026-05-18
**Substrates affected:** [S2, with consequences for S3, S4, S6, S8]
**Status:** active

**Decision.** Substrate-2's `identity_hash` mechanics are defined
by four locked components plus a six-rule governance contract.

**Hash input scope:**
- `archetype` (row discriminator)
- `claim_kind` (row discriminator)
- Canonicalized `asserted_truth` JSONB
- Canonicalized `semantic_conditions` JSONB

Out of scope: `test_id`, `version_seq`, temporal columns, `status`,
`identity_hash` itself, recipe content, coverage content, and
`body_schema_version`.

**Canonicalization rules (strict, RFC 8785-ish):**
- Alphabetical recursive object-key ordering
- Whitespace stripped between tokens; preserved in string values
- UTF-8 strings; case-sensitive; no escape variation
- Canonical JSON numeric form
- `null` vs missing distinguished; empty arrays vs missing
  distinguished
- Booleans lowercase
- **Array semantics schema-declared:** `ordered` (default) or
  `set` (sort before hash) per field
- Pinned references canonicalize to `{entity_id, entity_type}`
  only (per D-058 constraint; `version_seq`, `external_id`,
  `ref_kind` excluded)

**Hash algorithm:** SHA-256, hex-encoded → 64-char string stored
in `identity_hash` column.

**Canonicalization policy versioning:** new column
`identity_hash_version` (int) on `test_claims` records the
policy version that produced the row's hash. Hash equivalence
scoped to policy version. Policy evolution is explicit and
governed.

**Storage:** canonicalized JSONB stored on row (not original).
Computed at write time by shared application code.

**Six-rule governance contract:**

- **Rule 1 — S8 autonomy boundary.** Autonomous version creation
  requires hash preservation AND `identity_hash_version`
  preservation. Hash-changing or policy-version-changing edits
  require human authority.
- **Rule 2 — Approval invalidation.** Hash change between
  versions (or policy-version change without re-hashing under
  common policy) → predecessor approval not carried forward; new
  version begins `draft`. Mechanical.
- **Rule 3 — S8 evolution through entity changes (two-gate
  evaluation; refines D-058).** S8 autonomous update through S1
  entity evolution requires both Gate 1 (hash preservation —
  mechanical, passes by construction since `version_seq` not in
  hash) AND Gate 2 (entity-evolution semantic compatibility —
  judgmental; S8-design territory; entity-lineage does NOT
  guarantee semantic compatibility). Gate 2 failure → human
  review per D-058 unblessed-transition discipline.
- **Rule 4 — Cross-test semantic equivalence (scoped).** Same
  `identity_hash` AND same `identity_hash_version` →
  semantically equivalent under that policy. Cross-policy
  comparison requires explicit re-hashing.
- **Rule 5 — Schema migration discipline.** Body schema
  migrations declare canonical-form preservation explicitly.
- **Rule 6 — Canonicalization policy migration.** Policy
  evolution is governance-level. Re-hashing existing rows under
  new policy is explicit operation.

**Semantic projection fields reservation.** V1 hashes entire
canonicalized body. Future: support schema-declared per-field
hash-contribution annotation (`semantic` vs `projection`).
Storage shape preserves room; no v1 implementation.

**Rationale.** Sub-cycle 4's scope was elevated by D-057 from
"compute a hash" to "define governance for what counts as
semantic vs operational edit." The canonicalization policy
mechanically determines approval invalidation, S8's autonomous
authority boundary, and cross-test semantic equivalence reasoning.

The four critical refinements during design:

- **Strict canonicalization** (null/missing distinguished, etc.)
  selected over lenient. Strict creates spurious hash differences
  for content "obviously the same" to humans, but lenient creates
  subtle semantic-equivalence issues. Strict is more defensible.
- **Versioned canonicalization policy** instead of immutable.
  Treating canonicalization as immutable forecloses learning;
  versioning makes policy evolution explicit and governed.
- **Array semantics schema-declared** instead of universally
  ordered or universally sorted. Arrays may represent sequences
  (ordered) or sets (unordered); canonicalization cannot guess.
  Schema declares per field.
- **Two-gate evaluation for S8 entity evolution** refining
  D-058. Hash preservation alone is insufficient — entity-lineage
  (shared `entity_id`) does NOT guarantee semantic compatibility.
  Gate 2 (semantic compatibility) is a separate judgmental
  check, deferred to S8 design.

TA refinements integrated:

- Array semantics must become schema-defined (not universal
  default)
- Canonicalization policy versioned, not immutable
- Semantic projection field reservation
- Equivalence scope qualified to canonicalization-policy version
- Entity-lineage ≠ guaranteed semantic compatibility (refined
  Rule 3 framing)

**Alternatives considered.**

- *Lenient canonicalization* (null ≡ missing; whitespace
  normalized everywhere). Rejected. Creates subtle
  semantic-equivalence issues that violate the governance
  contract's clarity.
- *Universal array sort* (always sort arrays). Rejected.
  Conflates ordered sequences with unordered sets. Schemas know
  which is which.
- *Universal array order preservation* (never sort). Rejected.
  Same content in different order would hash differently for
  set-semantics arrays — false negatives in equivalence reasoning.
- *Immutable canonicalization policy.* Rejected per TA. Real-world
  evolution requires the policy to evolve; immutable forecloses
  learning.
- *Include `version_seq` in pinned-ref canonicalization.*
  Rejected per D-058. Violates D-058's lock.
- *Include `body_schema_version` in hash.* Rejected. Storage
  metadata, not semantic content. Migration discipline preferred
  over hash invalidation on every schema bump.
- *MD5 / SHA-1 hash.* Rejected. Insufficient collision resistance.
- *Treat S8 entity evolution as single-gate (hash preservation
  only).* Rejected per TA. Entity-lineage doesn't guarantee
  semantic compatibility; Gate 2 needed.

**Downstream consequences.**

- *S2-Q-003 sub-cycle 5 (Pydantic validation):* Array-semantics
  declarations live in Pydantic models. Models declare per-field
  `ordered`/`set`. Canonicalization reads schema to apply
  per-array sorting.
- *S2-Q-006 (mutation paths and authority):* Authority boundary
  is now fully mechanical for Gate 1. Human-edit / S3-regenerate
  / S8-rewrite are each evaluated against hash preservation.
  Gate 2's semantic-compatibility check is S8-territory but
  framed here.
- *S3 (Generation, future substrate):* Generator computes hash
  at write time; may use cross-test equivalence (Rule 4) for
  dedup-check before generating new claim.
- *S4 (Execution, future substrate):* Semantic replay's
  S8-blessed-transition mechanism (D-058 §6.8) is anchored on
  both gates of Rule 3.
- *S6 (Interpretation):* Hash equivalence (Rule 4) supports
  failure-attribution clustering — "tests with this hash all
  fail" is a queryable signal.
- *S8 (Evolution):* Must implement Gate 2's machinery (semantic
  compatibility check on entity evolution). Specification of
  Gate 2 is S8-design territory; this decision provides the
  framework but defers the mechanism.

**References.**

- `substrate_2_test_representation/SPEC.md` §6.3 (canonicalization
  mechanics and governance contract, substantive content added)
- `substrate_2_test_representation/SPEC.md` §4.1 (`test_claims`
  table — new `identity_hash_version` column)
- D-051 (identity model; hash scope rests on this)
- D-056 (storage realization; hash column placement)
- D-057 (versioning model; hash semantics anchored here)
- D-058 (reference model; reference canonicalization constraint;
  S8-blessed-transitions refined by two-gate evaluation)

---


## D-060 — Validation layering and the Semantic Transaction Coordinator [S2-Q-003 sub-cycle 5]

**Date:** 2026-05-18
**Substrates affected:** [S2, with consequences for S3, S4, S6, S8]
**Status:** active

**Decision.** Substrate-2's validation operates across three
complementary enforcement layers, coordinated through a named
substrate-level component (the Semantic Transaction Coordinator)
that maintains consistency invariants spanning multiple tables,
body schemas, and validation layers within write transactions.

**Three complementary enforcement layers (not hierarchical):**

- **DB layer** — substrate-critical structural invariants
  (discriminator enums, PK/FK integrity, CHECK constraints).
  Un-bypassable; slow to evolve.
- **Pydantic layer** — semantic content validation (body shape,
  cross-field rules, ontology enforcement, reference shapes).
  Bypassable by raw SQL; fast to evolve.
- **Schema layer** — per-body type definitions, semantic field
  descriptors, discriminator dispatch.

Substrate-critical invariants are deliberately double-enforced
across DB and Pydantic layers; the coordination cost is accepted.

**Pydantic model organization:** two-level discriminator dispatch
(row discriminator → family of body models; `body_schema_version`
→ specific version). Discriminated unions throughout.

**Reference type hierarchy with semantic role preservation:**

- `PinnedRef` — structural shape (entity_id + version_seq +
  external_id)
- `LogicalRef` — structural shape (external_id only)
- `IdentityBearingRef(PinnedRef)` — **distinct type**, not alias,
  carrying the semantic-role marker for identity-bearing pinned
  refs
- `OperationalRef = Union[PinnedRef, LogicalRef]` — operational
  layer ref type permitting either kind

D-058's hybrid-by-layer rule becomes structural type enforcement:
identity-bearing body models declare fields with
`IdentityBearingRef`; operational body models declare fields
with `OperationalRef`. Cross-layer violations fail Pydantic
validation as type mismatches.

**Semantic field descriptors via `Annotated[T, Marker]`
uniformly.** Today's marker: `ArraySemantics.SET` per D-059.
Trajectory: hash-contribution annotations (per D-059 §6.3.10
reservation), identity-contribution annotations, future markers.
Single mechanism; substrate tooling reads field metadata via
Pydantic introspection.

**The Semantic Transaction Coordinator** is a named
substrate-level component coordinating consistency invariants
across substrate-2's multiple tables and validation layers. All
API-driven writes route through it. The Coordinator has
architectural status — it is a named component, not
implementation glue. Future invariants from new sub-cycles or
substrates are added as named coordination steps.

**Hash computation hooks** live in the Coordinator (via shared
pure functions), not in Pydantic models, not in DB triggers.
Hash never recomputed on read within a given
`(identity_hash, identity_hash_version)` regime; cross-regime
comparison requires explicit re-hashing per D-059 Rule 6.

**Read-path error types distinguished:**

- `SchemaIncompatibilityError` — no Pydantic model exists for
  `(kind, body_schema_version)`. Graceful degradation; surface
  raw JSONB with warning. Indicates substrate-library version
  mismatch or missing migration.
- `BodyCorruptionError` — model exists but body fails validation.
  Incident-level. Log + alert; surface degraded; investigate
  (storage corruption, out-of-band edit, bug).
- `OntologyViolationError` (write-time) — cross-layer ref-kind
  rule violated.
- `ValidationError` (Pydantic standard) — routine write-time
  validation failure.

Distinguishing schema-incompatibility from corruption matters
for cross-substrate-version compatibility — a reader using an
older substrate library handles the schema-incompatibility
gracefully rather than crashing.

**Migration handling** (body-schema-version and
identity-hash-version both): governance-level operations with
explicit canonical-form-preservation declarations per D-059 Rule
5. Backfill applies migration → re-validates → re-canonicalizes
→ re-hashes → writes new row → records provenance event.

**Rationale.** Sub-cycle 5 composes existing commitments (from
D-051 through D-059) into a coherent validation architecture
with a named coordination point. The architectural questions
were largely answered by prior decisions; this sub-cycle locks
how they are implemented in Pydantic and where each layer of
validation lives.

The six critical refinements during design:

- **Complementary layers, not hierarchy.** Initial framing
  ("DB floor / Pydantic ceiling") suggested vertical ordering.
  Reality is complementary scopes; rephrasing prevents misreading.
- **`IdentityBearingRef` as distinct type, not alias.** Preserves
  semantic-role distinction from structural shape; enables
  documentation, evolution, and tooling introspection.
- **`SchemaIncompatibilityError` distinct from
  `BodyCorruptionError`.** Different failure modes with different
  mitigations; cross-substrate-version compatibility requires
  the distinction.
- **Semantic field descriptors as emerging trajectory.** Today's
  array-semantics marker is one of an emerging family; use
  `Annotated` uniformly to converge toward a coherent framework
  rather than proliferate bespoke patterns.
- **Hash trust scoped to regime.** Within a given
  `(identity_hash, identity_hash_version)` regime; cross-regime
  requires explicit re-hashing.
- **Semantic Transaction Coordinator as named substrate-level
  component.** Architectural status, not implementation glue;
  coordinates the substrate's consistency invariants
  transactionally; named in architectural diagrams.

TA refinements integrated:

- Floor/ceiling hierarchy wording replaced with
  complementary-layers framing
- `IdentityBearingRef` preserved as distinct semantic-role type
- `SchemaIncompatibilityError` vs `BodyCorruptionError`
  distinction at read time
- Trajectory toward semantic field descriptors acknowledged
- Hash trust qualified by hash-version regime
- Orchestrator elevated to Semantic Transaction Coordinator
  (named substrate-level component)

**Alternatives considered.**

- *Hierarchical layering with DB as foundation.* Rejected per TA.
  Misrepresents the architectural relationship; layers are
  complementary, not stacked.
- *`IdentityBearingRef` as type alias.* Rejected per TA.
  Collapses semantic-role information into structural shape;
  loses documentation, evolution, and introspection benefits.
- *Single read-time error type for all body validation failures.*
  Rejected per TA. Conflates schema-incompatibility (graceful
  degradation) with corruption (incident); blocks
  cross-substrate-version compatibility.
- *Bespoke patterns per semantic field annotation.* Rejected.
  Proliferating mechanisms (some via `json_schema_extra`, some
  via decorators, some via Annotated) makes substrate-level
  introspection brittle and fragments the schema-evolution
  story.
- *Procedural cross-layer validator instead of structural type
  enforcement.* Rejected. Structural enforcement via the type
  hierarchy makes ontology rules part of the type system rather
  than an ad-hoc procedural check; harder to bypass accidentally.
- *Hash computation in Pydantic computed_field or DB triggers.*
  Rejected. Hash spans multiple bodies + row columns, awkward
  in Pydantic; DB-side canonicalization is brittle in PL/pgSQL.
  Pure functions in Coordinator are testable, evolvable, and
  language-portable.
- *Orchestrator as "write helper" / implementation glue.*
  Rejected per TA. Under-names the architectural role of a
  component that coordinates substrate-level invariants;
  promotion to named component reflects reality.

**Downstream consequences.**

- *S2-Q-006 (mutation paths and authority):* The Coordinator is
  the locus where mutation paths (human edit, S3 regenerate,
  S8 autonomous rewrite) are routed; identity-continuity rules
  become explicit Coordinator policies.
- *S2-Q-009 (outward surfaces):* S3, S4, S6, S8 APIs interact
  with the substrate through the Coordinator. API shape derives
  from Coordinator's write/read interfaces.
- *S3 (Generation):* Generator uses cross-test equivalence
  (per D-059 Rule 4 + Coordinator's hash-equivalence query) for
  dedup-check before generating new claims.
- *S4 (Execution):* Read-path error types
  (`SchemaIncompatibilityError`) support cross-substrate-version
  resilience.
- *S6 (Interpretation):* Failure-attribution clustering uses
  Coordinator's hash-equivalence queries.
- *S8 (Evolution):* Gate 2 machinery (per D-059 Rule 3) lives
  outside the Coordinator's transactional scope (it is
  judgmental, not transactional) but interacts with Coordinator
  for resulting autonomous writes.

**References.**

- `substrate_2_test_representation/SPEC.md` §4.7 (validation
  layering and Semantic Transaction Coordinator, substantive
  content added in this commit)
- `substrate_2_test_representation/SPEC.md` §5.5 (cross-layer
  ontology enforcement, refined to reference structural type
  hierarchy per §4.7.3)
- `substrate_2_test_representation/SPEC.md` §6.3.10 (semantic
  field descriptors trajectory framing added)
- `substrate_2_test_representation/SPEC.md` §6.3.11 (hash trust
  scoping clarified)
- D-051 (identity model — the Coordinator's coordination
  responsibilities derive from this)
- D-056 (storage realization — multi-table consistency invariants
  the Coordinator maintains)
- D-058 (reference model — structural type hierarchy implements
  hybrid-by-layer rule)
- D-059 (canonicalization mechanics — hash computation hooks in
  Coordinator)

---


## D-061 — Mutation paths and authority over meaning [S2-Q-006]

**Date:** 2026-05-18
**Substrates affected:** [S2, with consequences for S3, S4, S6, S8]
**Status:** active

**Decision.** Substrate-2 defines three formal mutation paths
(human edit / S3 regeneration / S8 autonomous rewrite), each
routed by the Semantic Transaction Coordinator with per-path
authority rules. S8's invariant is **no autonomous semantic
divergence** — mechanically detected by `identity_hash` change.
Claim approval is governed by hash change (mechanical); recipe
re-approval is a **conservative default** awaiting future
detection mechanisms.

**Three mutation paths:**

- **Human edit** — Authorized human directly edits claim or
  recipe through substrate API. Source of authority: human
  identity.
- **S3 regeneration** — Future generation substrate produces new
  content (same JIRA ticket, new LLM version, different output).
  Autonomous-but-bounded actor.
- **S8 autonomous rewrite** — Future evolution substrate
  responds to S1 entity changes; rewrites recipes, bumps
  pinned-ref `version_seq` when entities evolve compatibly,
  surfaces tests for review when unblessed transitions occur.

All three paths route through the Coordinator. Direct DB writes
bypass authority enforcement (DB-layer invariants still apply
per D-060 §4.7.1, but mutation-path rules do not).

**S8 invariant: no autonomous semantic divergence.** S8 may
autonomously create new claim versions if and only if the new
version's canonical form equals the predecessor's (hash
preserved AND `identity_hash_version` preserved). Identity-
bearing layer mutations are **permitted within this bound** —
e.g., S8 bumping pinned-ref `version_seq` inside `asserted_truth`
JSONB. The invariant is mechanical and operates *within*
identity-bearing layers, not as a fence *around* them. Mutations
producing semantic divergence (hash change) require human
authority regardless of which layer they touch.

This refines the earlier framing ("S8 cannot mutate
identity-bearing content"), which was misleading. S8 *does*
mutate identity-bearing JSONB; what it cannot do is cause
semantic divergence.

**Per-actor authority scope:**

| Actor | Hash-preserving claim writes | Hash-changing claim writes | Recipe writes | Promote draft → approved |
|---|---|---|---|---|
| Human | ✓ (preserves approval) | ✓ (invalidates approval; new in `draft`) | ✓ (new requires re-approval) | ✓ (only humans promote) |
| S3 | ✓ (semantic no-op) | ✓ (writes draft; needs human promotion) | ✓ (new in `generated_unapproved`) | ✗ |
| S8 | ✓ (e.g., version_seq bumps per D-059 Rule 3 two-gate evaluation) | ✗ (`AuthorityViolationError`; surfaces for review) | ✓ (new in `generated_unapproved`) | ✗ |

**Identity continuity and semantic continuity** as orthogonal
dimensions:

- *Identity continuity* = stable identifier (`test_id`,
  `recipe_id`) continuity. Persists across all mutations.
- *Semantic continuity* = `identity_hash` continuity (scoped to
  `identity_hash_version` per D-059 Rule 4). Different hash =
  semantically different test, even with same `test_id`.

A test can preserve identity AND semantic continuity (operational
edit, S8 version_seq bump) OR preserve identity but lose semantic
continuity (hash-changing edit — same test, new meaning). Cannot
lose identity without leaving the mutation framework entirely.

**Trust boundary asymmetry:**

- **Claim approval is mechanical.** Governed by `identity_hash`
  change between versions per D-057 Rule 2 / D-059 Rule 2.
  Preserved on hash preservation; invalidated on hash change.
  Same rule across all actors.
- **Recipe re-approval is a conservative default.** Every new
  recipe version requires explicit re-approval. The reason is
  not that recipes are fundamentally different from claims —
  it is that the substrate currently lacks a mechanical
  detection mechanism for "this recipe edit didn't meaningfully
  change behavior." Without such a mechanism, the safe default
  is re-approval. Future evolution could relax this default;
  reserved as forward-compat.

**Linear supersession preserved.** "Latest" vs "current-approved"
as distinct query notions.

**Current-approved as governance resolution, not status lookup.**
`get_current_approved_claim(test_id)` is a Coordinator governance
operation interpreting version history per substrate rules:

- An approved version is current-approved if no later approved
  version supersedes it
- A deprecation event removes current-approved status
- Cross-policy considerations (per D-059 Rule 6): if approved
  versions exist under different `identity_hash_version`
  regimes, policy-version-aware resolution applies
- Future rules compose into resolution without schema or
  downstream-query change

Downstream substrates use Coordinator interface exclusively;
never query DB directly for current-approved.

**Test-level approval as derived composition** in Coordinator.
`get_test_approval_status(test_id)` returns:
- `fully_approved` — current-approved claim AND at least one
  current-approved recipe
- `claim_approved_recipe_pending` — claim approved, no
  current-approved recipe
- `draft` — claim is draft

**Rollback via supersession, not status mutation.** Status enum
doesn't permit direct un-approval (`approved` → `draft`).
Rollback creates a new draft that supersedes the prior approved;
prior version stays "approved" in history but is no longer
current-approved.

**Edge cases:**

- *Concurrent structural writes:* DB enforces PK uniqueness on
  `(test_id, version_seq)`; one wins, other gets conflict error;
  Coordinator-level retry with new base version_seq.
- *Concurrent semantic conflicts:* v1 = linear supersession;
  whoever writes last wins; losing edit recoverable via
  provenance. Future merge/rebase reserved.
- *S3 hash-preserving regeneration:* Coordinator skips writing
  (no-op); returns reference to existing version.
- *Cross-test semantic equivalence:* substrate provides
  equivalence query (per D-059 Rule 4); S3 may dedup; substrate
  does NOT auto-merge.
- *S8 claim references deleted entity:* surfaces for human
  review per D-058 unblessed-transition discipline; provenance
  records surfaced concern; test stays in current state pending
  human action.
- *Human promotes S3-generated draft:* status change `draft` →
  `approved`; provenance records `claim_approved` event with
  human actor; new version becomes current-approved.

**Rationale.** Sub-cycle 6 is composition over invention. The
authority machinery was fully locked by D-057 (versioning
anchors), D-059 (canonicalization mechanics and six-rule
governance contract), and D-060 (Coordinator as routing point).
What remained: formal definition of the three mutation paths,
identity-continuity framing, per-path approval semantics, edge
case handling.

The six critical refinements during design:

- **S8 invariant as "no autonomous semantic divergence."**
  Initial framing ("no autonomous semantic mutation")
  misdescribed what S8 does — S8 *does* mutate identity-bearing
  JSONB. Refined framing positions hash preservation as the
  universal autonomy rule operating *within* layers, not as a
  fence around them.
- **Recipe re-approval as conservative default.** Initial
  framing as intrinsic asymmetry foreclosed future evolution.
  Refined framing acknowledges the asymmetry is a product of
  current detection capabilities; future evolution could relax
  the default.
- **Current-approved as governance resolution, not simple
  status lookup.** Initial framing under-described what the
  resolution actually does (interprets history per substrate
  rules across policy versions, deprecation events, etc.).
  Refined framing makes the Coordinator's role explicit and
  protects against downstream substrates building incompatible
  direct queries.
- **Concurrent semantic conflicts merge/rebase reservation.**
  Initial framing only addressed structural concurrency;
  semantic concurrency needed acknowledgment as future work.
- **Provenance multi-stream taxonomy reservation.** Initial
  treatment lumped distinct streams under single `event_kind`;
  forward-compat framing reserved for stream classification.
- **Deprecation taxonomy reservation.** Single `deprecated`
  status conflates multiple distinct states; future taxonomy
  reserved.

TA refinements integrated:

- S8 invariant as "no autonomous semantic divergence" (not
  "no semantic mutation")
- Recipe re-approval as conservative default (not intrinsic
  asymmetry)
- Current-approved as governance resolution (not status lookup)
- Concurrent semantic conflicts merge/rebase acknowledgement
- Provenance multi-stream taxonomy reservation
- Deprecation taxonomy reservation

**Alternatives considered.**

- *S8 invariant as "no autonomous semantic mutation"
  (layer-based fence).* Rejected per TA. Misrepresents what S8
  actually does; under-describes its bounded authority.
- *Recipe re-approval as intrinsic asymmetry vs claim.*
  Rejected per TA. Forecloses future evolution; framing as
  conservative default is correct.
- *Current-approved as `WHERE status='approved' ORDER BY
  version_seq DESC LIMIT 1` query.* Rejected per TA. Bypasses
  governance; doesn't handle deprecation, policy-version
  scenarios, or future composition rules. Coordinator
  governance resolution is the principled answer.
- *Concurrent semantic conflicts: substrate auto-merges.*
  Rejected. Substrate's job is to maintain consistency
  invariants; semantic merge is judgmental. Linear supersession
  + future merge/rebase reservation is the principled v1
  position.
- *Direct un-approval transition (`approved` → `draft`).*
  Rejected. Status mutation without supersession breaks audit
  trail; rollback via new draft is cleaner and preserves history.
- *Test-level approval denormalized to schema column.*
  Rejected. Derivation rules may evolve (e.g., when
  cross-recipe-kind approval semantics emerge); composition
  in Coordinator is forward-compatible.
- *Provenance multi-stream as v1 implementation.* Rejected.
  Current single enum sufficient; framing reservation prevents
  ad-hoc taxonomy growth without committing v1 schema change.
- *Deprecation taxonomy as v1 implementation.* Rejected. Same
  reasoning as multi-stream provenance; framing reserves
  coherent future evolution.

**Downstream consequences.**

- *S2-Q-009 (outward surfaces):* API design must expose the
  Coordinator's per-path interfaces (human / S3 / S8). The
  authority enforcement step (D-060 §4.7.6 step 10) becomes
  visible at the API boundary.
- *S3 (Generation, future substrate):* Generator implements
  hash-preserving and hash-changing regeneration patterns;
  uses Coordinator's no-op detection for hash-preserving
  cases; produces drafts for hash-changing cases.
- *S4 (Execution, future substrate):* Distinguishes
  "current-approved" (production execution) from "latest"
  (replay or development) per use case.
- *S6 (Interpretation):* Failure attribution uses
  current-approved for production failures; uses provenance
  history for "test was approved at time T but failed at time
  T+N" attribution.
- *S8 (Evolution):* Must implement Gate 2 machinery (per
  D-059 Rule 3); must surface unblessed transitions per D-058
  rather than attempting hash-changing autonomous writes;
  must handle multi-mode entity drift per D-058 §5.6.
- *Future substrates / sub-cycles:* The four forward-compat
  reservations (recipe approval auto-preservation, merge/rebase,
  provenance streams, deprecation taxonomy) define a coherent
  evolution path; the substrate is designed to accommodate
  these without major refactoring.

**References.**

- `substrate_2_test_representation/SPEC.md` §7 (mutation paths
  and authority over meaning, substantive content added in this
  commit)
- `substrate_2_test_representation/SPEC.md` §2 (cross-reference
  to S2-Q-006 updated for mechanical authority framing)
- `substrate_2_test_representation/SPEC.md` §4.7.6 (write-flow
  extended with authority enforcement step)
- `substrate_2_test_representation/SPEC.md` §4.7.8 (read-path
  error types extended with `AuthorityViolationError`)
- `substrate_2_test_representation/SPEC.md` §6.3.9 Rule 1
  (refined phrasing — "no autonomous semantic divergence")
- `substrate_2_test_representation/SPEC.md` §6.6 (approval
  state lifecycle cross-references §7)
- D-051 (identity model — three layers identified as
  identity-bearing or operational; authority framework starts
  here)
- D-056 (storage realization — Coordinator-coordinated tables)
- D-057 (versioning model — approval invalidation Rule 2)
- D-058 (reference model — unblessed transitions, multi-mode
  drift)
- D-059 (canonicalization mechanics — Rule 1 S8 autonomy bound,
  Rule 3 two-gate evaluation)
- D-060 (validation layering — Coordinator as routing point)

---


## D-062 — Execution-history boundary against S4 [S2-Q-007]

**Date:** 2026-05-18
**Substrates affected:** [S2, with consequences for S4, S6, S8]
**Status:** active

**Decision.** Substrate-2's boundary with the future execution
substrate (S4) is the **last-run snapshot** pattern. S2 holds
minimal denormalized state per recipe (latest outcome,
`last_pass_at`, `last_failure_at`) via a new
`test_recipe_runtime_state` table; S4 holds the full evidence and
history. S4 pushes updates to S2 via Coordinator callback;
**S2 never queries S4.** Test-level runtime status is a
**resolution operation** composing recipe-level state with
conservative initial policy.

**The substrate boundary:**

Platform philosophy distinguishes execution (S4's domain),
interpretation (S6's domain), and representation (S2's domain).
S2 must NOT replicate S4's evidence — that would conflate
execution with representation. But S2 benefits from minimal
denormalized state for hot-path queries (S6 attribution
ergonomics, S8 evolution prioritization, UX status display) that
would otherwise force every status query to join with S4.

The boundary commitment: **S2 holds only what it needs for its
own resolution operations; S4 holds everything else.**

**The runtime-state snapshot:**

`test_recipe_runtime_state` table, one row per `recipe_id`
(NOT per recipe version):

- `recipe_id` UUID PK — FK to `test_recipes.recipe_id`
- `last_run_id` UUID — opaque reference into S4
- `last_run_at` timestamp
- `last_run_outcome` enum — passed / failed / errored / skipped
- `last_run_recipe_version_seq` int — which version was run
- `last_pass_at` timestamp NULL — when did this recipe last pass
- `last_failure_at` timestamp NULL — when did this recipe last fail
- `updated_at` timestamp

**Pure snapshot — no aggregate statistics.** No `run_count`, no
pass-rate percentages, no flakiness metrics. These belong to S4
(raw data) or S6 (derived analyses). S2 maintaining them would
muddy the substrate's purpose and add write coordination
overhead on every run report.

**Per-recipe, not per-recipe-version.** Recipes are versioned;
runtime state is not. The `last_run_recipe_version_seq` field
records which version was run, but only the latest outcome is
retained.

**Separate table, not columns on `test_recipes`.** The substrate
boundary must be visible in the schema. Mixing runtime state into
the representation table blurs the boundary.

**Push-based S4 integration:**

S4 reports run outcomes via Coordinator callback:

```
coordinator.report_run_outcome(
    actor=S4,
    run_id,
    recipe_id,
    recipe_version_seq,
    outcome,
    ran_at,
)
```

S2 never queries S4. S4 pushes; S2 ingests. This avoids S2 → S4
dependencies, concentrates write coordination at the Coordinator,
and allows S4 to batch reports if needed.

Idempotent on `run_id`. Re-reporting the same run is a no-op.

**Test-level runtime status as resolution operation:**

`coordinator.get_test_runtime_status(test_id)` returns:

- `passing` — at least one current-approved recipe has
  `last_run_outcome=passed` AND no current-approved recipe has
  `last_run_outcome=failed`
- `failing` — at least one current-approved recipe has
  `last_run_outcome=failed`
- `untested` — no current-approved recipe has a run
- `mixed` — multiple recipes with conflicting outcomes that
  don't fit the above

This is **resolution**, not lookup — composing recipe-level state
per substrate rules. Per D-064, resolution-class operations are
first-class substrate concepts.

**Multi-recipe outcome resolution has acknowledged pressure:**

The §8.4 composition rule is conservative and initial. Multi-recipe
outcomes have genuine ambiguity:

- API recipe (passed) + UI recipe (failed) — passing or failing?
- Primary recipe (passed) + regression recipe (failed) — is the
  primary's outcome canonical?
- 3 recipes, 2 passed, 1 errored — what's the status?

The substrate provides both raw recipe-level state (direct query)
AND derived test-level composition (resolution operation).
Consumers needing different composition policies compose against
raw state rather than the substrate's default.

**Rationale.** Sub-cycle 7 settles the substrate's first
**boundary** with another substrate. Prior cycles (D-051 through
D-061) established substrate-2's internal coherence; this cycle
establishes how that coherent design interfaces with execution
(which is owned by a different substrate per platform philosophy).

The four critical refinements during design:

- **Drop `run_count`.** Initial design included a run-count
  column as cheap denormalization. Refined: aggregate statistics
  belong to S4 (raw) or S6 (derived); S2 maintaining them is
  mission creep and adds write coordination overhead. Pure
  snapshot is the principled position.
- **Separate table, not columns on `test_recipes`.** The
  substrate boundary must be visible in the schema. Mixing
  runtime state into the representation table blurs the
  boundary that makes the platform architecture coherent.
- **Push-based S4 integration.** S2 never queries S4. The
  alternative (S2 pulls from S4) would create cross-substrate
  query dependencies that violate substrate isolation.
- **Multi-recipe resolution pressure acknowledged openly.**
  Initial framing buried the multi-recipe ambiguity as edge
  case. Refined: explicit §8.5 pressure-point framing
  acknowledges that test-level status composition has genuine
  open questions; substrate provides both raw and derived
  state.

**Alternatives considered.**

- *Run-id references only (S4 holds all evidence; every S2 status
  query joins to S4).* Rejected. Forces every "is this test
  passing" query to traverse a cross-substrate join. Hot-path
  performance cost is too high; resolution operations need
  in-substrate state.
- *Per-version pass/fail summary (aggregate counts denormalized
  into S2).* Rejected. Aggregate statistics belong to S4 or S6;
  S2 maintaining them is mission creep. Pure snapshot is
  cleaner.
- *Last-run + run history (last N runs stored in S2).* Rejected.
  Where does N stop? Run history is S4's domain; S2 retaining
  history beyond last-run pulls evidence into the representation
  substrate. Reserved as forward-compat if hot-path needs surface.
- *Columns on `test_recipes` rather than separate table.*
  Rejected per TA. Boundary visibility matters.
- *S2 polls S4 for run outcomes.* Rejected. Creates cross-substrate
  query dependencies; coupling that violates substrate isolation.
  Push-based callback is the principled approach.
- *Run-count column for cheap convenience.* Rejected per TA.
  Statistics belong elsewhere.
- *Single test-level runtime status column denormalized to
  `test_claims`.* Rejected. Test-level status is derived
  composition (per D-061's approval composition pattern); not
  denormalized to schema. Resolution operation is the
  principled placement.

**Downstream consequences.**

- *S4 (Execution, future substrate):* Must implement the
  `report_run_outcome` callback to Coordinator after each run.
  Idempotent on `run_id`.
- *S6 (Interpretation):* Queries `test_recipe_runtime_state`
  directly for raw last-run state; queries Coordinator's
  resolution operation for composed test-level status.
- *S8 (Evolution):* Uses runtime state to prioritize evolution
  work (recently-failing tests; long-untested tests).
- *UX/Dashboard:* Queries Coordinator's resolution operation
  for display.
- *S2-Q-009 (outward surfaces):* Runtime state interfaces
  appear as one of the five interface groups in D-064.

**Forward-compatibility reservations.**

- *Richer runtime-state resolution.* §8.4 composition rule may
  evolve toward recipe priority weighting, primary-recipe
  designation, or outcome-aggregation policies. Substrate
  exposes raw state today; evolved resolution policies layer
  on top without schema change.
- *Run history beyond last-run.* Some S6 attribution scenarios
  may want flakiness detection (run history with pass/fail
  pattern). Today deferred to S4-side queries or a future
  flakiness-detection substrate.

**References.**

- `substrate_2_test_representation/SPEC.md` §8 (execution-history
  boundary, substantive content added in this commit)
- `substrate_2_test_representation/SPEC.md` §4.1
  (`test_recipe_runtime_state` table definition)
- `substrate_2_test_representation/SPEC.md` §4.2 (architectural
  roles table extended)
- D-051 (identity model — recipes as operational entities)
- D-056 (storage realization — Pattern D extended with snapshot
  table)
- D-061 (mutation paths — runtime state outside claim/recipe
  mutation framework)
- D-064 (outward surfaces — Coordinator interfaces for runtime
  state)

---


## D-063 — Requirement linkage [S2-Q-008]

**Date:** 2026-05-18
**Substrates affected:** [S2, with consequences for S3, S6, UX integration]
**Status:** active

**Decision.** Substrate-2 links to external requirement-management
systems (JIRA, etc.) via **external typed references only**. No
ticket content is replicated in PrimeQA; the external system
remains the source of truth. A new `test_requirement_links` table
provides multi-kind linkage (`generated_from` / `verifies` /
`related_to`). Future evolution to registry-based external-system
identification is reserved.

**The external typed reference model:**

Substrate-2's role re requirements is **linkage, not ownership.**
Requirements (JIRA tickets, Linear issues, Azure DevOps work
items) are external to PrimeQA's domain. PrimeQA tests can
reference them for traceability — "this test was generated from
PROJ-1234," "this test verifies PROJ-5678" — but PrimeQA does
not own or replicate ticket content.

Why no content replication:

- Content goes stale (JIRA tickets evolve)
- Mission boundary (project management is a separate concern)
- Sync overhead (when to sync, how to handle conflicts)

**The `test_requirement_links` table:**

- `test_id` UUID — FK to `test_claims.test_id`
- `external_system` enum — `jira` today; extensible
- `external_key` text — e.g., `PROJ-1234`
- `external_version` text NULL — optional version/revision
- `link_kind` enum — `generated_from` / `verifies` / `related_to`
- `linked_at` timestamp
- `linked_by` text — actor
- PK: `(test_id, external_system, external_key, link_kind)`
- Index for reverse lookup: `(external_system, external_key)`

**Multi-kind linkage.** A test may be `generated_from` one
requirement AND `verifies` another. Many-to-many relationship.

**Three link kinds:**

- `generated_from` — S3 generated this test in response to this
  requirement
- `verifies` — this test contributes to verifying this requirement
- `related_to` — loose association catch-all

**No ticket content replicated.** Downstream consumers query the
external system's API directly when they need ticket content.

**Rationale.** The substrate's coherence depends on clear
boundaries. Requirements are external concerns; replicating them
would expand substrate-2's responsibility beyond its scope and
create stale-data problems. The link-only model preserves the
substrate boundary while supporting traceability.

The three critical refinements during design:

- **External typed reference, not first-class entity.** Initial
  candidates included absorbing requirements as a first-class
  S2 entity (mirroring v2.2's `requirements` table). Refined:
  the substrate boundary forbids absorbing concerns that belong
  to external systems.
- **Multi-kind linkage with explicit kinds.** Initial design
  considered a single "linked-to" relationship. Refined:
  `generated_from` / `verifies` / `related_to` capture
  genuinely distinct relationships, each with different
  semantic implications.
- **Registry-based evolution reserved.** Hardcoded enum is fine
  for v1, but future per-tenant external systems may emerge.
  Schema-shape commitment today is "typed identifier" (enum or
  FK), not an irrevocable type choice.

**Alternatives considered.**

- *First-class requirements entity in S2 (mirrors v2.2).*
  Rejected. Expands substrate responsibility; creates stale-data
  problems.
- *Separate substrate for requirements.* Possible future direction
  but unnecessary now — link-only model handles substrate-2's
  needs without requiring a separate substrate.
- *Single "linked-to" relationship without kind discriminator.*
  Rejected. Different relationships have different semantic
  implications (S3 attribution differs from manual verification
  linkage).
- *Replicate minimal ticket metadata (title, status).* Rejected.
  Where does minimal stop? Title is content; status is mutable;
  any replicated data goes stale.
- *Bidirectional sync to JIRA (PrimeQA → JIRA comments).*
  Out of scope. Could be a future integration layer; not S2's
  responsibility.

**Downstream consequences.**

- *S3 (Generation):* When generating a test from a JIRA ticket,
  writes a `test_requirement_links` row with `link_kind=generated_from`.
- *S6 (Interpretation):* When attributing a failure, may surface
  the requirement that the test was generated from.
- *UX:* Queries Coordinator for tests by requirement, displays
  ticket content via direct JIRA API call.
- *v2.2 disposition:* `requirements` table → DROP per D-065.
  Migration: extract test-to-requirement relationships from v2.2
  data into `test_requirement_links`; ticket content discarded.

**Forward-compatibility reservations.**

- *Registry-based `external_system`.* `external_systems` registry
  table could replace the enum if multi-tenant external-system
  configuration emerges.
- *Sprint / release / project associations.* External-system
  concerns; not S2 schema.
- *Bidirectional sync.* Future integration layer; not substrate.

**References.**

- `substrate_2_test_representation/SPEC.md` §9 (requirement
  linkage, substantive content added in this commit)
- `substrate_2_test_representation/SPEC.md` §4.1
  (`test_requirement_links` table definition)
- D-064 (outward surfaces — `list_tests_by_requirement` interface)
- D-065 (v2.2 disposition — `requirements` table → DROP)

---


## D-064 — Outward surfaces [S2-Q-009]

**Date:** 2026-05-18
**Substrates affected:** [S2, with consequences for S3, S4, S6, S8, and all future substrate consumers]
**Status:** active

**Decision.** Substrate-2's outward surface is the **Semantic
Transaction Coordinator**, framed as **semantic OS infrastructure**
rather than as a substrate-internal component. The Coordinator
exposes five interface groups, each with explicit **behavioral
contracts** (idempotency, authority, atomicity, error,
concurrency, asymptotics) as substrate-level commitments. Three
Coordinator-level operations are named **resolution-class
operations** — first-class substrate concepts that compose
substrate rules rather than executing simple queries. Wire format
(Python-direct, gRPC, REST) is unspecified at the substrate
level; behavioral contracts are not.

**The Coordinator as semantic OS infrastructure:**

After D-060 (Coordinator as named substrate component), D-061
(mutation paths routed through Coordinator), and D-062 (runtime
state managed through Coordinator), the Coordinator is no longer
a "substrate-2 component." It is **semantic OS infrastructure** —
the kernel through which all substrate operations route, the
surface against which all consuming substrates build, the locus
where consistency invariants and authority rules are enforced.

Consequences of this elevation:

- Interface stability is **foundational** — changes ripple to all
  consuming substrates.
- Behavioral contracts are first-class architectural commitments,
  not implementation conventions.
- Future substrates (S1 Coordinator, S4 Coordinator) may form a
  Coordinator family with cross-coordinator concerns.
- "Semantic OS infrastructure" is the right framing in
  cross-substrate documentation.

**Five interface groups:**

Organized by consumer concern, not by consuming substrate:

1. **Write interfaces** (actor-aware, authority-enforced) —
   `write_claim`, `write_recipe`, `promote_claim_to_approved`
   (human-only), `deprecate_claim` (human-only),
   `deprecate_recipe` (human-only),
   `surface_unblessed_transition` (S8-only).

2. **Read interfaces** (current-approved vs latest distinction) —
   `get_current_approved_claim` (resolution operation),
   `get_latest_claim`, `get_claim_version`, `list_active_recipes`,
   `select_recipe_for_execution` (resolution operation).

3. **Equivalence and discovery interfaces** —
   `query_equivalent_claims`, `list_tests_affected_by_entity`
   (uses coverage), `list_tests_by_requirement`.

4. **Runtime state interfaces** (per D-062) —
   `report_run_outcome` (S4-only),
   `get_recipe_runtime_state`,
   `get_test_runtime_status` (resolution operation).

5. **Provenance interfaces** — `get_provenance`,
   `get_recipe_provenance`.

**Behavioral contracts per interface:**

Substrate-level commitments, not implementation conventions:

- **Idempotency** — Each interface declares its idempotency key
  (canonical content for `write_claim`; `(actor, recipe_id,
  version_seq)` for `write_recipe`; `run_id` for
  `report_run_outcome`; etc.)
- **Authority** — Per D-061 §7.2; authority violations raise
  `AuthorityViolationError`
- **Atomicity** — Write interfaces are atomic across the
  relevant tables (e.g., claim writes atomic across `test_claims`
  + `test_claim_coverage` + `test_provenance`)
- **Error contracts** — Per D-060 §4.7.8 + D-061
  `AuthorityViolationError`; each interface documents possible
  error types
- **Concurrency** — Writes use DB-level conflict detection;
  reads are transaction-consistent
- **Performance asymptotics** — Hot-path resolution operations
  should be O(constant) or O(log n); discovery operations are
  O(coverage rows for entity)

**Resolution-class operations:**

Three Coordinator interfaces are **resolution-class operations**:

| Operation | Composes |
|---|---|
| `get_current_approved_claim` (D-061) | Status events, deprecation, policy-version scenarios |
| `get_test_runtime_status` (D-062) | Recipe outcomes, approval state, conservative initial policy |
| `select_recipe_for_execution` (this) | Environment matching, priority, approval state, replay mode, S8-blessing |

Distinguished from lookups by composition over substrate rules,
governance/policy implications, and future-extensibility. Named
as a substrate-level pattern; future resolution operations (S6
attribution clustering, S8 evolution prioritization) will follow
this pattern rather than reinvent the architectural slot.

**Wire format reservation:**

The Coordinator's interface and behavioral contracts are the
architectural commitment. Concrete wire formats — Python-direct,
gRPC, REST — are deployment concerns. Wire formats may multiply
(in-process for direct consumers; cross-service for distributed
consumers) without changing the substrate's commitment.

**Rationale.** S2-Q-009 originally framed as "what APIs S2
exposes." Refined framing: the substrate doesn't expose APIs in
the conventional sense; it exposes a **Coordinator surface** with
behavioral contracts. This framing matters because:

- The Coordinator's elevation to semantic OS infrastructure (per
  TA refinement) positions it as platform-foundational
- Behavioral contracts (per TA refinement) are first-class
  commitments, not implementation conventions
- Resolution-class operations (per TA refinement on recipe
  selection) emerge as a recognized pattern across D-061, D-062,
  and this decision

The four critical refinements during design:

- **Coordinator as semantic OS infrastructure.** Initial framing
  as "named substrate-level component" undersells the role after
  Coordinator absorbs mutation routing (D-061), runtime state
  (D-062), and now serves as the full outward surface (this
  decision). Elevation reflects architectural reality.
- **Behavioral contracts as substrate-level commitments.**
  Initial framing left contracts implicit ("wire format
  unspecified" was loose). Refined: contracts are explicit
  per-interface commitments; wire format is downstream of
  contracts.
- **Resolution-class operations named as first-class pattern.**
  Initial framing treated each resolution as ad-hoc. Refined:
  three resolution operations across recent decisions form a
  recognized pattern; naming it prepares for future resolution
  operations.
- **Recipe selection as policy resolution.** Initial framing
  treated recipe selection as deterministic lookup. Refined:
  selection composes environment matching, priority, approval
  state, replay mode, S8-blessing; it's policy resolution, not
  lookup.

**Alternatives considered.**

- *Per-substrate API layer (S3-API, S4-API, etc.).* Rejected.
  Different consuming substrates have overlapping needs
  (S6 and S8 both query coverage); per-consumer APIs would
  duplicate logic. Concern-grouped is the principled
  organization.
- *Coordinator as substrate-2 component.* Rejected per TA.
  Undersells architectural role.
- *Behavioral contracts as implementation conventions.* Rejected
  per TA. Contracts are part of the substrate's commitment;
  treating them as implementation makes them invisible at the
  API boundary.
- *Wire format committed at substrate level (e.g., gRPC).*
  Rejected. Different deployment contexts have different needs;
  substrate's commitment is interface + contracts, not wire
  format.
- *Resolution operations as undistinguished interfaces.*
  Rejected per TA. Naming the pattern prevents future
  resolution operations from being reinvented ad-hoc.

**Downstream consequences.**

- *All consuming substrates (S3, S4, S6, S8):* Build against
  Coordinator interface and behavioral contracts. Direct DB
  access prohibited.
- *Cross-substrate Coordinator concerns:* As future substrates
  develop their own Coordinators, patterns for cross-coordinator
  coordination may emerge. Reserved.
- *API versioning:* Today single-version; changes are breaking.
  Future may need explicit versioning. Reserved.
- *Implementation:* Coordinator implementation is substantial
  engineering work; behavioral contracts inform implementation
  testing.

**Forward-compatibility reservations.**

- *Cross-substrate Coordinator concerns* — distributed
  transactions across substrate boundaries, cross-substrate query
  composition
- *API versioning* — explicit version pinning for backward
  compatibility
- *Behavioral contract evolution* — performance asymptotics and
  concurrency guarantees may strengthen as substrate matures

**References.**

- `substrate_2_test_representation/SPEC.md` §10 (outward surfaces,
  substantive content added in this commit)
- `substrate_2_test_representation/SPEC.md` §4.7.5 (Coordinator
  framing updated to semantic OS infrastructure)
- D-060 (Coordinator established as named substrate-level
  component; this decision elevates further)
- D-061 (mutation routing through Coordinator; current-approved
  as first resolution operation)
- D-062 (runtime state through Coordinator; test runtime status
  as second resolution operation)
- D-063 (`list_tests_by_requirement` as discovery interface)

---


## D-065 — Disposition of v2.2 test-management tables [S2-Q-010]

**Date:** 2026-05-18
**Substrates affected:** [S2, with consequences for v2.2 migration, future "test catalog" and "review workflow" substrates]
**Status:** active

**Decision.** Each v2.2 test-management table is dispositioned
for the v2 substrate-based architecture. Two tables are absorbed
by substrate-2; three are dropped; four migrate to orthogonal
substrates (TBD); one is dropped in favor of S8 territory. The
dispositions reflect an **intentional architectural trade-off** —
short-term v2.2 feature parity sacrificed for long-term
substrate coherence. Migration execution is post-Phase-3
implementation work.

**Disposition vocabulary:**

- **ABSORB** — Content moves into substrate-2's new schema.
- **DROP** — Content is not retained (or is replaced by a
  mechanism that doesn't require migration).
- **MIGRATE** — Content lives in a separate (TBD) substrate.

**Per-table disposition:**

| v2.2 Table | Disposition | Rationale |
|---|---|---|
| `sections` | MIGRATE | Organizational concern; future "test catalog" substrate. |
| `requirements` | DROP | External typed reference replaces (per D-063); no PrimeQA-side replication. |
| `test_cases` | ABSORB | Replaced by `test_claims` + `test_recipes` (per D-056). |
| `test_case_versions` | DROP | Effective-time supersession replaces (per D-057). |
| `test_suites` | MIGRATE | Curation concern; future "test catalog" substrate. |
| `suite_test_cases` | MIGRATE | Same as `test_suites`. |
| `ba_reviews` | MIGRATE | Workflow concept; future "review workflow" substrate. |
| `metadata_impacts` | DROP | S8 territory in v2; derived from S1 bitemporal history. |

**Intentional architectural trade-off:**

The four MIGRATE dispositions create an explicit gap:
substrate-2 v1 doesn't handle sections, suites, or BA reviews.
Teams using v2.2 features in those areas have a feature gap
during transition.

This is **not a pressure point to be mitigated — it is a
deliberate architectural commitment.** The substrate's
coherence is more valuable than short-term feature parity.

- Short-term cost: v2.2 features unavailable in v2 until
  orthogonal substrates ship
- Long-term gain: each concern lives in its own substrate with
  clean boundaries; future evolution of each concern happens
  independently

Each MIGRATE-targeted concern represents a *separate substrate's
responsibility*. Absorbing them into S2 would compromise the
substrate boundary that makes the platform architecture coherent.

**The gap is real; the gap is acceptable; the gap is intentional.**

**Migration strategy (high-level):**

For ABSORB-dispositioned content:

- v2.2 `test_cases` + `test_case_versions` → v2 `test_claims` +
  `test_recipes` via S3-assisted decomposition. Each v2.2 test
  → claim + one or more recipes per the six-layer model. The
  procedural steps in v2.2 become recipe bodies; the asserted
  truth must be extracted, often via LLM-assisted parsing.

For DROP-dispositioned content:

- `requirements` content not migrated; instead,
  `test_requirement_links` populated from v2.2's
  test-to-requirement relationships.
- `test_case_versions` content not migrated; effective-time
  supersession replaces; v2.2 version history is provenance-only
  (recorded as `claim_created` events in v2 provenance).
- `metadata_impacts` content discarded; S1 + S8 reconstruct as
  needed.

For MIGRATE-dispositioned content:

- Out of substrate-2's v1 scope. Migration deferred until
  receiving substrates ship. v2.2 tables can be retained
  in-place under separate ownership during transition.

**Detailed migration execution** (data scripts, validation,
rollback) is implementation work post-Phase-3.

**Rationale.** S2-Q-010 walks each v2.2 table and decides its
fate. The walk is mostly mechanical given prior decisions:

- `test_cases` + `test_case_versions` are exactly what S2's
  data model replaces (D-056 + D-057) → ABSORB / DROP
- `requirements` is exactly what D-063 dispositions externally
  → DROP
- `metadata_impacts` is exactly what S8 covers from S1 → DROP
- `sections`, `test_suites`, `suite_test_cases`, `ba_reviews`
  are concerns outside substrate-2's domain → MIGRATE

The decision's substance is the **intentional architectural
trade-off** framing. v2.2 had bundled all these concerns into
test-management tables; v2 substrate-based architecture
deliberately splits them along clean boundaries even at the cost
of short-term parity.

The single critical refinement during design:

- **MIGRATE as deliberate commitment, not pressure point.**
  Initial framing treated the v2.2 feature gap as a "pressure
  point" requiring acknowledgment as a concern. Refined: the
  gap is a deliberate architectural commitment. Substrate
  coherence trumps short-term parity. Surfacing the trade-off
  explicitly as commitment rather than concern is the
  principled position.

**Alternatives considered.**

- *Absorb all v2.2 tables into substrate-2.* Rejected. Sections,
  suites, BA reviews are not test-representation concerns;
  absorbing them violates the substrate boundary.
- *Retain v2.2 schema alongside v2 substrate-based schema (dual
  data model).* Rejected. Creates dual-write coordination,
  consistency problems, and doesn't progress toward the
  substrate architecture.
- *Defer migration until all orthogonal substrates ship (no v1
  release).* Rejected. Substrate-2 has independent value;
  shipping it first builds momentum and proves the substrate
  pattern.
- *Treat v2.2 gap as pressure point requiring mitigation.*
  Rejected per TA. The gap is the architecture working as
  designed; framing it as concern misrepresents the commitment.

**Downstream consequences.**

- *Migration team:* Has clear per-table disposition; can plan
  ABSORB migrations (test_cases) and DROP rationales
  (requirements, test_case_versions, metadata_impacts).
- *Future "test catalog" substrate:* Inherits sections,
  test_suites, suite_test_cases when it ships. Its scope must
  cover organizational and curation concerns.
- *Future "review workflow" substrate:* Inherits ba_reviews
  when it ships. Its scope must cover BA review workflows
  distinct from substrate-2's mechanical approval lifecycle.
- *Operations / Product:* Must communicate the intentional v2.2
  feature gap during transition; teams needing migrated
  features must wait for orthogonal substrates.

**Forward-compatibility reservations.**

The MIGRATE dispositions create implicit dependencies on future
substrates:

- *Test catalog substrate* — for `sections`, `test_suites`,
  `suite_test_cases`
- *Review workflow substrate* — for `ba_reviews`

These substrates ship later. Substrate-2 v1 ships first; the
orthogonal substrates ship in subsequent phases as their scope
becomes clear. The substrate roadmap is sequential and
deliberate.

**References.**

- `substrate_2_test_representation/SPEC.md` §11 (v2.2 disposition,
  substantive content added in this commit)
- D-056 (storage realization — test_claims, test_recipes replace
  v2.2 test_cases)
- D-057 (effective-time supersession — replaces v2.2
  test_case_versions)
- D-061 (approval lifecycle as mechanical — distinct from BA
  review workflows)
- D-063 (requirement linkage — replaces v2.2 requirements)

---
