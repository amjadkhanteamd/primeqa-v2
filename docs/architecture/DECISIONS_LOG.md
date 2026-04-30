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

*End of Phase 2 additions.*
