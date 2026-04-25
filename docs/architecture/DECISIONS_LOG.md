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
