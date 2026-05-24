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


## D-066 — Actor taxonomy expansion: `s4` recognized as runtime-state-only callback

**Date:** 2026-05-19
**Substrates affected:** [S2, with consequences for S4]
**Status:** active

**Decision.** Extend the substrate's actor taxonomy from
`Literal["human", "s3", "s8"]` to `Literal["human", "s3", "s8", "s4"]`.
The `s4` value is recognized ONLY by
:func:`check_runtime_state_write_authority` + the
:meth:`SemanticTransactionCoordinator.report_run_outcome` callback
per SPEC §8.3. Every other Coordinator method that accepts an
`actor` rejects `s4` with `AuthorityViolationError`, returned via
the existing `AuthorityDecision` contract.

**Context.** Track D-δ implemented the S4 boundary callback per
SPEC §8.3. The substrate's authority model already treats `actor`
as a first-class parameter with enforcement at every entry point
(humans, S3, S8 are evaluated for claim and recipe writes per
D-061). S4 being a *boundary callback only* (substrate-2 never
queries S4; S4 pushes via Coordinator) needed a mechanical way
to express that constraint consistent with the existing
authority-enforcement pattern.

**Rationale.** Two paths considered:

1. **Special-case S4 as a sentinel inside `report_run_outcome`
   without taxonomy recognition.** Rejected: this would make the
   actor parameter ambiguous (sometimes a member of
   `{human, s3, s8}`, sometimes a `report_run_outcome` sentinel),
   weakening the mechanical-check contract that every other
   substrate method depends on.
2. **Recognize `s4` in the taxonomy and enforce its scope with
   the same allowlist mechanism used by all other entry points.**
   Chosen: keeps the contract uniform; future actor additions
   (S6 for read-only attribution, admin override, etc.) follow
   the same precedent.

**Consequences.**

- `ActorKind` expansions are non-breaking additions when handled
  as `Literal` extensions.
- `check_*_write_authority` functions enforce their actor
  allow-list via `AuthorityDecision` returns (allowing the
  shared `enforce_authority` helper to raise uniformly).
- Authority error messages reference both the rule (e.g.,
  "humans-only per D-ε-1") AND the SPEC section
  ("per SPEC §8.3") for debug context.
- SPEC §7.1 lists S4 alongside the three mutation paths with an
  explicit note that it's a *boundary callback only*, not a
  fourth mutation path.

**Related decisions / sections.**

- D-061 (mutation paths + authority — the existing model that
  this decision extends).
- D-062 (S4 boundary — runtime state semantics that
  `report_run_outcome` implements).
- SPEC §7.1 + §8.3 (updated in the Phase 4 documentation pass).

---


## D-067 — Substrate-2 test convention: local PostgreSQL with per-test transactional rollback

**Date:** 2026-05-19
**Substrates affected:** [S2]
**Status:** active

**Decision.** Substrate-2 adopts a substrate-local integration-test
convention: local PostgreSQL 16.13 + pgvector 0.8.0 + pgcrypto via
Homebrew; per-test SQLAlchemy `Session` fixture bound to a
`Connection` in a transaction, with the transaction rolled back
at teardown. The test database (`primeqa_test_substrate2`) is
created and migrated at pytest-session start; dropped at
session end unless `SUBSTRATE_2_KEEP_TEST_DB=1` is set.
Substrate-1 retains its existing convention (tests against the
actual Railway DB + prefix-based cleanup).

**Context.** Track D-β.2 implemented the 11-step `write_claim`
orchestration, which needed integration tests verifying "fails at
step N, transaction rolls back, no partial state remains."
Substrate-1's convention (tests against the actual Railway DB
with prefix-based cleanup at teardown) cannot express this:
prefix cleanup runs ordered DELETEs *after* the test body
completes; it doesn't model a transaction aborting *during* the
test.

**Rationale.** Substrate-2's write-flow tests have
architecturally different needs than substrate-1's sync tests.
The transactional-rollback pattern is *necessary*, not aesthetic:

- Step-failure tests need to assert that NO rows persist after
  a mid-flow exception. Prefix cleanup cannot verify "no rows
  persisted" — it only cleans up rows that DID persist.
- Concurrent-collision tests deliberately produce
  `IntegrityError` and need clean rollback in the test fixture
  to recover.
- E2E scenarios assert state after each step; an outer
  transaction makes "the previous step's writes are visible to
  the next step's reads" trivial without committing.

Local PostgreSQL already exists in the dev environment (per
project working memory — set up to address Railway-proxy
unreliability for substrate-1's sync tests). This decision uses
it as the substrate-2 test environment without enforcing
substrate-1 migration.

**Consequences.**

- Substrate-1 keeps its existing pattern; no enforced uniformity.
- CI integration requires local PG availability (test DB URL
  configurable via env var `SUBSTRATE_2_TEST_DB_URL`).
- Future substrates choose their test convention based on the
  test patterns they need, not project uniformity.
- The e2e test convention adds **flush-not-commit**: the
  Coordinator's internal `session.flush()` calls make
  intermediate state visible across steps within a scenario
  without committing the outer transaction. Explicit
  `session.commit()` calls in tests would break per-test
  rollback isolation, so the e2e suite avoids them.
- Setup gotchas documented in SPEC §12.3: pgvector + pgcrypto
  extensions, Alembic multiple-heads handling (branch-qualified
  `upgrade` targets).

**Related decisions / sections.**

- D-α §A6 (substrate-isolation principle — testing follows the
  same principle as schema layout: substrate-local
  organization).
- SPEC §12.1 + §12.3 (test convention + setup gotchas
  documented in the Phase 4 documentation pass).

---


## D-068 — In-place mutation for status and priority changes; no version_seq bump

**Date:** 2026-05-19
**Substrates affected:** [S2, with consequences for S3, S6, S8]
**Status:** active

**Decision.** The Coordinator's status-mutation methods
(`promote_claim_to_approved`, `promote_recipe_to_approved`,
`deprecate_claim`, `deprecate_recipe`) and the operational
priority-change method (`change_recipe_priority`) UPDATE the
target row's `status` (or `priority`) column **in place**.
`version_seq` is NOT incremented. State transitions are captured
in `test_provenance` via the appropriate `event_kind`
(`claim_approved` / `claim_deprecated` / `recipe_approved` /
`recipe_deprecated` / `recipe_priority_changed`).

**Context.** Track D-ε implemented the lifecycle-mutation
methods. The design question: should these operations create new
version_seq rows (preserving prior status in history via the row
itself), or mutate the target row in place (with audit-trail
history in `test_provenance`)?

**Rationale.** `version_seq` models semantic supersession per
D-057 — it tracks "this claim's meaning has been replaced by
the next version's meaning." Bumping `version_seq` for status
changes would conflate two distinct lifecycle dimensions:

- *Semantic supersession*: a new identity_hash (or, for recipes,
  a new operational realization). Modeled by `version_seq`.
- *Operational lifecycle*: approval, deprecation, priority
  adjustment. None of these change the body content; all are
  governance / operational metadata.

Bumping `version_seq` for approvals would inflate the counter
for non-semantic reasons, breaking the invariant that
`version_seq` measures *semantic* progression. Provenance is
already the substrate's audit trail (every mutation method
appends an event); the row itself can mutate in place without
losing history.

**Consequences.**

- After `promote_claim_to_approved(test_id, version_seq=1)`,
  querying by `(test_id, version_seq=1)` returns the same row
  with `status='approved'`. Callers reasoning about
  `version_seq` continuity stay correct.
- Provenance carries the full audit trail.
  `event_data["prior_status"]` + `event_data["new_status"]`
  capture the transition; deprecation events additionally
  carry `event_data["reason"]` per D-ε-5.
- The `reason` field for deprecation lives ONLY in
  `test_provenance.event_data` — there is no `reason` column on
  `test_claims` or `test_recipes`. SQL filters against
  `status='deprecated'` won't surface the reason; audit tooling
  reads provenance for it.
- Mechanically verified by integration tests
  (`test_promote_*.py`, `test_deprecate_*.py`,
  `test_change_recipe_priority.py`): each scenario writes,
  applies a status / priority change, then asserts that the row
  count for the target `recipe_id` (or `test_id`) is exactly 1
  with the new state.

**Related decisions / sections.**

- D-057 (effective-time supersession — `version_seq` semantics
  this decision preserves).
- D-061 (mutation paths + authority — status mutations are
  human-only per the conservative default this decision
  inherits).
- SPEC §6.6 + §10.2 (updated in the Phase 4 documentation pass
  to reflect in-place mutation).

---


## D-069 — S3 design begins ahead of substrate-1's deferred-item resolution

**Date:** 2026-05-19
**Substrates affected:** [S1, S3]
**Status:** active

**Decision.** Begin S3 Phase 1 (architectural design) without
waiting for substrate-1 to retire its remaining deferred items
(ValidationRule `REFERENCES` edge population pending a Salesforce
formula parser per substrate-1 corrections-log §17; standard-field
→ `StandardValueSet` detection per §22). Substrate-1's 11 Tier 1
entity types and 13-of-14 populated edge types provide sufficient
design surface; the open items affect specific generation pathways,
not S3's architectural shape.

**Context.** Substrate-1's Phase 2 sync has shipped 11 of the 12
originally-scoped entity types (Object, PicklistValueSet,
PicklistValue, Field, RecordType, Layout, ValidationRule, Profile,
PermissionSet, User, Flow). The 12th (FlowDefinition) was
deliberately unified into Flow per corrections-log §20 — Flow is
versioned natively via bitemporal supersession; the original
"FlowDefinition as separate entity" framing was retired. The
remaining open items in substrate-1 are sub-feature deferrals
(formula parser for `REFERENCES` edges; content-matching heuristic
for standard-field StandardValueSet detection), not missing
entity types.

S3 (Generation) is the substrate whose design is most
architecturally consequential and whose value is most commercially
significant — substrate-2 was specifically designed around S3's
authority constraints. The natural sequence would be substrate-1
100% completion → S3 design, but two factors argue for not
waiting:

1. **Architectural continuity.** Substrate-2's Phase 4 just
   completed; the substrate's design is fresh. S3 design will
   reference substrate-2 constantly (authority model, body
   registry, canonicalization governance, reference discipline).
   Doing S3 design while substrate-2 is freshly in mind produces
   better-grounded architectural decisions.

2. **Work-mode parallelization.** Substrate-1's deferred items
   are mechanical sub-features (parser implementation, heuristic
   population); S3 Phase 1 design is architectural. The two work
   modes are different enough that they can run in sequence with
   no real cost (or in parallel if a second developer joins).

**Rationale.**

- S3 design does not depend on the deferred substrate-1 items
  being implemented — they affect specific `REFERENCES` and
  standard-picklist generation paths, not S3's architectural
  shape.
- Substrate-1 deferred-item completion can run in parallel with
  S3 implementation (Phase 2) or after S3 design.
- The greenfield-vs-evolve strategic decision (how S3 maps onto
  the existing PrimeQA v2 generation surface) is independent of
  either substrate-1 completion or S3 design; it can be
  resolved separately before S3 implementation.
- S3 design's preconditions are captured in
  `docs/architecture/substrate_3_generation/PRECONDITIONS.md`
  for explicit articulation of inherited assumptions.

**Consequences.**

- Substrate-1's deferred items (formula parser, StandardValueSet
  detection) are completed after S3 design (or in parallel if
  Dev B is available).
- If S3 design surfaces a hard requirement on either deferred
  item, that item becomes an accelerated substrate-1 work item.
- S3 Phase 1 design proceeds with awareness of which entity
  types and edge types are currently populated (per PRECONDITIONS
  §1.1–§1.2) and which are deferred (per §1.3).
- Future decision-log readers see the substrate sequence broke
  from the roadmap order for the reasons above.

**Related decisions / sections.**

- D-051 through D-065 (substrate-2 Phase 3 design).
- D-066 through D-068 (substrate-2 Phase 4 implementation).
- `substrate_3_generation/PRECONDITIONS.md` (S3 design ground
  state, including the 11-entity-type + 14-edge-type substrate-1
  inventory).

---


## D-070 — S3 is a constrained interpretation engine bounded by S1 ontology and substrate-2 taxonomy, with refusal as first-class output [Theme 1]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S1, S2, S4, S5, S6, S7, S8]
**Status:** active

**Decision.** Substrate-3 (the Generation Engine) is positioned as
a constrained interpretation engine: it takes structured
requirements (JIRA tickets in v1; the architecture admits other
requirement sources later) and produces substrate-2 records
(claims plus recipes) via an interpretation pipeline grounded in
substrate-1. Seven structural commitments anchor the substrate:

1. **Interpretation, not translation.** Requirements are
   interpreted through S1's ontology (which entities exist, what
   kinds they are) and topology (how they connect — Profile
   inheritance, Flow triggering, validation rule reference
   graph). Interpretation produces a scoped semantic neighborhood
   within S1 that bounds where the LLM reasons. Retrieval is
   mechanics; interpretation is the architectural layer.

2. **Admissible grounding.** Every claim S3 emits is admissibly
   grounded in S1: not merely that referenced entities exist, but
   that the org's actual constraint structure (as modeled at S1's
   current capability tier) supports the claim's assertion. V1
   admissibility-checking is rigorous on what S1 currently
   exposes (type compatibility, picklist value membership,
   permission grants, layout containment) and degrades cleanly
   on the surfaces S1 has deferred (validation-rule formula
   semantics, pending substrate-1 §17 formula parser;
   standard-field StandardValueSet detection per substrate-1
   §22).

3. **Autonomous-but-bounded actor.** S3's authority position
   under substrate-2's D-061 model: writes drafts (claims in
   `status='draft'`, recipes in `status='generated_unapproved'`);
   never auto-promotes; cannot diverge `identity_hash` on
   existing approved tests; same-hash regeneration is
   mechanically no-op via substrate-2's `was_noop=True` response.
   Dedup (`query_equivalent_claims` before write) is an
   architectural concern, not optimization.

4. **Verification bar, not co-authoring.** S3's output bar is
   calibrated for verification by humans — bounded-time,
   structured affirmation — not co-authoring. Output below the
   verification bar refuses rather than emits weakly. This
   forbids the slow-erosion failure mode where review degrades
   into repair work.

5. **Refusal as first-class product surface.** When output
   cannot reach the verification bar, S3 refuses with typed,
   actionable feedback. Refusals carry a typed `RefusalKind`
   discriminator and flow through the same surfaces as drafts.
   A high-quality refusal is informative to the engineer; a
   low-quality refusal silently says "could not generate."
   Refusal quality is measurable parallel to generation quality.

6. **Semantic search space bounded (S3 Guardrail 1).** The LLM's
   semantic search space is bounded by S1's ontology ×
   substrate-2's locked taxonomy. The LLM cannot invent semantic
   content outside this bounded space, and operates
   conservatively within it (ambiguity refuses or surfaces
   disambiguation rather than guesses).

7. **Failure-loud philosophy extended.** Substrate-1's "fail
   loud over hallucinating" and substrate-2's "no autonomous
   semantic divergence" are extended by S3 as: S3 refuses to
   produce output rather than producing structurally valid but
   semantically wrong claims.

Five refusal categories named in Theme 1 (typed `RefusalKind`
taxonomy extends through Theme 2):

- `underspecified-requirement` — requirement itself lacks
  specificity to ground anywhere in S1.
- `no-relevant-context` — requirement is specific but does not
  connect to anything in this org's S1.
- `ambiguous-reference` — requirement references something that
  disambiguates to multiple S1 entities without further input.
- `ungrounded-claim` — proposed claim would not be admissibly
  supported by S1's current constraint structure.
- `structural-validation-failure` — LLM output cannot be coerced
  to a valid substrate-2 body shape after bounded retry.

A sixth — `no-admissible-negative-scenario-found` — is
anticipated for Theme 4 (grounded negatives).

Three Theme 2 design surfaces established here (not resolved):

- Typed `RefusalKind` discriminator with actionable feedback
  shape per category.
- S3-owned generation ledger with schema forward-compatible to
  substrate-2's reserved `get_provenance` /
  `get_recipe_provenance` interfaces; ledger retires into
  substrate-2 on the same commit that ships those interfaces.
- `GenerationOutcome` protocol union admitting drafts, refusals,
  and partial outcomes mixing both.

**V1 archetype-coverage reality.** Substrate-3's
four-discriminator architecture (archetype × claim_kind ×
trigger_kind × recipe_kind) supports all five product archetypes
at full strength. V1 product coverage is layered against current
S1 Tier 1: data-behavior strong, configuration solid, permission
usable, UI minimal (Layout-level only; element-level claims
blocked until S1 Tier 3), integration scoped. The architecture
forecloses none; v1 product reality is honest about current S1
ceiling. Themes 3 and 4 operationalize this.

**Rationale.** The framing emerged through TA-loop refinement
from a weaker opening hypothesis ("S3 discovers what should be
claimed; produces recipes alongside") to the load-bearing
position above. Seven critical refinements during review
tightened the substrate's stance from "LLM-mediated generation
we hope produces good output" to "constrained interpretation
engine where the LLM operates within a bounded semantic space
and refusals are first-class outputs." This is a more demanding
architecture but a more honest one. It is the substrate-3 analog
of substrate-2's architectural thesis: substrate-3's thesis is
that AI-mediated generation is trustworthy only when bounded by
deterministic ontology and substrate-validated structure, and
only when refusal is as architecturally first-class as emission.

**Alternatives considered (during TA-loop).**

- *Requirements as translation rather than interpretation.*
  Rejected per TA refinement 1. Translation framing under-names
  the structured-inference work over S1's ontology + topology
  that must precede claim generation.
- *Reference-existence grounding rather than admissibility
  grounding.* Rejected per TA refinement 2. Existence is the
  weakest possible grounding bar; admissibility against the
  org's actual constraint structure is the real bar and the
  defense against tests that fail for reasons unrelated to the
  bug they're meant to catch.
- *Review as compensating for weak generation.* Rejected per
  TA refinement 4. Review-as-repair collapses the trust loop
  and converges the platform back to manual-authoring tools.
- *Refusals as internal error handling.* Rejected per TA
  refinement 5. Refusals carry product value (actionable
  feedback) that confident-but-wrong output does not; treating
  them as errors hides their value.
- *Generation ledger as deferred sub-decision.* Rejected per TA
  refinement 6. Ledger underpins iterative regeneration,
  refusal continuity, and evaluation infrastructure; it's a
  first-order Theme 2 design surface.
- *Implicit semantic boundedness.* Rejected per TA refinement
  7. Named guardrail is the discipline that keeps prompt design
  and retrieval strategy honest as the substrate evolves.

**Downstream consequences.**

- Theme 2 (generation request shape): Must specify `RefusalKind`
  taxonomy, `GenerationOutcome` union, S3-owned generation
  ledger schema with substrate-2 forward-compat.
- Theme 3 (per-archetype strategies): Admissibility is
  per-archetype-specific. UI archetype is honestly constrained
  by S1 Tier 1.
- Theme 4 (grounded negatives): Negative scenarios are grounded
  against the org's actual rejection-producing constraint
  structure; the formula-parser deferral creates a layered
  admissibility ceiling.
- Theme 5 (LLM architecture): Semantic-search-space guardrail
  constrains prompt design and retrieval scope. Q-004 (tool-use
  vs structured JSON, top-level OPEN_QUESTIONS) is evaluated
  partly against which better enforces conservative LLM
  behavior within bounded space.
- Theme 6 (prompt management): Eval surface measures generation
  quality AND refusal quality on parallel dimensions.
- Theme 7 (quality envelope): Quality has two measurable
  dimensions (emission, refusal) each with acceptance
  thresholds.
- S5 (Knowledge System): Domain Pack integration shapes the
  interpretation layer; S3 consumes from S5, not the inverse.
- S6 (Interpretation): When S6 ships, attribution feedback can
  inform S3's interpretation layer, closing the generation-time
  / execution-time gap on domain-truth checking.
- S8 (Evolution): Recipe-evolution responsibility migrates
  progressively; S3's design bar of "S8-evolvable recipes from
  day one" means no large-scale rewrite is required when
  handoff happens.

**References.**

- `substrate_3_generation/SPEC.md` §2 (substrate boundaries and
  architectural posture — substantive content of Theme 1,
  written in this commit)
- `substrate_3_generation/PRECONDITIONS.md` (S3 baseline; §2.1
  corrected per the get_provenance reservation in commit
  `42cae2d`)
- `substrate_2_test_representation/SPEC.md` §1.1 (substrate-2's
  architectural thesis, which S3 extends), §7 (authority model
  S3 operates under), §10 (Coordinator outward surface S3
  consumes)
- `substrate_1_semantic_org_model/SPEC.md` (ontology and
  capability tier S3 grounds against),
  `PHASE_2_PLAN_corrections.md` §17 (formula-parser deferral
  that limits v1 admissibility)
- `archive/ARCHITECTURE_4_NOTE.md` (carry-forward principles —
  scenario-binds-execution, state-handed-out-not-invented,
  strict-validation-over-silent-recovery)
- D-051, D-061, D-064, D-068 (substrate-2 commitments S3
  operates under)
- D-069 (S3 design proceeds ahead of substrate-1's deferred-item
  resolution)

---


## D-071 — Generation request shape with typed regeneration lineage and three-axis context separation [Theme 2]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S2 provenance integration, S6 attribution, S8 evolution]
**Status:** active

**Decision.** Substrate-3's unit of work is a `GenerationRequest`. It carries one or more typed external requirement references, three separated context axes (semantic / governance / operational), and — for regeneration — a binary `prior_request_id` discriminator with a typed `deltas` payload categorizing the regeneration's causal change.

**Request structural shape:**

    GenerationRequest {
      request_id              UUID
      tenant_id               FK
      requirement_refs        [<typed external-system references; cardinality 1..N>]
      semantic_context        { domain_pack_refs, system_rule_version,
                                s1_version_seq, archetype_hint }
      governance_context      { refusal_policy_version,
                                dismissal_taxonomy_version,
                                transparency_policy_version }
      operational_context     { prompt_template_version, llm_model_identifier,
                                retry_policy, cost_budget_usd, latency_budget_ms }
      prior_request_id        UUID NULL
      deltas                  <typed payload; non-null iff prior_request_id non-null>
      created_at              TIMESTAMPTZ
      completed_at            TIMESTAMPTZ NULL
    }

**Three-axis context separation.** Three orthogonal context categories:

- *semantic_context* — what admissible world state was visible. Domain Packs invoked, system rule version, S1 `version_seq` pinned, archetype hint. Two requests with the same `semantic_context` saw the same semantic world.
- *governance_context* — what behavioral policy regime was active. Refusal policy version, dismissal taxonomy version, transparency policy version. Two requests with the same `governance_context` are governed by the same thresholds, taxonomies, and stability rules.
- *operational_context* — how generation was mechanically executed. Prompt template version, model identifier, retry policy, budgets. Variation here is execution mechanics, not semantic or governance behavior.

This separation enables a clean equivalence algebra:

- Same semantic + same governance + different operational → expected semantic equivalence (substrate-2 `identity_hash` match per D-059) and explanation equivalence (substrate-3 `explanation_hash` match per D-075).
- Same semantic + different governance → expected behavior change (different refusals, different dismissed alternatives) by design, not regression.
- Different semantic → expected semantic divergence by construction.

**Regeneration lineage typed.** `prior_request_id` is the binary discriminator: NULL → fresh request; non-NULL → regeneration. When regeneration, `deltas` carries a typed `regeneration_kind` discriminator over five values:

| regeneration_kind          | Category             | Semantic continuity edge? |
|----------------------------|----------------------|---------------------------|
| `clarification`            | semantic evolution   | yes — clarifies prior refusal or under-specification |
| `grounding_evolution`      | semantic evolution   | yes — S1 state advanced (new entities, formula parser shipped, StandardValueSet linked) |
| `requirement_change`       | semantic evolution   | yes — requirement itself updated |
| `model_experimentation`    | operational          | no — same semantic, different model |
| `eval_replay`              | operational          | no — benchmarking against historical generation |
| `failure_recovery`         | operational          | no — retry after operational failure |

Three semantic-continuity edges migrate into substrate-2 provenance as typed lineage events when `get_provenance` ships. Three operational edges stay in substrate-3's operational observability surface and do not migrate.

The `deltas` payload, per regeneration_kind, carries typed structures appropriate to the kind: `clarification` deltas carry resolved-refusal references; `grounding_evolution` deltas reference the S1 version_seq diff; `requirement_change` deltas reference the updated requirement; `model_experimentation` deltas reference the model identifier change; etc.

**Lineage as queryable substrate property.** The `prior_request_id` chain is first-class semantic continuity infrastructure, not an audit field. Substrate-3 exposes lineage traversal operations (`get_request_lineage(request_id)` returning the ordered chain, filterable by `regeneration_kind` category) to itself and to UX/eval consumers. When substrate-2's `get_provenance` ships, semantic-continuity lineage migrates as a chain of provenance events linked by typed lineage edges.

**Rationale.** The three-axis context separation emerged through TA-loop review surfacing that refusal policy is conceptually neither semantic visibility nor execution mechanics; it is behavioral policy regime. The natural decomposition produces three contexts with clean equivalence algebra and clean substrate-2 migration semantics.

The typed `regeneration_kind` discriminator resolves the lineage-pollution risk: undifferentiated lineage cannot support clean eval, audit, or provenance traversal because semantic continuity and operational experimentation are categorically different.

**Alternatives considered.**

- *Three regeneration modes (fresh / regenerate_from / regenerate_with_clarification).* Rejected. Over-factored; every regeneration is a regeneration with some delta. Binary discriminator plus typed deltas is more honest.
- *Single context envelope.* Rejected. Conflating semantic visibility, governance policy, and execution mechanics produces incoherent equivalence semantics and incoherent substrate-2 migration.
- *Undifferentiated lineage chain.* Rejected. Type-distinguishing semantic continuity from operational experimentation is required for clean eval and audit.

**Downstream consequences.**

- *D-072:* `GenerationOutcome` references its `GenerationRequest`; outcome equivalence is computed against `(semantic_context, governance_context)` invariance.
- *D-074:* Two-surface architecture aligns context separation with substrate boundary — semantic_context + governance structural metadata → semantic ledger → substrate-2 provenance; operational_context + LLM telemetry → operational observability → stays substrate-3.
- *D-075:* `explanation_hash` stability is computed under invariant `(semantic_context, governance_context)`.
- *Theme 3:* Per-archetype strategies operate within context-typed requests; `archetype_hint` constrains scope.
- *Theme 5:* `operational_context` houses LLM call parameterization; Q-004 (tool-use vs structured JSON) resolution operates within operational_context.
- *Theme 6:* Governance context versions operationalize their machinery here.
- *Substrate-2 forward-commitment:* Semantic-continuity lineage edges migrate as typed lineage events when get_provenance ships.

**References.**

- `substrate_3_generation/SPEC.md` §3.2 (request shape and three-axis context separation)
- `substrate_3_generation/SPEC.md` §2.3 (substrate-2 authority model S3 operates under)
- D-070 (Theme 1; S3 as constrained interpretation engine; this entry operationalizes the request shape inside that framing)
- D-059 (substrate-2 identity_hash; semantic equivalence definition substrate-3 inherits)
- D-061 (substrate-2 authority model)
- D-064 (substrate-2 Coordinator outward surface)
- substrate-2 SPEC §10.2 (reserved `get_provenance` / `get_recipe_provenance`)

---


## D-072 — GenerationOutcome protocol with binary draft/refusal kinds, dedup as draft form, and the no-silent-drops invariant [Theme 2]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S2 provenance integration, S6 attribution]
**Status:** active

**Decision.** Substrate-3's output protocol is the `GenerationOutcome` discriminated union with binary kinds: `draft` | `refusal`. Every requirement in a request is explicitly resolved to one outcome — no requirement is silently dropped. Dedup-against-existing-claims is a form of draft outcome with zero new writes, not a third top-level outcome kind. Every outcome — draft or refusal — carries a mandatory `attempted_interpretation` artifact and an `explanation_hash`.

**GenerationOutcome shape:**

    GenerationOutcome {
      outcome_id                  UUID
      request_id                  FK to GenerationRequest
      requirement_ref             <typed external-system reference>
      outcome_kind                'draft' | 'refusal'
      
      // draft variant
      claims_written              [<{test_id, version_seq}>] NULL
      recipes_written             [<{recipe_id, version_seq}>] NULL
      equivalent_existing         [<test_id>] NULL
      
      // refusal variant
      refusals                    [<typed refusal entries>] NULL
      
      // mandatory on every outcome
      attempted_interpretation    <typed structure per D-075>
      explanation_hash            VARCHAR
      
      created_at                  TIMESTAMPTZ
    }

**Binary outcome_kind.** The discriminator is binary because the substrate's posture is binary: a requirement is either resolved (one or more drafts produced, possibly mixed with dedup matches) or refused (the substrate cannot reach the verification bar). Intermediate categories drift toward "couldn't quite decide" — the verification-bar discipline forbids this.

**Dedup as draft form.** When dedup (via substrate-2's `query_equivalent_claims` against the interpreted semantic neighborhood) finds existing claims that satisfy the requirement, the outcome is `draft`:

- `claims_written` — possibly empty (no new claims emitted)
- `recipes_written` — possibly empty
- `equivalent_existing` — populated with the test_ids of existing-satisfying claims

A draft outcome with empty `claims_written` and non-empty `equivalent_existing` is the pure-dedup case. Mixed cases (some new claims, some existing satisfying parts of the requirement) are normal.

**No-silent-drops invariant.** Every requirement in `request.requirement_refs` MUST be resolved by exactly one `GenerationOutcome`. The semantic ledger enforces this structurally: the substrate cannot mark a request `completed_at` without one outcome row per requirement_ref.

This is the architectural defense against the "N drafts produced for M requirements where N < M" failure mode. Every dropped requirement surfaces as either a draft (possibly pure-dedup) or a refusal.

**Mandatory attempted_interpretation on every outcome.** Per D-075, every outcome — draft or refusal — carries a typed `attempted_interpretation` artifact and an `explanation_hash` derived from it. Drafts get attempted_interpretation so reviewers see why this draft and not other admissible candidates. Refusals get it so engineers see what the substrate considered before refusing. This connects to D-070's verification-bar principle: reviewers verify rather than co-author, and attempted_interpretation is the substrate's transparency-grade justification supporting verification.

**Rationale.** The binary discriminator collapsed cleanly from an earlier three-kind framing once dedup was recognized as a successful resolution where the satisfying claim already exists, not a third category. The `equivalent_existing` field preserves full audit information without expanding the discriminator.

The no-silent-drops invariant emerged from Theme 1's verification-bar discipline: review compensating for invisible drops collapses the trust loop. Explicit per-requirement resolution is the defense.

Mandatory `attempted_interpretation` on every outcome (not just refusals) emerged from the transparency-as-product-grade-infrastructure principle: drafts need transparency for verification just as refusals need it for engineer repair.

**Alternatives considered.**

- *Three top-level kinds (draft / refusal / noop_already_covered).* Rejected. `noop_already_covered` is a successful resolution form, not a separate kind.
- *Allowing requirements to silently drop on edge cases.* Rejected. Defense against the failure mode requires explicit resolution per requirement.
- *attempted_interpretation only on refusals.* Rejected. Drafts need transparency for verification too.
- *Allowing mixed outcome_kind per requirement.* Rejected. Per-requirement clarity preserves semantic ledger interpretability.

**Downstream consequences.**

- *D-073:* `RefusalKind` taxonomy lives inside the refusal-variant `refusals` field; each refusal carries its own typed feedback payload.
- *D-074:* The semantic ledger's row-per-(request, requirement) shape derives from this protocol.
- *D-075:* `attempted_interpretation` shape is defined; mandatory-on-every-outcome is enforced by the protocol.
- *Theme 6:* Eval consumes outcome rows; emission quality and refusal quality measurable on parallel dimensions.
- *Theme 7:* Per-requirement resolution discipline is the substrate's measurement primitive.

**References.**

- `substrate_3_generation/SPEC.md` §3.3 (outcome protocol and the no-silent-drops invariant)
- D-070 (Theme 1; verification-bar discipline)
- D-051 (substrate-2 identity model; `claims_written` references substrate-2 typed identity)
- D-061 (substrate-2 authority model; dedup operates within authority constraints)
- substrate-2 SPEC §10.2 (`query_equivalent_claims` interface)

---


## D-073 — RefusalKind taxonomy at six categories with invalidity-vs-policy distinction and refusal-as-governed-behavior [Theme 2]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S2 provenance integration, S6 attribution, S7 conversational]
**Status:** active

**Decision.** Substrate-3 commits to a typed `RefusalKind` taxonomy of six categories at Theme 2 close (seventh anticipated for Theme 4). Each refusal carries a typed actionable-feedback payload appropriate to its kind. Refusals are *governed behavior* — each carries a `refusal_policy_version` from the request's `governance_context` per D-071, making refusal calibration a versioned substrate-3 governance concern, not "natural" generation behavior.

**The six categories at Theme 2 close:**

| RefusalKind                     | Category               | What it signals |
|---------------------------------|------------------------|-----------------|
| `underspecified-requirement`    | invalidity (input)     | Requirement lacks specificity to ground anywhere in S1 |
| `no-relevant-context`           | invalidity (grounding) | Requirement is specific but no S1 entities support its assumptions |
| `ambiguous-reference`           | invalidity (resolution)| Reference disambiguates to multiple S1 entities; needs engineer input |
| `ungrounded-claim`              | invalidity (admissibility)| Proposed claim not admissibly supported by org's constraint structure |
| `structural-validation-failure` | invalidity (output)    | LLM output cannot be coerced to valid substrate-2 body after bounded retry |
| `low-generation-confidence`     | **policy (threshold)** | Proposed claim is admissibly grounded but doesn't reach `refusal_policy_version` confidence threshold |

A seventh — `no-admissible-negative-scenario-found` — is anticipated for Theme 4 as a *policy (scope)* category.

**Invalidity vs policy distinction.** Five invalidity categories are *structural failures* — the proposed claim is structurally wrong (input under-specified, grounding missing, reference ambiguous, claim inadmissible, output invalid). One policy category at Theme 2 (`low-generation-confidence`) is a *governance threshold* — the proposed claim is structurally fine but doesn't meet the calibrated bar.

The distinction is architecturally consequential. Invalidity refusals would be refusals under any reasonable policy. Policy refusals depend on `refusal_policy_version` calibration — the same underlying claim could pass under v1 and refuse under v2. The substrate exposes the distinction so engineers see which refusals are structural vs which are policy-driven.

**Refusal-as-governed-behavior.** Each refusal entry in the `generation_outcomes.refusals` array carries:

- `refusal_kind` — the discriminator (also exposed at the row level per D-074)
- `refusal_policy_version` — from the request's `governance_context` (also exposed at row level)
- `refusal_schema_version` — the version of the feedback_payload typed shape (also exposed at row level)
- `feedback_payload` — the typed actionable feedback, per kind

Row-level exposure of `refusal_kind`, `refusal_policy_version`, and `refusal_schema_version` is the substrate-2 forward-commitment for typed cross-substrate provenance (per D-074). This makes refusal replay a first-class substrate operation: "would this requirement still be refused under policy v2?" is queryable without rerunning generation.

**Per-kind feedback payload shapes:**

    underspecified-requirement: {
      missing_axes:                [<typed axis names — target_object, operation_kind, actor_role, etc.>]
      suggested_clarifications:    [<typed prompts to engineer>]
    }
    
    no-relevant-context: {
      searched_terms:              [<terms extracted from requirement>]
      closest_matches:             [<{S1 entity_ref, similarity_score}>] // omitted if all below threshold
      org_capability_gap:          <optional typed description>
    }
    
    ambiguous-reference: {
      ambiguous_term:              <surface form from requirement>
      candidate_entities:          [<S1 entity_refs>]
      disambiguation_prompt:       <typed clarification engineer can answer>
    }
    
    ungrounded-claim: {
      proposed_claim_kind:         <claim_kind enum value>
      proposed_subject_refs:       [<S1 entity_refs>]
      admissibility_gap:           <typed reason from substrate-authorized vocabulary,
                                    may cite dismissal_reason codes per D-076>
      what_would_unblock:          <optional — references deferred S1 capabilities>
    }
    
    structural-validation-failure: {
      attempt_count:               <number of bounded retries before refusal>
      last_validation_error:       <typed Pydantic validation error from substrate-2>
      llm_output_summary:          <bounded raw output; persistent occurrences flag prompt/model issues>
    }
    
    low-generation-confidence: {
      candidates_considered:       [<{candidate_path_index, confidence_score, threshold_used}>]
      threshold_calibration_basis: <typed reason — what refusal_policy_version defined as threshold>
      disambiguation_prompt:       <typed prompt to engineer if applicable>
    }

Each payload type is itself a typed structure with a `refusal_schema_version`. Schema versions evolve through deliberate substrate-3 design cycles; cross-version comparisons may need migration semantics (substrate-2 provenance design surface when `get_provenance` ships).

**Refusal multiplicity (forward-compat reservation).** V1 ships flat-list refusals per outcome (one refusal-variant outcome may carry multiple `refusal_kind` entries when multiple categories apply — e.g., a requirement that is BOTH underspecified AND has ambiguous references). Future evolution may introduce hierarchy (causality DAG over refusals) or sequencing (repair-path ordering); flat-list schema reserves both non-breakingly via NULL-default fields.

**Rationale.** The `low-generation-confidence` category emerged from review surfacing that the architecture conflated admissibility (mechanical, from S1) with confidence (judgmental, threshold-based). These are categorically different. Surfacing the distinction makes policy-threshold refusals visible as such.

The refusal-as-governed-behavior commitment emerged from review surfacing that refusal thresholds are governance, not generation behavior. Per D-074, refusal_kind, refusal_policy_version, and refusal_schema_version are queryable structural fields at the semantic ledger row level for cross-substrate typed provenance.

**Alternatives considered.**

- *Five categories collapsing `low-generation-confidence` into `ungrounded-claim`.* Rejected. Distinct architectural categories with distinct repair paths.
- *Free-form refusal feedback text.* Rejected. Typed payloads required for UX consistency, eval comparability, refusal replay under policy changes, and cross-substrate provenance typing.
- *Refusals as opaque "could not generate" outcomes.* Rejected per Theme 1 D-070 (refusal as first-class product surface).
- *Refusal policy as operational concern.* Rejected. Refusal policy is governance, not operational mechanics; lives in `governance_context`.

**Downstream consequences.**

- *Theme 4:* Adds `no-admissible-negative-scenario-found` (policy-scope category) with its own typed feedback payload.
- *Theme 6:* Refusal policy version machinery; refusal replay implementation; eval mechanics measuring refusal quality.
- *Theme 7:* Refusal calibration thresholds operationalized; per-category refusal quality on parallel dimensions.
- *D-074:* refusal_kind, refusal_policy_version, refusal_schema_version exposed at semantic ledger row level for cross-substrate typed provenance.
- *S6 attribution:* When S6 ships, refusal categorization may inform attribution categories.
- *S7 conversational:* Refusals are substrate-3's surface for conversational clarification flows.

**References.**

- `substrate_3_generation/SPEC.md` §3.4 (RefusalKind taxonomy)
- D-070 (Theme 1; refusal as first-class product surface)
- D-071 (`governance_context.refusal_policy_version`)
- D-074 (typed cross-substrate provenance for refusal structural metadata)
- D-075 (attempted_interpretation mandatory on refusals)
- D-076 (dismissal_reason taxonomy; refusals may cite dismissal_reasons in `admissibility_gap`)

---


## D-074 — Two-surface ledger architecture with typed cross-substrate provenance commitment [Theme 2]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S2 provenance interface design when get_provenance ships]
**Status:** active

**Decision.** Substrate-3 carries two structurally separate ledger surfaces with distinct lifecycles, retentions, and consumers:

- **Semantic ledger** (S3-owned, retires to substrate-2 provenance when `get_provenance` ships): `generation_requests` and `generation_outcomes` tables. Carries the semantic audit trail and supports cross-substrate typed provenance.
- **Operational observability** (S3-adjacent, stays substrate-3 permanently): `llm_calls` table. Carries operational telemetry. Does NOT migrate to substrate-2 provenance.

The two surfaces are linked by `outcome_id`. Eval joins both surfaces. UX surfaces typically read only the semantic ledger. Operational monitoring reads only the observability surface.

**Semantic ledger schema:**

    generation_requests {
      request_id              UUID PRIMARY KEY
      tenant_id               FK
      actor                   's3'
      requirement_refs        JSONB
      semantic_context        JSONB              -- per D-071 shape
      governance_context      JSONB              -- per D-071 shape
      operational_context     JSONB              -- per D-071 shape
      prior_request_id        UUID NULL FK self
      deltas                  JSONB NULL         -- typed per D-071 regeneration_kind
      created_at              TIMESTAMPTZ
      completed_at            TIMESTAMPTZ NULL
    }
    
    generation_outcomes {
      outcome_id               UUID PRIMARY KEY
      request_id               FK
      requirement_ref          JSONB
      outcome_kind             'draft' | 'refusal'
      -- draft variant fields (NULL when refusal)
      claims_written           JSONB NULL
      recipes_written          JSONB NULL
      equivalent_existing      JSONB NULL
      -- refusal variant fields (NULL when draft)
      refusal_kind             VARCHAR NULL        -- EXPOSED AT ROW LEVEL
      refusal_policy_version   VARCHAR NULL        -- EXPOSED AT ROW LEVEL
      refusal_schema_version   VARCHAR NULL        -- EXPOSED AT ROW LEVEL
      refusals                 JSONB NULL          -- typed feedback payloads array
      -- mandatory on every outcome
      attempted_interpretation JSONB               -- per D-075 shape
      explanation_hash         VARCHAR             -- EXPOSED AT ROW LEVEL
      dismissal_taxonomy_version VARCHAR           -- EXPOSED AT ROW LEVEL (from governance_context)
      created_at               TIMESTAMPTZ
    }

**Critical schema discipline — typed cross-substrate provenance.** The fields `refusal_kind`, `refusal_policy_version`, `refusal_schema_version`, `explanation_hash`, and `dismissal_taxonomy_version` are exposed at the row level, NOT buried inside JSONB. This is the substrate-2 forward-commitment.

When substrate-2's `get_provenance` ships, refusal events migrate from `generation_outcomes` to substrate-2 provenance event rows. Substrate-2 needs these fields as typed structural columns to support:

- Provenance filtering by `refusal_kind`
- `refusal_policy_version` drift detection along a test_id's lineage
- `explanation_hash` drift detection across regenerations
- Decisions about whether two refusal events are comparable under their schema versions

Substrate-2 does NOT interpret `refusals.feedback_payload` contents or `attempted_interpretation` internals — those remain substrate-3-typed and opaque from substrate-2's perspective. The boundary:

> **Substrate-3 owns refusal and explanation *semantics*. Substrate-2 owns refusal and explanation *provenance continuity* through typed structural metadata fields.**

**Operational observability schema:**

    llm_calls {
      call_id                  UUID PRIMARY KEY
      outcome_id               FK to generation_outcomes
      call_sequence_number     INT                -- ordering within an outcome
      input_tokens             INT
      output_tokens            INT
      cost_usd                 NUMERIC
      latency_ms               INT
      result_kind              'valid_output' | 'structural_validation_failure' | 'other_error'
      error_payload            JSONB NULL
      raw_output_truncated     TEXT NULL          -- bounded size for debug
      model_identifier         VARCHAR
      prompt_template_version  VARCHAR
      created_at               TIMESTAMPTZ
    }

Strictly operational data. Retention bounded by storage/cost considerations (archival policy named when storage pressure surfaces). Does NOT migrate to substrate-2.

**Atomicity discipline.** Each LLM call attempt produces one `llm_calls` row immediately. Each completed outcome produces one `generation_outcomes` row at outcome resolution time. If operational write fails after semantic write succeeded, the substrate has the semantic outcome (audit-grade record) and loses telemetry. Telemetry can be lossy; semantics cannot. Write ordering enforces this property.

**Rationale.** Two-surface split resolves three architectural concerns: lifecycle alignment (semantic ledger aligns with test lifecycle; operational observability has bounded retention), substrate-2 migration boundary (clean — substrate-2 absorbs semantics, not telemetry), and access patterns (eval, UX, ops have different needs).

The cross-substrate typed provenance commitment emerged from review surfacing that refusal payload schema belongs to substrate-3 but refusal governance semantics leak into substrate-2 provenance law. Substrate-2 needs structural metadata for provenance continuity without interpreting payload internals. Row-level typed exposure (vs. buried JSONB) is what makes this clean rather than opaque.

**Alternatives considered.**

- *Single three-tier ledger with `generation_attempts` as third tier.* Rejected. Conflates lifecycles and migration boundary.
- *Opaque event_data carrying all refusal structure to substrate-2.* Rejected. Substrate-2 needs typed structural metadata for provenance traversal; opaque event_data breaks cross-substrate audit operations.
- *Substrate-2 interpreting refusal payload internals.* Rejected. Wrong substrate boundary — substrate-2 knows refusal is a category of provenance event, doesn't know what `low-generation-confidence` payload internals mean.

**Downstream consequences.**

- *D-071, D-072, D-073, D-075, D-076:* Row-level typed fields exposed at semantic ledger row level, not in JSONB.
- *Substrate-2 forward-commitment:* When `get_provenance` is designed, it MUST absorb the cross-substrate typed provenance constraint. Substrate-2's provenance event row schema includes `refusal_kind`, `refusal_policy_version`, `refusal_schema_version`, `dismissal_taxonomy_version`, `explanation_hash` as typed structural columns. Substrate-2 design surface for that cycle.
- *Theme 5:* `llm_calls` schema operationalizes here. Tool-use vs single-shot affects `call_sequence_number` semantics.
- *Theme 6:* Eval joins both surfaces; prompt-version comparison reads operational observability; semantic outcome comparison reads semantic ledger.
- *Theme 7:* Quality measured against semantic ledger (emission, refusal, explanation stability); cost/perf measured against operational observability.
- *Forward-compat reservation:* Operational observability archival policy named when storage pressure surfaces.
- *Forward-compat reservation:* Operational observability may eventually move to a future observability substrate. Substrate-3 commitment in v1 is the schema; substrate boundary for the observability table is provisional.

**References.**

- `substrate_3_generation/SPEC.md` §3.5 (two-surface architecture)
- `substrate_3_generation/SPEC.md` §3.9 (typed cross-substrate provenance commitment)
- D-071 (request shape — semantic, governance, operational context fields)
- D-072 (outcome protocol — outcome_kind and field shapes)
- D-073 (RefusalKind taxonomy — refusal structural metadata exposed)
- D-075 (attempted_interpretation; explanation_hash)
- D-076 (dismissal_taxonomy_version exposed)
- substrate-2 SPEC §10.2 (reserved get_provenance; design surface affected)

---


## D-075 — S3 Guardrail 2 (ontology-bound reasoning artifacts), attempted_interpretation shape, and transparency as governed substrate artifact [Theme 2]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S2 provenance integration, S5 knowledge, S6 attribution]
**Status:** active

**Decision.** Substrate-3 commits to two interlocking architectural commitments:

**(a) S3 Guardrail 2 — Ontology-bound reasoning artifacts.** Substrate-3 reasoning artifacts persisted in substrate state may only reference semantic concepts authorized by S1's ontology and substrate-2's taxonomy. They may not introduce durable semantic concepts outside this authorized set. This extends Theme 1's S3 Guardrail 1 (semantic search space bounded) to the substrate's own reasoning vocabulary.

**(b) Transparency as governed substrate artifact.** `attempted_interpretation` is not a debug surface. It is substrate-grade governed behavior with its own equivalence relation (`explanation_hash`), its own stability commitment, and its own substrate-2 forward-compatibility through typed cross-substrate provenance (per D-074).

**Attempted_interpretation structural shape:**

    attempted_interpretation {
      scoped_neighborhood:    [<IdentityBearingRef>]                     // S1 entity refs only
      candidate_paths:        [{
                                path_id:           INT                   // canonical index
                                archetype:         <S2 archetype enum>
                                claim_kind:        <S2 claim_kind enum>
                                subject_refs:      [<IdentityBearingRef>]
                                status:            'selected' | 'dismissed'
                              }]
      dismissal_reasons:      [{
                                path_id:           INT                   // FK to candidate_paths
                                reason_codes:      [<dismissal_reason enum value per D-076>]
                              }]
      selected_path_id:       INT NULL                                   // when outcome_kind = 'draft'
    }

Every field is substrate-authorized typed structure. No free-form prose. No LLM-generated rationale text. No invented entity descriptions. Every concept is either an S1 entity (via `IdentityBearingRef`), a substrate-2 taxonomic value (archetype, claim_kind from locked enums), or a substrate-3 reasoning-vocabulary value (`dismissal_reason` from D-076's bounded enum).

**Guardrail 2 enforcement.** The discipline is enforced structurally: the `attempted_interpretation` Pydantic body validates against the typed schema. LLM output proposing invalid entities, invalid claim_kinds, or invalid dismissal_reasons fails validation and either repairs (bounded retry) or surfaces as `structural-validation-failure`.

> *The LLM may generate invalid alternatives during its reasoning, but the substrate only persists alternatives that pass the same admissibility check as the selected path.*

Invalid LLM-proposed alternatives cost LLM latency (recorded in `llm_calls` for cost accounting and debug) but never reach substrate state. This is fail-loud-over-hallucinating applied to the reasoning artifact, not just the output.

**Transparency as governed substrate artifact.**

Once persisted, `attempted_interpretation`:

- Becomes part of product truth (reviewers depend on it, regeneration consumes it, eval compares it)
- Must remain stable under invariant `(semantic_context, governance_context)` per D-071's equivalence algebra
- Is subject to a typed equivalence relation (`explanation_hash`) and a typed drift event when stability is violated

**Explanation_hash canonicalization.**

A canonical hash computed over `attempted_interpretation` under ordered canonicalization:

- `scoped_neighborhood` — canonical lexicographic order over `IdentityBearingRef`s (by serialized form)
- `candidate_paths` — canonical order by `(archetype, claim_kind, subject_refs_canonical)`; path_ids reassigned to match canonical order
- `dismissal_reasons` per path — canonical alphabetical order by `reason_code`
- `selected_path_id` — refers to canonical index

The hash is computed only over substrate-authorized typed fields. Per Guardrail 2, no free-form content participates by construction. The hash is mechanical, reproducible, and comparable across runs.

**Explanation drift events.**

When same `(semantic_context, governance_context)` regeneration produces a different `explanation_hash` than its lineage parent, the substrate emits a typed explanation drift event into the semantic ledger:

    explanation_drift_event {
      drift_id:                 UUID
      outcome_id:               FK
      prior_outcome_id:         FK to lineage parent
      drift_kind:               'structure' | 'composition' | 'reasoning_path'
                                // structure: scoped_neighborhood changed
                                // composition: candidate_paths set changed
                                // reasoning_path: same candidates, different selected_path or different dismissals
      prior_explanation_hash:   VARCHAR
      current_explanation_hash: VARCHAR
      detected_at:              TIMESTAMPTZ
    }

Detection is mechanical (hash inequality). Categorization (regression / evolution / acceptable variation) is judgmental and deferred to S3-Q-008 (semantic equivalence policy under operational variation, addressed in Theme 5 / Theme 7).

**Transparency_policy_version.** Carried in `governance_context` per D-071. Explicit versioning machinery deferred to Theme 6. V1 ships with `transparency_policy_version='v1'` as implicit calibration: the substrate commits to stable `explanation_hash` under invariant `(semantic_context, governance_context)`, with drift events as the violation signal.

**What v1 does NOT commit to:**

- Free-form prose surfaces in `attempted_interpretation`. V1 ships structured-only because Position B (transparency as governed substrate artifact) requires that everything participating in `explanation_hash` is substrate-validated, and validated prose canonicalization is heavier architecture deferred to a later iteration.
- LLM-generated rationale text alongside structured artifacts. Reserved forward-compat surface.
- Explicit transparency policy version machinery (bump rules, replay semantics for version migration). Deferred to Theme 6.

**Rationale.** Guardrail 2 emerged from review surfacing that `attempted_interpretation` without ontology binding drifts toward shadow semantic authority for substrate-3, undermining substrate-2's status as the only semantic authority. The mechanical defense — restricting reasoning artifact vocabulary to substrate-authorized concepts — keeps substrate boundaries clean by construction.

Transparency-as-governed-artifact emerged from review surfacing that surfaced reasoning becomes product truth, and product truth requires substrate-grade stability commitments. The half-position (some transparency governed, some not) creates an undefended boundary that erodes substrate credibility. Accepting Position B (governed explanation surface) absorbs the design weight as the price of architectural coherence.

This is the substrate-3 analog of substrate-2's D-051: identity is mechanical, not judgmental. For substrate-3, **explanation is mechanical, not judgmental, within substrate-authorized vocabulary.**

**Alternatives considered.**

- *attempted_interpretation as ephemeral debug surface.* Rejected. Inconsistent with substrate-3's trust commitment; users will depend on it whether or not the substrate commits to stability.
- *Free-form prose in reasoning artifacts.* Rejected for v1. Violates Guardrail 2. Forward-compat reservation: prose canonicalization is heavier architecture for a later iteration.
- *Snapshot testing as the only stability mechanism.* Rejected. Snapshot testing detects drift but doesn't give the substrate a typed equivalence primitive. Explanation_hash is the substrate-3 mechanical primitive analogous to substrate-2's identity_hash.
- *Explanation stability as informal product commitment.* Rejected. Position B requires mechanical detection (hash) and typed drift events, not informal commitments.

**Downstream consequences.**

- *D-076:* `dismissal_reason` enum is the substrate-3 reasoning vocabulary that `attempted_interpretation.dismissal_reasons` references.
- *Theme 3:* Per-archetype shape of `scoped_neighborhood` and `candidate_paths` operationalized. Per-archetype dismissal_reason applicability defined.
- *Theme 5:* Multi-stage interpretation implied (Q-004 resolution bounded toward tool-use). `attempted_interpretation` produced as explicit reasoning step, not extracted post-hoc from prose.
- *Theme 6:* Explanation stability is a first-class eval signal. Interpretation-layer versioning (distinct from prompt versioning) becomes a substrate concern.
- *Theme 7:* Explanation drift thresholds calibrated; transparency-stability quality measured on parallel dimension to emission and refusal quality.
- *S3-Q-008 extended:* Semantic equivalence policy now covers `identity_hash` AND `explanation_hash` divergence categorization under invariant `(semantic_context, governance_context)`.
- *Substrate-2 forward-commitment:* `explanation_hash` exposed at semantic ledger row level (per D-074) for cross-substrate provenance traversal.

**References.**

- `substrate_3_generation/SPEC.md` §3.6 (ontology-bound reasoning artifacts; Guardrail 2)
- `substrate_3_generation/SPEC.md` §3.7 (transparency as governed substrate artifact; explanation_hash)
- D-070 (Theme 1; S3 Guardrail 1; this entry adds Guardrail 2)
- D-051 (substrate-2 identity discipline; substrate-3 explanation discipline is the analog)
- D-059 (substrate-2 `identity_hash` canonicalization; substrate-3 `explanation_hash` is the parallel mechanism)
- D-071 (`governance_context.transparency_policy_version`; equivalence algebra)
- D-072 (mandatory `attempted_interpretation` on every outcome)
- D-074 (row-level `explanation_hash` exposure for cross-substrate provenance)
- D-076 (dismissal_reason enum referenced by attempted_interpretation)

---


## D-076 — Dismissal_reason taxonomy as substrate-3 reasoning vocabulary [Theme 2]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S2 provenance integration when get_provenance ships]
**Status:** active

**Decision.** Substrate-3 commits to a bounded, versioned, governance-disciplined enum of `dismissal_reason` codes. This enum is the substrate's reasoning vocabulary — the only new semantic vocabulary substrate-3 introduces beyond what S1 and substrate-2 already authorize. It is governed under the same discipline substrate-2 uses on `claim_kind`: bounded, locked through deliberate D-entries, extended only through substrate-3 design cycles.

**V1 bootstrap enum (8 entries across 5 categories):**

    dismissal_reason: enum {
      // TOPOLOGY — missing or insufficient S1 grounding
      insufficient_grounding,
      no_grant_supports_capability,
      no_constraint_supports_negative,
      
      // ONTOLOGY_INVALIDITY — substrate-authorized taxonomy violation
      type_incompatibility,
      archetype_mismatch,
      
      // RANKING — alternative was preferred or no clean disambiguation
      ambiguous_target_resolution,
      lower_specificity,
      
      // GOVERNANCE — would-be valid but doesn't meet policy threshold
      policy_threshold_not_met,
      
      // CONFIDENCE — reserved category; no v1 entries
      // (confidence dismissals enter as refusal-kind `low-generation-confidence` per D-073,
      //  not as alternative-dismissal reasons, in v1)
    }

Each entry carries a typed `category` meta-property: TOPOLOGY | ONTOLOGY_INVALIDITY | RANKING | GOVERNANCE | CONFIDENCE. CONFIDENCE is a reserved category with no v1 entries; the placeholder reserves the architectural slot for future evolution.

**Discipline rules:**

- *Bounded enum.* No runtime extensibility. Adding entries requires a deliberate substrate-3 design cycle producing a D-entry. Same discipline substrate-2 applies to `claim_kind`.
- *Versioned.* The enum carries `dismissal_taxonomy_version` (v1 at Theme 2 close). Bumps occur only via D-entry. Carried in `governance_context` per D-071; exposed at semantic ledger row level per D-074.
- *Categorized.* Every entry's typed `category` is a property of the entry, not an external mapping. Category structure is part of the locked taxonomy.
- *Non-exclusive.* A dismissed candidate may carry multiple `reason_codes`. Persistence is an array of codes per dismissed candidate (per D-075's `attempted_interpretation.dismissal_reasons` shape).
- *Unordered, unweighted in v1.* Reserved forward-compat: future evolution may introduce ordering (primary vs supporting reasons) or weighting (severity values). V1 schema accommodates non-breakingly via NULL-default fields.

**Why this taxonomy is reasoning vocabulary, not UX enum:**

The taxonomy participates in:

- `explanation_hash` canonicalization (per D-075) — codes are part of the hash input
- Eval comparison across runs — same codes in same canonical order means same explanation
- Refusal feedback payloads — `ungrounded-claim` refusals may cite codes in `admissibility_gap`
- Cross-substrate provenance — `dismissal_taxonomy_version` exposed at row level per D-074
- Regeneration interpretation — `grounding_evolution` regeneration may invalidate prior `insufficient_grounding` dismissals; the substrate reasons about this through the taxonomy

Treating dismissal_reasons as substrate-grade reasoning vocabulary preserves the architectural integrity that Position B (D-075 transparency-as-governed-substrate-artifact) requires.

**Rationale.** The commitment emerged from review surfacing that `attempted_interpretation`'s dismissal_reason field is the durable substrate-3 reasoning vocabulary, and without governance discipline it would drift into a casually-extensible registry that pollutes substrate behavior over time. Specifically pressure-tested: bounded enum vs extensible registry, policy-version coupling, category hierarchy, exclusivity, ordering/weighting — all resolved per the discipline rules above.

The five-category structure lifts structural information into the type system rather than leaving it implicit.

**Alternatives considered.**

- *Free-form text for dismissal reasons.* Rejected. Violates Guardrail 2 (D-075); breaks `explanation_hash` canonicalization; makes eval comparison impossible.
- *Extensible registry pattern.* Rejected. Taxonomy drift becomes semantic behavior drift; this is substrate law territory.
- *Single flat category (no `category` meta-property).* Rejected. Category structure is architecturally meaningful and surfaces in product UX — engineers reading a refusal see "this was dismissed for topology reasons" vs "for ranking reasons" — categorically different repair paths.
- *Mutual-exclusivity at the row level.* Rejected. Real dismissals often carry multiple applicable reasons; forcing exclusivity loses information.

**Downstream consequences.**

- *D-075:* `attempted_interpretation.dismissal_reasons` references this enum; `explanation_hash` canonicalization includes dismissal_reason codes.
- *D-074:* `dismissal_taxonomy_version` exposed at semantic ledger row level for cross-substrate typed provenance.
- *D-071:* `governance_context.dismissal_taxonomy_version` carried in every request.
- *Theme 3:* Per-archetype applicability of dismissal_reason codes operationalized (some codes universal — `ambiguous_target_resolution`; some archetype-specific — `no_grant_supports_capability` applies to permission archetype).
- *Theme 6:* Eval mechanics include dismissal_reason coverage and drift detection.
- *Forward-compat reservation:* CONFIDENCE category reserved with no v1 entries. When confidence-as-dismissal becomes relevant (likely Theme 5 or post-Phase-1), entries may be added under deliberate D-entry.
- *Forward-compat reservation:* Ordering and weighting reserved. V1 schema non-breaking to either extension.
- *Substrate-2 forward-commitment:* `dismissal_taxonomy_version` is part of substrate-2's eventual `get_provenance` typed provenance metadata (per D-074).

**References.**

- `substrate_3_generation/SPEC.md` §3.8 (dismissal_reason as reasoning vocabulary)
- D-070 (Theme 1; S3 Guardrail 1)
- D-071 (`governance_context.dismissal_taxonomy_version`)
- D-074 (row-level exposure for cross-substrate provenance)
- D-075 (Guardrail 2; reasoning vocabulary discipline; `explanation_hash` canonicalization)
- substrate-2 D-052/D-053 (substrate-2 claim_kind taxonomy discipline; substrate-3 dismissal_reason is the architectural analog)

---


## D-077 — Cross-cutting per-archetype framework: four dimensions, shared interpretation context, archetype hint as guidance, dismissal_reason by phase [Theme 3]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for Theme 5 LLM implementation topology]
**Status:** active

**Decision.** Theme 3's cross-cutting design framework operationalizes the locked governance architecture (Themes 1 + 2) per archetype. Four architectural commitments anchor the framework:

**(a) Four dimensions per archetype.** Each of the five archetypes is specified along four dimensions:

1. *Interpretation scope* — which S1 entity types the interpretation layer constructs `scoped_neighborhood` from for this archetype.
2. *Admissibility-checking shape* — what concretely constitutes admissibly grounded (per D-070 §2.4) for the claim_kinds in this archetype, given S1's current capability tier.
3. *Recipe-kind selection* — which substrate-2 recipe_kinds are appropriate for which claim_kinds in this archetype; where the substrate has latitude vs where claim semantics force the selection.
4. *Refusal dominance* — which `RefusalKind` categories tend to dominate in this archetype, informing the Theme 7 quality envelope's expected refusal-rate calibration per archetype.

The four-dimensional framework is the structural pattern; D-078 through D-082 fill in the per-archetype specifics.

**(b) Shared interpretation context across the batch.** Theme 3 commits to the architectural property that the substrate's reasoning has access to all requirements in the batch when scoping neighborhoods and constructing candidate paths. This preserves D-071's batch-capable commitment (cross-requirement awareness for sprint batches) and enables multi-archetype decomposition where one ticket touches multiple archetypes.

*The implementation topology that delivers shared context is resolved in Theme 5.* Substrate-3 does not commit to one-pass interpretation, multi-pass coordination, planner-style with explicit dependency graph, or any other specific orchestration shape at Theme 3. The commitment is to the architectural property (shared context across the batch); the mechanism is Theme 5's design surface.

**(c) Archetype hint as guidance, not constraint.** The `archetype_hint` carried in `semantic_context` (per D-071) is the interpretation layer's prior, not a hard constraint that admits or refuses claims by archetype. The substrate:

1. Uses the hint to bias initial scoping (which S1 entity types to load first, which candidate paths to enumerate first).
2. If grounding succeeds outside the hinted archetype, continues with the better-grounded interpretation and surfaces the detected archetype in `attempted_interpretation.candidate_paths` so the engineer sees the substrate's reading.
3. Refuses only when reinterpretation is itself ambiguous — mapping to existing refusal kinds: `underspecified-requirement` (no archetype grounds well) or `ambiguous-reference` (multiple archetypes ground equally well without disambiguation).

No new refusal kind needed. The substrate serves engineer intent over engineer classification when grounding signal is strong, surfacing the detected archetype transparently.

**(d) Dismissal_reason applicability by phase, not by archetype.** The D-076 dismissal_reason bounded enum applies uniformly across archetypes. Applicability is governed by reasoning *phase*, not by archetype:

| Phase | What happens | Applicable dismissal_reasons |
|---|---|---|
| Interpretation | Scoping neighborhood; enumerating candidates; resolving references | `ambiguous_target_resolution`, `lower_specificity` |
| Grounding | Admissibility checking against S1's constraint structure | `insufficient_grounding`, `no_grant_supports_capability`, `no_constraint_supports_negative`, `type_incompatibility`, `archetype_mismatch` |
| Governance | Policy threshold evaluation | `policy_threshold_not_met` |

Phase is metadata-about-the-taxonomy, not metadata-about-individual-instances; it is documentation of how the bounded enum applies, not new persisted vocabulary (Guardrail 2 unaffected). The 8 D-076 reason codes map cleanly to three phases (six grounding, two interpretation, one governance). Reserved CONFIDENCE category would attach to governance phase if entries are added.

This decouples reasoning vocabulary from archetype implementation. When archetypes evolve, when hybrid claims emerge, when per-archetype interpretation strategies change, the dismissal_reason vocabulary remains stable because its applicability is governed by phase, not by archetype.

**Rationale.** The four refinements emerged from round 2 TA review surfacing real architectural pressure points:

- *Shared context vs one-pass:* The original framing committed to "one-pass interpretation per request" which over-constrained Theme 5 before implementation design. The substantive commitment is *cross-requirement awareness in batch mode*; the mechanism is downstream.
- *Archetype hint as guidance:* Refuse-on-mismatch creates workflow friction masquerading as semantic rigor. Real enterprise tickets are ambiguous and archetypes overlap; the substrate should serve grounding signal over engineer classification.
- *Dismissal_reason by phase:* Per-archetype applicability rules fossilize reasoning behavior tied to current implementation shape. Phase-based applicability decouples vocabulary from archetype implementation.

The four-dimensional framework itself is the structural pattern that emerged from designing the five archetypes individually and recognizing the shared decomposition.

**Alternatives considered.**

- *One-pass interpretation as architectural commitment.* Rejected. Over-constrains Theme 5 implementation freedom; the substantive commitment is shared context, not topology.
- *Archetype hint as hard constraint with refuse-on-mismatch.* Rejected. Creates workflow friction; better product behavior is guidance with override on strong grounding signal.
- *Per-archetype applicable dismissal_reason subsets.* Rejected. Couples reasoning vocabulary to archetype implementation, fossilizing behavior. Phase-based applicability is structurally cleaner.
- *Fewer than four dimensions per archetype.* Considered. Three dimensions (omitting refusal dominance) would be tighter but lose the Theme 7 quality-envelope calibration signal. Four dimensions is the right factorization.

**Downstream consequences.**

- *D-078 through D-082:* Each per-archetype D-entry specifies the four dimensions for that archetype.
- *Theme 5 (LLM integration):* Implementation topology for shared interpretation context resolved here. Tool-use vs structured JSON vs planner-style is a Theme 5 decision against the shared-context requirement.
- *Theme 6 (prompt management):* Eval expectations include per-phase dismissal_reason coverage and per-archetype refusal dominance.
- *Theme 7 (quality envelope):* Per-archetype expected refusal rates inform calibration thresholds; quality envelope is per-archetype, not uniform.
- *UX:* Surfacing detected archetype when overriding hint is a UX commitment for transparency.

**References.**

- `substrate_3_generation/SPEC.md` §4.2 (cross-cutting framework — substantive content)
- D-070 (Theme 1; constrained interpretation engine; archetype × claim_kind × trigger_kind × recipe_kind discriminator structure)
- D-071 (Theme 2; `archetype_hint` in `semantic_context`; batch capability)
- D-072 (Theme 2; outcome protocol; `attempted_interpretation` shape including `candidate_paths`)
- D-073 (Theme 2; refusal taxonomy)
- D-075 (Theme 2; Guardrail 2; reasoning artifact discipline)
- D-076 (Theme 2; dismissal_reason bounded enum; this entry specifies its applicability semantics by phase)

---


## D-078 — Data-behavior archetype generation strategy [Theme 3]

**Date:** 2026-05-19
**Substrates affected:** [S3]
**Status:** active

**Decision.** Data-behavior archetype is operationalized along the four dimensions established in D-077. This is the strongest v1 archetype coverage (per D-070 §2.8) where most v1 generation lands.

**Interpretation scope.** `scoped_neighborhood` for data-behavior is Object-centered:

- Target Object entity (resolved from requirement; ambiguity surfaces as `ambiguous-reference` refusal during interpretation phase).
- Field entities on the Object, filtered by relevance from requirement text.
- ValidationRule entities applicable to the Object.
- Flow entities triggering on Object events (DML, scheduled, platform event).
- Profile and PermissionSet entities granting access to the Object.

The scope graph traverses STRUCTURAL and BEHAVIOR edges (per substrate-1's edge taxonomy) outward from the Object, with relevance filtering on Field nodes.

**Admissibility-checking shape per claim_kind.**

- *value-claim*: Field's data type admits the asserted value (type compatibility); permission grants allow the asserting actor to read/write the Field; picklist value membership verified for custom-field picklists. *V1 limit*: standard-field picklist values blocked by S1 §22 deferral.
- *state-transition-claim*: ValidationRules don't structurally reject the transition (Layer 1 admissibility — rule exists and is active); permissions admit the transition path; Flow side-effects are tractable when Flow modeled. *V1 limit*: full ValidationRule formula reasoning blocked by S1 §17 deferral; Layer 2 admissibility (formula actually rejects/permits this specific scenario) requires the formula parser.
- *automation-effect-claim*: the asserted side effect derives from an automation entity in S1 (Flow primarily; Apex trigger partial per S1 Tier 2). Admissibility = "there is a Flow with this effect in its action set." *V1 limit*: Apex-driven effects are S1 Tier 2; admissibility for these is structurally limited to existence-of-trigger.
- *prohibition-claim*: a constraint entity in the org would reject the proposed scenario. Layer 1 admissibility (rule exists and is active) fully supported; Layer 2 admissibility (formula semantics) requires formula parser. Layer-1-only admissibility is honest about v1 reality; the resulting test verifies "this validation rule fires" not "this validation rule rejects this exact scenario."

**Recipe-kind selection.** Data-behavior recipes are almost always API-execution (creating or updating records via Composite API to trigger validation rules, flows, and automation side effects under realistic conditions). UI-execution is appropriate when the requirement explicitly references UI-driven behavior (approval modals, inline editing, lightning-record-form behaviors). Metadata-inspection is rare — data-behavior is about runtime behavior, not configuration state.

**Refusal dominance.** Expected v1 frequency, in approximate descending order:

1. `underspecified-requirement` — real-world JIRA tickets vague about target Object or trigger conditions.
2. `ambiguous-reference` — multiple Objects or Fields match without disambiguation.
3. `ungrounded-claim` — prohibition-claim cases that need formula parser (Layer 2 admissibility unavailable).
4. `low-generation-confidence` — multiple valid interpretations exist and the substrate can't pick one.

`no-relevant-context` and `structural-validation-failure` are infrequent in data-behavior because S1's data-model coverage is dense at Tier 1.

**Rationale.** Data-behavior is the archetype substrate-1's Tier 1 supports most deeply (Objects, Fields, ValidationRules, Flows, Profiles, PermissionSets all modeled). The four-dimensional spec reflects this coverage: rigorous admissibility, dominant API-execution recipe path, refusal dominance shaped by input quality rather than S1 ceiling.

**Alternatives considered.**

- *Per-claim_kind separate recipe selection.* Considered. Almost always converges to API-execution; per-claim_kind decision tree adds complexity without value. Recipe-kind selection is straightforward in this archetype.
- *Mandate Layer 2 admissibility for prohibition-claims.* Rejected. Would force all prohibition-claims to refuse until formula parser ships, eliminating useful Layer 1 coverage. Honest v1 commitment is Layer 1 admissibility with explicit "formula not parsed" in `attempted_interpretation`.

**Downstream consequences.**

- *S1 deferred items lifting:* When formula parser ships (S1 §17), prohibition-claim and state-transition-claim admissibility upgrades from Layer 1 to Layer 2 automatically. Non-breaking to Theme 3 design.
- *S1 Apex Tier 2:* When Apex modeling ships, automation-effect-claim admissibility for Apex-driven effects upgrades from existence-only to effect-tractable.
- *Theme 4 (grounded negatives):* Prohibition-claims are the primary grounded-negative case; Theme 4 operationalizes within data-behavior's Layer 1 admissibility plus the formula-parser-deferred reality.
- *Theme 7 (quality envelope):* Data-behavior's expected refusal pattern (underspecified + ambiguous-reference dominant) calibrates the per-archetype quality threshold.

**References.**

- `substrate_3_generation/SPEC.md` §4.3 (data-behavior archetype)
- D-077 (Theme 3; cross-cutting framework)
- D-070 (Theme 1; v1 archetype coverage reality §2.8)
- Substrate-1 PHASE_2_PLAN_corrections.md §17 (validation rule formula parser deferral)
- Substrate-1 PHASE_2_PLAN_corrections.md §22 (StandardValueSet detection deferral)

---


## D-079 — Configuration archetype generation strategy [Theme 3]

**Date:** 2026-05-19
**Substrates affected:** [S3]
**Status:** active

**Decision.** Configuration archetype is operationalized along the four dimensions established in D-077. Solid v1 archetype coverage (per D-070 §2.8).

**Interpretation scope.** `scoped_neighborhood` for configuration is centered on the specific metadata entity (or entities) the requirement names. Lighter graph traversal than data-behavior — configuration claims are typically about a single entity's properties or a specific edge between two entities.

- Target metadata entity (Object, Field, Profile, PermissionSet, ValidationRule, Flow, etc.).
- For metadata-relationship-claim, both ends of the asserted edge.
- Limited STRUCTURAL traversal — configuration claims rarely require wide neighborhood.

**Admissibility-checking shape per claim_kind.**

- *existence-claim*: S1 has (or does not have) the asserted entity. Fully admissible from S1 directly. The strongest v1 admissibility surface.
- *property-claim*: the property in question is part of S1's modeled attributes for the entity type, and S1 has the property set as asserted (or not). *V1 limit*: some properties of some entity types may not be modeled at S1's current Tier 1; admissibility falls back to "entity exists; property unmodeled" with `ungrounded-claim` refusal in those cases. The `attempted_interpretation` surfaces specifically which property is unmodeled.
- *metadata-relationship-claim*: the edge type asserted exists in S1's Tier 1 edge taxonomy (14 Tier 1 edge types); the specific edge between the named entities is present (or not) per S1. Fully admissible for the 14 Tier 1 edge types substrate-1 modeled.

**Recipe-kind selection.** Almost exclusively metadata-inspection — configuration claims are about S1's modeled state, not runtime behavior. Recipe verifies against either fresh metadata (via Tooling/Metadata API) or trusts S1's sync depending on the freshness contract the recipe specifies. Theme 5 operationalizes recipe-side freshness semantics.

**Refusal dominance.** Expected v1 frequency, in approximate descending order:

1. `no-relevant-context` — the org doesn't have what the requirement assumes (a custom field that doesn't exist, a profile that wasn't created).
2. `ambiguous-reference` — requirements like "the Status field" without specifying which Object's Status.
3. `ungrounded-claim` — property-claim cases where the property is unmodeled at S1 Tier 1.

`underspecified-requirement` and `low-generation-confidence` are infrequent in configuration because configuration claims tend to be specific enough to ground or not.

**Rationale.** Configuration claims are the simplest admissibility case in substrate-3: S1's metadata coverage at Tier 1 is the substrate of truth, and configuration claims read against it directly. The four-dimensional spec is tighter than data-behavior's because there's less surface area: lighter scoping, metadata-inspection-dominant recipes, refusal patterns shaped by S1 coverage gaps rather than input quality.

**Alternatives considered.**

- *Wider STRUCTURAL traversal during scoping.* Rejected for most cases. Configuration claims are local to their entity (or edge); wider traversal pulls in irrelevant context and degrades grounding signal. Wider traversal reserved for cases where the requirement explicitly invokes related entities ("the validation rule on the Status field").
- *Recipe-kind permitting UI-execution.* Rejected. Configuration is not about runtime UI behavior; UI-execution recipes for configuration claims would verify the *wrong* truth (the UI displays the configuration vs the configuration exists in the org). Honest archetype scoping prevents this.

**Downstream consequences.**

- *S1 Tier 1 property modeling depth:* When S1 deepens property modeling for entity types (Tier 2/3 expansion), property-claim admissibility automatically deepens. Non-breaking to Theme 3 design.
- *Theme 4 (grounded negatives):* Configuration negatives are typically existence-claim absences ("this org doesn't have a custom Status field") which are mechanically tractable. Configuration is among the cleanest archetypes for grounded negatives.
- *Theme 7 (quality envelope):* Configuration's expected refusal dominance pattern (no-relevant-context + ambiguous-reference + ungrounded-claim for unmodeled properties) calibrates the per-archetype quality threshold.

**References.**

- `substrate_3_generation/SPEC.md` §4.4 (configuration archetype)
- D-077 (Theme 3; cross-cutting framework)
- D-070 (Theme 1; v1 archetype coverage reality §2.8)
- Substrate-1 SPEC (Tier 1 entity types and edge taxonomy)

---


## D-080 — Permission archetype generation strategy with recipe-kind selection preserving claim semantics [Theme 3]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S1 Tier 2 sharing/OWD/Apex sharing roadmap]
**Status:** active

**Decision.** Permission archetype is operationalized along the four dimensions established in D-077, with the explicit architectural commitment that **recipe-kind selection preserves claim semantics — the substrate does not silently substitute one verification surface for another.**

**Interpretation scope.** `scoped_neighborhood` for permission includes:

- Target Object and Field entities (the subject of the asserted capability).
- Profile and PermissionSet entities (the asserting grants).
- User entities (or surrogates — typically Profile and PermissionSet stand in for representative users).
- Permission grant edges: GRANTS_OBJECT_ACCESS, GRANTS_FIELD_ACCESS (per substrate-1's PERMISSION edge category).
- Profile and PermissionSet assignment edges: HAS_PROFILE, HAS_PERMISSION_SET.

Sharing rules, OWD, role hierarchy, and Apex sharing are S1 Tier 2 and not modeled at v1; permission scoping does not extend into these dimensions.

**Admissibility-checking shape per claim_kind.**

- *capability-claim*: Profile or PermissionSet grants the asserted capability on the asserted target. Fully admissible for object-level and field-level grant verification. *V1 limit*: capabilities that depend on sharing rules, OWD, role hierarchy, or Apex sharing are not S1-modeled at Tier 1; admissibility falls back to grant-level only.
- *sharing-rule-claim*: the org has a sharing rule (or absence thereof) matching the assertion. *V1 limit*: sharing rules are S1 Tier 2; v1 admissibility-checking is structurally weak here. Most sharing-rule-claims will refuse with `no-relevant-context` until S1 Tier 2 ships.

**Recipe-kind selection — the architectural commitment.** Two recipe kinds are valid for capability-claim, and they verify *different epistemic truths about reality*:

- *Metadata-inspection* verifies: the org's permission configuration is set as claimed (a statement about configured permission state).
- *Run-as-execution* verifies: a user with this profile experiences this capability at runtime (a statement about runtime-effective experience).

These are not interchangeable verification surfaces. Silent substitution between them would alter what the test asserts is true about reality. The substrate commits to **preserving claim semantics by selecting recipe-kind explicitly:**

1. **Default to metadata-inspection** for permission claims where it is sufficient — object-level CRUD and field-level FLS verification without sharing/OWD/Apex sharing dependencies. The claim's semantic content is *configured permission state*; metadata-inspection is the appropriate verification surface.
2. **Refuse rather than silently substitute** when the claim's grounding indicates that runtime-effective verification is required (sharing rules, OWD, or Apex sharing dimensions affect the assertion). The refusal kind is `low-generation-confidence` with a typed disambiguation prompt: "this claim's verification surface requires either (a) restricting the assertion to configured-state truth (metadata-inspection appropriate) or (b) explicit runtime-experience scope (run-as-execution required, currently not v1-grounded due to S1 Tier 2 absence of sharing/OWD/Apex-sharing modeling)."
3. **Run-as-execution as engineer-opt-in only** in v1, gated by explicit `operational_context` preference. Not substrate-defaulted, because substrate-default run-as-execution would silently alter verification surface for claims where the engineer expected configured-state truth.

This is more conservative than naive complexity routing. It accepts higher v1 refusal rate in complex permission cases as the price of semantic precision. Engineers know exactly what verification surface their generated tests cover.

**Refusal dominance.** Expected v1 frequency, in approximate descending order:

1. `ungrounded-claim` — sharing-rule-claim cases blocked by S1 Tier 2 absence.
2. `low-generation-confidence` — capability-claim cases where sharing/OWD/Apex-sharing dimensions affect the assertion and the substrate cannot determine which verification surface the engineer intended.
3. `no-relevant-context` — assertions about Profiles or PermissionSets the org doesn't have.

`underspecified-requirement`, `ambiguous-reference`, and `structural-validation-failure` are less dominant.

**Rationale.** The recipe-kind-preserves-claim-semantics commitment emerged from round 2 TA review surfacing that metadata-inspection and run-as-execution do not verify equivalent truths. Recipe-kind selection in permission archetype is not merely a complexity-routing or optimization decision — it changes what kind of truth the generated test asserts. The substrate must preserve claim semantics explicitly or it drifts into silently producing tests that verify different things than engineers intended.

The conservative v1 commitment (default metadata-inspection; refuse with disambiguation when run-as required; opt-in for run-as) is honest about v1's S1 Tier 2 ceiling and protects engineers from semantic drift.

**Alternatives considered.**

- *Substrate-defaulted recipe-kind by complexity routing.* Rejected per round 2 TA review. Silent substitution between metadata-inspection and run-as-execution alters what the claim verifies; this is unacceptable semantic drift.
- *Engineer chooses recipe-kind per request.* Considered. Rejected as default behavior because most permission claims don't need engineer-level decisions — metadata-inspection covers the common case cleanly. Engineer opt-in via `operational_context` is the right surface for the cases that do.
- *Refuse all complex permission claims until S1 Tier 2 ships.* Rejected as too conservative. Simple capability-claims (object-level + field-level grants only, no sharing dependencies) are valuable and ground cleanly with metadata-inspection. Refusing them all would eliminate useful v1 coverage.

**Downstream consequences.**

- *S1 Tier 2 sharing/OWD/Apex-sharing modeling:* When ships, currently-refused complex capability-claims may upgrade to grounded run-as-execution recipes. The substrate's refusal-with-disambiguation pattern is the right v1 posture pending Tier 2.
- *Theme 5 (LLM integration):* `operational_context` carries the engineer's run-as-execution opt-in when applicable; LLM integration handles the recipe-kind decision tree per claim.
- *Theme 7 (quality envelope):* Permission archetype's expected refusal dominance pattern (ungrounded-claim for sharing rules; low-generation-confidence for complex capability cases) calibrates the per-archetype quality threshold. Higher baseline refusal rate than data-behavior is honest about v1 S1 ceiling.
- *Forward-compat reservation:* Run-as-execution upgrade path documented in substrate-3 OPEN_QUESTIONS.md.

**References.**

- `substrate_3_generation/SPEC.md` §4.5 (permission archetype)
- D-077 (Theme 3; cross-cutting framework)
- D-070 (Theme 1; v1 archetype coverage reality §2.8)
- D-073 (Theme 2; `low-generation-confidence` refusal kind)
- D-071 (Theme 2; `operational_context` for engineer opt-in)
- Substrate-1 entity types (Profile, PermissionSet, GRANTS_OBJECT_ACCESS, GRANTS_FIELD_ACCESS, HAS_PROFILE, HAS_PERMISSION_SET)
- Substrate-1 Tier 2 roadmap (sharing rules, OWD, Apex sharing — future capability)

---


## D-081 — UI archetype generation strategy with honest v1 scope [Theme 3]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for S1 Tier 3 Lightning page composition roadmap]
**Status:** active

**Decision.** UI archetype is operationalized along the four dimensions established in D-077, with explicit acknowledgment of S1 Tier 3 absence. Minimal v1 archetype coverage (per D-070 §2.8). Higher baseline refusal rate is honest about v1's S1 ceiling, not a quality regression.

**Interpretation scope.** `scoped_neighborhood` for UI is structurally limited at v1:

- PageLayout entities.
- Field entities on those layouts (via INCLUDES_FIELD edges).
- The Object the layout belongs to.

Lightning Pages, Lightning Web Components, Aura Components, Dynamic Forms, Flow Screens are S1 Tier 3 and not modeled. The interpretation layer cannot construct neighborhoods for UI requirements that target these surfaces.

**Admissibility-checking shape per claim_kind.**

- *layout-claim*: the asserted field/section appears on the asserted Page Layout. Fully admissible for Tier 1 PageLayout coverage. The strongest v1 UI admissibility surface.
- *element-state-claim*: the asserted UI element exists in some layout-derivable surface (the field is on a layout, the section is on a layout). *V1 limit*: element-state-claims that target non-layout-derived elements (Lightning component states, Dynamic Form fields, custom JavaScript-driven UI, conditional visibility from Lightning Page configurations) cannot be grounded. These refuse with `no-relevant-context` plus a typed `org_capability_gap` note identifying which UI surface requires S1 Tier 3.

**Recipe-kind selection.**

- *layout-claim*: metadata-inspection primarily (read PageLayout metadata directly). UI-execution available if the requirement specifically asserts a layout-rendering behavior.
- *element-state-claim*: UI-execution (Playwright-driven) for layout-derivable elements where it grounds.

**Refusal dominance.** Expected v1 frequency, in dominant-and-large-margin order:

1. `no-relevant-context` — dominates by wide margin. Most real UI requirements target Lightning composition, which v1 cannot ground.
2. `ungrounded-claim` — element-state-claims that name elements not derivable from PageLayout.
3. `underspecified-requirement` and `ambiguous-reference` — less frequent in UI because UI requirements tend to be specific about target page/element.

The Theme 7 quality envelope must accept that UI archetype has a higher baseline refusal rate than other archetypes — this is honest about v1 reality.

**Rationale.** S1 Tier 3 (Lightning page composition) is the dominant constraint on UI archetype's v1 coverage. The substrate refuses informatively rather than generating weak claims that pretend to ground against an absent S1 layer. Honest scope is the right substrate posture; the alternative (weak UI claim generation) erodes the substrate's trust commitment substantially.

This is also consistent with substrate-3's failure-loud philosophy (D-070 §2.5): refuse to produce output rather than producing structurally valid but semantically wrong claims. UI v1 claims that pretend to ground against absent S1 modeling would be semantically wrong by construction.

**Alternatives considered.**

- *Generate weak UI claims with low-confidence markers.* Rejected. Violates verification-bar discipline (D-070 §2.4) — output below the verification bar refuses rather than emits weakly. Confidence markers don't substitute for ground-truth admissibility.
- *Defer entire UI archetype until S1 Tier 3 ships.* Rejected. Layout-claim and layout-derivable element-state-claim are valuable v1 surfaces. Excluding them entirely would lose meaningful coverage.
- *Treat UI archetype as documentation-only at v1.* Rejected. The substrate is operational at v1 for layout-level claims; refusal-with-honest-context is the right posture for non-layout cases.

**Downstream consequences.**

- *S1 Tier 3 Lightning page composition:* When ships, UI archetype coverage expands materially. Currently-refused element-state-claims may upgrade to grounded Lightning-component-aware admissibility.
- *Theme 7 (quality envelope):* UI archetype's higher baseline refusal rate is calibrated separately from other archetypes. Quality is not measured uniformly across archetypes.
- *Product strategy:* UI archetype's v1 limitation produces a perception pattern (PrimeQA appears strong on backend/configuration/permission, weaker on UI) that affects evaluation perception, demo strategy, and rollout sequencing. Worth recognizing in product roadmap planning; not architecturally addressable until S1 Tier 3 ships.

**References.**

- `substrate_3_generation/SPEC.md` §4.6 (UI archetype)
- D-077 (Theme 3; cross-cutting framework)
- D-070 (Theme 1; v1 archetype coverage reality §2.8 — UI minimal; failure-loud philosophy §2.5; verification-bar discipline §2.4)
- Substrate-1 Tier 3 roadmap (Lightning page composition, Lightning Components, Dynamic Forms — future capability)

---


## D-082 — Integration archetype generation strategy with operational-only v1 admissibility [Theme 3]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for future substrate-3 cycles when integration becomes larger product surface]
**Status:** active

**Decision.** Integration archetype is operationalized along the four dimensions established in D-077. V1 ships with operational-only admissibility — verification of existence of integration entities and structural connectivity, not cross-system causality semantics. Scoped v1 archetype coverage (per D-070 §2.8).

**Interpretation scope.** `scoped_neighborhood` for integration varies per claim_kind:

- *platform-event-claim*: PlatformEvent entities; subscribing Flow entities (Apex subscribers partial per S1 Tier 2).
- *outbound-message-claim*: Workflow rule and OutboundMessage configuration entities (S1 coverage depends on Tier 1 workflow modeling).
- *callout-claim*: NamedCredential, RemoteSiteSetting, callout-defining Apex entities (Apex partial per S1 Tier 2).
- *inbound-effect-claim*: Apex REST/SOAP endpoint definitions, inbound HTTP handlers (Apex partial per S1 Tier 2).

**Admissibility-checking shape per claim_kind.** V1 admissibility is *operational-only*:

- *platform-event-claim*: PlatformEvent entity exists in S1; at least one subscribing entity exists. *V1 limit*: Apex subscriber effects partial per S1 Tier 2.
- *outbound-message-claim*: OutboundMessage configuration entity exists. *V1 limit*: workflow rule sending the message may need fuller workflow modeling than S1 Tier 1 provides.
- *callout-claim*: NamedCredential and RemoteSiteSetting exist; callout-defining Apex entity exists. *V1 limit*: Apex Tier 2 limits depth.
- *inbound-effect-claim*: inbound handler exists. *V1 limit*: Apex Tier 2 limits depth.

Operational-only admissibility means: the substrate verifies that the integration entities are configured and structurally connected. It does not verify cross-system causality, external observability, temporal sequencing, or protocol semantics — these are *interaction-topology* concerns that require their own architectural treatment when integration becomes a larger product surface.

**Recipe-kind selection.** Mix per claim_kind:

- *platform-event-claim*: API-execution (fire the event, observe subscriber effects) + metadata-inspection (verify configuration).
- *outbound-message-claim*: API-execution (trigger workflow conditions, observe outbound dispatch via mock listener) + metadata-inspection.
- *callout-claim*: API-execution (trigger the callout, observe outbound HTTP via mock external service) + metadata-inspection.
- *inbound-effect-claim*: API-execution (POST to the inbound endpoint, observe effect on org data) + metadata-inspection.

The metadata-inspection-alongside pattern is consistent: verify configuration exists before firing the operational test. This grounds the operational verification in the substrate's structural admissibility.

**Refusal dominance.** Expected v1 frequency, in approximate descending order:

1. `ungrounded-claim` — cases requiring Apex Tier 2 depth.
2. `no-relevant-context` — integration requirements that reference patterns the specific org doesn't implement.
3. `ambiguous-reference` — "the outbound message" without specifying which workflow rule's.

`underspecified-requirement` and `low-generation-confidence` are less dominant.

**Forward-compat reservation: interaction-topology admissibility philosophy.** Integration claims involve concerns that go beyond what operational-only admissibility captures:

- *Cross-system causality:* what observable effect should follow from what trigger across system boundaries.
- *External observability:* how the substrate verifies effects that manifest outside the org (in external systems via callout).
- *Temporal sequencing:* what order of operations the integration claim asserts.
- *Protocol semantics:* what message format, retry behavior, idempotency properties the integration commits to.

These are *interaction-topology claims*, structurally distinct from existence/property/runtime-effect claims that data-behavior, configuration, permission, and UI archetypes handle. V1's operational-only admissibility is honest about not yet having an interaction-topology admissibility framework. A future substrate-3 cycle may add this admissibility philosophy when integration becomes a larger product surface; the architectural slot is reserved.

Substrate-3 OPEN_QUESTIONS.md carries the forward-compat reservation.

**Rationale.** Integration archetype is conceptually weaker at v1 than other archetypes — not because of S1 ceiling alone, but because integration claims are categorically different (interaction-topology) from the other archetypes' assertion types (existence, property, runtime effect, capability, configured-state). Pretending v1 fully addresses integration would be architectural dishonesty. Naming the gap explicitly preserves architectural integrity and reserves the slot for future treatment.

The operational-only v1 admissibility is what the substrate can honestly ground today. Forward-compat reservation for interaction-topology admissibility is honest about what's deferred.

**Alternatives considered.**

- *Defer entire integration archetype to a later substrate-3 cycle.* Rejected. Operational-only v1 admissibility provides useful coverage for the common cases (existence + structural connectivity); excluding it entirely loses real value.
- *Force interaction-topology admissibility into v1.* Rejected. Substrate-3 doesn't yet have the architectural framework for it; designing the framework in Theme 3 would expand Theme 3 scope substantially without proportionate v1 value.
- *Per-protocol admissibility shapes (REST vs SOAP vs Platform Event protocols specified individually).* Considered. Premature at v1 — protocol semantics admissibility is part of the interaction-topology framework reserved for a future cycle.

**Downstream consequences.**

- *Future substrate-3 cycle:* Interaction-topology admissibility philosophy (cross-system causality, external observability, temporal sequencing, protocol semantics) may need its own architectural cycle when integration becomes a larger product surface.
- *S1 Apex Tier 2:* When Apex modeling ships, several integration claim_kinds upgrade from existence-only to depth-tractable.
- *Theme 4 (grounded negatives):* Integration negatives at v1 are typically existence absences (no NamedCredential, no inbound handler), which are mechanically tractable within operational-only admissibility.
- *Theme 7 (quality envelope):* Integration's expected refusal dominance pattern (ungrounded-claim due to Apex Tier 2; no-relevant-context due to org-specific implementation variance) calibrates the per-archetype quality threshold.

**References.**

- `substrate_3_generation/SPEC.md` §4.7 (integration archetype)
- D-077 (Theme 3; cross-cutting framework)
- D-070 (Theme 1; v1 archetype coverage reality §2.8 — integration scoped)
- Substrate-1 Tier 2 roadmap (Apex modeling — affects all four integration claim_kinds)

---


## D-083 — Grounded-negative discipline: S3 Guardrail 3, seventh refusal kind, polarity strictly derived, bounded decomposition, Layer 1 visible trust degradation [Theme 4]

**Date:** 2026-05-19
**Substrates affected:** [S3, with downstream consequences for Theme 5 LLM integration, Theme 7 quality envelope, and substrate-3 artifact-level output schema]
**Status:** active

**Decision.** Theme 4 commits substrate-3 to a grounded-negative discipline preventing the v2 failure mode of plausible-but-ungrounded negatives. Five architectural commitments, integrated from round 2 TA convergence:

**(a) S3 Guardrail 3 — Requirement-anchored origination.** Grounding constraints justify candidate negatives derived from requirement interpretation. They do not independently originate negatives the requirement did not semantically imply.

Lineage: Guardrail 1 (Theme 1; semantic search space bounded by S1 × substrate-2 taxonomy) → Guardrail 2 (Theme 2 D-075; ontology-bound reasoning artifacts) → Guardrail 3 (Theme 4 this entry; requirement-anchored origination). Each Guardrail tightens what the substrate may do under what authority.

Mechanical enforcement:

- Interpretation phase produces candidates from requirement text. Requirement text, archetype hint, and explicit negation cues are the only origination signal. Constraint discovery during interpretation serves candidate disambiguation, not candidate origination.
- Grounding phase tests admissibility of requirement-derived candidates. Constraints are read from S1 to verify admissibility; constraints do not introduce new candidates.
- Every candidate carries — in `attempted_interpretation.candidate_paths` — the requirement excerpt(s) from which it was derived. A candidate without traceable origin is rejected as substrate-internal product before grounding-phase admissibility checking.

The architectural defense against the substrate's quiet drift from "constrained interpretation engine" (D-070 §2.1) to "exploratory QA generator." Constraint-first generation would shift S3's mission to exploratory QA generation — categorically different product. Guardrail 3 prevents this.

**(b) Seventh refusal kind with typed internal cause.** `no-admissible-negative-scenario-found` (Theme 2 anticipated; Theme 4 ships) carries a typed internal `cause` field distinguishing three semantic causes under one external refusal kind:

- `ontology_gap` — substrate cannot ground because S1 doesn't model the relevant constraint dimension. The substrate is incapable, not the org. `what_would_unblock` points to substrate capabilities (formula parser, S1 Tier 2 sharing, S1 Tier 3 Lightning composition, Apex modeling).
- `no_org_constraint` — the org genuinely has no constraint producing the asserted rejection. The substrate could ground if a constraint existed; none does. `what_would_unblock` typically empty.
- `policy_restraint` — a candidate grounding exists but the substrate's admissibility-confidence (distinct from D-073's selection-confidence governed by `low-generation-confidence`) doesn't meet threshold. `what_would_unblock` may point to substrate-3 confidence-calibration evolution.

Updated refusal taxonomy at Theme 4 close (7 categories): the six from Themes 1-2 plus `no-admissible-negative-scenario-found` (policy-scope). External refusal_kind taxonomy stays at 7; internal cause preserves semantic granularity for evals, replay, analytics, capability tracking. Pattern consistent with D-073's typed structured payloads under stable refusal kinds. Cause vocabulary governed under Guardrail 2 (substrate-3 reasoning vocabulary discipline).

Feedback payload:

    no-admissible-negative-scenario-found: {
      cause: <ontology_gap | no_org_constraint | policy_restraint>
      proposed_negative_assertion: <typed structure>
      searched_constraint_dimensions: [<typed list>]
      no_grounding_found_because:    <typed reason from substrate-authorized vocabulary>
      what_would_unblock:            [<optional typed list>]
    }

Interaction with D-076's `no_constraint_supports_negative` dismissal_reason: the dismissal_reason fires per dismissed candidate during grounding-phase reasoning. The refusal_kind is the outcome-level aggregate when all candidates dismissed for grounding reasons.

**(c) Polarity strictly derived from claim semantics — no parallel field.** Polarity is semantic claim identity, not interpretation metadata. The substrate recognizes negatives from claim_kind + content, not from a separate `polarity` field. Adding `polarity` to `candidate_paths` would create parallel semantic systems where claim_kind says one thing and polarity says another; architecturally fragile.

Recognition per archetype:

- Inherently negative claim_kinds: `prohibition-claim` (data-behavior). The claim_kind IS the negative semantic.
- Content-derived polarity claim_kinds: `capability-claim` (grant asserted vs grant denied), `existence-claim` (entity exists vs absent), `property-claim` (property is X vs is not X), `metadata-relationship-claim` (edge present vs absent), `layout-claim` (field on layout vs not), `element-state-claim` (state X vs not), integration claim_kinds (effect occurs vs does not occur). Polarity determined by claim content.

The grounded-negative discipline applies to claim instances whose semantic content asserts rejection or absence, recognized from claim_kind + content. No parallel polarity property; no risk of inconsistency between claim_kind and a separate polarity field. Substrate-2 claim_kind remains the authoritative semantic identity.

**(d) Bounded decomposition discipline.** Three-part principle protecting against combinatorial expansion in enterprise orgs with overlapping constraints:

1. Canonical negative per identifiable failure mode. An identifiable failure mode is a distinct semantic dimension of negative the requirement implies. The substrate emits one negative per failure mode. Requirements explicitly enumerating multiple failure modes get distinct emitted negatives per mode.
2. Highest-specificity grounding among admissible alternatives. When multiple constraints could ground one failure mode, the substrate selects the most specific. Specificity = how directly the constraint addresses the requirement's intent. Dismissed alternatives surface in `attempted_interpretation.dismissal_reasons` with `lower_specificity` (D-076 existing reason; no new vocabulary).
3. Bounded candidate enumeration during interpretation. Interpretation phase enumerates top-K candidates per failure mode, K configurable per `governance_context.transparency_policy_version`. Enumeration cap protects against combinatorial expansion at the interpretation layer.

Combined: enterprise orgs with overlapping validation rules, layered permissions, and multiple automation gates do not produce many emitted negatives. They produce one canonical negative per failure mode with dismissed alternatives transparently surfaced as `lower_specificity` dismissals. Review UX stays bounded; lineage stays tractable. Architectural posture consistent with D-080's recipe-kind discipline (substrate picks the appropriate verification surface, surfaces dismissed alternatives transparently).

**(e) Layer 1 admissibility produces artifact-level visibly degraded trust marker.** Layer 1 admissibility (validation-rule-grounded negatives at v1, where formula not parsed per D-078) is not buried in metadata. Substrate-3 commits to artifact-level trust visibility:

- Typed `admissibility_layer` field at artifact top level — `layer_1` | `layer_2`. Not nested in `attempted_interpretation`; at the artifact's structural top level alongside claim, recipe, and provenance.
- Substrate-emitted natural-language caveat in Layer 1 artifacts — the artifact's narrative includes: "Layer 1 admissibility — validation rule applicability verified; formula-specific rejection logic not parsed." The caveat is part of the artifact, not optional UX rendering.
- Downstream review UX renders the layer prominently — UX-level rendering is product responsibility; substrate-3 makes the artifact-level field available and the natural language explicit.

Architectural defense against false trust. The Layer 1 marker is at the same surface level as the test name; the natural language makes the limitation explicit. Engineers who don't deeply internalize Layer 1 vs Layer 2 still see, prominently on every Layer 1 artifact, that the test verifies rule firing without formula-specific verification. Layer 2 artifacts (post formula parser) carry `admissibility_layer = layer_2` without the caveat.

**Rationale.** Five integrated architectural commitments from round 2 TA convergence:

- Guardrail 3 (a) prevents grounded-negative discipline from operationally drifting into constraint-first generation, which would shift S3's mission.
- Typed internal cause (b) preserves semantic clarity for evals, replay, analytics without proliferating refusal kinds at product surface.
- Polarity derived (c) avoids parallel semantic systems; preserves substrate-2 claim_kind as authoritative semantic identity.
- Bounded decomposition (d) prevents combinatorial expansion while preserving transparency through dismissed-alternatives surfacing.
- Layer 1 visible trust (e) prevents the false-trust failure mode for v1 validation-rule-grounded negatives that are technically grounded but semantically weak.

All five emerged from round 2 TA pressure-test as essential to converging Theme 4 without compromising substrate-3 mission integrity.

**Alternatives considered.**

- *No Guardrail 3 — accept constraint-first generation as a parallel mode.* Rejected. Constraint-first is a different product (exploratory QA generation); substrate-3 is the constrained interpretation engine. Mixing modes muddies mission clarity.
- *Multiple refusal kinds for ontology_gap, no_org_constraint, policy_restraint.* Rejected per TA: external product behavior should be one refusal kind; internal cause carries semantic granularity. Multiple kinds would proliferate the taxonomy without product-surface value.
- *Polarity as authoritative candidate-path field.* Rejected per TA: parallel-semantic-systems fragility; polarity is semantic claim identity, not metadata.
- *No bounded decomposition — emit one negative per admissibly-grounded constraint.* Rejected per TA: combinatorial expansion is operationally unmanageable in large orgs.
- *Layer 1 admissibility marker as metadata only.* Rejected per TA round 2: users won't internalize Layer 1 vs Layer 2 from metadata; visible trust degradation must be at artifact surface.

**Downstream consequences.**

- *D-084 (Theme 4 per-archetype):* Each archetype's negative scope operationalizes within Guardrail 3 + bounded decomposition + Layer 1 trust visibility discipline.
- *Theme 5 (LLM integration):* Interpretation-phase candidate origination must enforce requirement-anchored origin signal (Guardrail 3); LLM integration topology bounds K per `transparency_policy_version`.
- *Theme 6 (prompt management):* Eval suite includes negative-test admissibility-layer distribution, dismissed-alternative surfacing, and cause distribution for `no-admissible-negative-scenario-found` refusals.
- *Theme 7 (quality envelope):* Per-archetype expected refusal-rate calibration includes `no-admissible-negative-scenario-found` cause distribution; admissibility-confidence threshold for `policy_restraint` cause calibrated per archetype.
- *Substrate-3 artifact-level output schema:* `admissibility_layer` field at top level of every generated artifact.
- *Substrate-2 typed provenance:* Theme 4 introduces no new structural metadata at the substrate-2 boundary (layer is artifact-internal; cause is refusal-payload-internal; both governed under substrate-3 refusal_schema_version per D-074).

**References.**

- `substrate_3_generation/SPEC.md` §5.2 (grounded-negative discipline), §5.3 (seventh refusal kind), §5.4 (polarity recognition), §5.5 (bounded decomposition), §5.6 (Layer 1/Layer 2 admissibility)
- D-070 (Theme 1; constrained interpretation engine; Guardrail 1; failure-loud philosophy)
- D-071 (Theme 2; `governance_context` housing `transparency_policy_version`)
- D-073 (Theme 2; refusal taxonomy; seventh refusal kind anticipated as policy-scope)
- D-074 (Theme 2; refusal_schema_version)
- D-075 (Theme 2; Guardrail 2; reasoning artifact discipline)
- D-076 (Theme 2; dismissal_reason taxonomy; `lower_specificity`, `no_constraint_supports_negative`)
- D-077 (Theme 3; cross-cutting framework; dismissal_reason by phase)
- D-078 (Theme 3; data-behavior Layer 1/Layer 2 distinction for validation-rule grounded claims)
- D-080 (Theme 3; recipe-kind discipline pattern extended here to negative-grounding selection)
- Substrate-1 PHASE_2_PLAN_corrections.md §17 (formula parser deferral)

---


## D-084 — Per-archetype grounded-negative scope with integration causal-admissibility reserved [Theme 4]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for future substrate-3 cycles when integration becomes larger product surface]
**Status:** active

**Decision.** Theme 4's grounded-negative discipline (D-083) operationalizes per archetype within the locked Theme 3 archetype strategies. Per-archetype groundings and admissibility-layer distribution:

**Data-behavior negatives.** Richest archetype. Groundings:

- Validation rule — Layer 1 admissibility at v1 (rule exists and is active per Theme 3 D-078); Layer 2 when substrate-1 §17 formula parser ships. Layer 1 negatives carry the substrate-emitted caveat per D-083 (e).
- Required-field constraint — Layer 2 admissibility at v1 (S1 directly models required-field state).
- Type incompatibility — Layer 2 admissibility (S1 models field types).
- Permission restriction — Layer 2 admissibility (S1 models grants); permission-grounded data-behavior negatives leverage Theme 3 D-080's discipline.
- Automation rejection — partial admissibility (Flow with negative-condition guards is tractable; Apex-driven rejection is S1 Tier 2).

Canonical negative shape per failure mode is `prohibition-claim`. `state-transition-claim` and `automation-effect-claim` negatives decompose to one or more of the groundings above per D-083 (d).

**Configuration negatives.** Cleanest archetype mechanically. Groundings:

- S1 entity absence — Layer 2 admissibility (existence-claim negatives trivially admissible: S1 has the entity or not).
- Property state absence — Layer 2 when S1 models the property; refuses with `ungrounded-claim` when property unmodeled at S1 Tier 1.
- Edge absence — Layer 2 (S1 has the edge or not).

Configuration negatives are mechanically the simplest because S1 is the substrate of truth for what does and does not exist.

**Permission negatives.** Leverages Theme 3 D-080's recipe-kind discipline. Groundings:

- Grant absence — Layer 2 within v1 grant-level scope (S1 models GRANTS_OBJECT_ACCESS, GRANTS_FIELD_ACCESS).
- Sharing-rule absence — structurally weak at v1; most sharing-rule-claim negatives refuse with `no-admissible-negative-scenario-found` cause=`ontology_gap`, `what_would_unblock` pointing to S1 Tier 2 sharing modeling.

Per D-080, recipe-kind selection preserves claim semantics for negatives as well: capability-claim negatives grounded by grant absence are metadata-inspection-verifiable. Negatives requiring sharing/OWD/Apex-sharing grounding refuse cleanly with the typed cause.

**UI negatives.** Narrow scope at v1. Groundings:

- INCLUDES_FIELD edge absence — Layer 2 admissibility for layout-claim negatives (field NOT on Page Layout directly verifiable from S1).
- Element-state negatives on non-layout-derivable elements — refuse with `no-admissible-negative-scenario-found` cause=`ontology_gap`, `what_would_unblock` pointing to S1 Tier 3 Lightning page composition.

**Integration negatives — operational-only admissibility with causal-admissibility forward-compat.** V1 ships integration negatives with constraint-admissibility framing (simplest cases only):

- Integration entity absence — operational-only-admissible (no NamedCredential, no inbound handler, no PlatformEvent subscriber).
- Configuration absence — operational-only-admissible.

The philosophically deeper integration negatives (verifying non-firing of effects under specific causal conditions) require causal admissibility — temporal observation, causal interpretation, distributed-state reasoning — categorically different from constraint admissibility. V1 does not have a causal-admissibility framework; a future substrate-3 cycle may add this when integration becomes a larger product surface. Forward-compat reservation in substrate-3 OPEN_QUESTIONS.md; parallel to D-082's interaction-topology admissibility reservation; likely converges with it in the same future cycle.

**Per-archetype negative recognition discipline (operationalizing D-083 c).** Polarity is derived from claim_kind + content. Recognition rules per archetype:

- Data-behavior: prohibition-claim inherently negative; state-transition-claim, automation-effect-claim, value-claim derive polarity from content.
- Configuration: existence/property/metadata-relationship-claims derive polarity from content (presence or absence).
- Permission: capability/sharing-rule-claims derive polarity from content (grant asserted or denied).
- UI: layout/element-state-claims derive polarity from content (presence or absence; state X or not-X).
- Integration: all four integration claim_kinds derive polarity from content (effect occurs or does not).

No `polarity` field. Recognition is reading the claim's semantic content per archetype.

**Rationale.** Theme 4's per-archetype scope is conceptually uniform — D-083's discipline applies consistently — but admissibility-layer distribution and grounding sources vary by archetype's S1 coverage. Per-archetype documentation makes the differences explicit and provides eval suite (Theme 6) and quality envelope (Theme 7) with calibration anchors.

Integration is the philosophically weakest archetype in the negative space. Honest acknowledgment plus forward-compat reservation preserves substrate-3 architectural integrity without forcing a causal-admissibility framework into Theme 4 scope.

**Alternatives considered.**

- *Force causal-admissibility framework for integration into Theme 4.* Rejected per TA. The framework requires its own architectural treatment; designing it within Theme 4 would expand scope substantially without proportionate v1 value.
- *Generate integration negatives at causal-admissibility framing without the framework.* Rejected. Would produce structurally valid but semantically unsupported negatives — exactly the v2 failure mode Theme 4 defends against.

**Downstream consequences.**

- *S1 §17 formula parser:* data-behavior validation-rule-grounded negatives upgrade from Layer 1 to Layer 2 automatically when ships. Non-breaking; `admissibility_layer` artifact field shifts value, substrate-emitted caveat no longer emitted.
- *S1 Tier 2 sharing/OWD/Apex-sharing modeling:* permission sharing-rule negatives upgrade from `ontology_gap` refusal to admissible (likely Layer 2) when ships.
- *S1 Tier 3 Lightning page composition:* UI element-state negatives on non-layout-derivable elements upgrade from `ontology_gap` refusal to admissible when ships.
- *Future substrate-3 cycle (integration causal admissibility):* parallel to D-082's interaction-topology admissibility reservation; both likely converge in same cycle.
- *Theme 7 (quality envelope):* per-archetype expected `no-admissible-negative-scenario-found` rates calibrated per archetype, with cause distribution (ontology_gap vs no_org_constraint vs policy_restraint) per archetype expected.

**References.**

- `substrate_3_generation/SPEC.md` §5.7 (per-archetype scope), §5.8 (forward-compat reservations)
- D-083 (Theme 4; grounded-negative discipline machinery)
- D-077, D-078, D-079, D-080, D-081, D-082 (Theme 3; per-archetype strategies; this entry operationalizes negatives within them)
- Substrate-1 PHASE_2_PLAN_corrections.md §17 (formula parser deferral; affects data-behavior Layer 1 / Layer 2 distribution)

---


## D-085 — LLM integration topology: tool-use selected; substrate-3 as constrained semantic orchestration runtime [Theme 5]

**Date:** 2026-05-19
**Substrates affected:** [S3, with downstream consequences for Theme 6 prompt management and Theme 7 quality envelope]
**Status:** active

**Decision.** Theme 5 selects tool-use as substrate-3's LLM integration topology and reframes the substrate-LLM authority boundary. The substrate is a constrained semantic orchestration runtime; the LLM is a bounded cognition provider.

**Topology selection: tool-use over structured JSON and planner-style.**

Three candidate topologies evaluated:

- *Tool-use (function-calling).* LLM produces structured outputs via typed tool invocations; substrate validates each call at the tool boundary; per-call observability.
- *Structured JSON.* LLM produces a single JSON blob conforming to a schema; substrate parses and validates the entire blob.
- *Planner-style.* LLM produces a multi-step plan, then executes; substrate validates plans and execution.

Tool-use selected. Rationale:

1. *Mechanical Guardrail 2 enforcement at emission boundary.* Tool calls have typed parameter schemas. The LLM cannot emit free-form values at vocabulary positions; substrate-authorized enums (D-076 dismissal_reason, D-073 refusal_kind, D-083 cause and admissibility_layer) are structural preconditions of emission. Structured JSON requires post-emission validation the LLM can drift from over many attempts; tool-use makes vocabulary discipline an emission precondition.
2. *Per-operation observability for ledger/eval/replay.* Each tool call produces an observable record (D-074's `llm_calls` operational telemetry per D-087). Theme 7 quality envelope benefits from per-call granularity rather than per-generation aggregation.
3. *Decomposed reasoning maps to substrate orchestration phases.* Tool-use naturally maps to substrate-3's reasoning phases (interpretation / grounding / governance per D-077). Substrate orchestrates across phases; LLM contributes at specific phase boundaries.
4. *Incremental correction.* Tool-use rejects invalid calls immediately with typed substrate feedback; LLM can correct within the same generation. Structured JSON requires full-regeneration on validation failure; tool-use enables incremental correction.
5. *Planner-style rejected.* Planner autonomy is misaligned with substrate-3's constrained-interpretation-engine mission (D-070 §2.1). Plan-then-execute introduces LLM-authored intermediate plans the substrate would have to validate; the validation surface expands without proportionate value.

**Substrate-3 as constrained semantic orchestration runtime.**

The round 2 TA pushback (item 8) correctly identified that the substrate is the locus of architectural authority; the LLM contributes within substrate-bounded discipline. Theme 5 makes this explicit:

*Substrate-3 responsibilities:*

- *Orchestration engine.* Coordinates the reasoning phase pipeline (interpretation → grounding → governance per D-077) per request.
- *Governance engine.* Enforces three Guardrails at both schema and semantic levels (D-087 two-layer enforcement).
- *Admissibility engine.* Derives admissibility from S1 + substrate-2 taxonomy + Layer 1/2 discipline (D-083 e); LLM does not author admissibility.
- *Decomposition controller.* Enforces canonical-negative-per-failure-mode + highest-specificity + bounded enumeration (D-083 d).
- *Replay controller.* Computes identity_hash and explanation_hash over semantic substance; surfaces drift events (D-088).
- *Refusal router.* Categorizes refusal causes across 8 typed kinds with typed payloads across three categories (invalidity / policy / operational); routes operational vs policy vs invalidity distinctly.

*LLM responsibilities (bounded cognition provider):*

- *Semantic intent interpretation* — what the requirement implies; emitted via `propose_semantic_intent`.
- *Selection judgment* — when the substrate presents multiple admissibly-grounded canonical options; emitted via `select_canonical`.
- *Outcome emission* — final structured emission via `emit_outcome`.

The LLM does not orchestrate, does not author admissibility, does not categorize dismissals, does not select among refusal kinds. Those are substrate-locus responsibilities.

**Tool surface — three thin semantic primitives.**

Detailed in D-086. The reshape from a six-tool phase-shaped surface to three thin primitives prevents Theme 5 from baking current reasoning choreography into protocol law; Themes 6/7 are free to evolve substrate-internal orchestration without breaking the API contract.

**Rationale.**

The integration topology decision is straightforward: tool-use provides mechanical Guardrail enforcement and per-call observability that no other topology achieves. The substrate-3-as-orchestration-runtime framing is the deeper commitment — it correctly locates architectural authority and provides a clean evolution surface (LLM cognition advances do not require substrate rework; substrate orchestration evolves through design cycles without breaking the LLM contract).

**Alternatives considered.**

- *Structured JSON as integration topology.* Rejected. Vocabulary discipline weaker at the emission boundary; per-call observability lost; incremental correction unavailable.
- *Planner-style.* Rejected per substrate-3 mission alignment. Planner autonomy expands validation surface without value; constrained interpretation engine is incompatible with autonomous plan-then-execute.
- *LLM-centric framing.* Rejected per round 2 TA pushback (item 8). The substrate is the architectural locus; LLM contributes within bounded discipline. Framing the LLM as central understates substrate's responsibilities.

**Downstream consequences.**

- *D-086:* Tool surface schema operationalizes the thin-primitives commitment.
- *D-087:* Two-layer Guardrail enforcement (schema + semantic governance) realizes the substrate-side governance commitment.
- *D-088:* Replay equivalence semantics and eighth refusal kind realize the substrate-orchestration commitments for state, identity, and budget-exhaustion routing.
- *Theme 6 (prompt management).* Prompts engineer LLM behavior within the substrate-bounded tool surface; eval suite measures per-call substrate compliance.
- *Theme 7 (quality envelope).* Per-archetype quality thresholds calibrated against per-tool-call observability; model routing operational, not architectural.

**References.**

- `substrate_3_generation/SPEC.md` §6.2 (integration topology and substrate framing)
- D-070 (Theme 1; constrained interpretation engine; Guardrail 1; failure-loud philosophy)
- D-071 (Theme 2; semantic_context, governance_context, operational_context separation)
- D-072 (Theme 2; binary draft/refusal outcome protocol)
- D-073 (Theme 2; refusal taxonomy with typed structured payloads)
- D-074 (Theme 2; llm_calls as substrate-3-adjacent operational observability)
- D-075 (Theme 2; Guardrail 2; explanation_hash)
- D-077 (Theme 3; reasoning phases — interpretation / grounding / governance)
- D-083 (Theme 4; Guardrail 3; bounded decomposition; admissibility_layer; cause discipline)

---


## D-086 — Thin tool surface schema: three semantic primitives with substrate as admissibility authority [Theme 5]

**Date:** 2026-05-19
**Substrates affected:** [S3]
**Status:** active

**Decision.** Substrate-3 exposes three thin semantic primitives to the LLM, with substrate-side orchestration internal. The LLM does not author admissibility; the substrate computes admissibility from S1 + substrate-2 taxonomy + the substrate's admissibility logic.

**Three tools:**

**(1) `propose_semantic_intent(requirement_excerpt, intent_descriptor)`**

The LLM proposes what the requirement implies semantically. Parameters:

- `requirement_excerpt: string` — Guardrail 3 anchor. Excerpt from the request's requirement text supporting the proposed intent. Mandatory (Layer A schema enforcement); substrate validates substantive relevance to the proposed intent at Layer B (D-087).
- `intent_descriptor: typed structure` — substrate-authorized fields:
  - `archetype_hint: enum` (substrate-2 archetypes, 5 values)
  - `target_subject_hint: structured reference` (S1 entity ref or descriptive selector)
  - `polarity_hint: enum` (positive | negative, derived per D-083 c; not authoritative — substrate may reinterpret based on grounding signal)
  - `failure_mode_framing: optional structured description` (for negatives; identifies the distinct failure mode the requirement implies per D-083 d)
  - `claim_kind_hint: optional enum` (substrate-2 claim_kinds; substrate may select a different claim_kind if grounding signal is stronger)

Substrate processing on receipt:

1. Validates Layer A schema; rejects malformed calls.
2. Maps intent_descriptor against substrate-2 taxonomy + Guardrail 1 substantive enforcement (archetype × claim_kind semantically meaningful for target subject).
3. Validates requirement_excerpt's substantive relevance (Layer B Guardrail 3 enforcement).
4. Derives candidate(s) internally — candidate enumeration is substrate orchestration, NOT LLM tool calls.
5. For each derived candidate, computes admissibility against S1 constraint structure using substrate-3's admissibility logic (Layer 1 vs Layer 2 per D-083 e; Guardrail 1 grounding checks).
6. Records dismissed candidates with typed `dismissal_reason` (D-076) in `attempted_interpretation`.
7. Returns to LLM with structured response: admissibly-grounded candidates (≥1 → continue; 0 → routes through substrate to refusal emission).

**(2) `select_canonical(candidate_refs, selection_rationale)`**

When the substrate has presented multiple admissibly-grounded candidates for one failure mode, the LLM selects the canonical (per D-083 d highest-specificity discipline). Parameters:

- `candidate_refs: list of path_ids` — refs to substrate-presented admissibly-grounded candidates.
- `selection_rationale: typed structure` — substrate-authorized:
  - `selected_path_id: path_id`
  - `rationale_kind: enum` (highest_specificity | only_admissible | other_substrate_authorized)
  - `dismissed_alternatives_with_reason: list of (path_id, dismissal_reason)` — uses D-076 enum; typically `lower_specificity` for the highest-specificity discipline.

Substrate processing on receipt:

1. Validates Layer A schema.
2. Validates Layer B: rationale_kind matches the substrate's view of the candidates; dismissed_alternatives's reasons accurately characterize substrate's reasoning.
3. Records selection in `attempted_interpretation.selected_path_id`.
4. Auto-skipped when only one admissibly-grounded candidate exists; substrate auto-selects.

**(3) `emit_outcome(outcome_kind, payload)`**

The LLM emits the final structured outcome per D-072. Parameters:

- `outcome_kind: enum` (`draft` | `refusal`).
- `payload`:
  - For `draft`: claim (substrate-2 structured claim ref), recipe (substrate-2 structured recipe ref), admissibility_layer (substrate-authored — LLM transcribes from substrate-presented value, not asserts).
  - For `refusal`: refusal_kind (D-073 enum, now 8 values), refusal_payload (per D-073's per-kind typed schema; including D-083 cause for `no-admissible-negative-scenario-found` and D-088 budget payload for `operational-budget-exhausted`).

Substrate processing on receipt:

1. Validates Layer A schema.
2. Validates Layer B: emitted claim references admissibly-grounded candidate from `attempted_interpretation`; emitted recipe respects D-080 recipe-kind discipline; admissibility_layer matches substrate-authored value (D-083 e; not LLM-asserted).
3. Validates D-072 no-silent-drops invariant: every requirement in the input is explicitly resolved.
4. Constructs `GenerationOutcome` per D-072 with `attempted_interpretation`, `explanation_hash` (computed per D-088 over semantic substance), and references to operational telemetry in `llm_calls`.

**Substrate-side orchestration internal flow:**

The substrate's per-request lifecycle (not exposed as tools):

1. *Request receipt.* `GenerationRequest` per D-071; semantic_context + governance_context + operational_context parsed.
2. *Per-requirement orchestration loop.* For each requirement in the batch:
   a. Solicit `propose_semantic_intent` from LLM.
   b. Process intent: derive candidates, compute admissibility, record dismissals.
   c. If ≥2 admissibly-grounded candidates per failure mode, solicit `select_canonical`; else auto-select (or auto-refuse).
   d. Construct partial `attempted_interpretation` for this requirement.
3. *Outcome composition.* Across requirements, the substrate composes overall outcome state; verifies no-silent-drops.
4. *`emit_outcome` solicitation.* Substrate solicits final emission from LLM with full context of resolved interpretations.
5. *Ledger writes.* `GenerationOutcome` to semantic ledger; `llm_calls` to operational telemetry.

Candidate derivation, admissibility evaluation, dismissal recording, canonical auto-selection — all substrate-internal. Free to evolve per Themes 6/7 calibration without changing the LLM contract.

**Substrate is the admissibility authority.**

Per round 2 TA pushback (item 2): admissibility is substrate governance truth, not LLM-authored interpretation. The mechanical realization:

- `admissibility_layer` is substrate-authored (D-083 e at artifact level).
- The LLM never has a tool parameter where it asserts a candidate's admissibility_layer; the substrate computes it.
- In `emit_outcome`, the LLM transcribes the substrate-authored admissibility_layer onto the artifact (Layer A: presence required; Layer B: must match substrate-presented value).

The LLM proposes semantic intent and selects among presented options; the substrate determines what counts as grounding, what layer applies, and which candidates are admissible.

**Rationale.**

The three-tool factorization emerged from the round 2 TA pushback (item 1) to avoid baking phase-shaped reasoning choreography into protocol law. Concretely:

- `propose_semantic_intent` is durably semantic — what the requirement implies. Stable across reasoning topologies.
- `select_canonical` is durably semantic — judgment among substrate-presented options. Stable across decomposition strategies.
- `emit_outcome` is durably structural — D-072's binary protocol. Stable across orchestration approaches.

Substrate orchestration (candidate derivation, admissibility evaluation, dismissal recording) lives below the protocol surface, free to evolve.

The substrate-as-admissibility-authority commitment (item 2) follows naturally: admissibility is substrate logic computing against S1; if the LLM asserted admissibility, the substrate would have to second-guess every assertion, which is operationally fragile and semantically wrong (admissibility is substrate truth).

**Alternatives considered.**

- *Phase-shaped six-tool surface (original draft).* Rejected per TA item 1. Bakes current reasoning choreography into protocol; freezes Theme 6/7 experimentation space.
- *Single-tool emit-everything API.* Rejected. Loses incremental substrate feedback; reverts to structured-JSON-equivalent semantics.
- *LLM-authored admissibility with substrate validation.* Rejected per TA item 2. Even with validation, the LLM frames the question; substrate authority shifts unacceptably.

**Downstream consequences.**

- *D-087:* Layer A enforcement (schema validation) lives at tool boundary; Layer B enforcement (substantive semantic governance) lives in substrate processing. Theme 5 specifies the Layer B substantive rules per tool.
- *D-088:* Multi-turn statefulness operates over these three tools; rejected calls categorized per item 3.
- *Theme 6:* Prompts engineer LLM behavior to use the three tools effectively; eval measures per-tool substantive compliance.
- *Theme 7:* Substrate orchestration evolution (better candidate derivation, sharper admissibility evaluation) calibrated through per-archetype quality envelopes; tool surface stable.

**References.**

- `substrate_3_generation/SPEC.md` §6.3 (tool surface schema)
- D-070 (Theme 1; Guardrail 1; archetype × claim_kind × trigger_kind × recipe_kind)
- D-071 (Theme 2; request shape)
- D-072 (Theme 2; outcome protocol)
- D-073 (Theme 2; refusal taxonomy; typed payloads)
- D-076 (Theme 2; dismissal_reason vocabulary)
- D-077 (Theme 3; reasoning phases)
- D-080 (Theme 3; recipe-kind discipline)
- D-083 (Theme 4; admissibility_layer; bounded decomposition; cause discipline)
- D-085 (Theme 5; integration topology and substrate framing)

---


## D-087 — Two-layer Guardrail enforcement and clean separation of operational telemetry from semantic provenance [Theme 5]

**Date:** 2026-05-19
**Substrates affected:** [S3, with downstream consequences for substrate-3 schema design and Theme 6 eval suite]
**Status:** active

**Decision.** Theme 5 commits substrate-3 to two-layer Guardrail enforcement (schema validation + substrate-side semantic governance validation) and cleanly separates operational telemetry from semantic provenance.

**(a) Two-layer Guardrail enforcement.**

Per round 2 TA pushback (item 5): typed schemas constrain vocabulary, structure, and references — but do not constrain semantic misuse, shallow grounding, misleading decomposition, or weak requirement anchoring. Schemas are *necessary but not sufficient*. Guardrail enforcement is two-layered:

*Layer A — Tool-boundary schema validation (necessary).* Validates at the tool emission boundary:

- *Substrate-authorized vocabulary at vocabulary positions.* All enum-typed parameters bounded to substrate-2 taxonomy or substrate-3 reasoning vocabulary. Archetype ∈ {data-behavior, configuration, permission, ui, integration}; claim_kind ∈ substrate-2 taxonomy; dismissal_reason ∈ D-076 enum (8 values); refusal_kind ∈ D-073 enum (8 values post-Theme 5); cause ∈ D-083 enum (3 values); admissibility_layer ∈ D-083 enum (2 values).
- *Structural well-formedness.* Required parameters present; types match schemas.
- *Guardrail 3 syntactic precondition.* `requirement_excerpt` present on every `propose_semantic_intent`; references resolvable to the request's requirement text.
- *S1 entity refs.* All S1 entity references validated as existing at the current `s1_version_seq` per D-071's semantic_context.

Layer A violations are *operational* — the LLM emitted ill-formed or vocabulary-invalid tool calls. They route to substrate-side typed-feedback correction within the same generation (incremental correction per D-085 rationale 4) or, on persistent violation, to `structural-validation-failure` refusal.

*Layer B — Substrate-side semantic governance validation (sufficient).* Validates during substrate orchestration:

- *Guardrail 1 substantive enforcement.* The proposed archetype × claim_kind combination is semantically meaningful for the referenced S1 entities. Example: `capability-claim` on an S1 Object is meaningful; `capability-claim` on an S1 ValidationRule is not — Layer A would accept both as structurally valid; Layer B rejects the latter.
- *Guardrail 2 substantive enforcement.* Substrate-3 reasoning artifacts semantically appropriate, not just structurally valid. Example: `lower_specificity` dismissal_reason emitted only when a higher-specificity alternative exists in the substrate's reasoning; not arbitrarily applied.
- *Guardrail 3 substantive enforcement.* `requirement_excerpt` semantically supports the proposed intent. Substrate verifies excerpt's relevance to the candidate's claim_kind and subject — not just that the excerpt is a syntactic substring of the requirement text.
- *Bounded decomposition substantive enforcement (D-083 d).* Canonical selection respects highest-specificity discipline; the LLM's `selection_rationale` in `select_canonical` must match the substrate's view of the candidates' specificity.
- *Admissibility substantive enforcement (D-083 e).* admissibility_layer assignment respects Layer 1 vs Layer 2 distinction's semantic meaning; substrate-authored.

Layer B violations are *semantic findings* — the substrate determined the proposed intent or selection doesn't substantively satisfy the architectural commitment. They route to substrate-orchestrated dismissals (recorded in `attempted_interpretation`) or to typed refusals (per D-073 taxonomy).

Both layers are required. Schema validation alone is insufficient; substrate-side semantic governance is what makes Guardrail enforcement substantive rather than performative.

**(b) Clean separation: operational telemetry vs semantic provenance.**

Per round 2 TA pushback (item 4): `llm_calls` cannot serve simultaneously as operational telemetry and semantic provenance. Theme 5 cleanly separates the two.

*Operational telemetry — `llm_calls` (per D-074 substrate-3-adjacent):*

Schema (specified at Theme 5):

```
llm_calls: {
  call_id:               uuid (PK)
  generation_outcome_id: uuid (FK → generation_outcomes)
  tool_name:             string (propose_semantic_intent | select_canonical | emit_outcome)
  raw_parameters:        jsonb (untyped; for debugging)
  raw_response:          jsonb (untyped; for debugging)
  operational_outcome:   enum (success | transient_failure | operational_error | rejected_for_correction)
  attempt_index:         integer (1, 2, ... within the same logical tool emission if Layer A correction loops occurred)
  timing_start:          timestamp
  timing_duration_ms:    integer
  token_count_input:     integer
  token_count_output:    integer
  model_identifier:      string (e.g., claude-opus-4-7)
}
```

Used for: cost analysis, latency monitoring, error tracking, operational debugging, per-model performance comparison (Theme 7 calibration). NOT used for replay determinism, semantic eval, transparency, or refusal analysis.

*Semantic provenance — `attempted_interpretation` (part of `generation_outcomes`, semantic ledger):*

Schema (refined at Theme 5):

```
attempted_interpretation: {
  candidate_paths:        list of structured candidates
    Each: {
      path_id:               string
      archetype:             enum (substrate-2 archetypes)
      claim_kind:            enum (substrate-2 claim_kinds)
      subject_refs:          list of S1 entity refs
      requirement_anchor:    structured (per-Guardrail-3 traceability)
      admissibility_status:  enum (admissibly_grounded | dismissed)
      admissibility_layer:   enum (layer_1 | layer_2; populated when admissibly_grounded; substrate-authored)
      dismissal_reason:      D-076 enum (populated when dismissed)
    }
  selected_path_id:       string (refs admissibly_grounded candidate; per D-083 d canonical)
  dismissed_alternatives_by_reason: structured (D-076 reason → list of dismissed path_ids; bounded set, not ordered list)
}
```

Used for: replay determinism (via `explanation_hash` per D-088), semantic eval, transparency surfacing, refusal analysis.

Tension resolved: `llm_calls` is bytes-on-the-wire telemetry; `attempted_interpretation` is the substrate's semantic reasoning record. Different tables; different code paths; different consumers. The "retires to substrate-2 provenance when get_provenance ships" disposition (per D-074) applies to `attempted_interpretation`, not to `llm_calls`.

**Rationale.**

Two-layer enforcement (a) is essential because schema validation alone cannot prevent semantic misuse. The TA's example holds: a propose_semantic_intent with all valid enum values and a technically-valid requirement_excerpt substring could still semantically misuse the requirement. Layer B is what protects substrate-3's mission integrity beyond mere structural compliance.

Telemetry-provenance separation (b) was implicit in D-074 but not mechanically clean. Theme 5 specifies the schema boundary so both consumers — operational analysts and semantic eval engineers — have clean models without cross-contamination.

**Alternatives considered.**

- *Schema-validation-only as Guardrail enforcement.* Rejected per TA item 5. Schemas constrain vocabulary but not semantic substance; substrate-3 mission integrity requires substantive governance.
- *Single unified `llm_calls`-style table doubling as semantic provenance.* Rejected per TA item 4. Tension between operational and semantic concerns is real; separation is cleaner and matches D-074's destination disposition.
- *Layer B as optional / progressive enhancement.* Rejected. Layer B is necessary for Guardrail integrity; making it optional erodes substrate-3 mission discipline.

**Downstream consequences.**

- *D-088:* Replay equivalence computed over `attempted_interpretation` semantic substance, not over `llm_calls` operational trace. Sharp tension resolution.
- *Theme 6 (prompt management).* Prompts engineer LLM behavior to satisfy Layer A and Layer B together; eval suite measures per-Guardrail per-layer compliance (Layer A acceptance rate; Layer B substantive correctness rate).
- *Theme 7 (quality envelope).* Per-archetype Layer B substantive correctness thresholds calibrated separately from Layer A schema compliance.
- *Substrate-3 schema design:* `llm_calls` table and `attempted_interpretation` structure both formalized for v1 implementation; both are substrate-3-implementation territory per Theme 5.

**References.**

- `substrate_3_generation/SPEC.md` §6.4 (two-layer Guardrail enforcement), §6.5 (telemetry vs provenance separation)
- D-070 (Theme 1; Guardrail 1)
- D-071 (Theme 2; semantic_context, governance_context, operational_context)
- D-072 (Theme 2; outcome protocol)
- D-074 (Theme 2; llm_calls operational observability)
- D-075 (Theme 2; Guardrail 2)
- D-076 (Theme 2; dismissal_reason vocabulary)
- D-083 (Theme 4; Guardrail 3; admissibility_layer; bounded decomposition; cause)
- D-085 (Theme 5; integration topology)
- D-086 (Theme 5; tool surface schema)

---


## D-088 — Multi-turn statefulness semantics, replay equivalence over semantic substance, eighth refusal kind operational-budget-exhausted [Theme 5]

**Date:** 2026-05-19
**Substrates affected:** [S3, with downstream consequences for Theme 6 eval suite and Theme 7 quality envelope]
**Status:** active

**Decision.** Theme 5 clarifies multi-turn tool-use statefulness semantics, tightens D-071/D-075 replay equivalence to semantic substance (not operational trace), and introduces the eighth refusal kind `operational-budget-exhausted` as a third refusal category (operational, alongside invalidity and policy).

**(a) Multi-turn statefulness: rejected tool calls are operational, not semantic.**

Per round 2 TA pushback (item 3): multi-turn tool-use creates conversational statefulness. Theme 5 clarifies which kinds of state are semantic and which are operational.

Rejected tool call categorization:

| Rejection type | Origin | Category | In semantic history? |
|---|---|---|---|
| Schema violation (LLM emits malformed tool call) | LLM error | operational | no |
| Vocabulary violation (LLM emits value outside enum) | LLM error | operational | no |
| Layer A governance violation (e.g., requirement_excerpt missing on propose_semantic_intent) | LLM error | operational | no |
| Operational error (timeout, rate limit, model unavailable) | infrastructure | operational | no |
| Substrate-derived dismissal of a proposed intent | substrate orchestration | semantic | yes — recorded in `attempted_interpretation.dismissed_alternatives_by_reason` |
| Layer B governance finding (e.g., requirement_excerpt doesn't substantively support proposed intent) | substrate orchestration | semantic | yes — recorded as dismissal with appropriate D-076 reason |

The first four categories are LLM-side or infrastructure errors; they are recorded in operational telemetry (`llm_calls.operational_outcome = rejected_for_correction`) and do not enter semantic provenance. They do not affect `attempted_interpretation`; they do not affect `explanation_hash`.

The last two categories are substrate-derived semantic findings; they are substrate orchestration internal (not LLM tool call rejections under D-086's reshape) and are recorded in `attempted_interpretation`.

Net result: multi-turn statefulness exists operationally (LLM does see prior rejections and adapts); semantic identity is deterministic given semantic_context + governance_context.

**(b) Replay equivalence over semantic substance.**

Per round 2 TA pushback (item 6): the previous framing — same semantic_context + governance_context → same explanation_hash — was too strong if explanation_hash was computed over operational trace. Multi-turn variation would produce false replay regression signals. Theme 5 tightens D-075's explanation_hash semantics and D-071's equivalence algebra to be computed over semantic substance.

*Semantic substance (in scope for explanation_hash):*

- Set of admissibly-grounded candidates per failure mode (unordered set; not ordered list).
- Canonical selection per failure mode (selected_path_id).
- Set of dismissed alternatives per failure mode, indexed by dismissal_reason category (D-076 category distribution; not the specific sequence of dismissals).
- Admissibility_layer per emitted artifact (D-083 e).
- Outcome kind (draft | refusal) and outcome payload semantics:
  - For draft: claim ref + recipe ref + admissibility_layer.
  - For refusal: refusal_kind + refusal payload semantics (cause for `no-admissible-negative-scenario-found`, budget_dimension for `operational-budget-exhausted`, etc.).

*Operational trace (out of scope for explanation_hash):*

- Ordering of LLM tool calls within the generation.
- Specific tokens in intermediate LLM responses.
- Number of operational corrections (schema/vocabulary/Layer A violations corrected mid-generation).
- LLM model identifier (operational_context).
- Specific timing or token counts.

*Updated D-071 equivalence algebra (refinement, not contradiction):*

- Same semantic_context + same governance_context + different operational_context → expected identity_hash + explanation_hash match.
- "Match" defined over semantic substance per above; operational trace variation is permitted and expected.
- Explanation-hash drift events (per D-075) fire on semantic substance divergence, NOT on operational trace divergence.

*Replay equivalence definition:*

Two generations are replay-equivalent iff their identity_hash and explanation_hash match, computed over semantic substance. Replay regressions surface real semantic drift; operational variation is filtered out by construction.

This refinement is a tightening of D-075's commitment, not a reversal. D-075 committed to explanation_hash as a mechanical equivalence primitive; Theme 5 specifies the semantic-substance computation rule. D-071 committed to the equivalence algebra; Theme 5 specifies "match" as semantic substance match.

Forward-compat reservation: the precise computation of explanation_hash over semantic substance may be tuned in Theme 7 quality envelope calibration as substrate-3 observes drift patterns in production.

**(c) Eighth refusal kind: `operational-budget-exhausted`.**

Per round 2 TA pushback (item 7): collapsing budget exhaustion into `structural-validation-failure` pollutes analytics. Budget exhaustion is operational incompletion, not structural invalidity. Taxonomy expansion is justified.

*Updated refusal taxonomy at Theme 5 close (8 kinds across 3 categories):*

| RefusalKind | Category | Origin theme |
|---|---|---|
| `underspecified-requirement` | invalidity (input) | Theme 1 |
| `no-relevant-context` | invalidity (grounding) | Theme 1 |
| `ambiguous-reference` | invalidity (resolution) | Theme 1 |
| `ungrounded-claim` | invalidity (admissibility) | Theme 1 |
| `structural-validation-failure` | invalidity (output) | Theme 1 |
| `low-generation-confidence` | policy (threshold) | Theme 2 (D-073) |
| `no-admissible-negative-scenario-found` | policy (scope) | Theme 4 (D-083) |
| `operational-budget-exhausted` | operational (incompletion) | Theme 5 (this entry) |

The third category axis — operational — is new. Substantive distinction:

- *Invalidity refusals* are about content/structure quality. The substrate examined the request and could not produce a substantively valid output.
- *Policy refusals* are about substrate-deliberate restraint. The substrate could produce output but chose not to, per policy (confidence threshold; scope of grounded negatives).
- *Operational refusals* are about substrate-runtime-resource constraints. The substrate ran out of budget before completing reasoning.

All three are genuine refusal causes that downstream consumers should distinguish in eval, analytics, and reliability metrics.

*Feedback payload for `operational-budget-exhausted`:*

```
operational-budget-exhausted: {
  budget_dimension:         enum (token | time | tool_call_count)
  budget_limit:             typed numeric (the cap from operational_context.budgets)
  budget_consumed:          typed numeric (the amount actually consumed before exhaustion)
  partial_state_at_exhaustion: {
    candidates_proposed:       count
    candidates_admissibly_grounded: count
    canonicals_selected:       count
    requirements_resolved:     count
    requirements_unresolved:   count
  }
  recommended_budget_increase: optional typed numeric (substrate-3 may suggest a budget that would have completed based on consumption rate)
}
```

The `partial_state_at_exhaustion` preserves semantic substance up to the exhaustion point. Replay equivalence applies: replaying with the same budgets should produce equivalent partial state (same candidates proposed and dismissed up to exhaustion).

**Rationale.**

The three commitments cohere:

- Multi-turn statefulness clarification (a) categorically separates LLM-side / infrastructure operational events from substrate-derived semantic findings. Establishes the foundation for semantic-substance replay equivalence.
- Replay equivalence over semantic substance (b) filters operational variation out of drift signals, preserving D-075's drift-detection capability while preventing false regressions.
- Operational refusal category (c) provides downstream consumers with categorically clean refusal analytics, separating operational incompletion from semantic invalidity and policy restraint.

Together they make substrate-3's semantic identity robust to operational variation while preserving sharp signals when real semantic drift occurs.

**Alternatives considered.**

- *Rejected tool calls in semantic history.* Rejected per TA item 3. Pollutes semantic identity with operational variation; breaks replay equivalence.
- *Replay equivalence over full operational trace.* Rejected per TA item 6. Too fragile; false drift signals from operational variation.
- *Budget exhaustion as `structural-validation-failure` subtype.* Rejected per TA item 7. Operational incompletion is categorically different from structural invalidity; collapsing them pollutes analytics.
- *New operational category axis with multiple operational refusal kinds.* Considered. V1 ships with one operational kind (`operational-budget-exhausted`); future operational refusal kinds (e.g., `operational-model-unavailable`, `operational-rate-limit-exhausted`) can be added if Theme 7 quality envelope identifies need.

**Downstream consequences.**

- *Theme 6 (prompt management).* Eval suite measures three categories of refusal separately; per-category rates inform prompt engineering priorities.
- *Theme 7 (quality envelope).* Per-archetype expected refusal rates broken down by category; operational-budget-exhausted rate informs budget calibration.
- *Substrate-3 implementation:* explanation_hash computation must operate over `attempted_interpretation` semantic-substance structure (not over `llm_calls` operational trace).
- *Eval and replay infrastructure:* replay tooling operates over semantic ledger; operational telemetry is parallel concern.

**References.**

- `substrate_3_generation/SPEC.md` §6.6 (multi-turn statefulness and replay equivalence), §6.7 (eighth refusal kind)
- D-071 (Theme 2; equivalence algebra; operational_context.budgets)
- D-072 (Theme 2; outcome protocol)
- D-073 (Theme 2; refusal taxonomy with typed payloads)
- D-074 (Theme 2; llm_calls; semantic ledger vs operational observability)
- D-075 (Theme 2; explanation_hash as mechanical equivalence primitive)
- D-083 (Theme 4; admissibility_layer; bounded decomposition; cause discipline)
- D-085 (Theme 5; integration topology)
- D-086 (Theme 5; tool surface schema)
- D-087 (Theme 5; telemetry vs provenance separation)

---


## D-089 — Prompt management architecture with bounded co-evolution and policy-adjacent surface acknowledgment [Theme 6]

**Date:** 2026-05-19
**Substrates affected:** [S3, with downstream consequences for substrate-3 evolution discipline and Theme 7 quality envelope]
**Status:** active

**Decision.** Theme 6 commits substrate-3 to a prompt management architecture covering versioning, composition, lifecycle, and the prompt-substrate-orchestration co-evolution discipline. Prompts are explicitly acknowledged as a policy-adjacent surface within substrate-bounded governance.

**(a) Prompt versioning.**

- Sequential `prompt_template_version` per template file. Increments on any change.
- Storage in version-controlled repository at `substrate-3/prompts/`. Version tied to git commit.
- Immutable per version. Once a `prompt_template_version` is referenced by a `GenerationRequest`, that content is frozen. Required for replay determinism.
- Forward-compat with rollback. When a new version ships, old versions remain available for replay indefinitely. Substrate maintains a prompt registry mapping version → content.

Per D-071: prompts are operational_context. `prompt_template_version` field exists alongside `llm_model_identifier`, `retry_policy`, `budgets` in the operational axis. Per D-088: operational variation does not affect identity_hash; same semantic_context + governance_context + different operational_context (including different prompt_template_version) → expected identity_hash match.

**(b) Prompt composition.**

V1 composition is layered:

- Base system prompt. Describes substrate-3's three-tool surface (D-086), Guardrail commitments, mission as constrained semantic orchestration runtime (D-085). Same across all generations.
- Per-archetype fragment. Extends base with archetype-specific guidance for grounding sources and admissibility patterns. V1 ships three archetype fragments (data-behavior, configuration, permission); UI and integration fragments deferred to a future Theme 6 calibration cycle.
- Per-request context. Request-specific content composed at request time (requirements being processed, S1 context, archetype_hint when supplied). Not separately versioned; part of the request itself.
- Feedback templates. Substrate-emitted feedback for Layer A violations during multi-turn correction loops.

The `prompt_template_version` references the composed (base + active fragments) content. Composition is mechanical; substrate-3 implementation territory.

**(c) Prompt lifecycle.**

- Authoring. Prompts authored by substrate-3 maintainers; reviewed via pull request.
- Eval gate. New prompt versions must pass the eval suite (per D-090) before merge.
- Deployment. Merge to main triggers prompt registry update; new version becomes available; existing generations continue using their referenced version (immutability).
- Deprecation. Old versions remain available for replay indefinitely.

**(d) Prompts as a policy-adjacent surface within bounded governance.**

Per round 2 TA pushback (item 2): prompts encode admissibility heuristics, refusal tendencies, decomposition preferences. They are behavior-shaping policy surfaces, not merely contextual guidance.

Architectural framing:

- Substrate governance authority (governance_context, Layer B enforcement). The substrate enforces what behaviors are permitted — Guardrail 1/2/3 compliance, bounded decomposition, semantic-substance discipline. Governance-owned.
- Prompts within bounded space (operational_context). Within the substrate-permitted behavior space, prompts influence what behaviors the LLM tends toward — aggressiveness vs conservatism in negative proposal, breadth of candidate generation, decomposition style. Operational but policy-adjacent.

Operational discipline:

1. Prompt review includes governance-implications check. Not just performance ("does this prompt produce better artifacts?") but behavioral consequences ("does this prompt shift refusal aggressiveness in ways inconsistent with substrate's mission?").
2. Major prompt fragment changes treated like architectural changes. Eval-gated; reviewed for behavior-shaping consequences; Theme 7 quality envelope re-validated.
3. Minor prompt tuning (typos, clarifications) treated like operational changes. Standard PR review.
4. Prompt-driven behavior changes documented in EVOLUTION.md. When a prompt change materially alters Layer B-permitted behavior (e.g., shifts refusal-rate distribution beyond calibrated thresholds), the change is recorded as a substrate evolution event.

This does not move prompts to governance_context. Per D-071, prompts remain operational_context. The behavioral classification (policy-adjacent) is metadata about how the team manages this surface, not a structural recategorization.

**(e) Prompt-substrate-orchestration as bounded co-evolution.**

Per round 2 TA pushback (item 6): prompts and substrate orchestration inevitably co-evolve. The contract is not fully decoupled independent evolution but bounded co-evolution.

Co-evolution discipline:

- Each side has its own design cycle. Substrate orchestration evolves through substrate-3 architectural cycles. Prompts evolve through prompt design cycles (D-090 eval-gated PR review).
- Changes on either side may require co-evolution on the other. Substrate orchestration changes that affect tool-rejection patterns or candidate availability invalidate prompt assumptions. Prompt changes that shift LLM behavior outside Layer B governance bounds will surface as Layer B violations.
- Major orchestration changes trigger prompt re-validation. The substrate-3 design-cycle process commits to: substantive orchestration changes trigger a prompt re-validation pass before deployment.
- Major prompt changes trigger orchestration eval. Substantive prompt changes trigger a substrate-side eval re-run before deployment.
- Migration costs explicitly acknowledged. Substrate evolution and prompt evolution have costs to the other side.
- Replay corpus shifts during co-evolution. When either side undergoes substantive change, the replay corpus's drift evaluation must account for the change. Drift signals during planned co-evolution are expected, not regressions (per D-090 drift framework).

**Rationale.** Round 2 TA integration accepted: prompts are policy-adjacent (item 2), prompt-substrate contract is bounded co-evolution (item 6). Both refine the operational discipline surrounding substrate-3's locked architectural commitments without changing the architectural categorization (prompts remain operational_context per D-071).

**Alternatives considered.**

- *Move prompts to governance_context.* Rejected. Architectural categorization per D-071 is sound: prompts are operational, not governance. Behavioral classification (policy-adjacent) is metadata about management discipline, not structural recategorization.
- *Frame prompts as pure guidance, decoupled from policy.* Rejected per TA item 2. Practically inaccurate; prompts shape behavior significantly within Layer B's permitted space.
- *Frame prompt-substrate as fully decoupled evolution.* Rejected per TA item 6. Inevitable co-evolution; bounded co-evolution is the honest contract.
- *Ship UI and integration prompt fragments at v1.* Considered. Deferred. UI and integration archetypes are narrower at v1 (per D-081 minimal coverage; D-082 operational-only admissibility); ship base + 3 archetype fragments first, expand when these archetypes have larger production footprint.

**Downstream consequences.**

- *D-090:* Eval suite measures prompt changes for behavior-shaping consequences; drift evaluation distinguishes prompt-driven evolution from semantic regression.
- *D-091:* Model routing decisions interact with prompt selection; same prompt under different models produces different behaviors.
- *Theme 7 quality envelope:* Prompt-driven behavior thresholds calibrated per archetype.
- *Substrate-3 maintenance discipline:* Documented in substrate-3 maintenance docs; prompt PRs follow governance-implications review.

**References.**

- `substrate_3_generation/SPEC.md` §7.2 (prompt management architecture), §7.3 (prompts as policy-adjacent surface and bounded co-evolution)
- D-071 (Theme 2; operational_context.prompt_template_version)
- D-077 (Theme 3; archetype framework)
- D-081 (Theme 3; UI archetype minimal scope)
- D-082 (Theme 3; integration archetype operational-only admissibility)
- D-085 (Theme 5; substrate-3 as constrained semantic orchestration runtime)
- D-086 (Theme 5; thin tool surface; substrate-side orchestration evolution)
- D-087 (Theme 5; two-layer Guardrail enforcement)
- D-088 (Theme 5; explanation_hash semantic substance; operational variation tolerance)
- D-090 (Theme 6; eval suite architecture)

---


## D-090 — Eval suite architecture with two-invariant replay equivalence and drift-as-evolution framework [Theme 6]

**Date:** 2026-05-19
**Substrates affected:** [S3, with downstream consequences for Theme 7 quality envelope and substrate-3 maintenance discipline]
**Status:** active

**Decision.** Theme 6 commits substrate-3 to an eval suite spanning four categories (correctness, quality, performance, drift), with explicit separation of two replay invariants (semantic continuity via identity_hash; transparency continuity via explanation_hash) and a drift evaluation framework that distinguishes regression from healthy architectural evolution.

**(a) Four eval categories.**

*Correctness evals (structural compliance).* Measure per-Guardrail Layer A acceptance rate per D-087.

- Layer A acceptance rate by tool (propose_semantic_intent | select_canonical | emit_outcome).
- Layer A acceptance rate by Guardrail (1: S1 entity refs valid; 2: vocabulary in substrate-authorized enums; 3: requirement_excerpt present).
- Layer A correction loops per generation.

*Quality evals (semantic appropriateness).* Measure per-Guardrail Layer B substantive correctness, per-archetype emission quality, per-refusal-kind appropriateness.

- Layer B substantive correctness rate by Guardrail.
- Per-archetype emission quality (does the generated test actually verify what it claims?).
- Per-claim-kind admissibility precision (does substrate admissibility evaluation match human-expert judgment?).
- Per-refusal-kind appropriateness (was the refusal correct? cause distribution within `no-admissible-negative-scenario-found` per D-083 b).
- Per-archetype Layer 1 vs Layer 2 distribution.

*Performance evals (operational).* From `llm_calls` operational telemetry per D-087 b.

- Cost per generation by archetype.
- Latency per generation by archetype.
- Per-model performance comparison (per D-091 routing).
- Budget exhaustion frequency (`operational-budget-exhausted` rate per archetype).

*Drift evals (replay).* From semantic ledger. See (b) and (c) for two-invariant framework and drift judgment discipline.

**(b) Two-invariant replay equivalence.**

Per round 2 TA pushback (item 1): replay equivalence is two invariants, not one. They are treated distinctly in eval.

*`identity_hash` (semantic continuity).* Per D-071's identity_hash commitment + D-088's semantic-substance refinement. Same outcome — same emitted claim, same recipe, same outcome_kind, same refusal_kind + payload semantics if refusal. Strict invariant. Drift indicates semantic regression: same input should produce same emitted output. Presumption of regression unless explained.

*`explanation_hash` (transparency continuity).* Per D-075 + D-088. Same `attempted_interpretation` semantic substance — same candidate set, same canonical selection, same `dismissed_alternatives_by_reason` category distribution, same admissibility_layer per artifact. Weaker invariant. Drift indicates reasoning trajectory varied, but emitted output may still be correct.

Per-archetype drift thresholds calibrated separately by Theme 7 per invariant. identity_hash thresholds tight (semantic regressions are real bugs). explanation_hash thresholds looser and contextualized (reasoning trajectories evolve with prompt refinement, substrate orchestration improvement, model updates).

This refines D-088's drift semantics. D-088 committed semantic-substance computation for both hashes; Theme 6 separates their downstream treatment in eval.

**(c) Drift as evolution framework.**

Per round 2 TA pushback (item 5): drift signals trigger investigation, never auto-failure. Each drift event evaluated against architectural improvement criteria.

Drift evaluation framework:

- identity_hash drift — sharp regression signal. Triggers investigation. Same input previously produced output X; now produces output Y. Judgment criteria:
  - If Y substantively wrong → regression. Investigated as a bug.
  - If Y substantively better (sharper refusal, more specific grounding, narrower admissibility) → accepted improvement.
- explanation_hash drift with identity_hash stable — weaker signal. Investigated, but presumption shifts toward explanation refinement or healthy evolution. Same output emitted; reasoning trajectory changed.
  - Judgment criteria: did the substrate get sharper (more targeted dismissal reasons, more specific candidate paths)? Likely healthy.
- explanation_hash drift with identity_hash drift — concurrent signals. Both surfaces affected; investigated together.

Drift events surface in EVOLUTION.md alongside substrate code changes and prompt changes. Each annotated with substrate-3 maintainer judgment: `regression` | `evolution` | `neutral`. Patterns inform Theme 7 quality envelope calibration.

Cultural commitment: substrate-3 maintainers explicitly tasked with judging drift as evolution vs regression, not auto-treating drift as failure. Theme 6 establishes the framework; Theme 7 calibrates judgment thresholds per archetype.

**(d) Ground truth strategy and v1 limits.**

Three sources:

- Curated test corpus. Maintainer-authored requirements with expected outcomes. V1 target: 200–500 cases. Maintained as substrate-3 evolves.
- Pilot customer feedback. Pilot review feeds back into curated corpus and Theme 7 calibration.
- Replay corpus. Each shipped generation enters mechanically. Replay evals run continuously.

V1 ground truth quality limits explicitly acknowledged:

- Curated corpus is small (hundreds, not thousands).
- Pilot customer feedback just starting.
- Replay corpus empty at v1 launch; only meaningful after 3–6 months accumulation.

V1 eval rigor calibrated to these limits. Layer A correctness mechanically self-validating. Layer B substantive correctness has limited ground truth at v1; relies on curated corpus. Per-archetype emission quality validates against curated expected outcomes initially; pilot feedback over time. Drift evals become meaningful as replay corpus accumulates.

Theme 7 quality envelope work will calibrate per-archetype thresholds against this evolving ground truth.

**(e) Eval cadence.**

- Pre-commit / CI. Every PR runs eval suite subset (correctness fully; quality against curated; performance smoke).
- Pre-release. Full eval suite before deploying new prompt template version or substrate version. Pass thresholds enforced.
- Continuous production. Sampling drift evals on every production generation. Aggregated drift event rate dashboard. Threshold-triggered alerts.
- Post-pilot. Pilot customer feedback batch-processed weekly; feeds into curated corpus and calibration.

**(f) Semantic adjudication theory acknowledged as unresolved.**

Per round 2 TA pushback (item 4): in ambiguous enterprise QA scenarios, two generations may both be grounded, admissible, requirement-supported, review-approved — and yet differ. Substrate-3 currently lacks a theory of canonical semantic correctness in the validity-space sense (correctness as space, not point).

Theme 6 ships eval against substrate-architectural-compliance (Guardrail compliance, admissibility discipline, refusal-kind appropriateness) rather than absolute semantic correctness. Forward-compat reservation in OPEN_QUESTIONS.md. Theme 7 quality envelope work may begin addressing the structural question; full resolution requires production data, longitudinal study, and likely formal work beyond v1 scope.

**Rationale.** Round 2 TA integration accepted: two-invariant separation (item 1), drift as evolution framework (item 5), semantic adjudication unresolved (item 4). Together they refine eval discipline without changing the locked architecture: identity_hash and explanation_hash both computed over semantic substance per D-088, but treated distinctly downstream; drift signals investigated, not auto-failures; eval framework explicit about what it measures (architectural compliance) and what remains unresolved (canonical semantic correctness in ambiguous cases).

**Alternatives considered.**

- *Single replay invariant (explanation_hash carrying all responsibility).* Rejected per TA item 1. Overloads one artifact; conflates semantic continuity with transparency continuity; produces false drift alarms; creates anti-evolution gravity.
- *Drift as auto-failure.* Rejected per TA item 5. Punishes healthy architectural evolution; substrate-3 maintainers tasked with judgment, not gated by mechanical drift detection.
- *Resolve semantic adjudication theory at Theme 6.* Rejected per TA item 4. Requires production data and longitudinal study; not blocking v1; forward-compat reservation.

**Downstream consequences.**

- *D-091:* Model routing decisions calibrated against eval framework (per-model behavioral profile, per-model identity_hash stability, per-model explanation_hash variation).
- *Theme 7 quality envelope:* Per-archetype thresholds for each eval category calibrated against this framework. Two-invariant separation enables tight identity_hash thresholds + contextual explanation_hash thresholds.
- *Substrate-3 maintenance discipline:* Drift event annotation in EVOLUTION.md becomes standard practice. Regression vs evolution judgments documented longitudinally.

**References.**

- `substrate_3_generation/SPEC.md` §7.4 (eval suite architecture), §7.5 (two-invariant replay equivalence), §7.6 (drift as evolution)
- D-071 (Theme 2; identity_hash; equivalence algebra)
- D-074 (Theme 2; llm_calls operational telemetry)
- D-075 (Theme 2; explanation_hash; drift events)
- D-083 (Theme 4; admissibility_layer; cause discipline; bounded decomposition)
- D-087 (Theme 5; two-layer Guardrail enforcement)
- D-088 (Theme 5; semantic substance for both hashes)
- D-089 (Theme 6; prompt management and bounded co-evolution)

---


## D-091 — Single-model-per-batch routing with model selection as behavior-shaping decision [Theme 6]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for Theme 7 quality envelope and per-batch operational discipline]
**Status:** active

**Decision.** Theme 6 commits substrate-3 to single-model-per-batch LLM routing at v1, chosen by dominant archetype, with explicit acknowledgment that model selection is a behavior-shaping operational decision affecting refusal aggressiveness, decomposition style, grounding conservatism, and ambiguity handling.

**(a) Model selection as behavior-shaping operational decision.**

Per round 2 TA pushback (item 3): different models do not merely differ in quality, speed, or token efficiency. They differ in semantic temperament — refusal aggressiveness, decomposition behavior, grounding conservatism, ambiguity handling. Model routing creates substrate-observable behavior variation.

Architectural categorization:

- Model identifier is operational_context (per D-071). Architectural categorization unchanged.
- Model selection affects substrate-observable behavior. Different models produce different Layer A acceptance rates, different Layer B substantive correctness rates, different per-archetype emission quality, different refusal-kind distributions.
- Theme 7 quality envelope calibrates per-model behavior expectations. Per-model behavioral profiles tracked alongside per-archetype thresholds.

Replay equivalence under model variation: per D-088, same semantic_context + governance_context + different operational_context (including different model_identifier) → expected identity_hash match. The emitted output should be model-invariant; model behavioral differences fall within Layer B governance bounds. If a different model produces a different emitted output, that's a substrate-governance failure (Layer B should have caught it), not a model-routing failure. In practice: model routing affects explanation_hash (different reasoning trajectories per model) but should not affect identity_hash (same emitted output). Theme 7 calibration validates empirically.

**(b) Single-model-per-batch in v1.**

Per round 2 TA pushback (item 7): different models inside one semantic batch creates instability — different refusal tendencies, decomposition styles, transparency shapes within one generation. Especially problematic given D-077's shared interpretation context across batches.

V1 routing commitment:

- Single model per batch. All requirements in one `GenerationRequest` use the same `operational_context.llm_model_identifier`.
- Dominant-archetype selection. The batch's model is determined by the most prevalent archetype across its requirements.
- Mixed-archetype batches default to the "complex" model (Claude Opus 4.7); captures the most demanding archetype's reasoning needs.
- Per-customer override preserved. `operational_context.llm_model_identifier` may be explicitly set per request, overriding the default.

Cross-archetype consistency benefits:

- Within a batch, all requirements receive consistent model cognition.
- D-077's shared interpretation context operates over consistent model behavior.
- Replay determinism easier to validate (model is batch-invariant within batch boundaries).

**(c) V1 model defaults per archetype.**

Calibrated by D-090 eval framework on curated corpus before v1 ships. Initial defaults (subject to refinement):

- Pure data-behavior batch → Claude Opus 4.7 (complex multi-constraint reasoning).
- Pure configuration batch → Claude Sonnet 4.7 (S1-lookup-dominant).
- Pure permission batch → Claude Opus 4.7 (recipe-kind discipline requires sharp judgment).
- Pure UI batch → Claude Sonnet 4.7 (narrow scope at v1).
- Pure integration batch → Claude Opus 4.7 (interaction complexity).
- Mixed-archetype batch → Claude Opus 4.7 (default to capability).

Defaults are operational, not architectural. Theme 7 quality envelope refines them as eval data accumulates and model capabilities evolve.

**(d) Future per-archetype within-batch routing deferred.**

V1 commits single-model-per-batch. Post-v1, finer per-archetype routing within batches may be revisited once eval infrastructure matures and per-archetype model behavior is well-characterized within a stable framework. Theme 6 commits this as deferred operational refinement, not architectural capability.

Forward-compat reservation: per-archetype within-batch routing may be enabled in future Theme 6 calibration cycles when:

- Per-archetype × model behavioral profiles are well-characterized via production data.
- Cross-model coherence within a batch can be validated empirically.
- The cost of per-archetype routing within batches is justified by quality gains.

**Rationale.** Round 2 TA integration accepted: model selection as behavior-shaping (item 3), single-model-per-batch (item 7). Both refine the operational discipline surrounding model routing without changing the architectural categorization (model_identifier remains operational_context per D-071). Architectural coherence within batches takes priority over theoretical per-archetype optimization at v1.

Cost/latency consequence: some single-archetype batches use Opus where Sonnet would have sufficed; some mixed batches use Opus where archetype-specific Sonnet would have been adequate for simpler components. Acceptable trade-off for architectural coherence; Theme 7 calibrates whether the cost is justified at scale.

**Alternatives considered.**

- *Per-archetype within-batch routing in v1.* Rejected per TA item 7. Creates cross-model semantic incoherence within one generation; problematic for D-077's shared interpretation context.
- *Single model for all generations (no archetype-based routing at all).* Rejected. Some archetypes (configuration, UI at v1) don't need Opus-level capability; some (data-behavior, permission, integration) do. Per-batch routing captures this without within-batch fracturing.
- *Model selection as semantic_context.* Rejected. Per D-071, model identifier is operational; same emitted output should result from same semantic + governance + different operational. Recategorizing to semantic_context would invalidate replay equivalence framework.

**Downstream consequences.**

- *D-090 eval framework:* Per-model behavioral profile tracked; per-model identity_hash stability validated; per-model explanation_hash variation expected and acceptable.
- *Theme 7 quality envelope:* Per-archetype per-model thresholds calibrated; defaults refined as production data accumulates.
- *Operational tuning:* Cost/latency calibration depends on archetype distribution in actual workloads.

**References.**

- `substrate_3_generation/SPEC.md` §7.7 (single-model-per-batch routing)
- D-071 (Theme 2; operational_context.llm_model_identifier)
- D-077 (Theme 3; shared interpretation context across batches)
- D-085 (Theme 5; LLM integration topology)
- D-087 (Theme 5; two-layer Guardrail enforcement)
- D-088 (Theme 5; explanation_hash; operational variation tolerance)
- D-090 (Theme 6; eval framework calibrates per-model behavior)

---


## D-092 — Quality envelope framework: calibrates behavioral distributions, not architectural invariants [Theme 7]

**Date:** 2026-05-19
**Substrates affected:** [S3, with downstream consequences for substrate-3 calibration discipline and Phase 1 closeout]
**Status:** active

**Decision.** Theme 7 commits substrate-3 to a quality envelope that is a structured calibration framework — defining which behavioral dimensions are calibrated per archetype, how v1 initial values are derived, and how thresholds evolve — explicitly bounded so that it calibrates behavioral distributions and never architectural invariants. The quality envelope is conceptually separated from the operational envelope.

**(a) The quality envelope calibrates behavioral distributions, not architectural invariants.**

Per round 2 TA pushback (item 1): not all dimensions are equally calibratable. Some are operational distributions and evolving quality heuristics; others are substrate law. The quality envelope must never blur them.

Architectural invariants (substrate law — NOT envelope surfaces, NOT calibratable):

- identity_hash semantic continuity (D-090 b) — same semantic_context + governance_context yields same emitted output. Not a "tight threshold"; an invariant.
- Guardrail Layer A validity (D-087) — always holds; not a tunable acceptance rate.
- Refusal transparency presence (D-073) — every refusal carries its typed payload; not a tunable rate.
- Grounding requirements (Guardrail 1, D-070) — admissibility requires grounding; not a calibration knob.
- The three Guardrails, eight-refusal-kind taxonomy, three-context separation, two-layer enforcement, substrate-as-admissibility-authority.

These are enumerated in `SUBSTRATE_3_WORLDVIEW.md` as the canonical invariant registry. The quality envelope observes invariant compliance (an invariant breach is a bug surfaced by eval) but never tunes invariants.

Calibratable surfaces (behavioral distributions — quality envelope dimensions):

- Refusal-rate distribution by semantic category (invalidity 5 kinds + policy 2 kinds; the operational category is the operational envelope per (b)).
- Layer 1 / Layer 2 admissibility distribution (D-083 e).
- explanation_hash drift threshold (contextual, D-090 b).

Commitment, verbatim: the quality envelope calibrates behavioral distributions, not architectural invariants. This protects later teams from "tuning" non-negotiable substrate commitments.

**(b) Quality envelope vs operational envelope.**

Per round 2 TA pushback (item 5): budget exhaustion is operational incompletion (D-088's operational refusal category), not semantic quality. Tracking it as archetype semantic behavior over-couples operational tuning to semantic calibration — tighter budgets would falsely read as worse semantic behavior.

Two conceptually separate envelopes:

- Quality envelope (semantic behavioral distributions): refusal-rate by semantic category (invalidity + policy); Layer 1 / Layer 2 admissibility distribution; explanation_hash drift threshold.
- Operational envelope (operational tuning surface): cost per generation by archetype; latency per generation by archetype; budget caps (token / time / tool_call_count); `operational-budget-exhausted` rate (D-088 operational refusal category).

This maps onto D-088's three-category refusal axis: invalidity and policy refusals are semantic (quality envelope); the operational refusal category is operational (operational envelope). The two are calibrated independently — operational tuning does not pollute semantic calibration. The drift-as-evolution framework and evolution-adjudication governance (D-093) apply to the quality envelope; the operational envelope is operational tuning, not subject to semantic evolution adjudication.

**(c) Envelopes are relative to the canonical routing profile.**

Per round 2 TA pushback (item 4): once model routing is behavior-shaping (D-091 a), the archetype alone no longer defines expected behavior — Opus-data-behavior and Sonnet-data-behavior have different refusal distributions, decomposition patterns, grounding aggressiveness.

Without exploding to archetype×model in v1: each per-archetype quality envelope is defined relative to the archetype's canonical routing profile — the expected behavioral distributions under the archetype's default model (D-091 v1 defaults). Non-canonical model usage (per-customer `operational_context.llm_model_identifier` override) produces distributions outside the canonical envelope's scope; the canonical envelope does not claim to characterize them. Per-archetype×model envelopes are deferred (forward-compat).

**(d) Structure-not-values: per-archetype dimensions and v1 provisional profiles.**

The quality envelope is structure — which dimensions are calibrated, how v1 values are derived, how thresholds evolve — not fixed numerical gates. V1 ships with no production data (replay corpus empty per D-090 d), so fixed thresholds would be false precision; and substrate behavior is expected to improve (D-090 c), so static thresholds would flag healthy evolution as breach.

Per-archetype quality envelope dimensions (per (a) calibratable surfaces), with v1 expected shapes derived from Theme 3 design intent:

- Data-behavior (D-078, refusal-dominant): higher policy-refusal rate; significant Layer 1 share (validation-rule Layer 2 awaits formula parser).
- Configuration (D-079, cleanest): lowest refusal rate; S1-direct admissibility.
- Permission (D-080): moderate refusal rate with run-as-disambiguation policy refusals.
- UI (D-081, minimal v1): higher refusal rate, honest not regression (Lightning composition is S1 Tier 3, absent).
- Integration (D-082, scoped v1): higher refusal rate (operational-only admissibility; interaction-topology deferred).

These are v1 expected shapes (provisional), not pass/fail gates. They tell the eval framework what distribution to expect per archetype so anomalies surface as signal.

**(e) Calibration mechanism and cadence.**

- v1 initial values (pre-launch): heuristic, derived from per-archetype design intent (D-078–D-082), curated-corpus runs, D-091 model defaults. Explicitly provisional.
- Pilot-phase refinement: pilot customer feedback (D-090 d) adjusts ranges — where most calibration happens.
- Production calibration: as the replay corpus accumulates (3–6 months per D-090 d), ranges refined against observed production distributions.
- Ongoing evolution: range shifts tracked per D-093 evolution-adjudication governance.

Cadence aligns with D-090 eval cadence: pre-commit checks against current ranges; pre-release validates full envelope; continuous production monitors distribution drift; post-pilot feedback refines weekly.

**Rationale.** Round 2 TA integration (items 1, 4, 5) sharpened the quality envelope from a single set of thresholds into a structured framework that protects architectural invariants from calibration pressure, separates semantic from operational calibration, and is honest about model-relativity. The structure is the architectural commitment; the values are instantiation that accumulates against ground truth.

**Alternatives considered.**

- *Fixed numerical thresholds at v1.* Rejected. No production data; false precision; would flag healthy evolution as breach.
- *Single undifferentiated envelope (semantic + operational together).* Rejected per TA item 5. Operational tuning pollutes semantic calibration.
- *Per-archetype×model envelopes at v1.* Rejected per TA item 4 (deferred). Combinatorial; per-model profiles not yet characterized. Canonical-routing-profile relativity is the v1 commitment.
- *Treating identity_hash / Layer A / grounding as tight envelope thresholds.* Rejected per TA item 1. These are architectural invariants, not calibration surfaces.

**Downstream consequences.**

- *D-093:* Threshold evolvability operates on quality envelope ranges, bounded by the invariants enumerated here; evolution adjudication governance.
- *D-094:* Admissibility-confidence threshold (governance_context) shapes the policy-refusal distribution the quality envelope observes.
- *SUBSTRATE_3_WORLDVIEW.md:* Canonical registry of the architectural invariants this framework protects.
- *Phase 1 closeout:* Quality envelope is the bridge converting pilot feedback into substrate calibration.

**References.**

- `substrate_3_generation/SPEC.md` §8.2 (calibration vs invariants), §8.3 (quality vs operational envelope), §8.4 (per-archetype dimensions and v1 profiles)
- `substrate_3_generation/SUBSTRATE_3_WORLDVIEW.md` (architectural invariant registry)
- D-070 (Theme 1; Guardrail 1; grounding)
- D-073 (Theme 2; refusal taxonomy; transparency)
- D-078 through D-082 (Theme 3; per-archetype design intent)
- D-083 (Theme 4; admissibility_layer; Layer 1/2)
- D-087 (Theme 5; Layer A/B enforcement)
- D-088 (Theme 5; three-category refusal axis; operational category)
- D-090 (Theme 6; eval framework; two-invariant replay; drift-as-evolution; ground truth)
- D-091 (Theme 6; model routing; canonical defaults)
- D-093 (Theme 7; threshold evolvability and evolution-adjudication governance)
- D-094 (Theme 7; admissibility-confidence as governance_context)

---


## D-093 — Threshold evolvability and evolution-adjudication governance [Theme 7]

**Date:** 2026-05-19
**Substrates affected:** [S3, with downstream consequences for substrate-3 governance discipline and Phase 1 closeout]
**Status:** active

**Decision.** Theme 7 commits that quality envelope thresholds are expected ranges subject to evolution, bounded by architectural invariants, with hardened safeguards against rationalizing semantic regression as evolution. Theme 7 also reconciles the validity-space framing with replay determinism via the reproducibility-versus-acceptability distinction.

**(a) Threshold evolvability bounded by architectural invariants.**

Per D-090 c (drift-as-evolution): the substrate's behavior is expected to improve (narrower admissibility, more specific grounding, cleaner refusals). Fixed thresholds would flag improvement as breach. So quality envelope thresholds are expected ranges, not fixed gates:

- A threshold breach triggers investigation, not auto-failure.
- The breach is adjudicated: `regression` (substrate got worse — gate fails, blocks deployment) | `evolution` (substrate got better — range shifts) | `neutral` (environmental change — range may shift).
- Range shifts are recorded in EVOLUTION.md with the drift annotation vocabulary.

Per round 2 TA pushback (item 2): this evolvability is bounded. The architectural invariants (D-092 a; enumerated in SUBSTRATE_3_WORLDVIEW.md) are the floor. Evolution adjudication operates only within the space of architecturally-compliant behavioral shifts. A drift that breaches an invariant is regression by definition — not subject to maintainer judgment. Maintainer judgment exists only above the invariant floor.

Commitment: evolution adjudication must preserve substrate-level architectural commitments even when behavioral distributions shift.

**(b) Evolution-adjudication safeguards.**

Per round 2 TA pushback (item 2): maintainer-judged regression-vs-evolution risks gradual semantic-center drift — normalizing drift, rationalizing regressions, overfitting to pilot feedback, preferring prettier explanations or lower refusal rates. Safeguards:

1. Recorded rationale, not just annotation. An `evolution` annotation requires explicit written justification in EVOLUTION.md tying the shift to a specific per-archetype evolution signature (per (c)). "Looks better" is insufficient.
2. Asymmetric scrutiny on lower-refusal shifts. Given the fail-loud-over-hallucinate philosophy, a drift toward lower refusal rate carries a presumption of regression and requires stronger evidence to ratify as evolution than a drift toward higher refusal. Lower refusal means the substrate asserts more — where hallucination risk lives.
3. Periodic architectural-invariant audit. Accumulated `evolution` shifts are audited periodically against the full invariant set (SUBSTRATE_3_WORLDVIEW.md) to confirm incremental shifts have not collectively breached an invariant that no single shift breached. Guards against gradual semantic drift.
4. Design-cycle-weight review. Ratifying an envelope-range shift as evolution is governance-weight, reviewed like an architectural change (consistent with D-089's treatment of major prompt changes), not a routine maintenance action.

A principled, mechanical theory of semantic improvement remains future (forward-compat). The bounding rule + asymmetric scrutiny + invariant audit prevent the worst rationalization paths without pretending the theory exists.

**(c) Per-archetype drift judgment signatures.**

Per D-090 c (Theme 6 deferred formalized judgment criteria to Theme 7). Partial formalization:

- Per-archetype evolution signatures. What healthy evolution looks like per archetype (e.g., data-behavior: shift toward more Layer 2 admissibility as the formula parser ships; UI: lower refusal rate as Lightning composition enters S1 Tier 3). These justify an `evolution` annotation.
- Per-archetype regression signatures. What regression looks like (e.g., configuration: refusal rate rising on cases that should be clean S1-direct admissibility — signals a substrate bug).
- Neutral signatures. Environmental changes (org metadata shifts, model updates) that move distributions without indicating substrate quality change.

Full formalization (automated judgment) remains future — v1 ships documented signatures + maintainer judgment, not an automated classifier.

**(d) Validity-space and replay determinism: reproducibility versus acceptability.**

Per round 2 TA pushback (item 6): the per-archetype distribution framing is a validity-space framing (validity as a space, not a point). But the more valid outputs exist, the weaker replay identity / explanation stability / canonical-interpretation expectations become. Validity-space and replay determinism are in natural tension.

The distinction that resolves it at v1:

- Semantic reproducibility (replay determinism, identity_hash) is a substrate-engineering property: same substrate version + same semantic_context + same governance_context yields same emitted output. The substrate deterministically selects one point.
- Semantic acceptability (validity-space) is a semantic property: which outputs would be correct for a requirement. Often a space.

V1 resolution: the substrate is deterministic (reproducible) within a wider acceptability space. The validity-space may contain multiple acceptable outputs; the substrate deterministically picks one and reproduces that pick on replay. Reproducibility does not require the picked point be the only acceptable point — only that the substrate picks the same point each time.

Forward-compat: when the substrate should legitimately vary its pick within the validity-space versus always reproduce requires the unresolved semantic adjudication theory (D-090 f). V1 commits to reproducibility, full stop, while acknowledging the acceptability space is wider. The reproducibility-versus-acceptability distinction is the architectural handle for that future work.

**Rationale.** Round 2 TA integration (items 2, 6) hardened the evolution-adjudication surface — the deepest governance risk in Theme 7 — by bounding evolvability with invariants and adding rationalization safeguards, and reconciled validity-space with replay determinism by distinguishing reproducibility (engineering property) from acceptability (semantic property). Together they let the quality envelope evolve without eroding Themes 1–5.

**Alternatives considered.**

- *Fixed thresholds (no evolvability).* Rejected per D-090 c. Anti-evolution gravity; flags improvement as breach.
- *Unbounded maintainer-judged evolvability.* Rejected per TA item 2. Risks gradual semantic-center erosion; invariants must bound judgment.
- *Symmetric scrutiny on refusal-rate shifts.* Rejected per TA item 2. Lower-refusal shifts carry hallucination risk; asymmetric scrutiny is warranted by the fail-loud philosophy.
- *Treating validity-space and replay determinism as fully coexistent.* Rejected per TA item 6. They are in tension; reproducibility-versus-acceptability is the v1 reconciliation.

**Downstream consequences.**

- *D-092:* Operates on the quality envelope ranges this entry makes evolvable.
- *SUBSTRATE_3_WORLDVIEW.md:* The invariant registry that bounds evolution adjudication.
- *Substrate-3 governance discipline:* Evolution adjudication is a design-cycle action with recorded rationale and periodic invariant audit.
- *Future semantic adjudication theory:* reproducibility-versus-acceptability is the handle.

**References.**

- `substrate_3_generation/SPEC.md` §8.5 (threshold evolvability and evolution-adjudication governance), §8.6 (validity-space and replay determinism)
- `substrate_3_generation/SUBSTRATE_3_WORLDVIEW.md` (architectural invariant registry)
- D-071 (Theme 2; identity_hash; three-context separation)
- D-075 (Theme 2; explanation_hash; drift events)
- D-088 (Theme 5; semantic substance)
- D-089 (Theme 6; major-change review weight)
- D-090 (Theme 6; drift-as-evolution; two-invariant replay; semantic adjudication unresolved)
- D-092 (Theme 7; quality envelope framework; calibration vs invariants)

---


## D-094 — Admissibility-confidence threshold as governance_context: semantic risk tolerance [Theme 7]

**Date:** 2026-05-19
**Substrates affected:** [S3, with consequences for D-071 three-context separation and Phase 1 closeout]
**Status:** active

**Decision.** Theme 7 categorizes the admissibility-confidence threshold (which governs the `policy_restraint` cause for `no-admissible-negative-scenario-found` per D-083 b) as governance_context — semantic risk tolerance — not operational_context or a quality-envelope-owned parameter. This resolves the Theme 4 forward-compat reservation as a governance resolution.

**(a) Recategorization to governance_context.**

Per round 2 TA pushback (item 3): the admissibility-confidence threshold directly determines what the substrate is willing to assert as truth. That is semantic risk tolerance, which is governance — not operational preference, routing, or prompt strategy.

The decisive argument: same candidate, same grounding, same topology, different threshold yields different refusal behavior. The parameter changes semantic admissibility policy, not generation quality. This is categorically different from D-089's prompts (which shape behavior diffusely within governance bounds) and D-091's model routing (which shapes cognition style). The admissibility-confidence threshold is a direct numerical determinant of the substrate's truth-assertion boundary.

Therefore the threshold is governance_context per D-071's three-axis separation, not operational_context. The Theme 7 opening's lean (operational, quality-envelope-owned) was incorrect; this entry corrects it.

**(b) Semantic risk tolerance framing.**

The threshold is semantic risk tolerance — a governance policy determining how confident the substrate must be before asserting a grounded negative, and by extension the boundary of `policy_restraint` (D-083 b):

- Higher threshold yields more conservative behavior (more `policy_restraint` refusals, fewer speculative negatives).
- Lower threshold yields more permissive behavior.
- Substrate-authored governance default: conservative, per the fail-loud-over-hallucinate philosophy.
- Per-customer governance override: a risk-averse enterprise customer may set a higher threshold (more refusals, fewer speculative asserts). This is a legitimate governance knob, not operational tuning.

**(c) Quality envelope observes, does not own.**

Per D-092 (b) measurement/behavior cleanliness: the quality envelope observes the resulting policy-refusal-rate distribution but does not own or tune the threshold. The envelope is a measurement surface; the threshold is a governance input. This keeps the measurement/behavior line clean — the exact pollution D-092 (b) and TA item 5 also warn against.

**(d) Replay implication.**

Because the threshold is governance_context: per D-088, changing it changes governance_context and is expected to change identity_hash (different refusal behavior is a different emitted output). This is correct — semantic risk tolerance is a semantic-identity-bearing input, confirming it belongs in governance, not operational. An operational parameter changing identity_hash would have been a contradiction (operational variation must preserve identity_hash per D-088); a governance parameter changing it is exactly right. The recategorization strengthens the three-context separation rather than altering it.

**Rationale.** Round 2 TA integration (item 3): the admissibility-confidence threshold determines what the substrate asserts as truth — semantic risk tolerance, which is governance. The replay test confirms it: a parameter that legitimately changes identity_hash must be governance_context (or semantic_context), never operational_context. This is the cleanest categorization argument in the substrate and corrects the opening lean. It resolves the Theme 4 reservation (admissibility-confidence calibration for `policy_restraint`) as governance.

**Alternatives considered.**

- *Operational_context, quality-envelope-owned (opening lean).* Rejected per TA item 3. The threshold determines truth-assertion policy, not generation quality; an operational parameter changing identity_hash contradicts D-088.
- *Quality-envelope-owned calibration parameter.* Rejected. Conflates measurement with behavior; the envelope observes, governance owns.
- *Semantic_context.* Rejected. The threshold is a policy applied across requests, not a per-request semantic input; governance_context is the correct axis.

**Downstream consequences.**

- *D-071 three-context separation:* strengthened — admissibility-confidence threshold added to governance_context as semantic risk tolerance.
- *D-083 (b):* `policy_restraint` cause boundary determined by this governance parameter.
- *D-092:* Quality envelope observes the policy-refusal distribution shaped by this threshold.
- *SUBSTRATE_3_WORLDVIEW.md:* governance_context semantic boundary documents semantic risk tolerance.
- *Per-customer governance:* semantic risk tolerance is a customer-facing governance knob.

**References.**

- `substrate_3_generation/SPEC.md` §8.7 (admissibility-confidence threshold as governance_context)
- `substrate_3_generation/SUBSTRATE_3_WORLDVIEW.md` (semantic boundaries; governance_context)
- D-071 (Theme 2; three-context separation; governance_context)
- D-083 (Theme 4; policy_restraint cause; admissibility_layer)
- D-088 (Theme 5; operational variation preserves identity_hash; governance variation may change it)
- D-089 (Theme 6; prompts as behavior-shaping but operational — contrast)
- D-091 (Theme 6; model routing as behavior-shaping but operational — contrast)
- D-092 (Theme 7; quality envelope observes, does not own)

---

## D-095 — S3 spine + tool surface — enforcement boundary, runtime control, conversation granularity

**Date:** 2026-05-21
**Substrates affected:** [S3]
**Status:** Locked (TA-converged)

**Context:** Phase 2 implementation of the S3 spine + tool surface, realizing Theme 5 (D-085–D-088). Grounding confirmed the LLM gateway is single-shot; the multi-turn orchestration loop is greenfield. Four implementation-time architectural forks, resolved via the TA review loop.

**1. Layer-A enforcement boundary.** All of Layer A is spine-orchestrated and operational. The grounding-free checks (schema well-formedness, vocabulary-at-enum-positions, Guardrail-3 excerpt presence) execute in the spine. The one grounding-dependent Layer-A check — S1 entity refs exist at the pinned s1_version_seq (D-087) — is delegated through a NARROW operational seam method (check_refs_exist), distinct from the semantic resolve_intent. REJECTED: deferring S1-ref-existence behind the semantic seam. Layer-A operational identity is preserved structurally — a ref-existence failure is an operational rejection (rejected_for_correction -> structural-validation-failure, D-088a), never a semantic dismissal; the seam method's return type encodes the distinction. The semantic seam (resolve_intent) is reached only after all of Layer A passes. Keeps the operational/semantic separation (D-087) clean and Layer A enforced as a boundary precondition.

**2. Gateway binding (tool_turn).** The S3 runtime owns the multi-turn loop; the gateway gains a transport-thin primitive (tool_turn) performing exactly one turn over caller-supplied messages/tools/tool_choice. The single-chokepoint discipline is satisfied by SHARED GOVERNANCE INTERNALS, not a single API function: tool_turn shares llm_call's rate-limit, routing, PII-redaction, and cost-logging (llm_usage_log) internals. No orchestration in tool_turn — the spine owns message history, tool_result construction, and turn sequencing. REJECTED: routing the loop through llm_call's prompt-module abstraction; calling provider.invoke raw.

**3. Runtime control (force-per-phase).** The substrate forces the expected tool per phase via tool_choice (propose -> select -> emit), per D-085 (substrate in control; LLM as bounded cognition provider). Refusals remain substrate-routed: the LLM always proposes; the substrate authors admit-or-refuse. The phase choreography is ORCHESTRATION POLICY, not substrate ontology — an evolvable policy of the orchestration engine, not part of the locked tool contract or substrate invariants (consistent with D-086's substrate-internal lifecycle being free to evolve).

**4. Conversation granularity.** One LLM conversation per requirement (1:1 with GenerationOutcome; clean budget attribution and replay isolation). The batch's shared interpretation context (D-077) is computed once and injected as a BOUNDED context into each per-requirement conversation. No hidden shared conversational state across requirements — the shared context is explicit and bounded, not an implicit shared conversation.

**Relationship to Theme 5:** refines the realization of D-085–D-088; does not alter the locked tool contract (D-086) or the two-layer enforcement taxonomy (D-087). The Layer-A/Layer-B taxonomy is unchanged; resolution 1 specifies only the enforcement's code placement and operational identity.

---

## D-096 — Governance-core — admissibility model, Layer-1 semantics, Layer-B discipline, slicing

**Date:** 2026-05-21
**Substrates affected:** [S3]
**Status:** Locked (TA-converged)

**Context:** Phase 2 governance-core — filling the GovernanceProvider seam (D-095) with real reasoning, realizing the D-085 engines (admissibility, governance / Layer B, decomposition, refusal router) over the S1 SemanticOrgModel boundary (db92aaf). S1 grounding offers get_entities (exact-match) + get_related (single-hop) + current_version_seq only; Layer 2 admissibility (formula semantics) needs the deferred S1 §17 parser. Resolved via the TA review loop.

**1. Admissibility model.** Candidate derivation is requirement-anchored — origination is the requirement excerpt; S1 verifies, never originates (Guardrail 3 / D-083a). Scoped neighborhoods are built from single-hop get_related walks + exact-match get_entities; traverse is NOT built yet — single-hop now, traversal later, because multi-hop without semantic scoping degrades into graph-wandering, and admissibility patterns + neighborhood discipline must mature first. Admissibility is determined per claim_kind (D-078); dismissals are phase-tagged (D-076/D-077). Admissibility is substrate-authored; the LLM never authors it (D-085).

**2. Layer-1 semantics — transitional, plausibility not verification.** At v1 only Layer 1 is available (Layer 2 = formula-semantics confirmation, parser-deferred per D-083/§17). Layer 1 proves a constraint EXISTS and is ACTIVE on the subject — NOT that its formula enforces the specific claimed rejection. Its semantic promise is therefore "constraint-grounded rejection plausibility," NOT semantic negative verification, and it must be framed and sold as such (no internal overselling). The Layer-1 marker is structurally unavoidable in the emitted artifact — artifact-top-level and operationally visible (D-083), never buried metadata; a caveat that is "legally true but operationally invisible" is unacceptable. Layer 1 is explicitly TRANSITIONAL: Layer 2 (the S1 §17 formula parser) is the intended semantic end-state and is preserved as first-class, so the architecture does not ossify around constraint-existence-as-grounding-truth.

**3. Layer-B discipline — sanity filter, not verifier.** Layer B is a semantic SANITY FILTER, not a semantic verifier. It may ONLY reject (structurally weak excerpt support, obvious contradiction, missing anchoring); it may NOT author semantics, reinterpret intent, or upgrade grounding. The proposing model never validates its own proposal (no self-authored semantic legitimacy); no "mini semantic judges" inside Layer B — that path leads to duplicated reasoning, governance ambiguity, and validator/proposer disagreement spirals. v1 is a structural floor with explicit semantic incompleteness; full semantic-support verification is a known deferred capability, gated on the same semantic end-state as Layer 2.

**4. explanation_hash.** Computed now — a refusal outcome is incomplete without it — mechanically over the typed attempted_interpretation per D-075, adapted to the Slice-0/D-087 shape (path_id normalized away by canonical ordering; scoped_neighborhood carried via extra). The full replay/regeneration controller (drift events, lineage comparison, transparency_policy_version migration) is deferred.

**5. Slicing — refusal vertical first.** The cut is at "admissibly-grounded -> emit." The refusal vertical implements the full admissibility engine (it is how no-grounding is determined), Layer-B-for-refusal, the refusal router, explanation_hash, and persistence — end-to-end with real S1 grounding. resolve_intent is built whole (grounded-or-not); finalize_outcome (emission) is stubbed. The emission half (S2 write_claim/write_recipe + identity_hash + draft outcome) and the config-vs-data_behavior claim-body decision are deferred to the draft vertical.

**6. Persistence.** Per-requirement transaction boundary — partial-failure isolation, important for replay and budget-exhaustion partial state; a runtime-invoked persistence module; FK write order generation_requests -> generation_outcomes -> llm_calls.

**Relationship to Theme 5:** realizes the D-085 engines and the two-layer enforcement (D-087) under the S1/S2 boundaries actually shipped. Does not alter the locked vocabularies (D-076/D-088) or the tool contract (D-086).

---

## D-097 — Draft vertical (governance emission) close-out

**Date:** 2026-05-21
**Substrates affected:** [S3]
**Status:** Locked (TA-converged)

The emission half of S3 governance: an admissible grounded candidate becomes an emitted S2 claim + recipe (a draft `GenerationOutcome`). Builds on D-096 and the draft-vertical grounding (S2 write path; Layer-1-completeness per D-078/D-079). TA-converged.

**.1 Debut = `data_behavior` value-claim positive (Option B).** The first emitted artifact sets the substrate's trust posture, so the debut must be verified, not caveated. B is Layer-1-complete (type + permission verification is the verification), needs no S2 claim-body cycle (the value-claim body ships), exercises the full emission machinery (admissibility, grounding, permission reasoning, identity, recipe, ledger, draft semantics), is the highest-volume archetype (D-078), and is operationally meaningful to QA users (e.g. "Profile X can edit Field Y; accepted values A/B/C" — actionable, org-specific). Configuration (Option C) is the immediate fast-follow — cleaner technically but less convincing as a debut (reads as metadata inspection, not test intelligence); its S2 claim-body cycle (3 bodies) runs in parallel. `data_behavior` negative (Option A) is never the debut — debuting caveated plausibility would anchor PrimeQA as intelligent approximation rather than grounded semantic infrastructure, and that is hard to reverse.

**.2 Standard-picklist grounding gap = explicit degrade-or-refuse, never silent.** B's verified scope is type + permission + custom-picklist values; standard-picklist values are gapped (S1 §22, deferred). The substrate must not silently emit a value-claim touching standard-picklist values as if fully grounded — a hidden grounding asymmetry corrodes trust. On the unsupported path it must refuse (ungrounded) or degrade with an explicit unsupported-dimension marker. Fail loud; no quiet fallback. (v1 lean: refuse on the gapped dimension, so every emitted value-claim is fully verified — implementation call within this envelope.)

**.3 Layer marker ≠ caveat; caveat from a centralized semantic-completeness registry.** `admissibility_layer` records how deep grounding went (the marker). The caveat records whether deeper unimplemented semantics exist for the claim_kind (a Layer 2 defined but unbuilt). Distinct axes: a Layer-1-complete claim_kind (config existence/property, value-claim positive — no deeper layer) carries the marker and no caveat; a Layer-1-plausible claim_kind (`data_behavior` negative — Layer 2 defined, parser-deferred) carries the marker and the mandatory caveat. The "does this claim_kind have a Layer 2?" determination is claim_kind-derived (per D-078), not a new `AdmissibilityLayer` enum value, and lives in one semantic-completeness registry / authority surface — never scattered across emission code.

**.4 A draft is one semantic transaction.** Claim + recipe + draft outcome are atomic — one Session bound to the tenant-scoped connection (the S2 Coordinator writes claim/recipe in that Session; the `generation_*` ledger rows write in the same Session; one commit). The draft is itself the governed artifact, so loosely-coupled writes are wrong. The refusal-vertical persister's raw-connection path is reconciled to this Session-based path now, before the bifurcation hardens.

**.5 Substrate authors semantic assertion; LLM owns linguistic realization (Guardrail 2).** The substrate authors the claim body from the grounded candidate's S1 entities — it owns what is asserted true. The LLM (via `emit_outcome`) phrases / structures / narrates / selects — it owns linguistic realization — and never decides what is true or authors entities. LLM-authored claims would make grounding advisory, weaken ontology boundaries, collapse replay stability, and blur semantic provenance. The substrate remains the author of semantic truth.

**.6 Negative semantic verification is a first-class future milestone (architectural-gravity guard).** Debuting positives, and the cleanliness of verifiable positives (clean replay/eval, no parser dependence, trustworthy appearance), create long-term pressure to privilege positives and perpetually defer Layer 2 — making Layer 1 a comfortable local maximum and starving PrimeQA's real differentiation (semantic negative reasoning, edge-condition generation, admissibility-aware rejection). Layer 2 / negative semantic verification is recorded as a core committed milestone, not a deferred edge feature. Sequencing positives-first is correct; permanent privileging is not.

**Slicing:** implement B end-to-end (the registry, positive admissibility, the standard-picklist handling, `accept_selection`/`finalize_outcome` → `write_claim`/`write_recipe` + `compute_identity_hash`, the conditional marker, the unified transaction with persister reconciliation), tested on local PG. C (configuration) is the immediate fast-follow.

---

## D-098 — Draft-vertical debut: flip B → C (supersedes D-097.1, on a grounding finding)

**Date:** 2026-05-21
**Substrates affected:** [S3]
**Status:** Locked (TA-converged)

The draft-vertical grounding (read-only) contradicted a load-bearing premise of D-097.1's B debut. Recorded as a superseding entry so the reversal and its cause stay on the record. D-097.2 goes moot for the config debut; D-097.3/.4/.5/.6 stand unchanged. TA-converged.

**Finding (why D-097.1 reopened).** (1) A `data_behavior` value-claim's accepted-values dimension is ungroundable today: inline custom picklists are not edge-modeled, GVS-backed fields are absent in the sandbox with their values on an unreachable detail table, standard picklists are unlinked. (2) Field type lives on a detail table, and SPEC §12's query interface (five primitives + diffs) exposes no detail-table data and designs no detail join — detail exposure is not a deferred-but-designed item but unperformed S1 architecture work. So B's verified debut collapses to permission-only (shipped boundary) or permission+type (requiring net-new S1 design); the "verified value-claim with accepted values" artifact overshoots current grounding. B's semantic center was always the value semantics, not the permission assertion; once values/type are ungrounded, B is weak permission metadata or ontology distortion.

**.1 (supersedes D-097.1) Debut = configuration metadata-relationship-claim.** C is genuinely complete over the shipped substrate boundary — the asserted relationship is a Tier-1 edge verified via `get_related`, no S1 change, no detail reads. Preferred over bare existence-claim: a metadata-relationship-claim is a requirement-grounding assertion ("Requirement assumes Validation Rule R on Account — verified: the org contains R") — semantic grounding, org-aware verification, requirement interpretation, not passive metadata reporting. S2 cost is one config claim body (`MetadataRelationshipClaimBody`) + admissibility; bounded and needed eventually regardless.

**.2 Trust principle clarified — semantic completeness, not perceived richness.** The debut artifact is chosen for semantic completeness over the current substrate boundary, not for maximal semantic richness. A smaller, fully-grounded claim is architecturally stronger than richer-but-partially-fictional semantics. Debuting B as a "verified value-claim" it cannot fulfill would be the oversell D-097.1 exists to prevent; C honors the principle that chose B.

**.3 Reject permission-only B as ontology distortion.** "Profile X can read/edit Field Y" is a permissions-archetype assertion; forcing it into a `data_behavior` value-claim body to dodge the S2 cycle is architectural cheating — permission is not value behavior. Not an option.

**.4 S1 detail-read = deliberate future substrate work, not debut patching.** Detail-table exposure is unperformed S1 query-interface design. It is authored deliberately as its own S1 increment when its consumers arrive (config property-claims, value-claim type grounding) — never as a quick debut-driven join. S1 query-interface evolution stays deliberate substrate design.

**.5 Configuration existence is NOT the long-term semantic center of S3.** C is the correct debut because it is the strongest fully-grounded artifact over the current substrate boundary — not because configuration existence is the long-term semantic center of S3. Config claims replay / ground / explain / validate cleanly — an attractive local maximum — and the substrate must not psychologically recenter on metadata existence. PrimeQA's differentiation depends on behavioral semantic verification, explicitly preserved as the long-term center of gravity (reinforcing D-097.6). The grounding gap validates D-097.6: the substrate is discovering where semantic verification genuinely becomes hard, which is evidence the decomposition is correct.

**Net effect on D-097:** .1 flips B → C; .2 (standard-picklist handling) is moot for the config debut; D-097.3 (semantic-completeness registry), .4 (unified transaction), .5 (substrate-authored / LLM-transcribed), .6 (negatives first-class) stand unchanged.

**Slicing:** ship `MetadataRelationshipClaimBody` (config) + config metadata-relationship admissibility (edge-existence via `get_related`, Layer-1-complete) + emission (the unchanged D-097.3–.5 machinery), tested on local PG.

---

## D-099 — Trigger taxonomy reopened: execution-initiation modes (supersedes D-055's five-kind lock); emission transaction realized

**Date:** 2026-05-22
**Substrates affected:** [S2, S3]
**Status:** Locked (TA-converged)

Building the C debut's emission revealed that the trigger taxonomy (D-055, locked at five) cannot represent a verification recipe. TA-converged. Reopens D-055 cleanly; the five existing kinds are unchanged and remain the behavioral core.

**Finding.** A config metadata-relationship verification recipe ("read S1, assert edge R exists") is a static invariant assertion with no Act phase — it has execution, initiation, replayability, and operational semantics, but no causal event. Yet `write_recipe` hard-requires a `causal_initiation` whose `kind` is one of the five D-055 kinds, every one a causal event (inbound / DML / UI / time / metadata-deploy). The observation side already fits (`metadata-recipe`/`metadata_read`); only the trigger layer has the gap. A genuine ontological category miss surfaced under real execution pressure — not convenience or leakage.

**.1 The taxonomy classifies execution-initiation modes, not exclusively causal events.** The original five-kind taxonomy implicitly assumed all executions are reactions to events — an overfit to behavioral testing. Verification recipes reveal the missing half: some executions are inspections of extant state. Recast: the trigger taxonomy classifies execution-initiation modes, not exclusively causal events. Recipes are fundamentally either event-reactive (the five causal kinds) or invariant-inspective (inspection of current state).

**.2 Add a sixth trigger kind: `inspection-trigger`.** Defined as execution initiated by explicit inspection of extant state, not by a causal event — an operator, release gate, verifier, or scheduled inspection pass chooses to inspect current state. A distinct initiation mode, not the absence of a trigger. Minimal body (`kind` + `body_schema_version`). Purely additive: `identity_hash` excludes operational layers, so the new kind cannot perturb any existing claim identity or dedup; the five event kinds are unchanged.

**.3 Inspection means execution-time reinspection, NOT frozen snapshot.** An `inspection-trigger` recipe asserts the org's current state at execution time — S4 actively re-inspects and re-verifies the edge when the recipe runs. It does not mean "assert whatever S3 once observed at grounding." This is the release-gate / drift-detection value: the recipe is the executable re-verification contract, not frozen grounding history. (Why B2 — emit no recipe — was rejected: it would collapse verification into a one-time snapshot and force S4 to re-enter S3 grounding, blurring the S3/S4 boundary.)

**.4 Event triggers remain the behavioral core; inspection-trigger is not a substitute (gravity guard, reinforcing D-097.6 / D-098.5).** `inspection-trigger` is cleaner, replayable, deterministic, and operationally cheap — an attractive local maximum. It exists to represent invariant verification, not to replace behavioral execution semantics. Event triggers must not become an "advanced mode"; behavioral verification, runtime causality, and effect observation remain PrimeQA's center of gravity.

**.5 (Noted, NOT modeled now) inspection-trigger may later bifurcate** — likely invariant inspection (static state assertion) vs observational inspection (read a current runtime artifact without a causal trigger). Single `inspection-trigger` is correct now; do not reopen further. Recorded only so the pressure is anticipated.

**Emission transaction (realizes D-097.4 — recorded for clarity, not a new decision).** `finalize_outcome` authors the claim + recipe bodies during the conversation (substrate owns semantic truth, D-097.5) — no DB writes inside an LLM turn. A post-conversation Session-based persister, bound to the tenant connection, runs `write_claim → write_recipe → ledger` in one Session, one commit; `OutcomeVerdict` carries authored bodies (refs exist only post-write); the refusal-vertical raw-connection persister reconciles onto this path.

---

## D-100 — Phase 2 (S3 generation) close-out scope

**Date:** 2026-05-22
**Substrates affected:** [S3]
**Status:** Locked (TA-converged)

Phase 2 is complete when the generation engine is structurally whole and demonstrably produces the full outcome spectrum — verified draft, caveated draft, refusal — end-to-end across representative claim shapes, with production scaffolding to trust it (managed prompts D-089, a golden-case eval suite D-090, model routing D-091), tested and documented, and merged to main. This is the pilot-ready bar for the engine.

Explicitly Phase 3+ (carve-out, so the behavioral-verification frontier stays a named commitment per D-098.5/D-099.4, not abandoned at the config local maximum): (1) the formula parser (Layer 2) → verified negatives — the Phase 3 differentiation headline; (2) the expect-rejection recipe observation mode — a second Phase-3 structural prerequisite (the recipe model has no expect-rejection/expect-error step; a behavioral negative is double-gated: parser and this recipe-model addition — Phase 3 is "parser + observational semantics," not just the parser); (3) the S1 detail-read increment + value-claim positives; (4) remaining archetypes (permissions, ui, integration); (5) full replay/regeneration controller, Theme 7 calibration, automation-effect/Apex.

---

## D-101 — Caveated negative (first Layer-1-plausible emission); caveat persistence

**Date:** 2026-05-22
**Substrates affected:** [S3]
**Status:** Locked (TA-converged)

TA-converged. Debut: `data_behavior` prohibition-claim, negative polarity — the only inherently-negative claim_kind; its semantic is rejection (1:1 with the plausibility caveat); admit dimension `validation_rule`/`APPLIES_TO` already grounded. Adds the third outcome type (caveated draft) and fires the caveat path for the first time.

**.1 Admissibility — reuse.** `_evaluate_negative` already admits Layer-1-plausible (VR `APPLIES_TO` subject → `LAYER_1`; absent → `no_constraint_supports_negative`). No new admit branch; the gap is `finalize_outcome`'s non-config stub. The negative grounded path stashes its S1 grounding into `state`, mirroring config's `_resolve_configuration`.

**.2 Emission.** `finalize_outcome`'s first non-config branch authors `ProhibitionClaimBody` from the stashed S1 grounding (substrate authors, LLM transcribes, D-097.5): `target` = subject `PinnedRef`; `operation` bound from the intent hint against the closed enum; `prohibition_mechanism = validation_rule`; `expected_rejection = RejectionSignal(error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION")` — the generic code any VR rejection surfaces, derivable from the mechanism without the formula (honest floor; anything more specific without the parser is fabricated specificity); `semantic_conditions = []` (the triggering condition is in the unparsed formula; the caveat covers it). Recipe = inspection (`inspection-trigger` + `metadata_read` asserting the VR `APPLIES_TO` edge), reusing config's shape; the behavioral test is parser-gated (Phase 3, D-100). Marker `LAYER_1`.

**.3 Caveat persistence (the architectural decision).** The caveat is persisted as emission-time epistemic posture, not derived-on-read. Rationale: a provenance ledger row must be self-describing — `admissibility_layer=layer_1` alone is semantically insufficient (it conflates layer-1-complete [no caveat] and layer-1-plausible [caveat], and resolving that needs claim_kind + the app-code registry, which a stored row must not depend on); and re-derived caveat semantics are not historically trustworthy (a future Layer-2 parser rollout must not silently rewrite the posture of older emitted artifacts). So store `caveat_required` + a typed `caveat_kind` on `GenerationOutcome` + a ledger column, written at emission from the registry verdict. The semantic-completeness registry (D-097.3) remains the sole authority of the caveat decision; the stored field is the emitted verdict snapshot, not duplicated logic. Store typed posture only — never rendered caveat prose (human wording is presentation-layer policy that will evolve; storing it creates stale semantics / replay mismatch). `caveat_kind` is an enum, not a boolean, because the substrate models epistemic qualification classes, not warning presence — one value now (the deeper-verification-layer-unparsed class), future causes (partial grounding, runtime approximation, …) as distinct kinds. Caveat persistence records the emission-time epistemic posture of the artifact, not a presentation-layer warning.

**.4 Persistence/dedup — reuse the unified persister verbatim** (claim+recipe+ledger atomic) plus the caveat column; identity/dedup unchanged.

---

## D-102 — Generation eval suite: v1 realization (deterministic core first)

**Date:** 2026-05-22
**Substrates affected:** [S3]
**Status:** Confirmed (no TA — realizes D-090)

Realizes D-090 as a Phase-2-scoped subset; D-090 stays the full target. Confirmed (no TA — realizes D-090; the strategy follows from the governed outcome being deterministic given a fixed intent).

**.1 Two-layer determinism strategy.** The governed outcome is deterministic given a fixed intent + fixture (grounding, admissibility, refusal routing, emission authoring, caveat verdict, and both hashes are substrate-authored and mechanical); the LLM's only outcome-affecting freedom is which intent it proposes and which candidate it selects. So: a deterministic core (scripted/recorded tool-turns → reproducible governed outcome + stable `identity_hash`/`explanation_hash`) is the CI gate and the two-invariant replay net (D-090(d)), asserting exact governed properties — `outcome_kind`, `admissibility_layer`, caveat, `refusal_kind`+cause, claim/recipe shape — never LLM phrasing (D-090(f)); a live-LLM layer (binds the real gateway) tests the LLM's interpretation, asserts with tolerance (governed-outcome family, never phrasing), runs periodically (not PR-gating) at D-090's pre-release/continuous cadence, and its variance feeds D-090(c)'s drift framework (regression|evolution|neutral), not suppressed as noise.

**.2 Sequencing.** The deterministic core lands first (no D-089 dependency — it stubs the LLM) as the regression net protecting the rest of close-out (D-089 prompts, D-091 routing). The live layer lands with/after D-089's prompt registry, since its purpose is to validate real prompt versions (D-089 makes prompts eval-gated by D-090). Scripted intents — representative of real requirements — seed the deterministic corpus; recorded-replay accumulates over time (D-090(d): replay empty at v1).

**.3 v1 scope cut.** A subset of D-090's full vision: a curated, spectrum-representative golden corpus (not the 200–500-case production corpus) + correctness (D-090(a)) + the deterministic replay/regression net + drift hooks. Deferred to Theme 7 / post-pilot: the full corpus, continuous-production drift sampling, and performance evals (cost/latency from `llm_calls`).

---

## D-103 — D-089 prompt management: realization decisions

**Date:** 2026-05-22
**Substrates affected:** [S3]
**Status:** Confirmed (no TA — realizes D-089)

Realizes D-089's specified design. Confirmed (no TA — realizes D-089; the freezing invariant and pin-for-eval are correctness/isolation refinements, not contested forks).

**.1 Immutability via per-version freezing + content-hash.** Each shipped version's composed content is frozen per-version and recoverable independent of later base/fragment edits — replay determinism requires reconstructing vN's exact prompt forever. The working `base` + fragments author the next version only. A content-hash drift-guard records each frozen version's SHA; a unit test asserts the live frozen content still hashes to the recorded value (catches edits to a frozen version, which would corrupt replay). Convention + the hash guard make immutability mechanical, not aspirational.

**.2 All-fragments composition.** A version composes base + all archetype fragments (v1: data-behavior, configuration, permission). Necessary, not merely simpler: the LLM chooses the archetype at propose-time and needs every archetype's guidance before proposing; per-requirement fragment selection is impossible pre-proposal. One composed artifact per version.

**.3 Prompt is a quality component, not correctness-critical.** The substrate guarantees governed-outcome correctness regardless of the prompt (bounded cognition: `tool_choice` forces the phase tool, schemas lock vocabulary, the substrate authors admissibility — the LLM cannot emit a wrong tool, an out-of-vocab value, or assert admissibility). The prompt shapes only the semantic quality of the proposal within the rails. Therefore the deterministic eval core (prompt-bypassing) gates correctness; the live eval layer gates prompt quality; and shipping v1 ungated is safe — an ungated prompt can degrade quality but cannot break correctness. v1 ships marked "pre-live-gate baseline."

**.4 Live eval pins the model; production routing is separate.** The live layer pins a model (`model_override`) to isolate the prompt variable (same prompt × different models → different behavior), records `(prompt_version, model)` per drift annotation, may run against multiple pinned models. Substrate-3 production routing (D-091) is a separate increment; D-089's slices do not depend on it.

**.5 Slicing.** Slice 1 = registry (per-version frozen + content-hash guard) + runtime refactor (retire `_SYSTEM`) + v1 prompt; schema-free; deterministic CI unaffected. Slice 2 = the live eval layer (the real gate per .3) + `requirement_text` on live-eligible corpus cases + the now-justified `llm_calls.prompt_version` column. Provenance until slice 2: the request `operational_context.prompt_template_version` (FK-traceable, per D-071).

---

## D-104 — Live eval prompt gate: ontology-coherence semantics (TA-converged)

**Date:** 2026-05-22
**Substrates affected:** [S3]
**Status:** Locked (TA-converged)

Reconciles D-089 ("eval-gated before merge") with D-090(c) ("drift investigated, never auto-failed").

**.1 Principle.** The gate enforces ontology coherence, not output equality — it checks whether the prompt keeps the LLM's interpretation inside the substrate's semantic worldview, not whether output matches a snapshot. Per divergence: could this plausibly be a semantically coherent reinterpretation? Yes → human-judged drift; no (structurally implausible / ontology collapse) → auto-fail. Rejects outcome_kind-only auto-fail (too weak — lets config→data_behavior collapse pass) and global archetype/claim_kind auto-fail (too rigid — auto-fails legitimate reinterpretation, the anti-evolution gravity D-090(c) exists to prevent).

**.2 Encoding: per-probe semantic envelopes.** Each live-eligible probe declares three levels — invariant (must-not-drift; violation auto-fails), acceptable variants (coherent alternate resolutions; human-judged drift), benign variance (ignored). Global field-tier classification is too coarse; ambiguity surfaces differ per probe. Invariants are authored as coherence boundaries (broader than the expected output), never as output snapshots — snapshotting is the rejected output-equality and the overfitting failure mode of .5. Example — "VR R exists on Opportunity": invariant {configuration archetype; verified/non-caveated; not behavioral-negative; no refusal absent ambiguity}; acceptable {existence vs metadata-relationship claim; decomposition}; benign {explanation, phrasing, excerpt}.

**.3 Auto-fail = invariant violation** (structurally implausible reinterpretation / ontology collapse): config→behavioral-negative; permission→metadata-relationship; refusal where strong grounding exists and no ambiguity; caveated negative where verified config expected; archetype shift contradicting requirement topology.

**.4 Human-judged drift = acceptable-variant divergence** (coherent alternate resolution): config existence vs metadata-relationship; value-claim vs configuration-property; refusal_kind refinement; claim decomposition; stricter admissibility; more conservative refusal. Flagged `regression|evolution|neutral`, never auto-failed. Merge gate = (no invariant violations) AND (human reviews + accepts the drift report).

**.5 Review adjudicates coherence, not preference.** The reviewer asks "does this remain semantically coherent within substrate law?", not "which output do I prefer?" — maintainers are semantic governors, not prompt stylists. Forward-caution (architecturally important, not v1-urgent): per-probe envelopes risk the corpus becoming hidden prompt-training fixtures; mitigate later with rotating / hidden / adversarial probes so the gate stays semantic-regression detection, not overfitting infrastructure.

**.6 Confirmed (Claude, no TA).** Pinned model: default-only (Sonnet) gate + optional periodic Opus sweep. Full gateway (production-path fidelity; periodic-eval env has the v2-platform infra). Include the underspecified probe (invariant = must-refuse; loose). requirement_text probes reviewed; naturalistic phrasing broadens coverage as the corpus grows.

---

## D-105 — Refuse-not-crash for grounded-but-unbuilt claim_kinds (engine robustness; runner prerequisite)

**Date:** 2026-05-22
**Substrates affected:** [S3]
**Status:** Confirmed (no TA)

Surfaced by the production-runner grounding. Confirmed (no TA — realizes fail-loud / no-silent-fallbacks; closes a production crash).

**Problem.** `resolve_intent` `PROCEED_TO_EMIT`s for any grounded claim; `finalize_outcome` authors only config (metadata-relationship) and prohibition (negative), raising `NotImplementedError` for the rest (value / state-transition / automation-effect — D-097.6 deferred). Invisible in eval/verticals (the value-claim probe sits on a bare org → no-grounding refusal). In a real org with the Field present, a grounded value-claim → `PROCEED_TO_EMIT` → `NotImplementedError` → batch abort. A crash, not a graceful fail.

**Decision.**

**.1** A single source of truth for emittable claim_kinds (config metadata-relationship, prohibition negative today; grows as kinds are built).

**.2** Admissibility gates `PROCEED_TO_EMIT` to emittable kinds; a grounded-but-unbuilt kind yields an honest emission-deferred capability refusal (groundable, but emission for this kind isn't built yet — a boundary that lifts as kinds land; the runtime face of D-097.6's deferral). The expected path.

**.3** A drift-guard test binds the resolution-`PROCEED` surface to the emittable source of truth — a future kind added to resolution without emission support fails at build time.

**.4** `finalize_outcome` converts its `NotImplementedError` to a graceful, visible refusal (fail-loud, not batch-destructive) — a should-never-reach backstop given .2, ensuring a gating gap degrades one requirement, not the batch.

**.5** Prerequisite for the production runner; the deterministic eval corpus may later gain a grounded-value-claim → emission-deferred probe to cover the path.

---

## D-106 — Production generation runner + D-091 routing realization

**Date:** 2026-05-24
**Substrates affected:** [S3]
**Status:** Confirmed (no TA)

Realizes the runner grounding + D-091. Confirmed (no TA — realizes settled design; forks resolved in the runner HOLD).

**.1 Runner.** `run_generation(request, *, tenant_id, api_key, tenant_policy=None, tool_turn_fn=None) -> BatchResult`: routes the model once per batch (`route_model`), binds the routed `gateway.tool_turn` closure (default; `tool_turn_fn` override = test seam), opens a tenant connection (`GovernanceCore` over `SemanticOrgModel`), runs `GenerationRuntime().run` with `LedgerPersister`. In-process orchestration generalizing `live.py` (production task, routed model, persistence ON).

**.2 route_model (D-091).** Pure `route_model(request[, tenant_policy]) -> model_id`: explicit `operational_context.llm_model_identifier` wins; else the D-091 archetype table on `semantic_context.archetype_hint` (`configuration`/`ui` → Sonnet, `data_behavior`/`permission`/`integration` → Opus); else Opus (default-to-capability); tenant `always_use_opus` honored. One model per batch, bound as `model_override`; reuses `router.SONNET`/`OPUS`. Forks 2–4: `model_override` mechanism (not `_CHAINS`); tenant `always_use_opus` honored; `archetype_hint` reliability — Opus-default safe, the Sonnet cost win contingent on callers setting the hint (pilot-integration note).

**.3 Error policy (pilot).** Abort-on-error with per-requirement-committed isolation (D-096.6). With D-105's refuse-not-crash, the remaining uncaught-error source is provider `LLMError` → aborts the batch, earlier requirements stay committed. Best-effort-continue deferred (needs a runtime error hook).

**.4 Deferred** to the production-integration phase (the HTTP/worker layer wrapping the runner): the trigger/intake (Jira → request building), auth, async job queue, retry/idempotency (re-running an aborted batch conflicts on the `request_id` PK — fresh id per attempt or upsert, an API-layer strategy). Connection held across LLM latency is pilot-acceptable (keepalives + small batches); flagged for scale.

**.5 Provenance.** `llm_calls.model_identifier` records the actual routed model (`turn.model`); `prompt_version` threaded (slice 2). `route_model` may write the resolved model back to `operational_context.llm_model_identifier` for request-level provenance.

---
