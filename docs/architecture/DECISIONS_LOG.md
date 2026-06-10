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

**Regeneration lineage typed.** `prior_request_id` is the binary discriminator: NULL → fresh request; non-NULL → regeneration. When regeneration, `deltas` carries a typed `regeneration_kind` discriminator over six values:

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

- *Substrate-authorized vocabulary at vocabulary positions.* All enum-typed parameters bounded to substrate-2 taxonomy or substrate-3 reasoning vocabulary. Archetype ∈ {data_behavior, configuration, permission, ui, integration}; claim_kind ∈ substrate-2 taxonomy; dismissal_reason ∈ D-076 enum (8 values); refusal_kind ∈ D-073 enum (8 values post-Theme 5); cause ∈ D-083 enum (3 values); admissibility_layer ∈ D-083 enum (2 values).
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

## D-107 — Formula parser → verified negatives: phase design (S1 sub-feature feeding S3)

**Date:** 2026-05-25
**Substrates affected:** [S1, S3] (S2 behavioral-recipe follow-on flagged)
**Status:** Active — Fork 2 (caveat semantics) OPEN, gates slice 4

Realizes D-100.1 (the formula parser → verified negatives, the Phase 3 differentiation headline), grounded against corrections-log §17, D-083 (grounded-negative discipline), D-101 (the caveated negative), and SF validation-rule formula reality. The arc: a pure formula parser produces a typed AST consumed by two sides — S1 REFERENCES-edge field extraction (closes §17) and S3 verified-negative violating-value derivation. This is an **S1 sub-feature feeding an S3 phase**: the parser + REFERENCES edge is independently mergeable S1 value (closes a Tier-1 edge gap with no S3 dependency); the verified negative is the S3 consumer. One phase branch, sliced S1-first.

**.1 The verified bar (fail-loud, grounded-only).** A verified negative is emitted only when the formula is *fully parsed* AND the predicate is *create-time / single-object / field-value* AND a violating create-payload is *derivable with certainty*. "Fully parsed" is necessary, not sufficient: `PRIORVALUE` / `ISCHANGED` / `ISNEW` (org-state / update scenarios) and cross-object refs parse but are not statically derivable to a create → caveated fallback (D-101), unchanged. Anything the parser does not fully understand → caveated fallback. No guessed semantics.

**.2 Forks locked.** **Fork 1 = A:** the parser is a shared pure library; S1 and S3 each re-parse `formula_text` at use — no persisted AST (re-parse is cheap + deterministic). **Fork 3 = hand-written recursive descent** (bounded grammar, no dependency, deterministic). **Fork 4 =** the parser lives in `primeqa/semantic/` (S1 owns the VR formula data + §17; S3 imports it, consistent with existing S3→S1 imports).

**.3 Fork 2 — OPEN (gates slice 4).** What "verified" does to the caveat: (2a) static parse-verification drops the caveat fully → marker `LAYER_2`, no caveat (the caveat's stated reason — "formula unparsed", D-101.3 `DEEPER_VERIFICATION_LAYER_UNPARSED` — is genuinely resolved); (2b) keep a caveat under a new kind ("statically-verified, not behaviorally-executed"), reserving full drop for the behavioral recipe. Leaning 2a; locked with the maintainer before slice 4. Either way the integration mechanism is a **per-emission** caveat decision: `has_layer_2(claim_kind) AND NOT verified_this_formula` (vs today's claim_kind-general `semantic_completeness` verdict).

**.4 D-100.2 behavioral expect-rejection recipe — out of phase.** A fully *behavioral* verified negative (the recipe performs the prohibited op and asserts SF rejects) needs a substrate-2 recipe-model expect-rejection / expect-error step (the executor already has step-level `expect_fail`; the recipe *artifact* cannot express it). Flagged as an S2 follow-on, not this phase. This phase delivers **static** Layer-2 verification.

**.5 Ordered slices.** (1) parser library — tokenizer + recursive-descent → typed AST for the covered grammar, fail-loud not-fully-parsed sentinel; (2) REFERENCES-edge field extraction → `validation_rule_field_refs` → existing `derivation.py` edges (closes §17 — standalone S1 value); (3) violating-value derivation (AST → verified payload | not-derivable, encoding the .1 bar); (4) emission integration (per-emission caveat, `LAYER_2` marker, caveated fallback) — gated on Fork 2.

**Amendment (2026-05-25) — slice 2 landed (REFERENCES, approach B).** §17 closed for **same-object** references via a sync-time bespoke writer (`primeqa/sync/validation_rule_refs.py`): parse `formula_text` → resolve same-object refs via `parent_resolver` (Field external_id `"{Object}.{field}"`) → write `validation_rule_field_refs` rows (`read`/`priorvalue`/`ischanged`, multi-row per field); `derivation.py` emits the REFERENCES edges from the junction, unchanged. **Deferred (Phase-3+ item):** cross-object **dotted** REFERENCES — the dotted segment is a relationship name, not an object; resolving it needs cross-object relationship resolution at sync (a separate S1 capability). Skipped → `references_status = partial`. **ISNEW** references no field → no edge (1a). **`references_status`** (migration `20260525_0010`) is 4-state: **`pending`** (honest default — extraction not yet evaluated, never conflated with a parse verdict), `complete`, `partial`, `unparsed` (3a). **Re-sync is version-scoped:** `errorConditionFormula` is in the change-detection hash (`hash_normalized`), so a formula change supersedes the VR (new `entity_id`); the new version gets fresh field_refs, the old id keeps its historical refs, and the current view reflects the new formula — no stale current edges, no clear-then-write (insert-only + `ON CONFLICT DO NOTHING` for idempotent re-runs). **Writer = bespoke (Fork B):** REFERENCES owns a single-purpose writer + `derivation.py`; the shared property-less `EdgeSpec` junction mirror is untouched. **Framework generalization (Fork C)** deferred to the next property-bearing junction (extract-on-recurrence).

**Fork 2 resolved — 2a (caveat semantics).** When a formula is fully parsed and the violating value is derivable, drop the `DEEPER_VERIFICATION_LAYER_UNPARSED` caveat and mark `LAYER_2` (verified). The "not behaviorally executed" status lives in the recipe (inspection, not a masquerading rejection test), not as a semantic caveat — pinning it as a caveat would leak an S4/execution concern into the S3 claim. Behavioral confirmation is D-100.2 (the expect-rejection recipe), a separate S2 follow-on. Realized in slice 4. (Supersedes .3's "OPEN".)

**Amendment (2026-05-25) — slice 4 landed (emission integration, Option C).** Static Layer-2 verification wired end-to-end: a grounded prohibition negative whose grounding VR formula *parses* and yields a *derivable* violating value is emitted `LAYER_2` (verified), caveat dropped; everything else keeps the Layer-1-plausible caveated fallback (D-101), unchanged. The verified-vs-caveated line **is** the derivable/not-derivable line (slice 3's `derive()`), realizing 2a. **Option C — `derive()` is the gate, not a persisted payload.** The derived violating create-payload decides verified-ness and is then discarded; it is **not** written onto the claim. Rationale: `ProhibitionClaimBody` is identity-bearing, and `_generic_canonicalize_body` (S2) folds *every* field — `None` included — into `identity_hash`; a `violating_values` field would shift the hash of *all* prohibition claims, breaking the D-088 continuity invariant for already-persisted negatives. So the claim body is **byte-identical** whether verified or caveated — only `admissibility_layer` and the caveat posture differ. Persisting the violating payload is deferred to **D-100.2** (the behavioral expect-rejection recipe), where the mutation is the recipe's legitimate content (a recipe-model field, not a claim-identity field). **Mechanism:** the caveat authority gains a per-emission axis — `requires_caveat(claim_kind, verified)` / `caveat_kind(claim_kind, verified)` = `has_layer_2(claim_kind) AND NOT verified` (the claim_kind-general registry is unchanged; `verified` is the per-formula discharge). `GroundedNegative.vr_formulas` carries the grounding VRs' `formula_text` (re-found from the same in-scope neighborhood by the same (edge_type, far_type) the Layer-1 dimension matched); `_author_negative` runs `derive(parse(f))` over them with **at-least-one-derivable** semantics (any single VR firing produces the rejection; others can only add rejections); `EmissionBundle.admissibility_layer` carries the marker and `finalize_outcome` reads it (no longer hardcoded `LAYER_1`). The recipe stays *inspection* (re-verify a VR APPLIES_TO the subject) — the behavioral construct-and-observe is still D-100.2. **Drift-guard:** the invariant is `LAYER_2 ⟺ caveat-dropped` — under Option C there is no payload-present clause, so derivability alone is the pivot. **Eval:** corpus gains `verified-prohibition-negative` (a VR with `ISBLANK(Reason__c)` → `layer_2`, no caveat); the caveated probe's note is refreshed (parser landed). All four slices now landed; phase ready for close-out (deferred: conservative bail-case revisit, cross-object dotted REFERENCES, the D-100.2 behavioral recipe).

---

## D-106.4 — Production integration of the substrate-3 generation engine (the S3 service layer)

**Date:** 2026-05-25
**Substrates affected:** [S3]
**Status:** Active — Forks A/B locked; build pending (slices 1–5)

Expands D-106.4 (flagged-deferred in D-106) into its design. **Context:** `run_generation` (D-106.1) is a pure in-process core, **test-invoked only** — no service layer. The v1 `TestCaseGenerator` already has a complete async queue (`generation_jobs`: enqueue → worker consumer + heartbeat → status / cancel → scheduler reaper). D-106.4 builds the **S3** service layer, mirroring that v1 lifecycle as a proven in-repo template — **mirror, not reuse** (different generator, different output).

**Scope (minimal pilot-drivable vertical).** Intake (requirement → `GenerationRequest`); authed enqueue; an S3-owned job queue + worker consumer running `run_generation` with heartbeat; idempotency; status + a thin stale-job reaper; thin cancel. **Deferred:** best-effort-continue (abort-on-error D-106.3 retained; the runtime error hook stays deferred); the connection-held-across-LLM-latency scale mitigation (pilot-OK); a dedicated generation process (co-locate the consumer in the existing worker); full error-UX polish.

**.A Fork A — mirror, not generalize (LOCKED).** An S3-owned `generation_jobs`-style table + a clean S3 consumer module, shaped like v1's claim → heartbeat → reap → status lifecycle, **sharing no code**, co-located in the worker process. *Rationale:* a shared queue primitive now would abstract from one real implementation plus one hypothetical (premature), and would couple the S3 substrate to the legacy v1 runtime — inverting the intended dependency direction. *Accepted cost:* two near-identical queues. *Revisit trigger:* a genuine third consumer.

**.B Fork B — two-layer idempotency (LOCKED).** (1) **Job-level get-or-create** per `(tenant, requirement, s1_version)` — dedupes the logical request; mirrors v1 `create_or_get_job`. (2) **Fresh `request_id` per attempt**, linked via `prior_request_id` (D-071) — no PK collision; the ledger stays append-only (D-096.6). **Not** upsert on `generation_requests` (which would mutate the immutable ledger record and discard attempt history).

**Secondary decisions.** *s1_version:* pin to the tenant's current S1 version **at enqueue**, recorded on the job — a reproducible run against a known org snapshot. *LLM key:* resolve from tenant config (the gateway already does the lookup), **not** passed raw into `run_generation`. *Error capture:* a thin classified failure on the job; abort-on-error retained.

**Slice plan (dependency-ordered).** (1) S3 job model + the two-layer idempotency (.B); (2) intake builder (requirement → `GenerationRequest`); (3) worker consumer (claim → intake → `run_generation` → persist → complete / fail, with heartbeat); (4) endpoints (authed enqueue + status + thin cancel); (5) stale-job reaper.

**Amendment (2026-05-25) — slice-1 grounding refinements (supersede "mirror v1's placement" and "linked via `prior_request_id`").** Grounding the build against the shipped schema corrected two points:

- **.A placement is PER-TENANT.** Fork A's "mirror" is the v1 *lifecycle* (claim → heartbeat → reap → status columns), **not** its placement. v1 `generation_jobs` is a *shared* public-schema table (FKs to v1 control tables); the S3 ledger is *per-tenant* (alembic tenant branch, "no `tenant_id` column — isolation by schema"). The S3 job table follows the **substrate's per-tenant convention** — where its referenced data (`s1_version_seq` → `logical_versions`, `current_request_id` → `generation_requests`) lives and where `run_generation` already operates. Knock-on: **no `tenant_id` column**; idempotency is a **full `UNIQUE (requirement_key, s1_version_seq)`** (S3 retries *in place* via `start_attempt` — one job per logical request, ever; v1's app-enforced *partial*-unique was a DDL workaround for a "new job after terminal" model the S3 design does not use); `created_by` is a loose int (no per-tenant `users` table to FK). Consequence: cross-tenant work discovery becomes a **slice-3** concern (iterate tenant schemas at pilot scale; a shared work-signal at real scale).

- **.B attempt lineage is JOB-level (B-job).** A queue retry is *operational* machinery, so by the semantic-vs-operational separation (D-087 b) attempt history lives **on the job**, not in `generation_requests.prior_request_id`. The ledger's `prior_request_id` (CHECK `(prior_request_id IS NULL) = (deltas IS NULL)`, `regeneration_kind` set-iff) stays reserved for genuine **semantic** regeneration (incl. `FAILURE_RECOVERY` — a real user/eval-initiated re-gen, not a transient worker retry). Each attempt is an **independent** ledger request (`prior_request_id` NULL, no synthesized `deltas`); a normalized `s3_generation_job_attempts` table owns the per-attempt history. This still closes the `request_id` PK collision (the fix only needs a *fresh* id; the *link* was the part that would have leaked operational retries into the semantic chain).

**Amendment (2026-05-25) — slice-2 intake is caller-fed (decoupled, option B).** S3 intake assembles a `GenerationRequest` from per-tenant inputs but **does not fetch requirement text**: `GenerationRequest.requirement_refs` is caller-supplied `{key, text}` by design (the substrate *receives* requirement text, it does not fetch it). Requirement-text resolution from the v1 `public.requirements` table therefore lives at the **enqueue boundary (slice 4, v1-side)** — not in S3 intake — honoring the substrate contract and Fork A's anti-coupling rule (no S3 → v1 schema dependency; `jira_key` is also nullable for manual reqs, which the v1 side keys by int `id`). Slice 2 ships the two clean, per-tenant/pure pieces: `resolve_current_s1_version` (the `MAX(version_seq)` snapshot pin + name, fail-loud when the tenant has no S1 version) and `build_generation_request` (pure fresh single-requirement assembly — no lineage, CHECK-valid; receives the `request_id`, does not mint). **Tracked → slice 4:** `resolve_requirement(requirement_key) -> {key, text}` (v1-side read of `public.requirements`; manual reqs keyed by int `id`).

**Amendment (2026-05-25) — slice-3 worker consumer (api_key secondary CORRECTED).** Grounding the consumer corrected the secondary "LLM key: resolve from tenant config":

- **api_key is environment → connection-scoped, resolved worker-side.** There is **no tenant-level Anthropic key**; the gateway's `llm_call` takes `api_key` from the *caller*. The v1 path resolves it via `conn_repo.get_connection_decrypted(env.llm_connection_id, tenant_id)["config"]["api_key"]` (Fernet-decrypted from the v1 connection store). The S3 consumer mirrors that **in the worker layer** (v1 `EnvironmentRepository` + `ConnectionRepository`), so the job pins an **`environment_id`** as the resolution handle. The substrate core stays **api_key-param-pure** (caller-supplied to `run_generation`); `environment_id` lives only on the queue table (the v1↔substrate bridge) and **never reaches the ledger / `GenerationRequest`**. The resolver is **injectable** (`api_key_resolver`) so the consumer is testable without a real Fernet connection.
- **Cross-tenant discovery — resilient enrichment pattern.** Mirror `enrichment_tick`'s `_discover_tenant_schemas` + **per-tenant try/except** (one tenant's failure must not starve the others) — **not** the fail-loud `admin_iterate_all_tenants`. One job per tenant per tick (bounded, like the v1 generation tick).
- **Heartbeat — `run_generation` is a single blocking call** (no progress hook): heartbeat at claim/start, with a **generous reaper timeout** (slice 5); mid-run heartbeat (threading) deferred.
- **`requirement_text` + `environment_id` pinned on the job** (migration `20260525_0030`, both nullable/loose; populated at enqueue/slice 4, read by the consumer). Under B the consumer must not re-read `public.requirements`, so the text rides the job. **`environment_id` is a determined attribute** (`s1_version_seq` comes from one environment's sync, so it implies the env) — **NOT** part of the key; `UNIQUE (requirement_key, s1_version_seq)` is unchanged.

**Amendment (2026-05-25) — slice-4 enqueue endpoint (layer split; `resolve_requirement` realized).**

- **`resolve_requirement` realized** (closes the slice-2-tracked line): v1-side, reads `public.requirements` via `RequirementRepository`. Keying: `key = jira_key or "req-<id>"` (manual reqs have NULL `jira_key` → keyed by int id); `text` = `jira_summary` / `jira_description` / `acceptance_criteria` concatenated.
- **Layer split.** The enqueue is a thin **bridge**: the **substrate** `enqueue_s3_generation(*, tenant_id, requirement_ref, environment_id, created_by)` is **pure + caller-fed** (takes a *resolved* `{key,text}` + a route-validated `environment_id`; does `resolve_current_s1_version` + `create_or_get_job` pin) and contains **no v1 read**; the **v1-side** `resolve_requirement` (and env validation) do the v1 reads; the **`views.py` routes** glue them. This is option B / Fork A applied at the enqueue layer — the substrate core stays v1-read-free. The split was *surfaced* by the test-DB boundary (v1 data on Railway, the substrate queue on the governance DB) but it mirrors the architectural seam held all phase: the seam was already correct; the test infra just made it visible.
- **Permission — reuse the v1 role-gate** (admin / tester, superadmin bypass), mirroring `/requirements/<id>/generate`. No new "generate" permission minted (the catalog has none; generation is role-gated in v1).
- **env / version consistency.** Enqueue pins the tenant's **current** `s1_version` (per-tenant `MAX(version_seq)`) + the caller's `environment_id`. The generated output depends on (requirement, `s1_version`); `environment_id` is the api_key handle, so it stays a determined **attribute** — the `UNIQUE (requirement_key, s1_version_seq)` key is unchanged.
- **Deferred (tracked) — full HTTP-route test.** A Flask-client test of the routes (auth-reject / status / cancel) needs a **combined v1+substrate test DB** (the test infra splits the two — v1 on Railway, substrate on the governance DB — though production is one DB). The routes mirror the proven v1 generation routes, and behavior is covered at the **service level** (the requirement mapping; `enqueue_s3_generation` pin + idempotency; the enqueue→consumer→complete e2e smoke — the first full vertical). The HTTP-route test is a **test-infra follow-on**, not a silent skip.

**Amendment (2026-05-25) — slice-5 stale-job reaper (the final slice).**

- **Reap stale → `failed`.** `GenerationJobStore.reap_stale_jobs(stale_minutes)` selects jobs in `claimed`/`running` whose `COALESCE(heartbeat_at, claimed_at)` is older than the timeout and fails them through the existing `fail()` path (`error_code='stale_timeout'`), so the **open attempt is finalized like a normal failure** (D-106.3 fail-loud — the dead job surfaces, doesn't sit forever). `COALESCE` covers the brief "claimed but not yet heartbeated" window.
- **Generous timeout.** Unlike the v1 reaper's 2 min (v1 heartbeats every ~10s *during* the run), the S3 consumer heartbeats only at claim/start — `run_generation` is a single blocking call with no mid-run hook (slice 3) — so the timeout must exceed the longest legitimate run; default **10 min**. Mirrors the v1 reaper's shape, not its value.
- **Scheduler-hosted, per-tenant, resilient.** `s3_reaper_tick` runs in the scheduler's `scheduler_tick` (every `REAPER_INTERVAL=60s`), enumerates active tenants from `shared.tenants` (`admin_run_in_shared_schema`), and calls the substrate `run_s3_reaper_tick(tenant_ids)` — a **per-tenant try/except** loop (one tenant's failure never starves the others), the same resilience the consumer uses.
- **`fail()` hardened (race-safe).** `fail()`'s job-UPDATE gains `AND status NOT IN ('completed','failed','cancelled')` so a job that completes between the reaper's select and its fail-call is never clobbered back to failed. Backward-compatible (the consumer fails a `running` job — non-terminal).
- **Requeue-with-cap deferred.** Stale → `failed` (terminal); automatic requeue-with-attempt-cap is deferred (the `attempt_count` / `s3_generation_job_attempts` machinery exists, so it's a clean future increment). A user/endpoint re-enqueue is the manual path today.

**Phase status:** all five slices landed (job model + idempotency → intake → consumer → enqueue → reaper). Phase close-out (SPEC §-realized / DEFERRED_ITEMS / EVOLUTION reconciliation, per the D-107 pattern) + the tracked HTTP-route test are the remaining, separate steps.

---

## D-108 — Substrate 4 (Execution): foundational design

**Date:** 2026-05-26
**Substrates affected:** [S4] (reuses neutralized v1 mechanics; feeds S6)
**Status:** Active — F1 / F2 / F3 locked (TA-reviewed); F4–F7 triaged. First vertical (metadata-inspection) pending.

Opens Substrate 4, the **execution engine**: S2 recipe → execution → captured truth → S6. Execution **captures** truth; intelligence **interprets** it (the substrate boundary). Architecture: `substrate_4_execution/SPEC.md`.

**F1 — v1 reuse boundary (LOCKED; TA-reviewed + code-verified).** The v1 mechanics are cleanly separable from v1 semantics — at the layer *beneath* `execute_step` (itself an entangled monolith: mechanical dispatch + `success→status` + the inline `expect_fail` flip + `run_step_results` persistence + v1 SSE, in one function). **Reuse** the pure mechanical primitives — the REST transport client (`SalesforceExecutionClient`), `integrations/` retry/auth + the pure `classify_sf_exception`, the `$var` resolvers, the `data_engine` factory/template primitives, the cleanup mechanism (reverse-order + `PQA_%` sweep) — **lifted to a neutral shared module** where pure (resolving the substrate→v1 dependency direction; `integrations/` is already neutral). **Own** the orchestration, outcome interpretation, the result model, and the negative-test semantics. **Out:** the `execute_step` monolith, the `expect_fail` shallow-negative, `run_step_results`, `TestCaseDataBinding`'s TC-link. The lift is a **small incremental v1 refactor**, per-increment, not up front.

**F2 — result model (LOCKED philosophy; schema deliberately NOT locked).** **Evidence-first, S4-owned:** capture raw observations richly + honestly (timestamps + ordering, request/response, before/after state, error surfaces, env context, per-step outcomes) — an **extensible** schema that grows with the first vertical and richer recipe kinds. **Posture, not evidence, crosses to S2:** S2 receives a compact posture (executed / verified / failed / caveated; latest refs; coverage freshness); the raw evidence stays S4-owned and is **S6's** raw material. **Mine v1 for lessons, not inheritance:** v1's run/result schema (api_request/response, before/after, `comparison_details`, `failure_class`, timings) informs *what* to capture, not the schema (welded to v1's `expect_fail`/run model).

**F3 — first vertical = metadata-inspection (LOCKED).** The only kind S3 emits today (inspection-trigger + metadata-recipe, D-099 / D-107): live-read the org and assert the grounded claim still holds (execution-time re-inspection, D-099.3). No test data (F6), no browser (F4) — the thinnest end-to-end spine: bridge → executor → evidence → posture.

**F4–F7 (triaged; leans, not locked).**
- **F4 — recipe-kind scope.** Defer `ui-recipe` (browser), `event-subscription-recipe`, `callout-intercept-recipe`; start metadata, then CRUD (`data-recipe`). The behavioral expect-rejection negative (D-100.2) lands with CRUD.
- **F5 — capability matching.** Minimal `ExecutionEnvironmentBody`→env match for the first vertical (metadata-inspection assumes only read access); rich capability-fit selection deferred.
- **F6 — test-data provisioning.** None for the first vertical (inspection needs no prerequisite records); provisioning (reusing/evolving `data_engine` + cleanup) lands with the CRUD increment.
- **F7 — failure-path / remediation.** S4 **captures failure-truth and does not remediate.** The dormant fix-and-rerun agent (G-001) stays a v1 concern; the S4-execution-failure ↔ agent relationship is settled later (S4 produces the evidence a remediation loop would consume).

---

## D-108.1 — S4 slice 2: thin S4-local Tooling-read client + translator/transport boundary (F1 realization)

**Date:** 2026-05-27
**Substrates affected:** [S4] (reuses v1 credential plumbing; feeds S6)
**Status:** Active — slice-2 design (TA-reviewed). Sub-decision of D-108 F1.

Resolves F1's "authenticated Tooling transport" question for the metadata-inspection executor. **Grounding finding:** the S1-sync Tooling *fetchers* (`integrations.sf_client.SalesforceClient.fetch_validation_rules`) ride a **refresh_token** client wired only in tests; the **production-credentialed** Tooling path is the D-106.4 one — env → `ConnectionRepository.get_connection_decrypted` → `_oauth_token` (client_credentials / password per `auth_flow`) → access token → a generic `query_tooling(soql)`. So the reusable unit is the **credential plumbing** + the **encoded edge→SOQL translation knowledge**, **not the fetcher object** (which carries sync-world assumptions — bulk two-phase fetch, syncability filtering, normalize/materialize).

**Decision 1 = (a): an S4-local thin Tooling-read client reusing `_oauth_token`.**
- **Reject (b)** (import the v1 metadata `SalesforceClient`): an S4→v1 dependency inversion — the direction F1 explicitly resolves by lifting to neutral.
- **Defer (c)** (lift a neutral transport now): only once the neutral transport's shape is visible under CRUD / broader-read pressure — not because a single consumer exists.

**Boundary (slice-2 realization of F1).** Credential resolution → **reused** (`_oauth_token` / D-106.4); Tooling transport → **thin S4-local** (authenticated read + pagination + typed error mapping, *nothing more* — never entity semantics, edge logic, metadata interpretation, or traversal policy); edge→SOQL translation → **S4 operational mapping** (finite, edge-keyed); semantic interpretation → **S6**; ontology authority → **S1 / S2**. *S4 reuses operational credential plumbing, not metadata-sync semantics or semantic execution assumptions.*

**Realization principle (translator is operational, not semantic).** Edge→SOQL mappings are operational realization rules, not semantic authority: the query reflects only what the recipe's assertion carries; a semantic filter (active-ness, object identity) **must trace to the recipe/claim, never a translator default.** Consequence for slice 2: the emitted inspection recipe asserts plain `exists`, so the `APPLIES_TO` translation carries **no `Active` filter** — active-ness, if required, is an S3/emission concern, not a translator injection. Slice 2 verifies where active-ness lives.

**Guards.** Result-model schema stays unlocked (slice 3); the translator stays operational (no ontology); the no-interpretation boundary (S4 records absences, S6 interprets them) stays hard.

---

## D-108.2 — S4 slice 3: result-store schema (run-entity + JSONB captured-trace, per-tenant)

**Date:** 2026-05-27
**Substrates affected:** [S4] (owns the result store; hands run identity to S2 at slice 4; feeds S6)
**Status:** Active — slice-3 design (TA-reviewed). Concretizes D-108 F2 (the result model's first schema).

Unlocks F2's first concrete schema for the metadata-inspection vertical: where S4's captured truth is persisted, and in what shape.

**Placement — per-tenant.** The result store is execution truth for one tenant's recipes against one tenant's orgs — isolated tenant data. It lands in the **per-tenant schema** (alembic tenant branch, unqualified, **no `tenant_id` column** — isolation by schema, the substrate-1/-2/-3 convention), beside `test_recipes` / `test_recipe_runtime_state`. New tenant-branch migration chains off the head `20260525_0030`.

**Schema = A: a run-entity with typed columns + a JSONB captured-trace.** One **kind-agnostic** table `s4_execution_runs`:
- **Typed identity / outcome columns (queryable):** `run_id` UUID **PK** · `recipe_id` · `recipe_version_seq` · `claim_test_id` · `claim_version_seq` (NULL) · `environment_id` · `outcome` · `started_at` · `finished_at` · `duration_ms`.
- **`evidence` JSONB:** the per-step captured trace (translated queries, structured filters, returned rows, per-step timings + error surfaces) — *raw observation*, the extensible part that grows per recipe kind (F2).
- **`outcome` enum:** reuses the existing `run_outcome` PG enum (`passed`/`failed`/`errored`/`skipped`, `create_type=False`) — **verified** to match the S4 vocabulary AND slice 4's `report_run_outcome` signature exactly (no v1 `error`-vs-`errored` divergence). The run column reconciles to the S2 boundary verbatim.

**Why A fits the DB philosophy (not a bent rule).** The run **is an entity** — its identity + outcome are typed, queryable columns, never buried in JSONB. The JSONB carries *only* the captured trace (raw observation). The no-JSONB-blob discipline targets the **semantic store** (claims/recipes — meaning must be columnar, queryable, hashable); execution truth is not semantic data, so JSONB-for-trace is the right tool, not an exception. One table serves all recipe kinds; only the JSONB grows — CRUD/UI reuse the same identity columns without schema churn.

**Decisions recorded:**
- **Schema A**, kind-agnostic (`s4_execution_runs`), per-tenant.
- **B-trigger (A→B is a reversible forward migration, not a lock):** promote per-step facts to a structured child table when a real per-step query need emerges — S6's concrete query patterns, or CRUD's N-step shape. Additive (a child table beside the run row); deferring it costs no rework.
- **`run_id` executor-minted** (`uuid4()` at run start; the run self-identifies from birth) — a small slice-2-shape extension to `RunEvidence` (F2-expected). Flows produce → persist (PK) → slice 4's `report_run_outcome(last_run_id=run_id, …)`.
- **Produce/persist boundary:** the executor stays produce-only (in-memory `RunEvidence`, no DB import); a separate persister (`persist_run_evidence(session, evidence) → run_id`) writes. Slice 2's no-DB unit tests stay untouched.

**Guards.** §4 concretizes but the JSONB trace stays extensible (grows per kind); the A→B path is recorded; append-only DECISIONS_LOG.

---

## D-108.3 — S4 slice 4: finalize step (persist + posture callback), the S4→S2 write boundary

**Date:** 2026-05-27
**Substrates affected:** [S4] (writes to its own result store + to S2's runtime-state surface); [S2] (consumed via `report_run_outcome`, no new method)
**Status:** Active — slice-4 design (TA-reviewed). Completes the first vertical's four components (bridge → executor → result store → posture callback).

Closes the first vertical's spine: the grounded run outcome + evidence flows back to S2 as posture.

**Finalize orchestration (persist → posture, atomic on one session).** A thin `finalize_run(session, evidence, *, coordinator=None) → RecipeRuntimeState` in `execution_engine/finalize.py`:
1. `persist_run_evidence(session, evidence)` — the slice-3 result-store write (`s4_execution_runs` row; the run's durable truth).
2. `coordinator.report_run_outcome(session, actor='s4', recipe_id=evidence.recipe_id, last_run_id=evidence.run_id, last_run_at=evidence.finished_at, last_run_outcome=evidence.outcome, last_run_recipe_version_seq=evidence.recipe_version_seq)` — the S2 boundary callback (`test_recipe_runtime_state`; coverage freshness).

Both calls `flush()` (never `commit()`) on the **same caller-provided session**, so persist + posture are **one atomic unit** — the caller owns the commit boundary (the substrate D-β contract). `report_run_outcome` is idempotent first-write-wins on `last_run_id`, so a re-finalize of the same run is a safe no-op.

**Idempotency model — two layers (refines the "safe no-op" shorthand above).** `finalize_run` is **not** a whole no-op on a repeated `run_id`. It persists first, and `s4_execution_runs` has a `run_id` **PK**, so a true re-finalize of the same run is **fail-loud** (`IntegrityError`) — runs are never silently duplicated (each run mints a fresh `run_id`, so a double-finalize is a bug, caught not swallowed). The no-op idempotency lives at the **posture layer only**: `report_run_outcome` is first-write-wins on `last_run_id`, so a *posture-only retry* (re-reporting an already-recorded run) is the safe no-op. The two layers: persist = fail-loud on duplicate `run_id`; posture = idempotent on `last_run_id`.

**Grounding finding (no precondition surprise).** `report_run_outcome` validates *only* actor authority (`actor='s4'` is purpose-built — the `ActorKind` taxonomy scopes S4 to this one call); it does **not** check recipe existence or state (logical FK; upsert keyed on `recipe_id`). S4 supplies a real `recipe_id` (carried by the plan from the `RecipeRead`), so no precondition gap.

**The S4→S2 write boundary completes the read-through.** Slice 1 (D-108.1) established the **read** side — S4 reads S2 recipes through the Coordinator (`select_recipe_for_execution`). This adds the **write** side: S4 reports posture through the Coordinator (`report_run_outcome`). The new dependency `execution_engine → SemanticTransactionCoordinator` is the sanctioned S4↔S2 direction; the Coordinator is **injectable with a default** (`coordinator or SemanticTransactionCoordinator()`) so unit tests spy the exact `report_run_outcome` kwargs without a DB.

**Decisions recorded:**
- `finalize.py` is **separate from `result_store.py`** — persistence (the store) and orchestration (persist + posture) are distinct concerns.
- **Coordinator injectable, default-constructed** — testable without a DB; the Coordinator is stateless (`SemanticTransactionCoordinator()`, no args).
- **Scope boundary:** slice 4 = the finalize **seam** + tests. The production **trigger** — a worker / route running `resolve_tooling_client → build plan → execute → finalize` against a real recipe + live org — is **deferred** (nothing calls the executor yet; the spine is built bottom-up). It is the next piece after the vertical's four components land.

**Guards.** No new S2 method; no migration; persist + posture atomic on one session; the read-through boundary now covers both directions.

---

## D-108.4 — S4 run path: the end-to-end recipe-execution orchestrator (third Coordinator caller)

**Date:** 2026-05-27
**Substrates affected:** [S4] (orchestrates the spine); [S2] (read via `select_recipe_for_execution`, no new method)
**Status:** Active — run-path design (TA-reviewed). Wires slices 1–4 into a runnable synchronous path; the async-orchestration restructure is deferred.

Slices 1–4 built the spine's components (bridge → executor → result store → finalize) but **nothing called them** (the slice-4 grounding found no caller). The run path is that caller.

**Orchestrator.** `run_recipe_execution(session, test_id, *, environment_id, available_environment=None, client=None, coordinator=None) → RunPathResult` chains `select_recipe_for_execution` → `build_metadata_inspection_plan` → `resolve_tooling_client` (or injected `client`) → `execute_metadata_inspection` → `finalize_run`. A thin outer `run_recipe_execution_for_tenant(tenant_id, …)` owns the `get_tenant_connection` context + the single commit (the production entry). Defaults are injectable (the executor/finalize discipline): `available_environment` → minimal inspection env (`auth_kind="metadata_api_user"`); `client` → `resolve_tooling_client`; `coordinator` → `SemanticTransactionCoordinator()`.

**Third Coordinator caller — the read/select side.** S4 now uses the S2 Coordinator on both sides of the read-through boundary: **read** via `select_recipe_for_execution` (this run path) — joining the **write** side (`finalize.py` → `report_run_outcome`, D-108.3). The S2 Coordinator's production callers are now `generation/persistence.py` (LedgerPersister — claim/recipe write), `execution_engine/finalize.py` (posture write), and `execution_engine/run.py` (recipe read).

**`RunPathResult`.** Distinguishes **ran** (carries `evidence` + `runtime_state`) from **no-eligible-recipe** (carries a reason; no run happened — `select_recipe_for_execution` returned `None`: no approved claim / no approved recipe / no environment match). "No recipe" is not an error and not a run — it is a first-class, distinguishable result.

**Transaction boundary = A (single transaction), with the async restructure deferred.** One tenant-scoped session/transaction spans `select → execute → finalize`, committed once on clean exit (the `LedgerPersister` idiom). One session spans both data domains because `get_tenant_connection` sets `search_path = "tenant_<id>", public` (per-tenant S2/S4 tables unqualified → `tenant_N`; v1 `environments`/`connections` → `public`). **A holds the DB transaction open across the live Tooling read (~1–2 s)** — acceptable for this **bounded, low-concurrency synchronous** path. It does **not** generalize: the future **async orchestration must not hold DB across external I/O** and will bracket the live read with brief transactions (orchestrating the components directly). A here is the right sync-path call and does not preclude that restructure.

**Errored runs still finalize.** The executor catches `SFClientError` → an `errored` `RunEvidence` (does not raise); an errored run is truth, so it persists + reports posture. Only an *unexpected* exception (code bug / fail-loud predicate) propagates and rolls back — a half-run from a defect is never persisted.

**Inject-client driven by a schema gap (live-test finding).** The local substrate test DB has **no `environments`/`connections` tables** (those are v1 `migrations/*.sql` public-schema tables, not in the alembic shared branch), so `resolve_tooling_client` cannot run there. The whole-spine live test injects a real `ToolingReadClient` (slice-2 pattern, from `SF_*` creds) and bypasses credential resolution (already unit-tested in slice 2). This *necessitates* the injectable `client` parameter.

**Scope.** This is the synchronous run path + its tests. The **async/worker orchestration** (higher concurrency, brief-transaction bracketing) and any v1 route/scheduler wiring are deferred — they consume this orchestrator's components.

**Guards.** No new S2 method; no migration; persist + posture stay atomic (finalize, D-108.3); boundary A is sync-only with the async restructure explicitly reserved.

---

## D-109 — Substrate 4 CRUD phase: opening + landscape (forks open)

**Date:** 2026-05-27
**Substrates affected:** [S4] (the data-mutation vertical); [S3] (data-recipe emission — prerequisite); [S2] (recipe-model expect-rejection — for the behavioral negative)
**Status:** Active — phase opening. Landscape grounded (read-only); the polarity + sequencing forks are **open** (leans noted, not locked). No build yet.

Opens the second S4 vertical: **CRUD / `data-recipe`** (data mutation — the first mutation recipe kind), broadening S4 from the metadata-inspection vertical (D-108 → D-108.4). Built PR-based on `phase-5-substrate-4-crud` per the CONVENTIONS working agreement (substrate work → feature branch → merge to main at phase completion via PR).

**The reshape (vs the inspection vertical).** Inspection was **S4-only** — S2 + S3 already had everything (S3 emits inspection recipes; the bridge/executor/store/finalize were the gap). CRUD is **cross-substrate**: it needs S2 (the recipe-model expect-rejection, for the negative), S3 (data-recipe emission — none exists today), and S4 (the data executor + provisioning + cleanup + result-model extension). Sequencing across substrates is the first decision.

**Landscape (grounded read-only):**
- **S2 — ready, with one gap.** `DataRecipeBody` is fully designed (6 steps: create/read/update/delete/assert/apex; `api_choice` rest/bulk/composite; `identity_context`; `execution_mechanism`); `DataMutationTriggerBody` is ready. **Gap:** the `AssertStep` uses the same `AssertionPredicate` as inspection (`exists`/`equals`/…) — there is **no expect-rejection step**, so the behavioral negative (D-100.2) has no recipe-model expression.
- **S3 — emits inspection only.** Both `EMITTABLE` paths author `metadata-recipe`/`inspection-trigger`; **no data-recipe is emitted today**. CRUD input requires new S3 emission (positive data-recipe, and/or the D-100.2 behavioral negative).
- **S4 lift surface (F1) — small.** `SalesforceExecutionClient` (v1 data REST: create/update/delete/query/get/convert → normalized envelope, built from instance_url/api_version/token, no DB) is pure transport — the data-REST analog of slice-2's `ToolingReadClient`; cleanly liftable or rebuilt thin. `classify_sf_exception` already neutral.
- **Provisioning + cleanup (F6) — split.** Pure/liftable: `data_engine.generate_value` (factories, no DB), `cleanup.classify_failure` + `_build_deletion_order` (reverse-order children-then-parents), the `PQA_%` emergency sweep, multi-pass dependency retry. v1-welded: the `DataTemplate`/`DataFactory`/`RunCreatedEntity`/`RunCleanupAttempt` tables + repos (FKs to tenants/users/test_case_versions/pipeline_runs). Confirms F1's "reuse the mechanism, own the created-record tracking (re-keyed)" — S4 needs a new tracking table.
- **D-100.2 behavioral negative — designed, not expressible.** `ProhibitionClaimBody` carries `expected_rejection: RejectionSignal`; the recipe has no expect-rejection step. Gaps: (a) S2 model (a recipe-level expect-rejection — new step kind or a flag on mutation steps); (b) S3 emission (`_author_negative` emits inspection today, not a behavioral mutation); (c) S4 expect-rejection eval (a mutation that *should* fail → `passed`).
- **Executor extensibility.** Plan gains mutation steps; executor dispatches create/update/delete via a data client + captures before/after state (the `evidence.py` reserved N/As fill in here) + field diffs; assert eval reuses `exists`/`equals`; expect-rejection eval is new; cleanup (delete created records) is a new post-run phase inspection never needed.

**Open forks (leans noted, NOT locked — resolved next, into this PR):**
- **Fork A — polarity first.** The two polarities have *inverted* cost: the **behavioral negative** (a create a VR rejects → creates nothing → no provisioning/cleanup) is mechanically thinnest but scaffolding-heavy (S2 expect-rejection model + S3 emission + S4 eval — the D-100.2 differentiator); the **positive** (create→read→assert) reuses the assertion model but needs provisioning + cleanup.
- **Fork B — cross-substrate sequencing.** S3 emission leads, **or** S4 builds + tests against **seeded** data-recipes (the inspection precedent — Coordinator-authored recipes decoupled S4 from S3 emission timing). *Lean B (seed first).*
- **Fork C — provisioning/cleanup: lift-to-neutral now vs S4-own** (and how much of the v1 mechanism).
- **Fork D — data client: lift `SalesforceExecutionClient` vs build a thin S4 data client** (the `ToolingReadClient` precedent *leans build-thin*).
- **Fork E — result model: extend the `evidence` JSONB (reserved before/after N/As) vs trigger the A→B child table now** (CRUD's N-step shape is exactly the D-108.2 B-trigger).

**Guards.** This entry records the landscape + open forks; it locks nothing. Fork resolutions land as design amendments (the inspection-vertical pattern). No code yet.

---

## D-110 — S4 CRUD broadening: behavioral-negative-first programme

**Date:** 2026-05-27
**Substrates affected:** [S2] (recipe-model expect-rejection — the precursor); [S4] (the behavioral-negative vertical); [S3] (negative emission — last)
**Status:** Active — programme decision. Resolves the forks D-109 opened. Built on `phase-5-substrate-4-crud` (PR #5).

Resolves the open forks of D-109 (CRUD phase opening) and locks the broadening sequence.

**Cross-substrate (the headline reshape).** Unlike the inspection vertical (S4-only — S2 + S3 already had everything), CRUD spans **S2** (the expect-rejection model — the data-recipe can't express "this mutation should be rejected" today), **S3** (data-recipe emission — none exists), and **S4** (executor / provisioning / cleanup / result-model). The sequencing across substrates is the programme's spine.

**Fork A = negative-first (a create-rejected behavioral negative).** The differentiator — it verifies the constraint *actually enforces* (does the VR reject the violating mutation?), the question inspection cannot answer (inspection only re-confirms the VR `APPLIES_TO` exists). And it is **mechanically thinnest**: a rejected create **creates nothing** → no provisioning, no cleanup, no created-record tracking table. Positive CRUD follows as the **mechanical-completion** vertical (it needs provisioning + cleanup). The thinnest negative is **create-rejected**; **update/delete-rejected need a provisioned record first → deferred** with positive CRUD. The expect-rejection scaffolding is **D-100.2** (built eventually regardless of order).

**Fork B = seed-first.** S4 builds + tests against a **Coordinator-seeded** behavioral-negative recipe (the inspection precedent — seeding decoupled S4 from S3 emission timing). The **S2 expect-rejection model is the precursor**: you cannot seed a behavioral-negative recipe without a recipe-level way to express the expected rejection.

**Sequence: S2 → S4 → S3.** (1) S2 — the recipe-model expect-rejection representation. (2) S4 — the behavioral-negative vertical, against seeded recipes. (3) S3 — negative emission (`_author_negative` authors the behavioral recipe instead of/alongside the inspection one).

**Cascade (C / D / E from D-109):**
- **C — provisioning/cleanup: deferred.** The create-rejected negative provisions nothing and creates nothing, so F6 is not on the critical path; it lands with positive CRUD.
- **D — data client: build-thin.** A thin S4-local data-mutation client (create + the rejection envelope), per the slice-2 `ToolingReadClient` precedent (reject the S4→v1 inversion; defer the neutral lift).
- **E — result model: extend the `evidence` JSONB** to capture the rejection signal (the attempted mutation + the org's rejection — error code / message / field). The **A→B child table stays deferred** (a create-attempt + an expect-rejection assert is ~2 steps, not the N-step shape that is the D-108.2 B-trigger).

**Guards.** Programme decision only — locks the sequence + the cascade leans; the S2 expect-rejection representation itself is grounded + designed next (read-only first). No code yet.

---

## D-110.1 — S2 expect-rejection model (RejectionExpectation projection; flag on the mutation step)

**Date:** 2026-05-27
**Substrates affected:** [S2] (the recipe-model expect-rejection — the D-110 precursor)
**Status:** Active — S2 design (sub-decision of D-110). The first build step of the behavioral-negative programme.

The data-recipe cannot express "this mutation should be rejected" today (the `AssertStep` carries the same `AssertionPredicate` as inspection). This adds that, as the S2 precursor D-110 named (S2 → S4 → S3).

**Representation — a flag on the mutation step, not a separate step kind.** `expect_rejection` lives on the mutation step (`CreateStep` first). It is **intrinsic to the mutation's outcome** — self-describing ("this create should be rejected with X"), no look-ahead, the executor judges at the step. A separate `ExpectRejectionStep` would re-introduce a mutation↔expectation disconnect (the executor couldn't tell "expected rejection" from "broken recipe" until a later step) — the exact shape of the v1 `expect_fail` sin. The flag *carries the signal* (not a bare boolean), so it is grounded — that is what distinguishes it from the v1 sin, not the step-vs-flag choice.

**Operational primitive — `RejectionExpectation` (new), the semantic/operational boundary.** Scalars only: `error_code` / `error_message_pattern`, ≥1-required (same discipline as `RejectionSignal`), **no `IdentityBearingRef`**. This exists because operational-layer bodies forbid `IdentityBearingRef` (`_verify_no_identity_bearing_refs`, recursive), and the claim's `RejectionSignal.error_field` *is* one. So the two layers split cleanly: the **claim's `RejectionSignal`** (identity-bearing) is the *semantic* assertion; the **recipe's `RejectionExpectation`** is its *operational projection*. The constraint is the semantic/operational boundary asserting itself, not an obstacle. S3's `_author_negative` (later) authors both from one grounded source — the recipe projecting the claim (dropping / stringifying `error_field`); for the v1 VR negative both carry `error_code=FIELD_CUSTOM_VALIDATION_EXCEPTION` (the D-101.2 honest floor — no fabricated field/message).

**Versioning — additive v1.** Greenfield (no data-recipes emitted or persisted), so `expect_rejection` is an optional field on v1 — no `body_schema_version` bump, no migration, no compat surface.

**Invariant — at-most-one `expect_rejection` per recipe.** 0 (non-negative recipe) / 1 (behavioral negative) / ≥2 → reject. **At-most-one, not exactly-one** — exactly-one would forbid future positive data-recipes (which carry zero). One prohibition per recipe.

**Carry-forward (S4, later).** The executor attempts the create → a rejection matching `error_code` → **`passed`**; a success (no rejection) → **`failed`** (the prohibition did not enforce — the grounded analog of v1's `expected_fail_unverified`); an unexpected error → `failed` / `errored`. The S2 model carries exactly what that eval needs.

**Guards.** Additive v1; the projection (not reuse) keeps the operational layer free of `IdentityBearingRef`; at-most-one preserves room for positives; no Coordinator change (the registry + Pydantic discriminated union decode it).

---

## D-110.2 — S4 behavioral-negative vertical (grounded create-reject eval; parallel bridge/plan/executor; minimal-cleanup)

**Date:** 2026-05-27
**Substrates affected:** [S4] (the behavioral-negative data-recipe vertical)
**Status:** Active — S4 design (sub-decision of D-110). The second build step (S2 → **S4** → S3); executes a seeded behavioral-negative data-recipe (a create the org rejects).

The S4 half of D-110: run a `data-recipe` whose `CreateStep` carries `expect_rejection` (D-110.1) and verify the org rejects it as asserted. Mirrors the inspection vertical's S4 build, for a mutation-attempt + expect-rejection.

**Slice arc (leaner than inspection — store + finalize reused unchanged).**
1. **Bridge + plan** — `build_data_recipe_plan` → `DataRecipePlan` / `PlannedCreate`.
2. **Thin data client + executor + evidence** — `DataMutationClient`, `execute_data_recipe` (the grounded eval), `CreateAttemptEvidence`.
3. **Run-path dispatch + live test** — `run_recipe_execution` dispatches on `recipe_kind`; `finalize_run` reused.

**Parallel, not generalize (N-1 / N-2 / N-3).** New `build_data_recipe_plan` → new `DataRecipePlan` / `PlannedCreate`; new `execute_data_recipe`; the run path dispatches on `recipe_kind`. It **shares** the read-through-Coordinator pattern + the plan → execute → finalize spine, but **projects + evaluates a different shape** (create + expect-rejection, not read + assert). Parallel now; generalization deferred to recipe-kind-family growth (rule of three).

**Grounded eval — strictly stronger than v1's `expect_fail` sin.** The v1 sin: a bare boolean that flips *any* failure to passed, never checking *why*. The grounded eval, against the step's `RejectionExpectation`:
- create **rejected** AND `error_code` **matches** → **`passed`** (the prohibition enforced as asserted);
- create **succeeds** (2xx) → **`failed`** (the prohibition did **not** enforce — the grounded analog of v1's `expected_fail_unverified`);
- create **rejected but `error_code` doesn't match** → **`failed`** (rejected for the *wrong* reason — **the exact case v1 wrongly flips to passed**);
- create **couldn't be attempted** (auth / transport) → **`errored`**.
The **match-the-code** step is what makes it grounded. Match is robust to a **multi-error body** (match if *any* error's `errorCode` matches; optional `error_message_pattern` too); evidence captures the **full** error body.

**Thin data client (Fork D = build-thin).** `DataMutationClient` (create + delete), reusing `_oauth_token` + the neutral `integrations.exceptions` — the `ToolingReadClient` precedent; `resolve_data_mutation_client` (the `resolve_tooling_client` analog). `delete` exists **only** for the N-5 cleanup.

**Evidence (Fork E = extend JSONB).** A new `CreateAttemptEvidence` `StepEvidence` variant: the attempted mutation (sobject, field_values), the rejection captured (`error_code`, `message`, `http_status`, `matched: bool`, full body), cleanup (`attempted`, `succeeded`, `record_id`), timings; `before/after_state` + `field_diff` stay N/A. **No migration, no new persister** — it serializes via `persist_run_evidence`; the store + finalize are reused.

**N-5 — cleanup refinement (corrects D-110 Fork C).** "No cleanup (a rejected create creates nothing)" holds for the **passing** path. The **failing** path (create *succeeds*) DOES create a record (the envelope returns `record_id`) → a **targeted best-effort delete** of that one record (not the full F6 machinery). Best-effort: logged-not-fatal, the outcome stays `failed`, the evidence records the cleanup attempt. The negative vertical is **minimal-cleanup**, not zero. **F6** (full provisioning / cleanup with a tracking table) stays deferred.

**N-4 — live test.**
- **(c) stub-prove the four outcomes** — the eval logic, unit-tested against a stub client, regardless of live.
- **(d) live spine proof** — a **self-contained deterministic rejection** (a required-field miss → `REQUIRED_FIELD_MISSING`): reliable, no sandbox dependency, proves the whole spine end-to-end. Labeled a **mechanism proof**, distinct from the product use case (a VR firing).
- **(a) opportunistic VR probe** — a read-only sandbox probe for a firing VR; if found, a VR-specific live test (product-realistic `FIELD_CUSTOM_VALIDATION_EXCEPTION`); else the VR-specific live proof is **deferred** (the spine is already proven via (d)).
- Optional live cleanup proof (a controlled create-that-succeeds → targeted delete) — deferred / nice-to-have.

**Guards.** Parallel (not premature generalization); the grounded match-the-code eval (not the v1 bare flag); minimal-cleanup only (F6 deferred); the store + finalize reused (no migration). The seam is read-through-Coordinator, as inspection.

---

## D-110.3 — S3 emission: the third leg (use the parser's violating payload; behavioral replaces inspection; S3-thin-first)

**Date:** 2026-05-27
**Substrates affected:** [S3] (emits the behavioral negative); [S2]/[S4] (consume it — both built)
**Status:** Active — S3 design (sub-decision of D-110). Completes the S2 → S4 → **S3** sequence; lets a *generated* negative flow S3 → S2 → S4 end-to-end (replacing the manually-seeded recipe the S4 vertical already proves).

The S2 precursor (D-110.1) + the S4 vertical (D-110.2) are done. This is the S3 half: `_author_negative` authoring a **behavioral** data-recipe (a violating create + `expect_rejection`) instead of today's inspection re-verify.

**The parser is the foundation.** D-107's `derive(parse(formula))` already yields the **violating field assignment** (`VerifiedNegative.violating_payload = {field: value}`) for a formula subset (comparisons, AND/OR/NOT, ISBLANK/ISNULL, ISPICKVAL; bails → caveated on org-state / cross-object / field-to-field / non-numeric-ordering / NOT-ISBLANK / bare-field). Today it is computed inside `_formula_verifies` and **discarded** — only the boolean gate is used, under Option C, to protect the claim's `identity_hash`. The third leg **uses** that same payload (the hard semantic half is already built).

**Replace, not augment (the S3-recipe fork).** A **verified** negative emits the **behavioral** recipe (violating create + `expect_rejection`) *instead of* the inspection re-verify — behavioral subsumes structural for a verified negative (it tests that the VR *enforces*, not merely that it is *configured*). **Caveated** negatives stay inspection (the parser couldn't derive a violation, so there is nothing to construct). Single-recipe. **Augment** (emit *both* inspection + behavioral for one claim) needs an N-recipe `EmissionBundle` and is **deferred** — a future S6-disambiguation play, not v1.

**The projection (D-110.1) is trivial.** Both the claim's `RejectionSignal` and the recipe's `RejectionExpectation` carry `error_code = FIELD_CUSTOM_VALIDATION_EXCEPTION` (the VR-mechanism source — the D-101.2 honest floor); the recipe's drops `error_field`. One grounded source.

**⚑ The reshape (recorded).** The parser gives the violating *value*, not a valid *record*. A meaningful VR create must be **valid-except-the-violation** — else `REQUIRED_FIELD_MISSING` fires first and the VR is never reached. The required-field metadata **exists** (S1 `field_details.is_required` / `is_nillable` / `is_createable`); a **field-type → valid-value generator** is a small new build (call it **S3-A**; v1's `generate_value` is pure but factory-typed, not SF-field-typed, so it is new, not reuse). Deeper wall: a required **master-detail / required-lookup** field needs a **parent record** → **provisioning (F6, deferred)**. And the **necessity is empirical**: S4's `_matches` is multi-error-robust (matches if *any* error's code matches), so **if** Salesforce returns the VR code *alongside* `REQUIRED_FIELD_MISSING` on an incomplete create, the violating value alone suffices and S3-A is unnecessary — a live question, not derivable from the code.

**The plan: S3-thin first + a live experiment.**
- **S3-thin** — author the violating-value-**only** behavioral create (the parser's `violating_payload`, no required-field population). Smallest increment; proves the S3 → S2 → S4 emission flow.
- **The live experiment** (the deferred D-110.2 N-4 VR-specific live test) resolves the necessity question: does the sandbox's VR surface `FIELD_CUSTOM_VALIDATION_EXCEPTION` on an *incomplete* create? **Yes** → S3-thin is the working differentiator now (no S3-A needed). **No** → scope **S3-A** (required-field population, gated to objects without required relationships; required-lookup objects → F6) off the measured result.
- **Skip S3-B** (a required-field-omission "negative" doesn't fit the VR-formula grounding and is product-tautological).

**Guards.** The violating payload lives in the **recipe** (operational), never the claim — so `ProhibitionClaimBody` is byte-identical and the claim `identity_hash` stays stable (the Option-C invariant); only the *recipe's* identity is the new behavioral one (expected). Caveated path unchanged. No S2/S4 changes (both built). S3-A + augment + the master-detail/provisioning wall stay deferred.

---

## D-110.3 — Result (2026-05-27): S3-thin proven live; S3-A deferred-not-needed

The live necessity experiment ran (the deferred N-4 VR-specific live proof, now committed gated). **S3-thin is the live differentiator** for the common VR-enforced class:

- **Observed (raw org response).** A violating-value-only create on `CHANNEL_ORDERS__Service_Order__c` (`{CHANNEL_ORDERS__Partner_Contract_Rules__c: None}`, the D-107-derived payload of the `ISBLANK(...)` VR) → HTTP 400, **two errors, both `FIELD_CUSTOM_VALIDATION_EXCEPTION`** ("Contract is a required field." + "You must select an Order Type."), **zero `REQUIRED_FIELD_MISSING`**, no record created. The full S3→S2→S4 spine computed **`passed`** (`matched=True` via the multi-error match; no cleanup).
- **Why.** This object enforces required-ness via **validation rules** (returning `FIELD_CUSTOM_VALIDATION_EXCEPTION`), not platform required-field constraints — so the create trips the VR immediately. No short-circuit, no provisioning, no F6.
- **S3-A deferred-not-needed.** Required-field population is **not** on the critical path for VR-enforced prohibitions. It remains a narrow, deferred refinement only for a hypothetical object where a *platform*-required field blocks before any VR.

**⚑ Honest scope (recorded):**
- (a) Behavioral emission covers the **derivable-formula subset** (verified negatives); **caveated** negatives (org-state `PRIORVALUE`/`ISCHANGED`/`ISNEW`, cross-object dotted, field-to-field, non-numeric ordering, NOT-ISBLANK) **stay inspection** — widening the subset is the formula parser's future work (S1 §17).
- (b) The platform-required-field short-circuit is **inferred, not proven**: SF returned *multiple* validation errors in one body (it did not short-circuit on the first VR), which strongly implies it would surface a VR error *alongside* `REQUIRED_FIELD_MISSING` → the multi-error match still passes. Confirmable via a standard-object VR (a sandbox-content task).
- (c) The 5 derivable VRs in this sandbox are all **managed-package** rules; a standard-object **product-demo** VR is a separate sandbox-content task.

**Status: the behavioral-negative vertical (S2 → S4 → S3) is realized + live-proven end-to-end** with a real VR rejection. Open PR #5.

---

## D-111 — Substrate 6 (Observation & Interpretation): foundational design

**Date:** 2026-05-27
**Substrates affected:** [S6] (new — interprets S4's captured truth); [S4] (consumed, unchanged)
**Status:** Active — substrate opening. The boundary + deterministic-first are locked; slice 1 (Interpretation model + deterministic interpreter) is the first build. Architecture: `substrate_6_intelligence/SPEC.md`.

Opens **Substrate 6**, the interpretation layer: it takes S4's captured truth (a grounded run outcome + evidence) and produces a structured, QA-readable **interpretation** — what was tested, what happened, the semantic attribution. The v1 product moment (a release reviewer reads an answer) lives here.

**The boundary — S4 captures truth, S6 interprets it (LOCKED).** S4 executes, captures evidence, and renders the **grounded outcome** — which is S4's, final, and not S6's to recompute. S6 **consumes `evidence.outcome`** and explains it (classification / attribution / explanation / clustering); it **never executes, re-runs, or re-judges** the outcome. The signal S6 exists to surface is the combination S4 records but does not interpret: a **`verified` claim with a `failed` run** (well-grounded at generation, yet it didn't hold live). One-directional: `S4 RunEvidence → S6 Interpretation`.

**Deterministic-first (LOCKED).** The interpreter maps structured evidence → a structured `Interpretation` with **no LLM**. Attribution is *derived from the evidence*, never generated — S6 does not invent root causes. LLMs enter later, in **separate slices, for phrasing + clustering only** (reviewer prose; cross-run grouping) — additive presentation/aggregation over the deterministic core, never the attribution source. (The same capture-vs-interpret discipline S4 holds, one level up: the deterministic interpreter attributes; an LLM only phrases.)

**The `Interpretation` (structured, evidence-referencing).** Reviewable / editable / versionable (the S2-claim lifecycle discipline, one substrate over). Carries: identity/provenance (the `run_id` + claim/recipe refs); the **outcome carried verbatim** (restated, not recomputed); a semantic **verdict** (e.g. `prohibition_enforced` / `prohibition_not_enforced` / `rejected_unasserted_reason` / `not_evaluated` / `asserted_metadata_present`/`…_absent`); the **attribution** (what + why, derived from the evidence); and **supporting evidence refs** into the `RunEvidence` (auditable, not opaque).

**Slice arc.** (1) `Interpretation` model + deterministic `interpret_run` over both built verticals' outcomes — produce-only, no LLM (mirrors how the S4 executor started). (2) Deeper attribution (S1 cross-ref for a non-enforcing VR — inactive/misconfigured). (3) Clustering across runs. (4) LLM phrasing. (5) Interpretation persistence + the reviewer edit/version lifecycle.

**Gate (RunEvidence shape — verified).** Slice 1 consumes S4's `RunEvidence` verbatim: run-level (`run_id`, `recipe_id`/`recipe_version_seq`, `claim_test_id`/`claim_version_seq`, `environment_id`, `outcome` ∈ {passed, failed, errored}, `error`); `StepEvidence` variants (`ReadEvidence` + `AssertEvidence` for inspection — `held`, `row_count`, `subject_external_id`; `CreateAttemptEvidence` for behavioral — `success`, `matched`, `error_code`, `message`, `rejection_body` full body, `http_status`, `cleanup`). The four behavioral verdicts disambiguate from those fields without S6 re-judging.

**Guards.** S6 never recomputes the outcome (S4 owns it). Deterministic attribution only in slice 1 (no LLM). Produce-only (persistence is a later slice). The interpreter reads the *real* `RunEvidence` shape, not a parallel copy.

---

## D-111.1 — S6 deeper attribution: the differentiating *why* for failed behavioral verdicts

**Date:** 2026-05-27
**Substrates affected:** [S6] (the enrichment step); [S1] (read through its query interface, possibly extended)
**Status:** Active — S6 slice-2 design (sub-decision of D-111). Deterministic deeper attribution; no LLM.

Slice 1's `interpret_run` produces the basic `Interpretation` (verdict + evidence-derived attribution). Slice 2 deepens the **why** for the two *failed* behavioral verdicts, deterministically, by reading S1's validation-rule metadata.

**The seam — enrichment, not re-judgment.** `interpret_run` (slice 1) stays **pure** (DB-free). A new `attribute_run(interpretation, evidence, *, s1) → Interpretation` enriches **only** `prohibition_not_enforced` + `rejected_unasserted_reason` (pass-through for `prohibition_enforced`, the inspection verdicts, and `not_evaluated`). It reads S1 **read-only** and **never re-judges the outcome** — it deepens `attribution` and attaches a structured `cause`; the S4-owned outcome is untouched.

**Structured `cause` (S6-2).** An optional frozen field on `Interpretation`: a `cause_kind` literal (`vr_inactive` / `vr_formula_drift` / `enforcement_gap` / `other_vr_fired` / `platform_constraint`) + the supporting VR reference. `attribution` stays prose; `cause` is the machine-structured companion (so the interpretation is both reviewer-readable and queryable/clusterable downstream).

**`prohibition_not_enforced` (create succeeded — the defect) → three deterministic distinctions:**
- **(a) `vr_inactive`** — `is_active = False` on the grounding VR. It's disabled; it didn't enforce.
- **(b) `vr_formula_drift`** — re-derive the violating payload via the D-107 parser from the VR's *current* `formula_text`; if it differs from the create's `field_values`, the VR was edited since generation (the stored payload no longer matches the current condition). Reliable given the deterministic parser. **Full payload-vs-formula *evaluation* (does this arbitrary assignment satisfy this formula) is deferred (S6-1)** — it needs an evaluator the parser doesn't have; the re-derivation drift-signal covers the common case.
- **(c) `enforcement_gap`** — VR active + the payload still violates the current formula + the create *succeeded*. The genuine, highest-signal defect: the rule should have blocked it and didn't.

**`rejected_unasserted_reason` (rejected, wrong code) → which constraint fired:**
- Map each `rejection_body` **message** to S1's per-VR `error_message` → `other_vr_fired` (a *different* validation rule blocked it; cite the VR).
- A non-VR code (`REQUIRED_FIELD_MISSING`, `DUPLICATE_VALUE`, …) → `platform_constraint` (a platform rule, not a configurable VR).

**S6→S1 read boundary (S6-3).** S6 reads S1's VR metadata **through S1's query interface** (S1 owns its read API), **not** a raw S6-local query over S1's tables — the inter-substrate read-through pattern (mirroring S4→S2 via the Coordinator). The narrow `S1VrReader` port (`vrs_for_object(external_id) → (VrMeta…)`, `VrMeta = {name, is_active, formula_text, error_message}`) is a **test port**; production delegates to S1's query interface, **extending S1's read API** if it doesn't already expose the VR attributes. (The S4-local SF-client precedent is *external* org access — not an inter-substrate read — so it does not apply here.)

**Split (S6-4).** **2a** — model `cause` + the `S1VrReader` port + `attribute_run`'s (a)/(b)/(c) + other-VR/platform classification, driven by a **stub** `S1VrReader` (offline, no real S1). **2b** — the production `S1VrReader` through S1's query interface (+ extend S1's read API if needed) + an integration test. 2a is offline/safe; 2b touches S1 (integration → HOLD-and-show).

**Guards.** Enrichment never mutates the carried outcome (S4 owns it). Deterministic — no LLM (the D-111 invariant). The S6→S1 read goes through S1's interface, not raw table access. Full formula-evaluation (S6-1) deferred.

---

## D-111.2 — S6 interpret stage realized in the run-path: eager, best-effort, persisted

**Date:** 2026-05-31
**Substrates affected:** [S6] (the interpret stage in the run-path + the persistence store); [S4] (the run-path wiring point — after `finalize_run`); [S1] (read through its query interface, at run time)
**Status:** Active — S6 slice (sub-decision of D-111). Realizes the value-chain's *interpret* stage in the run-path. Resolves the eager-interpretation temporal assumption (DEFERRED item 1) + introduces the interpretation store (DEFERRED item 2).

Slices 1 (`interpret_run`) + 2 (`attribute_run` + the production `S1ValidationRuleReader`) were **produce-only** — nothing called them in the run-path, and a run yielded no *durable* interpretation. This slice wires the interpret stage into the run-path: a run now produces **and persists** its `Interpretation`, eagerly at run time.

**Eager, contemporaneous (A = full).** Immediately after `finalize_run` (which captured + persisted the run truth and reported posture), the run-path runs the full S6 step on the **in-memory** `evidence`: `interpret_run(evidence)` → `attribute_run(interp, evidence, s1=…)`. Eager run-time interpretation means S1-at-interpretation ≈ S1-at-execution — the production reader reads `current_version_seq()` (no pin), so the deeper attribution (`vr_inactive` / `vr_formula_drift` / `enforcement_gap`) reflects the org model the run executed against, not a possibly-later one. `attribute_run` self-limits the S1 read to the two failed behavioral verdicts (pass-through — no S1 query — for inspection + passed runs), so the reader is constructed always but queries only when a failed behavioral verdict needs it. This is a local DB read + a local insert added inside transaction boundary A — no new live/external call, so the held-open window does not meaningfully widen.

**Best-effort isolation (B) — evidence-first.** The whole S6 step (`interpret_run` → `attribute_run` → `persist_interpretation`) is wrapped in a `session.begin_nested()` **savepoint** + a `try/except`. Any failure — an S1-read SELECT error, a persist flush violation, or an interpreter defect — rolls back to the savepoint; the run truth `finalize_run` already flushed (the `s4_execution_runs` row + the S2 posture callback) **survives**, and the outer transaction still commits. The failure is logged loudly, and `RunPathResult.interpretation` stays `None` (observable, not swallowed). The principle: S4 captures truth and truth-capture is sacred — a *softer* S6 interpretation failure must never roll back captured truth. (Re-interpretation later is possible: the evidence is durably persisted.) This is the `enrichment.py` story-view precedent ("never raises") given a transaction-safe form via the savepoint.

**Persistence (C = mirror-S4).** A new per-tenant table `s6_interpretations` — migration `20260527_0020_s6_interpretations` (`down_revision='20260527_0010'`, the current tenant-branch head). The precise mirror of the S4 result store (`s4_execution_runs` / `persist_run_evidence`): typed identity/semantic columns that are the queryable axes — `run_id` (UUID PK), `recipe_id`, `claim_test_id`, `outcome` (reused `run_outcome` enum, carried verbatim), `verdict` — plus a `detail` JSONB carrying the rich part (`attribution`, `evidence_refs`, `cause`). Per-tenant schema, **no `tenant_id`** (isolation by schema, the substrate convention). `persist_interpretation(session, interpretation)` = `add` + `flush`, **no commit** (the caller owns the transaction — mirrors `persist_run_evidence`). Following S4's "don't over-index until a query pattern emerges" discipline, `cause_kind` / `vr_name` stay in the JSONB and **promote to typed columns when cause-clustering lands** (the A→B forward-migration idiom; DEFERRED item 2's clustering layer is the trigger).

**RunPathResult (D).** Additive field `interpretation: Optional[Interpretation] = None` on `RunPathResult` — set to the produced interpretation on success, left `None` when the best-effort step failed (so a caller can observe both the run truth and whether interpretation succeeded). Low-risk, purely additive to the result shape.

**Guards.** The outcome is carried verbatim into the `Interpretation` (S4 owns it; S6 restates, never recomputes — the D-111 invariant). Deterministic — no LLM. The run-time S1 read goes through `SemanticOrgModel` (the S6-3 read-through), not raw table access. Best-effort: a S6 failure never rolls back the run truth, never raises out of the run-path, and is always logged + observable via the `None` interpretation. Persistence joins the caller's transaction (flush, no commit — boundary A).

---

## D-112 — Substrate 8 (Evolution Engine) opened: grounding continuity under identity preservation

**Date:** 2026-05-31
**Substrates affected:** [S8] (opened — the evolution faculty); [S1] (entity-resolution, read through its query interface); [S3] (`derive`/D-107 + admissibility, re-consumed); [S6] (parallel sibling — boundary drawn, no dependency)
**Status:** Active — S8 foundational opening. Faculty-first: the grounding-validity predicate is the semantic core; evolution *mechanics* explicitly deferred. SPEC: `docs/architecture/substrate_8_evolution/SPEC.md`.

Substrate 8 opened — the **evolution layer**: it governs whether a test remains *meaningfully true* as the org evolves. Faculty-first (mirroring D-111): the semantic core (the grounding-validity predicate + the supersession law) opens; the evolution *mechanics* (manifests, triggers, reverse-index, auto-rerun) are explicitly fenced. This entry is the SEMANTIC opening — not mutation mechanics, which are the deliberately-avoided regeneration-infrastructure local maximum.

**Keystone — evolution is a grounding-axis event, never an identity-axis event.** A claim's `identity_hash` is org-independent *by construction*: `IdentityBearingRef` canonicalizes to `{entity_id, entity_type}` only — `version_seq` + `external_id` are stripped (the C0/C1 invariants, `canonicalization.py`). A rename / formula-edit / re-sync **cannot** change identity. So org evolution never moves the identity axis; it moves the **grounding** axis. S8 governs grounding continuity **under identity preservation** — it never changes what a test means (the S2 constitution already mandates identity-preserving-versions-only, S2 SPEC §7). One-sentence form: **S8 re-asks generation's grounding questions against the current org.**

**The semantic core — the grounding-validity predicate.** A deterministic **pure function** `(artifact, current org) → intact / drifted / broken`, **on-demand + stateless** (derived from the refs already embedded per-artifact; no standing manifest/verdict — mirrors S6's `attribute_run` shape). It is *generation's own grounding checks re-asked*; its legs are generation's grounding faculties — an **initial, extensible** set: **claim-grounding** (subject resolves? → S1 entity-resolution), **recipe-grounding** (payload still violates? → S3 `derive`/D-107), **admissibility** (LAYER_1↔LAYER_2 → S3 admissibility — closes G3, making the caveat a re-evaluable function of `(claim, current org)` rather than a frozen emission-time snapshot). The set grows (a likely fourth **field-value-validity** leg for the picklist-removed case — the payload's value no longer *exists*, distinct from no-longer-*violates*); the full taxonomy→leg mapping is a later pass. **Two-level (Fork C):** claim- and recipe-grounding consume different org facts and drift independently (one claim, many recipes) — composed, never collapsed.

**The dependency law — parallel to S6, never S6 → S8 (R1 + R2).** `S8 → {S1 entity-resolution, S3 derive (D-107), S3 admissibility}` — the foundational faculties generation itself used, re-asked. The decisive reason is **epistemic**: S6's drift attribution (`_attribute_not_enforced(create: CreateAttemptEvidence, vrs)`) judges with the **actual run evidence in hand** (real `field_values`, observed success) on top of the re-derivation; S8's predicate is **pre-run, derivation-only**. S6 holds strictly better ground for "did the payload violate" — a stronger-evidence faculty must never route through a weaker one. **Parallel because of different evidence bases, not arrow direction.** (The narrow-vs-broad-verdict coupling stands alongside, secondary.) The boundary is **not** rested on the value-chain-grain argument — the platform vision is ambiguous there (graph draws S8→S6; prose lists S6's deps as S4+S1). **Code-confirmed:** `derive` (D-107) has two importers today — `emission.py` (S3, grounding-time) + `attribution.py` (S6, post-run); S8 is the **third sibling** on the same primitive (parallel-consumer structure already in the tree, not aspirational).

**The supersession law — re-grounding as identity-preserving supersession.** When grounding drifts and is repaired (the mechanics phase), the result is a **new version with the *same* `identity_hash`**, on the existing lineage spine (`test_id` + monotonic `version_seq` + `valid_to IS NULL`), stamped with the provenance already reserved (`recipe_s8_rewrite` event_kind / `'s8'` actor; `grounding_evolution` regeneration_kind). Meaning is the invariant; grounding evolves — **governed semantic evolution**. A genuinely different subject is a *new* claim (S3-authored), not an evolution.

**Deferred — evolution mechanics (the fence).** Explicitly out of the semantic core: the standing dependency manifest (Fork A), the change→impact reverse index (G5), the S1-sync trigger (Fork E), the standing recorded verdict (Fork B), the coverage-version gap (G4), and the one plausible S8→S6 edge (a *drift-trigger signal*, "keeps drifting across runs → re-evaluate"). All **impact/trigger machinery** — the regeneration-infrastructure local maximum deliberately not built at the opening. The semantic core takes nothing from S6 and needs no standing state.

**Guards.** S8 never changes a test's meaning (identity preserved — the keystone). The predicate is deterministic + pure (no LLM, no standing state). The S1 read goes through S1's query interface (the read-through pattern), not raw tables. S8 is parallel to S6 (the epistemic boundary), and re-consumes generation's faculties (S1 resolution / S3 derive / S3 admissibility) — never re-implementing them. Mechanics are fenced (§6) and not in the opening.

---

## D-113 — S8 recipe-grounding leg: the `evaluate` primitive + the object-level grounding-validity verdict

**Date:** 2026-05-31
**Substrates affected:** [S8] (the recipe-grounding leg — the first grounding-validity leg); [S1-semantic] (a new neutral `formula.evaluate` primitive in `primeqa/semantic/formula/`); [S2] (reads the recipe's stored payload); [S6] (a flagged follow-up only — not touched)
**Status:** Active — S8 slice 1 (sub-decision of D-112). Faculty-first, produce-only, recipe-grounding axis only. Refines D-112's dependency line (§3) + SPEC §2/§3.

The first leg of the grounding-validity predicate (D-112 SPEC §2): **does a behavioral-negative recipe's stored payload still violate the current validation-rule formula?** Built as a pure, object-level function over a new evaluation primitive. Forks resolved: F1 extend · F1b neutral package · F2 broken-holding+reason · F3 align-S6-separately.

**The `evaluate` primitive (F1 extend · F1b neutral package).** D-107 had only `derive` (formula → a *violating assignment*; a constraint solver) — there was **no** `evaluate` (formula + payload → *fires?*). The precise leg requires evaluation, so a new `evaluate(formula_ast, payload) → True | False | NonEvaluable(reason)` lands in the **neutral formula package** (`primeqa/semantic/formula/eval.py`), beside `parse` / `walk` / `nodes` — a pure formula-semantics primitive, not in S3. It is parser-shaped over the same single-object create-time subset `derive` solves (comparisons, AND/OR/NOT, ISBLANK/ISNULL/ISPICKVAL); anything outside (org-state funcs / cross-object dotted refs / NotParsed / type-uncertain / non-numeric ordering) yields `NonEvaluable(reason)`, mirroring `derive`'s `_Undecidable → NotDerivable` boundary. **Three-valued (Kleene):** the boolean connectives combine so a *determinable* result resolves past a non-evaluable sibling (`True OR NonEvaluable` = `True`; `False AND NonEvaluable` = `False`) — strictly more precise than bailing on the first non-evaluable node, and what lets the leg answer "still violates?" for a formula mixing evaluable + org-state clauses. Pure computation — no satisfiability/merge logic.

**The recipe-grounding leg (object-level, evaluate-based).** A pure function (`primeqa/evolution/recipe_grounding.py`) reading the stored payload from S2 (`CreateStep.field_values` + `target_object.external_id`) and the current **active** VR formula(s) from S1 via a VR-read port (the `APPLIES_TO` path). Verdicts: **intact** = ≥1 active VR's current formula evaluates `True` (still rejected); **drifted** = none triggered but ≥1 active *evaluable* VR exists (re-groundable); **broken** (reason-tagged, **load-bearing**) = no active VR triggered and no re-groundable foundation — `no_active_vr` (none active) or `formula_non_evaluable` (active VR(s) present, none evaluable).

**Refinements to D-112 (recorded in SPEC §2/§3):**
- **Dependency** — recipe-grounding → the **neutral `formula.evaluate`** primitive, *not* `S3 derive` (D-112's line was approximate: `derive` solves, the leg evaluates). The parallel-siblings law holds and is cleaner — a neutral primitive S6 and S8 consume independently; **S6 ↛ S8**.
- **Object-level bound** — the recipe pins no VR (only the generic `FIELD_CUSTOM_VALIDATION_EXCEPTION`), so the verdict is *behavioral* ("rejected by any active VR"), not claim-specific. An `intact` can mask a specific-VR loss when another active VR catches the payload. A generation-side **VR-pin** is the named deferred sharpening.
- **Non-evaluable split** — `broken/formula_non_evaluable` is **structural only** (the formula left the single-object subset). Field/object-gone → **claim-grounding** (schema resolution, a later leg); picklist-value-removed → **field-value-validity** (a later leg; a known **false-`intact`** here — the formula still evaluates `True` on the now-invalid value).
- **Deactivation verdict** — corrects SPEC §2's flat "deactivated ⇒ broken": deactivation is `broken` **only when nothing else active catches the payload**; if another active VR still rejects it, the verdict is `intact`.

**S6 (F3) — flagged, not in this slice.** S6's `_attribute_not_enforced` still uses the `derive` + subset-compare **proxy**, which mis-reports a *loosened-but-still-violating* formula as `vr_formula_drift` (a false drift; it is really an `enforcement_gap`). Aligning it to consume the shared `evaluate` removes that imprecision and the duplicated proxy — **boundary-clean** (S6 consumes the shared neutral primitive, not S8's verdict). It touches S6's tested code, so it is a **separate small follow-up**.

**Guards.** Produce-only (a pure verdict; nothing wired, no persistence). `evaluate` never raises on a recognized AST. The leg consumes the neutral `evaluate` + reads S1 through its own duck-typed port (S6's `VrMeta` satisfies it, but S8 does **not** import S6 — parallel-siblings). Recipe-grounding axis ONLY — claim-grounding / admissibility / field-value-validity are later legs, and their territory is kept clean (the non-evaluable split + the picklist false-`intact` are named, not silently absorbed).

---

## D-114 — F3 resolved: S6 attribution on the shared `evaluate` (3-way) + the `vr_formula_indeterminate` / `no_active_vr` causes

**Date:** 2026-05-31
**Substrates affected:** [S6] (`_attribute_not_enforced` rewrite + two new causes); [S1-semantic] (S6 consumes the neutral `formula.evaluate`); [S3] (S6 drops its `verified_negative.derive` dependency)
**Status:** Active — resolves the F3 follow-up flagged in D-113 §3. Localized: no migration (causes ride `s6_interpretations.detail` JSONB), no exhaustive `CauseKind` consumer (`_prose` formats generically).

D-113 §3 flagged S6's `_attribute_not_enforced` as still using the `derive` + subset-match **proxy** — which mis-reports a *loosened-but-still-violating* formula as `vr_formula_drift` (the proxy re-derives the canonical violating value `199` from a current `Amount < 200`, misses the subset-match against the stored `99`, and calls it drift — when `99` still violates `< 200`, so it is really an `enforcement_gap`). This resolves it: S6 now consumes the neutral `formula.evaluate` (D-113) — three-valued — and its over-broad drift bucket is split precisely.

**The rewrite (3-way, 5-precedence).** Per active/inactive VR: `evaluate(parse(current_formula), create.field_values) → True | False | NonEvaluable`. Precedence:
1. any **active** VR violated (`True`) → `enforcement_gap` — **closes the loosened-still-violating false-drift**.
2. an **inactive** VR violated → `vr_inactive`.
3. any **NonEvaluable**, nothing violated → **`vr_formula_indeterminate`** (new) — **closes the old `NotDerivable → drift` collapse**: a current formula that left the single-object subset (org-state / unset fields) is *indeterminate*, not a guessed drift. Ahead of drift (don't guess). Carries the VR.
4. an **active** VR evaluable + not violated (`False`) → `vr_formula_drift` — now *confirmed* drift (the rule was edited so the payload no longer trips it).
5. else → **`no_active_vr`** (new) — the residual: no active VR enforces (removed / deactivated, no matching inactive rule). **The old code mis-labeled this as drift; closed here** — matching S8's `no_active_vr` reason (parallel verdict vocabulary across the two faculties). (An inactive VR that is *not* violated gets no bucket — an inactive rule does not enforce.)

**Two new causes (`model.py`).** `vr_formula_indeterminate` + `no_active_vr` added to the `CauseKind` Literal. Same localized profile as D-113's reason tags: no migration (cause rides `s6_interpretations.detail` JSONB), no exhaustive consumer.

**Boundary consolidation.** S6 now stands on the neutral `evaluate`, **off S3's `derive`** (the `verified_negative` import is removed; the `_payload_violates` proxy deleted). `derive` returns to S3-internal (`emission.py`); the parallel-siblings law (D-112) is realized **on `evaluate`** — its two consumers are S6 `attribution.py` (post-run) + S8 `recipe_grounding.py` (pre-run). **S6 ↛ S8** (both on the neutral primitive, never each other's verdict).

**Tests.** Preserved: `vr_inactive`, `enforcement_gap`, the `_attribute_unasserted` set (`other_vr_fired` / `platform_constraint`), passthroughs. **Adjusted** `vr_formula_drift`: its proxy-era absent-field formula (`Amount__c = 99` on `{Reason__c:None}`) is `NonEvaluable` under `evaluate` (not confirmed-`False`); swapped to a genuinely-evaluable-`False` `ISPICKVAL` so it tests *confirmed* drift. **Added**: `enforcement_gap_loosened_still_violating`, `vr_formula_indeterminate`, `no_active_vr`.

**Flagged next — S6↔S8 NonEvaluable symmetry.** S8's recipe-grounding leg (D-113 slice 1) does `≥1 False → drifted`, *skipping* `NonEvaluable` — so a `False` + `NonEvaluable` mix → `drifted`. S6's conservative line (this decision) puts any `NonEvaluable` ahead of drift (→ indeterminate). For symmetry, S8 should match — `NonEvaluable` present + nothing violated → `broken/formula_non_evaluable` ahead of `drifted`. A focused symmetry pass, **grounded on frequency first** (measure how often the mix actually occurs before touching the tested leg). Not in this slice.

**Guards.** Deterministic, no LLM. S6 consumes the neutral `evaluate` only (S6 ↛ S8). The outcome is still carried verbatim (S6 never re-judges). The drift bucket is now precise — it no longer absorbs `enforcement_gap` (violated), `vr_formula_indeterminate` (non-evaluable), or `no_active_vr` (no enforcer) as guesses.

---

## D-115 — S4 Slice 1: positive create-and-verify (directly-set state)

**Date:** 2026-06-01
**Substrates affected:** [S4] (the first positive data-execution vertical — the first true *semantic-execution* slice); [S3] (`GroundedPositive` + value-claim emission); [S2] (`value-claim` + Create/Read/Assert-equals — already modeled, no change); [S1] (read: object requiredness + field grounding)
**Status:** Active — S4 Slice 1 design. **Design-only; no impl.** Full design: `docs/architecture/substrate_4_execution/SLICE_1_POSITIVE_CREATE_VERIFY_DESIGN.md`.

The first vertical where the weight is on **constructing a valid operational world** on the live org + **policing the S3/S4 boundary**, not the create call. It verifies that a requirement's stated field value is *operationally achievable* and *persists* on the current org (catching VR / FLS / type conflicts at execution time), and it lays the **positive execution spine** that automation-effect positives reuse later.

**Governing boundary (k16, TA-locked) — operational validity, never semantic meaning.** S4 resolves operational validity against the live org but never changes the recipe's semantic meaning, **enforced structurally**: (1) the recipe carries the semantic field-value (the claim's, S3-set) + the target object; (2) **S4's writable set = (the object's required fields) − (the semantic fields)** — S4 fills only that *operational padding* with valid filler (validity checked vs. the live org), and the semantic field-under-test is recipe-set and **never enters S4's writable set**, so S4 *structurally cannot choose the value under test*; (3) grounding compares observed vs. the claim's targets **verbatim** (carried, not recomputed), so S4 cannot reinterpret or soften the verification. Both halves — what is set and what counts as verified — are closed by construction, not by discipline.

**Value-sourcing (resolved seam).** The value-claim's `expected_value` is **requirement-sourced** (carried from synthesis); `_author_positive` threads the one value into both the `CreateStep` (`field = V`) and the `AssertStep` (`field == V`). **S3 never fabricates a value** — no stated value → no value-claim grounds (stays `EMISSION_DEFERRED`). No representative / invented values. (Contrast the negative, which *derives* its value via D-107; the positive is *given* one or does not emit.)

**Scope fence (directly-set-state only).** IN: create, read back, ground the observed *directly-set* value vs. the claim. OUT (deferred): automation effects / branch-sensitive flows / async observation / entanglement detection (**k8**); prerequisite-parent construction — **no required lookups** (scalars / simple-picklist padding only); complex / async teardown; multi-step composition (**k15**).

**Two-sided build.** **(A) S3:** a `GroundedPositive` (object + field + sourced value) grounded on S1; `_author_positive` emits a value-claim + `CreateStep` (**no `expect_rejection`**) → `ReadStep` → `AssertStep(equals)`; `EMITTABLE += ("data_behavior", "value-claim")`; `author_emission` dispatch + governance stash. The S2 model needs no change. **(B) S4:** construct the operational world (S1 requiredness → padding fill) → create-expect-success → **observe as a distinct phase** (async-ready; *no immediate-consistency assumption baked into finalization / grounding*) → ground `field == V` → structured-trace evidence → teardown framed as **execution-isolation (k14)**.

**Closes generate → run.** For the first time a *positive data recipe* flows the whole chain — synthesis → S3 emission → live S4 execution → grounded verification — end to end; S4 executes a genuinely S3-emitted positive recipe.

**Guards.** Structural boundary (the writable-set difference + the verbatim-carried assertion target) is the enforcement, not reviewer trust. S3 never invents values. Observation is a distinct, async-ready phase (no immediate-consistency assumption). Scope-fenced to directly-set state; k8 / k15 / required-lookup parents / complex teardown deferred. Design-only — no impl until GO.

---

## D-115.1 — Slice 1 side A realized: positive emission-authoring + the EMITTABLE-deferral (Option Q)

**Date:** 2026-06-01
**Substrates affected:** [S3] (positive value-claim emission-authoring); [S4] (consumes the recipe in side B); [S2] (no change — the model already sufficed)
**Status:** Active — D-115 slice 1 side A realized (the author-capability half). On `phase-6-substrate-4-positive`. The governance grounding stash is held (the next S3-grounding decision).

Side A of D-115's two-sided build — the **emission-authoring half**, the recipe S4 side B executes — is realized. `GroundedPositive` (target object + field + value; the value carried **verbatim** from the value-claim's `expected_value`) + `_author_positive` (a `ValueClaimBody` + a data recipe `CreateStep(field_values={field: V})` no-`expect_rejection` → `ReadStep` → `AssertStep(equals, field == V)`) + the `author_emission` dispatch + a dormant `finalize_outcome` read of `grounded_positive`. The CreateStep carries the **semantic field only** (k16 — S4 pads required fields at execution). The S2 model needed no change (`ValueClaimBody`, `CreateStep`/`ReadStep`/`AssertStep`, the `equals` predicate all already existed). Tested via a directly-constructed `GroundedPositive`, mirroring how the negative emission is tested.

**Option Q — `EMITTABLE += value-claim` deferred to land with the held grounding stash.** A side-A planning pass surfaced a coupling the side-A spec missed: `EMITTABLE` drives `resolve_intent`'s **proceed-gate** (`governance_core.py`), not only `author_emission`'s dispatch. Adding `("data_behavior", "value-claim")` to `EMITTABLE` *without* a grounding stash would flip a real grounded value-claim from a graceful resolve-time `EMISSION_DEFERRED` to `PROCEED_TO_EMIT` → the EMIT phase — which (a) **crashes** the propose-only emission-deferred integration test (`FakeToolTurn` over-call: it feeds one propose turn, the runtime now needs an emit turn) and (b) burns an emit LLM call in production before `finalize_outcome` defers. So `EMITTABLE += value-claim` is **coupled to the grounding stash** and lands *with* it, not in the authoring half. The drift-guard (`test_emittable_set_matches_author_emission_shapes`) stays green **unchanged** — the extra `author_emission` `GroundedPositive` dispatch is invisible to it; and a real grounded value-claim keeps deferring `EMISSION_DEFERRED` gracefully at resolve (the existing test passes unmodified). Option P (decouple the resolve-gate from `EMITTABLE` now via two sets) was rejected as the broader expansion this slice is scoped against.

**Held — the governance grounding stash.** `resolve_intent` does not yet build a `GroundedPositive` from a real intent: today the value-claim grounding is **object-level** (it confirms the object *has* a field via `BELONGS_TO`) and the intent descriptor carries no specific field or value. Producing a field-and-value-specific `GroundedPositive` requires extending the **synthesis→intent contract** so synthesis threads `{field, expected_value}` to grounding. That contract decision — the production-reachability piece — is the next S3-grounding step, after the S4 execution spine (side B). Until it lands, value-claims stay `EMISSION_DEFERRED`.

**Guards.** Value carried verbatim (S3 never invents). The author-capability is independently testable + **dormant** from real intents (no production reach until the stash). The drift-guard + the emission-deferred behaviour are preserved unchanged.

---

## D-115.2 — Slice 1 side B: positive execution spine — read-resolution + 400-outcome seams resolved

**Date:** 2026-06-01
**Substrates affected:** [S4] (the positive execution spine — construct-world → create → observe → ground → teardown); [S1] (read: object requiredness + field types for the operational padding); [S3] (no change — side B consumes side A's recipe)
**Status:** Active — D-115 slice 1 side B. Design for the two seams the SLICE_1 design left "side B's to define"; impl follows on `phase-6-substrate-4-positive` (HOLD-and-show per commit).

The SLICE_1 design (D-115 §5(B)) named two mechanisms as side B's to define. Both are resolved here; the rest of side B is the §5(B) spine made concrete.

**(1) Read-resolution = SOQL substitution (not by-id retrieve).** Side A's `ReadStep` carries `soql = "SELECT {field} FROM {object} WHERE Id = '$create-record.id'"`. Side B *defines* `$create-record.id`: after the create succeeds the executor binds `state["create-record"] = {"id": <sf-id>}` and substitutes `$<step_id>.id` → the literal Id in the SOQL (`refs.resolve_step_refs`, **fail-loud** on an unresolved `$ref`), then issues the SOQL **verbatim** via a new data `query(soql)` (REST `/query`). Executing the authored SOQL as written honors the recipe and makes the substitution a real, reusable convention. By-id retrieve was rejected — it would leave the authored SOQL vestigial and *bypass* the convention rather than define it. (v1's `_resolve_soql_refs` is the reference pattern, substrate-owned, never imported.)

**(2) 400-rejection outcome = disambiguate by offending field.** A create the org rejects (HTTP 400 business rejection) is graded by *which field the org names*: the **semantic** field named → `failed` (the requirement's value is not operationally achievable — the slice's headline finding, §4); only S4's **padding** field(s) named, or none → `errored` (S4's own operational-world construction failed — not a verdict on the value under test). The split is **structural, not heuristic**: side A's `CreateStep` carries the semantic field *only* (k16), so the semantic set = the recipe create's `field_values` keys and the padding set = the executor-added filler keys are cleanly separable, and the executor tests the rejection body's named `fields` against each. The full rejection body is captured in evidence regardless (evidence-first; S6 attributes). Always-`failed` (symmetric with the negative) was rejected — a padding-caused rejection would post a false "value doesn't hold"; always-`errored` was rejected — it buries the real finding.

**The outcome grammar (the semantic core).** create-success + observed `== V` → `passed`; observed `≠ V` → `failed`; read returns 0 rows or transport-fails → `errored` (observation is a **distinct async-ready phase** — *no immediate-consistency assumption*, so a 0-row read is "couldn't observe," never silently "wrong value"); create transport-raise / non-400 (401/403/429/5xx) → `errored`; create-400 → the (2) disambiguation; operational world unfillable (a required lookup / unfillable type) → `errored` *pre-create*. **k14 teardown:** any 2xx create is **always** best-effort-deleted (leave the org as found) on every downstream path — never part of the verdict.

**Operational padding (k16 realized).** S4 reads the target object's fields from S1 (`field_details`: `is_nillable` / `is_required`, `field_type`, `references_object_entity_id`, `is_calculated`, `picklist_value_set_entity_id`) and fills the *required-writable-non-lookup-non-semantic* set with type-valid filler (simple-picklist via the value set's default / first-active value). The semantic field-under-test is **structurally excluded** from the writable set (k16) — there is no code path by which S4 writes the field it verifies. A required field S4 cannot fill (a lookup parent — the §3 scope fence — or an unknown type) makes the recipe unrunnable in this slice → `errored`, never a guessed value.

**No migration / no persister change.** `persist_run_evidence` is `dataclasses.asdict`-generic and the `run_outcome` enum already carries `passed/failed/errored/skipped`; `finalize_run` reports `evidence.outcome` verbatim. The positive vertical's evidence (a lean `DataReadEvidence` + reused `AssertEvidence` / `CreateAttemptEvidence`) serializes unchanged.

**Guards.** Read-resolution is a verbatim substitution, fail-loud on an unresolved ref (no silent ungrounded read). The 400-disambiguation is structural off the k16 field-set split, not a heuristic. The observation phase bakes in no immediate-consistency assumption. The padding filler never chooses the value under test (k16) and *refuses* (errored) rather than guessing when it cannot construct a valid world. Teardown is best-effort and never changes the verdict (k14).

---

## D-115.3 — Value-claim grounding stash: production-reachable positive (Option Q resolved)

**Date:** 2026-06-01
**Substrates affected:** [S3] (the governance grounding stash + `EMITTABLE` — the production-reachability seam); [S4] (consumes the emitted recipe — side B, no change); [S2] (no change — the model already sufficed)
**Status:** Active — D-115 slice 1 grounding stash. On `phase-6-substrate-4-positive` (same slice as A+B). Mechanism + tests; the LLM propose-guidance + the live eval probe are deferred (below).

The third piece of D-115's positive value-claim. Side A authored the emission (`_author_positive`), side B built the S4 execution spine — but `resolve_intent` could not build a `GroundedPositive` from a real intent, so no real requirement reached the positive path. This slice closes it: a field-and-value `GroundedPositive` is grounded + stashed during `resolve_intent`, and `EMITTABLE += ("data_behavior","value-claim")` opens the proceed-gate. The mechanism was pre-wired — `finalize_outcome` already reads `state.grounded_positive` and authors it; `target_subject_hint` is an open object that already accepts `field_name` / `expected_value` (exactly as the negative's `operation` rides it). **No S2 model change, no tools-schema change, no migration** (the `EMISSION_DEFERRED` enum shipped in `20260522_0040`).

**Option Q resolved (the coupled landing).** D-115.1 deferred `EMITTABLE += value-claim` because, alone, it flips the resolve-gate from `EMISSION_DEFERRED` → `PROCEED_TO_EMIT` — which routes to the emit phase and (a) crashes the propose-only emission-deferred test (`FakeToolTurn` over-call) and (b) burns an emit call in production. The resolution lands the gate-flip **in lockstep** with: the grounding stash (so a *gated* PROCEED always reaches an authorable finalize — the `test_every_emittable_pair_is_authorable` invariant), the drift-guard map (`_EMITTABLE_SHAPES += value-claim → GroundedPositive`, keeping `set(_EMITTABLE_SHAPES) == set(EMITTABLE)`), and the integration test (the incomplete-intent case re-aimed to the stash-level refuse).

**Verify-at-grounding (Q1).** A value-claim asserts `field == V`, so grounding must verify the **named** field exists. `_evaluate_positive` gains a `field_hint`: a value-claim grounds iff a Field whose `sf_api_name == field_hint` `BELONGS_TO` the object; an unknown named field (or none) → `insufficient_grounding` (mirrors the negative's "no constraint supports it"). Other positive claim_kinds keep the object-level any-field proxy (backward-compatible). The rejected alternative (ground object-level + catch in the stash) conflated "named field absent" with "no value" and left a grounded-then-refused candidate.

**The refusal taxonomy.** field exists + value carried → `PROCEED_TO_EMIT` (emits the value-claim + the create→read→assert recipe side A/B own); field exists, **no value** → `EMISSION_DEFERRED` (S3 never fabricates a value — D-115 §2; grounded-then-deferred at the stash); field **absent** / none named → `insufficient_grounding` → `UNGROUNDED_CLAIM`; object has no fields → `UNGROUNDED_CLAIM` (unchanged).

**Scope (Q2) — mechanism + tests; LLM-guidance + eval deferred.** This lands the governance mechanism + the unit/integration coverage. The positive is reachable the instant the LLM supplies `field_name` + `expected_value` in `target_subject_hint`; **telling** it to (the propose tool-description / prompt guidance) and a **live eval probe** confirming a real requirement emits end-to-end are a deferred follow-up (mirrors the negative — mechanism first, the verified-prohibition eval probe separate). Until then a complete value-claim is exercised by the unit/integration suites, not the live model.

**Guards.** Grounded ⟺ the named field exists. The value is carried verbatim from the intent — S3 never fabricates one (no value → deferred). The gate-flip lands only with the stash + the drift-guard, preserving "a gated PROCEED is always authorable." The propose-only test's incomplete-intent case stays `EMISSION_DEFERRED` (re-aimed to the stash-level refuse), keeping the enum round-trip coverage.

---

## D-115.4 — Value-claim live reach: prompt v2 guidance + eval probe

**Date:** 2026-06-01
**Substrates affected:** [S3] (the propose prompt + the eval corpus — the live reach); no governance / S2 change
**Status:** Active — D-115 slice 1 live reach. On `phase-6-substrate-4-positive`. Mechanism + offline probe are CI-gating; the live probe is authored + periodic (skipped without `ANTHROPIC_API_KEY`).

The last gap of D-115's positive value-claim. D-115.3 made it production-reachable — `resolve_intent` grounds + stashes a `GroundedPositive` and emits — **but only if the LLM supplies `field_name` + `expected_value`** in the propose intent's `target_subject_hint`, and the frozen prompt `generation@v1` never told it to. This slice closes the reach: a new frozen prompt version that guides the model + an eval-corpus probe.

**Prompt `generation@v2` (the freeze ritual).** Frozen prompt versions are immutable + SHA-256 hash-guarded (replay determinism, D-103.1), so the prompt change authors a **new version**, never edits v1. The working source — `base.md` (title) + `fragments/data_behavior.md` (the "Positives" bullet) — is edited, `compose_working()` freezes `versions/generation_v2.md`, the registry records its hash + bumps `CURRENT = "generation@v2"`. v1 stays frozen + valid (a pinned-v1 request still resolves it); v2 differs from v1 only in the value-claim guidance + the version title.

**Prompt-only, strict field matching (Q1).** S1 stores field API names **qualified** (`Account.Status`) and grounding does exact-match (`sf_api_name == field_hint`), so v2's "Positives" bullet instructs the LLM to supply `field_name` as the **fully-qualified `Object.Field`** name + `expected_value` verbatim, and — when the requirement states no concrete value — to **not invent one** (propose the Object-level claim, let the substrate defer; D-115 §2). The grounding is unchanged (no leniency); a bare unqualified field would miss, which the live probe is positioned to catch. (The rejected alternative — grounding leniency matching a bare `Status` against `Account.Status` — would have softened the strict verify-at-grounding precision.)

**Eval probe — offline + live (Q2), mirroring `verified-prohibition-negative`.** A `value-claim-positive-draft` corpus case carries both tiers: an **offline scripted probe** (a value-claim intent with a qualified `field_name` + value replayed through governance → `draft` / `value-claim` / `data-recipe`) — deterministic, CI-gating, no LLM; and a **`live` block** (a real requirement + semantic-envelope invariants) — the actual prompt-effect confirmation, periodic (skipped without `ANTHROPIC_API_KEY`). The offline tier guards the governance + emission chain in the canonical corpus; the live tier is the only thing that confirms the real model, given v2, emits a value-claim — and would surface a field-name-format miss.

**Scope honesty.** This lands the CI-verifiable mechanism (prompt freeze + the offline probe + the registry/hash guards). The live confirmation is **periodic, not gating** — the positive value-claim's end-to-end LLM reach is authored here but proven on a periodic live run, exactly as the verified-negative's live twin is.

**Guards.** v1 stays byte-frozen (hash guard); v2 self-describes its version + carries only the additive value-claim guidance. The grounding is untouched (strict). The offline probe is deterministic; the live probe never gates CI (it auto-fails only on a live run with a key).

---

## D-116 — S6 cross-run clustering (deterministic): cause / VR / flapping patterns

**Date:** 2026-06-01
**Substrates affected:** [S6] (the cross-run clustering layer — promote `cause_kind` / `vr_name` + a deterministic grouping service); no LLM, no v1, no other substrate
**Status:** Active — S6 phase-7 slice (clustering). On `phase-7-substrate-6-interpretation`. Pure substrate, deterministic; S6 stays write-only (the clusters are queryable, no consumer yet).

The next held S6 layer (S6 `DEFERRED_ITEMS` §2). S6 produces a deterministic per-run `Interpretation` (verdict + attribution + a structured `Cause` = `{cause_kind, vr_name}`), persisted to `s6_interpretations` (D-111.2). Clustering aggregates *across* runs into release-level patterns: recurring non-enforcement, the same VR failing across runs, flapping outcomes. The `s6_interpretations` migration explicitly anticipated this — *"cause_kind / vr_name promote to columns when cause-clustering lands"*; this slice is that trigger.

**Chosen over LLM-phrasing.** The two held S6 layers are phrasing (LLM prose over the interpretation) and clustering. Clustering is the cleaner opener: **pure substrate, deterministic SQL grouping, no LLM, no v1 coupling, fully testable** (seed interpretations → assert clusters), and it keeps `interpretation/` LLM-free (the deterministic-first principle). Phrasing — which pulls the v1 LLM gateway across the substrate↔v1 boundary, is nondeterministic, and is dormant until a consumer reads it — is deferred (still S6 `DEFERRED` §2).

**Promote, not index-in-place.** `cause_kind` + `vr_name` move from the `detail` JSONB to typed (TEXT, nullable) columns on `s6_interpretations` + b-tree indexes — the queryable axes the GROUP BYs need. `detail.cause` stays the structured source of truth; the columns are the clustering index (back-filled from existing rows; written by `persist_interpretation` going forward). This is the migration authors' stated plan, not an expression index.

**The service (read-only).** `clustering.py` — module fns mirroring the `result_store` style, each on a caller-provided tenant-scoped session (isolation by schema), read-only `text()` SELECTs, optional `recipe_id` filter + `min_runs` threshold: `cluster_recurring_causes` (GROUP BY cause_kind), `cluster_by_vr` (GROUP BY vr_name, carrying the distinct outcomes), `cluster_flapping` (GROUP BY claim_test_id HAVING COUNT(DISTINCT outcome) > 1 — uses the typed `outcome` column). Frozen result dataclasses.

**S6-Q-007 (clustering grain) resolved.** Per-cause / per-VR / per-claim, scoped tenant-wide or by `recipe_id`. **Release-grain deferred** — `s6_interpretations` has no release→runs key; a release-level view waits on that link (and a consumer dashboard).

**Guards.** Deterministic (no LLM); `interpretation/` stays LLM-free. Read-only service (clustering never writes). The persist promotion is additive — the existing run-path interpret stage just writes two more columns; the JSONB `cause` is unchanged. S6 stays write-only — the clusters are built + tested, consumer-surfacing deferred (the dormant-substrate posture).

---

## D-117 — S6 LLM-phrasing (presentation layer): invent-nothing prose over the interpretation

**Date:** 2026-06-01
**Substrates affected:** [S6] (the LLM-phrasing presentation layer — a v1 enricher + a substrate column + a pure write-helper); [v1 `intelligence`] (the enricher + the prompt task — the LLM lives here)
**Status:** Active — S6 phase-7 slice (phrasing). On `phase-7-substrate-6-interpretation`. The capability is built + stub-tested; the live trigger is **dormant** (S6 is write-only — no consumer reads interpretations yet).

The second held S6 layer (S6 `DEFERRED_ITEMS` §2 / S6-Q-006). S6 produces a deterministic per-run `Interpretation` (verdict + attribution + a structured `Cause`); phrasing turns it into QA-readable prose — a `headline` + a 2–3-sentence plain-English `explanation`. It is a **presentation layer**: it **phrases what the deterministic core already attributed and invents nothing** — it never produces the attribution (the deterministic core stays the source of truth, the S6-Q-002 invariant).

**The boundary (the key decision).** Phrasing needs the LLM, but `interpretation/` is deliberately LLM-free (deterministic-first) and imports nothing from v1's `intelligence`. The substrate's own LLM path is forced-tool-use (structured output), wrong for prose. So the split: the **LLM lives in v1** — a `StoryViewEnricher`-shaped `InterpretationPhrasingEnricher` over `gateway.llm_call` + a Haiku prompt module (`interpretation_phrasing@v1`, no cache / no escalation), mirroring the migration-048 story_view pattern (best-effort, never-raises, hard caps, a per-tenant flag); the **schema + a pure-SQL write-helper live in the substrate** (`result_store.set_phrasing`, an `UPDATE`, no LLM). `interpretation/` stays LLM-free; v1 annotates the substrate row (the allowed consumer→producer direction).

**Storage = a column on `s6_interpretations`** (the S6-Q-006 "nullable field" lean, chosen over a separate v1 cache table): a nullable `phrasing JSONB` column, NULL until phrased. **On-demand + cache:** `get_or_phrase(run_id)` returns the cached prose, or (per-tenant flag on) phrases once + caches via `set_phrasing`. The trigger is a future consumer's; today it is dormant (S6 write-only) — the enricher + helper are built + tested, not live-fired.

**S6-Q-006 resolved.** Runs **on-demand + cache** (a v1 enricher), stored in a **nullable `s6_interpretations.phrasing` column**; invention is prevented **structurally** — the prompt is handed only the deterministic `{outcome, verdict, attribution, cause}` and instructed to paraphrase them, and the enricher validates / caps the output (it can echo + shorten, never source new facts). The deterministic `attribution` remains the single source of truth.

**Guards.** `interpretation/` gains no LLM import (the deterministic core stays pure; the LLM is confined to `intelligence/`). Phrasing never produces the attribution — it paraphrases the deterministic facts (invent-nothing). Best-effort: a failed phrasing returns None and caches nothing (the interpretation is unaffected). Stub-tested in CI (shape / validation / caps deterministic); the real-Haiku output is periodic, as story_view's is. The column is additive — `persist_interpretation` is unchanged.

---

## D-118 — S1 Tier-2 slice 1: standard-field → StandardValueSet `HAS_PICKLIST_VALUES` via content-match

**Date:** 2026-06-02
**Substrates affected:** [S1] (Tier-2 slice 1 — populate the existing `HAS_PICKLIST_VALUES` edge for standard picklist fields); [S3] (the unblocked consumer — `value-claim` accepted-values grounding, D-097.2 / D-098.1)
**Status:** Active — Phase-0 (S1 Tier-2) slice 1, on `phase-8-substrate-1-tier2`. Design only; impl is the next HOLD. Resolves S1 `PHASE_2_PLAN_corrections` §22 (the deferred standard-field SVS detection).

The first Tier-2 increment, chosen as the cleanest S3-breadth unblocker. The gap (§22): a **standard** picklist field (`Account.Industry`, `Lead.LeadSource`) draws its values from a StandardValueSet, which **does** materialize as a `PicklistValueSet` entity (external_id `SVS:{FullName}`) — but **no Salesforce API exposes the field → SVS linkage** (REST describe carries inline `picklistValues` only; standard fields are absent from Tooling `CustomField`; `FieldDefinition` 400s at v66.0). So the ~95 synced SVSes carry **0 `HAS_PICKLIST_VALUES` edges** to the standard fields that use them, and S3 cannot enumerate a standard field's accepted values to ground a `value-claim`.

**Approach — additive fill of the locked edge, no schema change (lock-clean).** Slice 1 adds a **content-match** step in the field-sync path: for a standard picklist field, set the **existing** `field_details.picklist_value_set_entity_id` to the matched SVS entity; the **existing** `_edges_from_field_row` derivation (`derivation.py:163`) then emits the **already-locked** `HAS_PICKLIST_VALUES` edge (Field → PicklistValueSet, `edges.py:319`, D-019) with no change to the edge layer. **No new edge type, no new column, no migration** — the D-019 taxonomy is untouched and the D-024 design-lock is honored. This is the D-027 "additive fill" spirit applied to the very edge whose live 0-count §22 explicitly attributed to this deferral.

**Matching policy — exact set-equality, fail-closed.** A field's active describe value api-name set is linked **only when exactly one** synced SVS's active value api-name set is **set-equal**. **0 matches → no edge** (honest absence); **>1 → no edge, logged** (two SVSes with identical value lists — ambiguous; refuse rather than guess). Chosen over §22's looser "subset/overlap threshold" because S1 is a **foundation feeding S3 grounding**: a *false* link (wrong accepted-values → a wrong test) is worse than a *missing* one. Exact-match is high-confidence, produces zero false links, and is **self-healing** — if an admin edits the field's values, the next sync's match fails and the edge correctly drops (the §22 fragility becomes correct behavior, not a bug). The **subset/overlap tolerance** for admin-customized standard fields is a **deliberate deferral** to a follow-up slice with its own disambiguation analysis (per §22's "heuristics earn their own cycle"). Ordering: the SVS `PicklistValueSet`s sync before the field content-match (the `fetch_standard_value_sets()` catalog-pin runs in the value-set phase) — verified in impl.

**Guards / verification.** Lock-safety is diff-checkable: no new edge type, no new column, no migration. Unit — the matcher picks the right SVS on set-equality, returns None on 0-or-many. Live sandbox — the `HAS_PICKLIST_VALUES` count moves 0 → ~N (one per un-customized standard picklist field), a spot-check field (e.g. `Account.Industry`) links to its SVS, and a value-customized field stays unlinked. **Downstream caveat (not slice 1):** S3's *full* `value-claim` accepted-values grounding also needs detail-table exposure in the §12 query interface (D-097 — reading the matched SVS's `picklist_value_details`); slice 1 produces the **link**, the detail-read is a separate S1 query-interface item.

---

## D-119 — S1 query-interface read: `get_picklist_values` (value-claim accepted-values)

**Date:** 2026-06-02
**Substrates affected:** [S1] (a new additive query-interface read primitive); [S3] (the consumer — `value-claim` accepted-values admissibility, lands in Phase 2)
**Status:** Active — Phase-0 (S1 query-interface) slice 2, on `phase-8-substrate-1-tier2`. Design only; impl is the next HOLD. The S1 half of the "finish value-claim" unblock (the S3 consumption is Phase 2).

D-118 created the Field → PicklistValueSet `HAS_PICKLIST_VALUES` edge, but S3 still cannot **enumerate** a standard field's accepted values. The read chain is Field →`get_related(HAS_PICKLIST_VALUES)`→ PicklistValueSet → its PicklistValue children → `get_entity_details` per value, and the **middle hop is missing**: a PicklistValueSet's PicklistValue children are linked only by the `picklist_value_details.picklist_value_set_entity_id` FK — *not* a `BELONGS_TO` edge (its sources are Field / RecordType / ValidationRule / Layout → Object; there is no `_edges_from_picklist_value_row`), and *not* reachable via `get_entities` (which filters id / sf_id / sf_api_name / display_name only). `query_entities` (detail-column conditions) is deferred (D-022). So there is no way to list a value set's values today. (Note: `get_entity_details` already exists — added D-111.1 for the S6 consumer — so D-097 / D-098's "the §12 query interface exposes no detail-table data" is now **stale**; only the children-enumeration is missing.)

**Decision — one additive read primitive.** Add `SemanticOrgModel.get_picklist_values(picklist_value_set_id, at_seq) -> list[dict]`: the version-current `picklist_value_details` rows for the value set (each carrying `value_api_name` / `is_active` / `sort_order`), ordered by `sort_order`, `[]` when none. It mirrors `get_entity_details`'s contract — `_validate_version(at_seq)`, a **trusted hardcoded table** (no caller-input SQL surface) — but reads a *set* of children version-scoped via the `valid_from_seq` / `valid_to_seq` window join to `entities` (the `get_entities` idiom), since the children aren't the 1:1-by-id row `get_entity_details` returns. It completes the chain: Field → PicklistValueSet (the D-118 edge) → `get_picklist_values` → the accepted value set.

**Why a read primitive, not an edge.** A PVS→PicklistValue containment edge would be the "natural" graph model, but the edge taxonomy is **D-019-locked at 14 types** — a 15th contradicts a locked decision (unlike D-118, which *populated* an existing edge). A new *read primitive* is **additive and uncovered** by D-022's five primitives, so it lands under D-024's "new decisions D-025+ may be added for matters not covered, but may not contradict locked decisions" — the same carve-out `get_entity_details` used (D-111.1). Consumer-driven, per D-022's "full ergonomics emerge with the consumer slice": the `value-claim` grounder is the consumer surfacing this read.

**Phase boundary.** Slice 2 is the **S1 read** only — a field's accepted values become enumerable and the chain is proven end-to-end. The **S3 consumption** (wiring `get_picklist_values` into `value-claim` *admissibility* — is the requirement's `V` in the accepted set?) is **Phase 2** (S3-breadth, the value-claim kind's completion), keeping the breadth-first boundary intact. Slice 2 ships value-claim *groundable-from-S1*; the kind finishes when S3 consumes it.

**Guards / verification.** Additive — no edge, no schema, no migration (diff-checkable). Unit: version-scoped rows on seeded data (a superseded value excluded at the old seq, present at the new) + the fail-loud-version + no-raw-SQL contract mirrored from `get_entity_details`. Read-chain integration: Field → `get_related(HAS_PICKLIST_VALUES)` → PVS → `get_picklist_values` → the value set. Live-sandbox: `get_picklist_values` on a real standard field's SVS (e.g. `Account.Industry`) returns its values — the slice exit-gate.

---

## D-120 — Phase-0 (S1 breadth-unblock) closure: the verified S3-readiness map; Tier-2 remainder → S1 Phase-3

**Date:** 2026-06-02
**Substrates affected:** [S1] (Phase-0 close — a readiness verification + a scope decision, no new primitive); [S3] (the consumer — which claim-kinds are S1-groundable for Phase-2 emission)
**Status:** Active — Phase-0 close, on `phase-8-substrate-1-tier2`. A scope decision + verification tests; no `query.py` change.

Phase 0's purpose was to unblock S3 breadth on the **S1 side**. The slice-2/3 surveys found the S1 foundation already covers more than the roadmap assumed — `get_entity_details` (D-111.1) and `get_related` (which returns edge properties) plus the already-synced permission grant edges already ground three of the data-present claim-kinds. So Phase 0 **closes here with a verified readiness map**, not more Tier-2 slices.

**The S1 grounding map (what Phase 2 / S3 breadth can rely on):**
- **`value-claim` (accepted-values) — BUILT this phase.** D-118 (standard-field → SVS `HAS_PICKLIST_VALUES` edge via content-match) + D-119 (`get_picklist_values`). The Field → PVS → values chain is proven (the D-119 chain test).
- **`permission-claim` (capability) — ALREADY READY.** The five PERMISSION edges (`GRANTS_FIELD_ACCESS` / `GRANTS_OBJECT_ACCESS` / `HAS_PERMISSION_SET` / `INHERITS_PERMISSION_SET` / `HAS_PROFILE`) exist and are synced (real sandbox data: ~11K FieldPermissions + ~2.6K ObjectPermissions), and `get_related` already returns the far-end entity **and** the edge's `can_read` / `can_edit` properties. No S1 work needed — verified by test (`get_related` over a `GRANTS_*_ACCESS` edge surfaces the grant flags).
- **`config` existence / property — ALREADY READY.** Existence via `get_entities` (a non-empty result), property via `get_entity_details` (the field/object detail row). Both pre-existing; existence newly tested here, property already tested (`test_s6_s1_reader`).

**What defers, and why Phase 0 closes without it:**
- **automation-effect ← Flow logic interpretation.** The Flow entry-condition parse is **dormant on this sandbox** (zero record-triggered flows — all AutoLaunched), so it unblocks nothing now; the work goes to **S1 Phase-3**, firing automatically when a future org has record-triggered flows (the TRIGGERS_ON correctness-complete-despite-zero-edges precedent).
- **permission run-as / sharing rules / OWD / Apex sharing; approval-process modeling.** The heavier Tier-2 graph, explicitly **beyond the D-024 lock window** → **S1 Phase-3** (post-~2026-07-20). Their S3 kinds (complex / run-as permission, integration topology) defer with them (D-080 / D-082). The **D-020 `effective_field_permissions`** materialized view (inheritance aggregation) is Phase-2-scoped per the D-024 phase plan and unneeded for the simplest single-edge permission-claim — deferred.
- **Phase-0 ops** (refresh scheduling, observability, tenant onboarding). Operational hardening, **not breadth-blocking** → folded into S1 Phase-3.

**So Phase 0 ships:** the value-claim S1 grounding (built, D-118 / D-119) + a **tested readiness map** confirming permission + config are already groundable. No new S1 primitive in this close — three deterministic tests pin the existing capability so Phase 2 can rely on it. **Merge exit-gate:** the live-sandbox probe of the built slices (D-118 edge-count 0→~N; D-119 `get_picklist_values` on a real SVS), run before the phase merge. The deferred Tier-2 reopens as S1 Phase-3 when the design-lock lifts.

---

## D-121 — Substrate-2 readiness ratification: the S3/S4-breadth contract is settled [Phase 1]

**Date:** 2026-06-02
**Substrates affected:** [S2] (a readiness ratification — no build); [S3, S4] (the consumers — confirmed the breadth surface they call is complete)
**Status:** Active — Phase 1 of the program roadmap, on `phase-9-substrate-2-readiness`. A ratification + an executable contract pin; no new method / table / enum / migration.

Phase 1's purpose: confirm Substrate-2 (Test Representation) is ready for the S3 (generation) and S4 (execution) **breadth** phases before they build on it, and ratify two deferred-handoff decisions. The finding: **S2 is complete and there is no gap** — Phase 1 is a ratification, not a build.

**Coverage — the breadth surface S3/S4 call is fully present.**
- The **22-method Semantic Transaction Coordinator** (`primeqa/test_representation/coordinator.py`; Phase 4 / D-064 / 1148 tests) is the single read/write entry point — the write / read / discovery / resolution / boundary groups.
- The **taxonomies already cover every breadth kind**: `CLAIM_KIND_ENUM` holds all **16 claim-kinds** (value / state-transition / automation-effect / prohibition / existence / property / metadata-relationship / capability / sharing-rule / element-state / navigation / layout / platform-event / outbound-message / callout / inbound-effect); `RECIPE_KIND_ENUM` all **5** recipe verticals; `TRIGGER_KIND_ENUM` all **6** triggers (`models_db.py:82/108/119`). So S3 emitting the remaining 13 claim-kinds (Phase 2) and S4 running any recipe vertical hit **no** unlisted kind — `claim_kind` / `recipe_kind` are *parameters* to the same `write_claim` / `write_recipe`, not per-kind code.
- **S3** routes through `query_equivalent_claims` → `write_claim` → `write_recipe` (`primeqa/generation/persistence.py:118/124/137`); **S4** through `select_recipe_for_execution` + `report_run_outcome`. The **e2e round-trip is already proven** (`tests/integration/test_representation/e2e/{lifecycle,s4_boundary,multi_recipe}.py`). **No coverage gap.**

**Ratify §11 disposition (D-065).** The v2.2 test-management tables' dispositions stand: ABSORB (`test_cases` → claims/recipes) / DROP (`test_case_versions`, `requirements`, `metadata_impacts`) / **MIGRATE** (`test_suites` / `sections` / `suite_test_cases` / `ba_reviews` → future "test catalog" + "review workflow" substrates). The MIGRATE gap is a **deliberate boundary**, not a defect — short-term v2.2 parity traded for long-term substrate coherence. No v2-GA product reason to renegotiate surfaces here; if one arises it is a substrate-boundary decision, not Phase-1 work. Migration execution stays post-cutover.

**Ratify the provenance retirement (D-074).** S3's **semantic ledger** (`generation_requests` + `generation_outcomes`) retires into S2 provenance when the typed read API `get_provenance` / `get_recipe_provenance` ships — reserved in SPEC §10.2; the `test_provenance` rows are **already written** by every Coordinator mutation, so only the typed read surface is pending. Target: the **Phase-7 greenfield cutover**, where S3's ledger retires alongside v1. `llm_calls` (operational observability) **stays in S3 permanently** — it does NOT migrate (D-074). Until then S3 owns the semantic ledger as a v1 shim.

**Deliverable.** No product code: an executable **taxonomy-contract drift-guard** (`tests/unit/test_representation/test_taxonomy_contract.py`) pins `CLAIM_KIND_ENUM` (16) + `RECIPE_KIND_ENUM` (5) + `TRIGGER_KIND_ENUM` (6) as the S3/S4 breadth contract — a future edit that drops or renames a kind fails loud. The standing S2 proof (1148 tests + the e2e round-trip) is unchanged. **Merge gate is deterministic** (Phase 1 touches no Salesforce — no live probe).

---

## D-122 — Configuration breadth: existence-claim + property-claim emission (Phase 2 slice 1)

**Date:** 2026-06-02
**Substrates affected:** [S3] (emission — two new configuration claim-kinds become emittable); [S2] (two new claim-body Pydantic models — additive, no migration)
**Status:** Active — Phase 2 (S3 generation breadth) slice 1, on `phase-10-substrate-3-breadth`. The emission path; the prompt live-reach (the LLM *proposing* these) is slice 1b.

Phase 2 grows the emittable surface from 3 of 16 toward all groundable kinds. Slice 1 — the cleanest entry (S1-ready, Layer-1-complete, no caveat machinery) — adds the two configuration claim-kinds D-098 / D-098.4 deferred to the S1 detail-read increment (now shipped, Phase 0 / D-119–D-120):

- **`existence-claim`** — "Object / Field / Flow X exists in this org."
- **`property-claim`** — "Field X is required / has length N; RecordType RT exists on Object" — an S1-Tier-1-modeled property holds.

**Mirror the proven `_author_config` pattern.** Both are **Layer-1-complete, no caveat** — reading the metadata *is* the verification (D-079, as for `metadata-relationship-claim`). The shape is the existing `_inspection_recipe` (a metadata-read + an assert), `inspection-trigger` + `metadata-recipe`, `LAYER_1`. The extension points (the established add-a-kind pattern):
- **S2** — two claim bodies in `test_representation/models/claims/configuration/`: `ExistenceClaimBody(subject)` and `PropertyClaimBody(subject, property_name, expected_value)`, `@register_body`-registered. **Additive, no migration** — `CLAIM_KIND_ENUM` already holds both (D-121); the body is just the JSONB `asserted_truth` shape.
- **S3 `emission.py`** — `GroundedExistence` / `GroundedProperty` dataclasses (mirror `GroundedEmission`); `_author_existence` / `_author_property` (reuse `_inspection_recipe`); two `author_emission` dispatch arms; `EMITTABLE += {(configuration, existence-claim), (configuration, property-claim)}`.
- **S3 `governance_core.py`** — open the `_resolve_configuration` gate (today it refuses everything but metadata-relationship): **existence** grounds via `self._s1.get_entities(entity_type, filters)` (found → `GroundedExistence`; absent → `no_relevant_context` refusal); **property** grounds via `self._s1.get_entity_details(entity_id)` (the property is an S1-modeled detail column carrying the asserted value → `GroundedProperty`; unmodeled column → `ungrounded-claim` / `ontology_gap` — the honest Tier-1 ceiling, D-079).

**Grounding is invent-nothing (Guardrail 2).** Existence is the non-empty `get_entities` result; the property value is read from the S1 detail row — never the requirement's assertion on faith. A mismatch (asserted ≠ S1) refuses rather than emit a false claim.

**Drift-guard kept lockstep.** `EMITTABLE` and the `_EMITTABLE_SHAPES` map (the `set(_EMITTABLE_SHAPES) == set(EMITTABLE)` test) update together — the mechanical guard against a kind in the gate with no author arm (or vice-versa). Deterministic emit probes assert the bundle shape / recipe / `LAYER_1`-no-caveat for both.

**Scope boundary.** Slice 1 is the **emission path** — the two kinds become emittable + grounded + deterministically tested. The **prompt live-reach** (a `configuration` fragment line so the LLM proposes existence/property + a live ontology-coherence probe) is **slice 1b** (the D-115.4-style activation), kept separate so the prompt freeze ritual doesn't entangle the emission build. No S1 change (the reads shipped in Phase 0); no migration.

---

## D-123 — Permission capability-claim emission (Phase 2 slice 2)

**Date:** 2026-06-02
**Substrates affected:** [S3] (emission — the first permission-archetype kind); [S2] (one new claim-body Pydantic model — additive, no migration)
**Status:** Active — Phase 2 (S3 generation breadth) slice 2, on `phase-10-substrate-3-breadth`. The emission + grounding path; the prompt live-reach is batched into slice 4 (D-125).

The second Phase-2 slice, and the **only** one of the four permission / UI / integration archetypes that is **S1-Tier-1-groundable today** (the readiness audit: integration entity types are absent from S1 Tier-1, and sharing/OWD/Apex are Tier-2/Tier-3 — all deferred to S1 Phase-3). `capability-claim` asserts "**Profile / PermissionSet P grants read/edit on Object/Field X**" — buildable now because the `GRANTS_OBJECT_ACCESS` / `GRANTS_FIELD_ACCESS` edges are synced (~11K FieldPermissions on the sandbox) and `get_related` returns their `can_read` / `can_edit` properties (proven Phase 0 / D-120). **Layer-1-complete, no caveat** — reading the grant IS the verification (D-079).

**Shape — two patterns already shipped.** capability-claim fuses the **two-endpoint** grounding of `metadata-relationship-claim` (a grantee + a target + the grant edge between them) with the **metadata-inspection recipe** of the configuration kinds. The extension points (the established add-a-kind pattern; **no migration** — `capability-claim` is already in `CLAIM_KIND_ENUM`, D-121):
- **S2** — `CapabilityClaimBody(granting_subject: IdentityBearingRef, target: IdentityBearingRef, granted_capability: str, grant_type: Literal["object","field"])` in a new `test_representation/models/claims/permission/`, `@register_body`-registered + added to the flat `ClaimBody` union.
- **S3 `emission.py`** — `GroundedCapability` + `_author_capability` (reuse `_inspection_recipe` — read the grantee, assert the grant edge surfaces); the `author_emission` dispatch arm; `EMITTABLE += ("permission","capability-claim")`; the `_EMITTABLE_SHAPES` drift-guard kept lockstep. (`_HAS_LAYER_2["capability-claim"]` confirmed `False` — no caveat.)
- **S3 `governance_core.py`** — `_resolve_permission` (dispatch on `archetype_hint == "permission"`): resolve the grantee (Profile/PermissionSet) + the target (Object/Field), verify the `GRANTS_OBJECT_ACCESS` / `GRANTS_FIELD_ACCESS` edge via `get_related` (the `can_read`/`can_edit` ride the edge properties); absent → `no_relevant_context`. `check_refs_exist` gains a permission branch (two refs — grantee + target, like the metadata-relationship source/target branch).

**Scope — the D-080 honesty (recipe-kind preserves claim semantics).** v1 grounds **direct grants** with a **metadata-inspection** recipe: it verifies the grant is *configured*, not the *effective runtime* capability. The **run-as-execution** recipe (a test user with profile P attempts the op + observes success/failure) is a different verification surface, and D-080 forbids silently substituting one for the other. So complex capability claims implying sharing / OWD / role-hierarchy / Apex-sharing (S1 Tier-2, unmodeled) **refuse with disambiguation** rather than emit a metadata-inspection that overstates verification — a higher refusal rate on complex claims, which is the honest posture, not a defect. The run-as path + the Tier-2 sharing model reopen later (S1 Phase-3 / S4 side-B).

**Verification + boundary.** Emit-probes (bundle shape / recipe / Layer-1-no-caveat) + the drift-guard + an integration grounding test on real seeded S1 (seed a Profile + a `GRANTS_FIELD_ACCESS` edge → resolve → check-refs → emit → persist, mirroring the existence test that caught the slice-1 wiring bugs). The **prompt live-reach** (the LLM *proposing* capability-claim) is **slice 4 (D-125)** — batched with existence/property/layout into one prompt freeze. No S1 change; no migration.

---

## D-124 — UI layout-claim emission (Phase 2 slice 3)

**Date:** 2026-06-03
**Substrates affected:** [S3] (emission — the first UI-archetype kind); [S2] (one new claim-body Pydantic model — additive, no migration)
**Status:** Active — Phase 2 (S3 generation breadth) slice 3, on `phase-10-substrate-3-breadth`. The emission + grounding path; the prompt live-reach is batched into slice 4 (D-125).

The third Phase-2 slice and the **UI-archetype debut**. `layout-claim` asserts "**Field F appears on PageLayout L**" — the emittable surface grows **6 → 7**, completing the **S1-Tier-1-groundable set** (the remaining ~8 kinds are all Tier-2/Tier-3/integration-blocked → S1 Phase-3).

**Verify-data-first gate → BUILD (the discipline the slice required).** Unlike `state-transition-claim` (dormant: the sync author's docstring records "this sandbox has **zero** record-triggered flows → zero `TRIGGERS_ON` edges", phases.py — built-correct-but-grounds-nothing, deferred), the layout grounding edge is **live**: `phase_layout` is a fully-implemented, active sync phase whose docstring documents **~115 layouts → ~3,000–12,000 `INCLUDES_FIELD` edges** on the dev sandbox (phases.py:846/881). `INCLUDES_FIELD` (Layout→Field, grid-placement properties) is in the locked `TIER_1_EDGES` (edges.py:290), and `get_related` traverses it generically (no new S1 work). The one residual — *live*-confirming instances this session — is the **same standard capability-claim (D-123) was built on** (documented expected cardinality; the live probe deferred to the post-merge sandbox run); every Salesforce org universally ships page layouts, so the dormancy risk is near-zero.

**Shape — a clone of capability-claim (D-123), `archetype="ui"`, `edge="INCLUDES_FIELD"`.** Two endpoints (Layout + Field) + a **metadata-inspection** recipe. **Layer-1-complete, no caveat** — the `INCLUDES_FIELD` edge IS the verification (D-079). The established add-a-kind pattern (**no migration** — `layout-claim` already in `CLAIM_KIND_ENUM`, the `"ui"` archetype already in `ARCHETYPE_ENUM`, D-121):
- **S2** — `LayoutClaimBody(layout: IdentityBearingRef, field: IdentityBearingRef)` in a new `test_representation/models/claims/ui/`, `@register_body`-registered + added to the flat `ClaimBody` union.
- **S3 `emission.py`** — `GroundedLayout` + `_author_layout` (reuse `_inspection_recipe` — read the Layout, capture the `INCLUDES_FIELD` edge); the `author_emission` dispatch arm; `EMITTABLE += ("ui","layout-claim")`; the `_EMITTABLE_SHAPES` drift-guard kept lockstep. (`_HAS_LAYER_2["layout-claim"] = False` — no caveat.)
- **S3 `governance_core.py`** — `_resolve_ui` (dispatch on `archetype_hint == "ui"`): resolve the Layout + the Field, verify the `INCLUDES_FIELD` edge via `get_related`; absent → `no_relevant_context` / ungrounded. `check_refs_exist` gains a UI branch (two refs — layout + field, like the permission grantee/target branch).

**Key design call — metadata-recipe, NOT ui-recipe.** Layout placement is a *metadata* fact (the layout includes the field), verified by inspection — `metadata-recipe` / `inspection-trigger`, exactly like capability/config. The `ui-recipe` / `ui-trigger` kinds stay reserved for `element-state-claim` (the *runtime* render/enable question — does the field actually surface in the live Lightning page), which is S1 Tier-3 and deferred. Using a UI-interaction recipe for a placement claim would overstate verification (the D-080 honesty applied to the UI archetype).

**Scope.** v1 grounds **placement-existence** (the field is on the layout). There is no capability-style "bit" to check — `INCLUDES_FIELD`-edge existence *is* the placement. Section/row/column **assertions** (e.g. "F appears in the 'Address' section") are a v1.1 refinement (the edge carries `section_name`/`row`/`column` as properties; deferred, not yet asserted). Other UI kinds — `element-state-claim` (Tier-3 Lightning), `navigation-claim` — stay deferred.

**Verification + boundary.** Emit-probes (bundle shape / metadata-recipe / Layer-1-no-caveat) + the drift-guard + two integration grounding tests on real seeded S1 (seed a Layout + an `INCLUDES_FIELD` edge → resolve → check-refs → emit → persist; edge-absent → refuse) + the `test_substrate_imports` registry guard (+`layout-claim`). The **prompt live-reach** (the LLM *proposing* layout-claim) is **slice 4 (D-125)** — batched with existence/property/capability into one prompt freeze. No S1 change; no migration.

---

## D-125 — Prompt live-reach + eval corpus for the Tier-1 breadth set (Phase 2 slice 4)

**Date:** 2026-06-03
**Substrates affected:** [S3] (prompts — the freeze ritual; eval corpus). No S2/S1 change; no migration.
**Status:** Active — Phase 2 (S3 generation breadth) slice 4, on `phase-10-substrate-3-breadth`. The cross-cutting live-reach for slices 1–3's four new kinds.

The fourth Phase-2 slice — the **end-to-end live-reach** for the kinds slices 1–3 made emittable + groundable (`existence-claim` / `property-claim` D-122, `capability-claim` D-123, `layout-claim` D-124). Until now those kinds ground + emit when *handed* an intent, but the LLM has no guidance to *propose* them — so they're unreachable from a real requirement. This slice closes that, batched into a single prompt freeze (the value-claim live-reach precedent, D-115.4).

**The de-risking finding — the tool schema is already permissive.** `propose_semantic_intent`'s `_ARCHETYPES` / `_CLAIM_KINDS` already enumerate `permission`/`ui` + `capability`/`layout`/`existence`/`property` (verbatim from S2's enums, D-095.A), and `target_subject_hint` is a free-form `{"type":"object"}` (no fixed key set, no `additionalProperties:false`). So **zero tool-schema / Layer-A change** — the live-reach is purely prompt fragments + the freeze + eval probes.

**Prompt fragments (the live-reach).** Each fragment names the *exact* `target_subject_hint` keys the resolver reads — the precision bar `data_behavior.md` set for value-claim:
- `configuration.md` — **add** existence-claim (flat `{entity_type, sf_api_name}`) + property-claim (flat + `property_name` + `expected_value`). Was metadata-relationship-only.
- `permission.md` — **sharpen** to the precise keys: `grantee` / `target` as `{entity_type, sf_api_name}`, `granted_capability` (read/edit), `grant_type` (object/field).
- `ui.md` (**new**) — layout-claim: `layout` / `field` as `{entity_type, sf_api_name}`.

**The freeze (`generation@v2` → `v3`).** `_FRAGMENTS += "ui"`; `base.md` title bumped to v3; `compose_working()` → freeze `versions/generation_v3.md`; add to `_FILES`; record its SHA-256 in `RECORDED_HASHES`; `CURRENT = "generation@v3"`. v1/v2 stay frozen + pinned-resolvable (replay determinism, D-103.1). The hash-guard test loops `versions()` — v3 auto-covered.

**Eval corpus.** +3 offline+live probes in `drafts.json` — `config-existence-draft`, `permission-capability-draft`, `ui-layout-draft` (each: fixture → scripted propose+emit → expect draft / Layer-1 / no-caveat / metadata-recipe + a live envelope). One runner one-liner: `eval/runner.py:_seed` reads edge `properties` from the fixture (mirrors the existing entity-`attributes` read) so the capability probe's grant edge carries `can_edit:true` — additive, defaults `{}`, every existing probe unaffected. **D-104 envelope revisit:** the now-stale `_note` on `config-verified-draft` is corrected (existence/property are emittable now → they no longer auto-fail; they stay correctly in `acceptable_variants` as alternate *readings*, not forced into invariants — forcing one reading would make the probe brittle).

**Honest scope cut — property's eval probe defers (with #83).** property-claim grounding reads `get_entity_details`, which hits a per-type **detail table** (`field_details`) the eval `_seed` (and the integration `seeded` fixture) doesn't populate — the *same blocker* that deferred the property grounded integration test (D-122 follow-on, task #83). Rather than expand the fixture loaders to seed detail-tables (its own piece of work), property's deterministic probe folds into that follow-on; property's **live-reach still ships** (the fragment guidance). existence + capability + layout ship full offline+live probes now.

**Verification + boundary.** `compose_working()` round-trips; the v3 content-hash guard; `test_current_resolves_to_v3` (CURRENT + v3 title strings); `test_compose_working_has_all_fragments` (+ui); the offline corpus green (3 new drafts auto-load + auto-run, the `>=` category asserts hold); the live envelopes authored-but-skipped (periodic, env-gated). No S2/S1 change; no migration.

---

## D-126 — Phase 2 close: the realized Tier-1 breadth set (7/16) + the deferred-9 catalog

**Date:** 2026-06-03
**Substrates affected:** [S3] (ledger / close — no code). Documentation + merge gate.
**Status:** Active — Phase 2 (S3 generation breadth) **close**, merging `phase-10-substrate-3-breadth` → `main`.

Phase 2 set out to grow the emittable claim-kind surface from the Phase-2-lock thin vertical toward substrate-2's 16-kind taxonomy. The realized outcome — **7 of 16**, the *complete S1-Tier-1-groundable set* — is the honest ceiling at the current S1 tier, not a shortfall: the re-scope (D-122 design) established early that only S1-Tier-1-groundable kinds are buildable now, and all of them are now built, grounded, and LLM-reachable.

**Realized emittable set (7).** `configuration`: metadata-relationship (D-098), existence + property (D-122) — **3/3 complete**. `permission`: capability (D-123). `ui`: layout (D-124). `data_behavior`: prohibition (D-101), value (D-115). The four breadth kinds (existence/property/capability/layout) are LLM-reachable end to end via prompt `generation@v3` (D-125); the configuration archetype is fully realized.

**Deferred-9 catalog (each an honest refusal, by unblock condition).** None is a defect — each is a kind whose grounding the current S1 tier cannot support, so the substrate *refuses with disambiguation* rather than overstate:
- `state-transition-claim` (data_behavior) — **dormant**: zero record-triggered flows on the sandbox (`phase_flow` docstring), build-correct-but-grounds-nothing; reopens when the org carries record-triggered flows or the formula parser lands (D-100.1).
- `automation-effect-claim` (data_behavior) — Apex effect-tractability → **S1 Tier-2 (Apex)** (D-078 / D-100.5).
- `sharing-rule-claim` (permission) — sharing / OWD / role-hierarchy capability unmodeled → **S1 Tier-2 (sharing)** (D-080).
- `element-state-claim` (ui) — runtime Lightning render/enable (distinct from static layout placement) → **S1 Tier-3** (D-081).
- `navigation-claim` (ui) — navigation model unmodeled → **S1 Tier-3**.
- `platform-event-claim` / `outbound-message-claim` / `callout-claim` / `inbound-effect-claim` (integration ×4) — the integration entity types are absent from S1 Tier-1 → **S1 integration modeling** (D-082 / D-084).

These reopen as an S3-breadth continuation after the corresponding S1 tiers land (S1 Phase-3); the catalog above + DEFERRED §1/§6/§8 are the standing record.

**Open follow-on (deferred, not flushed) — task #83.** property-claim's *grounded integration test* and its *deterministic eval probe* both need a seeded `field_details` detail-table row that neither fixture loader (eval `_seed` / integration `seeded`) yet produces — its own piece of test-infrastructure work. property's **feature is fully shipped** (emission D-122 + grounding + live-reach D-125, unit-probed); only its grounded *test* lags. Deferred as a tracked follow-on per the Phase-2-close decision; it does not gate the breadth claim.

**Merge gate.** A real green run of the substrate-relevant suites (generation unit + integration + representation + the offline eval corpus + eval-harness) — never the live probes (env-gated, periodic). No migration across the whole phase (every kind was pre-seated in `CLAIM_KIND_ENUM` / `ARCHETYPE_ENUM` at the D-121 readiness ratification). Merge `phase-10-substrate-3-breadth` → `main` via PR on green.

---

## D-127 — S4 existence execution: the read-shape dispatch + entity self-read (Phase 3 slice A1)

**Date:** 2026-06-03
**Substrates affected:** [S4] (execution — the metadata translator). Pure-S4; no S2/S3 change, no migration.
**Status:** Active — Phase 3 (S4 execution) slice A1, on `phase-11-substrate-4-execution`. The first slice closing the generation→execution gap Phase 2 opened.

Phase 2 made existence/property/capability/layout **emittable + grounded + LLM-reachable**, but **not executable**: S4's metadata translator (`translator.py`) has exactly one edge (`APPLIES_TO`) and `translate_read` assumes `fields_to_capture[0]` *is* an edge to traverse. existence breaks that assumption — its recipe (`_author_existence` → `_inspection_recipe(capture_field="sf_api_name", assert "exists")`) reads the subject's **own** metadata, not a related-edge. So executing an existence recipe today raises `UnsupportedEdgeError`. This slice makes existence runnable end-to-end; it is the simplest breadth win because the `exists` predicate is **already** supported by the executor.

**The shape fork — read-shape dispatch (D-127.A).** `translate_read` is refactored from a flat edge-lookup into a **two-mode dispatch**:
- **edge-read** — `capture_field` ∈ the known Tier-1 edges → the existing `_EDGE_TRANSLATORS` (today: `APPLIES_TO`). Unchanged.
- **self-read** — `capture_field` is the subject's own surface (`sf_api_name` for existence; a property name in A2) → a new finite registry of **entity-self-read builders keyed on `subject.entity_type`**.

The self-read builders reuse the **proven** Tooling SOQL already shipped in v1 sync (`metadata/service.py`): `Object` → `SELECT QualifiedApiName FROM EntityDefinition WHERE QualifiedApiName = '<api>'`; `Field` (external_id is the qualified `Object.Field`) → `SELECT QualifiedApiName FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName = '<object>' AND QualifiedApiName = '<field>'`. We reuse the *query knowledge* (the column + relationship vocabulary), not the v1 fetcher objects — the thin `ToolingReadClient` stays the only transport. An unknown `subject.entity_type` raises (fail-loud, never a silent empty query — the SPEC §5 realization discipline).

**Why this is faithful (no semantic injection, D-108.1).** existence asserts *the subject surfaces in the org's metadata*. A non-empty self-read **is** that verification — the query adds only operational mechanics (the `FROM` object, the `QualifiedApiName` scoping), never a predicate the recipe didn't assert. The executor's existing `exists` (held ⇔ row_count > 0) renders the grounded outcome unchanged.

**Boundary.** No executor change (`exists` already supported). No bridge change (`build_metadata_inspection_plan` already projects any `ReadMetadataStep`/`AssertStep` verbatim — verified). No S2/S3/S1 change; no migration. The `ToolingQuery` evidence dataclass gains a self-read shape (the `edge` field carries the captured surface, e.g. `sf_api_name`, so S6 can still tell an absent subject from a present one).

**Verification.** Deterministic stub-client unit tests (no org, no PG): `test_translator.py` (Object + Field self-read SOQL exact strings; the `Object.Field` split; SOQL-literal escape; fail-loud on unknown entity_type), `test_executor.py` (existence passes on ≥1 row / fails on empty), `test_run_dispatch.py` (a real `author_emission(GroundedExistence(...))` recipe routes through the metadata path to a grounded outcome). Live-sandbox proof deferred (decision) — the inspection spine is already live-proven; this is a translator extension.

---

## D-128 — S4 property execution: the equals/is_null predicate + a finite property→column map (Phase 3 slice A2)

**Date:** 2026-06-03
**Substrates affected:** [S4] (execution — the metadata executor + translator). Pure-S4; no S2/S3 change, no migration.
**Status:** Active — Phase 3 (S4 execution) slice A2, on `phase-11-substrate-4-execution`.

Property-claim recipes (`_author_property` → `_inspection_recipe(capture_field=<property_name>, assert_predicate="equals"/"is_null", assert_value=<S1 value>)`) read the subject's own metadata and assert a property holds a value. Two gaps block execution: the executor supports only `exists`, and the translator has no self-read for a property capture. This slice adds both — the durable value is the **predicate machinery** (reusable by every future property-bearing read); the mapped property set is a deliberately narrow, honest starting point.

**The executor — equals + is_null over a captured value (D-128.A).** `_SUPPORTED_PREDICATES |= {"equals", "is_null"}`. Unlike `exists` (row-count), these read a **captured column value** out of the read's single row. The metadata property recipe's assert carries `subject_ref="read-subject"` (the step, no field — unlike the data-recipe's `<step>.<field>`), so the executor cannot learn the column from the assertion. It learns it from the **read**: the translator records the SELECTed Tooling column on the `ToolingQuery` (`capture_column`), the executor stashes it per read step, and `_run_assert` reads `rows[0].get(capture_column)`. `equals` holds iff `observed == value` (with a `str()`-coercion fallback for the int-vs-string JSON representation gap, never masking a semantic mismatch); `is_null` holds iff the value is absent/None. A non-`exists` predicate over a read that captured no column fails loud (a recipe/plan defect).

**The translator — a finite, honest property→FieldDefinition-column map (D-128.B, Fork A-P).** The `_self_read_field` builder gains a property branch over a finite map of the **cleanly equals-mappable** Field properties: `length`→`Length`, `precision`→`Precision`, `scale`→`Scale` (numeric, same value semantics on Tooling `FieldDefinition`). The SELECT names that column; `capture_column` carries it to the executor. **Deliberate fail-loud** (`UnsupportedPropertyError`, a new sibling of `UnsupportedEdgeError`) — the "never guesses" discipline (SPEC §5) — for:
- **`is_required`** — describe/layout-derived, **no faithful Tooling `FieldDefinition` column** (entity_attributes.py: it is the page-layout create-time enforcement, distinct from the column-level `is_nillable`). A future describe-backed read path (Fork A-P.2, a new transport) reopens it.
- **`field_type`** — the S1 value is the describe vocabulary (`"picklist"`, `"string"`); Tooling `DataType` is a display string (`"Picklist"`, `"Text(255)"`). A naive `equals` would mismatch — **not faithful**, so refuse rather than wrongly fail. A describe-type read or a vocabulary map reopens it.
- **other field_details flags** (`is_custom`/`is_unique`/`is_nillable`/…) and **Object-subject properties** — each needs its own verified column+value mapping before it can ground; until then, refuse.

This is the same honest-refusal posture as the D-125 `field_details` scope cut: the *machinery* ships; an unmapped property surfaces as a clean `UnsupportedPropertyError`, never a wrong `passed`.

**Why faithful (no semantic injection).** The self-read adds only the `FROM`/`WHERE QualifiedApiName` scoping + the SELECTed column; the asserted predicate + value are the recipe's, read verbatim from S1 at grounding (D-122). The mapped columns' **live** value-correctness (e.g. that Tooling `Length` equals S1 `length`) is asserted-but-not-proven this session — the deterministic tests check the SOQL + the comparison logic; live-sandbox proof is deferred (decision), and an unmapped/wrong column fails *safe* (an SF error → `errored`, never a false `passed`).

**Boundary.** `ToolingQuery` + the executor's per-read column stash are the only additions; no `ReadEvidence`/result-store schema change (the plan's "no new evidence schema"). No bridge/S2/S3/S1 change; no migration. existence (A1) is unaffected (its capture has no column; `exists` ignores `capture_column`).

**Verification.** Deterministic stub-client unit tests: `test_translator.py` (length/precision/scale → correct `FieldDefinition` SOQL + `capture_column`; `is_required`/`field_type`/unmapped → `UnsupportedPropertyError`), `test_executor.py` (equals holds/fails; is_null holds/fails; the int-vs-string coercion; fail-loud when a value predicate has no captured column), `test_run_dispatch.py` (a real `author_emission(GroundedProperty(...))` length recipe routes + grounds). Live-sandbox proof deferred.

---

## D-129 — S4 async orchestration wrapper: brief transactions around the live read (Phase 3 slice B0)

**Date:** 2026-06-03
**Substrates affected:** [S4] (execution — the run path). Pure-S4; no S2/S3 change, no migration. The first PART-B (async trigger) slice.

The sync run path (`run_recipe_execution` / `_for_tenant`) holds **one DB transaction across the live Salesforce read** (Boundary A, D-108.4) — correct for a one-off sync call, but the production trigger (a worker tick over many tenants) must not pin a pooled connection across ~1–2 s of network I/O. S4's DEFERRED_ITEMS §1 names the requirement: the async path **brackets the live read with brief transactions**, orchestrating select / persist directly rather than under one umbrella. B0 builds exactly that — the foundation the B1/B2 queue+consumer drive.

**Shape — an additive wrapper, sync path untouched (Fork B-O.1).** `run_recipe_execution_async(tenant_id, test_id, *, environment_id, client, …)` reuses the **same component functions** as the sync path (`select_recipe_for_execution`, `_execute_for_kind`, `finalize_run`, `_interpret_and_persist`) across **three brackets**:
- **TX1 — select (brief, read-only).** Open a tenant session → `select_recipe_for_execution` → the `RecipeRead` is plain detachable data (the Coordinator fully hydrates it) → close. The connection is released before any live I/O.
- **Execute — NO DB connection held.** `_execute_for_kind` for the metadata path needs only the injected `client` (the bridge + executor are DB-free; the consumer resolves the client up front). The live read runs with zero open connections — the load-bearing invariant.
- **TX2 — persist + posture + interpret (brief).** Open a fresh tenant session → `finalize_run` (the `s4_execution_runs` row + the S2 posture callback) + `_interpret_and_persist` (S6, best-effort, its own SAVEPOINT) → commit.

The live-proven sync `run_recipe_execution` is **not touched** (its ~138 tests stay green); the async path is purely additive, exactly the "orchestrate the components directly" the docs call for.

**Scope — metadata-path only; data-recipe async deferred (honest).** The metadata verticals (existence / property / metadata-relationship / the caveated negative) are DB-free during execute, so B0 brackets them cleanly **today**. The positive **data** vertical reads S1 mid-execute (`SemanticOrgModel(session.connection())` for k16 padding, `run.py`) — it genuinely needs a connection *inside* the execute step, so its brief-tx bracketing is its own piece of work. B0's async wrapper **raises a clear deferral** for a non-metadata recipe rather than silently holding a connection (the consumer surfaces it as a failed job). This is the same dormancy-honesty as the deferred recipe kinds — build the clean path, refuse the unbuilt one loudly.

**Testability seam.** The two DB brackets go through an injectable `session_scope(tenant_id)` context manager (default: `get_tenant_connection` + a bound `Session`, committing on clean exit — the `_for_tenant` idiom). A deterministic test injects a fake scope yielding a `_FakeSession` + an asserting client that checks **no scope is open when `client.query` fires** — proving the invariant with no real PG.

**Boundary.** No change to the executor / bridge / finalize / result-store / S6 stages — B0 only re-orchestrates their transaction boundaries. No S2/S3/S1 change; no migration. The sync path and `RunPathResult` shape are unchanged.

**Verification.** `tests/unit/execution_engine/test_async_run.py` (deterministic, no PG): the **no-connection-during-live-read** invariant; select + persist happen in two *distinct* scopes; a metadata recipe grounds end-to-end (passed); a data recipe raises the deferral; the no-eligible-recipe branch returns `ran=False`. Live-sandbox proof deferred — B0 is a transaction-boundary refactor of already-live-proven stages.

---

## D-130 — S4 execution-job queue: s4_execution_jobs + ExecutionJobStore (Phase 3 slice B1)

**Date:** 2026-06-03
**Substrates affected:** [S4] (execution — the async job queue). Pure-S4; **one tenant migration** (`s4_execution_jobs` + `s4_execution_job_attempts`); no S2/S3 change.
**Status:** Active — Phase 3 (S4 execution) slice B1, on `phase-11-substrate-4-execution`. The per-tenant queue B2's consumer drains.

The async run path (B0) executes when called; B1 gives it a **queue** — the durable per-tenant record of "execute recipe `test_id` on `environment_id`," with claim / attempt / heartbeat / reap lifecycle. A near-mechanical mirror of S3's proven `s3_generation_jobs` / `GenerationJobStore` (D-106.4), per-tenant (schema isolation, no `tenant_id` column), `SELECT … FOR UPDATE SKIP LOCKED` claim, fresh-`request_id`-per-attempt child table, race-safe terminal guards, reaper.

**The one real design call — idempotency differs from S3 (D-130.A).** S3 keys jobs `UNIQUE (requirement_key, s1_version_seq)` — "**one job ever**" — because generating the same requirement at the same S1 version is deterministic (same input → same output). **Execution is not** — re-running a recipe on the same env at a later time is a *legitimate* operation (the org state changed; re-verification is the point). So S4 uses a **partial-unique on the active set**: `UNIQUE (test_id, environment_id) WHERE status IN ('queued','claimed','running')`. This dedups a double-enqueue while a run is pending **but allows a fresh job once the prior is terminal** — the v1 `generation_jobs` "new-job-after-terminal" model the S3 design explicitly *didn't* need, which S4 explicitly *does*. `create_or_get_job` `INSERT … ON CONFLICT (test_id, environment_id) WHERE status IN (…) DO NOTHING` then SELECTs the active job — race-safe get-or-create of the *active* job.

**Schema.** `s4_execution_jobs` (per-tenant): `id` PK, `test_id` UUID, `environment_id` int (which org — required), `status` (queued/claimed/running/completed/failed/cancelled, CHECK), `current_request_id` UUID, `attempt_count`, `claimed_at`/`started_at`/`completed_at`/`heartbeat_at`, `error_code`/`error_message`, `created_by`, timestamps; the partial-unique active index + a partial status index for the claim. `s4_execution_job_attempts` (child, CASCADE): `job_id`, `attempt_no`, `request_id` (UNIQUE), `status`, `error_code`, `started_at`/`finished_at`. **No FK to logical_versions** (S4 keys on test_id + env, not an S1 version — unlike S3). Attempts are **observability-grade** for S4 (the executor mints its own `run_id` per run; the attempt `request_id` is the job-level attempt identity, not an idempotency key against a ledger PK).

**Store.** `ExecutionJobStore(tenant_id)` mirrors `GenerationJobStore`: `create_or_get_job(test_id, environment_id, …)`, `claim_next_queued_job()` (SKIP LOCKED), `start_attempt()` (fresh request_id), `complete`/`fail`(race-safe terminal guard)/`cancel`/`heartbeat`, `reap_stale_jobs(stale_minutes=10)` (fail jobs stuck past the heartbeat threshold, reusing `fail`), `get_job`/`get_attempts`. Per-call `get_tenant_connection` (the `LedgerPersister` idiom).

**Verification + the DB-gated honesty.** The store's behavior is **Postgres semantics** (FOR UPDATE SKIP LOCKED, the partial-unique ON CONFLICT, the race-safe terminal guard) — it can only be faithfully tested against a real per-tenant schema, exactly like the S3 jobs tests (the project norm: "integration tests against the real Railway database"). So B1 ships: (a) a **DB-gated integration test** (`tests/integration/execution_engine/test_jobs.py`) — idempotency (active dedup + re-run-after-terminal), SKIP-LOCKED claim, attempt minting, race-safe terminal guard, reap — runnable where `DATABASE_URL` is set; and (b) **no-PG local checks** — the migration imports + `upgrade`/`downgrade` are well-formed, the store imports, and the `_job` row→dataclass mapping unit-tests. The behavioral green runs in the governance-DB environment (a phase-close gating item, like the Phase-0 sandbox probe #82); my sandboxed shell has no `DATABASE_URL`, so I verify (b) locally and author (a) to the project's integration-test standard.

---

## D-131 — S4 execution consumer + reaper ticks (Phase 3 slice B1's driver, slice B2)

**Date:** 2026-06-03
**Substrates affected:** [S4] (execution — the worker loop). Pure-S4; no migration.
**Status:** Active — Phase 3 (S4 execution) slice B2, on `phase-11-substrate-4-execution`. The loop that drains the B1 queue through the B0 async run.

The worker layer driving `run_recipe_execution_async` (B0) off the `s4_execution_jobs` queue (B1) — a near-mechanical mirror of S3's `consumer.py` (D-106.4 slice 3): per-tenant, one job per tenant per tick, per-tenant isolation (one tenant's failure never starves the others).

**Flow.** `process_execution_job_for_tenant(tenant_id, *, client_resolver, run_fn=run_recipe_execution_async)`: claim (SKIP LOCKED) → `heartbeat` → `start_attempt` → resolve the Tooling client worker-side → `run_recipe_execution_async(tenant_id, test_id, environment_id, client)` → `complete`. Any raise aborts the job to `failed` with a thin classified `error_code` (`credential_error` / `sf_error` / `execution_error`) + finalizes the attempt. The claim is **not** wrapped — an infrastructure failure (missing tenant schema) propagates to the per-tenant boundary in the tick. `run_s4_execution_tick` + `run_s4_reaper_tick` mirror the S3 ticks (`{tenant_id: outcome}` / `{tenant_id: reaped_count}`, per-tenant try/except).

**Two injected seams (D-131.A).**
- **`client_resolver: (tenant_id, environment_id) → client`** — resolves the Tooling/data client worker-side (the S3 `api_key_resolver` discipline), so the consumer stays decoupled from the v1 connection store and is stub-testable. The client is resolved **up front** (before the async run's execute bracket) so the async path holds no connection across the live read. The **production** resolver (a brief-tx read of v1 `connections`/Fernet creds) is wired in B3; B2 ships the seam.
- **`run_fn`** — defaults to `run_recipe_execution_async`; injectable so the consumer's *orchestration* (claim → attempt → complete/fail → isolation) is tested without re-seeding a full approved S2 recipe (the async run itself is B0-tested, the executor A1/A2-tested, the queue B1-tested). The test asserts the consumer calls `run_fn` with the right `(tenant_id, test_id, environment_id, client)` so a wiring defect is still caught.

**Complete-on-ran, fail-on-raise.** A run that returns (even `ran=False` no-eligible-recipe, or `evidence.outcome=='errored'`) **completes** the job — the *worker* did its job; the run outcome / absence of a recipe is not a worker failure (mirrors S3 completing on a refusal outcome). Only a raised exception fails the job. The run outcome lives on the persisted `s4_execution_runs` row (S6's to interpret), not the job status.

**Verification.** Governance-DB integration (`tests/integration/execution_engine/test_consumer.py`, the B1 harness): empty queue → None; one job → claimed + attempted + `run_fn` called with the right args + completed; a raising run → failed + classified `error_code`; a raising `client_resolver` → failed (`credential_error`); `ran=False` → completed; `run_s4_execution_tick` per-tenant outcomes + isolation (a bogus tenant's claim raises → `error:…`, the real tenant still processes); `run_s4_reaper_tick` reaps a stale job. Pure-S4; no migration.

---

## D-132 — S4 execution scheduler/worker wiring (Phase 3 slice B3)

**Date:** 2026-06-03
**Substrates affected:** [S4] (execution — the worker + scheduler tick wiring). v1 `worker.py` + `scheduler.py` (the deploy services); no migration.
**Status:** Active — Phase 3 (S4 execution) slice B3, on `phase-11-substrate-4-execution`. The last build slice — fires the B2 consumer + reaper from the Railway services.

B2 built the consumer + reaper *functions*; B3 fires them from the deploy services, mirroring the S3 split exactly: the **consumer** tick runs in the **worker** loop (`worker.py`, beside `s3_generation_tick`), the **reaper** tick in the **scheduler** loop (`scheduler.py`, beside `s3_reaper_tick`).

**Worker (`worker.py`).** `_default_s4_client_resolver(db_factory)` — the production `client_resolver`: `(tenant_id, environment_id) → resolve_tooling_client(db, environment_id)` (the env→connection-scoped SF token, the D-106.4 credential path), opening + closing a v1 session per resolve so **no connection is held into the async run's execute bracket**. `s4_execution_tick(db_factory=None, *, client_resolver=None)` discovers tenant schemas (`_discover_tenant_schemas`, the enrichment idiom) → `run_s4_execution_tick(tenant_ids, client_resolver=…)`; wired into `worker_tick` next to `s3_generation_tick`.

**Scheduler (`scheduler.py`).** `s4_reaper_tick(ctx)` enumerates active tenants from `shared.tenants` (via `admin_run_in_shared_schema`) → `run_s4_reaper_tick(tenant_ids)`; wired into `scheduler_tick` next to `s3_reaper_tick`. A verbatim structural mirror of the proven `s3_reaper_tick`.

**Enqueue source DEFERRED (Fork B3).** Nothing enqueues S4 jobs yet, so both ticks **no-op on an empty queue** today — exactly as `s3_generation_tick` did before its slice-4 enqueue. The *product* trigger (a post-approval hook on a freshly-approved recipe / scheduled re-verification / a CI release gate) is its own design — each implies different ownership + cadence — and is a tracked follow-on. Jobs can be enqueued programmatically via `ExecutionJobStore.create_or_get_job` (a later trigger slice, or a test). Shipping the queue + consumer + reaper + the firing wiring without a UI enqueue is coherent: the production loop is live and idle, ready for the trigger.

**Verification.** Worker-side unit tests (`tests/unit/execution_engine/test_worker_wiring.py`, mock-patched like the S3 worker tests — no DB): `s4_execution_tick` no-ops `{}` on no tenants; delegates to `run_s4_execution_tick` with the discovered `tenant_ids` + the resolver; `_default_s4_client_resolver`'s closure raises on a `None` environment_id. `s4_reaper_tick` mirrors the (precedent-untested) `s3_reaper_tick` — verified by structural mirror + import; the underlying `run_s4_reaper_tick` logic is B2-tested. Pure wiring; no migration.

---

## D-133 — Phase 3 close: S4 execution breadth (existence/property) + the async production trigger

**Date:** 2026-06-03
**Substrates affected:** [S4] (execution — close). Documentation + merge gate.
**Status:** Active — Phase 3 (S4 execution) **close**, merging `phase-11-substrate-4-execution` → `main`.

Phase 3 closed the **generation→execution gap** Phase 2 opened (Phase 2 made existence/property/capability/layout emittable + grounded + LLM-reachable; they did not yet *execute*) and laid the **async production-trigger foundation**. Six slices, each a design/impl triad; EVOLUTION/DEFERRED currency batched here.

**Realized — PART A (execution breadth, pure-S4).** `existence` (D-127) + `property` (D-128) now execute end-to-end. The translator gained a **read-shape dispatch** (edge-read vs self-read: Object→`EntityDefinition`, Field→`FieldDefinition`, reusing v1 sync's Tooling vocabulary); the executor gained `equals` / `is_null` over a captured column value. The `metadata-relationship` vertical (the original first vertical) is unchanged; the new kinds ride the same bridge (generic) + the same `exists`/value executor.

**Realized — PART B (the async trigger).** `run_recipe_execution_async` (D-129) brackets the live read with **brief transactions** (select TX → execute with **no DB connection held** → persist+posture+interpret TX), leaving the live-proven sync path untouched. The per-tenant `s4_execution_jobs` queue + `ExecutionJobStore` (D-130, **the phase's one migration** `20260603_0010`) — partial-unique active-set idempotency (re-runnable after terminal, unlike S3). The consumer + reaper ticks (D-131) draining the queue through the async run, per-tenant isolation. The scheduler/worker firing wiring (D-132) — the production loop ships **live + idle**, no-opping on the empty queue until a trigger enqueues.

**Deferred (honest, tracked).**
- **capability + layout execution** — their recipes are **under-specified for live execution** (the recipe reads one endpoint + the edge *type*; the grant target / placed field is env-detail prose only). Live execution needs an **Option-X recipe enrichment** (S2 `ReadMetadataStep` + S3 emission carry the structured second endpoint), which reopens the S2/S3 territory Phase 2 sealed — deferred to a follow-on S3 recipe-enrichment slice. The S4 translator branch (`GRANTS_*` / `INCLUDES_FIELD`) lands with it.
- **`is_required` property** — page-layout-derived, no faithful Tooling `FieldDefinition` column; refuses (`UnsupportedPropertyError`) until a describe-backed read path lands. `field_type` (describe-vocab vs `DataType`) likewise. `matches_pattern` / `not_equals` predicates likewise.
- **The data-path async bracketing** — B0 is metadata-path-only; the positive-data vertical reads S1 mid-execute, so its brief-tx bracketing is its own work (the async wrapper refuses a data recipe loudly today).
- **The product enqueue source** — the queue+consumer+reaper+wiring ship, but *what* enqueues (a post-approval hook / scheduled re-verification / a CI release gate) is its own trigger design — each implies different ownership + cadence. Jobs are enqueueable programmatically via `ExecutionJobStore.create_or_get_job` until then.

**Merge gate.** A real green run of the substrate-relevant suites — `execution_engine` (unit + the governance-DB integration: jobs + consumer), the worker wiring units, and the generation / representation / interpretation suites the run path touches — never the live Salesforce sandbox (deferred per decision: the metadata-inspection spine is already live-proven; the new predicates/self-reads + the async restructure are mechanism extensions, sandbox-confirmable post-merge like #82). One migration (`20260603_0010`), applied to the governance DB before its integration tests (proven via `alembic upgrade tenant@head`). Merge `phase-11-substrate-4-execution` → `main` via PR on green.

---

## D-134 — Substrate-5 Knowledge ratification: open the formal Knowledge substrate over the realized machinery (Phase 4 slice 1)

**Date:** 2026-06-03
**Substrates affected:** [S5] (the Knowledge System — opened as a formal substrate). Documentation + a unified public API + a contract drift-guard (Slice 2); no migration, no v1-runtime behavior change.
**Status:** Active — Phase 4 (S5 Knowledge) slice 1, on `phase-12-substrate-5-knowledge`. The ratification, mirroring S2's D-121 readiness pattern.

S5 is the **only core-path substrate built before it was documented.** Its machinery shipped in the v1 intelligence stack (≈ Apr 2026) and has fed v1 test-case generation in production since: the `KnowledgeProvider` port + `KnowledgeAssembler`, the system-rules JSON channel (33 rules), the feedback/learned channel, the Domain Packs channel. The PLATFORM_VISION names it Substrate 5 ("persists + improves the knowledge that shapes generation and execution… extends/formalizes the current Domain Packs and System Rules infrastructure"); the design order permits opening it after S1–S4. This entry **opens + ratifies** it as a formal substrate.

**The binding scope — ratify + consolidate, docs-led (the chosen fork).** The exploration surfaced a real fork: S5 could (a) be ratified/formalized over the existing machinery, (b) also be wired into the S3-substrate generation (the forward-seam), or (c) also build the unbuilt vision (per-tenant learned facts). **(a) chosen.** Phase 4 formalizes what is built — a doc set + this ratification + a unified API + a contract drift-guard — and **defers all new wiring**. Rationale: the vision says "formalize the current infrastructure"; v1 is the live product, so relocating the code or wiring new consumers now would churn it for no functional gain; the eventual greenfield cutover (Phase 7) reorganizes packages anyway. Smallest correct change.

**The ratified boundary.** S5 is a **retrieval/curation layer, not an LLM layer** — all knowledge is human-authored (git files, signal aggregation, DB rows); the LLM lives in the *consumer*. Knowledge flows one way (consumers read; S5 learns from signals *about* generations but never writes one). The provider-port contract: `Rule` (`id`/`object`/`field`/`category`/`rule_text`/`source`/`confidence`/`scope`) + `QueryContext` + `KnowledgeProvider.get_rules` + the `KnowledgeAssembler`'s dedup-by-id, **source-precedence `learned > curated > system`**, token cap, and **deterministic cache-stable render** (the prompt-cache invariant). Domain Packs are a **parallel** prescriptive channel (not a `KnowledgeProvider` — packs are ~1200-token patterns, not ≤140-char rules). Scoping: system rules global/git; domain packs global/git + the per-tenant `llm_enable_domain_packs` flag; feedback rules per-tenant DB (`generation_quality_signals`).

**The realized consumer today is v1 generation, NOT the substrate spine.** `intelligence/generation.py` → the LLM gateway → `prompts/test_plan_generation.py` injects the three blocks; the S3-substrate generation (`primeqa/generation/`) carries only an attribution stub and does not consume S5. This v1-vs-substrate split is *why* the forward-seam needs a semantic-fit design (substrate gen emits claims, not test cases — different knowledge) and is deferred.

**Slice 2 + boundary.** `knowledge/__init__.py` extended (additively) to export the full S5 surface incl. the Domain Packs channel; `tests/test_s5_knowledge_contract.py` pins the invariants. **Deferred** (DEFERRED_ITEMS): the S3-substrate forward-seam; serving S6; the unbuilt vision (per-tenant learned facts, cross-tenant patterns, the reserved `curated` provider slot); the physical relocation to `primeqa/knowledge/` (Phase-7 cutover). No migration; no v1 behavior change — the realized code is unchanged, only documented + given a coherent public API + a drift-guard.

---

## D-135 — Phase 4 close: S5 Knowledge opened, ratified, contracted

**Date:** 2026-06-03
**Substrates affected:** [S5] (close). Documentation + merge gate.
**Status:** Active — Phase 4 (S5 Knowledge) **close**, merging `phase-12-substrate-5-knowledge` → `main`.

Phase 4 opened S5 as a formal substrate over its already-deployed machinery (docs-led, per the binding scope). **Realized:** the substrate doc set (`substrate_5_knowledge/` SPEC/EVOLUTION/DEFERRED) + the D-134 ratification of the provider-port contract + the boundary; the **unified public API** (`knowledge/__init__` exports all three channels — provider port + both rule providers + the Domain Packs channel) — additive, no call-site change; the **contract drift-guard** (`test_s5_knowledge_contract.py`, 12 tests) pinning the surface + the `Rule`/`QueryContext` shape + the assembler invariants (dedup / `learned>system` precedence / byte-identical determinism / token cap / broken-provider tolerance) + domain-pack selection.

**Deferred (designed, tracked):** the S3-substrate generation forward-seam (needs a semantic-fit design — substrate gen emits *claims*, not v1 test cases — so the v1-calibrated knowledge isn't a mechanical wire-in); serving S6 interpretation; the unbuilt vision (a per-tenant learned-knowledge provider; cross-tenant patterns; the reserved `curated` provider slot); the physical relocation `primeqa/intelligence/knowledge/` → `primeqa/knowledge/` (folded into the Phase-7 cutover). S5 is now a documented + contracted boundary future consumers build against — without churning the live v1 stack.

**Merge gate.** A green run of the knowledge-relevant suites — `test_s5_knowledge_contract` + the existing `test_knowledge_architecture` / `test_domain_packs` / `test_generation_quality_gate` — never red. **No migration**; **no v1-runtime behavior change** (the only code delta is additive package exports + a new test), so the deploy is inert. Merge `phase-12-substrate-5-knowledge` → `main` via PR on green.

---

## D-136 — S6 full-surface verdicts: the positive value-claim branch + property value verdicts (Phase 5 slice 1)

**Date:** 2026-06-03
**Substrates affected:** [S6] (interpretation — the verdict surface). Pure-S6; no migration (`verdict` is a TEXT column).
**Status:** Active — Phase 5 (S6 interpretation) slice 1, on `phase-13-substrate-6-interpretation`. Closes the interpretation gap for what S4 executes today; fixes a real mis-interpretation.

S6's `interpret_run` dispatches on step shape: *any* `CreateAttemptEvidence` → the negative-prohibition branch; else → inspection. Two gaps against the **realized S4 execution surface** (Phase-3 closed: metadata-inspection incl. existence/property + data-recipe negative **and positive**):

**The positive value-claim is mis-interpreted (a correctness bug, not just missing coverage).** The positive create-and-verify vertical (D-115) emits `[CreateAttemptEvidence, DataReadEvidence, AssertEvidence]` — so its `CreateAttemptEvidence` routes to `_interpret_behavioral`, which assumes a *violating* create that should be *rejected*. A passed value-claim is verdicted `prohibition_enforced` ("the violating create was rejected as asserted") — nonsense. **The fix — a robust discriminator:** the negative emits a *single* step `(create,)` (no assert); the positive emits create + read + assert. So `interpret_run` routes **create + AssertEvidence present → `_interpret_positive`** (the value-claim); **create alone → `_interpret_behavioral`** (the prohibition negative). `_interpret_positive` → `value_persisted` (passed: created + read-back value matched the assertion) / `value_not_persisted` (failed: created, but the read-back value differs) / `not_evaluated` (errored: create failed or 0-row read-back). Attribution derived from the evidence (the create's success, the read rows, the assert's `held`) — never generated.

**Property inspection is imprecise.** existence + property both route to `_interpret_inspection` → `asserted_metadata_present`/`absent`. existence fits ("the field exists"); **property** (an `equals`/`is_null` value assert — `AssertEvidence.predicate` carries it) is mis-described as presence when the field IS present and only its *value* differs. **The fix:** `_interpret_inspection` dispatches on `assertion.predicate` — `exists` → present/absent (unchanged: existence + metadata-relationship); `equals`/`is_null` → `asserted_value_matches` (passed) / `asserted_value_differs` (failed).

**Taxonomy.** `model.py` `Verdict` Literal grows by four: `value_persisted`, `value_not_persisted`, `asserted_value_matches`, `asserted_value_differs` (the closed taxonomy "grows with recipe kinds" — the SPEC's stated pattern). **No cause attribution** for positive/property failures yet (the *why* — value drift / org change — needs the deeper org-change-correlation work; deferred). **No migration** — `verdict` persists as TEXT.

**Boundary.** No change to `attribute_run` (VR-cause is behavioral-negative only), the store, the run-path wiring, or the dormant verticals (capability/layout/ui/event/callout — no executor emits their evidence, so no verdict; deferred). The only *behavioral* change: the persisted verdict for a positive value-claim changes from a wrong value (`prohibition_enforced`) to a right one (`value_persisted`).

**Verification.** Deterministic interpreter tests (pure, no DB): positive value-claim passed/failed/errored → the new verdicts (constructed `RunEvidence` or real `author_emission(GroundedPositive(...))` + the data executor with a stub client); property equals match/mismatch → value verdicts; existence still present/absent; **regression** — the negative prohibition + metadata-relationship verdicts unchanged.

---

## D-137 — S6 in-substrate consumer: the read API + the phrasing live-fire + the clustering read surface (Phase 5 slice 2)

**Date:** 2026-06-03
**Substrates affected:** [S6] (interpretation — the read/consumer surface) + a v1 `intelligence/interpretation_phrasing` caller helper. **No migration** — the `phrasing` column + the `llm_enable_interpretation_phrasing` flag already exist (D-117 / migration 050).
**Status:** Active — Phase 5 (S6 interpretation) slice 2, on `phase-13-substrate-6-interpretation`. Makes S6 *readable*: the write-only store gains a read API; the built-but-unfired phrasing primitive fires on a real read path; the dormant clustering reads become a named consumer surface.

S6 persists every run's interpretation (`persist_interpretation`, wired into both S4 run paths) but is **write-only**: `result_store` exposes only `persist_interpretation` + `set_phrasing`. The clustering reads (D-116) and the phrasing `get_or_phrase` (D-117) exist but are fired only from tests (grep: zero production call sites). This slice gives S6 a coherent **read surface** and fires the phrasing live.

**The read API (substrate, pure — no LLM, no v1 DB).** `result_store` gains, alongside the writers:
- `InterpretationRead` — a frozen DTO: the row hydrated (`run_id`, `recipe_id`, `claim_test_id`, `outcome`, `verdict`, `attribution`, `evidence_refs`, `cause`, **`phrasing`**). The read-side projection that, unlike the produce-side `Interpretation`, carries the persisted presentation layer.
- `read_interpretation(session, run_id) -> InterpretationRead | None` — one row by PK.
- `list_interpretations(session, *, recipe_id=None, claim_test_id=None, limit=200) -> list[InterpretationRead]` — bounded (the substrate's hard-cap convention), ordered by `run_id` (deterministic — the table carries no timestamp axis), optionally scoped by recipe / claim.
- `_row_to_interpretation(row) -> Interpretation` — the model hydrator (rebuilds `EvidenceRef`s + `Cause` from `detail` JSONB); feeds the phrasing live-fire. `_row_to_read` builds the DTO. All on the caller-provided tenant-scoped session (isolation by schema — the substrate convention).

**The phrasing live-fire — and the realized seam.** The flag `llm_enable_interpretation_phrasing` (migration 050) lives on **`tenant_agent_settings`, a v1 public-schema table keyed by `tenant_id`** — it is NOT in the alembic tenant chain, so it is **unreachable from the substrate's per-tenant session** (where `s6_interpretations` lives). The plan's literal `read_and_phrase(session, …)` gating on the flag from one substrate session is therefore unsound against the code. **Resolution — flag-as-param:**
- `read_and_phrase(session, run_id, *, tenant_id, api_key, phrasing_enabled) -> InterpretationRead | None` (in v1 `intelligence/interpretation_phrasing.py`, alongside `get_or_phrase` — the LLM stays out of `interpretation/`). Reads the row on the substrate session; when `phrasing_enabled`, hydrates the model (`_row_to_interpretation`) and fires `get_or_phrase` (cache-or-phrase), attaching `.phrasing` to the returned DTO. Best-effort: disabled or a failed phrasing returns the unphrased read.
- `interpretation_phrasing_enabled(db, tenant_id) -> bool` (v1, mirrors `_story_enrichment_enabled`) — reads the flag off the v1 `db`, tolerant (absent row / error → False). The consumer composes the two: resolve the flag on the v1 session, pass the boolean to `read_and_phrase` on the substrate session. This keeps `read_and_phrase` single-session + pure of the settings table, honors "flag-gated" (it never phrases when False), and stays testable in governance without standing up `tenant_agent_settings`.

*(Considered + rejected: a two-session `read_and_phrase(tenant_session, v1_db, …)` reaching the flag itself — couples the helper to both DBs + the v1 settings table for no gain, since the eventual caller already holds both sessions.)*

**The clustering read surface.** Re-export the existing `clustering.py` reads (`cluster_recurring_causes` / `cluster_by_vr` / `cluster_flapping` + their `CauseCluster` / `VrCluster` / `FlappingCluster` dataclasses) and the read API from `primeqa/interpretation/__init__.py` — already-built pure reads become a named, discoverable S6 consumer API. Surface only; no behavior change.

**Boundary.** No migration. No change to `interpret_run` / `attribute_run` / `persist_interpretation` / the run-path wiring. The substrate read API touches no LLM and no v1 DB; the live-fire + the flag-read live in v1 (the allowed v1→substrate direction). **Deferred (tracked):** the user-facing UI/dashboard over substrate runs (Phase-7 cutover — substrate runs ≠ v1 pipeline-runs); folding S6 verdicts into v1's GO/NO-GO; cause attribution for positive/property failures (the *why* — value drift / org change); a standing production consumer wired into a worker tick (the read surface + the gated live-fire land here; the always-on caller is a cutover concern).

**Verification.** Governance-DB integration (the existing S6 harness; `alembic upgrade tenant@head`): persist → `read_interpretation` round-trip (incl. the `cause` rehydration) + `list_interpretations` scoping / bound; `read_and_phrase` with a **stubbed** enricher — `phrasing_enabled=True` → phrasing attached + cached on the row; `False` → unphrased (no `llm_call`); a stubbed failure → unphrased; `interpretation_phrasing_enabled` True / False / absent-row; a clustering read surfaces a recurring cause through the re-exported surface. Reuses the `test_s6_*` patterns. Merge gate: the S6 suites green; no v1-runtime behavior change (additive reads + a flag-gated, currently-uncalled live-fire helper) — inert deploy.

---

## D-138 — Phase 5 close: S6 full-surface verdicts + the in-substrate consumer

**Date:** 2026-06-03
**Substrates affected:** [S6] (interpretation). **No migration** (`verdict` is TEXT; the `phrasing` column + the `llm_enable_interpretation_phrasing` flag pre-exist).
**Status:** Active — Phase 5 (S6 interpretation) close, merging `phase-13-substrate-6-interpretation` → `main`.

Phase 5 brings S6 current with the **realized S4 execution surface** (Phase-3 closed) and makes the write-only store **readable**, across two slices:

- **D-136 — full-surface verdicts.** Fixed the positive value-claim mis-interpretation (`interpret_run` routes **create + assert → `_interpret_positive`**: `value_persisted` / `value_not_persisted` / `not_evaluated`; **create alone → `_interpret_behavioral`**, the prohibition negative, unchanged) + property value precision (`_interpret_inspection` dispatches on `AssertEvidence.predicate`: `equals`/`is_null` → `asserted_value_matches` / `asserted_value_differs`; `exists` → present/absent, unchanged). `Verdict` taxonomy +4; attribution stays evidence-derived. The only behavioral change: a positive value-claim's persisted verdict moves from a wrong value to a right one.
- **D-137 — the in-substrate consumer.** A pure read API (`InterpretationRead` DTO + `read_interpretation` / `list_interpretations` + the row→model/DTO hydrators) re-exported from `interpretation/__init__` alongside the clustering reads as one coherent S6 consumer surface; and the phrasing live-fire (`read_and_phrase` fires `get_or_phrase` on a real read path). Resolved the realized seam — the per-tenant flag `llm_enable_interpretation_phrasing` lives on the **v1 public-schema `tenant_agent_settings`** (unreachable from the substrate session), so `read_and_phrase` takes a resolved `phrasing_enabled` boolean (flag-as-param) and v1's `interpretation_phrasing_enabled` (targeted single-column SELECT, fails closed) supplies it. S6 is no longer write-only.

**Doc currency.** `SPEC.md` — §3 verdict taxonomy grown (+4) + Status "realized through Phase 5". `EVOLUTION.md` — the Phase-5 build-arc entry (D-136 / D-137). `DEFERRED_ITEMS.md` — §2 (read API + phrasing live-fire LANDED; the UI/dashboard consumer + release-grain defer) + §3 (positive value-claim + property value verdicts LANDED; the dormant verticals + positive/property cause attribution defer). `OPEN_QUESTIONS.md` — S6-Q-006 live-fire note.

**Deferred (tracked).** The user-facing UI/dashboard over substrate runs + a standing production consumer wired into a tick (Phase-7 cutover — substrate runs ≠ v1 pipeline-runs); folding S6 verdicts into v1's GO/NO-GO; cause attribution for positive/property failures (the *why* — value drift / org change); the dormant verticals' verdicts (ui/event/callout — no executor emits their evidence); the reviewer edit/version lifecycle (S6-Q-005).

**Merge gate.** A green run of the S6 suites — `test_s6_full_surface` (10) + `test_s6_consumer` (14) + the existing `test_s6_interpret_persist` / `test_s6_clustering` / `test_s6_phrasing` / `test_s6_s1_reader` + the interpreter unit suites (30) — plus the v1 `test_interpretation_phrasing` (7) / `test_s5_knowledge_contract` (12) regression + the v1 app import. **No migration**; the only v1-runtime delta is additive read paths + a currently-uncalled live-fire helper (inert deploy). Merge `phase-13-substrate-6-interpretation` → `main` via PR on green.

---

## D-139 — S8 claim-grounding leg: does the claim's subject still resolve? (Phase 6 slice 1)

**Date:** 2026-06-03
**Substrates affected:** [S8] (the claim-grounding leg — the second built leg of the grounding-validity predicate), reading S1 entity-resolution through S8's own port. **No migration** (pure, produce-only).
**Status:** Active — Phase 6 (S8 evolution) slice 1, on `phase-14-substrate-8-evolution`.

The grounding-validity predicate's recipe-grounding leg (D-113) asks "does the payload still violate?"; the **claim-grounding leg** asks the prior question — **does the claim's subject still resolve in the current org?** It re-asks generation's own resolution step (`resolve_subject` → `SemanticOrgModel.get_entities(type, at_seq, filters={"sf_api_name": external_id})`) against today's S1 active set. A subject that no longer resolves (a renamed/deleted Field or Object) means the test can no longer address what it claims — broken grounding, independent of any VR.

**Realized — verified against the code.** Claim subjects are `IdentityBearingRef(entity_type, external_id, …)` in `asserted_truth` (every kind carries ≥1: value/property/existence/state-transition `subject`, prohibition `target`, metadata-relationship `source`+`target`, capability `granting_subject`+`target`, automation-effect `automation`, layout `layout`+`field`). S1 re-resolves by `(entity_type, external_id)` — the exact key generation used. **By external_id, not entity_id:** a rename supersedes the S1 entity (entity_id persists, external_id changes) and execution addresses the org by api-name (external_id), so the execution-faithful "still resolves" is by-external_id — it catches the rename-break that by-entity_id would mask.

**Shape (mirrors `recipe_grounding.py` exactly — pure fn + S8's own port + adapter):**
- `ClaimGroundingResult(verdict: Literal["intact","broken"], reason, unresolved: tuple[(entity_type, external_id), …])` — **two-valued** (resolution is binary; the value/formula *drift* axes belong to the other legs). `reason="subject_not_resolved"` + `unresolved` are load-bearing on broken (which subject(s) broke).
- `SubjectResolver` Protocol — `resolves(entity_type, external_id) -> bool`. S8's OWN port (parallel-siblings, D-112); the production adapter over `SemanticOrgModel` lands with the composition (slice 3, `s1_reader.py`); slice-1 tests inject a stub.
- `claim_grounding_validity(entity_type, external_id, *, s1)` — resolves → intact; not → broken.
- `claim_grounding_validity_for_claim(asserted_truth: BodyBase, *, s1)` — walks the body for **every** `IdentityBearingRef` (claim-kind-agnostic), resolves each; **broken if any** unresolved (a claim grounded on a deleted Field *or* Object is broken). A local `_identity_bearing_refs` walk mirrors `coverage._walk` (the D-058 §5.4 pattern) projected to (entity_type, external_id) — `coverage.extract_coverage` projects to `entity_id` (the wrong key here), so the walk is re-expressed locally rather than refactoring `coverage.py` (shared-walker extraction = tracked adjacent work).

**Boundary.** `asserted_truth` only (the subject — what the claim is *about*); `semantic_conditions` scoping refs are a noted later extension. No `entity_id`/version-pin check (by-external_id is the execution-faithful resolution). A degenerate claim with no identity-bearing ref → vacuously intact. No migration; produce-only; no composition yet (slice 3). The recipe-grounding leg is untouched.

**Verification.** Pure unit tests (no DB, stub resolver): subject resolves → intact; subject gone → broken/`subject_not_resolved` + `unresolved` populated; adapter extracts + resolves from a prohibition (`target`), a value-claim (`subject` Field, dotted external_id), a capability (two refs, one gone → broken); multi-ref all-resolve → intact. Mirrors `test_recipe_grounding.py`.

---

## D-140 — S8 field-value-validity leg: do the recipe payload's field values still exist? (Phase 6 slice 2)

**Date:** 2026-06-03
**Substrates affected:** [S8] (the field-value-validity leg — the third built leg), reading S1 picklist metadata through S8's own port. **No migration** (pure, produce-only).
**Status:** Active — Phase 6 (S8 evolution) slice 2, on `phase-14-substrate-8-evolution`.

The recipe-grounding leg (D-113) asks "does the payload still violate a VR formula?" — but `evaluate` returns `True` on a removed picklist value (the formula compares fine on the string), so recipe-grounding **cannot** catch a value that Salesforce would now reject for being *invalid*, not for *violating the rule*. The **field-value-validity leg** closes that false-`intact`: **do the recipe payload's field values still exist in the current org?** Each payload picklist value is checked against the field's current active value set.

**Realized — verified against the code.** Picklist *values* are synced: `picklist_value_details` carries `value_api_name` + `is_active`; `SemanticOrgModel.get_picklist_values(pvs_id, at_seq)` enumerates them version-aware (`query.py:374`); Field→set via `field_details.picklist_value_set_entity_id` (1:1 FK), surfaced by `get_entity_details`. So **payload {field: value} → resolve Field → picklist_value_set_entity_id → get_picklist_values → is `value` an active value_api_name?** is fully walkable on realized reads.

**Shape (mirrors `recipe_grounding.py` — pure fn + S8's own port + adapter):**
- `FieldValueGroundingResult(verdict: Literal["intact","broken"], reason, invalid: tuple[(field_api, value), …])` — two-valued; `reason="picklist_value_removed"` + `invalid` are load-bearing on broken (which value(s) no longer exist).
- `PicklistReader` Protocol — `active_values(object_external_id, field_api) -> Optional[frozenset[str]]`: the field's active value_api_names, or **None when the field is not a constrained picklist** (→ the leg skips it — not its concern). S8's own port; the production adapter (Field → set → values) lands in slice 3.
- `field_value_grounding_validity(payload, object_external_id, *, s1)` — per payload key: skip None values + non-picklist fields (port `None`); a present value not in the active set → collected into `invalid`. Broken iff any invalid.
- `field_value_grounding_validity_for_recipe(recipe, *, s1)` — reuses `recipe_grounding._negative_create_step` (a private intra-package import; shared-helper extraction is tracked adjacent work).

**Boundary.** Object-level / behavioral-negative-only v1 (the negative create step's `field_values`), the same scope as recipe-grounding. **Multi-select picklist** (semicolon-delimited) values are a v1 simplification — single-value membership first; the split is a tracked follow-up. Null payload values are skipped (not a membership concern). No migration; produce-only; no composition yet (slice 3).

**Verification.** Pure unit tests (no DB, stub reader): value still active → intact; value removed / inactive → broken/`picklist_value_removed` + `invalid` populated; non-picklist field (port `None`) → skipped/intact; null value → skipped; payload with no picklist fields → intact; adapter extracts from a `DataRecipeBody` + raises without a negative step. Mirrors `test_recipe_grounding.py`.

---

## D-141 — S8 two-level grounding_validity composition: claim-level + recipe-level, composed not collapsed (Phase 6 slice 3)

**Date:** 2026-06-03
**Substrates affected:** [S8] (the grounding-validity predicate's public composition over the three built legs). **No migration** (pure, produce-only).
**Status:** Active — Phase 6 (S8 evolution) slice 3, on `phase-14-substrate-8-evolution`.

The three legs (recipe-grounding D-113, claim-grounding D-139, field-value-validity D-140) judge in isolation; this slice composes them into the predicate SPEC §2 names: `grounding_validity(artifact, current_org) → intact | drifted | broken`, **two-level** (claim-level + recipe-level), **composed never collapsed** (D-112 Fork C — the parts stay individually addressable).

**The artifact + the composed verdict.**
- `Artifact(claim: BodyBase, recipes: tuple[DataRecipeBody, …])` — one claim's `asserted_truth` + its recipes (the realized S2 join: `test_claims` + `test_recipes` by `claim_test_id`).
- `RecipeVerdict(recipe_grounding, field_value, rolled_up)` — per recipe, both recipe-level legs + their roll-up.
- `GroundingValidity(claim_grounding, recipe_verdicts, overall)` — the claim-level leg (one) + the recipe-level verdicts (per recipe) + a derived `overall`, with every part addressable.
- `grounding_validity(artifact, *, subjects, vrs, picklists)` — **three ports, not one** (each leg keeps its own port — parallel-siblings preserved).

**The precedence law (the one new semantic decision).** `broken > drifted > intact`. Per recipe, roll up the two recipe legs by max-severity — so **field-value `broken` un-masks a recipe-grounding `intact`** (the whole point of D-140: the payload still fires a VR, but its picklist value is gone). `overall` = max-severity over the claim-level verdict + every recipe's roll-up — so **claim-level `broken` dominates** (the subject is gone; nothing grounds). claim-level is intact/broken (no drift axis); recipe roll-ups carry the drift axis.

**Mixed-recipe honesty.** The recipe legs are **behavioral-negative-only** (D-113/D-140). An artifact's recipes may include non-negative recipes (positive / inspection); the composition **skips** those for the recipe legs (`_negative_create_step is None` → no `RecipeVerdict`) rather than fabricate a verdict — a non-negative recipe's grounding is the claim-grounding leg + future legs. An artifact with zero negative recipes (e.g. a positive value-claim) → empty `recipe_verdicts`, `overall` = the claim-level verdict.

**Refinement vs the plan — `s1_reader.py` moves to slice 5.** The plan placed the production tri-port adapter (SubjectResolver + VrReader + PicklistReader over one `SemanticOrgModel`, pinning `version_seq` once) in this slice. It is first **used** + governance-testable in slice 5 (the recompute trigger runs the composition against real S1); building it here would ship untested glue. So slice 3 is the **pure composition only** (fully unit-tested with stub ports), and `s1_reader.py` lands in slice 5 with the trigger that exercises it. The composition's three-port signature is the seam the adapter plugs into.

**Boundary.** Pure; produce-only; no persistence (slice 4) and no production reader (slice 5). The three legs are unchanged. No migration.

**Verification.** Pure unit tests (no DB, three stub ports): all-intact artifact → overall intact + parts addressable; claim broken + recipes intact → overall broken, recipe parts still intact (**non-collapse proof**); recipe-grounding intact + field-value broken → recipe rolls up broken (**un-masking proof**); claim intact + one recipe drifts → overall drifted; mixed multi-recipe → per-recipe addressable; artifact with no negative recipe → empty `recipe_verdicts` + overall = claim verdict.

---

## D-142 — S8 recorded-verdict store + read API: s8_grounding_validity (Phase 6 slice 4)

**Date:** 2026-06-03
**Substrates affected:** [S8] (the thin mechanics — a per-tenant recorded-verdict store + read API). **MIGRATION** (alembic tenant `20260603_0030`, down_revision `20260603_0010`).
**Status:** Active — Phase 6 (S8 evolution) slice 4, on `phase-14-substrate-8-evolution`.

The composition (D-141) produces a `GroundingValidity` on demand; this slice persists it + makes it readable — the **thin mechanics** (the D-137 parity S6 just got), the recorded verdict SPEC §6 named as deferrable-but-bounded. **Persist + read only** — NO sync trigger / reverse index / orchestration (those stay fenced; the trigger is slice 5).

**The store (mirrors `interpretation/result_store.py` + the `s6_interpretations` migration).**
- Per-tenant table `s8_grounding_validity` (no `tenant_id` — isolation by schema, the substrate convention). Typed columns: `test_id` (UUID) + `version_seq` (int) — the **claim version** grounded; `evaluated_at_version_seq` (int) — the **S1 seq** the verdict was computed against (the contemporaneous-grounding axis, S8-Q-007, made queryable); `overall` (TEXT verdict) + `claim_verdict` (TEXT). PK `(test_id, version_seq)` — the artifact grain (the verdict is per-claim-version; the per-recipe verdicts live in `detail`). `detail` JSONB — the rich part (claim-grounding reason/unresolved + each recipe's leg verdicts/reasons/invalid + rolled_up). Index on `overall` (the "show me all drifted / broken" query).
- `persist_grounding_validity(session, *, test_id, version_seq, evaluated_at_version_seq, validity)` — an **UPSERT** on `(test_id, version_seq)`: re-grounding the same claim version at a later `evaluated_at_version_seq` **refreshes** the row (the recompute path, slice 5) rather than colliding. add/update + flush, **no commit** (the caller owns the tx — the substrate convention).
- `GroundingValidityRead` DTO + `read_grounding_validity(session, test_id, version_seq)` + `list_grounding_validity(session, *, test_id=None, overall=None, limit=200)` (bounded at 500; ordered by `(test_id, version_seq)` deterministic; optional scope by test or by overall-verdict).

**`overall` / `claim_verdict` are TEXT** (not a PG enum) — the Python `GroundingVerdict` Literal is the source of truth; TEXT avoids an ALTER TYPE and stays queryable (the `s6_interpretations` precedent).

**Honest caveat (the snapshot).** A recorded verdict is a **snapshot** as-of `evaluated_at_version_seq` — nothing refreshes it absent a recompute (the slice-5 trigger). The column makes the staleness queryable (a consumer can spot rows grounded against an older S1 seq). Consistent with the S6 store (a run's interpretation is also a snapshot) — named here so it isn't mistaken for a standing live index.

**Boundary.** Persist + read only. No trigger / reverse index / orchestration (deferred — the heavy mechanics). The composition + legs are unchanged. The store is the only writer; the pure core stays DB-free (`import grounding_validity` pulls no DB).

**Verification.** Governance-DB integration (the `test_representation` `session` fixture; `alembic upgrade tenant@head` applies `20260603_0030`): persist a hand-built `GroundingValidity` → `read_grounding_validity` round-trip (incl. `detail` rehydration + `evaluated_at_version_seq`); the UPSERT — persist twice on one `(test_id, version_seq)` with a later `evaluated_at_version_seq` → one row, refreshed; `list_grounding_validity` scope by test + by `overall` + the bound.

---

## D-143 — S8 S1-sync recompute trigger: the production tri-port reader + the grounding recompute + scheduler wiring (Phase 6 slice 5)

**Date:** 2026-06-03
**Substrates affected:** [S8] (the production S1 read adapter + the recompute orchestrator + the scheduler tick — the live mechanics), reading S1 (`SemanticOrgModel`) + S2 (`SemanticTransactionCoordinator`) and writing the S8 store. **No migration** (freshness is derived from the store — no watermark table).
**Status:** Active — Phase 6 (S8 evolution) slice 5, on `phase-14-substrate-8-evolution`. The last build slice — S8 fires as the org evolves.

The composition (D-141) is pure; slice 4 persists a verdict on demand. This slice makes S8 **fire**: when a tenant's S1 advances, recompute grounding-validity over the tenant's current claims + refresh the store. Three pieces.

**1. The production tri-port reader (`s1_reader.py`, mirrors `interpretation/s1_reader.py`).** `S8S1Reader(model: SemanticOrgModel, *, at_seq=None)` implements all three ports the composition needs, pinning `at_seq` once (default `current_version_seq()`):
- `resolves(entity_type, external_id)` → `bool(get_entities(type, at_seq, {"sf_api_name": external_id}))` (the claim-grounding port).
- `vrs_for_object(external_id)` → the exact S1ValidationRuleReader composition (Object → `get_related(["APPLIES_TO"], "inbound")` → ValidationRule → `get_entity_details` `is_active` + `attributes.formula_text`), returning S8-local `_GroundingVr(is_active, formula_text)` (no `VrMeta` import — parallel-siblings).
- `active_values(object_external_id, field_api)` → resolve the Field by `sf_api_name` (compose `{object}.{field}` when undotted) → `get_entity_details` `picklist_value_set_entity_id` (None → not a picklist → return None) → `get_picklist_values` → frozenset of `value_api_name` where `is_active` (the field-value port).

**2. The recompute (`recompute.py`).**
- `load_current_artifacts(session)` — `SELECT test_id, version_seq FROM test_claims WHERE valid_to IS NULL` (no ready S2 API for "all current claims"); per claim, `coordinator.get_latest_claim` (the `asserted_truth` body) + `list_active_recipes` (the `observation_realization` bodies, **DataRecipeBody only** — the recipe legs are data-recipe-only) → `Artifact`. Returns `[ArtifactRef(test_id, version_seq, artifact)]`.
- `recompute_grounding(session, artifact_refs, *, s1, at_seq, cap=100)` — the testable orchestration: per ref, **freshness** = a store row at `(test_id, version_seq)` with `evaluated_at_version_seq == at_seq`; fresh → skip; stale/absent → `grounding_validity(artifact, subjects=s1, vrs=s1, picklists=s1)` + `persist_grounding_validity(evaluated_at_version_seq=at_seq)`, up to `cap` groundings; returns `RecomputeResult(grounded, skipped_fresh, remaining)` (remaining = stale refs left past the cap — logged, never silently dropped).
- `recompute_tenant_grounding(tenant_id, *, cap=100)` — the production wrapper: `get_tenant_connection` → `SemanticOrgModel(conn)` → `at_seq = current_version_seq()` (a tenant with no S1 versions raises `VersionNotFoundError` → 0, skipped) → `S8S1Reader(model, at_seq=at_seq)` + `Session(bind=conn)` → `load_current_artifacts` → `recompute_grounding`. The connection context owns the commit.
- `run_s8_grounding_tick(tenant_ids, *, cap=100)` → `{tid: grounded}` with per-tenant try/except (mirrors `run_s3_reaper_tick`).

**3. Scheduler wiring (`scheduler.py`).** `s8_grounding_tick(ctx)` folded into `scheduler_tick` (after `s4_reaper_tick`): enumerate tenants from `shared.tenants`, call `run_s8_grounding_tick`, log the total. Mirrors `s3_reaper_tick`.

**No watermark table (refinement vs the plan).** The plan proposed a per-tenant `s8_grounding_watermark`. **Freshness is instead derived from the store** — a claim is fresh iff its verdict's `evaluated_at_version_seq` equals the current S1 seq. This needs **no new table/migration**, and is **cap-correct**: a partial recompute (cap hit) leaves stale claims un-fresh, so the next tick resumes them (a global watermark would wrongly mark "done"). When S1 advances, every claim's verdict is stale → re-grounded (recompute-all).

**Deferred (the genuine remaining heavy mechanics).** The **change→impact reverse index** (narrow the recompute to claims actually hit by *this* S1 change — recompute-all is correct, just unoptimized); **re-grounding orchestration + supersession execution** (re-deriving payloads + authoring new identity-preserving versions — the large artifact-mutation body, gated on the autonomy boundary S8-Q-006); the per-artifact **job queue** (the freshness+inline approach is the simpler correct v1). All fenced.

**Verification.** Governance-DB integration: **(a)** `S8S1Reader` against seeded S1 (an Object + an active VR with `formula_text` + `APPLIES_TO` + details; a picklist Field + value set + values) → `resolves` true/false, `vrs_for_object` returns the VR (is_active + formula), `active_values` returns the active set / None for a non-picklist (mirrors `test_s6_s1_reader`); **(b)** `recompute_grounding` over **injected** `ArtifactRef`s + a stub tri-port (no S1/S2 seed) → grounds + persists; a fresh row → skipped (no re-persist); a stale row (older seq) → re-grounded; `cap=1` over 2 stale → 1 grounded + 1 remaining; **(c)** `load_current_artifacts` decode — seed one claim + a data-recipe (via the coordinator) → returns the `ArtifactRef` with the decoded `Artifact`.

---

## D-144 — Phase 6 close: S8 evolution — predicate legs + the in-substrate mechanics

**Date:** 2026-06-03
**Substrates affected:** [S8] (evolution). **One migration** (alembic tenant `20260603_0030`).
**Status:** Active — Phase 6 (S8 evolution) close, merging `phase-14-substrate-8-evolution` → `main`.

Phase 6 completes the grounding-validity predicate over the **realized** S1/S2/S3 surface, persists its verdicts, and wires it to **fire** as the org evolves — across five slices:

- **D-139 (claim-grounding leg).** Does the subject still resolve? Re-resolve every `IdentityBearingRef` by `sf_api_name` (external_id, rename-faithful) through S8's own `SubjectResolver` port. Pure.
- **D-140 (field-value-validity leg).** Do the payload values still exist? Closes recipe-grounding's removed-picklist-value false-`intact` via the `PicklistReader` port. Pure.
- **D-141 (two-level composition).** `grounding_validity` composes the three legs claim-level + per-recipe, composed never collapsed (`broken` > `drifted` > `intact`; field-value `broken` un-masks a recipe-grounding `intact`). Pure.
- **D-142 (recorded-verdict store).** Per-tenant `s8_grounding_validity` (migration `20260603_0030`) + UPSERT persist + read/list. The thin mechanics.
- **D-143 (S1-sync recompute trigger).** `S8S1Reader` tri-port + `recompute_grounding` (freshness off the store — no watermark table, cap-correct) + scheduler `s8_grounding_tick`. S8 fires on S1 advance (recompute-all).

**Doc currency.** `SPEC.md` — §2 leg table (3 legs realized, admissibility deferred) + the composition / store / trigger marked realized + Status realized-through-Phase-6; §6 fence annotated (trigger + recorded-verdict landed thin). `EVOLUTION.md` — the Phase-6 build-arc entry. `DEFERRED_ITEMS.md` — §1 (trigger / verdict thin; reverse-index + queue still fenced) + §3 (legs landed; admissibility S3-blocked; VR-pin / multi-select still deferred). `OPEN_QUESTIONS.md` — S8-Q-007 (the snapshot axis `evaluated_at_version_seq`). `GLOSSARY.md` — +5 terms.

**Deferred (tracked).** The admissibility leg (S3-blocked — the synthesis→intent contract, S8-Q-004); the change→impact reverse index (recompute-all is correct, just unoptimized); re-grounding orchestration + supersession execution (the artifact-mutation body, autonomy-gated, S8-Q-006); the generation-side VR-pin (an S3-emission change); the held NonEvaluable-symmetry pass; the multi-select picklist value split.

**Merge gate.** The S8 suites — `test_recipe_grounding` + `test_claim_grounding` + `test_field_value_grounding` + `test_grounding_validity` (37 unit) + `test_s8_s1_reader` (6) + `test_s8_grounding_store` (6) + `test_s8_recompute` (5) — green; app + scheduler import OK. **One migration** (alembic tenant `20260603_0030`); the substrate stays off the v1 request path (the only runtime delta is a best-effort scheduler tick), so the v1 deploy is inert. Merge `phase-14-substrate-8-evolution` → `main` via PR on green.

---

## D-145 — Phase 7 opened: the greenfield cutover, authored docs-led

**Date:** 2026-06-03
**Substrates affected:** [cutover] (cross-substrate — the v1→substrate migration program). **No code, no migration — docs only.**
**Status:** Active — Phase 7 (greenfield cutover) open, on `phase-15-greenfield-cutover`. Docs-led (mirrors S5's D-134 "ratify + consolidate, docs-led").

"Phase 7 — greenfield cutover + meta_* drop" is the milestone where the substrate spine (S1–S8) replaces the live v1 product and the v1 `meta_*` tables are dropped. **Verified against the code, it is not executable as one phase today, and it is undesigned** — the intent is scattered across D-012 / D-065 / D-074 / D-111 / D-134 with no consolidated cutover doc:

- `meta_*` is still the **live metadata store** the v1 product reads (generation context, the validator's CRUDQ, preflight staleness, the metadata UI) — dropping it now breaks production.
- The S1 sync writer exists (`primeqa/sync/` — `SyncEngine.run_sync`, materialize/phases/edge_specs) but **no production trigger** runs the full Salesforce→`entities` materialization, and there is **no `meta_*`→S1 migration**.
- The substrate outputs (S3/S4/S6/S8) are **not surfaced in the product UI** (S6/S8 are write-only, each deferring its consumer "to the Phase-7 cutover").

So the cutover is a multi-month, multi-part program with a hard prerequisite (S1-sync proven in prod) and a single irreversible final step (the `meta_*` drop) — **not one executable phase.**

**The chosen fork — open docs-led (mirrors S5's D-134).** Rather than barrel into a wholesale cutover (or drop `meta_*` against the premise-break), Phase 7 opens by **authoring the missing cutover design + sequencing it** into a gated, executable program. Zero production risk; the cutover then runs slice-by-slice off the SPEC in later phases. *(Considered + rejected: (b) the safe prep slices now — relocations + additive consumer wiring — premature without the sequence that orders them; (c) tackle the S1-sync prod blocker first — a substantial build the SPEC must scope before it is built; (d) defer the cutover + build S7 — leaves the program's largest risk undesigned. The design is the prerequisite for all three.)*

**The deliverable: `docs/architecture/greenfield_cutover/`.** `SPEC.md` (slice 1) — the v1-module→substrate **disposition map** consolidated from the logged decisions (each row D-cited), the **migration strategy**, the **schema-topology resolution**, the **non-goals**. `SEQUENCE.md` + `EVOLUTION.md` (slice 2) — the **ordered, gated cutover steps** (the `meta_*` drop strictly last; each step entry/exit-gated + a rollback) + the **consolidated Phase-7 work-list** (every "deferred to the cutover" item across the substrate docs, folded into the sequence).

**Two consolidations the SPEC fixes (grounded in the primary entries, not new policy):**
- **Migration strategy = greenfield re-sync, NOT a `meta_*`→S1 backfill** (D-012: "the new Substrate 1 sync engine is greenfield, not bridged"; `meta_*` dropped in one migration "once S1 is verified as the production data source"). `meta_*` is kept read-only through a parallel-run window, dropped last.
- **Terminology reconciliation:** D-012's "Phase 4 cutover" (the product-roadmap phase where S1 replaces `meta_*`) and "the Phase-7 greenfield cutover" (the engineering-phase counter) name the **same event**. The SPEC adopts "greenfield cutover" and notes the alias.

**Boundary.** Docs only — **no code, no migration, no v1 behavior change** (inert deploy). The cutover *execution* (the S1-sync prod trigger, the re-sync + `meta_*` drop, the UI consumers, the S5 relocation, the S3 ledger retirement) is deferred to later phases, gated by this SPEC. The MIGRATE tables (`test_suites` / `sections` / `suite_test_cases` / `ba_reviews`) stay **out of the cutover entirely** — post-cutover future substrates (D-065). DECISIONS_LOG append-only; AK author / zero `Co-Authored-By`.

**Verification.** Docs-only — internal consistency + fidelity to the logged decisions: every disposition-map row cites a real D-entry that says what the row claims; the `meta_*` drop is strictly last + gated; no code touched (the suite is inert; `primeqa.app` imports). Merge `phase-15-greenfield-cutover` → `main` via PR at close (D-147).

---

## D-146 — The greenfield cutover sequencing law: gated steps, the `meta_*` drop strictly last

**Date:** 2026-06-03
**Substrates affected:** [cutover]. **Docs only.**
**Status:** Active — Phase 7 (greenfield cutover) slice 2, on `phase-15-greenfield-cutover`.

The SPEC (D-145) fixed the cutover's target state (the disposition map + the greenfield re-sync strategy). This entry fixes the **sequencing law**: the cutover runs as **ordered, gated steps** — each with an entry-gate (what must hold), an exit-gate (what proves it done), and a rollback — with **one irreversible step, the `meta_*` drop, strictly last**.

**The ordering principle — reversible-before-irreversible, additive-before-substitutive:**
0. **S1-sync prod trigger** — fire `primeqa/sync/` against live Salesforce per tenant; populate `entities`/`edges` on a cadence. The hard prerequisite; additive (v1 still reads `meta_*`). Rollback: trivial.
1. **Relocations (zero-risk)** — `primeqa/intelligence/knowledge/` → `primeqa/knowledge/` (+ `feedback_rules`); a pure move + import updates. Independent of step 0. Rollback: revert.
2. **Additive substrate consumers** — surface S6 interpretations / S8 grounding verdicts / S3+S4 outputs in **additive** UI (no v1 removal). Rollback: hide.
3. **v1 read-path switch (flagged)** — generation context / validator CRUDQ / preflight read S1, behind per-tenant flags, `meta_*` still populated. Rollback: flip the flag.
4. **Parallel-run validation** — both stacks; S1-sourced == `meta_*`-sourced over a window; fold S6 verdicts into GO/NO-GO; retire the S3 ledger into S2 provenance (D-074). Rollback: extend / revert.
5. **The `meta_*` drop (LAST, irreversible)** — drop `public.meta_*` + the DROP tables (D-065) in one migration; remove the v1 metadata module + the flags. **Entry-gate: a clean parallel-run window + S1 verified as the production data source (D-012). Rollback: none past here — the gate IS the safety.**

**The consolidated work-list.** Every "deferred to the cutover" item across the substrate docs is folded into the step it belongs to (the SEQUENCE carries the full mapping): the S1-sync trigger → step 0; the S5 relocation + `feedback_rules` move → step 1; the S6 UI consumer + clustering dashboard + the S8 verdict surface + the S5→S3 forward-seam (the "v1-vs-substrate generation direction settles at the cutover") → step 2; the v1 read-path switch → step 3; the GO/NO-GO folding + the S3-ledger retirement → step 4; the `meta_*` re-sync completion + drop → step 5. So the ~dozen scattered deferrals (S5 D-134; S6 D-137 / D-111; S3 D-074) become one tracked checklist.

**Invariant.** No step that removes a v1 read-path or drops a table runs before its substrate replacement is proven *additive → flagged → parity-validated*. The `meta_*` drop is the only irreversible step and is gated on the full parallel-run window. **Docs only** — this is the order the execution phases follow; nothing is built here.

**Verification.** Internal consistency: every substrate `DEFERRED_ITEMS.md` "Phase-7 / cutover" item has a home in `SEQUENCE.md`; the step order is reversible-before-irreversible with `meta_*` drop strictly last + gated. Append-only.

---

## D-147 — Phase 7 close: the greenfield cutover, designed + sequenced (docs-led)

**Date:** 2026-06-03
**Substrates affected:** [cutover]. **Docs only — no code, no migration, no v1 behavior change.**
**Status:** Active — Phase 7 (greenfield cutover) close, merging `phase-15-greenfield-cutover` → `main`.

Phase 7 opened the greenfield cutover **docs-led** and authored its missing design — turning a loosely-defined, multi-month, production-touching program into an executable gated sequence, without touching production. Two slices:

- **D-145 (open + SPEC).** The premise-break recorded (not executable as one phase: `meta_*` is the live store; no S1-sync prod trigger; no `meta_*`→S1 migration; no substrate UI consumers) + the docs-led fork chosen. `greenfield_cutover/SPEC.md` — the v1→substrate disposition map (D-cited), the greenfield re-sync migration strategy (not backfill, D-012), the realized per-tenant schema topology (D-015 built; D-023 superseded), the non-goals.
- **D-146 (SEQUENCE).** The sequencing law — ordered, gated steps (reversible-before-irreversible) with the `meta_*` drop strictly last + gated. `greenfield_cutover/SEQUENCE.md` (the gated checklist + the coverage table folding every cutover deferral into its step) + `EVOLUTION.md` + reciprocal SEQUENCE pointers in the S5/S6 `DEFERRED_ITEMS.md`.

**The outcome.** The cutover is now a single tracked, gated program — 6 steps (S1-sync prod trigger → relocations → additive consumers → flagged read-switch → parallel-run validation → `meta_*` drop), each with entry/exit gates + rollback, every scattered deferral (S5/S6/S3) given a home. Execution is later phases, one gated step each.

**Deferred (the cutover *execution* — gated by this design).** Building each step: the S1-sync prod trigger; the S5 relocation; the substrate UI consumers + GO/NO-GO folding; the v1 read-path switch; the parallel run; the `meta_*` drop. The MIGRATE tables stay out of the cutover (post-cutover future substrates, D-065).

**Merge gate.** Docs only — no code, no migration, no v1 behavior change. Internal consistency verified: every disposition-map row + SEQUENCE step cites a real D-entry; every substrate Phase-7 deferral has a SEQUENCE home (reciprocal pointers); the `meta_*` drop is strictly last + gated; DECISIONS_LOG + EVOLUTION append-only; `primeqa.app` imports (inert deploy). Merge `phase-15-greenfield-cutover` → `main` via PR.

---

## D-148 — Cutover Step 1: relocate S5 to `primeqa/knowledge/` (`feedback_rules` stays)

**Date:** 2026-06-03
**Substrates affected:** [S5] (physical relocation) + the v1 import graph (`generation.py`, `test_plan_generation.py`). No migration; a **pure refactor, no behavior change**.
**Status:** Active — greenfield-cutover **SEQUENCE Step 1**, on `phase-16-cutover-step1-relocation`. Executes the relocation D-134 deferred to the cutover; the first cutover-*execution* phase (the zero-risk, independent step).

S5 alone lived under the v1 `intelligence/` tree — every other substrate is top-level (S1 `semantic/`, S2 `test_representation/`, S3 `generation/`, S4 `execution_engine/`, S6 `interpretation/`, S8 `evolution/`). The cutover's first step gives S5 its top-level home (the anomaly D-134 flagged).

**The move.** `primeqa/intelligence/knowledge/` → `primeqa/knowledge/` (`git mv`, history-preserving; all 7 files). Every `primeqa.intelligence.knowledge` reference rewrites to `primeqa.knowledge` — **3 internal** (the package's absolute self-imports in `__init__.py` / `domain_packs.py` / `domain_pack_provider.py` + the `__init__` usage-docstrings) + **6 external importers** (`intelligence/generation.py`, `intelligence/llm/prompts/test_plan_generation.py`, and `tests/test_{domain_packs,generation_quality_gate,knowledge_architecture,s5_knowledge_contract}.py`), 23 lines. The `salesforce_domain_packs/` dir is a passed-in `packs_dir` (env-defaulted at `generation.py`), **not** package-relative — unaffected.

**The sub-decision D-134 left open — `feedback_rules.py` STAYS in `intelligence/llm/`.** It is the LLM feedback-aggregation machinery: imported by the gateway / dashboard / feedback layer + v1 (`views.py`, `worker.py`, `test_management/service.py`) — 13 importers. The knowledge package consumes it only through the `LearnedRulesProvider` adapter (`learned_rules.py` → `from primeqa.intelligence.llm import feedback_rules`), the clean port seam. Relocating it would drag the LLM-feedback dependency graph + churn 13 importers for no architectural gain; the wrap-via-provider is the realized design (D-134). So only the knowledge package moves; the one `knowledge → intelligence.llm.feedback_rules` import stays as a cross-package consume (the allowed direction — the port wrapping a v1 source).

**Boundary.** A pure relocation — no behavior change, no migration, no public-API change (the `primeqa.knowledge` surface is identical to the old `primeqa.intelligence.knowledge` surface; only the module path differs). **Merge gate:** the knowledge / generation / LLM suites green (`test_s5_knowledge_contract`, `test_domain_packs`, `test_knowledge_architecture`, `test_generation_quality_gate`, `test_llm_architecture`) + `primeqa.app` import. S5 doc currency (mark the relocation landed; SEQUENCE Step 1 done) + the close land with the merge (D-149).

---

## D-149 — Cutover Step 1 close: S5 relocated to `primeqa/knowledge/`

**Date:** 2026-06-03
**Substrates affected:** [S5] (relocated) + the v1 import graph. No migration; a pure refactor.
**Status:** Active — greenfield-cutover Step 1 close, merging `phase-16-cutover-step1-relocation` → `main`. Executes + closes D-148 (the relocation D-134 deferred to the cutover).

The cutover's first *executed* step (the zero-risk, independent relocation). `primeqa/intelligence/knowledge/` → top-level `primeqa/knowledge/` (`git mv`, renames preserved; 7 files) + the import-graph rewrite across the 6 importers + the package self-imports. **`feedback_rules.py` stayed** in `intelligence/llm/` — D-134's open sub-decision resolved: it is LLM-feedback machinery with 13 importers (gateway / dashboard / views / worker / test_management); the knowledge package consumes it only through the `LearnedRulesProvider` adapter, so relocating it would churn the dependency graph for no gain.

**A regression caught + fixed by the verify gate.** `system_rules.py` resolved its default `salesforce_knowledge/system_rules.json` path by `__file__`-relative parent navigation (`..`×3 — correct from the 3-deep `intelligence/knowledge/` home); the up-one-level move made it overshoot the repo root → an empty rule list. The pre-commit suite surfaced it (3 `test_knowledge_architecture` failures), it was root-caused + fixed to `..`×2. The discipline (run before commit) earned its keep.

**Doc currency.** S5 `SPEC.md` (location → `primeqa/knowledge/`; relocation moved from Deferred → LANDED) + `DEFERRED_ITEMS.md` (the relocation bullet → LANDED). The cutover `SEQUENCE.md` Step 1 + coverage row marked ✅ done; `SPEC.md` spine list updated; `EVOLUTION.md` Step-1 entry appended.

**Merge gate.** The relocation-relevant suites green — `test_s5_knowledge_contract` + `test_domain_packs` + `test_knowledge_architecture` + `test_generation_quality_gate` (29 passed) — + `primeqa.app` import. `test_llm_architecture`'s 12 failures are **pre-existing environmental** (`JWT_SECRET` / DB unset in this shell; proven identical on clean `main` via a stash run), not relocation-caused. No migration; the only runtime delta is the module path. Merge `phase-16-cutover-step1-relocation` → `main` via PR.

---

## D-150 — Cutover Step 0 / S0.1: the S1-sync provisioning + SF-client resolution seam

**Date:** 2026-06-04
**Substrates affected:** [S1] (the sync trigger's credential + provisioning seam) + an additive `connected_orgs.environment_id` column + an additive `access_token` param on `integrations/sf_client.SalesforceClient`. **MIGRATION** (tenant `20260604_0010`, chains onto head `20260603_0030`).
**Status:** Active — greenfield-cutover **Step 0, slice S0.1**, on `phase-17-cutover-step0-s1-sync-trigger`. Cutover SEQUENCE Step 0 (the hard unblocker); the engine is finished-but-dormant (`run_sync`, zero prod callers) — this phase wires the trigger.

S0.1 builds the two seams the trigger needs: a **Salesforce client per environment** + a **sync target** (`connected_orgs`).

**The SF-client seam — bridge v1's auth to the engine's client (the resolved grant mismatch).** The engine's client (`integrations/sf_client.SalesforceClient(instance_url, client_id, client_secret, refresh_token)`) is **refresh-token-grant only** (`_refresh_access_token` posts `grant_type=refresh_token`). But v1's connections authenticate via **client_credentials / password** (`metadata.worker_runner._oauth_token` → a bare `access_token`; the decrypted config carries no `refresh_token`). So "build the client from the connection 4-tuple" (the agent's first lean) does **not** work. **Resolution:** reuse v1's `_oauth_token` (env → connection → `access_token`, exactly S4's `credentials.py` path) and **pre-seed** the engine's client with that token — via a new **additive, backward-compatible `access_token: str | None = None`** param on `SalesforceClient.__init__` (`self._access_token = access_token`; when set, `_ensure_access_token` skips the refresh exchange). So `resolve_sync_sf_client(db, environment_id)` reuses ALL of v1's credential machinery + the engine's transport, both untouched. A mid-sync 401 (access token expiry past ~2h, beyond a ~30-min sync) → `SFAuthError` → the job fails + retries with a fresh token (acceptable v1 behaviour; noted). **This removes the agent-flagged "verify-against-connection-configs" HOLD** — any connection v1 metadata-sync can authenticate, S1 sync can too (the same `_oauth_token`).

**The provisioning seam — `environment → connected_org` (none exists).** `connected_orgs` (the sync target) carries `sf_instance_url` + oauth stubs but no `client_id/secret`, is **never INSERTed in prod**, and has no link to `environments`. S0.1 adds `connected_orgs.environment_id` (a nullable loose int → `environments.id`; no cross-schema FK — the substrate convention) + `ensure_connected_org_for_environment(conn, environment_id, sf_instance_url) -> connected_org_id` (idempotent upsert keyed on `environment_id`). The sync *targets* a connected_org; the *environment* is the credential source.

**Shape.** New `primeqa/sync/credentials.py` — `resolve_sync_sf_client(db, environment_id) -> SalesforceClient` (mirrors `execution_engine/credentials.py`; raises a sync-local `CredentialResolutionError` on missing env/connection) + `ensure_connected_org_for_environment(...)`. Additive: `connected_orgs.environment_id` (migration `20260604_0010`) + the `access_token` param on `integrations/sf_client.SalesforceClient`. **No engine/phase edits.**

**Verification.** Governance DB + stubbed creds: `ensure_connected_org_for_environment` creates then returns the same row (idempotent); `resolve_sync_sf_client` builds a token-seeded client from a stubbed connection (monkeypatch `_oauth_token` + `get_connection_decrypted`); missing env/connection → `CredentialResolutionError`; the `access_token`-seeded `SalesforceClient` skips `_refresh_access_token`. Plus `import primeqa.sync.credentials` + `primeqa.integrations.sf_client` (backward-compat: default `access_token=None` keeps the refresh path).

---

## D-151 — Cutover Step 0 / S0.2: the `s1_sync_jobs` queue + `SyncJobStore`

**Date:** 2026-06-04
**Substrates affected:** [S1] (the sync trigger's job queue). **MIGRATION** (tenant `20260604_0020`, chains onto S0.1's `20260604_0010`).
**Status:** Active — greenfield-cutover **Step 0, slice S0.2**, on `phase-17-cutover-step0-s1-sync-trigger`. Builds the durable async-work record wrapping `SyncEngine.run_sync` — the queue the S0.3 consumer drains.

`run_sync` is long (~30 min live), so the production trigger is a **per-tenant job queue + worker consumer** (the `s4_execution_jobs` pattern, D-130), not an inline scheduler tick. S0.2 builds the queue + its repository; S0.3 the consumer; S0.4 the scheduler enqueuer + reaper wiring.

**A near-mechanical mirror of `s4_execution_jobs` (D-130) — with one divergence.** Per-tenant schema (unqualified, **no `tenant_id` column** — isolation by schema), `SELECT … FOR UPDATE SKIP LOCKED` claim, partial-unique on the active set. **The one divergence: no attempts child table.** S4 carries `s4_execution_job_attempts` (one row per `run_recipe_execution_async`, a fresh `request_id` per attempt). S1's engine already writes its **own** per-run history to `sync_runs`; the job's **`last_sync_run_id`** links the current/last run — the resume anchor for `run_sync(resume_sync_run_id=…)`. A second attempts table would duplicate `sync_runs`, so the job keeps a single `last_sync_run_id` pointer instead.

**Idempotency — the S4 repeatable shape (not S3's "one job ever").** Partial-unique `UNIQUE (connected_org_id) WHERE status IN ('queued','claimed','running')` — one active sync per connected_org, a fresh job allowed once the prior is terminal (re-sync as the org evolves). Keyed on **`connected_org_id` alone** (not `(connected_org_id, environment_id)`, where S4 keys `(test_id, environment_id)`): the connected_org **is** the sync-target identity — one org, one active sync — and `environment_id` rides alongside as the credential source the consumer resolves (D-150), not part of the dedup key.

**The lifecycle** (`SyncJobStore`, mirroring `ExecutionJobStore`): `create_or_get_job(connected_org_id, environment_id)` (get-or-create the active job — `ON CONFLICT … WHERE active DO NOTHING` then SELECT; race-safe) → `claim_next_queued_job()` (`FOR UPDATE SKIP LOCKED`, queued→claimed) → `mark_running(job_id)` (→running + bump `attempt_count`; S4's `start_attempt` minus the attempts-row insert) → `set_sync_run(job_id, sync_run_id)` (link the engine's run — the resume anchor) → `heartbeat` → `complete` / `fail` (the latter guarded `status NOT IN terminal` — reaper-race-safe) → `reap_stale_jobs(stale_minutes=45)`.

**The reaper timeout — 45 min (generous by design).** It must **exceed the longest legitimate sync** (~30 min live), or the reaper would kill a healthy run; `reap_stale_jobs` fails jobs whose `COALESCE(heartbeat_at, claimed_at)` is older than the threshold. On a stale-fail, **`last_sync_run_id` survives** (the `fail` path never clears it) → a fresh `create_or_get_job` + the consumer's resume picks up that `sync_run` rather than restarting from phase 1.

**Shape.** `s1_sync_jobs` (migration `20260604_0020`, chains onto S0.1's `20260604_0010`) + `SyncJobStore` (new `primeqa/sync/jobs.py`). Additive — a new table + a new module; **no engine/phase edits**, no v1 change.

**Verification.** Governance DB (stubbed — no SF): the job lifecycle (create → claim → mark_running → heartbeat → complete, statuses + timestamps progressing); active-set idempotency (a second `create_or_get_job` returns the **same** active job; a fresh one only after the prior is terminal); the reaper fails a stale `running` job **and preserves `last_sync_run_id`** (resumable) while leaving a fresh/heartbeating job untouched. Plus `import primeqa.sync.jobs`.

---

## D-152 — Cutover Step 0 / S0.3: the S1-sync consumer (resume-on-reap)

**Date:** 2026-06-04
**Substrates affected:** [S1] (the sync trigger's consumer) + an extension to D-151's `SyncJobStore.create_or_get_job` (resume carry-forward). **No migration.** Additive new `primeqa/sync/consumer.py`; no engine/phase/v1 edits.
**Status:** Active — greenfield-cutover **Step 0, slice S0.3**, on `phase-17-cutover-step0-s1-sync-trigger`. The worker loop that drains the D-151 queue: claim → resolve creds → `run_sync` → complete/fail.

**The consumer — mirrors `generation/consumer.py`, with two engine-driven divergences.** `process_sync_job_for_tenant(tenant_id, *, sf_client_resolver, engine_factory)` claims one job (`SKIP LOCKED`) → `mark_running` + one heartbeat → resolves a `SalesforceClient` via the **injected** `sf_client_resolver` (the generation `api_key_resolver` pattern — keeps the substrate consumer decoupled from the v1 connection store + stub-testable; production wires `resolve_sync_sf_client` (D-150) inside a v1 session) → builds `SyncEngine(get_engine(), sf, _resolve_schema_name(tid))` via an **injected** `engine_factory` (default the real engine; tests inject a fake) → `run_sync(connected_org_id, resume_sync_run_id=job.last_sync_run_id)`. `run_s1_sync_consumer_tick(tenant_ids, *, sf_client_resolver, engine_factory)` runs one job per tenant with per-tenant try/except isolation (the resilient-tick pattern), returning `{tid: processed:<id> | empty | error:<Type>}`.

**Divergence 1 — complete/fail reads `sync_runs.status`, not raise-vs-return.** Unlike `run_generation` (which raises on failure), the engine **captures phase failure in the `sync_run` row and returns the id regardless** (only fatal infra — `SyncEngineError` — raises). So the consumer's terminal decision is: `run_sync` **raises** → `fail(error_code='sync_engine_error')`; else read `sync_runs.status` for the returned id — `'failure'` → `fail(error_code='sync_failed', error_message=<the row's message>)`; `'running'/'success'/'partial_success'` → `set_sync_run` (provenance) + `complete`. (`'running'` is a structural-complete success: the engine leaves status `running` / phase `enrichment` for the separate enrichment worker — the job's unit is the **structural** sync, not enrichment.)

**Divergence 2 — resume-on-reap via the durable `sync_run` (realizing D-151, per the resume-now decision).** The engine exposes `run_sync(resume_sync_run_id=…)`, which reads `last_completed_phase` and continues from the next phase. To make a reaped sync resume, D-152 extends **`create_or_get_job`**: a newly-created job's `last_sync_run_id` is **seeded from the org's most-recent incomplete `sync_run`** (`status NOT IN ('success','partial_success') AND last_completed_phase IS DISTINCT FROM 'Flow'`), read directly from `sync_runs`. The `sync_run` row — not a mid-run-captured job column — is the durable resume source of truth (cleaner than the daemon-capture floated at the fork; identical outcome, because there is at most **one** incomplete `sync_run` per org: resume continues the same run rather than forking a new one). The flow: worker dies mid-sync → reaper fails the job (D-151) → re-enqueue → `create_or_get_job` finds the incomplete `sync_run` → the new job carries its id → the consumer passes it to `run_sync` → the sync **resumes from `last_completed_phase`** (same logical_version). On structural completion the `sync_run` is no longer incomplete → the next enqueue is fresh.

**Heartbeat — a single beat + the resume safety net (periodic deferred).** The engine has **no progress hook** (its phase loop calls back nothing), so periodic heartbeats would need a daemon thread. Because resume now makes a reap **cheap** (a spuriously-reaped long sync simply resumes), v1 keeps the generation pattern — **one heartbeat at `mark_running`** + the generous 45-min reaper window (D-151, > the ~30-min sync) — and relies on resume to recover the rare over-window sync. The daemon-thread periodic heartbeat is a noted optimization, deferred (its only value, avoiding spurious-reap churn, is now low-stakes).

**Shape.** New `primeqa/sync/consumer.py` (`process_sync_job_for_tenant`, `run_s1_sync_consumer_tick`, a thin `_classify_error`) + the `create_or_get_job` carry-forward extension in `primeqa/sync/jobs.py`. No migration; no engine/phase/v1 edits.

**Verification.** Governance DB + an injected fake engine (writing controllable `sync_runs` rows) + a stub `sf_client_resolver`: the consumer claims → runs → `complete` with `last_sync_run_id` set; a `sync_run` left `status='failure'` → job `failed` (`sync_failed` + the row's message); `run_sync` raising → job `failed` (`sync_engine_error`); a job carrying `last_sync_run_id` → `run_sync` receives it as `resume_sync_run_id`; **carry-forward** — an incomplete `sync_run` for an org → the next `create_or_get_job` seeds the new job's anchor (a complete one → fresh NULL); per-tenant tick isolation (a resolver that raises → `error:<Type>`, others unaffected) + empty-queue → `empty`. The **live-SF run** (real `run_sync` → entities/edges/`sync_runs`/`ai_enrichment_queue` rows) stays the ops-deferred sandbox e2e (`test_e2e_sync_scenarios.py`).

---

## D-153 — Cutover Step 0 / S0.4: the enqueuer + scheduler/worker wiring

**Date:** 2026-06-04
**Substrates affected:** [S1] (the sync trigger's cadence + service wiring) — additive `run_s1_sync_{enqueuer,reaper}_tick` in `primeqa/sync/consumer.py` + thin `s1_sync_enqueuer_tick`/`s1_sync_reaper_tick` in `primeqa/scheduler.py` + `s1_sync_tick` + `_default_s1_sf_client_resolver` in `primeqa/worker.py`. **No migration; no engine/phase edits.**
**Status:** Active — greenfield-cutover **Step 0, slice S0.4** (the last build slice), on `phase-17-cutover-step0-s1-sync-trigger`. Closes the loop: enqueue (cadence) → consume (D-152) → reap (D-151). After this, ops points it at a real org and S1 goes live.

S0.4 wires the dormant engine's trigger into the running services — the cadence that creates jobs + the scheduler/worker ticks that drain + reap them.

**The enqueuer — the cadence (`run_s1_sync_enqueuer_tick`).** Per tenant (isolated), find `connected_orgs` needing a (re)sync → `create_or_get_job` (idempotency dedups). The **needs-sync policy**: an org with `environment_id` set (provisioned, D-150) **and no active job** is enqueued iff EITHER it has an **incomplete `sync_run`** (`status NOT IN ('success','partial_success') AND last_completed_phase IS DISTINCT FROM 'Flow'` — the reaped/failed case → **resume promptly**, per the resume-now decision) OR **no `sync_run` started within `resync_interval_hours`** (default **24 h** — never-synced or stale → a fresh re-sync to catch metadata drift). Idempotency means a fresh-complete org (synced < 24 h, no incomplete run) is skipped, and an in-flight org (active job) is skipped — so the tick never piles up. `resync_interval_hours` is a hardcoded default; per-org cadence config is a deferred ops enhancement.

**Scheduler ticks — mirror `s3_reaper_tick`/`s4_reaper_tick`.** `s1_sync_enqueuer_tick(ctx)` + `s1_sync_reaper_tick(ctx)` each enumerate active tenants (`shared.tenants WHERE deleted_at IS NULL`) and delegate to the resilient per-tenant `run_s1_sync_{enqueuer,reaper}_tick` (per-tenant try/except — one tenant's failure never starves the others), wired into `scheduler_tick`. The reaper uses `stale_minutes=45` (D-151, > the longest sync).

**Worker consumer tick — mirror `s4_execution_tick`.** `s1_sync_tick(db_factory, *, sf_client_resolver)` discovers tenant schemas (`_discover_tenant_schemas`) + delegates to `run_s1_sync_consumer_tick` (D-152), wired into `worker_tick`. `_default_s1_sf_client_resolver` is the worker-side credential closure — `(tenant_id, environment_id) → SalesforceClient` via `resolve_sync_sf_client` (D-150), opening + closing a v1 session per resolve (the `_default_s4_client_resolver` pattern — no v1 connection held into the ~30-min sync). One sync job per tenant per tick.

**Deferred (recorded at close).** The **live-SF prod-proving** (a real `run_sync` → entities/edges/`sync_runs`/`ai_enrichment_queue` rows + the `meta_*` parity probe) → **ops** (the sandbox e2e suites; needs SF creds + ~30 min). The **interactive "sync this env now" v1 route** → cutover Step 3 (needs Flask + auth). **Per-org sync cadence config** → ops enhancement.

**Verification.** Governance DB: the enqueuer creates a job for a never-synced org, a stale-complete org (`sync_run` started > 24 h ago), and an org with an incomplete `sync_run` (resume); **skips** a fresh-complete org (< 24 h), an org with an active job (idempotent — a second tick enqueues 0), and an org without `environment_id` (no creds); per-tenant isolation (a bad tenant → 0, others unaffected). The reaper tick fails a stale job per tenant. Plus `import primeqa.worker` + `primeqa.scheduler` (the wiring compiles) + the D-151/152 sync suites stay green.

---

## D-154 — Cutover Step 0 close: the S1-sync production trigger, built (live-proving → ops)

**Date:** 2026-06-04
**Substrates affected:** [S1] (the sync trigger — built + governance-tested) + 2 tenant migrations (`20260604_0010`/`0020`) + thin scheduler/worker wiring. **No engine/phase edits, no v1 behaviour change.**
**Status:** Active — greenfield-cutover **Step 0 close**, merging `phase-17-cutover-step0-s1-sync-trigger` → `main`. Executes + closes the SEQUENCE's hardest step (the readiness audit's #1 blocker).

Step 0 wired a production trigger to the **finished-but-dormant** S1 sync engine (`SyncEngine.run_sync`, all 11 phases, zero prior prod callers) — four build slices + this close, no engine/phase edits:

- **S0.1 (D-150)** — `connected_orgs.environment_id` + `resolve_sync_sf_client` (reuses v1's `_oauth_token`, pre-seeds the engine's refresh-token-grant client via an additive `access_token` param — the grant mismatch resolved) + `ensure_connected_org_for_environment`.
- **S0.2 (D-151)** — the `s1_sync_jobs` queue + `SyncJobStore` (mirrors `s4_execution_jobs` minus the attempts table; `last_sync_run_id` resume anchor; 45-min reaper).
- **S0.3 (D-152)** — the consumer (complete/fail via `sync_runs.status`; **resume-on-reap** via carry-forward from the org's incomplete `sync_run`).
- **S0.4 (D-153)** — the enqueuer cadence (24 h + prompt resume) + the scheduler ticks + the worker consumer tick.

**The realized loop:** the scheduler enqueues (`s1_sync_enqueuer_tick`) → the worker consumes (`s1_sync_tick` → claim → resolve creds → `run_sync` → complete/fail) → the scheduler reaps (`s1_sync_reaper_tick`; 45-min stale → resumable). Additive throughout: 2 tenant migrations, new `primeqa/sync/{credentials,jobs,consumer}.py`, thin scheduler/worker wiring — **no engine/phase edits, no v1 read-path change** (v1 still reads `meta_*`; this is the additive Step-0 of the gated SEQUENCE).

**Doc currency.** Cutover `SEQUENCE.md` Step 0 marked **✅ BUILT (live-proving → ops)** + its coverage row; `SPEC.md` realized-state (the S1 production trigger now exists but isn't live-proven); `EVOLUTION.md` Step-0 build-arc entry (D-150–D-154).

**Deferred → ops/later (the standing follow-ons).** The **live-SF prod-proving** — a real `run_sync` against a connected org → `entities`/`edges`/`sync_runs`/`ai_enrichment_queue` rows + the `meta_*` parity probe — is unavoidably ops (needs SF creds + ~30 min; the `@pytest.mark.sandbox` e2e suites cover it). The **prod-migration applies** (`20260604_0010`/`0020`) join the standing list. The **interactive "sync this env now" v1 route** → cutover Step 3. **Per-org cadence config** → ops enhancement.

**Merge gate.** The 29 S1-sync governance suites green (`tests/integration/sync/` — jobs + consumer + enqueuer) + `import primeqa.app` / `primeqa.worker` / `primeqa.scheduler` (the wiring compiles; app verified with dummy secrets — the prior `JWT_SECRET` error is env-only, unchanged). No engine/phase change; no v1 behaviour change. Merge `phase-17-cutover-step0-s1-sync-trigger` → `main` via PR.

---

## D-155 — Cutover Step 2 / Slice 2.1: the substrate-insights read surface

**Date:** 2026-06-04
**Substrates affected:** [S6, S8] (read-only consumers) + v1 (new `primeqa/intelligence/substrate_insights.py`, a `views.py` route, a template, a `navigation.py` entry). **No migration; no substrate-package change; no v1 behaviour change** (an additive read-only page).
**Status:** Active — greenfield-cutover **Step 2, slice 2.1** (the lead), on `phase-18-cutover-step2-substrate-consumers`. Makes the dormant substrate outputs **visible additively** in the v1 product — the first v1→substrate read consumer.

Step 2 surfaces the substrate outputs (S6 interpretation + clustering, S8 grounding-validity) in v1, additively. Slice 2.1 builds the first + cleanest surface: a standalone **`/substrate-insights`** read page. Three exploration findings shaped the choice.

**Why a standalone page (not a graft onto `/runs/:id`).** S6's `read_interpretation` keys on `s4_execution_runs.run_id` (a substrate UUID), **not** v1's `pipeline_runs.id`; no correlation exists (D-137's "substrate runs ≠ v1 pipeline-runs"). So an S6 panel cannot naively attach to the v1 run-detail page. A standalone page **keyed on substrate ids** sidesteps the mismatch entirely; the v1-run↔substrate-run correlation seam (for the eventual grafted panel) is deferred to slice 2.3.

**The session seam — v1 owns the bridge.** The S6/S8 read APIs (`list_interpretations`, `cluster_recurring_causes`/`cluster_by_vr`/`cluster_flapping`, `list_grounding_validity`) run on a **tenant-schema-scoped ORM `Session`**; v1 Flask routes hold the main-DB session. The bridge is `with get_tenant_connection(tenant_id) as conn: Session(bind=conn)` — the verified precedent in `evolution/recompute.py` + `execution_engine/run.py`. A new **v1-side** module `primeqa/intelligence/substrate_insights.py` owns it (the allowed v1→substrate direction, like `interpretation_phrasing.py`) — the substrate packages stay route-agnostic. Factored for testability: a thin outer `get_substrate_insights(tenant_id)` (opens the bridge; best-effort — any failure → `available=False`, never raises) wrapping a **pure** `_assemble_insights(session, limit) -> dict` (testable directly on the rollback-fixture session). Rows flatten to plain dicts inside the `with`-block (they detach on commit).

**Empty until live S1 → the empty-state is a first-class render branch.** Every substrate store is empty in prod until the ops live-SF run (task #119, Step 0's deferred exit-gate) + the first runs/recompute ticks. So the page has three branches (all via the `_empty_state` component): `available=False` (tenant schema absent / read error) → "unavailable"; `available=True, empty=True` (the prod state until S1 goes live) → "no insights yet"; data → render. This makes 2.1 shippable now (prod renders the empty branch; governance tests exercise the data branch with seeded rows).

**Role-gating — `view_intelligence_report` (the role→set map is counterintuitive).** Verified `_DEFAULT_SET_FOR_ROLE`: `ba→tester_base`, `viewer→release_owner_base`, `tester→developer_base`, `admin`/`superadmin→admin_base`. `view_intelligence_report` lives in tester_base + admin_base; gating on it → **ba + admin + superadmin** see the page, **viewer + tester excluded** (the detailed-intelligence audience). Gating on "report OR summary" would wrongly *include viewers* (release_owner_base holds `view_intelligence_summary`) — so the single `view_intelligence_report` gate is correct. `require_page_permission` supplies superadmin god-mode + a redirect (not a 403). The exact gate is reconfirmed against the live `permission_sets` seed at impl.

**Boundary.** Additive: a new v1 module + a read-only route + a template + a sidebar entry. The LLM phrasing layer (D-117) is deferred (2.1 lists the deterministic `attribution`); the run-correlation graft (2.3), release-grain clustering (2.4), and the S5→S3 forward-seam decision (2.2) are later slices. The live "parity-of-meaning" review is ops-deferred (needs live substrate data).

**Verification.** Governance DB (`tests/integration/test_representation/`, the rollback `session` fixture): seed S6 + S8 rows via `persist_interpretation` / `persist_grounding_validity` → assert `_assemble_insights(session)` surfaces them (sections populated, `empty=False`); seed nothing → `empty=True`; `get_substrate_insights` on an absent tenant → `available=False` (monkeypatched). Plus the existing S6/S8 suites stay green + `import primeqa.app` (route + nav compile).

---

## D-156 — Cutover Step 2 / Slice 2.2: the S5→S3 generation forward-seam — settled (not wired)

**Date:** 2026-06-04
**Substrates affected:** [S5, S3] — a **decision only**. No code, no migration, no behaviour change (docs).
**Status:** Active — greenfield-cutover **Step 2, slice 2.2**, on `phase-18-cutover-step2-substrate-consumers`. Resolves D-134's deferred "S5→S3 forward-seam" — the one genuinely-open architectural question Step 2 carries.

D-134 deferred wiring S5 knowledge into S3 generation "until a semantic-fit design is defined **and** the v1-vs-substrate generation direction settles at the cutover." Both conditions are now resolvable, and the decision is: **do not wire S5→S3 at the cutover** — close the seam, with the reasoning + the revisit-condition recorded.

**The generation direction IS settled — and it settles as "v1 stays."** The cutover SPEC §2 disposition map dispositions v1 generation (`intelligence/generation.py`) as **REPLACE the data-source layer** (read S1 entities, not `meta_*`) with the **code + the LLM gateway + the prompts STAYING** (operational infrastructure, D-111). S3 substrate generation (`primeqa/generation/run_generation`) is **not** mapped as a replacement for v1 generation — it emits a **different artifact** (semantic claims + recipes in S2 `test_claims`/`test_recipes`, D-056/D-065), consumed **downstream** by S4/S6/S8, **not** the product's `test_cases`. So post-cutover: v1 generation remains the product's test-case authoring path (re-sourced onto S1); S3 is a parallel substrate capability, not product-facing. The "direction" the forward-seam waited on has resolved — and it resolved **away from** "S3 is the v2 generation path."

**The semantic fit is ~zero — a wire would be net-new, not a seam.** S5's knowledge today is **test-case-authoring-calibrated**: domain packs are ~1200-token test-step pattern blocks (e.g. Case-escalation test patterns); system rules are authorship proscriptions ("formula fields are read-only; never set them in a payload"). S3's claim-emitter (`propose_semantic_intent`) needs **ontology signals** — which claim kinds exist, which S1 edges ground them (the `governance_core` edge/dimension mappings) — to propose the narrowest groundable intent. These are **adjacent, not complementary**: S5 answers "how do I author correct test steps?"; S3 needs "which claims should I propose, and what grounds them?". Wiring S5→S3 would therefore require a **new claim-calibrated knowledge channel** (sourced from `governance_core`'s mappings), not a port of the existing Rule/DomainPack channels — net-new design + build, for a generation path that is **not product-facing**. And S3 already enforces grounding deterministically *post-proposal* (Guardrail 1, D-085: the LLM proposes, the substrate validates/refuses), so much of that ontology knowledge is already enforced where it matters.

**The decision.** **The S5→S3 forward-seam is closed for the cutover — not wired.** Rationale: (1) the generation direction settled as "v1 generation stays as the product path; S3 is parallel + downstream-only," so there is no cutover-driven need; (2) the semantic fit is ~zero — a wire would be a new claim-ontology channel (net-new build), not a reuse; (3) building knowledge for a non-product-facing emitter is speculative. This **resolves D-134's deferral** (the "until the direction settles" condition is met). **Revisit-condition (recorded, not scheduled):** IF a future, post-cutover decision makes S3 a **product-facing** generation path (S3 claims/recipes surfaced as the product's tests), THEN design a claim-ontology knowledge channel (a `governance_core`-sourced MVP) — gated on that decision, which the cutover explicitly does not make.

**The alternative, weighed + rejected.** Wire a minimal claim-ontology channel into the S3 prompt now (a new `claim_ontology.json` sourced from `governance_core`, appended to the frozen `generation@v3` system prompt). Rejected: it is net-new build (not the "seam" D-134 framed), it serves a non-product-facing emitter, and it partly duplicates the deterministic grounding `governance_core` already enforces — speculative work the cutover does not need.

**Boundary.** Docs only — a decision + its doc-currency (the cutover SEQUENCE coverage row for the forward-seam marked **settled**; the S5 `DEFERRED_ITEMS.md` forward-seam item marked **resolved → D-156**). No code, no migration, no behaviour change.

---

## D-157 — Cutover Step 2 close: the additive substrate-insights surface (run-grafted consumers deferred)

**Date:** 2026-06-04
**Substrates affected:** [S6, S8] (read consumers) + v1 (the `/substrate-insights` page). No migration; no substrate-package change; no v1 behaviour change.
**Status:** Active — greenfield-cutover **Step 2 close**, merging `phase-18-cutover-step2-substrate-consumers` → `main`. Closes Step 2 with the additive-visibility goal met; the v1-run-grafted + release-grain consumers deferred to the steps that enable them.

Step 2 made the dormant substrate outputs **visible additively** in v1. Two slices delivered the goal; the two remaining planned slices hit a verified premise break and are deferred.

- **2.1 (D-155)** — the standalone **`/substrate-insights`** page: S6 interpretations + cross-run clustering + S8 grounding-validity, tenant-scoped via the first v1→substrate read bridge (`get_substrate_insights`), best-effort, with empty-states for the empty-until-live reality. 35 governance tests.
- **2.2 (D-156)** — the **S5→S3 forward-seam settled (not wired)**: the generation direction resolved as "v1 stays as the product path; S3 parallel + downstream-only"; the semantic fit is ~zero; revisit gated on S3 ever going product-facing.

**The deferral — 2.3 (run-detail graft) + 2.4 (release-grain) need execution-world unification (a verified premise break).** The plan's 2.3 would graft an S6 panel onto `/runs/:id` via a derive-path `pipeline_run → test_case → S2 test_claims.test_id → s6_interpretations.claim_test_id`. **That path does not exist** — it breaks at hop 2: v1 `test_cases` (Integer PK) carry **no** reference to S2 `test_claims` (UUID), and `s4_execution_runs`/`s6_interpretations` carry **no** v1 reference (only a too-weak shared `environment_id`). The v1 run-world (`test_cases`/`pipeline_runs`) and the substrate run-world (claims/recipes/`s4_runs`/`s6_interpretations`) are **entirely disjoint** — independently triggered, no shared key, no write-path coupling (consistent with D-156: v1 + substrate generation are separate, unwired, different-artifact paths). A correlation can't be derived **or** stored until the two execution paths are **unified**, which is the *later* cutover steps (Step 3 flagged read-switch / Step 4 parallel-run), **not** additive Step 2. Building the bridge now would do later-step work out of the gated order. So **2.3 + 2.4 fold into Steps 3–4** (the SEQUENCE coverage rows updated); the standalone page (2.1) already delivers the additive surface they would have refined.

**Doc currency.** Cutover `SEQUENCE.md` Step 2 marked **built (additive surface; D-155/D-156)** with the run-grafted + release-grain consumers folded into Steps 3–4; the S6/S8 coverage rows updated; `EVOLUTION.md` Step-2 entry. (The forward-seam row was already marked settled by D-156.)

**Deferred → ops/later.** The page's **live data** (every substrate store is empty until the ops live-SF run, task #119) + the exit-gate's **live parity-of-meaning review** are ops-deferred (the same live half as Step 0). The **run-grafted S6 panel** + the **release-grain clustering** are deferred to cutover Steps 3–4 (they require the v1↔substrate execution bridge).

**Merge gate.** The new `test_s6_s8_insights_surface` suite green + the existing S6/S8 suites (`test_s6_consumer`, `test_s6_clustering`, `test_s8_grounding_store`, `test_s8_recompute`) stay green + `import primeqa.app` (route + nav compile). Additive read-only page; no migration; no v1 behaviour change. Merge `phase-18-cutover-step2-substrate-consumers` → `main` via PR.

---

## D-158 — Cutover Step 3 / Slice 3.1: the v1 read-path switch — the `MetadataAccessor` seam + flag

**Date:** 2026-06-04
**Substrates affected:** [S1] (read consumer) + v1 (`metadata/accessor.py`, the `core/models.py` flag, `test_management/service.py` wiring). **MIGRATION** (`migrations/051` — public `tenant_agent_settings.cutover_read_s1`).
**Status:** Active — greenfield-cutover **Step 3, slice 3.1** (the seam), on `phase-19-cutover-step3-read-path-switch`. Opens Step 3 (the flagged v1 read-path switch to S1) + sets the contract for its slices.

Step 3 routes v1's metadata READS to the S1 semantic org model behind a **per-tenant flag** (`cutover_read_s1`), with `meta_*` the flag-off fallback — the start of the parallel run, reversible (flip the flag). Per the SEQUENCE: the **generation context** + the **validator CRUDQ** switch to S1; preflight stays on `meta_*` (GAP-2). Slice 3.1 builds the seam; later slices add the S1 reader + the field-CRUD parity.

**The seam — a `MetadataAccessor` facade (the one switch point).** The read-paths all go through `MetadataRepository`'s read methods. A new `primeqa/metadata/accessor.py` `MetadataAccessor(db, tenant_id, metadata_repo, s1_reader=None)` implements the same read-interface (`get_objects`/`get_fields`/`get_validation_rules`/`get_version`), reads the flag **once** at construction (tolerant try/except → False, the `_domain_packs_enabled` pattern), and routes `flag AND s1_reader ? s1_reader.X() : metadata_repo.X()`. The call sites (`test_management/service.py`: `generate_test_plan`, `revalidate_test_case_version`, the single-TC wrapper) inject the accessor in place of the raw repo; the consumers (`TestCaseGenerator`, `TestCaseValidator`) are duck-typed + accept it unchanged. **In 3.1 `s1_reader=None`** → pure passthrough → **identical v1 behavior** (additive, zero behaviour change). The S1 reader lands in 3.2 (generation) / 3.4 (validator).

**Three contract decisions (settled here for all Step-3 slices):**
- **`at_seq` ignores `meta_version_id`.** v1's `meta_version_id` (an environment-pinned `meta_versions` snapshot) and S1's `version_seq` (`logical_versions`) are **independent axes** — no mapping. For a read-*switch* (read the org's current metadata), the S1 reader resolves `at_seq = current_version_seq()` once + discards the passed `meta_version_id` (the S6/S8 `s1_reader` precedent). The one intentional asymmetry between the two paths.
- **Empty-S1 → fall back to `meta_*` (best-effort, never raise).** S1 is empty in prod until the ops live-SF run (#119); `current_version_seq()` raises `VersionNotFoundError` on a tenant with no S1 versions. The reader-builder mirrors `substrate_insights.get_substrate_insights` — best-effort; on any failure → `s1_reader=None` → the accessor reads `meta_*` + logs a warning. So a flag-on tenant whose S1 isn't synced yet degrades safely (the parallel-run safety).
- **The 4th coupling — the `GenerationLinter`.** `generate_test_plan` (~L183-199) builds `linter_metadata` from the validator's private indexes (`validator._obj_by_name`/`_fields_by_obj`), reading field `is_createable`/`is_updateable`/`field_type`/`picklist_values`. So the validator's `MetaField`-shaped objects feed **two** consumers — the S1 reader's synthetic `_S1Field` must carry the full duck-type incl. the field CRUD flags (→ GAP-1).

**GAP-1 — field-level `is_createable`/`is_updateable` (RESOLVED: add the S1 columns, user-authorized).** The validator's `field_not_createable` (CRITICAL) / `field_not_updateable` (WARNING) + the linter need per-field CRUD writability, which S1's `field_details` doesn't carry (only object-level). The sync **already fetches** them per field (the **object** mapper stores `createable`/`updateable` at `detail_mappers.py` L75-76; the field mapper doesn't). Resolution (3.3): an additive `field_details.is_createable`/`is_updateable` (alembic tenant migration, default TRUE — matches SF + `MetaField` server_default, no backfill) + a 2-line `_map_field_details` change. This is the **first sync-engine touch of the cutover** — authorized to give the validator true S1 parity (the Step-3 exit-gate is "S1 reads *agree* with `meta_*`"; object-level fallback would break that by design + was rejected).

**GAP-2 — preflight category-health (DEFERRED to a Step-5 prerequisite).** Preflight's `MetaSyncStatus` per-category health + `MetaVersion.completed_at` staleness have no clean S1 map (S1's sync model is `sync_runs` phases). Preflight is a **soft gate** and `meta_*` stays populated through Steps 3–4, so it reads `meta_*` correctly throughout. Its S1 cutover is relocated to a **Step-5 prerequisite** (the `meta_*` drop can't proceed until preflight has an S1 freshness/health source) — not built speculatively now.

**Slice 3.1 shape.** `migrations/051_cutover_read_s1_flag.sql` (public `tenant_agent_settings.cutover_read_s1 BOOLEAN DEFAULT false`, idempotent, mirrors 050) + the model column + `primeqa/metadata/accessor.py` (passthrough) + the 3-site wiring + a passthrough-parity governance test. Additive; flag default off; no v1 behaviour change.

**Verification.** Governance DB: the accessor with `s1_reader=None`, flag on AND off → identical delegation to `metadata_repo` for all four methods (passthrough parity — no S1 needed). Plus `import primeqa.app` (the wiring compiles) + the existing generation/validator suites stay green.

---

## D-159 — Cutover Step 3 / Slice 3.2: the generation S1-reader

**Date:** 2026-06-04
**Substrates affected:** [S1] (read) + v1 (new `metadata/s1_reader.py`, the `test_management/service.py` gen-vs-validator accessor split, a `cutover_read_s1_enabled` extraction in `accessor.py`). No migration; no v1 behaviour change when flag-off.
**Status:** Active — greenfield-cutover **Step 3, slice 3.2**, on `phase-19-cutover-step3-read-path-switch`. The first S1-served v1 read-path: flag-on tenants' **generation metadata context** reads S1.

3.1 built the `MetadataAccessor` seam (passthrough). 3.2 builds the **`MetadataS1Reader`** (S1 → the `MetaObject`/`MetaField`/VR duck-types) + wires it into the **generation** accessor. The validator stays on `meta_*` until 3.4 (it needs the field-CRUD columns 3.3 adds — the split below).

**The reader — eager-hydrated, read through `SemanticOrgModel`.** A new `primeqa/metadata/s1_reader.py` `MetadataS1Reader` reads S1's typed query interface (`get_entities`/`get_related`/`get_entity_details` — the S6/S8 `s1_reader` pattern, no S1-local SQL). It **eager-hydrates** at construction (one `with get_tenant_connection(tid) as conn:` → `SemanticOrgModel(conn)` → load the whole org's metadata into frozen `_S1Object`/`_S1Field`/`_S1ValidationRule` dataclasses), so it is a pure in-memory snapshot for its lifetime — sidestepping connection-close-across-the-generation-call (the validator already eager-hydrates; data volume is one org). The translation:
- **objects** → `get_entities("Object", at_seq)` → `_S1Object(id=entity.id, api_name=sf_api_name, is_createable, is_custom)` (`is_createable`/`is_custom` from `get_entity_details` → `object_details`). Sorted by `api_name`.
- **fields** → per object, `get_related(object.id, ["BELONGS_TO"], "inbound", at_seq)` → `_S1Field(api_name, field_type, is_required, is_custom, …, meta_object_id=object.id)` (`field_type`/`is_custom` from `field_details`; `is_required` from `FieldAttributes` JSONB). Sorted by `api_name`. **`_S1Object.id` == `_S1Field.meta_object_id`** (the same S1 entity UUID — the validator indexes by `meta_object_id`, looks up by `obj.id`).
- **VRs** → `get_entities("ValidationRule", at_seq)` → `_S1ValidationRule(rule_name, error_message, meta_object=<the APPLIES_TO-target object ref, carrying .api_name>)` (object via `get_related(vr, ["APPLIES_TO"], "outbound")`). Sorted by (object, rule).
- **`at_seq` = `current_version_seq()`** (ignores the passed `meta_version_id`, D-158). **Best-effort build** — `build_metadata_s1_reader(tenant_id)` returns `None` on `VersionNotFoundError` (empty S1) / any error → the accessor falls back to `meta_*` (the parallel-run safety). **Flag-gated build** — the service builds the reader **only when `cutover_read_s1` is on** (a shared `cutover_read_s1_enabled(db, tenant_id)` extracted from the accessor) → no wasted hydrate for flag-off tenants.

**The gen-vs-validator accessor split (the GAP-1 sequencing).** `_build_metadata_context` reads field `f.is_createable` (its "required createable fields" line). S1's `field_details` carries no per-field createable until 3.3 — so the 3.2 reader **approximates `_S1Field.is_createable=True`** (over-listing a non-createable required field is a soft prompt nudge, not a gate — acceptable for the *descriptive* generation context). But the **validator's** `field_not_createable` is a **CRITICAL gate** — `is_createable=True` would mask a real read-only field (a false-negative). So in 3.2 the validator must NOT read S1. `generate_test_plan` therefore uses **two accessors**: the generator's (`with_s1_reader=True`) + the validator's (`with_s1_reader=False`, stays `meta_*`); the linter reads the validator's `meta_*`-fed indexes (unchanged). `generate_test_case` (a generator) gets the reader; `revalidate`/`apply_validation_fix` (validators) stay `meta_*`. 3.4 flips the validator sites to S1 once 3.3's columns make `is_createable` real.

**Shape.** New `primeqa/metadata/s1_reader.py` (`MetadataS1Reader` + `build_metadata_s1_reader` + the frozen duck-types); `metadata/accessor.py` extracts `cutover_read_s1_enabled`; `test_management/service.py` `_metadata_accessor(..., with_s1_reader=…)` + the generate_test_plan split. No migration.

**Verification.** Governance DB (seed BOTH `meta_*` + S1 for one org): flag-on → the generation accessor's `get_objects`/`get_fields`/`get_validation_rules` return the S1 snapshot with the SAME api_names + object `is_createable` + field `is_required`/`is_custom` + VR error-messages + **the same `api_name` ordering** (prompt-cache determinism); `_build_metadata_context` is **byte-identical** S1-vs-`meta_*` for an org whose required fields are createable (the field-`is_createable` approximation is consistent there — the divergent required-non-createable case is the known 3.2 tolerance 3.4 resolves). Flag-off → `meta_*` unchanged. Empty-S1 → `None` reader → `meta_*` (no raise). Plus the accessor unit tests stay green + `import primeqa.app`.

---

## D-160 — Cutover Step 3 / Slice 3.3: S1 field-level CRUD flags (the first sync touch)

**Date:** 2026-06-04
**Substrates affected:** [S1] (the sync's field-detail mapper + an additive `field_details` migration). **MIGRATION** (tenant, chains onto `20260604_0020`). **The first sync-engine / S1-schema touch of the cutover.**
**Status:** Active — greenfield-cutover **Step 3, slice 3.3**, on `phase-19-cutover-step3-read-path-switch`. Resolves GAP-1 (D-158): S1 gains per-field `is_createable`/`is_updateable` so the validator can reach true S1 parity (3.4).

The 3.4 validator S1-switch needs per-field CRUD writability (its `field_not_createable` is a CRITICAL gate); S1's `field_details` carried only the **object**-level flag. 3.3 adds the two columns — **verified cheap**: the describe **already fetches** `createable`/`updateable` per field (the raw `DescribeFieldResult`), they **survive normalization** (not in `semantic/normalization.py::_VOLATILE_KEYS`; `_normalize_field = _strip_volatile + list-sort`), and the **object** mapper already stores them (`detail_mappers._map_object_details`). So 3.3 is **a 2-column migration + a 2-line mapper change** — no fetch-layer change.

**Shape.** (1) An additive tenant migration `field_details.is_createable`/`is_updateable BOOLEAN NOT NULL DEFAULT true` (idempotent `ADD COLUMN IF NOT EXISTS`; chains onto `20260604_0020`). **Default TRUE** matches Salesforce's permissive default + the v1 `MetaField` server_default → **no backfill** (existing `field_details` rows read True until the next sync repopulates the real value — acceptable in the parallel-run window; a permissive default never *adds* a false CRITICAL). (2) `detail_mappers._map_field_details` adds `"is_createable": bool(normalized.get("createable", True))` + `"is_updateable": bool(normalized.get("updateable", True))`.

**Why this unblocks 3.4 with no reader change.** The D-159 `MetadataS1Reader` already reads `field_details.get("is_createable", True)` (via `get_entity_details`'s `SELECT *`): absent in 3.2 → True (the approximation); **present after 3.3 → the real value**. So once 3.3's columns exist + the next sync populates them, the reader serves real per-field CRUD — and 3.4 just flips the validator's accessor to S1.

**The boundary it crosses.** This is the **first sync-engine + S1-schema touch** of the cutover (Steps 0–2 were "no engine/phase edits"). Authorized (the user's GAP-1 choice) because it's the only path to the Step-3 exit-gate ("S1 reads *agree* with `meta_*`") for the validator, and it's a clean additive column filling a real S1 fidelity gap (S1 *should* carry field CRUD flags regardless). **No behaviour change** to existing sync/phase logic — only two keys added to one detail row + two nullable-default columns.

**Verification.** The sync detail-mapper unit suite: `_map_field_details` maps `createable`/`updateable` from the normalized describe (and defaults True when the key is absent — the existing mock); the migration applies (the governance/semantic harness `alembic upgrade tenant@head` picks it up — `field_details.is_createable`/`is_updateable` exist); the D-159 reader test stays green (the reader's `get("is_createable", True)` is unchanged). No v1 behaviour change.

---

## D-161 — Cutover Step 3 / Slice 3.4: the validator + linter S1-reader (true parity)

**Date:** 2026-06-04
**Substrates affected:** [S1] (read) + v1 (the validator-site accessor flips + the reader's picklist population). No migration; no v1 behaviour change when flag-off.
**Status:** Active — greenfield-cutover **Step 3, slice 3.4**, on `phase-19-cutover-step3-read-path-switch`. Completes the read-switch: flag-on tenants' **validator + linter** read S1, now at **true parity** (3.3 made the field CRUD flags real).

3.2 routed generation to S1 + kept the validator on `meta_*` (it needed 3.3's field CRUD flags). 3.3 landed them. 3.4 flips the validator sites + closes the one remaining reader gap (picklist values), so the validator's *decisions* match `meta_*`.

**The validator-site flip.** The three validator-construction sites — `generate_test_plan`'s validator, `revalidate_test_case_version`, `apply_validation_fix` — flip from `_metadata_accessor(...)` (default `meta_*`) to `with_s1_reader=True`. The linter is covered transitively (it reads the validator's accessor-fed `_obj_by_name`/`_fields_by_obj`). **No reader change for the CRUD flags** — the D-159 reader already reads `field_details.get("is_createable", True)`, which 3.3 made real (`get_entity_details` `SELECT *`). So `field_not_createable` (CRITICAL) / `field_not_updateable` (WARNING) now fire correctly off S1 — the GAP-1 payoff.

**The one reader gap closed — picklist values.** The validator's `picklist_value_not_allowed` (WARNING) reads `f.picklist_values`; D-159 left `_S1Field.picklist_values=()` (the validator *gracefully skips* on empty — `_picklist_values` returns None → no false-positive, the existing degrade). For **true parity** (the WARNING should fire identically), 3.4 populates it via the clean **2-hop**: `field_details.picklist_value_set_entity_id` (already on the detail row) → `SemanticOrgModel.get_picklist_values(pvs_id, at_seq)` → the `value_api_name`s. So a picklist field's S1 read carries its allowed values, matching `meta_*`.

**Shape.** `test_management/service.py`: the 3 validator sites → `with_s1_reader=True`. `metadata/s1_reader.py`: `_hydrate`'s field loop populates `picklist_values` (the 2-hop, only for fields carrying a `picklist_value_set_entity_id`). No migration; no v1 behaviour change when flag-off.

**Verification.** Governance DB (semantic harness, the validator-over-reader): a step list exercising each rule on the S1 snapshot → **`field_not_createable` CRITICAL fires** on a non-createable field (3.3's real flag reaching the validator), `object_not_found` / `field_not_found` fire on absent subjects, **`picklist_value_not_allowed` WARNING fires** on a value outside the seeded picklist (the 2-hop), and a clean step → no issues. The D-159 generation-reader tests + the accessor unit + detail-mapper suites stay green; `import primeqa.app`. Live dual-stack byte-parity (real-org S1 vs `meta_*`) is the ops-deferred half.

---

## D-161.1 — amendment: the reader must emit *bare* field api-names (parity break found mid-impl)

**Date:** 2026-06-04. Amends D-161 (append-only — does not edit it).

Mid-impl verification (read, not assumed) surfaced a parity break the D-161 design
missed. S1 stores a Field's ``sf_api_name`` **object-qualified**:
``_extract_external_id`` (``sync/materialize.py``) builds ``f"{parent}.{name}"`` →
``"Account.Name"``. But v1 ``meta_*`` stores the **bare** name
(``metadata/service.py``: ``MetaField.api_name = describe["name"]`` → ``"Name"``),
and every test-case step references fields bare (``field_values: {"Name": …}`` /
``{"Status": …}`` — the ``generation.py`` prompt schema + the test fixtures). The
validator keys ``obj_fields = {f.api_name: f}`` and tests ``fname not in
obj_fields``; the reader passed the qualified name through unchanged →
**``field_not_found`` CRITICAL on every field of every TC** when the validator
reads S1. The slice's "the reader's already correct, just flip the validator"
premise was false.

**Fix (root cause, in the reader — NOT a validator workaround).**
``hydrate_metadata_s1_reader`` strips the parent-object prefix to recover the bare
name — the exact inverse of the sync's ``f"{parent}.{name}"``:
``bare = fe_api[len(obj_api)+1:] if fe_api.startswith(obj_api + ".") else fe_api``.
This is the literal "true parity" 3.4 promised; it corrects the validator AND the
already-shipped 3.2 generation context (which had been leaking ``"Account.Name"``
into the prompt — a quality regression, not a correctness break).

**Why 3.2 didn't catch it.** ``test_metadata_s1_reader.py`` asserted the reader's
*self-consistent* output (``"Account [required: Account.Name]"``), never seeding
``meta_*`` and comparing — so it codified the qualified shape as expected. 3.4
corrects those assertions to bare (``"Name"``) and adds the validator-over-reader
parity test that exercises real bare-name steps (the assertion the 3.2 test should
have made).

**Out of scope (noted, not fixed).** ValidationRule ``sf_api_name`` may carry the
same qualified skew, but no validator rule reads VR names — it only affects the
generation-context VR-list *text*. Left as a documented generation-cosmetics item;
revisit if/when VR naming reaches a rule.

---

## D-162 — Cutover Step 3 close: read-switch built; preflight → Step-5 prereq; merge

**Date:** 2026-06-04
**Substrates affected:** [S1] (read) + v1 (the accessor seam + readers). Closes
greenfield-cutover **Step 3** on `phase-19-cutover-step3-read-path-switch`
(D-158–D-162).
**Status:** Active — Step 3 **BUILT** (generation + validator/linter read S1 behind
`cutover_read_s1`); preflight deferred to a **Step-5 prerequisite** (GAP-2); the
live dual-stack parity rides ops task #119.

**What Step 3 landed.** v1's metadata *reads* route to S1 behind the per-tenant
`cutover_read_s1` flag, `meta_*` the flag-off fallback — the parallel run begins.
The seam is `MetadataAccessor` (3.1, D-158, migration `051`); the generation context
(3.2, D-159) and the validator + linter CRUDQ (3.4, D-161) read `MetadataS1Reader`;
the first sync-engine touch added `field_details.is_createable`/`is_updateable`
(3.3, D-160, tenant `20260604_0030`). D-161.1 caught + root-caused a field-naming
parity break (S1's object-qualified `sf_api_name` vs v1's bare names) mid-impl.
Best-effort throughout (empty/error S1 → `meta_*`, never raises), so a flag-on but
S1-empty tenant degrades safely during the parallel window.

**GAP-2 ratified — preflight stays `meta_*`.** `primeqa/runs/preflight.py` reads
`MetaSyncStatus` per-category health + `MetaVersion.completed_at` staleness; neither
has a clean S1 map (S1's freshness model is `sync_runs` phases, a different shape).
Preflight is a soft gate and `meta_*` is populated through Steps 3–4, so it reads
correctly throughout the parallel run. Its S1 cutover is **relocated to a Step-5
prerequisite** — the `meta_*` drop cannot proceed while preflight reads `meta_*`.
Recorded in SEQUENCE Step 5's entry-gate.

**Verification (run, observed).** Merge gate green: **46 semantic integration + 82
unit (accessor + detail-mapper) = 128**, no regression. The validator-over-reader
parity test exercises each rule (`object_not_found` / `field_not_found` /
`field_not_createable` CRITICAL + `picklist_value_not_allowed` WARNING + a clean
step) off a real S1 snapshot with **bare-name** steps; `import primeqa.app` green.
Flag-off → `meta_*` passthrough (zero v1 behaviour change). The live dual-stack
byte-parity (real-org S1 vs `meta_*` for pilot tenants) is ops-deferred with #119 —
the same live half as Steps 0/2.

**Boundary + standing follow-ons.** Additive — no v1 read removed, no table dropped
(Steps 4–5). Prod-migration applies: `migrations/051_cutover_read_s1_flag.sql`
(public) + `20260604_0030_field_details_crud_flags` (tenant). Merge
`phase-19-cutover-step3-read-path-switch` → `main`.

---

## D-163 — Substrate 7 (Conversation & Control) opened: grounded answering over the substrate spine

**Date:** 2026-06-04
**Substrates affected:** [S7] (opened — the conversation faculty); [S1] (entity-resolution, read through `SemanticOrgModel`); [S6] (interpretations + clustering, read); [S8] (grounding-validity verdicts, read); [S2] (requirement→tests, read) — all read-only, S7 writes nothing
**Status:** Active — S7 foundational opening (Phase 8). Faculty-first: the grounded-answering faculty is the semantic core; the Control half + the conversation mechanics explicitly deferred. SPEC: `docs/architecture/substrate_7_conversation/SPEC.md`.

Substrate 7 opened — the **conversation layer**: the natural-language surface through which a user asks the system about itself and gets an answer grounded in what the other substrates recorded (PLATFORM_VISION §S7 — "sits on top and touches every other substrate as a user-facing surface"). Faculty-first (mirroring D-111/D-112/D-134): the semantic core opens; the **Control** half + conversation **mechanics** are explicitly fenced. This is the **last** substrate — it opens because every other substrate now has a queryable read API.

**Keystone — grounded-or-refuse.** Every answer is grounded in substrate evidence retrieved deterministically; when nothing grounds the question, S7 **refuses** rather than guessing (D-073: refusals are the substrate's conversational-clarification surface). The refusal is **deterministic + substrate-authored, produced before any LLM call** — the model never gets the option to "answer anyway"; it only ever sees a non-empty, bounded evidence block. This raises the platform's spine-wide grounding discipline (S3 substrate-authored admissibility; S6/S8 deterministic-first "the LLM phrases, never invents") to the user surface.

**The semantic core — the grounded-answering faculty.** A deterministic pipeline with a fenced phrasing edge: `(question, QuestionContext) → classify_intent (keyword) → retrieve_<intent> (deterministic recipe over the substrate read-APIs) → assemble_evidence (bounded: item-cap + token budget + stable citation ids) → build_answer (empty ⇒ refuse, no LLM; else phrase over ONLY that evidence) → Answer{status, text, citations, refusal_reason}`. **Deterministic-first:** classification, retrieval, assembly, and the refusal decision are pure; the LLM phrases handed evidence and cites nothing beyond it (the structural anti-hallucination guarantee — the model can only restate what it was given). The `conversation/` package is **LLM-free** — the phrase step is an injected callable; the real `llm_call` lives in v1 (the S6 `interpretation_phrasing` boundary split).

**The intent set — three deterministic retrieval recipes (open).** Phase 1 (user-chosen): `failure_cause` (S6 `list_interpretations` + `cluster_recurring_causes` + `cluster_by_vr`), `grounding_drift` (S8 `list_grounding_validity(overall=...)`), `impact` (S1 `get_entities`→`get_related` single-hop + S2 `list_tests_by_requirement`). The first two phrase already-recorded deterministic verdicts (the safest debut posture — the substrate already decided, S7 adds zero judgment); `impact` is the one live S1 read-through (keeping the boundary honest — S7 reaches S1, not only the two answer-stores). `impact` takes its target from the bounded context (a picker), not free-text entity extraction. No keyword match ⇒ a deterministic clarify-refusal. The set is **explicitly open** (the S8 "the leg set grows" / D-096 "single-hop before traverse" discipline applied to intents).

**The dependency law — a pure consumer; no table.** `S7 → {S1 query, S2 coordinator-read, S6 interpretation-read + clustering, S8 grounding-validity-read, …}`; **S7 writes nothing to any substrate.** It is the one substrate primarily a consumer, not a producer — it reads others' durable artifacts and produces an *ephemeral* answer, so it owns **no table in phase 1** (S4/S6/S8 each own a result table because they produce a durable artifact; S7 does not). It reads through each substrate's public read API (never raw tables / internals): S1 via `SemanticOrgModel`, S6/S8/S2 via their `__init__` reads on a tenant-scoped `Session`. The v1→substrate bridge opens one tenant connection and derives both an S1 reader and an ORM session from it (the `evolution/recompute.py` dual-derivation), best-effort (any failure → `available=False`, never breaks the page — the `substrate_insights` precedent).

**Stateless + bounded (D-095.4).** Phase-1 answering is stateless per question; the scope is an explicit, bounded `QuestionContext` (release / environment / requirement), never an implicit accumulating session — D-095.4 ("the shared context is explicit and bounded, not an implicit shared conversation") at the user surface. Multi-turn (+ any persistence) is deferred.

**Deferred — the Control half + the conversation mechanics (the fence).** Explicitly out of the phase-1 core: the **Control** half (write-side commands — trigger/approve/apply, gating on the Permission Model + env run-policies — the next phase); **multi-turn + any S7 persistence** (D-095.4 forbids implicit shared state); **proactive/push insights**; **broad retrieval** over all substrates; the **open-ended NL router** (deferred until the fixed-intent vocabulary matures — retrieval stays substrate-authored, not model-authored); **rich chat UI**. The conversation-infrastructure local maximum, deliberately not built at the opening.

**Slice arc.** Slice 0 (this entry) lands the doc-set + the frozen contract types (`QuestionContext` / `Intent` / `EvidenceItem` / `Evidence` (+`is_empty`) / `Citation` / `Answer`) + a contract/drift-guard test (the pipeline stages are produce-only here). The faculty lands across D-163.1 (intent classification) → D-163.2 (retrieval + bounded assembly) → D-163.3 (the LLM phrasing edge + grounded-or-refuse) → D-163.4 (the thin `/ask` surface + close).

**Guards.** S7 writes nothing to any substrate (pure consumer). Retrieval + the refusal decision are deterministic + substrate-authored (the LLM phrases only). The `conversation/` package imports no `intelligence` (the phrase_fn is injected — LLM-free package, the S6/S8 invariant). Reads go through each substrate's public read API, not raw tables. No S7 table in phase 1. Control + mechanics are fenced (§6) and not in the opening.

---

## D-163.1 — S7 slice 1: deterministic intent classification

**Date:** 2026-06-04
**Substrates affected:** [S7] (`conversation/intent.py` — the classifier); [S5] (reuses the shared `knowledge._text` word-boundary matcher — read-only, pure)
**Status:** Active — S7 slice 1 (sub-decision of D-163). Pure, no-DB, no-LLM.

The first pipeline stage: `classify_intent(question_text, context) -> Intent | None`. Maps a natural-language question to one of the three fixed intents, or `None` (→ a deterministic clarify-refusal downstream). **Keyword, not LLM** (the SPEC §3 discipline — the classifier picks *which fixed recipe* runs; it never authors an arbitrary query; letting the model choose retrieval is letting it author its own grounding, the inversion the platform refuses).

**Reuse, corrected path.** Matching is the shared **`primeqa.knowledge._text`** (`kw_count` / `matched_keywords`) — inflection-aware word-boundary regex (`\bkw(?:s|es|ed|ing)?\b`), the same matcher S5 domain-pack selection + S3 `detect_complexity` use, so S7 doesn't reinvent matching semantics (and dodges the "flow silently matches workflow" class of bug `_text` was extracted to fix). The plan's path (`intelligence.knowledge._text`) was pre-cutover; the module relocated to S5 (`primeqa/knowledge/`) in Step 1, so the import is **`primeqa.knowledge._text`** — verified to pull in **zero `primeqa.intelligence`**, so the LLM-free package guard (D-163) holds. S7→S5 is within the dependency law.

**Scoring (deterministic).** Per intent, count the distinct matched keywords; **highest count wins; ties break by a fixed priority order** (`failure_cause` > `grounding_drift` > `impact` — SPEC §3) via a stable sort `(-count, priority_index)`. No intent matches ⇒ `None`. `impact`'s target object/requirement is **not** parsed from the question — it rides the bounded `QuestionContext` (the picker; SPEC §3) — so classification is keyword-only and entity-extraction-free.

**Shape.** `conversation/intent.py` (the keyword sets + `classify_intent`); re-export from `conversation/__init__.py`. **Verify:** a pure-unit table — each intent's keywords classify to it; the highest-count intent wins a mixed question; ties resolve by priority; an off-topic question → `None`; inflections match (`failed`/`drifting`/`affects`). No DB, no LLM.

---

## D-163.2 — S7 slice 2: deterministic retrieval recipes + bounded assembly

**Date:** 2026-06-04
**Substrates affected:** [S7] (`conversation/retrieval.py` + `assembler.py`); [S6] (`list_interpretations` / `cluster_recurring_causes` / `cluster_by_vr`, read); [S8] (`list_grounding_validity`, read); [S1] (`get_entities` / `get_related`, read); [S2] (`coordinator.list_tests_by_requirement`, read) — all read-only
**Status:** Active — S7 slice 2 (sub-decision of D-163). Pure over **injected** readers; no LLM, no connection-management (the bridge injects `session` + `s1`).

The deterministic core of the faculty: given an `Intent` + `QuestionContext`, run that intent's **retrieval recipe** over the substrate read-APIs and assemble a **bounded** `Evidence`. No LLM. Signatures **live-probed** (CONVENTIONS) — corrected vs the plan's approximations.

**The recipes (verified signatures), uniform `(s1, session, ctx)` for a clean dispatch:**
- `retrieve_failure_cause` → `list_interpretations(session, recipe_id=…, limit)` + `cluster_recurring_causes(session, recipe_id=…, min_runs=2)` + `cluster_by_vr(session, recipe_id=…, min_runs=2)` (all keyword-only, optional `recipe_id` scope from `ctx.recipe_id`). Flattens `InterpretationRead` / `CauseCluster(cause_kind,count,run_ids)` / `VrCluster(vr_name,count,outcomes,run_ids)`.
- `retrieve_grounding_drift` → `list_grounding_validity(session, test_id=…, overall="drifted")` + `overall="broken"` (the `overall`-indexed standing-verdict query S8 built for exactly this consumer). Flattens `GroundingValidityRead{test_id,version_seq,overall,claim_verdict,evaluated_at_version_seq}`.
- `retrieve_impact` → object branch: `s1.get_entities("Object", at_seq, filters={"sf_api_name": ctx.object_api_name})` → `s1.get_related(obj.id, ["BELONGS_TO","APPLIES_TO","HAS_RELATIONSHIP_TO","REFERENCES"], "inbound", at_seq)` (the object's impact surface — fields/VRs/referencing-fields that touch it; verified the edge taxonomy); requirement branch: `coordinator.list_tests_by_requirement(session, external_system="jira", external_key=ctx.requirement_key)` → `RequirementMatch(test_id,link_kind,external_version,linked_at)`. `current_version_seq()` `VersionNotFoundError` (empty S1) → no S1 evidence (a clean degrade → likely a downstream refusal). **Plan correction:** S2's read is a **coordinator method** with keyword args `external_system`/`external_key` (not a bare `jira_key`), and the impact edge set is the verified inbound taxonomy (not the plan's guessed list).

**The bounded assembler.** `assemble_evidence(intent, items, *, max_items, char_budget)` applies the **bound** — an item-count cap **and** a char/token budget on the serialized evidence (the D-095.4 "explicit and bounded" requirement + the substrate `_LIST_HARD_CAP` convention) — and **assigns sequential stable citation ids** (`E1..En`), returning `Evidence`. Deterministic: preserves recipe order, always admits ≥1 item (so a single large item isn't dropped to empty), then stops at the cap or budget. The recipes mint a natural ref per item (in `data`); the assembler is the sole authority on the canonical `E{n}` citation (the SPEC §2 "assembler assigns" reading) — so `Answer.citations` are always the assembler's ids.

**Boundary.** `retrieval.py` imports S6/S8/S2/S1 (all **verified** to pull in zero `primeqa.intelligence` — the LLM-free package guard holds; S7→{S6,S8,S2,S1} is the dependency law). The recipes take **injected** readers (no `get_tenant_connection` here) — the connection-management + best-effort lives in the slice-4 bridge; this keeps the recipes pure + directly unit-testable.

**Verify (governance, the semantic `conn`+`seed` harness — the bridge's own dual-derivation):** build `Session(bind=conn)` + `SemanticOrgModel(conn)` from one tenant `conn`; seed S6 via `persist_interpretation` + S8 via `persist_grounding_validity` (the `test_s6_s8_insights_surface` helpers) + S1 entities/edges via `seed` — assert each recipe returns the expected flattened evidence (failure_cause: interpretation + cause/vr clusters; grounding_drift: only `drifted`/`broken`; impact: the object + its inbound edges + linked tests). Plus a **pure-unit** assembler test: the item-cap, the char-budget early-stop, the ≥1-item floor, and `E1..En` numbering. No LLM anywhere.

---

## D-163.3 — S7 slice 3: the LLM phrasing edge + grounded-or-refuse

**Date:** 2026-06-04
**Substrates affected:** [S7] (`conversation/answerer.py` — the refusal gate, LLM-free); [v1 intelligence] (the `grounded_answer` prompt task + gateway registration)
**Status:** Active — S7 slice 3 (sub-decision of D-163). The only LLM in the faculty, fenced to phrasing.

Turns bounded `Evidence` into an `Answer` — the **grounded-or-refuse keystone**. Two pieces, split across the boundary:

**The refusal gate (`conversation/answerer.py`, LLM-free).** `build_answer(evidence, *, question, phrase_fn) -> Answer`. **Empty evidence ⇒ `refused` deterministically, BEFORE any phrasing** — the model never sees an empty block; refusal is a substrate decision, never the model's (D-073). Otherwise the **injected** `phrase_fn` phrases over only this evidence, and S7 returns the **evidence's** citations (`Answer.citations` = the assembler's `E{n}` ids), never citations the model claims. The phrase step is injected (a `Callable`) so `conversation/` stays LLM-free — the real `llm_call` is wired in the slice-4 bridge (v1). A phrasing failure (None / raise) degrades to `refused` with a `refusal_reason` + the citations (honest: there *is* grounding, the prose just failed) — best-effort, never raises.

**The phrasing task (`intelligence/llm/prompts/grounded_answer.py`, v1).** Copied from the `story_view` skeleton: `VERSION="grounded_answer@v1"`, Haiku, `SUPPORTS_CACHE=False`, `SUPPORTS_ESCALATION=False`, `detect_complexity→low`, the defensive `_extract_json` parser. The SYSTEM is the keystone instruction: *answer ONLY from the EVIDENCE; if it's not there, say you cannot answer; never state a fact not in the evidence; cite the evidence ids used.* Output `{answer, cited_ids}`. Registered `grounded_answer_generation` in `prompts/registry.py` `_REGISTRY` + a Haiku-only chain in `router.py` `_CHAINS` (the `story_view_generation` template). The LLM **phrases** — it never retrieves, never refuses, never cites beyond the handed evidence (the structural anti-hallucination guarantee: it can only restate what it was given).

**Soft citation back-check (S7-Q-001).** If the model returns no `cited_ids`, log it but **do not** downgrade to refused in phase 1 (harden once real outputs are observed — the story_view "verified periodically" posture).

**Shape.** `conversation/answerer.py` (`build_answer` + `_citations`); export from `__init__`. `intelligence/llm/prompts/grounded_answer.py` + registry + router. **Verify:** answerer unit with a **stubbed phrase_fn** — empty evidence → refused with **zero phrase_fn calls**; answered path returns the evidence's citations; `phrase_fn`→None / raise → graceful refused; the model's `cited_ids` never replace the evidence's. Prompt-module unit — `build` → `PromptSpec` (`has_cache_blocks=False`), `_parse` extracts JSON, `registry.get("grounded_answer_generation")` resolves, `router` has its chain. `tests/test_llm_architecture.py` stays green (registry/router intact).

---

## D-163.4 — S7 slice 4: the thin `/ask` consumer surface + Phase 8 close

**Date:** 2026-06-04
**Substrates affected:** [S7] (consumed end-to-end); [v1] (`intelligence/conversation_bridge.py` + a `/ask` route + template). Closes greenfield Phase 8 on `phase-20-substrate-7-conversation`.
**Status:** Active — S7 slice 4 + Phase 8 close. The faculty reaches a user surface; the substrate is opened, built, and demonstrable.

The end-to-end wiring + the close. v1 owns the bridge (the allowed v1→substrate direction — the `substrate_insights` + `interpretation_phrasing` precedent).

**The bridge (`intelligence/conversation_bridge.py`, v1).** `answer_question(tenant_id, question_text, *, environment_id, requirement_key, object_api_name, api_key, model) -> dict`. The **dual-derivation** (verified `evolution/recompute.py`): one `with get_tenant_connection(tenant_id) as conn:` serves `SemanticOrgModel(conn)` (S1) **and** `Session(bind=conn)` (S6/S8/S2). A pure inner `_answer(s1, session, *, question, ctx, phrase_fn)` runs the whole faculty — `classify_intent` (None → a clarify-refusal dict) → `retrieve` → `assemble_evidence` → `build_answer` — and flattens the `Answer` to a JSON-safe dict (status / text / refusal_reason / citations). The wrapper owns connection-management + is **best-effort** (any failure → `available=False`, never raises — the `get_substrate_insights` contract). The `phrase_fn` closes over `llm_call(task="grounded_answer_generation", …)`; **when no `api_key` resolves, `phrase_fn` is a null returning `None`** → `build_answer` degrades to a refused-with-citations (so the page works without an LLM — the common empty-store prod path needs no LLM at all, since empty evidence refuses before phrasing).

**The route + page (`views.py` `/ask` + `templates/conversation.html`).** `@login_required` + inner `@require_page_permission("view_intelligence_report")` (the **exact** `/substrate-insights` gate — ba+admin+superadmin; reuse, no new permission). GET renders the form (a question box + an environment picker from `EnvironmentRepository.list_environments` + optional requirement-key / object-api-name pickers for the `impact` bounded context); POST resolves the env's LLM `api_key`/`model` (`EnvironmentRepository.get_environment` → `ConnectionRepository.get_connection_decrypted`, best-effort) and calls the bridge, rendering an answered card (text + citation chips) or a refused empty-state. CSRF via `{{ csrf_input | safe }}`; the component kit (`_empty_state`, `breadcrumbs`).

**Close.** Doc currency: `substrate_7_conversation/EVOLUTION.md` (slices 1–4 landed) + `SPEC.md` Status (Phase 8 realized). **Merge gate:** the S7 suites green (contract + intent + assembler + answerer + retrieval governance + the new bridge governance), `import primeqa.app` (route registers), `import primeqa.intelligence.conversation_bridge`; then merge `phase-20-substrate-7-conversation` → `main` via PR. **Deferred unchanged** (SPEC §6): the Control half, multi-turn + persistence, proactive, broad retrieval, the open-ended router, rich UI.

**Verify.** Bridge governance on the semantic `conn`+`seed` harness: `_answer(s1, session, …)` with a **stub phrase_fn** over seeded S6/S8/S1 — an answered failure-cause question (citations present), a refused empty-store question, a `None`-intent clarify-refusal, an impact question over a seeded object; `answer_question(-1, …)` → `available=False` (best-effort). The substrate stores are empty in prod until live runs (S7-Q-005), so the *answered* path is demonstrated only with seeded data; the *refused* path is the correct default. No Flask-client route test (the `JWT_SECRET`-gated integration layer doesn't run locally — the `substrate_insights` precedent tests the bridge inner, not the route).

---

## D-164 — UI Phase opened + Area 1 (Org Model & Sync / S1) design

**Date:** 2026-06-04
**Affected:** v2 runtime UI (Flask views + templates) + a v1 metadata-domain bridge to the S1 sync. No substrate-package change. Commits to `main` (v2 runtime, per CLAUDE.md).
**Status:** Active — opens the **UI Phase** (wire the product UI onto the substrate spine S1–S8). Map-first (the U0 plan); area-by-area. This entry opens the phase + designs **Area 1**.

**The phase.** The spine is built + merged, but the product UI still surfaces v1 (`meta_*`, v1 runs, v1 failure analysis) — `connected_orgs` / `sync_runs` / the S1 read-APIs have **zero** UI touchpoints (only `/ask` + `/substrate-insights`). The UI Phase re-wires each surface onto the spine: **reuse** the chrome (component kit, settings shell, auth, CSRF, permission decorators), **re-point / redesign / discard** the content tied to dead v1 pipelines. Seven areas (U0 map), sequenced 1→7; **Area 1 first** (the data tap — every downstream surface needs S1 data). The phase parallels the cutover — during Steps 3–4 both `meta_*` and S1 UIs coexist; the v1 halves are discarded at Step 5.

**Area 1 — Org Model & Sync (S1).** Surface the three S1 operational concepts on the existing `/environments` admin surface: **provision + trigger** a sync, **watch its status**, and **browse the synced org model**. Wire to the existing backend (D-150–D-153); build no new sync logic.
- **Bridge (v1-owned, best-effort — the `substrate_insights` pattern).** A v1 metadata-domain module `primeqa/metadata/s1_sync_console.py`: `trigger_s1_sync(tenant_id, environment_id, sf_instance_url, created_by)` = `ensure_connected_org_for_environment` (provision, idempotent) + `SyncJobStore.create_or_get_job` (enqueue); `read_s1_sync_status(tenant_id, environment_id)` = the env's `connected_orgs` row → latest `s1_sync_jobs` + `sync_runs` (`status` / `last_completed_phase` / counters / `error_message`). Tenant-scoped via `get_tenant_connection`; never raises (`available=False` / `not_provisioned`).
- **Screens** (reuse the `/environments` detail shell + component kit): a **"Substrate (S1) sync" panel** on env detail (a "Sync substrate" button → POST trigger; the current run state + phase + entity/edge counts + last-synced + errors); a **poll-based status** surface (no SSE — the sync engine has no event bus); a net-new **org-model browser** (read-only S1 entities for the env's current version, via `SemanticOrgModel`).
- **Gating:** `role_required("admin")` (the env-detail gate); reuse the `trigger_metadata_sync` permission for the trigger (same admin-ops class — no new permission). CSRF on the POST.
- **Slices:** 1a bridge + trigger route + env-detail panel · 1b poll status/progress · 1c org-model browser · 1d run #119 through the UI + `scripts/probe_phase0_s1.py` (= #82) + Area-1 close.

**Boundary.** Additive — a new panel + routes + a browser page; the v1 metadata-sync panel stays through the parallel run. No migration. Commits land on `main` (auto-deploy to the dev env, so each slice is visible). Substrate packages untouched — the bridge reads their public APIs only.

---

## D-165 — UI Phase Area 2 (Test Authoring / S2+S3): re-point generation onto the substrate

**Date:** 2026-06-04
**Affected:** v2 runtime UI (requirements + test-authoring views/templates) + a v1 bridge to S3 generation + S2 claim/recipe reads. Commits to `main`. No substrate-package change.
**Status:** Active — UI Phase Area 2 (sequence step 2). **User decision: REPLACE the v1 generate flow with S3** (not additive — discard the v1 `test_cases` authoring path as the substrate path lands).

**The finding.** S3 generation is a full, working backend with API routes (`POST /api/s3-generation-jobs` enqueue, status poll, cancel; it writes S2 `test_claims`/`test_recipes` + the S3 ledger) but **zero UI** — no surface displays a claim or an outcome. v1 generation (→ `test_cases`) is a *separate* pipeline. Area 2 re-points the authoring UI from v1 onto S2/S3.

**The coupling (flagged, not blocking).** Generation + execution are linked: v1 Generate → `test_cases` → v1 Run; S3 Generate → claims/recipes → **S4** (Area 3). Replacing generate (Area 2) without re-pointing execution (Area 3) leaves generated **claims viewable but not runnable** until Area 3. Accepted as a transitional build state (generate + view now, run next); the loop closes at Area 3.

**Verdict map.** Requirements list/detail → **re-point** (the Generate button targets S3; the linked-tests view shows S2 claims/recipes). Test detail → **redesign** (a claim is *semantic* — archetype / claim_kind / asserted_truth / semantic_conditions + recipes — not procedural steps/validation/story_view). Test Library → **net-new** claims library over S2. Suites / Reviews / Sections / Milestones → **deferred** (re-point later; reviews need a semantic-claim rethink). The v1 `test_cases` surfaces are discarded as their S2 replacements land (final removal at cutover Step 5).

**Slices.**
- **2a — S3 generation console** (the spine): a v1 bridge `primeqa/intelligence/s3_generation_console.py` (best-effort): `trigger_s3_generation` (resolve_requirement → `enqueue_s3_generation`) + `read_requirement_claims` (`coordinator.list_tests_by_requirement` → per claim `get_latest_claim` + `list_active_recipes`, flattened) + `read_s3_job_status` (`GenerationJobStore.get_job`). Re-point the **requirement detail**: the Generate button → S3 trigger; the linked-tests section → the requirement's S2 claims/recipes; the async progress → poll the S3 job. The direct analog of Area 1's sync console — and the next live proving (substrate generation over the freshly-synced S1).
- **2b — claim + recipe detail** (the semantic view).
- **2c — claims library** (`/claims`, list + search over S2).
- **2d — close** + record the deferred re-points + the Area-3 execution coupling.

**Boundary.** The re-point is a behaviour change to a v1 surface (not purely additive) — but the v1 generate path/code stays callable until its S2 replacement is proven; the UI just points at S3. Best-effort bridges (never break the page). No migration. Commits to `main`.

---

## D-166 — Substrate gap-closure: S3 persister writes the `generated_from` requirement link

**Date:** 2026-06-05
**Affected:** `primeqa/generation/persistence.py` (S3 `LedgerPersister`) + a semantic-harness test. Commits to `main`. Unblocks UI Phase Area 2 slice 2a (D-165).
**Status:** Active.

**Context (premise break, found building D-165 slice 2a).** 2a specced
`read_requirement_claims` to read via `coordinator.list_tests_by_requirement(
external_system="jira", external_key=key)`. Building it surfaced that the S3
generation pipeline **never writes a `TestRequirementLink`**:
`LedgerPersister._write_emission` writes the claim + recipe + the
`generation_outcomes.requirement_ref` ledger trace, but does not call
`link_requirement`. The only callers of `coordinator.link_requirement` in the tree
are its own unit tests. So `list_tests_by_requirement` returns empty for every
generated test, and the re-pointed requirement detail would show zero claims after
a successful generation.

This contradicts the **documented-intended** behaviour: `link_requirement`'s
docstring states *"Typically S3 creates `generated_from` links during generation"*
and its authority gate explicitly admits `actor="s3"` (only `s4` is barred). The
coordinator method is built and authority-cleared for exactly this write; the
persister simply never makes the call — an unimplemented-but-specced gap, not a
design choice.

**Decision (root-cause, Option C).** Close the gap at the source rather than read
the ledger as a UI-layer workaround. `LedgerPersister._write_emission` calls
`coordinator.link_requirement(session, actor="s3", test_id=cr.test_id,
external_system="jira", external_key=outcome.requirement_ref["key"],
link_kind="generated_from")` immediately after `write_claim`, inside the **same
atomic per-requirement transaction**. Written on **both** the new-claim and the
same-hash no-op path (idempotent on the PK `(test_id, external_system,
external_key, link_kind)`) so a re-generation that re-versions an existing test
still records that this requirement generated it. `external_key` reads off
`outcome.requirement_ref["key"]` (already populated on the outcome — no signature
threading); guarded so a missing/blank key skips the link rather than raising.
`external_version` stays NULL in v1 (the requirement_ref carries only `key`+`text`;
no jira_version is pinned yet).

**Boundary.** S3 substrate write-path, discovered mid-UI-phase. The CLAUDE.md
branch model routes substrate work to a `phase-N-substrate-M` branch, but this is a
~4-line gap-closure whose sole purpose is to unblock the UI phase's Area-2 read.
Committed to `main` with this D-entry as the audit record (the UI phase is
main-line), not a phase branch for one call.

**Verification.** A semantic-harness test drives a full S3 draft (the existing
draft-vertical setup) for a `requirement_ref` and asserts
`list_tests_by_requirement(external_system="jira", external_key=key)` returns the
test with `link_kind="generated_from"`. 2a then reads the clean surface exactly as
D-165 designed (no D-165 amendment).

**Unblocks.** UI Phase Area 2 slice 2a (now reads a populated surface); and S6
impact-by-requirement / coverage queries gain `generated_from` links for free.

---

## D-167 — UI Phase Area 2 (Test Authoring / S2+S3) — close

**Date:** 2026-06-05
**Affected:** docs-only (this entry). Area 2 implementation landed across commits
`d367b98..f99506c` on `main`. **Status:** Area 2 COMPLETE — sequence step 2 of the
UI phase closed; Area 3 (Execution / S4) is next.

**What shipped (D-165 design; D-166 + slices 2a–2c + #143).**
- **D-166** — the S3 persister writes the `generated_from` requirement link, the
  gap-closure that makes `list_tests_by_requirement` resolve generated tests.
- **2a** — requirement detail re-pointed: Generate → S3
  (`POST /requirements/<id>/generate-substrate`), linked-tests → the S2
  claims/recipes view, async progress → S3 job poll. v1 `test_cases` loading removed.
- **2b** — claim + recipe detail (`/claims/<test_id>`): the semantic view
  (archetype / claim_kind / asserted_truth / semantic_conditions + recipes) via a
  generic recursive body renderer over all 16 claim-kind shapes.
- **2c** — claims library (`/claims`): list + search + pagination over current S2
  claims; the **Test Library nav re-points to `/claims`** (the v1 `/test-cases`
  list is nav-orphaned, URL-reachable until cutover Step 5).
- **#143** — requirements list re-pointed to browse + S2 claim counts (bulk
  `count_claims_by_requirement`); the v1 per-row generate/run + bulk-generate +
  generate_overlay removed (they produced now-invisible v1 `test_cases`).
- The v1→substrate bridge is `primeqa/intelligence/s3_generation_console.py`
  (best-effort, tenant-scoped reads; mirrors `s1_sync_console` / `substrate_insights`).

**Verification.** 13 console bridge tests on the generation harness + the D-166
draft-vertical link test; the generation integration suite stays green. Two
adversarial review workflows over the area surface (one over 2a–2c, one over #143):
a single low/cosmetic finding (claims_detail `active_page` + breadcrumb → fixed at
`2f069cd`), zero other confirmed defects — SQL injection (bound params), XSS
(auto-escaped / `textContent`), cross-tenant claim read (`get_tenant_connection`
schema isolation), and the key-consistency chain all cleared.

**Deferred (recorded, not done).**
- **Suites / Reviews / Sections / Milestones** — still v1. Re-point in a later pass;
  **reviews** in particular need a semantic-claim rethink (review-of-a-claim is not
  review-of-procedural-steps).
- **Claim body prose** — 2b renders bodies structurally (key/value); prose-style
  per-kind summaries (could lean on S7 grounded phrasing) are a future enhancement.
- **Bulk S3 generation** — the v1 bulk-generate UI was removed, not re-pointed; an
  S3 bulk path is a follow-up.

**The Area-3 coupling (carried from D-165).** Generated claims are **viewable but
not runnable** — there is no Run button on the substrate path yet. Execution is
Area 3 (S4 UI); building it closes the generate→run loop and makes the v1-removed
Run affordance real again on the new path. Accepted transitional state.

**Next.** Area 3 — Execution (S4): the Run surface for claims/recipes
(`s4_execution_runs`), which also unblocks running everything Area 2 now generates.

---

## D-168 — UI Phase Area 3 (Execution / S4): design

**Date:** 2026-06-05
**Affected:** v2 runtime UI (execution surfaces) + a new v1→substrate bridge to S4
+ S6-verdict reads. Commits to `main`. **Status:** Active — UI Phase Area 3
(sequence step 3). Closes the generate→run loop: makes everything Area 2 generates
actually runnable.

**The map (workflow `ww1dwx0e7`, 4 facets, file+line cited).**
- The S4 execution engine is fully built and worker/scheduler-wired (run engine,
  `finalize`, the `s4_execution_jobs` queue, consumer + reaper), but **nothing
  enqueues jobs** — the worker no-ops ("queue empty until an enqueue source ships
  (deferred)"). The seam `ExecutionJobStore(tid).create_or_get_job(test_id,
  environment_id, created_by)` exists; only the caller is missing. This is Area 3's
  D-166-analog gap.
- A claim runs by **`test_id`** (the engine selects the one highest-priority
  eligible recipe at run time via `select_recipe_for_execution`). The natural
  trigger is from a **claim** — the S2→S4 entry, mirroring Area 2's per-requirement
  generate.
- **The verdict lives in S6, not S4.** S4 writes `s4_execution_runs` (outcome
  `passed/failed/errored` + per-step `evidence` JSONB) at finalize; S6 owns the read
  API (`interpretation/result_store.list_interpretations(session, claim_test_id=…)`
  → outcome **+ verdict + cause**). The `/substrate-insights` bridge already reads S6
  — the precedent idiom.

**Three constraints (recorded; the Area-3 analog of D-166's gap surfacing).**
1. **Async queue = metadata-recipe ONLY.** Data-recipes (behavioral-negative,
   value-claims) raise `PlanTranslationError` on the queue path; only the
   **synchronous** `run_recipe_execution_for_tenant` runs them (and returns
   outcome+verdict in one call).
2. **No production guard in the substrate** — zero `is_production` checks; the
   data-recipe path *mutates the org*. The prod-confirm gate **must live in the UI
   trigger** (reuse v1's `runs/bulk.environment_can_bulk_run` / the `is_production`
   pattern).
3. **No job→run link + no `list_runs`/`list_jobs`** — job↔run correlate only by
   `(claim, env, recency)`; a runs index needs a new list read. (The sync path
   sidesteps this — it returns the run result directly.)

**User decision — SYNC-FIRST for the spine (3a).** 3a calls the synchronous
`run_recipe_execution_for_tenant(tenant_id, test_id, environment_id=…)`: one
blocking request (seconds of SF I/O) that runs **all** recipe kinds and returns
`RunPathResult` (outcome + verdict + per-step evidence) directly — no poll, no
job→run correlation gap. Simplest + complete proof of the loop. The async queue +
bulk (and the correlation / async-data-recipe substrate work) come in 3d.

**Verdict map.** Claim detail (`/claims/<id>`) → **net-new Run + results** (the
spine). `/runs` list → **re-point** to S4 (needs a list read). `/runs/<id>` detail →
**redesign** (S4 evidence steps + S6 verdict; defer agent-fix + cost). `/run` 4-mode
picker → **reuse-chrome + re-point submit**, deferred to 3d (needs a v1-selection→S2
mapping + the queue). `/runs/new` wizard + `/runs/scheduled*` → **reuse-chrome,
defer**. `/results*` → **free** (redirects to `/runs`).

**Slices.**
- **3a — run-a-claim spine** (sync): a best-effort `primeqa/intelligence/s4_execution_console.py`
  (`trigger_claim_run` — prod-confirm gate + `run_recipe_execution_for_tenant`,
  returns outcome/verdict/per-step; tenant-scoped, never raises) + a **Run** form on
  the claim detail (env picker + dynamic prod gate) + the result render (outcome +
  S6 verdict + per-step evidence). Governance tests on the generation/exec harness.
- **3b — runs history** (`/runs` → S4): a list read over `s4_execution_runs` (a small
  read API or raw tenant-scoped bridge read), re-pointed, keeping the chrome
  (breadcrumbs / pagination / status+label filters / My-vs-All).
- **3c — run detail redesign** (`/runs/<id>` → S4): the run row + per-step evidence +
  the S6 verdict/cause; agent-fix + cost + SSE-richness deferred.
- **3d — async queue + bulk + `/run` re-point**: build the enqueuer route + poll,
  close the job→run correlation gap (a small substrate fix — a `last_run_id` on the
  job, mirroring S2's posture) and the async-data-recipe limit; re-point `/run`'s
  submit.
- **3e — close**: record deferred (scheduled, agent-fix, cost, wizard) + currency.

**Boundary.** Best-effort bridges (never break the page). The **prod-confirm gate is
the UI trigger's responsibility** (substrate enforces none). The sync 3a path holds a
DB connection across SF I/O — acceptable for single-claim on the dev env; the queue
(3d) is the scale path. v1 `pipeline_runs` surfaces stay until cutover Step 5. No
migration in 3a (a `last_run_id` job column lands in 3d if we take that option).
Commits to `main`.

---

## D-169 — UI Phase Area 3 (Execution / S4) — close

**Date:** 2026-06-05
**Affected:** docs-only (this entry). Area 3 implementation landed across commits
`ab5e57f..7697459` on `main`. **Status:** Area 3 COMPLETE at the synchronous-run
milestone — the generate→approve→run→inspect loop is live on the substrate. Area 4
(Results & Intelligence / S6+S8) is next.

**What shipped (D-168 design; slices 3a / 3c / 3b).**
- **3a** — the run-a-claim spine (sync) + **Approve**: `s4_execution_console`
  (`trigger_claim_run` via `run_recipe_execution_for_tenant` → outcome+verdict;
  `read_claim_runs`; `approve_claim` promotes the draft claim + its
  `generated_unapproved` recipes → runnable, humans-only). The claim detail gains a
  Run panel (env picker + **production-confirm gate at the UI trigger**, reusing
  v1's `environment_can_bulk_run`) + a Recent-runs panel. Closed the
  generate→approve→run loop.
- **3c** — run detail (`/runs/<uuid:run_id>`): the S4 evidence trace (per-step) +
  the S6 verdict / attribution / cause. Reached from the claim's run rows.
- **3b** — the global runs index (`/runs/substrate`): paginated `s4_execution_runs`
  LEFT JOIN the S6 verdict, newest-first; a focused new surface (the dense v1
  `/runs` is re-pointed at cutover Step 5, not gutted now). Linked from the claims
  library.
- The v1→substrate bridge is `primeqa/intelligence/s4_execution_console.py`
  (best-effort, tenant-scoped; mirrors `s3_generation_console`).

**Verification.** 14 console bridge tests on the substrate harness + the engine's
own `test_s4_run_path.py`. Three adversarial review workflows over the area: two
low/cosmetic findings — the Recent-runs recency ordering (fixed by reading
`s4_execution_runs.finished_at` LEFT JOIN S6, which also surfaces interpret-failed
runs) and a dead double-escaped `&mdash;` (dropped) — and **zero** substantive
defects (run/approve correctness, authz, production-safety, tenant isolation, SQL
injection, XSS all cleared).

**Deferred (recorded, not done).**
- **3d — async queue + bulk + `/run` re-point** (the scale path): an enqueuer route
  over `ExecutionJobStore.create_or_get_job` + a poll loop; close the **job→run
  correlation gap** (a `last_run_id` on the job, or correlate by claim+env+recency)
  and the **async data-recipe limit** (the queue path runs metadata-recipes only).
  Re-point the `/run` 4-mode picker's submit to S4 (needs a v1-selection→S2-`test_id`
  map).
- **v1 `/runs` re-point** — the dense v1 history/detail (filters, SSE log, agent-fix,
  cost) stays on `pipeline_runs` until cutover Step 5.
- **Agent-fix + cost** tabs — not S4-wired; v1-bridged until a later substrate lands.
- **Scheduled runs** — re-point the firing path to S4 at cutover.

**Carried constraints (from D-168, still true).** Async queue = metadata-recipe
only; the substrate enforces no production guard (the UI trigger owns it); no
job→run FK.

**Claim-approval seam (3a).** `approve_claim` is the minimal human-approval step
that makes a claim runnable; a richer review UI (the Area-2 deferred "reviews need a
semantic rethink" bucket) can relocate/reuse it.

**Next.** Area 4 — Results & Intelligence (S6/S8): expand `/substrate-insights` into
the real results surface (cross-run clustering, grounding-validity), building on the
per-run verdict/cause 3c already surfaces.

---

## D-170 — UI Phase Area 4 (Results & Intelligence / S6+S8): design

**Date:** 2026-06-05
**Affected:** v2 runtime UI — the `/substrate-insights` results surface + the
`s3_generation_console`-style read bridge (`substrate_insights.py`). Commits to
`main`. **Status:** Active — UI Phase Area 4 (sequence step 4). No substrate change
(every read already exists).

**The map (workflow `wouts4ial`, 4 facets, file+line cited).**
- **The results surface largely exists.** `/substrate-insights` (`views.py:259`,
  nav `navigation.py:144`, gated `view_intelligence_report`) already renders S6
  interpretations + 3 cross-run clusters + the S8 grounding table, via
  `intelligence/substrate_insights.py` `get_substrate_insights`. But it is a
  **static flat dump**: no recency (Section A uses `list_interpretations`, ordered
  by the random-uuid `run_id`), no drill-through (cluster `run_ids` dropped to a
  count), no severity on grounding, and the S8 `detail` JSONB (the *why drifted*) +
  S6 phrasing/evidence are dropped by the flatteners.
- **Every read Area 4 needs already exists** (no substrate work): S6
  `list_interpretations` / `read_interpretation` / `cluster_recurring_causes` /
  `cluster_by_vr` / `cluster_flapping`; S8 `list_grounding_validity(overall=…)` /
  `read_grounding_validity`; and Area-3's `s4_execution_console.list_runs` (S4-base,
  true `finished_at DESC`, verdict LEFT JOIN) + `read_run_detail`.
- **The v1 intelligence APIs have NO UI** — `/api/patterns`, `/api/explanations`,
  `/api/causal-links`, facts, deps are JSON-only with zero templates; S6 supersedes
  them. `/impacts` (real UI) maps to S8 grounding-validity. `/results` redirects to
  the v1 `/runs`.

**Verdict map.** `/substrate-insights` → **EXPAND** into the real Results &
Intelligence dashboard (the keystone). v1 `/results` + the "Results" nav → **defer**
(re-point at 4d, gated on parity). v1 intelligence APIs (patterns/explanations/
causal-links/facts/deps) → **discard** (no UI, S6 supersedes). `/impacts` list/detail
→ **reuse-chrome + re-point to S8 grounding** → deferred. The run detail (`3c`,
`/runs/<uuid>`) + claim-runs → the **drill-down targets** (already built).

**Slices.**
- **4a — recency-correct results spine** (keystone): rewire the insights bridge's
  Section A from `list_interpretations` (uuid-ordered, drops interpret-failed runs)
  → `s4_execution_console.list_runs` (S4-base, `finished_at DESC`, verdict via LEFT
  JOIN). Rows link to `/runs/<uuid>`; an "All runs →" to `/runs/substrate`.
- **4b — cross-run patterns drill-through**: the clusters (recurring causes / same-VR
  / flapping) become expandable — `run_ids` → run links, flapping `claim_test_id` →
  `/claims/<id>`.
- **4c — grounding drift board**: a filter (All / Drifted / Broken via
  `list_grounding_validity(overall=…)` + its index), severity colours, an
  "N drifted · M broken" headline, and a drill into the `detail` JSONB (the *why* —
  unresolved subjects, removed picklist values, VR reasons).
- **4d — close**: defer the `/impacts` re-point to S8, the "Results" nav cutover
  (`/results` → the substrate surface), and release-scoped results; record the gaps.

**Substrate gaps recorded (NOT buildable in the UI — substrate asks).**
- **No release→runs key** — clustering is release-blind (`clustering.py:11`); a
  "results for release X" view is a substrate migration, not a UI slice (**HOLD**).
- **S6 has no time axis** — worked around by reading `s4_execution_runs` as the base
  (the Area-3 fix), applied here in 4a.
- **S8 has no wall-clock** — only `evaluated_at_version_seq` (an S1 sync seq); "drifted
  at sync #N", not a date, until a seq→timestamp join lands.
- **Clusters carry no human labels** (raw UUIDs); a run_id→claim-name/timestamp join
  is a bridge follow-up.
- **`min_runs=2` hardcoded** in the bridge (not yet user-tunable).

**Boundary.** Best-effort bridges; all reads exist (no substrate change, no
migration). The expanded page reuses `/substrate-insights` (nav + permission
unchanged until 4d). **Empty-state is the DEFAULT** (the S6/S8 stores stay empty
until the live SF sync populates them — `#119`). One surface: fold clustering +
grounding into the expanded `/substrate-insights`; `/runs/substrate` (the full runs
list) stays as the drill-target it links to. Commits to `main`.

---

## D-171 — UI Phase Area 4 (Results & Intelligence / S6+S8) — close

**Date:** 2026-06-05
**Affected:** docs-only. Area 4 implementation landed across commits
`f7f04f9..161af3b` on `main`. **Status:** Area 4 COMPLETE — `/substrate-insights`
is the Results & Intelligence dashboard. Area 5 (Releases & Decisions) is next.

**What shipped (D-170 design; slices 4a / 4b / 4c).**
- **4a — recency-correct spine**: Section A reads `s4_execution_runs LEFT JOIN
  s6_interpretations` (`finished_at DESC`), surfaces interpret-failed runs, rows
  drill to `/runs/<uuid>`; payload key `interpretations` → `recent_runs`.
- **4b — cross-run patterns drill-through**: cluster `run_ids` → run links, flapping
  → `/claims/<id>`.
- **4c — grounding drift board**: severity colours + "N broken · M drifted" counts
  (`GROUP BY overall`, accurate) + the `detail` *why* (claim reason + unresolved
  subjects; per-recipe reason + removed picklist values).
- **No substrate change, no migration** — every read pre-existed.

**Verification.** 5 insights bridge tests + the mapping (workflow `wouts4ial`) + an
adversarial review (workflow `wdmammyi0`, 11 agents): **9 findings, 0 confirmed** —
SQL injection / tenant isolation / XSS on the org-derived grounding detail /
null-safety all cleared.

**Deferred (recorded, not done).**
- **`/impacts` → S8 grounding** — the metadata-impact concept maps to
  grounding-validity; reuse the `impacts/list`+`detail` chrome, re-source from S8.
- **The "Results" nav cutover** — `/results` still redirects to v1 `/runs`; re-point
  to the substrate surface at cutover Step 5. (The dashboard's nav entry is the
  existing "Substrate Insights".)
- **Server-side grounding filter** (`overall=`) — the read + index support it; the
  counts headline + severity give the at-a-glance, so the filter is a follow-up.
- **S6 phrasing on the dashboard** — per-run phrasing already shows on the run detail
  (3c `read_run_detail`); a dashboard headline could surface it once populated
  (D-117 is feature-gated, often empty in prod).
- **Cluster human labels** + a **seq→timestamp join** for grounding wall-clock + a
  **tunable `min_runs`** — bridge follow-ups.

**Substrate gaps (HOLD — not UI-buildable, from D-170).** No release→runs key
(release-blind clustering); S6 has no time axis (worked around via the S4-base read);
S8 has no wall-clock (S1-seq only).

**Empty-state note.** The S6/S8 stores stay empty until the live `#119` sync + the
first runs/recompute ticks land — the dashboard renders guided empty-states by
default.

**Next.** Area 5 — Releases & Decisions: fold S6 verdicts + S8 grounding into the
GO/NO-GO decision surface (mostly re-point, per the U0 map).

---

## D-172 — UI Phase Area 5 (Releases & Decisions): design

**Date:** 2026-06-05
**Affected:** v2 runtime UI (the release detail Decision tab) + a new v1→substrate
bridge (`release_substrate_console`). Commits to `main`. **Status:** Active — UI
Phase Area 5 (sequence step 5). No substrate change; read-only assembly.

**The map (workflow `wshnthmwg`, 4 facets).**
- **TWO distinct GO/NO-GO surfaces**: the **per-release** decision (`/releases/<id>`
  → Decision tab; `DecisionEngine.evaluate` → `release_decisions`; reads
  `release_runs`→`pipeline_runs`) and the **env-scoped** Release Owner dashboard
  (`/dashboard`, `dashboard.get_dashboard_data`, keyed on the active *environment*,
  reads `pipeline_runs.release_status`; `/shared/<token>` mirrors it). They share no
  verdict.
- **The linkage (load-bearing, confirmed buildable):** `release →
  release_requirements → requirements.jira_key → _requirement_to_ref(req)["key"]`
  (`jira_key` or `req-<id>`) `→ coordinator.list_tests_by_requirement(
  external_system="jira", external_key=key, link_kind="generated_from") → claim
  test_ids →` S8 grounding (`list_grounding_validity(test_id=)`) + S6 verdicts
  (`s4_execution_console._read_claim_runs`). The v1 **test-plan axis**
  (`release_test_plan_items` → `test_cases`) is a **dead end** to the substrate
  (`test_cases` ≠ claims); the **requirements axis is the only substrate-reachable
  path**.
- **No release key on `s6_interpretations` / `s8_grounding_validity`** (confirmed) →
  release-scoped clustering NOT buildable; per-claim aggregation IS (join axis =
  claim `test_id`).

**Decision.** An **additive substrate-evidence panel** on the release detail Decision
tab. The v1 `DecisionEngine` stays the **verdict authority**; the substrate **adds
evidence** (grounding-drift + per-claim run verdicts) — it does NOT produce a verdict
or flip the v1 recommendation (out of scope; no substrate→v1 verdict dependency is
specced). Mirrors Area 3's additive claim-detail Run panel.

**Verdict map.** `/releases/<id>` Decision tab → **ADD** a substrate-evidence panel
(the keystone). `/releases` list + other tabs → **reuse** (a per-release at-risk chip
is a later follow-up). `/dashboard` + `/shared` (env-scoped) → **defer** (stays v1; no
release context to hang per-claim evidence on). `/milestones` → **defer**. The v1
`DecisionEngine` + `RiskEngine` → **unchanged**.

**The bridge** (new `primeqa/intelligence/release_substrate_console.py`, best-effort):
`get_release_substrate(tenant_id, external_keys)` opens **one** tenant connection + a
shared session, resolves the release's requirement keys → claim `test_id`s (bulk,
deduped), then per claim reads S8 grounding (`list_grounding_validity(test_id=)`) + S6
latest verdict (`_read_claim_runs` **in-session** — the recency-correct,
interpret-failed-surfacing read), and rolls up: grounding `{intact/drifted/broken
counts + at-risk claims}` + verdicts `{passed/failed/never-run}`. Never raises
(`available=False` on error); **empty-state is the default** (stores empty until
`#119`).

**Slices.**
- **5a — the substrate-evidence panel** (keystone): the bridge + a panel on
  `/releases/<id>` Decision tab — a grounding-at-risk headline ("N of M claims
  at-risk · X drifted · Y broken" + the at-risk claims, with the *why*) + a
  run-verdict breakdown (passed / failed / never-run); each claim links to
  `/claims/<id>`.
- **5b — close**: record deferred (a per-release at-risk chip on the list; the
  `/dashboard` + `/shared` env surfaces; folding into `DecisionEngine` [out of scope];
  persisted release-grain verdicts [migration, out of scope]) + the substrate gaps.

**Substrate gaps (HOLD).** No release→runs key (release-scoped clustering not
buildable); the substrate has no decision engine (the v1 `DecisionEngine` stays
authoritative); the roll-up is the bridge's job.

**Boundary.** Best-effort bridge; **one shared tenant connection** (not N
`read_claim_runs` calls); read-only (no migration, no substrate write). The substrate
path is via release **requirements** only (test-plan items are a v1 dead end) — the
panel notes this. Commits to `main`.

---

## D-173 — UI Phase Area 5 (Releases & Decisions): close

**Date:** 2026-06-06
**Affected:** docs-only. Area 5 implementation landed in commits `f8dc4a0` (5a) +
`1b89a61` (5a review fix) on `main`. **Status:** Area 5 COMPLETE — the release detail
Decision tab carries an additive substrate-evidence panel. Area 6 (Conversation / S7
polish) is next.

**What shipped (D-172 design; slice 5a + its review fix).**
- **5a — the substrate-evidence panel** (keystone): new best-effort bridge
  `release_substrate_console.get_release_substrate(tenant_id, external_keys)` +
  `_assemble_release_substrate`. `releases_detail` (only on `tab=='decision'`) derives
  the release's requirement keys (`jira_key` or `req-<id>`), resolves them to claim
  `test_id`s via `coordinator.list_tests_by_requirement(generated_from)`, and per claim
  reads S8 grounding-validity + the S6 latest verdict (`_read_claim_runs` in-session).
  The panel renders **two count cards** (grounding broken/drifted/intact/not-computed +
  latest-run verdict passed/failed/errored/never-run) + an **at-risk claims list** with
  the *why* (claim reason + unresolved subjects from the S8 `detail` JSONB), each claim
  linking to `/claims/<test_id>`. The v1 `DecisionEngine` stays the GO/NO-GO authority —
  the panel ADDS evidence, it does not produce or flip the verdict.
- **5a review fix (`1b89a61`)**: ground the **approved** claim version
  (`get_current_approved_claim` → `read_grounding_validity(test_id, approved.version_seq)`,
  fallback to the latest grounding row only when no approved version exists), not the
  newest (possibly-draft) version — a release ships the approved version. Dropped the
  dead `claim_verdict` payload; render `latest_verdict` + `@ S1 seq N` (grounding
  staleness).
- **One shared tenant connection** (not N `read_claim_runs` calls), best-effort (never
  raises; `available=False` on error), **read-only — no migration, no substrate write**.
- **No substrate change** — every read pre-existed.

**Verification.** 4 dedicated bridge tests pass (roll-up over the approved-version
grounding path; unknown-key empty; empty-keys short-circuit `available=True/claim_count=0`;
best-effort bad-tenant `available=False`) + the 5a adversarial review (workflow
`wpgv6n1ph` — 3 confirmed of 7, all fixed in `1b89a61`) + a completeness-critic close
sweep (workflow `wr9vn41w0`, 4 facets): the surface is integrity-clean — None/unavailable
guarded before any dict deref, the empty-state guided, every template key produced by the
bridge, at-risk links resolve to `/claims/<uuid>`.

**Deferred (recorded, not done — from D-172 5b; verified none shipped).**
- **Per-release at-risk chip on the `/releases` list** (+ other tabs) — reuse-only today;
  the chip is a later follow-up (no substrate refs in `templates/releases/list.html`).
- **The env-scoped `/dashboard` + `/shared/<token>` surfaces** stay pure-v1 (they key on
  `pipeline_runs.release_status`; no release context to hang per-claim evidence on).
- **Folding substrate evidence into the v1 `DecisionEngine`** — OUT OF SCOPE; no
  substrate→v1 verdict dependency is specced. `DecisionEngine` / `RiskEngine` unchanged.
- **Persisted release-grain verdicts** — OUT OF SCOPE (would need a migration; the
  substrate has no release key, so the roll-up is computed live in the bridge each render).
- **`/milestones`** stays v1.

**Follow-ups (emerged in 5a + the reviews).**
- **N+1 read loop** (`2·K` queries: per claim a `get_current_approved_claim` + grounding
  read + `_read_claim_runs`) — un-batched; noise for 5a's empty-store reality, a batching
  follow-up.
- **Inlined key-mint** — `releases_detail` inlines `jira_key or req-<id>` rather than
  calling the canonical `s3_enqueue._requirement_to_ref`. Byte-identical today (no bug),
  but a drift-fragility; route both through the one helper.
- **Panel copy** could explicitly name the v1 test-plan axis as the excluded path (it
  conveys requirements-only today but does not spell out the dead end).

**Substrate gaps (HOLD — not UI-buildable).**
- **No release→runs / release→claims key** on `s6_interpretations` / `s8_grounding_validity`
  (S8 PK is `(test_id, version_seq)`; S6 is keyed by `run_id`) → release-scoped clustering
  is NOT buildable; only **per-claim aggregation** (join axis = claim `test_id`) is, and the
  bridge does the roll-up itself.
- **The substrate has no decision engine** — the v1 `DecisionEngine` stays authoritative.
- **Requirements-only reachability** — the v1 test-plan axis (`release_test_plan_items` →
  `test_cases`) is a substrate dead end (`test_cases` ≠ S2 claims). A requirement never
  generated through the substrate contributes **zero claims silently** (`claim_count` just
  stays lower).
- **The run-verdict leg cannot be claim-version-pinned** (NEW, from the close sweep):
  `s4_execution_runs` is keyed by `claim_test_id` + recipe version, not claim version, so
  there is no DB path to co-version the "last run" verdict with the approved-version-pinned
  grounding leg. The grounding leg is approved-version-pinned (5a fix); the "last run" is
  the latest execution across **any** claim version (the label is temporal, honest).

**Empty-state note.** The S6/S8 stores stay empty until the live `#119` sync + the first
runs/recompute ticks; `available=True, claim_count=0` ("No substrate claims for this
release yet") is the common production state — the panel ships before any populated
substrate data exists.

**Next.** Area 6 — Conversation (S7): `/ask` polish + nav (mostly reuse, per the U0 map).

---

## D-174 — UI Phase Area 6 (Conversation / S7): design

**Date:** 2026-06-06
**Affected:** v2 runtime UI (the `/ask` page + the sidebar nav) + small additive changes
to the `/ask` route. **No substrate change** — the S7 package (`primeqa/conversation/`)
and the v1 bridge (`intelligence/conversation_bridge.answer_question`) stay as-is.
**Status:** Active — UI Phase Area 6 (sequence step 6). Verdict: **reuse + polish + nav**.

**The map (workflow `wri7txi80`, 4 facets — current UI / substrate consumer contract /
cross-surface entry points / nav + polish).**
- `/ask` (`views.py:300`, GET/POST, gated `view_intelligence_report`) already runs on S7
  via `answer_question`, which is best-effort (never raises; `available=False` on error)
  and returns a 5-key dict: `available / status / text / refusal_reason / citations`
  (each citation `{id, source, kind, ref}`).
- **Nav gap (the #1 leverage):** `/ask` has **zero** nav entries and **zero** inbound
  links anywhere — URL-only. The sibling `substrate_insights` IS registered
  (`navigation.py:144-150`, same `view_intelligence_report` gate, section `testing`).
- **The bridge already returns signals the template DROPS:** 4 distinct `refusal_reason`s
  (`no_intent_match` / `no_grounding_evidence` / `phrasing_unavailable` / `unavailable`)
  all collapse into one generic `empty_state`; the `phrasing_unavailable` refusal carries
  **citations** that get dropped; the citation chip drops `c.kind`. All template-only —
  the data is already in the payload.
- **No guided empty (no-question) state**; **no submit spinner** (`loading.js` is wired
  globally but `btn_primary('Ask')` ships no spinner span).
- **Contextual entry points** need only a small route change: the route reads scope fields
  (`object_api_name` / `requirement_key`) **only on POST**; a GET-querystring prefill path
  unblocks "Ask about this" deep-links. `retrieve_impact` already consumes both keys, so
  requirement/release launchers scope correctly with **zero bridge change**.

**Decision (verdicts).** `templates/conversation.html` → **polish** (differentiate the 4
refusal reasons, surface citations on the evidence-bearing refusal, guided empty state,
spinner, render `c.kind`). The `/ask` route → **re-point** (additive GET-prefill). The
sidebar nav → **net-new** (one `ask` item). Contextual launchers (requirement / release /
substrate-insights → `/ask`) → **net-new**. The S7 package + `conversation_bridge` →
**reuse, untouched** (the substrate boundary holds; the bridge stays LLM-free-respecting
and best-effort).

**Slices.**
- **6a — nav entry + grounded-or-refuse polish** (template + nav only; no route / bridge /
  substrate change — the keystone): add the `ask` SIDEBAR_ITEM (mirror `substrate_insights`:
  `url=/ask`, `permission=view_intelligence_report`, `section=testing`); in
  `conversation.html` differentiate the 4 refusal reasons (rephrase / narrow-scope /
  retry+show-citations / sync-not-live), surface citations on `phrasing_unavailable`, add a
  guided empty state with clickable example-question chips for the 3 intents
  (`failure_cause` / `grounding_drift` / `impact`) that prefill the textarea, add a submit
  spinner span, render `c.kind` on chips.
- **6b — contextual entry points** (small additive GET-prefill on the route + launchers that
  scope via `requirement_key` / `object_api_name`, **visibility-gated on
  `view_intelligence_report`** so no broken 403 links): route accepts GET querystring prefill
  (`q` / `object_api_name` / `requirement_key` / `environment_id`); launchers on requirement
  detail ("Ask about this requirement" + "Are its claims still valid?"), release detail
  (per-requirement "Ask" + panel-level "Why are these claims at risk?"), and substrate-
  insights (header "Ask a question →").
- **6c — close (D-175):** record deferrals + substrate gaps + the empty-until-`#119` reality.

**Forks (leans).**
1. **Per-artifact scoping** ("is THIS claim drifting" / "why did THIS run fail") needs the
   **bridge** signature widened to pass `recipe_id` / `test_id` (the `QuestionContext` fields
   + recipes already honor them — additive, backward-compatible). For *run*, the run-detail
   context doesn't expose `recipe_id` and `failure_cause` has no `run_id` axis (a real
   substrate gap). **Lean: DEFER** — requirement/release launchers already deliver
   contextual-ask value with zero bridge change; keep 6b bridge-clean.
2. **Release-owner permission:** `release_owner_base` lacks `view_intelligence_report`, so an
   "Ask" launcher on the Release surface would 403 for the owner of that surface. **Lean:
   gate launcher visibility on the permission** (no broken links) + defer the "should release
   owners get `/ask`?" product decision.
3. **Rich-field flattener** (S8 `detail` / S6 `attribution` dropped by `retrieval.py`'s
   `_grounding_item` / `_interp_item` → answers say *which* not *why*). **Lean: DEFER** as
   substrate-side S7 work — editing the flatteners is not UI-phase wiring; don't cross the
   boundary in Area 6.
4. **Citation click-through, real object/requirement pickers, question-history, confidence/
   intent badge.** **Lean: DEFER all** — each needs a substrate-contract change (`Citation.ref`
   is a free-form audit string, not a typed link target; `Answer` has no confidence/intent
   field), a new table (history), or route data not currently passed (picker lists). Won't
   ship brittle ref-string parsing or half-pickers; the 6b deep-links sidestep the picker
   problem by pre-filling the exact key from the source page.

**Substrate gaps (HOLD — not UI-buildable).** `failure_cause` has no `run_id` scope axis
(only `recipe_id`); `Answer` carries no confidence / intent metadata; `Citation.ref` is a
free-form audit string, not a structured link target. These bound forks 1, 3, 4.

**Boundary.** Template + nav + an additive GET-prefill on the route. **No substrate write,
no migration, no bridge contract change** in 6a/6b (the bridge signature widening is the
deferred fork 1). The S7 package's LLM-free guard and the bridge's best-effort contract are
untouched. Every grounded answer is a refusal until the live `#119` sync populates S6/S8 —
6a's polish is verified on the refusal/empty paths (the live paths anyway). Commits to
`main`.

---

## D-175 — UI Phase Area 6 (Conversation / S7): close

**Date:** 2026-06-06
**Affected:** docs-only. Area 6 implementation landed in commits `fc4d0ba` (6a) +
`7fff9fa` (6b) on `main`. **Status:** Area 6 COMPLETE — the S7 `/ask` page is surfaced
in nav, its grounded-or-refuse UX is differentiated, and contextual launchers reach it
from the requirement / release / substrate-insights surfaces. Area 7 (Knowledge &
Settings/Admin) is next.

**What shipped (D-174 design; slices 6a + 6b).**
- **6a — nav + grounded-or-refuse polish** (`fc4d0ba`; template + nav only): the `ask`
  SIDEBAR_ITEM (mirrors `substrate_insights` — gate `view_intelligence_report`, section
  `testing`); `conversation.html` differentiates the 4 `refusal_reason`s the bridge already
  returns (`no_intent_match` → example questions; `phrasing_unavailable` → the carried
  citations + retry; `no_grounding_evidence` → narrow-scope hint; `unavailable` →
  sync-not-live) instead of one generic empty-state; renders `c.kind` on citation chips;
  adds a guided empty state (example-question chips that prefill the textarea) + the submit
  spinner. `btn_primary` gained a default-off `spinner` kwarg (wires the `[data-spinner]`
  glyph `loading.js` already reveals; non-spinner buttons render byte-identical).
- **6b — GET-prefill + contextual launchers** (`7fff9fa`): the `/ask` route prefills
  `q` / `requirement_key` / `object_api_name` / `environment_id` from the query string on
  GET — **prefill-only, never auto-runs an LLM call** (a GET stays safe/idempotent),
  length-capped. Launchers (all gated `has_permission('view_intelligence_report')` so no
  403 links): requirement detail "Ask about this" + release detail per-requirement "Ask"
  (both impact via `requirement_key`); substrate-insights header "Ask a question →".
- **No substrate change, no migration, no bridge contract change.** The S7 package's
  LLM-free guard + the bridge's best-effort contract are untouched.

**Design refinement (premise checked against code).** D-174 listed a release panel-level
*grounding_drift* launcher. `retrieval.py` confirms `retrieve_grounding_drift` scopes ONLY
by `ctx.test_id` and `retrieve_failure_cause` only by `ctx.recipe_id`, and `answer_question`'s
signature carries neither — so from the UI only **impact** (via `requirement_key` /
`object_api_name`) is honestly scopable. A "scoped to this release" grounding launcher would
silently run tenant-wide, so it was **dropped** rather than mislabel scope (confirms the
deferred fork-1 per-artifact scoping).

**Verification.** Build-time: all 6 `conversation.html` render states (empty / unavailable /
3 refusals / answered) verified end-to-end (stub base + real components); nav gating across
4 roles + active highlight (new `test_dynamic_ui` test); `btn_primary` default byte-identical;
the launcher construct (encoded href, `req-<id>` fallback, permission-gated); GET-prefill
logic (trim/cap/isdigit-drop) + a prefilled form landing in the inputs. Plus an **adversarial
review** (workflow `wpxjiyd15`, 3 dimensions): **0 high/medium** — XSS/injection clean (every
prefill value autoescaped, no `|safe`), authz clean (nav + launcher gates match the route
gate exactly; release owners — who lack `view_intelligence_report` — never see a 403 link;
the ungated substrate-insights link is safe because the page carries the same gate), tenant
isolation clean (a cross-tenant `?environment_id=` never sticks on render and is re-scoped on
POST). One review LOW (the `req-<id>` fallback can't ground) was **refuted on read**:
generation persists the `generated_from` link with `external_system="jira"` +
`external_key=jira_key-or-req-<id>` (`persistence.py:140`, `s3_enqueue.py:23`), and
`list_tests_by_requirement(link_kind=None)` matches it — the same key path the 5a release
console grounds on, so the fallback grounds correctly.

**Deferred (recorded, not done).**
- **Per-artifact scoping** ("is THIS claim drifting" / "why did THIS run fail") — needs the
  **bridge** signature widened to pass `recipe_id`/`test_id` (the `QuestionContext` fields +
  recipes already honor them; for *run*, `failure_cause` also lacks a `run_id` axis). Blocks
  the claim/run scoped launchers + the dropped release grounding launcher.
- **`environment_id` is inert** (NEW, from the review): the form's Environment picker is
  collected and threaded into `QuestionContext.environment_id`, but **no recipe reads it** —
  choosing an environment has no effect on the answer (the env picker + the no-evidence
  refusal copy imply a narrowing the recipes don't honor). Pre-existing (predates Area 6);
  a substrate-side follow-up (recipes filter by environment, or relabel the field as
  context-only).
- **Rich-field flattener** — `retrieval.py`'s `_grounding_item` / `_interp_item` drop S8
  `detail` + S6 `attribution`, so answers say *which* not *why*. Substrate-side S7 work.
- **Citation click-through** (`Citation.ref` is a free-form audit string, not a typed link
  target), **real object/requirement pickers** (the route passes only `environments`, not the
  metadata-object / requirement lists), **question history** (no persistence layer — a new
  table), **confidence/intent badge** (the `Answer` model has neither field).
- **Release-owner `/ask` access** — `release_owner_base` lacks `view_intelligence_report`, so
  release owners don't see the Ask launchers (visibility-gated, no broken links). Whether they
  *should* get `/ask` is a deferred permission-model product decision.

**Substrate gaps (HOLD — not UI-buildable).** `failure_cause` has no `run_id` scope axis
(only `recipe_id`); `grounding_drift` / `failure_cause` scope only by `test_id` / `recipe_id`
(unreachable from the UI without the bridge widening); the recipes ignore `environment_id`;
`Answer` carries no confidence / intent; `Citation.ref` is a free-form audit string, not a
structured link target.

**Empty-state note.** The S6/S8 answer stores stay empty until the live `#119` sync; every
grounded answer is a refusal today — the polish + launchers are verified on the refusal/empty
paths (the live paths anyway), and the launchers will return grounded refusals until sync.

**Next.** Area 7 — Knowledge & Settings/Admin: S5 knowledge admin + re-point the SF
connection for S1 (per the U0 map). That closes the UI Phase's area sweep (Area 1's live
`#119` proving + 1d close remains parked separately).

---

## D-176 — UI Phase Area 7 (Knowledge & Settings/Admin): design

**Date:** 2026-06-06
**Affected:** v2 runtime UI (a net-new `/knowledge` admin page + the nav slot) + a new
best-effort read bridge over the S5 knowledge library. **No substrate change** — the
`primeqa/knowledge/` package is read-only/file-backed and stays untouched. **Status:**
Active — UI Phase Area 7 (sequence step 7, the last area). Verdict: **read-only knowledge
viewer + reuse**.

**The map (workflow `wq9j0g825`, 4 facets) — two grounded refinements of the U0 plan.**
- **S5 knowledge is read-only + file-backed.** Three channels behind `primeqa/knowledge/`:
  **System Rules** (global, `salesforce_knowledge/system_rules.json`, 33 rules),
  **Domain Packs** (global, `salesforce_domain_packs/*.md`, 1 pack), **Learned Rules**
  (per-tenant, *derived* from the v1 `generation_quality_signals` table via
  `feedback_rules.build_rules_block` — not stored in S5). **Zero write methods; no DB
  table backs S5 content.** Pack/rule files are **trusted git-controlled content — MUST
  NOT be user-uploaded/edited** (prompt-injection defence).
- **Nav slot reserved:** the `knowledge` SIDEBAR_ITEM exists (`enabled:False`, url
  `/knowledge`, gate `manage_knowledge`) but no route/page exists and `manage_knowledge`
  gates nothing today.
- **All 8 settings pages are pure-v1-admin** (general / agent / permission-sets / users /
  test-data / llm-usage / my-llm-usage) — none touch the substrate.
- **The SF-connection→S1 re-point is effectively ALREADY DONE by Area 1 (D-164).**
  `connected_orgs` is the sync *target*; the v1 environment+connection is the credential
  *source* (`sync/credentials.resolve_sync_sf_client` reads `env.connection_id →
  get_connection_decrypted`). The `connected_orgs.oauth_*` columns are **dead** for the S1
  path. Area 1's env-detail S1-sync panel + `/environments/<id>/sync-substrate` +
  `/org-model` already own provisioning. So connections **reuse as-is**.
- **Domain-pack attribution** (`llm_usage_log.context->domain_packs_applied`) is
  **write-only** — captured at generation, never read/rendered.

**Decision (verdicts).** Knowledge admin → **net-new, read-only** (a viewer over the three
S5 channels; the "curate/manage" ambition is **DISCARDED** — trusted-content boundary + no
write API). All settings pages → **reuse**. Connections + the SF→S1 re-point → **reuse**
(already covered by Area 1; the v1 connection is the credential source S1 reads through).
Per-tenant Story/Packs flag toggles (superadmin `/settings/llm-usage`) → **reuse** where
they are.

**Slices.**
- **7a — read-only Knowledge admin** (keystone): a `/knowledge` route + template, flip the
  reserved nav slot `enabled:True`, gate `manage_knowledge`. Three sections mirroring the S5
  channels — **System Rules** (`SystemPromptRulesProvider().get_rules(ctx)` → table of
  id/object/field/category/rule_text/confidence), **Domain Packs**
  (`DomainPackLibrary(...).load()` → cards: id/title/keywords/objects/version/measured_tokens),
  **Learned Rules** (`feedback_rules.build_rules_block(tenant_id)`, read-only). A thin
  best-effort `knowledge_console` bridge over the library reads. The trusted-content boundary
  shown as a visible "git-controlled — edit via PR" label; the dormant object-match path
  noted. **Zero substrate change, no migration.**
- **7b — close (D-177):** record the verdicts + deferrals, and mark the **UI Phase area
  sweep COMPLETE** (Areas 1–7; Area 1's `#119` live-proving + 1d close remain parked).

**Forks (leans).**
1. **SF-connection re-point scope.** The U0 plan named it for Area 7, but the map shows it
   is done by Area 1. The one genuine gap is **env-edit SF-connection re-pick** (an env's
   `connection_id` is settable only at create), and it carries a real caveat: swapping
   `connection_id` under a fixed `connected_org` (which keys on `environment_id`) silently
   changes the credential source under a fixed sync target — it needs a design decision
   (re-provision on swap?). **Lean: DEFER** as an Area-1-adjacent follow-up with that note —
   don't rush a caveated mutation into the final area.
2. **Knowledge URL.** `/knowledge` (the reserved top-level nav slot, section *admin*) vs
   `/settings/knowledge` (inside the hardcoded settings shell). **Lean: `/knowledge`** —
   honor the pre-wired slot; flip `enabled:True`, no settings-shell edit.
3. **Attribution-fired view + enablement matrix** (which packs fired / which tenants have
   them on). **Lean: DEFER** — write-only data with no read path yet, empty until packs fire
   in prod; a superadmin analytics follow-up, not the tenant knowledge viewer.

**Substrate gaps (HOLD — not UI-buildable).** No write/curate API for S5 (file-backed; the
`curated` source + `org` scope are *reserved* in `provider.py` but unimplemented — a
curated-knowledge store is a future substrate capability, not a re-point); learned rules are
read-only by design; domain-pack attribution has no read path; `connected_orgs.oauth_*` are
dead/plaintext (do not build a credentials-on-`connected_orgs` surface).

**Boundary.** 7a is a route + template + a best-effort read bridge over the existing S5
library — **no substrate write, no migration, no new substrate code**. Read-only by
contract; the page labels the git-PR authoring path so the trusted-content boundary is a
visible affordance, not a hidden landmine. Commits to `main`.

---

## D-177 — UI Phase Area 7 (Knowledge & Settings/Admin): close + UI Phase capstone

**Date:** 2026-06-06
**Affected:** docs-only. Area 7 implementation landed in commit `ac0bcf3` (7a) on `main`.
**Status:** Area 7 COMPLETE — a read-only `/knowledge` admin surfaces the S5 knowledge
substrate. **The UI Phase area sweep (Areas 1–7) is COMPLETE** (Area 1's `#119` live-proving
+ 1d close remain parked separately).

**What shipped (D-176 design; slice 7a).**
- **7a — read-only Knowledge admin** (`ac0bcf3`): new best-effort bridge
  `knowledge_console.get_knowledge_overview(tenant_id)` reading the three S5 channels
  independently — **System Rules** (`SystemPromptRulesProvider.get_rules`, file-backed),
  **Domain Packs** (`DomainPackLibrary.load`, basename only — no abs-path leak), **Learned
  Rules** (`feedback_rules.build_rules_block`, per-tenant). A `/knowledge` route gated
  `manage_knowledge`; the reserved nav slot flipped `enabled:True`; a `knowledge.html`
  template with three read-only sections + a visible **trusted-content banner** (git-PR
  authoring). **Read-only by contract; no substrate change, no migration.**

**Verdicts (the rest of Area 7).** All 8 settings pages → **reuse** (pure-v1 admin, no
substrate touch). Connections + the SF→S1 re-point → **reuse** — the re-point is effectively
done by Area 1 (`connected_orgs` is the sync *target*; the v1 connection is the credential
*source* S1 reads through). Knowledge *management* (UI write/curate) → **DISCARDED** —
trusted-content boundary + no write API.

**Verification.** Build-time: the bridge reads real S5 data (33 system rules, 1 domain pack);
all template states render (populated / learned-with-signals / all-unavailable / XSS-escaped);
nav gating (admin sees Knowledge, tester/developer don't); 6 hermetic bridge tests + the
`test_dynamic_ui` 4c nav test. Plus an **adversarial review** (workflow `wtffq6sxd`, 3
dimensions): **0 high/medium** — XSS clean (every field autoescaped, verified empirically,
incl. the learned-rules block whose text has a user-data lineage); authz clean (route gate +
nav gate both `manage_knowledge`, read-only, tenant-scoped, no path leak); best-effort
confirmed at runtime (`get_knowledge_overview` never raises on a dead DB; the template renders
every channel state under StrictUndefined). One **info nuance** (record-only): on DB-down the
learned channel reports `available=True, has_signals=False` (not `False`) because
`feedback.recent_for_tenant` swallows the error upstream — UI impact none (renders "No learned
rules yet", the correct degraded outcome); the swallow is pre-existing code outside this slice.

**Deferred (recorded, not done).**
- **Env-edit SF-connection re-pick** — an env's `connection_id` is settable only at create;
  re-pointing carries a caveat (swapping `connection_id` under a fixed `connected_org` changes
  the credential source under a fixed sync target — needs a re-provision-on-swap design note).
  An Area-1-adjacent follow-up.
- **Domain-pack attribution-fired view + enablement matrix** — `domain_packs_applied` is
  write-only; a "which packs fired / which tenants have them on" view is a superadmin analytics
  follow-up, empty until packs fire in prod.
- **The learned-channel DB-down nuance** (above) — cosmetic; not fixed (the swallow is upstream).
- **Curated / org-scoped rules** — the `curated` source + `org` scope are *reserved* in
  `provider.py` but unimplemented; a write-backed tenant knowledge store is a future substrate
  capability, not a re-point.

**Substrate gaps (HOLD — not UI-buildable).** No write/curate API for S5 (file-backed; trusted
git-controlled); learned rules read-only by design; domain-pack attribution has no read path;
`connected_orgs.oauth_*` are dead/plaintext (no credentials-on-`connected_orgs` surface).

---

### UI Phase capstone — the area sweep (Areas 1–7)

The UI Phase re-wired the product UI onto the substrate spine, area by area. Each area ran
the same rhythm: an exhaustive mapping workflow → a design D-entry (HOLD/GO) → slices
(HOLD/GO each, build → adversarial-review) → a close D-entry. All on `main` (Railway
continuous-deploy), author `AK`, zero `Co-Authored-By`, append-only `DECISIONS_LOG`.

- **Area 1 — Org Model & Sync (S1):** the S1-sync bridge + env-detail trigger panel +
  poll-based status + the read-only org-model browser (1a–1c). **1d (live `#119` proving +
  close) parked** on the sync report — the substrate data tap every downstream panel reads.
- **Area 2 — Test Authoring (S2/S3) (D-167):** requirement detail + claim/recipe detail +
  the `/claims` library, re-pointed onto S3 generation + S2 claims; the `generated_from`
  requirement link closed (D-166).
- **Area 3 — Execution (S4) (D-169):** run-a-claim spine (sync) + run detail (evidence +
  S6 verdict/cause) + the global `/runs/substrate` list; Approve-claim coupling.
- **Area 4 — Results & Intelligence (S6/S8) (D-171):** the recency-correct results spine +
  cross-run cluster drill-through + the grounding-drift board on `/substrate-insights`.
- **Area 5 — Releases & Decisions (D-173):** the additive substrate-evidence panel on the
  release Decision tab (grounding + per-claim verdicts via the release's requirements).
- **Area 6 — Conversation (S7) (D-175):** the `/ask` nav entry + grounded-or-refuse polish +
  GET-prefill contextual launchers from requirement / release / substrate-insights.
- **Area 7 — Knowledge & Settings/Admin (D-177):** the read-only `/knowledge` viewer over
  S5; settings + connections confirmed reuse (SF→S1 already done by Area 1).

**The standing reality.** The S6/S8/S7 answer stores stay empty until the live `#119` sync +
the first runs/recompute ticks land, so every substrate-backed surface (Areas 4/5/6) renders
guided empty-states by default today — they light up when the sync goes live. **The parked
threads:** Area 1's `#119` live-proving + 1d close (the data tap), and Area 3's 3d (async
execution queue + bulk + `/run` re-point).

---

## D-178 — S1 sync resilience: scheduler crash-isolation + periodic heartbeat (the 1d outage fix)

**Date:** 2026-06-06
**Affected:** `primeqa/scheduler.py` (`run_scheduler` loop + `scheduler_tick`),
`primeqa/sync/consumer.py` (periodic heartbeat during `run_sync`), `primeqa/sync/jobs.py`
(reaper window), `primeqa/metadata/s1_sync_console.py` + the env-detail panel (orphaned
state). **No migration.** **Status:** Active — the proper fix for the parked `1d` / `#119`,
surfaced by running the live sync. Commits to `main` (live-runtime hardening — it must
deploy).

**The bug (forensically confirmed, not asserted).** Running `#119` from the UI exposed the
S1 sync stuck 15h at phase `Object` (146 entities, 0 edges, `sync_run.status='running'`).
Root-caused via the live DB: `s1_sync_jobs.job=1` was `running`, `attempt_count=1`, heartbeat
**904 min (15h) stale** — 20× past the 45-min reaper window — yet never reaped, despite the
reaper SQL (`status IN ('claimed','running') AND COALESCE(heartbeat_at,claimed_at) < threshold`)
matching it and `tenant_1` being enumerated. The decisive evidence: `worker_heartbeats.
died_reason='heartbeat_timeout'` is stamped **only** by the scheduler's `reap_stale_workers`
(an *early* tick in `scheduler_tick`); its last occurrence was `2026-06-05 08:58:58`, then
**zero for 15h**. So `scheduler_tick` stopped running entirely ~15h ago — the **scheduler
process died and never recovered**. The mechanism: `run_scheduler`'s `while True` loop wraps
`scheduler_tick(ctx)` in `except KeyboardInterrupt` only — **any other tick exception
propagates out of the loop and exits the process**. One transient failure → scheduler down →
s1 reaper never runs → `job=1` orphaned → the enqueuer (which skips a tenant with an "active"
`running` job) never enqueues a resume → 15h stall. (Re-clicking "Sync substrate" is a no-op:
`create_or_get_job` returns the active `running` job.)

**The fix (three root-cause changes; all code, no migration).**
- **Fix 1 — `run_scheduler` per-iteration isolation (critical).** Wrap `scheduler_tick(ctx)`
  in `try/except Exception` → log, best-effort `ctx["db"].rollback()` (clear a poisoned
  transaction so the next tick isn't wedged), `continue`. A transient tick error can never
  again kill the scheduler. *This alone revives the scheduler permanently and auto-reaps the
  15h orphan.*
- **Fix 2 — `scheduler_tick` per-tick isolation (defense in depth).** Run the tick sequence
  in a guarded loop so one failing reaper can't skip the ones after it (the s1 ticks run
  *last*, after 12 others). Logs the failing tick by name.
- **Fix 3 — sync heartbeat hardening (the fragility that made a worker death invisible for
  15h).** Realize the deferred (`consumer.py:25`) **daemon-thread periodic heartbeat** during
  `run_sync` (beats ~every 30 s via `SyncJobStore.heartbeat`, which opens its own connection →
  thread-safe), stopped in a `finally`. With real liveness signal, lower the s1 reaper window
  (45 → ~10 min) for fast recovery without false-reap risk. The env-detail panel distinguishes
  **orphaned** (`run=running` but the job heartbeat is stale) from actively-syncing.

The orphaned `job=1` unblocks for free: the revived, crash-hardened scheduler reaps it on its
first tick → enqueuer resumes from `Object` → worker (now beating) runs to `Flow`. **No manual
SQL reset** (that would be the workaround).

**Critical operational constraint.** Each push to `main` SIGTERMs the worker (138 SIGTERM
deaths in `worker_heartbeats` — the deploy churn). So **all impl changes land in one push**,
then **no further pushes until the resumed sync reaches `Flow`** — a second push mid-resume
would re-kill it. The docs-only design commit may precede; the impl commit is the single
runtime push.

**Slices.**
- **1d-a — scheduler resilience** (Fix 1 + Fix 2) + unit tests (loop survives a throwing tick;
  every tick runs despite one throwing).
- **1d-b — sync heartbeat + reaper window + panel orphaned-state** (Fix 3) + tests (the beat
  thread beats then stops; orphaned-state derivation).
- Both impl changes ship in **one** push; then the strict no-push hold.
- **1d close (after live verification):** the resumed sync reaches `Flow` with `edges > 0` and
  entities across types, the org-model browser renders it — the actual `#119` proving — then
  close the parked `1d` / `#119`.

**Verification.** Offline: the loop doesn't exit when a tick raises; `scheduler_tick` runs all
ticks despite one throwing; the heartbeat thread beats + stops cleanly. Live (approved
read-only queries): scheduler reaps `job=1` → resume → `sync_run` advances past `Object` →
reaches `Flow` with edges + multi-type entities.

**Boundary.** Live-runtime hardening on `main`; no migration, no substrate-contract change. The
s1 reaper / enqueuer / resume machinery is already correct — this makes the **scheduler that
drives it** crash-resilient and the **sync liveness** real-time, so a single worker death can
never again become a silent multi-hour outage.

---

## D-179 — S1 enrichment provider keys resolve from per-env connections

**Date:** 2026-06-06
**Affected:** `primeqa/worker.py` (enrichment subticks + a new per-env key resolver),
`primeqa/intelligence/embeddings.py` (`embed_batch` injected key), `primeqa/core/repository.py`
(`_sensitive_fields['llm']` += `voyage_api_key`) + the LLM-connection UI. **No migration**
(Option A). **Status:** Active. Commits to `main`. Surfaced by the `#119` live sync: the run
finished structurally but the env-detail panel shows "running" forever because finalization is
gated on enrichment, which was skipping on missing env vars.

**The problem (forensic, from the live sync).** S1 enrichment has two providers — **summaries**
(Anthropic, via the LLM gateway) and **embeddings** (Voyage). Both read **bare worker env vars**
(`ANTHROPIC_API_KEY` at `worker.py:1035`; `VOYAGE_API_KEY` at `embeddings.py:64`). The user's
Anthropic key already lives in the env's **LLM connection** (generation resolves it via
`_default_s3_api_key_resolver`, `worker.py:1186`); enrichment just bypasses that. Worse, the
summary subtick's no-key path **early-returns without claiming** (`worker.py:1036-1041`),
stranding the queue rows in `pending` → `compute_org_status` never reaches `complete` →
`maybe_finalize_run` never flips the run to terminal → **`sync_run.status='running'` forever**
(the org model itself is fully readable regardless — version 43 current, 146 objects).

**The map (workflow `win0o1xkd`, 3 facets).**
- The correct Anthropic resolver already exists and is proven (`_default_s3_api_key_resolver`:
  `(tenant_id, environment_id) → env.llm_connection_id → get_connection_decrypted →
  config['api_key']`). Embeddings already classify a missing key as **non-retryable →
  failed_permanent** (finalize-safe); only the summary early-return hangs finalization.
- **Plumbing gap:** the enrichment worker has `tenant_id` but **no `environment_id`** (unlike the
  s3/s4/s1 ticks, which carry it on a job row). The only bridge is per-row, transitive: entity →
  `connected_org_id` (`_fetch_org_ids`, already computed as `org_by_id`) → `connected_orgs.
  environment_id` → the env's connection. So keys resolve **per the row's org's environment**,
  memoised per-tick to bound v1-session churn.
- `connected_orgs.environment_id` is nullable; a NULL-env org (pre-D-150, or out-of-band) →
  treat as **failed_permanent** (the run still finalizes `partial_success`), never re-hang.

**Decision (the Voyage fork — Option A chosen).** The Voyage key rides the **existing LLM
connection** as a second secret (`config['voyage_api_key']`), NOT a new connection type. Both
enrichment keys then come from the env's `llm_connection_id` — no migration, no new
`connection_type`, no `environments.embedding_connection_id` column, no env-edit-picker work.
The dedicated-`embeddings`-type alternative (Option B) was ~5× the surface (CHECK migration + env
FK column + connection UI + env create/edit pickers + a `test_connection` branch) for the
identical outcome; rejected for scope. Trade-off accepted: one connection row holds two provider
keys (Anthropic + Voyage).

**Slices.**
- **A — Anthropic from the LLM connection (worker-only):** add an org→env→key resolver +
  per-tick memo; rewire `_summary_subtick` to resolve the Anthropic key per-row (drop the
  `os.environ` read + early-return); no key / no LLM connection / NULL env → `_credit_fail(
  retryable=False)` (failed_permanent) so the run finalizes. Tests. No migration, no UI.
- **B — Voyage from the LLM connection:** add `voyage_api_key` to `_sensitive_fields['llm']`
  (Fernet round-trip) + a Voyage-key input on the LLM-connection new/edit UI; add an `api_key`
  param to `embed_batch` (env-var fallback retained for one release, with a warning); rewire
  `_embedding_subtick` (and the summary subtick's internal `embed_batch` call) to resolve the
  Voyage key per-org's-env from `config['voyage_api_key']`. Tests.
- **C — close (D-179 close):** docs + the ops note.

**Caveats.** Until an env's LLM connection carries the keys, enrichment fails-permanently and
syncs finalize `partial_success` (org model still fully readable). The `embed_batch` env-var
fallback stays one release (warned) so prod doesn't go dark on deploy. `embeddings.py` keeps
`model='voyage-3'` / dim 1024 hardcoded (the pgvector column dim is fixed) — the connection
carries only keys, not the model.

**Boundary.** Worker + connections, no migration, no substrate-schema change. The org model is
already synced + readable (D-178); this is the enrichment + status-finalization layer. Commits
to `main`.

---

## D-179 (close) — S1 enrichment provider keys now resolve per-env from the LLM connection

**Status:** Built + shipped to `main` (Railway redeploys web/worker/scheduler). Slices A + B + C
landed; no migration. Closes the enrichment-key half of the 1d outage work (D-178 fixed the
scheduler/sync half).

**Realized state.**
- **Slice A (`76dc2c1`) — Anthropic per-env.** `worker._summary_subtick` no longer reads
  `ANTHROPIC_API_KEY` / `os.environ`; it resolves the key per claimed row's org → environment →
  `llm_connection_id` → `config['api_key']` via `_resolve_org_keys` (new helper: batch
  `connected_orgs(id→environment_id)` lookup, then `_default_s3_api_key_resolver`, memoised per
  env per tick). A row whose org has no env / no LLM connection / no key → `_credit_fail(
  retryable=False)` → `failed_permanent`, so the run finalizes instead of hanging.
- **Slice B (`880894b`) — Voyage per-env.** `voyage_api_key` is a second secret on the LLM
  connection (`_sensitive_fields['llm']` → Fernet round-trip), with an optional input on the
  connection new/edit UI. `embed_batch(api_key=None)` uses the passed key; `None`/blank → env-var
  fallback **with a logged warning** (one-release safety net + system callers).
  `_embedding_subtick` groups embeddable rows by their org's per-env Voyage key and embeds per
  group; a no-key group → `failed_permanent` (queue drains, run finalizes). `_summary_subtick`
  resolves the Voyage key the same way for its internal summary-embed.
- **Slice C (this entry) — close.** Docs + the ops note below.

**Verification.** `py_compile` clean (worker / embeddings / repository / views); Jinja-parse
clean (connections new + edit). **Full unit suite 2263 green**, including 18 new Slice-A/B tests:
embed_batch `api_key` (used / overrides-env / blank-falls-back-with-warning / missing-non-
retryable), per-env Voyage grouping (distinct keys → N embed calls), no-key → fail-permanent
(both subticks), and the `voyage_api_key` Fernet round-trip + sensitivity-set membership. Live
finalization (`running → success/partial_success`) is the post-key-set confirmation — pending the
ops step below.

**OPS NOTE (action required for live finalization).** Enrichment now reads BOTH provider keys
from the environment's LLM connection — nothing comes from worker env vars anymore. For an
environment's syncs to finalize `success` (vs `partial_success`), that env's `llm_connection_id`
connection must carry:
- `api_key` (Anthropic) — already present in the production connection; and
- `voyage_api_key` (Voyage) — **NEW field; set it via /connections → edit the LLM connection →
  "Voyage embedding key".**
Until the Voyage key is set, embedding rows fail `failed_permanent` and syncs finalize
`partial_success`. **The org model is fully synced + readable regardless** (D-178) — only the
semantic-search/summary enrichment layer is gated on the keys.

**Deferred / carried.**
- The `embed_batch` `VOYAGE_API_KEY` env-var fallback is retained **one release** (warned on use)
  so a deploy doesn't go dark before the key is set; remove it in a follow-up once every env's
  LLM connection carries the Voyage key.
- `embeddings.py` keeps `model='voyage-3'` / dim 1024 hardcoded (the pgvector column dim is
  fixed); the connection carries keys only, not the model. A configurable embedding model is a
  separate change behind a dim-aware migration — not in scope.
- 1d / #119 / D-178 close: the S1-sync proving is structurally achieved (org model synced +
  readable, scheduler/sync resilient); the formal close is the next step once live enrichment
  finalization is confirmed post-key-set.

**Boundary held.** Worker + connections + the LLM-connection UI; zero migrations, zero
substrate-schema change. Commits direct to `main`.

---

## D-180 (design) — Re-enrich requeue: drain stranded `failed_permanent` enrichment through the product

**Status:** Design. Follows D-179. Triggered by the live finding below; user chose "build a requeue
action first" over hand-running SQL. No migration.

**Problem (confirmed live, tenant_1).** Enrichment queue rows that reach `failed_permanent` are
reset to `pending` ONLY on a *new structural change* to that entity — `materialize._batch_upsert_
queue`'s `ON CONFLICT (entity_type, entity_id, primitive_type) DO UPDATE` (materialize.py:769),
and unchanged entities are deliberately NOT re-enqueued (design §5; `_batch_touch_existing` only
refreshes `last_synced_at`). So when the failure was *systemic* — the keyless-worker bug D-179
fixed — and the org is otherwise stable, the rows strand permanently: the org reads
`ai_enrichment_status='complete'`, the run finalizes `partial_success`, and zero embeddings exist,
with no product-level remedy short of `psql`. Live read: 5870 embedding + 63 summary rows all
`failed_permanent` with `error_text='VOYAGE_API_KEY not set in environment'`, 0/5870 entities
embedded; the concurrently-running sync (`0 inserted / 0 superseded / 5631 unchanged`) re-enqueues
none of them.

**Decision.** Add a per-org **requeue action** that resets an env's connected-org `failed_permanent`
enrichment rows back to `pending`, recomputes `ai_enrichment_status`, and lets the worker's
`enrichment_tick` drain them under the D-179 per-env keys. Mirrors the existing `sync-substrate`
seam exactly (route → bridge → backend → panel). No migration; the queue table already exists.

**Layers.**
- **Backend** — `sync/readiness.requeue_failed_enrichment(session, connected_org_id) -> int`: a
  scoped UPDATE over the org's active-entity (`entities.last_synced_from_org_id = org`,
  `valid_to_seq IS NULL`) `failed_permanent` rows → `status='pending'`, `attempts=0`,
  `started_at/completed_at/error_text = NULL`; then `apply_org_status(session, org)` so the org
  flips off `complete`. Returns the count reset. Pure, joins the caller's tx, testable on the
  seeded `conn` harness.
- **Bridge** — `metadata/s1_sync_console.requeue_s1_enrichment(tenant_id, env_id) -> {ok,
  requeued}`: resolve env→connected_org, open a tenant-scoped session, call the backend, commit,
  best-effort wrapper (mirrors `trigger_s1_sync`, never raises hard). Also add a `failed_enrichment`
  count to `_read_status` so the panel renders the button conditionally + shows N.
- **Route** — `views.py POST /environments/<id>/sync-substrate/requeue-enrichment`,
  `@role_required("admin","superadmin")` + the same inner `trigger_metadata_sync` permission gate
  as the sync trigger; flash "Requeued N rows — the worker will re-embed them", redirect back.
- **Panel** — `environments/detail.html`: a `btn_secondary` "Re-run enrichment (N failed)" next to
  "Sync substrate", shown only when `s1_status.failed_enrichment > 0`, with `data-confirm` (bulk
  reset → confirm modal, never native `confirm()`).

**Forks (resolved).**
- **F1 — reset scope: all `failed_permanent` (chosen) vs error_text-matched.** Terminal rows are
  the only candidates; genuinely-bad rows (e.g. detail-row-not-found) just re-fail, bounded by the
  attempts cap — so a blanket per-org reset is safe and simpler than threading an error-substring
  filter through the UI. Error-substring scoping is a noted future refinement.
- **F2 — re-finalize the already-terminal run? No.** `maybe_finalize_run` only acts on the
  `running` run; the historical `partial_success` row stays as the audit record. The
  currently-running sync (or the next one) finalizes `success` once the queue is clean — favorable
  timing now (`7e03fb10` is running). Requeue populates embeddings; it does not rewrite history.
- **F3 — placement: env-detail S1 panel (chosen)** vs a global admin page. Same per-org
  boundary/permission, and the operator is already on the panel watching sync status.
- **F4 — confirm UX:** the existing attribute-driven `data-confirm` modal.

**Tests.** Backend unit on the seeded `conn` harness (seed `failed_permanent` rows → requeue →
assert `pending` / `attempts=0` / count returned / `ai_enrichment_status` recomputed off `complete`);
`_read_status` exposes `failed_enrichment`; route permission-gate smoke where runnable. Adversarial
review of the impl before the HOLD.

**Live verification (closes 1d / #119).** After merge+deploy, click "Re-run enrichment" on the env
panel → worker drains under the D-179 keys → entities get embeddings → the running sync finalizes
`success` → a real "running → success" *through the product*, which is the S1-sync proving exit-gate.

**Boundary.** One `readiness` fn, one bridge fn + a `_read_status` field, one route, one panel
button. No migration, no substrate-schema change. Commits to `main`.

---

## D-181 — Worker registers the full SQLAlchemy model set (FK-resolution fix, exposed by D-179)

**Status:** Built + shipped to `main`. One-cause mechanical fix; no migration. Surfaced live during
the D-180 enrichment drain.

**Symptom (live, tenant_1 worker logs).**
`llm usage log write failed task=entity_summary_validation_rule tenant=1: Foreign key associated
with column 'llm_usage_log.requirement_id' could not find table 'requirements' with which to
generate a foreign key to target column 'id'`.

**Root cause.** `app.py` (its model-registration block) imports the FULL model set so SQLAlchemy
can resolve cross-module **string** ForeignKeys (`LLMUsageLog` FKs to `requirements`,
`pipeline_runs`, `test_cases`, `generation_batches`, `users`, `tenants` — defined across
`test_management/`, `execution/`, `core/` models). The **worker** is a separate process
(`python -m primeqa.worker`) that never imports `app.py`; it loaded models only lazily/partially
inside functions. When `usage.record` flushes an `LLMUsageLog` row, SQLAlchemy resolves those FK
targets against `Base.metadata`; whichever defining module wasn't imported yet is absent →
`NoReferencedTableError`. **D-179 is what exposed it**: before D-179 the summary subtick
early-returned on the missing key, so the worker never called the LLM gateway / `usage.record`;
once keys resolve from the connection, the worker hits that write for the first time.

**Impact: non-fatal.** `usage.record` opens its own session and swallows the exception
(fire-and-forget). Summaries + embeddings still succeed (the live drain was unaffected); the only
loss was the `llm_usage_log` rows for S1 enrichment — so the superadmin `/settings/llm-usage`
per-task cost breakdown showed nothing for enrichment, plus repeated log-warning noise.

**Decision (Option A).** Register the full model set at worker import time — mirror `app.py`'s
`import primeqa.{core,metadata,test_management,execution,intelligence,vector,release}.models`
block (plus `core.permissions`, `intelligence.generation_jobs`, `execution.data_engine`,
`runs.schedule`) at `worker.py` module scope. Smallest correct + **systemic** (fixes every worker
model-flush path, not just usage logging), zero behaviour change, same pattern the web already
uses; imports are cheap + idempotent. Rejected: B (extract a shared `import_all_models()` helper —
DRYer but touches `app.py`, wider than the bug); C (localized import inside `usage.record` —
whack-a-mole, leaves other worker cross-module FK flushes broken).

**Proof (clean subprocesses).** Resolving `LLMUsageLog`'s FKs without the registration raises
`NoReferencedTableError ('could not find table users')`; after `import primeqa.worker` it resolves
cleanly to all six targets incl. `requirements`.

**Tests.** `tests/unit/test_worker_model_registration.py` — 3 guards: source-level (pollution-proof:
the model imports must exist at worker module scope), functional (LLMUsageLog FKs resolve after
worker import), metadata-presence. Full unit suite 2274 green.

**Boundary.** `worker.py` module-scope imports + one test file. No migration, no behaviour change.
Keep the worker's import list in sync with `app.py`'s registration block. Commits to `main`.

---

## D-180 (close) — Re-enrich requeue action proven live

**Status:** Built + shipped (`7f0c6da` design, `d5f17e2` impl) + **proven live on tenant_1**. No
migration.

**Realized state.** The env-detail S1 panel now carries a "Re-run enrichment (N failed)" button
(shown only when `failed_enrichment > 0`) → `POST /environments/<id>/sync-substrate/requeue-enrichment`
→ `requeue_s1_enrichment` bridge → `readiness.requeue_failed_enrichment` resets the org's
active-entity `failed_permanent` rows to `pending` (attempts=0, cleared timing+error) and recomputes
`ai_enrichment_status` off `complete`. The worker's `enrichment_tick` drains them under the D-179
per-env keys. Permission-gated identically to the sync trigger (`role_required` admin/superadmin +
inner `trigger_metadata_sync`).

**Live proof (tenant_1, env 59).** One button click requeued **5870 embedding + 63 summary** rows
that the keyless worker had stranded `failed_permanent` (`error_text='VOYAGE_API_KEY not set'`). The
worker drained them to **5870/5870 embeddings + 63/63 summaries `succeeded`, zero failures**;
`ai_enrichment_status` → `complete`.

**Adversarial review.** 3 lenses (SQL-correctness / security-permission / UI-kit) → verify: 12
findings, 11 refuted, 1 confirmed (low/cosmetic) — the F2 staleness (a finalized run's
`partial_success` badge isn't re-opened after a clean requeue). Confirmed harmless (no stranding, no
spurious poll, the button correctly disappears at `failed_enrichment=0`); documented in
`requeue_failed_enrichment`'s docstring rather than changed (re-opening a terminal run would break
the documented `last_sync_run_id` contract).

**Deferred / carried.** F1 error-substring scoping (blanket per-org reset shipped; bounded retries
make it safe). A UX refinement to surface `ai_enrichment_status` beside the run badge (would erase
the F2 cosmetic gap). Tests: 5 readiness unit + 6 seeded round-trip; full unit 2274 + semantic
integration 75 green.

**Boundary held.** One `readiness` fn (+ `count_failed_enrichment`), one bridge fn + a `_read_status`
field, one route, one panel button. No migration. Commits to `main`.

---

## D-182 — S1-sync live-SF prod-proving achieved (1d / #119 / Cutover Step 0 exit-gate close)

**Status:** **CLOSED.** The S1-sync exit-gate (task #119, Cutover Step 0; UI Area 1 slice 1d, #138)
is met live on Railway prod — the substrate org model syncs, is readable, fully enriches, and a sync
run finalizes `success`, all driven through the product UI.

**The arc (one outage → four durable fixes → live green).** The live #119 sync stalled ~16h
(screenshot: stuck at phase Object). Diagnosis + fixes, in order:
- **D-178** — scheduler/sync resilience: per-iteration crash isolation + per-tick guards + a periodic
  sync heartbeat + tightened reaper window (45→10 min) + the env-panel orphaned-state. The actual
  root cause was a missing `from sqlalchemy import text` in `scheduler.py` (`b39960a`) — every
  substrate tick's own try/except swallowed the `NameError` → 16h silent no-op. The resilience work
  kept the scheduler alive to log it.
- **D-179** — enrichment provider keys resolve per-env from the LLM connection (Anthropic via
  `config.api_key`, Voyage via a new `config.voyage_api_key`), replacing bare worker env vars. No
  migration.
- **D-180** — the re-enrich requeue action: drain rows stranded `failed_permanent` by the keyless
  worker, through the product (not psql).
- **D-181** — register the full model set at worker module scope so `LLMUsageLog`'s cross-module FKs
  resolve (exposed when D-179 made the worker call the LLM gateway).

**Live exit-gate evidence (tenant_1).** 5870 active entities / 20264 edges synced + readable via
`SemanticOrgModel` (current version); enrichment fully drained (5870/5870 embeddings, 63/63
summaries succeeded); `ai_enrichment_status='complete'`; sync run `b1424380` finalized `status='success'`.
End-to-end path was all product UI: **Sync substrate** → **Re-run enrichment** (after the Voyage key
was set on the connection) → **Sync substrate** → `success`.

**Carried (non-blocking).**
- **D-181 usage logging is forward-looking.** This drain completed under the pre-D-181 worker, so its
  `llm_usage_log` enrichment rows were dropped (the FK error, now fixed). Future enrichment runs log
  usage → the superadmin `/settings/llm-usage` per-task breakdown populates going forward. Not
  back-filled.
- The D-179 `embed_batch` `VOYAGE_API_KEY` env-var fallback stays one release (warned), then removed.
- D-180 F2 badge cosmetic (above).
- A second connected_org (`b113a242`, `ai_status='none'`, no run) exists on tenant_1 — a separate
  env's unprovisioned sync target, out of scope for this proving.

**Canonical status update.** Cutover Step 0 exit-gate: **PASSED**. UI Area 1 (Org Model & Sync / S1):
the live "click Sync, watch it land" payoff is realized. Commits to `main`.

---

## D-183 (design) — GAP-2: Preflight reads S1 freshness/health (cutover Step-5 prerequisite)

**Status:** Design. The hard prerequisite gating cutover Step 5 (the irreversible `meta_*`
drop). No migration — the `cutover_read_s1` flag already exists (migration 051, on prod).

**Problem.** Cutover Steps 0–3 are live; generation + validator + linter read S1 behind the
per-tenant `cutover_read_s1` flag via `MetadataAccessor` (D-158/D-159). But **Preflight still
reads `meta_*`** — `MetaVersion.completed_at` (org-wide staleness) + `MetaSyncStatus` (per-
category health) + the per-test skip-by-stale-category. The accessor's `get_version` explicitly
left this open: *"Version/freshness stays on meta_* (no clean S1 map — GAP-2, D-158)."* The
`meta_*` drop cannot proceed while Preflight reads `meta_*`. This is GAP-2, ratified as the
Step-5 entry-gate in D-162.

**Decision.** Switch Preflight's freshness + per-category-health onto the S1 substrate behind
the same `cutover_read_s1` flag, with a `meta_*` fallback during the parallel window.

**Mapping `meta_*` → S1.**
- **Org-wide freshness:** `MetaVersion.completed_at` → the **latest `sync_runs` with
  `status IN ('success','partial_success') AND completed_at IS NOT NULL`**, `.completed_at`.
  NOT `connected_orgs.last_sync_completed_at` (observed NULL in prod even post-success). Same
  thresholds (`METADATA_STALE_HOURS=168` warn / `METADATA_BLOCK_HOURS=720` block) — they're
  product policy, not storage shape.
- **Per-category health:** S1 has **no per-category partial state** (it syncs all entity types
  into one versioned run). So `healthy_categories` collapses to **all-six when the org model is
  usable, else empty** — and the per-test skip-by-stale-category becomes dormant in S1 mode
  (correct: an S1 version is atomic). `_per_test_checks` / `_categories_for_refs` keep their
  shape; they're just fed the S1-derived set.
- **"Usable" / provisioned:** `connected_orgs` row exists AND
  `SemanticOrgModel(conn).current_version_seq()` is not None. Not usable / never synced →
  blocker (stable code `NO_METADATA`, S1-worded message).

**Layers.**
- **`metadata/s1_sync_console.read_s1_freshness(tenant_id, environment_id)`** — new best-effort
  helper (own tenant conn, never raises; mirrors `read_s1_sync_status`). Returns
  `{available, provisioned, last_success_at, age_hours, current_version_seq, usable}`.
- **`runs/preflight.py`** — gate the freshness + healthy-categories on
  `cutover_read_s1_enabled(self.db, tenant_id)` (reuse the accessor's flag helper). Flag-on +
  S1 provisioned → S1 path; else (flag-off, or flag-on but S1 unprovisioned during parallel) →
  the existing `meta_*` path, unchanged. Populate the existing `meta_version` summary keys from
  S1 (so `preview.html` needs no change) + a `metadata_source: 's1'|'meta'` marker. Issue
  **codes stay stable**; only message text varies by source.
- **`metadata/accessor.py`** — update the `get_version` GAP-2 comment to point at the new
  preflight helper (doc-only).

**Forks (resolved).** F1 per-category → all-or-nothing in S1 (no partial state). F2 freshness →
latest successful `sync_runs.completed_at` (the `connected_orgs` column is unreliable). F3
fallback → `meta_*` when flag-on-but-S1-unprovisioned (parallel-window safety; retires at
Step 5). F4 codes → stable, vary message (keeps the JWT-gated integration tests green). F5
location → `s1_sync_console` (env-keyed status), not the `meta_version_id`-keyed accessor.

**Out of scope / companion follow-up.** `MetadataService.check_drift` (the live-SF Tooling
drift comparison in `views.runs_new_preview`) is a **separate** `meta_*` reader on the same
preview path; it also blocks the `meta_*` drop and needs its own S1 re-point/disable before
Step 5 — a distinct slice, not this one.

**Tests.** Seeded-conn unit for `read_s1_freshness` (extend `tests/integration/semantic/
test_s1_sync_console.py`): provisioned + success → usable + age; running-only + a version →
usable + age None; not provisioned → unusable; partial_success-with-completed_at counts.
Preflight unit (`tests/unit/`) mocking the flag + helper: flag-on fresh / very-stale / not-
usable / not-provisioned-fallback / flag-off-unchanged. Adversarial review of the impl before
the impl HOLD. (The JWT-gated Flask preflight integration tests don't run locally.)

**Boundary.** One bridge helper, one flag-gated branch in preflight, one doc comment. No
migration, no `meta_*` removal (that's Step 5). Commits to `main`.

---

## D-183.1 (impl correction) — `_read_freshness` env-scopes the version (adversarial-review fix)

**Status:** Impl-time correction to D-183, caught by the adversarial review before the impl
commit. Folded into the impl.

**Defect (HIGH, confirmed).** D-183's design specified the version from
`SemanticOrgModel(conn).current_version_seq()` = `MAX(version_seq) FROM logical_versions` — a
**tenant-schema-global** aggregate (`logical_versions` has no org/environment column; it's the
tenant-wide version anchor shared by every org). In a multi-env tenant where env A is synced but
env B was provisioned (`connected_orgs` row exists) and never synced its own org, `_read_freshness`
for env B returned `usable=True` (it inherited env A's global MAX version) → the `NO_METADATA`
blocker silently collapsed → env B passed the gate that immediately precedes the irreversible
`meta_*` drop, and downstream generation/validation/execution would read a **sibling env's** org
model. The v1 `meta_*` path this replaces is strictly per-env (`Environment.current_meta_version_id`
is a per-row column), so a never-synced env B would correctly `NO_METADATA`-block — a genuine
cutover-safety divergence. The single-env tests didn't exercise it.

**Fix.** Env-scope the version: `sync_runs.logical_version_seq` is allocated per run and is already
scoped by `source_org_id`, and `_read_freshness` already selects this env's latest successful
`sync_run`. The query now also returns `logical_version_seq` from that same row;
`current_version_seq` / `usable` derive from it (drops the `SemanticOrgModel` call). `usable` now
strictly means "**this env's own** org model has a successful sync version" — a never-synced env
(or a first-sync-still-running env) reads `usable=False` even when sibling envs have versions.
Consequence: the prior "usable but `age_hours=None` (first sync running)" state no longer exists —
a usable env always has a measured age; an in-progress first sync is `usable=False` and
`NO_METADATA`-blocks until it succeeds (the conservative, v1-matching posture).

**Regression test.** `test_freshness_env_scoped_not_contaminated_by_sibling` (two `connected_orgs`
in one schema: A synced with a version, B provisioned + running-only) asserts
`_read_freshness(B).usable is False` / `current_version_seq is None`. Full unit 2282 + semantic
integration green.

---

## D-184 (design) — `check_drift` reads the S1 sync anchor (cutover Step-5 companion to D-183)

**Status:** Design. The companion to D-183: the run-preview path (`/runs/new/preview`) had two
`meta_*` readers — Preflight (D-183) and the **metadata-drift banner**
(`MetadataService.check_drift`). This re-points the drift check onto S1 behind the same
`cutover_read_s1` flag, leaving the preview path `meta_*`-free behind the flag (so Step 5 — the
`meta_*` drop — isn't blocked by it). No migration.

**Key observation.** `check_drift` is **mostly live Salesforce**, not `meta_*`. Its ONLY `meta_*`
dependency is the "drift since" anchor: `metadata_repo.get_current_version(env)` →
`current.completed_at` (the timestamp the live-SF probes compare against) + `current.id` /
`current.version_label` (banner text). The four count-only Tooling probes
(`FieldDefinition` / `ValidationRule` / `Flow` / `ApexTrigger` `WHERE LastModifiedDate > since
LIMIT 200`) and `drift_detected = any(count > 0)` are source-agnostic and stay. Single caller:
`views.runs_new_preview` (~1689); the preview template reads
`drift.{drift_detected, counts, current_meta_version_label, synced_at, has_current_meta, error}`,
all anchor-derived → **no template change**.

**Decision.** Extract the anchor resolution into a testable helper and flag-branch it; the live-SF
drift comparison is untouched.
- `MetadataService._resolve_drift_anchor(environment_id, tenant_id) -> {version_id, version_label,
  synced_dt, source} | None`:
  - flag ON + S1 provisioned (`cutover_read_s1_enabled` + `read_s1_freshness`, D-183): `usable` →
    `{current_version_seq, "Org model v{seq}", fromisoformat(last_success_at), "s1"}`; not usable
    (never synced THIS env) → `None`. Reuses D-183.1's env-scoped `read_s1_freshness`, so no
    multi-env sibling-version contamination.
  - else (flag off / S1 unprovisioned / S1 unavailable → parallel-window fallback): the v1
    `get_current_version` row, or `None` when there's no current meta.
- `check_drift` calls the helper; `None` → the existing `has_current_meta=False` shape; otherwise
  the anchor's fields replace `current.id` / `version_label` / `completed_at` at all four return
  points + `since_iso`. Adds a `metadata_source` marker (parity with D-183).

**Forks (resolved, mirroring D-183).** meta_* fallback during parallel window; never-synced-this-
env → `has_current_meta=False`; banner label `"Org model v{seq}"`.

**Tests.** Unit on `_resolve_drift_anchor` (flag-off → meta; flag-on usable → s1 with the right
label/synced_dt + `get_current_version` not called; not-usable → None; not-provisioned → meta
fallback; fallback-with-no-current → None) + a `check_drift` `has_current_meta=False`-on-None test
(no SF mocking). Self-review for the D-183-class env-scoping bug (reuses the fixed reader, so
guarded). Full unit suite green. (JWT-gated Flask preview integration tests don't run locally.)

**Boundary.** One helper + a flag-gated swap in `check_drift`, one accessor comment. No migration,
no template change, no `meta_*` removal (Step 5). Commits to `main`. After this the preview path is
`meta_*`-free behind the flag; remaining cutover work is Step 4 (parity) → Step 5.

---

## D-185 (design) — Open cutover Step 4 (parallel-run validation): decompose + lead with parity

**Status:** Design — opens cutover Step 4. Entry-gate (Step 3 flagged reads live) is satisfied
(D-158–D-162 + D-183/D-184). Step 4's exit-gate is "a clean parity window — no divergence — across
the rollout tenants; the GO/NO-GO + ledger seams landed" (`SEQUENCE.md:57`).

**Decomposition.** Step 4's three `SEQUENCE.md` work-items are independent and at very different
readiness; this opens them as three slices and fixes the order.

- **4a — Metadata read-parity harness (lead; buildable now).** Prove S1-sourced reads equal
  `meta_*`-sourced reads. `MetadataS1Reader` (`primeqa/metadata/s1_reader.py`) and
  `MetadataRepository` expose matching reads. Build `MetadataParityChecker(meta_repo, s1_reader)`
  diffing, for an env's current `meta_version` ↔ S1 version:
  - objects by `api_name` → `{is_createable, is_custom}`;
  - fields by `(object, field)` → `{field_type, is_required, is_custom, is_createable,
    is_updateable, reference_to, picklist_values}` (the true parity axis);
  - VRs by `(object, rule_name)` → presence + `error_message` (S1 doesn't sync
    `error_condition_formula`/`is_active` — a documented gap, NOT a divergence).
  **Design point:** separate **shape divergence** (a field BOTH have, but a CRUD flag differs → a
  reader bug; the exit-gate) from **membership divergence** (a field in one but not the other →
  sync-time drift between the independent meta/S1 syncs; expected — exactly what `check_drift`
  surfaces). The exit-gate keys on the *intersection-shape* parity. Plus a `scripts/parity_check.py`
  runner over the rollout tenants. Reuses `build_metadata_s1_reader`, `cutover_read_s1_enabled`, the
  `diff_fields` keyed-diff pattern; unit-testable with synthetic readers.
- **4c — Retire the S3 ledger → S2 provenance (self-contained, no v1 schema risk).** `test_provenance`
  is **already fully written** by every S2 coordinator mutation; the gap is that
  `get_provenance`/`get_recipe_provenance` are **reserved but unimplemented** (SPEC §10.2). Work =
  build them on the S2 coordinator + rewire S3's eval/governance/routing readers off
  `generation_outcomes` + verify equivalence + deprecate the ledger. `llm_calls` stays in S3 (D-074).
- **4b — Fold S6 verdicts into GO/NO-GO (heaviest; carries a fork).** `release_runs` links only to
  v1 `pipeline_runs` — there is **no `s4_execution_run_id`** column; the v1 + substrate execution
  worlds are disjoint. *But* D-172/D-173 already shipped an additive **requirement-grain**
  substrate-evidence panel (`release_substrate_console.get_release_substrate`) that surfaces
  grounding + S6 verdicts and deliberately does NOT flip the verdict.
  **Fork 4b (settle in its own pass):** add a `release_runs.s4_execution_run_id` migration + wire it
  at execution-finalize + define a fold policy → a true release-grain fold into `DecisionEngine`;
  **or** accept the existing requirement-grain evidence panel as "the additive fold" (human weighs
  it; v1 stays the GO/NO-GO authority).

**Sequence + lean: 4a → 4c → 4b.** 4a is the literal exit-gate mechanism and depends on nothing; 4c
is self-contained; 4b is heaviest, carries the architectural fork, and the evidence panel already
exists, so the GO/NO-GO fold is least-urgent. **Lead with 4a.**

**Boundary (Step 4 overall).** Additive only — no v1 removal (that's Step 5). 4a adds a read-only
harness + a script; 4c adds a read API + rewires S3-internal readers; 4b (if the fork goes that way)
adds one nullable column + an additive decision input. Commits to `main`.

---

## D-186 (settlement) — Step 4c: the S3 generation ledger stays write-only; `get_provenance` deferred

**Status:** Settled, docs-only (the D-156 pattern — a premise-break resolution, not a build). Closes
Step 4 slice 4c. No code change, no migration.

**Why this is a settlement, not a build.** Slice 4c was framed (`SEQUENCE.md:56`, carrying D-074) as
"retire the S3 semantic ledger into S2 provenance once `get_provenance` ships" — implying: build
`get_provenance`, then rewire the ledger's readers. **Verified ground truth says there is nothing to
rewire and no consumer to build for:**
- The S3 ledger (`generation_requests` / `generation_outcomes`) is **WRITE-ONLY**. Every `*Row` DB
  reference in `primeqa/` is either the model definition (`generation/models_db.py`) or a
  `persistence.py` `INSERT`. The `GenerationOutcome` / `GenerationRequest` names used throughout
  `generation/` (governance, runtime, intake, routing, eval) are the in-memory **Pydantic DTOs**
  (`generation/protocol.py`), NOT DB reads. There is **no `SELECT FROM generation_outcomes`** anywhere.
- `get_provenance` / `get_recipe_provenance` are **unimplemented** (reserved in S2 SPEC §10.2) with
  **zero consumers** — only doc/comment references. Building them now is speculative (YAGNI).
- The ledger is **S3-internal, not `meta_*`** — it does not block the cutover `meta_*` drop (Step 5).

**Decision.**
- The generation ledger **stays** as a write-only audit / replay artifact — parallel to `llm_calls`,
  which the SEQUENCE already keeps in S3 (D-074). It is not redundant with `test_provenance`:
  `generation_outcomes` records the **generation event** (refusal_kind, explanation_hash, the
  request→outcome lineage, deltas), whereas `test_provenance` records the **S2 claim/recipe lifecycle**
  (created / edited / regenerated / approved). Different surfaces; no merge.
- `test_provenance` is the canonical S2-lifecycle record (already fully written by the Coordinator).
- `get_provenance` / `get_recipe_provenance` are **deferred to their first real consumer** (an S6 audit
  or S8 drift surface). When one lands, the typed read API is built then — over `test_provenance`, with
  the row-level fields (`refusal_kind`, `explanation_hash`, …) D-074 pre-exposed for exactly that.

**Consequence for the cutover.** 4c is **off the critical path** — neither a Step-5 blocker nor
cutover-coupled. Step 4's remaining real work is **4b** (fold S6 verdicts into GO/NO-GO + its
run-key fork). 4a (the parity harness) is shipped; its live cross-tenant run is a separate ops step.

**Carried (4a live verification).** The 4a harness + unit tests shipped, but the first live cross-tenant
parity run was killed mid-hydration: `build_metadata_s1_reader` reads the whole org model
(per-entity `get_entity_details` over thousands of entities) synchronously, which is too slow for a
foreground run. The live "clean parity window" needs either a background-job runner or a lighter
reader hydration — tracked, not blocking this settlement.

---

## D-187 (settlement) — Step 4b: the requirement-grain evidence panel IS the additive S6 fold

**Status:** Settled, docs-only (the D-156 / D-186 pattern). Closes Step 4 slice 4b. No code, no
migration.

**The fork.** `SEQUENCE.md:56` (carrying D-111) lists "fold S6 verdicts into the GO/NO-GO decision
(additive)". This is blocked from a *release-grain hard gate* because `release_runs` links only to v1
`pipeline_runs` — there is no `s4_execution_run_id`; the v1 and substrate execution worlds are
disjoint (no shared key, no write-path). Two ways: (A) add the run-key + a fold policy + a
`DecisionEngine` input → a release-grain hard gate; or (B) accept the already-shipped requirement-grain
substrate-evidence panel as the additive fold.

**Decision — Option B.** D-172/D-173 (UI Area 5) already shipped
`intelligence/release_substrate_console.get_release_substrate`, which reaches a release through its
**requirements**, reads per-claim **S6 verdicts + S8 grounding validity**, rolls up at-risk counts, and
renders them on the release surface — **deliberately advisory** ("does NOT produce or flip the verdict;
the human weighs it alongside the v1 `DecisionEngine`", D-172). That IS the SEQUENCE's "additive" fold:
the substrate evidence is folded into the human's GO/NO-GO judgment **without** the substrate gating a
v1 decision. The v1 `DecisionEngine` (`release/decision_engine.py`) remains the GO/NO-GO authority.

**Deferred (Option A — a release-grain hard gate).** Adding `release_runs.s4_execution_run_id` + wiring
it at S4 execution-finalize + a verdict fold policy in `DecisionEngine` is deferred because: (1) it's a
v1-schema change on the `release`/`pipeline_runs` path that Step 5 / the post-cutover release rework
may itself replace — risking a throwaway key; (2) the evidence panel already delivers the additive
signal a reviewer needs; (3) a *hard* substrate gate over a human-confirmed release decision is a policy
choice the product has not asked for. Revisit if/when releases are re-pointed to substrate runs.

---

## D-188 (close) — Cutover Step 4 (parallel-run validation): seams landed; live parity-window carried

**Status:** Step 4 build + settlements complete. The three work-items are resolved; **one exit-gate
criterion — a clean LIVE parity window — is a carried ops verification** (honest: it is not yet
demonstrated live), which becomes a Step-5 entry-gate.

**Exit-gate review (`SEQUENCE.md:57` — "a clean parity window; the GO/NO-GO + ledger seams landed").**
- **Parity window (4a):** the `MetadataParityChecker` + `scripts/parity_check.py` + unit tests shipped
  (D-185, `baa417a`). The live cross-tenant *clean window* is **NOT yet captured** — the first run was
  killed mid-hydration (synchronous full-org-model read; D-186 carry). Needs a background-job runner or
  lighter hydration. **This is the one open exit-gate criterion.**
- **GO/NO-GO seam (4b):** settled (D-187) — the requirement-grain substrate-evidence panel (D-172/173)
  is the additive fold; v1 `DecisionEngine` stays the authority.
- **Ledger seam (4c):** settled (D-186) — the S3 ledger stays write-only; `get_provenance` deferred.

**What's actually landed vs carried.** Landed: the parity *instrument*; both seam settlements; the
preflight + drift re-points (D-183/D-184) that the parity validates. Carried: the live clean-parity-
window evidence (an ops run, gated on the runner fix), and — separately — the **Step-5 code prep** from
the earlier `meta_*`-reader audit (relocate `MetadataS1Reader` out of `primeqa/metadata/`; the worker
Name-check disposition; discard the v1 sync UI/routes).

**Cutover position.** Steps 0–3 live; Step 4 seams landed. **Step 5 (the irreversible `meta_*` drop)
entry-gate** = (a) a clean live parity window [4a ops run] **and** (b) `cutover_read_s1` ON for the
rollout tenants **and** (c) the Step-5 reader-retirement prep. No `meta_*` removed yet. Commits to
`main`.

---

## D-189 (design) — bulk read primitives: the S1 metadata-reader O(entities) N+1 fix

**Status:** Design (impl pending). Cutover follow-on enabling the Step-4a live parity window + the live
read-switch. Touches S1 (`primeqa/semantic/query.py`, additive) + the cutover reader
(`primeqa/metadata/s1_reader.py`). Commits to **`main`** (cutover rhythm + v2-runtime deploy-sync — this
fixes a production read-path Railway deploys; user-confirmed this session).

**The problem.** `hydrate_metadata_s1_reader` (`s1_reader.py:118`) builds the whole-org reader by calling
`get_entity_details` **per object and per field**, `get_related` **per object** (fields) and **per VR**
(object), and `get_picklist_values` **per picklist field**. For a real org (~2k objects / ~40k fields)
that is **tens of thousands of sequential DB round-trips**. It (a) killed the Step-4a parity runner
(`scripts/parity_check.py`) mid-hydration — the one open Step-4 exit-gate criterion (D-186/D-188 carry) —
and (b) is a **latent blocker for the live read-switch**: the same `build_metadata_s1_reader` is on the
generation+validation read-path (`test_management/service.py:128`) whenever `cutover_read_s1` is on, so
any real tenant flipping the flag eats the N+1 on every generation.

**The fork (resolved).** (A) **bulk read primitives** — collapse hydration to ~6 queries [chosen];
(B) background-job runner — unblocks the ops script only, leaves the read-switch N+1 (a workaround,
rejected per root-cause discipline); (C) lazy reader — rejected (the validator reads the whole-org field
index, `validator.py:133`). The N+1 is intrinsic to per-entity hydration; the in-bounds fix is the bulk
*form* of S1's typed reads.

**Decision — three additive primitives on `SemanticOrgModel`** (`query.py`), each the bulk form of an
already-shipped read, same contract every prior primitive followed (validated `at_seq` via
`_validate_version`; the `_as_of` window join to `entities`; table names only via the trusted
`_detail_table_for`/`TIER_1_ENTITIES` registry; no caller SQL; tenant-scoped connection):
- `get_entity_details_bulk(entity_type, at_seq) -> {UUID: dict}` — `SELECT d.* FROM <detail_table> d
  JOIN entities e ON e.id = d.entity_id WHERE e.entity_type = :entity_type AND <_as_of('e')>`. **INNER**
  JOIN — detail tables carry no version columns (D-025), so pinning is the FK + `_as_of('e')`; an entity
  with no detail row is simply absent, and the reader's `.get(id, {})` reproduces today's `od or {}`.
- `get_related_bulk(edge_types, direction, at_seq) -> {UUID: list[RelatedEntity]}` — `_related_select`
  with the near-id predicate **dropped** and `e.<near> AS near_id` **added**, grouped by `near_id`, each
  `RelatedEntity` built by `_row_to_related` verbatim. Keeps **both** `_as_of('e')` (edge) and
  `_as_of('t')` (far entity, in the JOIN ON) — dropping `_as_of('t')` would leak a superseded far entity.
  `direction ∈ {inbound, outbound}` only (one grouping key needs one direction).
- `get_picklist_values_bulk(at_seq) -> {UUID: list[dict]}` — `get_picklist_values` with the grouping
  key `picklist_value_set_entity_id`, grouped + `ORDER BY …, sort_order`.

**The reader rewrite is a byte-for-byte drop-in.** `hydrate_metadata_s1_reader` issues the ~6 bulk reads
up front, then runs the **existing** object/field/VR construction loops unchanged in logic — each
per-entity call swapped for a dict lookup. `build_metadata_s1_reader` (best-effort, catches all → None)
is untouched. Every invariant is preserved and cited: object/field/VR sorts (`:123,:173,:188`); the
field-name **parent-prefix strip** (`:145-147`, kept correct by leaving the field loop **nested** inside
the object loop so `obj_api == e.sf_api_name`); `is_required` from `entities.attributes` not a detail
column (`:165`); CRUD defaults True (`:169-170`); picklist `()`/`list[str]` shape (`:154-161`, the type
`validator.py:89` requires) plus a **UUID-key coercion** before the picklist-set lookup (the bulk map
keys by real `UUID`; `fd.get("picklist_value_set_entity_id")` may be str). The correctness gate is a
**golden-equivalence test**: a vendored copy of the pre-D-189 hydrate (`_legacy_hydrate`, using the
retained per-entity primitives) must produce identical `get_objects`/`get_fields`(whole-org + per-object)
/`get_validation_rules` output — including `picklist_values` value **and `type`** — on a richer seeded
org (objects with/without details, with/without fields, picklist with 2 values + a set with 0
values-at-seq, VR with/without APPLIES_TO, a second version superseding a field).

**Framing vs the lock.** SPEC §12 lists "Bulk operations" as deferred. These are the bulk *form* of
already-shipped reads (same contract, one round-trip), **additive and uncovered** by D-022's five
primitives — landing under D-024's "matters not covered" carve-out, the exact path `get_entity_details`
(D-111.1) and `get_picklist_values` (D-119) took. SPEC §12's bullet is **annotated** (read-form
realized), not contradicted; a general bulk-query DSL and any write/mutation bulk stay deferred.

**Scope (smallest correct change).** Metadata reader only — S6 `S1VrReader` / S8 `S8S1Reader` read
per-object (bounded N+1) and are **not** converted. The four per-entity primitives stay (S6/S8 consumers
+ the `_legacy_hydrate` reference) — the three bulk forms are added alongside, no removal. No row cap
(returning the whole org is the point). The `field_details.object_entity_id` BELONGS_TO shortcut (one
fewer query for fields) is **deferred** — the edge stays the parity source-of-truth to avoid
edge-vs-column divergence.

**Verification.** Local: the golden-equivalence + per-primitive tests against the semantic integration
harness (`primeqa_test_semantic`) — a green local-PG run is the merge gate. Live: re-run
`scripts/parity_check.py` against prod (read-only; explicit approval first) — it must complete at scale
(not be killed) and report `PARITY CLEAN … divergent=0`, which **is** the Step-4a exit-gate evidence
carried since D-186/D-188.

---

## D-190 (design) — `is_required` is a non-parity axis; the parity runner respects `is_active` (closes Step-4a)

**Status:** Design (impl pending). Cutover Step-4 follow-on; closes Step-4a (the live clean parity
window — the criterion carried since D-186/D-188). Touches the parity instrument (`metadata/parity.py`,
`scripts/parity_check.py`) only. On `main`.

**What the D-189 live run revealed.** With the runnable parity instrument (D-189) and after retiring the
previous-version test environments (see *Data action* below), the **only** shape-axis divergence on the
one real env (env 59, "Prime QA NEW") is `is_required` — 41 fields, all `{meta: True, s1: False}`. Every
other shape axis is at **true parity**: objects (`is_createable`/`is_custom`) shape=0, fields
(`field_type`/`is_custom`/**`is_createable`/`is_updateable`**) shape=0. The CRUD-flag mismatches in the
first all-envs run (`Event.IsRecurrence2Exception`, `Contact.Name`) were all on now-retired **stale**
envs, not the live one.

**Root cause — a by-design definitional difference, not a reader bug.** `meta_*` computes
`is_required = (not nillable) AND (not defaultedOnCreate)` (`metadata/service.py:437`) — a *schema*
definition that over-marks system/permission fields (e.g. `ApexTrigger.UsageAfterDelete`,
`PermissionSetLicense.Maximum*` flags as "required"). S1's `is_required` is a different concept (the
UI/layout create-time enforcement flag; `semantic/entity_attributes.py:95-97`). The S1 reader faithfully
returns S1's stored value — there is **no reader bug** to fix; the two systems simply define the field
differently.

**Decision.** Exclude `is_required` from parity's **shape axis** (`_FIELD_SHAPE` in `metadata/parity.py`)
— exactly the treatment `reference_to` (S1 defers it) and `picklist_values` (stored differently) already
receive. The parity gate exists to catch **reader bugs** (a value both sources have, read wrong by S1);
a definitional difference is out of scope for that. Justification is airtight, not a judgment call:
1. **By-design difference**, documented in S1's own `entity_attributes.py:95-97` — not a reader bug.
2. **Drives no validator-blocking rule.** `intelligence/validator.py` does not read `is_required` at all;
   its sole consumer is `intelligence/generation.py:323`, which uses it for "[required: …]" *prompt
   hints*. So the divergence carries **zero cutover correctness risk** for the validator.
3. `meta_*`'s definition is demonstrably noisy; S1's is at worst a sparser signal.

This is **not** masking a symptom (the standing prohibition): there is no reader bug behind it, and the
already-excluded `reference_to`/`picklist_values` set the precedent.

**Companion fix — the runner respects `is_active`.** `scripts/parity_check.py` filtered only on
`current_meta_version_id IS NOT NULL`, so it validated **deactivated** envs (retired test data with stale
`meta_*` versions) and reported them as divergent. The runner now also filters `Environment.is_active`
— a deactivated env's stale `meta_*` must not gate the parity window. (The product soft-delete path,
`views.py` `environments_delete`, only flips `is_active`.)

**Data action (recorded, reversible).** The pre-D-189-era test environments were retired: 5 active
tenant-1 envs (`Production`, `Acme Production`, `Meta Test Sandbox`, `Intel Test 8d39c8`, `Acme UAT
Sandbox`) were soft-deleted (`is_active=false`) so only env 59 ("Prime QA NEW") remains active. Soft
delete — reversible, no cascade. The ~20 already-inactive envs are now correctly excluded by the
`is_active` filter.

**Honest downside (logged, not fixed here).** S1's `is_required` is sparser than `meta_*`'s, so the
generation prompt lists fewer "[required: …]" hints under `cutover_read_s1`. Non-blocking quality nuance;
a future enhancement — populate S1's `is_required` from the schema definition — is **deferred**, not done
in this slice.

**Exit-gate.** With both changes, env 59 parity → `shape=0` → **CLEAN**. That is the live clean parity
window carried since D-186/D-188 — **Step-4a's exit-gate is met.** Step 5's entry-gate still also
requires `cutover_read_s1` ON for the rollout tenant(s) + the Step-5 reader-retirement prep.

---

## D-191 (design) — relocate the cutover read-bridge out of `primeqa/metadata/` (Step-5 prep, reversible)

**Status:** Design (impl pending). Cutover Step-5 **prerequisite prep** — NOT the irreversible drop. Pure
code relocation, no behavior change, no schema/prod impact. On `main`.

**Why now — the Step-5 gate is unmet, so the drop is deferred.** "Step 5" (the irreversible act) drops
`meta_*` + the v1 product tables `test_case_versions` / `requirements` / `metadata_impacts` (D-065) and
deletes `primeqa/metadata/`. Its entry-gate (`SEQUENCE.md:64-65`) is materially **unmet**: GAP-2 — preflight
still reads `meta_*` (`MetaSyncStatus` / `MetaVersion`, no clean S1 map); `cutover_read_s1` is OFF and the
v1 metadata sync still actively writes `meta_*`; and `requirements` / `test_case_versions` still have dozens
of live readers (`views.py`, `release/*`, `runs/{wizard,bulk,preflight,my_tickets}`, `execution/routes`,
`risk_engine`, `agent`, `worker.py`). Dropping anything today would break production, and there is no
rollback past the drop. So Step-5 work begins with the **reversible** slices; this is the first.

**The mixed-concern problem.** `primeqa/metadata/` holds two unrelated concerns: the **v1-metadata to
delete** (`models.py`, `repository.py`, `service.py`, `worker_runner.py`, `sync_engine.py`, `routes.py` —
the `meta_*` ORM + repo + the v1 Tooling-API sync) and the **cutover read-bridge that must survive**
(`accessor.py` = the flag-gated read switch; `s1_reader.py` = the S1→meta-shape reader; `parity.py` = the
parity instrument; `s1_sync_console.py` = the v1 UI/ops bridge to the S1 sync + `read_s1_freshness`). The
bridge reads **S1, never `meta_*`** (verified: zero module-level `primeqa.metadata.*` imports in the four,
no cross-imports among them, only a docstring ref in `parity.py:4`).

**Decision.** `git mv` the four bridge modules (filenames unchanged) into a new package
**`primeqa/metadata_bridge/`** (+ empty `__init__.py`) and re-point every importer
`primeqa.metadata.<mod>` → `primeqa.metadata_bridge.<mod>`. Import surface (mapped): 5 source files
(`metadata/service.py`, `runs/preflight.py`, `test_management/service.py`, `views.py`,
`scripts/parity_check.py`) + 6 test files (`tests/unit/test_metadata_accessor.py`,
`tests/unit/test_metadata_parity.py`, and four `tests/integration/semantic/` files). Name rationale: it
survives the eventual `delete primeqa/metadata/` and is self-documenting (mirrors the D-148 precedent that
relocated S5 to `primeqa/knowledge/`). All four move together (one package, one re-point sweep, lowest
risk); a later split of `s1_sync_console` → `primeqa/sync/` is a noted trivial follow-up.

**Outcome.** `primeqa/metadata/` then contains only the v1-to-delete set, so Step 5's `delete
primeqa/metadata/` becomes a clean removal that cannot accidentally take out the S1 read-path. No
behavior changes — proven by the same 108 unit+semantic tests (D-190) passing against the new import
paths, plus `grep` showing zero stale `primeqa.metadata.{accessor,s1_reader,parity,s1_sync_console}`
importers and `primeqa/metadata/` reduced to the v1 set.

**Deferred (the rest of Step 5, gate unmet).** GAP-2 (preflight off `meta_*`); flip `cutover_read_s1` ON +
live-verify; retire the v1 metadata sync writer; retire the `requirements`/`test_case_versions` readers
onto the spine; then — strictly last, with a final explicit go — the migration dropping `meta_*` + the
D-065 product tables + deleting `primeqa/metadata/` + removing the flag.

---

## D-192 (design) — flip `cutover_read_s1` ON (tenant 1) + verify + preflight S1-only (closes GAP-2 + "S1 as production source")

**Status:** Design (impl pending). Two Step-5 entry-gate conditions closed for the rollout tenant
(tenant 1 / env 59): **S1 verified as the production read source** (flag flipped + live-verified) **and
GAP-2** (preflight off `meta_*`). Reversible. On `main`.

**Finding (premise-shift).** GAP-2 was framed as "preflight has a hard `meta_*` dependency with no S1
map." Reading the live code: **D-183 already built it** — preflight reads S1 freshness + health when
`cutover_read_s1` is on (`runs/preflight.py`, `read_s1_freshness`, 8 passing tests). The feared
"`MetaSyncStatus` per-category health has no clean S1 map" is **resolved**: S1 syncs the whole org
**atomically** (FK-ordered phases, one `sync_run`), so per-category partial state cannot exist — D-183
correctly collapses health to **all-or-nothing** (`healthy_categories = all-six if the org model is
usable, else none`). So GAP-2's only *remaining* work is **removing preflight's `meta_*` fallback** (the
`MetaSyncStatus` import at `preflight.py:383`, the `MetaVersion.get_version` freshness reads, and the
`_use_s1_freshness` flag gate) so preflight reads S1 **unconditionally** — the literal entry-gate ("the
`meta_*` drop cannot proceed while preflight still reads `meta_*`").

**Sequencing (why coupled).** Preflight's invariant is *check the same source the run will read.* Runs
read metadata via the flag-gated accessor; the flag is **OFF**, so runs read `meta_*`. Making preflight
S1-only while runs read `meta_*` would let preflight give false "fresh" confidence on a stale-`meta_*`
run. So GAP-2's cut is coupled to flipping the flag **ON** (then preflight + runs are both S1, consistent).
Flipping the flag is itself the entry-gate's *"S1 verified as the production data source"* step. (User
chose "couple it" over a premature cut.)

**Decision — one coherent slice, in order:**
1. **Flip `cutover_read_s1` ON for tenant 1** (a reversible `tenant_agent_settings` flag write). This
   moves tenant 1's *generation / validation* (`MetadataAccessor`) **and** `check_drift` (D-184) onto S1.
   Net improvement: env 59 has a full S1 sync (~4866 fields) vs a stale partial `meta_*` (~700 fields).
2. **Live-verify (read-only, no LLM, no product writes):** with the flag on, build the `MetadataAccessor`
   for tenant 1 and confirm it **routes to S1** (`_use_s1=True`) and returns the full org
   (`get_objects()` / `get_fields()` ≈ the env-59 counts). Proves generation/validation now read S1.
3. **Preflight S1-only (the GAP-2 cut):** remove the `meta_*` fallback branch + `_healthy_meta_categories`
   + the `MetaSyncStatus` import + the `_use_s1_freshness` gate. Preflight always calls
   `read_s1_freshness`; S1 unprovisioned/unusable → the existing `NO_METADATA` blocker (no `meta_*`
   fallback). Preserve every issue code (`NO_METADATA` / `METADATA_STALE` / `METADATA_VERY_STALE`), the
   S1-worded messages, `metadata_source='s1'`, and the all-or-nothing per-test skip. Drop the now-unused
   `meta_repo` freshness path (keep/drop the constructor param per its other uses; re-point callers if
   dropped). Rewrite the 3 fallback tests (D-183 tests 6/7/8) — there is no `meta_*` fallback now: S1
   unavailable/unprovisioned → `NO_METADATA` (degraded), not a meta read.

**Scope boundary (still deferred).** This does NOT retire the v1 metadata **sync writer** (still writes
`meta_*`), nor the *other* flag-gated readers' fallback (generation/validation accessor keeps its
`meta_*` fallback for safety until the writer is retired), nor the `requirements`/`test_case_versions`
reader retirement, nor the drop. It closes **preflight's** `meta_*` read + commits **tenant 1** to S1
reads. The flag stays (removed only at the final drop); preflight just stops consulting it.

**Verification + rollback.** Local: `pytest tests/unit/test_preflight_s1_freshness.py` + the metadata
suites green against the S1-only preflight. Live: step-2 verify above. **Rollback:** flip the flag OFF
(`UPDATE … SET cutover_read_s1=false`) reverts generation/validation/check_drift to `meta_*`; the
preflight code change is revertible by git — preflight would then need S1 (acceptable, env 59 has it).

---

## D-193 (design) — retire the v1 metadata-sync WRITER + its UI/wiring (Step-5 prep)

**Status:** Design (impl pending). Step-5 prep: stop all new `meta_*` writes so the table goes static
before the drop. The S1 sync (D-164, the env-panel "Sync substrate") is the replacement; this removes
the old v1 path. Reversible (git). On `main`.

**Why.** With reads on S1 (D-192), the v1 metadata sync is a **redundant writer** — the `meta_*` it
produces isn't read on the main paths for tenant 1. Retiring it makes `meta_*` static (no surprises)
and shrinks the v1 surface the Step-5 drop has to reason about. User chose the **full** retirement.

**Scope — REMOVE (the v1 sync writer + its now-dead UI):**
- **worker.py** — the v1 metadata tick (`poll_and_run_once` block, ~1460-1468). KEEP `_oauth_token`
  (137/175) — it is **shared** with pipeline execution + S1/S4 sync, not v1-sync-only.
- **scheduler.py** — `reap_stalled_metadata_jobs` (def ~476-488) + its entry in the tick tuple (~292).
  KEEP the S1 ticks + the `import primeqa.metadata.models` registration (line 179).
- **views.py** — the 5 v1 sync routes: `environments_refresh_metadata` (2976, the full-sync writer),
  `environments_quick_refresh` (3141, the delta writer), `environments_sync_progress` (3056) + the v1
  sync `cancel` (3200) + `retry` (3231). (The S1 routes `/sync-substrate` + `/sync-substrate/status` at
  2826/2855 STAY — the replacement.)
- **metadata/routes.py** — the `/api/metadata/<id>/refresh` POST (writer) + the `/sync-events` SSE
  (its only callers — detail.html:84 + sync_progress.html:304 — go with this slice). KEEP the GET
  readers (`current` / `diff` / `impacts` / `sync-status`) — reader-retirement scope.
- **templates** — `environments/detail.html`: remove the v1 refresh form (~40-73) + the v1 sync-events
  progress JS (~80-90); the **S1 panel already on the same page** (D-164, lines 103-208) is the
  user's path, so this is clean. `runs/preview.html`: replace the v1 quick/full buttons (~36-47) with a
  link to the env-detail page (where the S1 sync lives). **Delete** `environments/sync_progress.html`.
- **tests** — `tests/test_metadata.py` (the 10-test v1-sync integration suite) drives the removed
  `POST /api/metadata/<id>/refresh` end-to-end, so it is **obsolete** — remove it. `test_r3_metadata.py`
  (R3-1..R3-4) exercises `SyncEngine` **directly** (fake fetchers), which stays, so it is unaffected.

**Scope — KEEP (deleted wholesale at the Step-5 `meta_*` drop, or reader scope):**
- `primeqa/metadata/{models,repository,service,worker_runner,sync_engine}.py` internals — now
  **unreachable for writes** (the service writer methods `refresh_metadata`/`run_queued_sync` + the
  `worker_runner` claim/run functions become dead code), but they live in the module that the Step-5 drop
  deletes in one shot, so re-deleting their internals here is redundant churn. The `meta_*` models +
  GET readers + `_oauth_token` stay.

**Outcome.** No new `meta_*` writes from any path; the v1 sync UI is gone; users sync via S1. `meta_*`
becomes a static snapshot until the drop.

**Verification.** App imports clean; no dangling refs to the removed routes/templates (grep). Full unit
suite green. Manual: `/environments/<id>` shows only the S1 "Sync substrate" panel; `/runs/new/preview`
drift banner links to env settings. **Rollback:** git revert (and the v1 routes/worker tick return).

**Notable scope item (surface at the HOLD):** removing `test_metadata.py` drops the canonical suite's
v1-sync coverage (10 tests) — justified, the functionality is retired; the S1 sync has its own coverage.

---

## D-194 (scoping) — the reader-retirement long pole is an execution-engine program; split Step 5 into 5a / 5b

**Status:** Scoping decision (docs-only). Settles the size + shape of the remaining cutover work after a
four-way read-only audit of every `requirements` / `test_case_versions` / `metadata_impacts` reader.
Splits SEQUENCE Step 5; defines the 5b phased plan. No code.

**The audit finding.** The v1 test-authoring + **execution** + agent-repair flow is **still the live
product** and runs on `test_case_versions`: the executor reads `TestCaseVersion.steps` to run against
Salesforce (`worker.py:_run_execute_stage`), the agent fix-and-rerun loop writes new versions
(`intelligence/agent.py`), and the pre-execution validation gate reads `validation_report`. The
substrate spine exists and UI Areas 2–5 (D-166–173) added **parallel, mostly-read** surfaces — `/claims`,
claim detail, a *sync* claim-run (S4), `/substrate-insights`, the release evidence panel — but **S3
generation is a read-only intake channel and there is no substrate execution / repair / validation
path.** So retiring `test_case_versions`/`requirements` is **NOT a reader re-point** (the ~28 views/
template sites + ~14 release/runs resolvers are the *surface*); it is **replacing the v1 execution
engine with the S4/S3 spine + a data backfill + a dual-run cutover** — substrate-roadmap scope, not
Step-5 prep.

**The decisive consequence — the two drops have wildly different readiness.** D-065 bundled the `meta_*`
metadata drop with the v1 product-table drop into one Step 5. But:
- **`meta_*` (the 8 metadata tables): READY.** Every reader is retired — reads on S1 (D-159–162),
  preflight off `meta_*` (D-192), check_drift on S1 (D-184), the writer retired (D-193), parity clean
  (D-190), the read-bridge relocated (D-191). The cutover's actual goal is achievable now.
- **`test_case_versions` / `requirements`: FAR from ready** — the live execution engine runs on them.

**Decision — split Step 5 (SEQUENCE updated):**
- **Step 5a — the `meta_*` metadata drop (READY, irreversible).** Drop the 8 `meta_*` tables + resolve
  the FK web into `meta_versions` (`environments.current_meta_version_id`;
  `test_case_versions.metadata_version_id` + `validated_against_meta_version_id` → drop the FK
  constraints, columns stay inert on the kept table; `entity_dependencies.meta_version_id`); delete the
  v1 metadata module; remove the `cutover_read_s1` flag. **`metadata_impacts` is reassigned to 5a** — it
  is metadata-impact tracking whose writer retired in D-193 (now static) and it FK-bridges
  `meta_versions`↔`test_cases`; it drops in 5a (re-source the `/impacts` UI to S8 grounding, or drop only
  its FK constraints). Archive-first; no rollback past the migration.
- **Step 5b — the v1 product-table drop (`test_case_versions` / `requirements`), DEFERRED.** Phased,
  gated on S4 execution reaching v1 parity: **A** S4 execution at v1 parity (run S3 recipes, write
  results); **B** agent fix-and-rerun + validation gate on the spine; **C** re-point the v1 reader
  surfaces (test library, reviews, `/impacts`→S8, run history/detail, `/run` 4-mode picker → S4, release
  test-plan); **D** backfill v1 `test_case_versions` → S3 recipes; **E** dual-run flagged → cutover →
  drop + the ~17-FK web (`run_test_results`, `agent_fix_attempts`, `ba_reviews`,
  `test_case_data_bindings`, `release_*`, `generation_batches`, `llm_usage_log`,
  `generation_quality_signals`).

**Why decouple (not just sequence).** Bundling forces the *metadata* cutover — done — to wait on an
unrelated, far-larger *execution-engine* program. 5a delivers the cutover thread's goal (S1 as the sole
metadata source) on its own irreversible migration; 5b is its own roadmap.

**Status of the cutover after this.** Metadata cutover effectively complete (reads/writer/preflight/
drift all on S1; parity clean). 5a (the `meta_*` drop) is the next *achievable* irreversible step,
pending its own pre-drop checklist + an explicit GO. 5b is deferred substrate-execution work. This is a
docs/scoping entry — no behavior change.

---

## D-195 (design) — Step 5a: retire the last `meta_*` readers + the v1 metadata module, then drop the tables

**Status:** Design (impl pending, multi-phase). Executes cutover **Step 5a** (D-194): `meta_*` → S1 is
the sole metadata source. Ends in the **one irreversible migration** dropping the 8 `meta_*` tables +
`metadata_impacts`. On `main`. Plan: `.claude/plans/abstract-forging-moonbeam.md`.

**Shape — four phases; 5a.1–5a.3 reversible, 5a.4 irreversible (held last).** Each phase is its own
impl commit; 5a.4 applies the prod migration **only after** the `meta_*`-free code is deployed + a
zero-reader gate, with archive-first + a hard HOLD.

- **5a.1 — every remaining metadata READER goes S1-only.** Add `label` (from `display_name`) to the S1
  reader's `_S1Object`/`_S1Field` (the object/field picker needs it). Make `MetadataAccessor` S1-only
  (drop the `_repo`/flag-gate/`get_version`; read methods → the s1_reader, or `[]` when no org model).
  Re-point: `test_management/service._metadata_accessor` (drop the `metadata_repo` param, D-192
  precedent) + its callers (generation_jobs, the generate routes); the object/field picker
  (`test_management/routes.py`); `StepValidator` (`views.py:2438`); and **relocate check_drift** out of
  the to-delete `metadata/service.py` into `metadata_bridge/drift.py` (it reads only the S1 anchor +
  live SF Tooling), S1-only (no `meta_*` fallback).
- **5a.2 — remove `/impacts` + the dead GET routes.** `/impacts` retires entirely (web + API routes +
  the `MetadataImpact` model/repo/service + templates + the release "Impacts" tab) — decision: **remove**,
  not re-source to S8 (the `/substrate-insights` drift board already shows "what drifted"; the writer
  retired in D-193 so it's static). Delete the dead `metadata_bp` GET routes (current/diff/sync-status/
  impacts) + its `app.py` registration.
- **5a.3 — relocate `_oauth_token`, delete `primeqa/metadata/`, remove the flag.** Move `_oauth_token`
  (shared with pipeline + S1/S4 sync) → `primeqa/core/oauth.py`; re-point its 6 importers + 2 test
  patches. Delete the whole `primeqa/metadata/` module + the model-registration imports (app/scheduler/
  worker). Remove the `cutover_read_s1` flag (column + helper + branches). **Tests:** delete
  `test_r3_metadata` / `test_metadata_accessor` / `test_check_drift_anchor` (they test the removed sync/
  flag/fallback); **skip+defer** the 5 `meta_*`-seeding integration files (`test_management`/`executor`/
  `intelligence`/`cleanup`/`reliability_fixes` — they exercise 5b-bound v1 flows; decision this session);
  the 3 S1 reader tests survive (verified: no `primeqa.metadata.*` import). **Readiness gate:** a grep
  for every `meta_*`/`MetadataRepository`/`MetaVersion` reader returns zero outside `metadata_bridge`.
- **5a.4 — the IRREVERSIBLE migration (hard HOLD).** Archive-first (`pg_dump` the 9 tables).
  `migrations/052_drop_meta_tables.sql`: drop the external FK constraints from KEPT tables
  (`environments.current_meta_version_id`; `test_case_versions.metadata_version_id` +
  `validated_against_meta_version_id`; `entity_dependencies.meta_version_id`;
  `release_impacts.metadata_impact_id` — columns stay inert), then `DROP TABLE` `release_impacts`,
  `metadata_impacts`, and the 8 `meta_*` tables. Apply to Railway **only after** the `meta_*`-free app is
  deployed + the gate is green + an explicit GO. (`entity_dependencies` is a live `intelligence` table —
  its FK constraint drops, the table stays; the agents' "drop it" was wrong, verified.)

**Sequencing (critical).** 5a.1→5a.2→5a.3 commit to `main` → Railway auto-deploys → the prod app becomes
`meta_*`-free. The migration (5a.4) drops the tables only AFTER that deploy, so the running app never
queries a dropped table. Rollback is per-commit git revert through 5a.3; **past the 5a.4 migration there
is no rollback** — archive-first is the only safety.

**Scope boundary.** 5b (the v1 product-table drop `test_case_versions`/`requirements` + the
execution-engine replacement) stays deferred (D-194), untouched here.

---

### D-195.1 — Step 5a.1 impl: every live metadata reader S1-only

**What.** Retired the meta_* side of the read path so every *live* metadata read returns the S1
semantic org model. Six edits:

1. **`metadata_bridge/s1_reader.py`** — added `label` to `_S1Object` / `_S1Field`, populated from
   `Entity.display_name` (= the SF object/field label: `presentation.label or name or external_id`,
   per `sync/materialize.py`). The object/field picker needs it.
2. **`metadata_bridge/accessor.py`** — `MetadataAccessor` is now **S1-only**: dropped the `_flag_on`
   gate, the `meta_*` `_repo` path, `get_version`, and the `__getattr__` delegation; constructor is
   `(tenant_id, s1_reader)`; reads delegate to the reader or return empty. Safe to delete the repo
   path: the only consumers (`TestCaseGenerator`, `TestCaseValidator`) call **only** `get_objects` /
   `get_fields` / `get_validation_rules` — never `get_version` or any `__getattr__`-delegated method
   (verified by grep before removal).
3. **`test_management/service.py` `_metadata_accessor`** — always builds the S1 reader (dropped the
   `cutover_read_s1_enabled` gate); constructs the 2-arg accessor. The legacy `metadata_repo` param is
   accepted but **ignored**.
4. **`test_management/routes.py`** object/field picker — reads `build_metadata_s1_reader(tenant)`
   instead of `MetadataRepository`; gates on the reader being hydrated, not on the (soon-inert)
   `current_meta_version_id`.
5. **`views.py`** test-case-edit `StepValidator` — gets the S1 reader (the validator is duck-typed; a
   falsy reader short-circuits its metadata checks — no crash).
6. **Tests** — deleted `tests/unit/test_metadata_accessor.py` (its whole subject — the flag gate,
   meta_* passthrough, `get_version`-stays-meta — is gone); extended the golden-equivalence test
   (`test_metadata_s1_reader_bulk_parity`) so `label` is populated in the per-entity oracle and
   compared (object + field serializers) with a structural assertion.

**Scope refinements vs the plan (recorded forks).**
- **`metadata_repo` threading kept (deferred to 5a.3).** Rather than unthread `metadata_repo` from the
  4 public methods (`generate_test_plan` / `revalidate_test_case_version` / `apply_validation_fix` /
  `generate_test_case`) + their callers now, 5a.1 leaves the param in place (ignored). The callers'
  `MetadataRepository(db)` constructions are **inert** — the accessor reads S1 regardless — and their
  removal is cohesive with deleting `primeqa/metadata/` in 5a.3. Smaller, behavior-focused 5a.1.
- **`check_drift` deferred *wholesale* to 5a.3 (not split).** Its S1-freshness anchor already resolves
  to S1 for the live flag-on tenant (D-184), so no live reader is on meta_*. Relocating it out of the
  to-be-deleted `metadata/service.py` is entangled with the **v1 `SalesforceClient`** (with
  `query_tooling`) co-located in that same file — the canonical `integrations/sf_client.py` client has
  only *typed* tooling methods, no generic `query_tooling`. That SF-client decision belongs with the
  module dismantling in 5a.3, so anchor-S1-only + relocation move together there.
- **`cutover_read_s1_enabled` kept** in `accessor.py` (its sole remaining caller is `check_drift`'s
  anchor) until 5a.3 removes the flag.

**Verification.** `import primeqa.app` clean; **2382 unit + semantic tests green**; the golden test
proves bulk==per-entity hydration *including* `label`. Full reader inventory after 5a.1: the only
remaining `MetadataRepository` sites are `/impacts` (views.py — 5a.2 removal), the **inert** generation
threading (`generation_jobs:292`, `routes:1000/1030/1444`), and `check_drift` (views:1687) — all
scoped to 5a.2/5a.3. No live user-facing reader (generation, validation, picker, step-validation) reads
meta_*.

---

### D-195.2 — Step 5a.2 impl: remove the metadata-impact subsystem (full)

**Premise break (surfaced + decided).** The plan treated `/impacts` as a self-contained feature.
Ground truth: `MetadataImpact` is the spine of the v1 "metadata-impact → risk-score → GO/NO-GO" chain,
woven into *live* code — `RiskEngine.score_impact`/`score_all_release_impacts` (read it, reachable from
the live `/releases/:id/score-risks` endpoints), `decision_engine`'s GO/NO-GO "high-risk impacts"
criterion, and `MetadataImpactRepository` as a **required** `TestManagementService` constructor arg
(~6 app sites + 6 test files). Both feeder tables are already **dead-data** (writers retired in D-193:
`metadata_impacts` ← `run_impact_analysis` no callers; `release_impacts` ← `add_impact` no callers).
**User chose full subsystem removal now** (vs. defer the chain to 5b / re-scope).

**Removed.** `/impacts` UI (4 web routes + 5 API routes in `test_management/routes.py` +
`templates/impacts/`); `MetadataImpact` model + `MetadataImpactRepository` + the 6 impact service
methods + `regenerate_for_impact` + `_impact_dict`; the `impact_repo` constructor arg (re-pointed all 6
app sites + 6 test files); `RiskEngine.score_impact` + `score_all_release_impacts` (+ the dead
`import json`); `ReleaseImpact` model + `Release.impacts` relationship + `add_impact`/`list_impacts` +
`release_service.get_release_detail`'s impacts block; `decision_engine` Check 3 + the
`no_unresolved_high_risk_impacts` criterion (release-create handler + default + `new.html` checkbox);
the release-detail **Impacts tab**; the dead `metadata_bp` blueprint (`metadata/routes.py` deleted +
`app.py` unwired); the `/impacts` step in `system_validation/primeqa_core.json` + `_ux_audit.py`.

**Preserved (judgment call — not part of the impact subsystem).** `RiskEngine.score_test_case_priority`
+ `rank_release_test_plan` read run-history / `ReleaseTestPlanItem` (not impacts), so the two
score-risks endpoints stay — now **ranking-only** (dropped the `score_all_release_impacts` line; the
release-detail "Score risks" form still works). The GO/NO-GO decision loses one criterion (dead-data
today, so a no-op on real decisions).

**Deferred.** The `metadata_impacts` + `release_impacts` **tables** are untouched (inert); they drop in
the 5a.4 migration as originally planned — the ORM models are gone now, so that drop is clean.
`primeqa/metadata/service.py`'s `run_impact_analysis` (the dead writer, still imports `MetadataImpact`
function-locally — no callers) goes with the whole `primeqa/metadata/` deletion in 5a.3.

**Verification.** `import primeqa.app` clean; py_compile (incl. the 6 integration tests, which can't run
locally) clean; grep gate **zero** `MetadataImpact`/`ReleaseImpact`/`impact_repo`/`score_impact`/
`/impacts`/`metadata_bp` refs outside `primeqa/metadata/`; `primeqa_core.json` valid; **2382 unit +
semantic tests green**. 23 files, +32/−643.

---

### D-195.3 — Step 5a re-scope (design): the `meta_*` drop is 5b-coupled; 5a = read-cutover + flag retirement + dead-table drop

**How it surfaced.** Before touching 5a.3 (the planned "delete `primeqa/metadata/`"), a read-only
mapping workflow (7 agents) charted the footprint. Its adversarial critic flagged — and I then
**verified against ground truth** — a premise break in the D-195 plan.

**The verified break.** `test_case_versions.metadata_version_id` (`test_management/models.py:135`) is
`ForeignKey("meta_versions.id")` **`nullable=False`**, and is **runtime-populated on every test-case-
version insert** (`service.py:347`, `:769` → `env.current_meta_version_id`; `repository.py:429`;
`agent.py:506`). `environments.current_meta_version_id` (`core/models.py:105`) is also an FK to
`meta_versions`. Because the FK is a string table-reference, the `meta_versions` Table must stay
**registered** (`import primeqa.metadata.models` at boot) for the `TestCaseVersion` mapper to configure.
⟹ `meta_versions` + the `MetaVersion` model + `MetadataRepository` are welded to the **live**
`test_case_versions` product table, which is explicitly **5b** (D-194).

**Consequence — D-195's plan was wrong on two counts:** (1) "5a.3 deletes `primeqa/metadata/`
entirely" — false; `models`/`repository`/`service`(run_queued_sync) back the live TCV FK. (2) "5a.4
drops the 8 `meta_*` tables" — false for `meta_versions` (the FK target). D-194 cleanly split *metadata*
(5a) from *product tables* (5b), but a NOT-NULL FK straddles the line: the **physical** `meta_*` drop +
`primeqa/metadata/` deletion are inherently gated on `test_case_versions` retirement (5b).

**Re-scope (user-chosen — minimal close).** 5a's real deliverable, the **metadata READ cutover**, is
**already done** (5a.1 every reader S1-only; 5a.2 impact subsystem gone). The tail:
- **5a.3** = retire the `cutover_read_s1` flag only — `check_drift` / `_resolve_drift_anchor` S1-only
  **in place** (`metadata/service.py`; drop the flag gate + the `meta_*` anchor fallback — the live-SF
  Tooling drift probes are source-agnostic and stay; **no relocation**) + delete `cutover_read_s1_enabled`
  (`accessor.py`) + the `TenantAgentSettings.cutover_read_s1` attr (`core/models.py`; **DB column left
  inert** — not dropped) + simplify the cutover comments + cut the 1 unused module-scope import
  (`test_management/routes.py:23`). Tests: delete `test_check_drift_anchor` (flag + fallback gone) +
  `test_r3_metadata` (v1 SyncEngine, retired) + add an S1-only drift-anchor test.
- **5a.4** (irreversible, hard HOLD) = a migration dropping **only** the two fully code-dead tables
  `release_impacts` + `metadata_impacts` (writers retired D-193; all readers removed 5a.2). Archive-first.
- **Deferred to 5b** (with `test_case_versions`): the `meta_versions`/`meta_objects`/`meta_fields`/
  `meta_validation_rules`/`meta_flows`/`meta_triggers`/`meta_record_types`/`meta_sync_status` table drop
  + the `metadata_version_id` FK relaxation; the `_oauth_token` + `check_drift` relocations out of
  `primeqa/metadata/`; the canonical-`SalesforceClient` `query_tooling` addition; and the deletion of
  `primeqa/metadata/{models,repository,service,worker_runner,sync_engine}.py`.

**Verification of the map's own claims** (vetted, not assumed): the FK + nullable + runtime-population
read directly from the cited files; the map's self-caught gap (`scripts/revalidate_test_cases.py:38` +
`eval_sq205_domain_pack.py:34` module-import `MetadataRepository`) confirmed — both survive (repository
stays). The map's MEDIUM-confidence spots (the canonical-client `api_version` `'v'`-prefix mismatch; the
`views.py:1639` vs `:1683` import split; the exact relocated-`check_drift` line span) are **5b concerns**
now — moot for the in-place 5a.3.

**Impl landed (5a.3).** `_resolve_drift_anchor` (`metadata/service.py`) rewritten **S1-only** — reads only
`read_s1_freshness`, returns `None` when S1 is unavailable/unprovisioned/unusable; the `cutover_read_s1`
flag gate + the `self.metadata_repo.get_current_version` meta_* fallback are gone. `cutover_read_s1_enabled`
deleted from `accessor.py` (its `text`/`logging` imports too); the `TenantAgentSettings.cutover_read_s1`
model attr unmapped (DB column left inert); the cutover comments in `accessor`/`metadata_bridge.__init__`/
`preflight`/`worker` simplified. Tests: deleted `test_check_drift_anchor` + `test_r3_metadata`; added
`tests/unit/test_drift_anchor_s1.py` (4 cases — usable→anchor, and unusable/unprovisioned/unavailable→None,
each asserting **no** meta_* fallback). **Deviation from the 5a.3 map:** it claimed
`test_management/routes.py:23 from primeqa.metadata.repository import MetadataRepository` was unused — but
it's used at `routes.py:1360` (`generate_test_case`, no local re-import), so it was **kept** (and
`metadata.repository` survives to 5b regardless). Verified: `import primeqa.app` clean; **2378 unit +
semantic green** (the 4 new drift cases included). 6 code files + 2 test deletions + 1 new test.

---

### D-195.4 — Step 5a.4 APPLIED + Step 5a CLOSED

**The irreversible act, done.** `migrations/052_drop_dead_impact_tables.sql` applied to the Railway prod
DB (`BEGIN / DROP TABLE release_impacts / DROP TABLE metadata_impacts / COMMIT`, exit 0).
**Archive-first:** `pg_dump` of both tables (schema + data) captured to
`/tmp/archive_impact_tables_20260608_144738.sql` (8.4 KB) before the drop — the only recovery path
(ephemeral `/tmp`; dev env, backups waived by the user).

**Verified post-drop:** `metadata_impacts` + `release_impacts` are gone (`pg_tables` query empty);
`meta_versions` + `test_case_versions` are **untouched** (both still present) — only the two dead tables
dropped, the load-bearing ones intact. `import primeqa.app` clean post-drop (no code references the
dropped tables — all removed in 5a.2).

**Step 5a is COMPLETE.** S1 is the sole metadata **read** source. Ledger: 5a.1 (D-195.1) every reader
S1-only · 5a.2 (D-195.2) metadata-impact subsystem removed · 5a.3 (D-195.3) `cutover_read_s1` flag
retired · 5a.4 (D-195.4) the two dead impact tables dropped. **Deferred to 5b** (gated on
`test_case_versions`): the `meta_versions`/content-table drop, the `_oauth_token` + `check_drift`
relocations, and the `primeqa/metadata/` module deletion.

---

### D-195.5 — 5b assessment → census STOP → v1 test corpus deleted → pivot to S4 envelope growth

**5b (v1 product-table retirement) assessed; the dual-run probe correctly stopped.** A read-only
mapping + 3-agent assessment found the 5b entry-gate **not met**: S4 execution is ~40% of v1's envelope
(read-inspection + single negative-reject + partial single positive-CRUD; missing full provisioning,
dependency-aware cleanup, multi-step `$var` chaining, agent fix-and-rerun, validation gate), there is
**no** v1→S2 backfill path, and the v1 tables are woven through the views/generation/release/runs/agent
core + a ~13-FK web. A de-risking probe (5b-A0: a v1→S2 translator + dual-run parity harness) was scoped
+ approved, then its **A0.0 census gate** (read-only `classify_archetype` over the corpus) returned
**0/15 in-envelope** (8 lone-`query` smoke-tests + 6 empty + 1 full CRUD lifecycle; corpus-wide
`expect_fail`=0) — the probe would have zero coverage. STOP, per the gate's design.

**The user declared the v1 test data disposable and directed its deletion.** Archived first (full
`pg_dump public` → `/tmp/archive_v1_testdata_20260608_153004.sql`, 36 MB), then an ordered `DELETE`
(honoring `SET NULL`, so `worker_heartbeats`/`llm_usage_log`/`activity_log`/`users`/`environments`
survive) cleared the whole v1 corpus: **154 test_cases, 173 versions, 97 requirements, 170 runs, 726
stages, 3041 run-events, 230 quality-signals, 34 batches, 27 suites, 54 sections, …** — every
test-management + execution + generation + release row to 0. `import primeqa.app` clean post-delete.
`TRUNCATE … CASCADE` was rejected (it would have nuked `worker_heartbeats`/`llm_usage_log` via their
`SET NULL` FKs); `DELETE` was the correct tool.

**Pivot (user-directed):** grow S4's executable envelope so the substrate (S3 generates, S4 executes)
is a capable test engine. Detailed below in **D-196**. (5b table-retirement itself stays gated on S4
reaching parity + the reader retirement — now *without* a backfill, since the data is gone.)

---

### D-196 — Grow S4's executable envelope: F6 test-data provisioning + dependency-aware cleanup (design)

**Why.** With the v1 corpus gone, the product runs entirely on the substrate. S4 executes three
archetypes today (metadata-inspection, single behavioral-negative, single positive create→read→assert,
D-115). The positive vertical's ceiling: `world.py` `resolve_operational_padding` pads required
**scalars** only and **fences off required lookups/master-detail** ("no parent construction — the §3
fence", `world.py:106`), so any object needing a required parent record can't be created. The
substrate's own roadmap (`substrate_4_execution/DEFERRED_ITEMS.md`) names **F6 — test-data provisioning
+ cleanup** as the load-bearing next frontier: the shared prerequisite for the next verticals
(update/delete-rejected negatives, multi-step positives) and an immediate broadening of the positive
vertical to the large class of lookup/master-detail objects.

**Goal.** Make S4 execute positive data-recipes on objects requiring required lookup/master-detail
parents — construct the parent(s), track every created record, tear them down reverse-order.

**Phasing (each its own design→HOLD→impl on `phase-22-substrate-4-provisioning`):**
- **F6.1 — cleanup spine (first).** New per-tenant `s4_created_records` table (alembic tenant branch,
  no `tenant_id` col — schema isolation, mirroring `s4_execution_runs`). A `CreatedRecordTracker`
  accumulates `(sobject, record_id)` in create order; teardown deletes **reverse-order** (children
  before parents) reusing `data_executor._best_effort_delete` + the `PQA_%` convention. `_run_positive`
  swaps its inline single delete (`data_executor.py:186`) for the tracker — behavior unchanged for the
  single-create case; N-record-ready for F6.2. The tracked records persist to `s4_created_records` at
  `finalize_run` (audit).
- **F6.2 — parent-lookup provisioning.** Extend `world.py` to recursively construct required parent
  records (read `references_object_entity_id` → build parent → thread its id into the child lookup;
  bounded recursion + cycle guard); `_run_positive` provisions parents before the target create; all
  flow into the F6.1 tracker. The §3 fence is lifted for required references.
- **F6.3 — live proving (env 59).** A positive recipe on a lookup-needing object: parent created →
  target created → read-back → assert → every PQA_% record deleted (post-run SOQL confirms no leak).

**Central decisions / forks (recorded):**
1. **Teardown in-execution; audit at finalize; reaper deferred.** F6.1 tears down reverse-order over the
   in-memory tracker before grading (as today); `s4_created_records` is the finalize-persisted audit.
   A crash-recovery **reaper** (deleting PQA_% records leaked if the process dies mid-run) needs
   *pre-teardown* durability (a brief-tx write per create, the async-B0 pattern) — scoped as a follow-on,
   NOT F6.1, to keep the spine clean.
2. **F1 lift-to-neutral: minimal.** Extend the already-S4-native `world.py` + a thin cleanup; lift only
   the specific v1 primitives needed (`PQA_%` naming, REST create/delete, `cleanup.classify_failure`) —
   not a wholesale `data_engine` port.
3. **S3 object-selection coverage** (the buildable-now unknown): whether `generation/emission.py`
   `_author_positive` currently picks lookup-needing objects sets how many recipes F6.2 unblocks
   immediately; F6 is the right capability-first foundation regardless. Verify during F6.2.
4. **Cleanup multi-pass** (v1's dependency-retry) deferred — start reverse-order single-pass; add retry
   only if live runs leak.

---

### D-196.1 — F6.2 parent-lookup provisioning: the `construct_world` recursion (design)

**Grounding.** A 6-agent read of the live code (`world.py`, `data_executor.py`, `bridge.py`, `plan.py`,
`data_mutation_client.py`, `generation/emission.py`, `governance_core.py`) settled the shape: F6.2 is
contained to the **S4 execution layer**. The bridge and plan are **not touched** — `bridge._project_positive`
(`bridge.py:340-349`) hard-asserts the 3-step `(Create, Read, Assert)` triple and carries `field_values`
verbatim; parent provisioning is a **runtime side-effect inside `_run_positive`**, never a plan step. Only
`world.py` + `data_executor.py` change; `provisioning.py` (the F6.1 tracker), `data_mutation_client.py`,
`bridge.py`, `plan.py` are unchanged.

**The entrypoint.** Parent construction needs live org creates (`client.create`) + recursive S1 padding, so
it cannot live inside the *pure* `resolve_operational_padding` (no client; value-only `PaddingResult`). A
new `world.py` entrypoint drives it while keeping the existing function as the leaf scalar resolver:
`construct_world(object_api, semantic_fields, *, s1, client, tracker, at_seq, _visited=frozenset(),
_depth=0) -> (scalar_filler, parent_filler, unfillable)`.

**Detection — requiredness, not relationship-type.** The fence at `world.py:107` keys off
`references_object_entity_id` (set for any lookup/master-detail). S1 does **not** distinguish master-detail
from lookup, and F6.2 does not need it to: the gate that decides whether a parent **must** exist is
**requiredness** (`is_nillable == False`, checked at `world.py:102` *before* the reference check). A
master-detail (always required) and a required lookup both get a parent built; an **optional** lookup is
filtered out before the reference check ever runs. We build precisely the parents Salesforce would reject
the create without — no more.

**Algorithm.** (1) Resolve leaf scalars via the existing function, but split the loop's output into three
buckets: `scalar_filler` · `required_refs = [(field_api, ref_object_entity_id)]` (the lifted lookups — no
longer dumped into `unfillable`) · `unfillable` (genuinely unsynthesizable **non-reference** types, still a
hard stop). (2) For each required parent: depth-bound check → cycle-guard check → fetch the parent Object
(`get_entities("Object", filters={"id": ref_object_entity_id})`) → **recurse** for the parent's own required
fields/parents → `client.create(parent)` → `tracker.record(...)` immediately (creation order) → thread the
new id into `parent_filler`. (3) Back in `_run_positive`: if `unfillable` non-empty → tear down any parents
already created (construct can now have org side-effects) then return `errored` pre-target-create; else
create the target (recorded **last**, so reverse teardown deletes it before its parents).
- **Cycle guard:** the Object `entity_id` (UUID) tracked in a `_visited` frozenset — catches self-reference
  (depth 1) and N-hop object cycles.
- **Depth bound:** `MAX_PARENT_DEPTH = 3` (named constant). Real required chains are 1–2 hops; deeper is
  almost always a cycle/misconfig the guard catches. Over-deep → honest `unfillable` → `errored`, never a
  partial write.

**Forks resolved (from D-196):**
1. **F1 lift-to-neutral — minimal.** Add `construct_world` to the S4-native `world.py`; reuse F6.1's tracker
   + `_best_effort_delete` unchanged. No v1 `data_engine` port.
2. **F2 S3 coverage — F6.2 unblocks already-emitted recipes; no S3 change.** S3 emission (`emission.py:650`)
   and grounding (`governance_core.py:271-291`) apply **zero** filtering for required parents — Contact→Account
   etc. are already groundable + emitted, blocked only by the world.py fence. *Marked assumed (reader-level);
   impl step 1 corpus-confirms ≥1 emitted positive recipe on a required-parent object before the F6.3
   "immediate win" claim.*
3. **F3 cleanup — reverse-order single-pass.** A child is always created after its parents, so reverse order
   is a valid delete order for the tree S4 built; multi-pass retry only if live F6.3 leaks.

**Open questions / leans (none block the design):**
- **Parent Name run-scoping.** The scalar filler gives a required text field the constant `'PQA'`
  (`world.py:141`) — *pre-existing* for the target; F6.2 inherits it for parents. **Lean: keep `'PQA'`,
  defer run-scoped naming as a uniform follow-on** (applies to target + parents alike); fine for F6.3
  single-run proving.
- **Depth = 3** confirmable with a max-chain query over env 59 at impl; tunable constant.
- **Master-detail vs lookup** — requiredness already discriminates (see Detection); asserted by a test.
- **Optional parents** out of scope (only required parents built).
- **Errored-path audit** — `created_records` threaded into the errored envelope too so the
  created-then-deleted audit stays complete.

**Verification.** Unit (stub S1 + stub create/delete): one-hop happy path, two-level chain, cycle-guard
fires, depth-bound, **scalar-only byte-identical** (no-regression), parent-create-rejected; existing
world/data_executor suites green. Integration: lookup-needing recipe still projects to the 3-step plan;
`RunEvidence.created_records` carries parent + target. Live (F6.3): real run on env 59, post-run SOQL
confirms zero PQA_% leak.

---

### D-196.2 — F6.2 refocus: the `is_createable` gap (corpus-grounded) + the construct leak fix

**The corpus check changed the picture.** A read-only census of the live org (env 59 → `tenant_1`) for the
F2 "unblocks real recipes today" assumption found it false *and* surfaced a pre-existing gap. The corpus
has exactly **one** data-recipe — a positive create on **Opportunity**. Its required references
(`is_nillable=False` + reference) are **all Salesforce-managed**: `CreatedById`, `LastModifiedById` (audit)
and `OwnerId` (owner, defaulted). Several required **scalars** (`CreatedDate`, `SystemModstamp`, …) are
`is_createable=False`. The padding loop filtered only on `is_nillable`, so it would try to *set* those
Salesforce-managed fields → the org rejects the whole create. So the real blocker for the one live recipe
was never "build a parent" — it was **"stop setting non-createable fields."** No object in `tenant_1`'s
org needs a business parent for the current corpus; the dominant required references are `OwnerId` (almost
every object) + audit fields.

**The adversarial review** (3 independent lenses over `construct_world`) verified termination, cycle/depth
guards, reverse-order teardown, single-create behavior-neutrality, and audit correctness — and found **one
real leak**: `construct_world` calls S1 reads (`get_entities`/`get_related`/`get_entity_details`) that
raise `VersionNotFoundError`/`ValueError`, NOT `SFClientError`; `_run_positive`'s construct `except` caught
only `SFClientError`, so an S1 read error after a parent was built would escape uncaught and leak it.

**Decision (refocus — chosen over "principled `defaultedOnCreate` now" and "drop the parent code").**
F6.2 ships:
1. **`is_createable` filter** in `resolve_operational_padding` — skip required fields that are
   `is_createable=False` (Salesforce-managed: `CreatedById`, `CreatedDate`, `SystemModstamp`, …). The
   corpus-grounded correctness fix; it also repairs a *pre-existing* latent gap (the single-create vertical
   would have rejected on `CreatedDate` against the current synced field set).
2. **Owner/queue reference skip** in `construct_world` — a required createable reference whose target is
   `User`/`Group` is **omitted** (Salesforce defaults `OwnerId`); we never build a User to own a test
   record. `_DEFAULTED_REF_OBJECTS = {User, Group}`. A genuinely-required *non*-defaulted User lookup is
   then omitted too → an honest pre-create rejection, never a wrongly-built User.
3. **Construct leak fix** — `_run_positive`'s construct `except SFClientError` widened to `except Exception`
   (tear down built parents, return `errored`); `_best_effort_delete` widened to `except Exception`
   (teardown can never raise / flip the outcome).
The **parent-construction recursion (D-196.1) stays** — written, 3-lens-verified, unit + integration tested
— but is **dormant** for the current corpus: it activates the moment a recipe targets an object with a
required createable **business** lookup (Contact→Account, a master-detail child, …).

**Deferred.** (a) Capture Salesforce's **`defaultedOnCreate`** in S1 (sync-mapper + `field_details` column
+ migration) — the principled way to distinguish "must provide" from "defaulted" (`OwnerId`), replacing the
`User`/`Group` heuristic. Its own slice. (b) Live exercise of parent-construction on env 59 once a
business-lookup recipe exists (F6.3 may hand-craft one).

**Verification.** 164 execution_engine unit + 2756 broad unit/semantic green. New tests: `is_createable`
skip (scalar + reference), owner-reference omit, non-createable-reference skip, and the
S1-error-mid-construct leak (errored + first parent torn down).

---

### D-196.3 — F6.3a: bare Salesforce field-name translation at the executor boundary

**The blocker the read-only proof found.** F6.2's `is_createable` fix made the live Opportunity recipe's
world *buildable* (proven read-only against `tenant_1`'s S1). But the recipe + padding speak S1's
**object-qualified** field names (`Opportunity.StageName`, `Opportunity.Name`) — S1 names every Field
`{Object}.{field}` for graph uniqueness (`sync.phases` field phase, ~line 405). Salesforce's REST create
and SOQL speak **bare** names (`StageName`). Nothing in the execution path translated between them, so a
live create / read would be rejected by the org. Pre-existing, independent of F6.2; latent because no data
recipe had run live since field names became qualified.

**Root cause + layer.** S1's qualified names are correct *internally* (uniqueness); the executor is the
logical→physical boundary where they must become the org's bare API names. Fix there — NOT by re-emitting
recipes (heavy) or adding a bare-name S1 column (the bare name is derivable: drop the `{object}.` prefix).

**The fix (all in `data_executor.py`).** Three pure helpers — `_sf_field(name, sobject)` (strip the
`{sobject}.` self-prefix via `removeprefix`; a bare name, or a relationship path like `Owner.Name`, passes
through unchanged), `_sf_fields` (bare-ify create-payload keys), `_sf_soql` (bare-ify self-qualified field
tokens in a SOQL string) — applied at the **three** SF-facing points:
1. the create payload (recipe field(s) + operational padding, merged) before `client.create`;
2. the read-back SOQL (after `$<step>.id` resolution, so the WHERE id stays intact) + the captured field names;
3. the assert's `subject_ref` field lookup (SF returns rows keyed by bare names).

**Back-compatible by construction.** A name without the `{sobject}.` prefix is unchanged, so the existing
positive tests (bare names) stay green — verified.

**Readiness (read-only, real S1).** For the live Opportunity recipe the executor would now send create
`{StageName: 'Prospecting', CloseDate: '2026-06-09', Name: 'PQA'}` + SOQL
`SELECT StageName FROM Opportunity WHERE Id = '<id>'` — both valid. No parent construction (clean).

**Verification.** 165 execution_engine unit + 22 integration + 2779 broad green; new test feeds qualified
names and asserts bare create / SOQL / assert. **Next:** the live run on env 59 (the actual org write needs
an explicit go-ahead + a post-run leak check).

---

### D-196.4 — F6 close: provisioning + cleanup vertical realized; merge to main

F6 (test-data provisioning + dependency-aware cleanup) is built, green, and read-only-proven against the
live org. Three slices landed on `phase-22-substrate-4-provisioning`:
- **F6.1** (`33023d3`) — `s4_created_records` + `CreatedRecordTracker` reverse-order cleanup spine (the
  inline single-delete generalized to N records).
- **F6.2** (`32e7a14`, D-196.1/.2) — `construct_world` recursive parent provisioning (Object-`entity_id`
  cycle guard + `MAX_PARENT_DEPTH=3`, owner/queue refs omitted) + the corpus-grounded `is_createable`
  filter + the construct-path leak fix.
- **F6.3a** (`d879289`, D-196.3) — bare SF field-name translation at the executor boundary
  (`_sf_field` / `_sf_fields` / `_sf_soql`).

**Verification.** 165 execution_engine unit + 22 integration + 2779 broad green. A 3-lens adversarial review
verified the recursion (its one real finding — a construct leak — fixed). Read-only proofs against
`tenant_1`'s real S1: the live Opportunity recipe's world is buildable + the executor would send a valid
bare create (`{StageName, CloseDate, Name}`) + bare SOQL.

**The live run (F6.3) is deferred — blocked externally.** Executing the recipe against env 59 failed at
Salesforce OAuth (`invalid_client_id`) **before any org write** (zero side effects; the DB tx rolled back).
The connection "Prime QA SFDC" (id 2, `client_credentials`) has an invalid / stale Connected-App consumer
key (stored `client_id` is 204 chars vs the ~85 of a real key). Refreshing those credentials is the user's
action; the live proof re-runs unchanged once it's fixed. Logged as task #193.

**Merge.** `phase-22-substrate-4-provisioning` → `main` (`--no-ff`), the substrate convention at phase
close; the merge gate is the green suite above. The data-recipe path deploys **inert** — no enqueue source
(the production loop is live + idle, D-132) — so the positive-vertical breadth ships dormant until a data
recipe is intentionally run.

**Deferred (DEFERRED_ITEMS, dated 2026-06-09).** The live run (creds); `defaultedOnCreate` in S1 (the
principled `OwnerId` distinction, replacing the `User`/`Group` heuristic); the crash-recovery reaper
(pre-teardown brief-tx durability); live exercise of the dormant parent-construction (needs a business-lookup
recipe); the next verticals (update/delete-rejected negatives, multi-step positives — both now unblocked by
F6).

---

### D-197 — S4 enqueue source: the spine + a manual queue endpoint

**Why.** S4's execution loop is **wired but idle** (D-132): the `s4_execution_jobs` queue, `ExecutionJobStore`,
the consumer, and the reaper exist and fire every tick — but nothing enqueues a job, so no recipe runs in
production. This opens the **enqueue source**: the thing that puts a recipe-execution job on the queue. It's
the foundation the later automated triggers (approval-hook, scheduled re-verification) reuse unchanged.

**The load-bearing decision — the consumer runs all recipe kinds via the SYNC path.** The async consumer
default (`run_recipe_execution_async`) **refuses every data-recipe** (positive *and* behavioral-negative) — it
is metadata-path-only by design (D-129; the data vertical reads S1 mid-execute, and the async wrapper holds no
DB connection across SF I/O). So F6's data recipes can't flow through it. **Decision:** flip the consumer's
default `run_fn` to the **synchronous** `run_recipe_execution_for_tenant` (`run.py:230`), which runs *all*
recipe kinds (it holds a connection across the run — boundary A — exactly as the existing sync "Run" button
does). `client_resolver` becomes **optional**; the default path passes `client=None` so the sync run fn
self-resolves the correct client per kind (Tooling/Data) *after* it selects the recipe (the consumer can't know
the kind up front). `worker.py` `s4_execution_tick` drops the Tooling-only `_default_s4_client_resolver`
injection (the resolver stays *defined*, reserved for the future async path). Holding one DB connection per
in-flight job is an accepted low-volume interim; the **data-path async bracketing** (restructuring the
mid-execute S1 read into brief transactions) stays the deferred-proper path — when it lands, the default
`run_fn` becomes a metadata→async / data→sync dispatcher and `_default_s4_client_resolver` re-enters,
kind-aware.

**The enqueue function.** `execution_engine/intake.py` `enqueue_s4_execution(*, tenant_id, test_id,
environment_id, created_by=None)` — a thin wrapper over the already-built idempotent
`ExecutionJobStore.create_or_get_job` (mirror of `enqueue_s3_generation`). Thinner than S3's: no S1-version
pin, no requirement read — the job carries only `(test_id, environment_id, created_by)` and the recipe is
selected at run time by `test_id`.

**The manual queue endpoint (v1 runtime, lands on `main` after the substrate merges).** `POST
/api/s4-execution-jobs` (+ `GET /api/s4-execution-jobs/<id>` status poll), mirroring the S3 enqueue route. The
**production-confirm gate moves to ENQUEUE time** (reusing `runs/bulk.py environment_can_bulk_run`): the sync
"Run" button gates *before* its immediate run, but this route defers the run to the worker, so the human
confirm must happen at enqueue — else an unconfirmed prod data-recipe would mutate the org on the next tick.
The user-chosen first trigger; the sync "Run" button stays.

**Branch split.** Substrate (`consumer.py`, new `intake.py`, the `worker.py` firing tweak) → a feature branch,
merged to `main` at slice close; the Flask route → `main` directly (v1 runtime), landed after the substrate
merges (it lazy-imports `enqueue_s4_execution`, no import-time coupling). Seam discipline mirrors S3: the route
validates the env + gates prod + closes its db *before* calling the substrate, which opens its own tenant
connection.

**Verification.** 193 execution_engine unit + integration green: consumer default-path regression (no resolver
→ `client=None`), a drift-guard binding the default `run_fn` to the sync path (not the data-refusing async
one), `enqueue_s4_execution` idempotency + re-runnability, and the **full offline spine loop** (`enqueue →
run_s4_execution_tick (production defaults) → completed`). Route tests + the live run land with the `main`
route.

**Deferred.** The other two triggers — approval-hook (needs an env-selection policy: approval carries no target
env) + scheduled re-verification (a `test`/`recipe` column on `scheduled_runs` + a firer branch); the
data-path async bracketing (above); cancel/SSE/run↔job-correlation UI.

---

### D-197.1 — F6.3 CLOSED: the live data-recipe proof, through the production loop

**The proof (2026-06-10, env 59 "Prime QA NEW", job 5, run `6aab8882-…`).** The approved Opportunity
value-claim recipe ran **live against the real org through the full production chain** — `enqueue_s4_execution`
→ the deployed Railway worker's `s4_execution_tick` → `run_recipe_execution_for_tenant` (the D-197 sync
default) → live Salesforce. Observed: create **HTTP 201** (bare payload `{StageName, Name, CloseDate}` — the
D-196.3 translation live-verified), read-back `SELECT StageName FROM Opportunity WHERE Id='006Ip…'` → 1 row,
`equals` **held** → outcome **passed** (1.4 s); cleanup delete **succeeded** (Salesforce-confirmed);
`s4_created_records` audit row persisted. This simultaneously live-proves F6.2's `is_createable` padding,
F6.3a's bare-name translation, the F6.1 audit table, AND the D-197 enqueue→consume chain — the first
data-mutation run the substrate has ever executed against a live org, and it went through the queue.

**Two findings en route (both resolved):**
1. **The `invalid_client_id` OAuth failure was environmental, not stale credentials.** The local machine lacks
   `CREDENTIAL_ENCRYPTION_KEY`; `get_connection_decrypted` swallows the decrypt failure (`except: pass`,
   `core/repository.py`) and returns the **raw Fernet ciphertext**, which local runs then sent to Salesforce as
   the client_id. The deployed services (which hold the key) authenticate fine. Lesson recorded: local live-run
   attempts require the key, or must route via the deployed worker (the production loop — as this proof did).
   The silent-fallback-to-ciphertext behavior is a latent foot-gun (a clear raise would have named the real
   cause two days earlier) — candidate hardening, not changed here.
2. **The F6.1 tenant migration had not been applied to prod** (its application was deferred to F6.3 by design).
   First live attempt (job 4) ran the org-side spine fully (create/read/assert/teardown — no org leak; teardown
   is in-run, independent of the DB tx) and failed only at persist (`UndefinedTable: s4_created_records`), with
   a clean rollback. Applied with explicit user GO: `alembic -x mode=all_tenants upgrade tenant@head` →
   `20260604_0030 → 20260608_0010` on `tenant_1`, verified, then job 5 passed end-to-end.

**Leak check.** The run's own evidence: cleanup `attempted=True succeeded=True` is Salesforce's response to the
DELETE (org-confirmed). An independent post-run SOQL was not run locally (the ciphertext constraint above);
the org-side confirmation + the in-run read-back (1 row found, then deleted) is the accepted proof.

**F6 is now fully closed** (F6.1/F6.2/F6.3a built + merged; F6.3 live-proven). Product theme #1 ("prove it on
a real org") complete.

---

### D-198 — Close the decision loop on the substrate: run results → risk rollup → GO/NO-GO (design)

**Why (product theme #3).** The substrate runs recipes and records evidence (S4 outcomes, S6 verdicts, S8
grounding-validity) — but nothing turns that into a release recommendation. The v1 `DecisionEngine` consumes
only v1 `pipeline_runs`; substrate evidence dead-ends at the D-172 evidence-only panel. The decision system IS
the product category (release intelligence) — this wires the missing half of the loop.

**Shape (the central fork, resolved): a substrate-native decision module + a thin composer; the v1
`DecisionEngine` stays zero-diff.** New `primeqa/intelligence/substrate_decision.py` computes the substrate's
own risk rollup + recommendation from S4/S6/S8 reads; new `primeqa/release/decision_composer.py` runs BOTH
engines and records ONE `ReleaseDecision` row whose `reasoning` JSON carries `{v1, substrate, mode, combined}`
— **no migration** (`create_decision(reasoning=...)` already persists an arbitrary dict into the JSON column,
verified `release/routes.py` + `models.py`). Rationale: the v1 engine's internals are v1-table-shaped (5b
retires them); the composer isolation makes v1's later retirement "drop one input," not engine surgery.
Follows the established best-effort console pattern (`release_substrate_console.py` — one tenant connection,
never raises).

**The evidence chain (every hop verified in code):** `releases → release_requirements → requirements` →
external_key (`jira_key OR 'req-'||id` — the convention shared by the S3 persister + `views.py`) →
`coordinator.list_tests_by_requirement(external_system='jira', external_key, link_kind='generated_from')` →
deduped claim `test_id`s → `coordinator.get_current_approved_claim` (the version a release ships) → per claim:
- **latest COUNTED run**: extend `_CLAIM_RUNS_SQL` (S4 base LEFT JOIN `s6_interpretations` for the verdict)
  with the version filter `(r.claim_version_seq IS NULL OR r.claim_version_seq = :approved_seq)` —
  recency-correct AND version-correct;
- **grounding**: `read_grounding_validity(session, test_id, approved_seq)`, fallback latest row when
  unapproved (the D-172 idiom); **stale** = `evaluated_at_version_seq < SemanticOrgModel.current_version_seq()`.

**Correctness rules:** a non-NULL `claim_version_seq` ≠ approved seq is **superseded evidence** — excluded
(the claim counts `never_run` unless a current-version run exists, with a `superseded_newer_run` flag). NULL
`claim_version_seq` **counts** but carries `version_unknown=true` (it is legitimately Optional through the
plan chain; strict exclusion would zero out real evidence). Grounding `broken` = **blocker** (a passing run of
an ungrounded claim is vacuous); `drifted` / stale-`intact` = warnings; newest counted run older than
`substrate_max_run_age_hours` (default 168 — runs are sparse until theme-4 auto-triggers) = warning.

**Decision policy:** per-release `decision_criteria.substrate_mode ∈ {off, advisory, gating}`, default
**advisory** (v1's recommendation stands; the substrate block rides along for the human + CI). **gating** is
degrade-only: combined = min-severity(v1, substrate) over `no_go < conditional_go < go` — the substrate can
veto, never upgrade; scores are never blended. Risk rollup is **substrate-native** (pass-rate +
grounding-integrity + coverage + freshness → 0–100 + critical/high/medium/low — vocabulary-compatible with
`risk_engine.py`, internals not reused: its inputs (`referenced_entities`, `CRITICAL_ENTITIES`,
`RunTestResult`) have no substrate counterpart). Environment axis: aggregate across envs now; a
`substrate_environment_id` criteria key is the later refinement.

**Slices (each design→HOLD→impl; v2-runtime work → main):**
1. **Evidence assembly** — `_assemble_claim_evidence(session, external_keys)` (the chain above; per-claim
   `{test_id, approved_seq, grounding{overall, stale}, latest_run{outcome, verdict, version_unknown},
   superseded_newer_run, never_run}`).
2. **Decision compute** — pure `compute_substrate_decision(claim_evidence, criteria)` mirroring the v1 output
   shape (`{recommendation, confidence, reasoning[], criteria_met, metrics, risk{score, level}}`) + the
   best-effort `get_release_substrate_decision(tenant_id, external_keys, criteria)` wrapper.
3. **Composer + ledger** — `decision_composer.evaluate_and_record`: v1 evaluate (untouched) + substrate
   (best-effort) + mode combine → one `create_decision` row; the external-key builder extracted to a shared
   helper so the two call sites can't drift. Regression guard: a release with no substrate claims behaves
   byte-identically on `{recommendation, confidence, criteria_met}`.
4. **Surfaces** — the Decision-tab panel upgrades to a recommendation card (advisory/gating banner; the
   stored snapshot renders in Decision Details); `/api/releases/:id/status` adds a `substrate` block projected
   from the latest decision's stored reasoning (no substrate query on the CI hot path). Seeded E2E both
   directions (passing evidence → go; broken grounding / failed runs → degraded).

**Open (recorded, non-blocking):** the 168 h freshness default revisits when theme-4 auto-triggers ship;
gating-by-default is a product call for that same moment; the decision-ledger home after 5b retires v1 tables
is deliberately left open by the composer isolation.

---

### D-198.5 — Theme #3 CLOSED: the decision loop on the substrate is wired end-to-end

All four D-198 slices landed on `main` (v2-runtime work):
- **D-198.1** — `intelligence/substrate_decision.py` `_assemble_claim_evidence`: the recency- and
  grounding-correct evidence chain (version-correct counted runs, NULL-seq tolerance with
  `version_unknown`, tolerant staleness). 7 integration tests; plus a root-caused fix to a pre-existing
  provenance-ordering flake the new tests surfaced (`order_by(event_at)` under-specified within one tx —
  both sites now tiebreak on `event_data['new_version_seq']`).
- **D-198.2** — `compute_substrate_decision` (pure; six checks → go/conditional_go/no_go + the
  substrate-native 0–100 risk, v1-shape output) + the best-effort `get_release_substrate_decision`
  wrapper. 15 unit + 2 wrapper tests.
- **D-198.3** — `release/decision_composer.py` `evaluate_and_record`: both engines, mode-combine
  (off/advisory/gating degrade-only), ONE `ReleaseDecision` row carrying the `{v1, substrate, mode,
  recommendation_source}` envelope; the shared `external_keys_for_requirements` builder; the API
  evaluate route re-pointed. 7 unit tests incl. the byte-identical v1 regression guard.
- **D-198.4** — the surfaces: the Decision-tab **Substrate recommendation card** (badge + risk + checks +
  advisory/gating banner; conditional render), the **web Evaluate button re-routed through the composer**
  (a second uncomposed `DecisionEngine` call site found + fixed in this slice), the CI
  `/api/releases/:id/status` **`substrate` block** (projected from the stored envelope — no substrate
  query on the CI hot path), and the seeded E2E both directions (clean evidence → go/low; broken
  grounding + failed run → no_go/critical).

**Verification.** 33 theme-3 tests (11 integration incl. both E2E directions, 15 compute, 7 composer);
the template compiles; 2697 broad green. The v1 engine is zero-diff throughout.

**The loop now closes:** substrate run results → S6 verdicts + S8 grounding → risk rollup → GO/NO-GO →
the release page + CI — with the human still confirming (recommendation-only, as ever). Product theme #3
complete. Deferred (recorded in D-198): the 168 h freshness default + gating-by-default revisit when
theme-4 auto-triggers ship; the decision-ledger home after 5b.

---

### D-199 — Automate the execution triggers (product theme #4, design)

**Why.** D-197 shipped the enqueue source but only the manual trigger; every substrate run still needs a
human. The vision needs runs to fire on their own: on approval, on a cadence, and at the CI gate — all
three reuse `enqueue_s4_execution` unchanged (the point of the D-197 spine). Batch-authorized by AK
(2026-06-10): themes #4/#5/#6/#2 proceed without per-slice GOs; irreversible actions still hard-HOLD.

**Trigger 1 — auto-enqueue on claim approval (the env-selection policy, resolved).** Approval carries no
target environment. Policy: **enqueue to every ACTIVE, NON-PRODUCTION environment with a Salesforce
connection** (`is_active AND NOT is_production AND connection_id IS NOT NULL`) — "verify everywhere it is
safe." Production is structurally excluded (prod runs keep the human `confirm_production` path; the
gating semantics of D-197 are preserved). Idempotent by construction (the queue's active-set dedup).
Hook: `s4_execution_console.approve_claim` — after the approval transaction commits, best-effort enqueue
(an enqueue failure never un-approves or blocks).

**Trigger 2 — scheduled re-verification.** `scheduled_runs` (public, v1) gains a nullable
`substrate_test_id UUID`; `suite_id` relaxes to nullable with a CHECK that at least one source is set
(migration 053, idempotent). `fire_due_schedules` branches FIRST on `substrate_test_id` →
`enqueue_s4_execution(tenant_id, test_id, environment_id=sched.environment_id)` (the env is already on
the schedule row) → `mark_fired(sched.id, None)` (no pipeline_run for a substrate fire; the job id is
logged). The substrate branch is extracted as `fire_substrate_schedule(sched)` so it unit-tests with a
stub.

**Trigger 3 — the CI gate re-verify.** The existing HMAC `ci-trigger` webhook additionally enqueues the
release's substrate claims for execution on the given environment (release → requirements →
`external_keys_for_requirements` → `generated_from` claims → enqueue each; deduped; best-effort — the v1
run creation is untouched). CI then polls `/api/releases/:id/status` whose D-198 `substrate` block carries
the verdict — the gate reads fresh evidence instead of stale. Helper:
`s4_execution_console.enqueue_claims_for_keys(tenant_id, external_keys, environment_id)`, shared by the
webhook (and any future bulk trigger).

**Out of scope / deferred:** per-claim target-env preferences (the all-safe-envs policy is the v1
default; a `auto_verify_environment_id` tenant setting is the refinement); UI to create substrate
schedules (the column + firer land first; the picker follows with the surfaces theme); flipping
`substrate_mode` to gating-by-default (the D-198 product call — revisit once auto-runs make evidence
plentiful).

---

### D-200 — Everyday surfaces (product theme #5, design + realization note)

**Gap analysis against the theme's four items:** (1) *run-time test-data injection* — already realized
on the substrate by F6 (`construct_world` operational padding + parent provisioning, D-196); (2) *UI to
browse tests + drill into failures* — already realized by the UI Phase (claims library `/claims`, claim
detail, `/runs/substrate`, run detail with evidence steps + S6 verdict/cause). The two REAL gaps:

**(a) Flake quarantine — storage-free, decision-time (no migration).** The slice-1 evidence assembly now
reads the recent **counted** runs (window 5, same version-correct SQL) and flags
`flaky = transitions ≥ 2` over the outcome sequence — the chronically-flipping signature. A single
pass→fail edge is a REAL regression and is never quarantined. In `compute_substrate_decision`, a flaky
claim whose latest run is not-passed is **quarantined**: excluded from the pass-rate denominator
(one jittery test cannot block a good release) and surfaced as a `flaky_quarantine` warning +
`metrics.quarantined` — visible, never silent. A flaky-but-currently-passing claim counts normally.
Opt-out per release: `decision_criteria.substrate_quarantine_flaky = false`. Storage-free by design:
recomputed from S4 truth each evaluation, no flag to go stale; a persisted quarantine ledger is the
later refinement if operators need manual pin/unpin.

**(b) Real notifications.** `shared/notifications.send_email` gains two REAL providers behind the
existing stable seam: `smtp` (stdlib smtplib; SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/STARTTLS) and
`sendgrid` (the v3 mail API via requests; SENDGRID_API_KEY). `log` stays the safe default; provider
selection is unchanged (`NOTIFICATIONS_PROVIDER`). New `notify_release_decision`: after the composer
records a decision, tenant admins get a heads-up email when the verdict is NOT a clean go (no_go /
conditional_go / substrate-gate degrade) — best-effort, never blocks the decision; a clean go sends
nothing (no noise).

---

### D-201 — The substrate repair agent opens, evidence-first (product theme #6)

**Why + the deliberate scope.** v1's fix-and-rerun agent proposes LLM fixes and auto-applies on
sandbox. The substrate deliberately deferred its agent (evidence-first). This opens the substrate's
repair loop at the scope the evidence supports today: **deterministic, human-gated repair suggestions**
derived from the S6 interpretation's CLOSED verdict + cause vocabulary — no LLM call, no auto-apply.
The vocabulary is finite and machine-attributed (D-111.1), so the suggestion map is exact, auditable,
and free: every failing verdict/cause pair maps to a suggestion that names **who owns the fix** —
`org` (Salesforce config: re-activate the VR, restore drifted metadata, a genuine enforcement defect),
`claim` (the asserted truth drifted: re-point at the firing rule, update the expected value),
`recipe` (the execution shape: enrich the payload past a platform constraint), or `ops`
(credentials/infra → re-run). Passing verdicts suggest nothing.

**Where.** Pure `evolution/repair.py suggest_repairs(verdict, cause_kind, vr_name)` (S8 owns the repair
loop); the run-detail console read attaches `repair_suggestions` to the interpretation; the run-detail
page renders them as an owner-badged list under the S6 verdict ("agent · human-gated").

**Deferred (the v1-parity ladder, in order):** (1) LLM-proposed recipe rewrites through the existing
`recipe_s8_rewrite` provenance path (the write machinery already exists — `write_recipe(actor='s8')`),
human-applied; (2) one-click apply of a suggestion (e.g. "update claim value" → a prefilled edit);
(3) sandbox auto-apply with the v1 confidence-gating discipline — only after (1)/(2) have human mileage.
Theme #6 is OPENED at honest scope, not at v1 parity — recorded plainly.

---

### D-202 — Finish the re-platform (product theme #2): the 5b program charter

**Honest status.** Theme #2 cannot be batch-completed: it is a multi-arc program ending at an
**irreversible** v1-table drop (hard-HOLD regardless of any batch authorization). This entry converts
the vague "gated on S4 parity" into a concrete, sequenced, gated program — the charter the next arcs
execute.

**The measured parity gap (verified in code, 2026-06-10):** v1's `execution/executor.py` speaks **7
verbs** (`create / update / query / verify / delete / wait / convert`); the substrate's data-recipe
representation speaks **3** (`CreateStep / ReadStep / DataAssertStep`) with the bridge hard-asserting
the single triple (`bridge.py:340`, multi-step deferred). S2 has **no Update/Delete step models** — the
verticals are representation-first, not executor-first. Everything else 5b once feared is now DONE:
provisioning + cleanup (F6), the enqueue loop + auto-triggers (D-197/D-199), the decision consumer
(D-198), the live proof (D-197.1), quarantine + notifications (D-200), the repair opening (D-201).

**The program (each arc = its own design→impl cycle):**
- **5b-1 — update/delete-rejected negatives.** S2: `UpdateStep`/`DeleteStep` models (+ identity-hash
  treatment); S3: the negative authors for "must not update/delete X"; S4: bridge projection + executor
  branches (the F6 tracker already owns created-record lifecycle; an update needs before-state capture —
  the evidence shape reserved the fields). Exit: live-proven on env 59.
- **5b-2 — multi-step positives.** Lift `bridge.py:340`'s triple gate to N-step chains with `$var`
  threading across steps (the refs machinery exists); S3 multi-step emission. Exit: a 2-create chain
  with a cross-step reference live-proven.
- **5b-3 — corpus breadth.** Generate + approve claims across the real requirement set (the auto-enqueue
  + scheduled triggers now keep their evidence fresh by themselves). Exit: the substrate decision card
  GO/NO-GOs a real release on substrate evidence alone (gating mode candidate).
- **5b-4 — the dual-run window.** Run v1 and the substrate side-by-side on the same releases (the
  composer already records both verdicts per decision — the comparison ledger is FREE); divergences
  triaged to zero or explained. Exit: N consecutive releases with substrate-verdict parity-or-better.
- **5b-5 — retire.** Re-point the remaining v1 surfaces, freeze v1 writes, archive (`pg_dump`), then the
  irreversible drop of the v1 product tables (`test_cases`, `pipeline_runs`, `run_*`, the v1 executor
  modules) — **named user GO required at the drop, archive-first, exactly the Step-5a discipline.**
- **Cross-cutting residual:** the data-path async bracketing (D-129/D-197 interim — one held connection
  per in-flight job) should land before 5b-3's volume makes it bite.

**Why charter-not-build now:** 5b-1/5b-2 reopen S2's identity-bearing representation (claim/recipe
hashes, migrations on the per-tenant store) — exactly the kind of arc the working agreement says must
not be rushed inside a batch. The charter IS the theme-#2 deliverable of this batch; 5b-1 is the next
arc to open.

---

### D-203 — 5b-1: update/delete-rejected negatives (the 2-step behavioral shape)

**The arc.** First arc of the D-202 program: teach the substrate the v1 verbs "this UPDATE must be
rejected" / "this DELETE must be rejected". The shape is necessarily two steps — provision a valid
subject record, then attempt the prohibited mutation — so the arc spans S2 (vocabulary), S3
(authoring), S4 (execution), S6 (interpretation).

**Correction to D-202's measured gap.** D-202 stated "S2 has no Update/Delete step models". Wrong
against the code: `UpdateStep` / `DeleteStep` exist in the data-recipe union (`data_recipe.py:86–98`,
D-054 vocabulary) and the D-110.1 at-most-one-`expect_rejection` validator was written
forward-compatible for them ("counted automatically without touching this check"). The S2 gap is two
optional fields, not two models. Claim identity is untouched throughout — recipes are operational
layer, excluded from `identity_hash` per SPEC §6.3.1. **Zero DB migrations in the whole arc**
(evidence rides the `s4_execution_runs` JSONB via kind-agnostic `dataclasses.asdict`; recipe bodies
are JSONB with no step-kind constraints).

**The shape (bounded, fails loud).** A behavioral negative is either the existing 1-step
create-rejected, or exactly `[CreateStep(expect_rejection=None), UpdateStep|DeleteStep(expect_rejection
set)]`. The bridge's negative projection lifts from "exactly one step" to exactly these two shapes;
every other shape keeps failing loud. Target binding is **positional**: `PlannedUpdate`/`PlannedDelete`
carry `setup_step_id` and the executor resolves the record id from the setup create — no `$ref`
machinery (refs.py untouched; the cross-step graph validation D-060 §4.7.6 aspires to still doesn't
exist; the positional contract is documented at the bridge).

**Platform fact that splits the scope.** Salesforce validation rules fire on insert and update —
**never on delete**. So update-rejected gets the full vertical (VR-grounded, formula-derived,
auto-emitted, live-proven); delete-rejected ships as **engine capability** (S2 can represent, S4 can
run, S6 can interpret a hand-authored delete-rejected recipe) but S3 routes `delete` prohibitions to
the caveated inspection path — trigger/restricted-lookup grounding is the logged residual. This also
fixes an existing semantic blur: a "cannot delete X" requirement that grounded + derived used to emit
a CREATE-rejected behavioral recipe — the wrong operation testing the wrong thing.

**Graded operation dispatch in S3 (`_author_negative`).** `modify_record`/`modify_field`: try the
update shape — setup payload = `_satisfy(ast, False)` (a non-violating create), violating changes =
`_satisfy(ast, True)`; if only the violation derives, **fall back to today's create-rejected** (no
regression — a state-only VR fires on insert too); if nothing derives, caveated. `create_duplicate`:
create-rejected as today. `delete`/`share`/`transfer_ownership`: caveated always. Two derivation
facts recorded plainly: (a) only **comparisons** derive both directions — `NOT ISPICKVAL` / `NOT
ISBLANK` raise `_Undecidable` (`verified_negative.py:166–178`), so the live proof needs a
numeric-comparison VR; (b) the 2-step recipe's payload field names are **object-qualified**
(`Opportunity.Amount`, the positive vertical's convention) — bare VR-formula names would dodge
`construct_world`'s padding-exclusion and could be silently overwritten in the `_sf_fields` merge.

**S6 must re-dispatch.** The interpreter grades "create with no assert" as the behavioral negative —
against a 2-step negative it would grade the **setup create** (which succeeded) and emit a false
`prohibition_not_enforced`. Both `interpreter.py` and `attribution.py` select the *rejection-bearing
mutation step*. Verdict vocabulary unchanged. Cause attribution for update evaluates VR formulas
against the effective state `{**setup.field_values, **field_changes}` (ISCHANGED → NonEvaluable →
the existing honest `vr_formula_indeterminate`); for delete it attaches **no cause** rather than a
fabricated one (`repair.py` already handles cause-less verdicts with verdict-level defaults).

**Exit gate.** The live update-rejected run observed **passed** through the production loop on env 59
(setup create 201 → violating PATCH 400 `FIELD_CUSTOM_VALIDATION_EXCEPTION` matched → cleanup
succeeded → S6 `prohibition_enforced` referencing the update step). Delete vertical: offline-proven
only — not the gate.

**Residuals.** (1) delete-prohibition grounding (Apex trigger / restricted lookup); (2)
ISCHANGED/PRIORVALUE update derivation (needs before/after-state modeling); (3) S1-value-set-aware
picklist alternatives for setup-underivable formulas; (4) repair.py create-specific wording; (5)
before-state capture on update evidence (fields reserved); (6) coordinator cross-step graph
validation; (7) data-path async bracketing (D-129) — the 2-step run holds the connection one extra
org round-trip.

---

### D-203.1 — The silent caveated demotion: shape-tolerant VR formula readers

**The incident (surfaced by the 5b-1 live proof).** The proof's first generation attempt emitted a
caveated inspection for a requirement whose grounding VR (`Opportunity.Amount`, `Amount > 10000`) is
plainly two-way derivable. Root cause: S1's documented JSONB contract
(`semantic/entity_attributes.py` — `ValidationRuleAttributes` with `formula_text` /
`error_message`, per D-025) is **not what the post-cutover sync stores**. The greenfield sync
(D-150+) writes the raw normalized Tooling record into `entities.attributes`
(`materialize.py` — `json.dumps(e.normalized)`); its per-type designed mappers
(`sync/presentation.py`, e.g. `formula_text ← Metadata.errorConditionFormula`) feed **only**
`semantic_text`. Both production readers of the formula — `governance_core._grounding_vr_formulas`
(the S3 verified-vs-caveated gate, D-107) and `interpretation/s1_reader.vrs_for_object` (S6 cause
attribution, D-111.1) — read only the designed `formula_text` key, so **every negative generated
since the 2026-06-04 cutover silently demoted to caveated and every cause attribution lost its
formula**. Pre-cutover proofs (D-110.3) worked because the old writer honored the designed shape.

**The fix.** S1-owned shape-tolerant extractors `vr_formula_text` / `vr_error_message`
(`semantic/entity_attributes.py`): prefer the designed key, fall back to the raw Tooling shape.
Both readers re-pointed. Unit-pinned against both shapes
(`tests/unit/semantic/test_vr_attribute_extractors.py`). Committed directly to `main` mid-proof
(surgical fix; the proof depends on it).

---

### D-204 — entities.attributes storage contract RATIFIED: raw-as-stored, extractors as the read API

**The fork D-203.1 left open:** (a) project attributes through the per-type registry schemas at
materialize time (restore the designed sparse shape), or (b) ratify the raw normalized record as the
storage contract. **Ratified: (b).**

**Why (a) loses, decisively.** SCD Type 2 + content-hash change detection
(`hash_normalized` over the normalized record; a new entity row is written ONLY when the hash
moves) means rows keep their **birth shape forever**: history rows and unchanged entities never
rewrite. A write-side projection therefore still yields a permanently two-shape store — readers
must stay tolerant regardless, so the projection adds duplication without removing tolerance.
Forcing a one-time reshape (changing the hash input) would re-version **every** entity — a
meaningless "everything changed" event that churns S8 grounding-validity / staleness and the
S1-version pinning everywhere. The raw record also preserves provenance the projection discards.

**The ratified contract.**
- `entities.attributes` stores the sync's **normalized raw Tooling record** (plus designed-shape
  rows from seeds / the pre-cutover writer, which persist in history) — never assume one shape.
- Per-type keys are read **only** through S1-owned extractors (`vr_formula_text` /
  `vr_error_message`; the family grows per need) — designed key preferred, raw fallback. Ad-hoc
  `attributes->>'<designed key>'` SQL is NOT contract.
- The `_EntityAttributes` schemas + `TIER_1_ENTITIES` registry remain as (i) the documented
  **designed-key vocabulary** the extractors prefer and (ii) the registry for detail-table lookup
  (real callers: `detail_mappers`, `query`, `derivation`). `validate_entity_attributes` has no
  production writer — retained as the schema-conformance checker its unit suite exercises.
- The sync's own `attributes->>'Id'` reads (`sync/phases.py`) read the raw shape it writes — within
  contract.

**Reader inventory at ratification (full audit).** VR formula/message — fixed (D-203.1); Flow
`entry_condition_text` (`semantic/derivation.py` TRIGGERS_ON edge property) — the designed key is
absent on post-cutover rows AND the raw record carries no equivalent (post-cutover Flow attributes
are `{Id}` only): this is a **sync-completeness gap** (the Tier-2 "parse into
flow_details.parsed_logic" deferral), not a reader-shape gap — the edge still derives; the optional
`condition_text` property is dropped. No UI/template reads attributes JSONB; Object/Field readers
use detail-table hot columns.

**Residuals.** (1) Flow entry-condition capture (Tier-2 sync); (2) extractor-family growth as new
per-type keys gain readers.

---

### D-203.2 — 5b-1 CLOSED: the update-rejected negative live-proven through the production loop

2026-06-10, env 59, S4 job 9, run `3363f1e4-0ced-4c93-b56e-66979931c5d6` (3.1 s): the approved
prohibition claim `71583230` (operation `modify_field`, grounded on the user-authored
`Opportunity.Amount` VR, `Amount > 10000`) executed the **2-step behavioral negative** live —
**setup create** HTTP 201 (`006Ip000003Kdz9IAC`; semantic `Amount=10000` + padded
`{Name, StageName: "Prospecting", CloseDate}` — the picklist filler live-exercised) → **prohibited
update** PATCH `{Amount: 10001}` → HTTP 400 `FIELD_CUSTOM_VALIDATION_EXCEPTION`
("Amount should be greater than 10000"), `matched=true` → **passed** → teardown delete
Salesforce-confirmed → `s4_created_records` audit row → S6 verdict **`prohibition_enforced`**
referencing the `update-violating` step. The claim's recipe history honestly shows the journey:
v1 (caveated inspection, authored under the D-203.1 formula blindness) → v2 (the 2-step shape,
re-authored via the Coordinator's recipe re-version after the fix).

**Four latent defects flushed out en route — none in the 5b-1 code itself** (each root-caused,
regression-tested, deployed): D-203.1 (formula readers blind to the post-cutover attribute shape —
every negative silently caveated since 2026-06-04), D-204 (the attributes storage contract
ratified), D-204.1 (`isActive: null` read as inactive — 0/360 picklist fields linked to their value
sets), D-204.2 (picklist padding walked containment edges that by design do not exist — only its
stubs ever satisfied it). The arc is the theme-#1 thesis demonstrated: only live runs against a
real org surface this class of defect.

**5b-1 exit gate met.** Next arc per D-202: 5b-2 (multi-step positives). Open residuals carried:
single-quoted formula literals (the org's other three Opportunity rules stay caveated until the
parser learns them); the D-203 residual list; the sync attributes projection (ratified-raw, D-204).

---

### D-205 — 5b-2: multi-step positives (the N-create chain with cross-step references)

**The arc.** Second arc of the D-202 program: lift the positive data vertical from the exact triple
(create → read → assert, D-115) to **N-create chains** where a later create's field values reference
an earlier create's record (`"AccountId": "$create-account.id"`). Exit gate per the charter: a
2-create chain with a cross-step reference live-proven on env 59.

**Correction to D-202's charter (the S3 leg).** The charter said "S3 multi-step emission". Grounded
against the code, **no emission is buildable yet**: every grounded claim kind is single-object —
value-claim grounds field-on-object via BELONGS_TO; state-transition / automation-effect (the kinds
that semantically span two records) are EMISSION-DEFERRED **and** their groundings name a single
subject (a VR edge / a Flow edge — S1 does not model "this Flow affects Objects A and B"). For
ordinary value-claims an explicit chain would duplicate F6.2 (required parents are provisioned
invisibly as padding — the k16 boundary says the recipe never encodes operational scaffolding). So
5b-2 ships **engine capability** (S2 can already represent N-create bodies — the at-most-one
expect_rejection validator is the only step constraint; S4 learns to run them), with S3 multi-step
emission **gated on multi-object grounding** (S1 automation-dependency modeling — the residual).
Same honest narrowing as 5b-1's delete leg (D-203).

**The shape.** Bridge `_project_positive` partitions ``[CreateStep × N (no expect_rejection),
ReadStep, AssertStep]`` (N ≥ 1; everything else keeps failing loud) → ``(PlannedCreate × N,
PlannedDataRead, PlannedAssertion)``. The executor's positive path becomes a **loop**: per create —
construct-world (per-create semantic fields), resolve ``$step.attr`` references in the create's
field values against the accumulated state (NEW ``refs.resolve_field_value_refs`` — string values
only, the SOQL resolver's regex + fail-loud discipline; resolution after the padding merge, before
``_sf_fields`` bare-ification), create, ``tracker.record``, ``state[step_id] = {"id": …}``. A
mid-chain rejection grades with THAT create's semantic-vs-padding disambiguation (D-115.2) and tears
down everything already built. Teardown stays single + reverse-order (the tracker already handles
parents-then-children across N creates); **cleanup attribution by record id** — teardown's
CleanupRecords are mapped back to each create's evidence via ``record_id`` (no index arithmetic).
The read-back + ground are unchanged (state now carries every create's id for SOQL refs).

**Zero changes**: S2 recipe model, the plan-step union, the coordinator, provisioning, world.
Zero migrations.

**Live proof shape (genuinely additive, not padding-redundant).** Contact's Account lookup is
OPTIONAL in Salesforce — padding never builds it — so an explicit
``create Account → create Contact(AccountId="$create-account.id", …) → read → assert`` chain
exercises a real recipe-authored cross-step reference. Procedure mirrors D-203.2: requirement →
generate (the current single-create triple) → re-version the recipe to the 2-create chain via the
Coordinator (the recipe-update path, recipe v1 → v2 history) → approve → the deployed worker runs it
live → evidence shows the Contact created with the resolved Account id + reverse-order cleanup of
both records.

**Residuals.** (1) S3 multi-step emission — gated on multi-object grounding (S1 automation
dependencies); (2) chains beyond create (read-between-creates, multi-assert) — deferred until a
consumer; (3) non-string ref values (refs inside lists/nested dicts) — strings only in v1.

---

### D-205.1 — 5b-2 CLOSED: the 2-create chain live-proven through the production loop

2026-06-10, env 59, S4 job 10, run `db93ac3a-9910-48fc-a359-61e570b5eaae` (8.1 s): the approved
value-claim `dd75ef7a` (Contact.Email, req-282) executed the **2-create chain** live — **create
Account** `001Ip00000JQhQtIAL` (`{Name: "PQA D205 Chain"}`) → **create Contact**
`003Ip00000MlY3PIAV` with the **live-resolved cross-step reference**
(`AccountId: "$create-account.id"` posted as the real Account id) + padding (`LastName`) →
read-back resolved `$create-contact.id`, returned the asserted email → assert held → **passed** →
**both records torn down reverse-order**, each CleanupRecord attributed to its own create's
evidence by record id → S6 verdict **`value_persisted`**. Recipe history shows the journey: v1
(the S3-emitted single-create triple) → v2 (the chain, via the Coordinator's re-version path);
claim history v1 → v2 records the LLM's `"<email>"` placeholder corrected to the requirement's
literal (a generation-quality finding: the verbatim-value instruction lost to a placeholder —
logged, not blocking).

**5b-2 exit gate met.** Residuals carried: S3 multi-step emission stays gated on multi-object
grounding (D-205); S6's positive attribution PROSE names the FIRST create's sobject — for a chain
the asserted record is the read/assert subject (wording only; verdict + grading correct);
read-between-creates / multi-assert shapes deferred until a consumer. Next per D-202: **5b-3**
(corpus breadth — generate + approve claims across the real requirement set; the substrate decision
card GO/NO-GOs a real release on substrate evidence alone), with the data-path async bracketing
(D-129) due before 5b-3's volume.

### D-206 — Triage-first test pages: read-time presentation over the substrate surfaces

**Context.** AK chose **manual approval** for the 5b-3 corpus arc: every S3-generated draft is
approved by a human in the UI (approval auto-queues first runs per D-199). The existing claim pages
speak substrate vocabulary (`prohibition-claim`, `vr_formula_indeterminate`, raw asserted-truth
JSON) — fine for engineers, unusable as an approval/triage surface. Before 5b-3 floods the system
with drafts, the pages must say in plain words **what each test does**, **how honestly it tests it**,
and **what each run found**. Persona ratified: AK + testers (triage-optimized), not BA-stakeholder
prose (that layer stays deferred).

**Decisions.**
1. **Presentation is computed at READ time, never stored.** New pure module
   `primeqa/intelligence/claim_presentation.py`: `claim_title(claim_kind, asserted_truth)` (per-kind
   sentence templates — prohibition → "Rejects <operation words> on <target>", value-claim →
   '<field> saves as "<value>"', existence/property/capability/layout/metadata-relationship;
   humanized-kind fallback; never raises), `claim_depth(recipe_kinds)` (**behavioral** if any
   data-recipe else **configuration-check** — the honesty badge), `verdict_plain(verdict, outcome)`
   (full S6 vocabulary → plain sentences, raw verdict preserved as tooltip). Storing prose on SCD
   rows was rejected: stored presentation goes stale the moment templates improve (the S1-attributes
   staleness lesson, D-204).
2. **List enrichment via lateral joins, not N+1.** `_list_claims` gains `asserted_truth`, current
   recipe kinds (LEFT JOIN LATERAL array_agg over `test_recipes` valid_to IS NULL), and the
   requirement key (LATERAL over `test_requirement_links` link_kind='generated_from', newest first)
   + a `status` filter param. Rows carry `title` / `depth` / `requirement_key`.
3. **Approval inbox at `/claims/inbox`** (drafts only, per-row Approve with explainer that approval
   queues first runs automatically). `/claims` shows an "N awaiting approval" chip. Approve accepts
   a `next` redirect constrained to a `/claims` prefix (open-redirect-safe).
4. **Detail page leads with the sentence title + depth badge**; run history lines render
   `verdict_plain` (raw S6 verdict as tooltip); asserted truth / semantic conditions / recipes fold
   behind a `<details>` "Technical details" toggle — one click away, never deleted.
5. **Dedup transparency on the requirement page.** New console read
   `read_latest_generation_note(tenant_id, requirement_key)` surfaces
   `generation_outcomes.equivalent_existing` as a banner — "the last generation matched an existing
   test, no new test was created" — so a deduped regenerate (the SQ-211 confusion) no longer looks
   like a silent no-op. Test-plan rows on the requirement page get titles + depth badges too.

**Sequencing.** Phase A of the ratified 4-phase plan: D-206 (this) → D-207 multi-claim generation →
D-208 5b-3 corpus breadth (AK-gated approvals) → D-209 5b-4 dual-run window. v2 runtime work →
direct to main.

**Residuals.** (1) BA/stakeholder prose layer (story-style) — deferred until the persona shows up;
(2) title templates cover the 7 emitted kinds + fallback — new kinds land with the fallback until
templated; (3) inbox bulk-approve — deferred until single-approve friction is observed.

### D-207 — Multi-claim generation: one requirement -> N intents -> N claims, one outcome

**Context.** Every layer of S3 generation today carries exactly one test intent per requirement:
the `propose_semantic_intent` schema takes one `intent_descriptor`, `RequirementState` stashes one
grounding, `finalize_outcome` authors one bundle, the persister writes one claim. A real requirement
("the field must save; the org must reject X; the layout must show Y") implies several distinct
testable intents — v1's engine produced 3-6 TCs per requirement for exactly this reason. AK ratified
**multiple claims per requirement** for the 5b-3 corpus arc. Verified against the code: the protocol
shape is ALREADY plural (`GenerationOutcome.claims_written` / `recipes_written` /
`equivalent_existing` are lists, today always length 1) — the 1:1 lives only in the propose schema,
the singular state stashes, finalize's first-non-None pick, and the persister's single-bundle write.

**Decisions.**
1. **One propose call carries ALL intents.** `propose_semantic_intent` gains `intent_descriptors`
   (array, 1..6, each with its own verbatim `requirement_excerpt` per Guardrail-3). Chosen over a
   propose-N-times loop: per-phase tool forcing stays deterministic (no ambiguous tool_choice),
   one decomposition is one turn, and the substrate sees the full claimed coverage at once.
   Back-compat: the singular `intent_descriptor` stays accepted at Layer A as a 1-element array —
   pinned v4 replays are untouched.
2. **D-072 stands: one outcome per requirement.** The N claims ride the existing list fields.
   **Zero DB migrations.**
3. **Partial grounding is a draft, not a refusal.** Each descriptor resolves independently through
   the existing per-archetype machinery (extracted `_resolve_one`); a failed intent becomes a
   dismissal recorded in `attempted_interpretation` under its own path id (`c0..c{n-1}` — ending
   today's hardcoded `"c0"` everywhere); the **refusal outcome fires only when zero intents
   ground** (route the first directive; carrying one RefusalEntry per failed intent via D-073
   multiplicity is a residual refinement). The emittability gate (D-105.2) applies per intent.
4. **`RequirementState` accumulates an ordered `groundings` list**; the singular
   `grounded_emission` / `grounded_negative` / `grounded_positive` stashes retire in favor of
   appends. `finalize_outcome` authors one `EmissionBundle` per grounding (`author_emission`
   unchanged — it already dispatches on the grounding type); `OutcomeVerdict` / `RequirementResult`
   carry `emissions: list`.
5. **Outcome-level epistemic posture aggregates conservatively**: `admissibility_layer` = LAYER_2
   only when ALL bundles verified, else LAYER_1; `caveat_required = any(bundle.caveat_required)`;
   `caveat_kind` = the first caveated bundle's kind. Per-claim posture on the outcome row is a
   residual (each claim's own recipe still tells the truth per-claim).
6. **The persister loops bundles in the same atomic transaction**: per-bundle identity-hash dedup
   (a deduped intent appends to `equivalent_existing` and mints no recipe; the others still
   write), `generated_from` requirement link per claim (idempotent PK as today).
7. **SELECT stays dormant.** Decomposition still returns <=1 grounded candidate per intent, so
   AWAIT_SELECTION cannot trigger; intent-scoped selection is deliberately out of scope.
8. **`check_refs_exist` iterates descriptors** — any unresolved ref rejects the whole call as an
   operational correction (Layer A semantics; the model fixes that descriptor and retries).
9. **Prompt freeze `generation@v5`**: instruct full-coverage decomposition — the positive
   behavior, one negative per prohibition/condition, configuration checks — each as its own
   descriptor with its own verbatim excerpt; registry ritual (versions file, RECORDED_HASHES,
   CURRENT bump, hash-guard test). `_present_candidates` reply enriched to per-intent
   grounded/dismissed status so the model's emit turn sees what survived.

**Out of scope.** Jobs/queue/consumer (already multiplicity-agnostic); S4/S6 (consume claims
one at a time regardless of sibling count); the D-206 dedup banner keeps its boolean read
(per-intent dedup counts are a residual).

**Exit gate.** Generation suites green per-suite, plus a **live multi-claim generation on env 59**:
one requirement implying a positive + a prohibition produces one outcome with >=2 claims written,
both drafts landing in the D-206 approval inbox.

### D-207.1 — Multi-claim generation CLOSED: 2 claims from one requirement, live through the production loop

2026-06-11, env 59, S3 job 10 (s1 seq 52, prompt **generation@v5**, both tool calls operational
first-try): manual requirement **req-283** ("When an Opportunity is created, its Amount must save
as 5000. The org must reject any edit that raises an Opportunity's Amount above 10,000.") produced
**one draft outcome carrying TWO claims** — the live LLM proposed the `intent_descriptors` array
unprompted-by-script and the substrate grounded both:

- **value-claim** `95a3b823` (NEW, v1): `Opportunity.Amount saves as "5000"` — the requirement's
  literal carried verbatim (the D-205.1 placeholder regression did NOT recur); recipe minted;
  draft → the D-206 approval inbox, title + behavioral badge rendering correctly.
- **prohibition-claim** `71583230` — **per-bundle dedup demonstrated live**: the second intent's
  identity hash matched the D-203 approved update-rejected test; `equivalent_existing` records it,
  no duplicate minted, no second recipe, and the `generated_from` link to req-283 was still
  written — the approved test now correctly covers both requirements. `claims_written` carries
  both refs; `recipes_written` exactly the fresh one.

Outcome posture aggregated as designed (layer_1, no caveat). The D-206 surfaces close the loop:
inbox shows 1 draft awaiting AK; the requirement page shows both tests + the deduped-generation
banner (`read_latest_generation_note` → deduped=true).

**D-207 exit gate met** (suites green per-suite: 232 generation + 1503 cross-gate with
representation; live multi-claim outcome observed). Residuals carried: per-claim epistemic posture
on the outcome row (outcome-level is the conservative aggregate); refusal multiplicity (one
RefusalEntry per failed intent — first-directive-routes today); the live `claims_count >= 2`
envelope adjudicates on the periodic ANTHROPIC_API_KEY run, not PR-gating; SELECT stays dormant.
Next per the ratified plan: **D-208 (5b-3 corpus breadth)** — bulk-generate across the imported
requirement set, drafts to AK's inbox, AK-gated approvals queue the live runs.

### D-208 — 5b-3 corpus breadth: sweep the live corpus through v5 generation, AK-gated approvals

**Context.** The D-202 5b-3 charter: generate + approve claims across the real requirement set so
the substrate decision card can GO/NO-GO a real release on substrate evidence alone. Ratified
posture (2026-06-11 AskUserQuestion): **AK approves every draft manually** in the D-206 inbox;
approval auto-queues first runs on auto-verify envs (D-199). Both prerequisites landed this arc:
D-206 (the approval surface) and D-207 (multi-claim generation, live-proven on req-283).

**Premise correction (code is ground truth).** The expected "SQ-* set" is not in the store: the
live corpus is **4 requirements** (SQ-211 the only Jira import; req-280/282/283 manual), 3 already
carrying claims. Real breadth needs AK importing more Jira tickets — his UI action, not mine.
5b-3 proceeds on the available corpus; the breadth gate widens as imports land.

**Decisions.**
1. **The sweep is pure ops — zero code.** Enqueue an S3 generation job at the current S1 seq for
   every active requirement whose latest outcome predates generation@v5 (SQ-211, req-280,
   req-282; req-283 ran in D-207.1). Job idempotency on (key, s1_seq) makes re-sweeps safe;
   per-bundle identity dedup (proven live, D-207.1) means existing claims re-link rather than
   duplicate, and only genuinely NEW intents mint drafts into the inbox.
2. **D-129 deferral stands for this volume** (verified at the source): the S4 consumer claims ONE
   job per tenant per tick (SKIP LOCKED) — a burst of approvals queues jobs that drain serially;
   no async bracketing needed at 4-requirement scale. Re-assess at real corpus volume.
3. **Approvals stay human.** I sweep generation; AK approves in `/claims/inbox`; D-199 queues the
   live runs; S6 verdicts land per run. No auto-approve anywhere.

**Exit gate.** Every active requirement has a generation outcome at the current S1 seq under v5;
the inbox holds the resulting drafts; AK's approved claims show live env-59 runs with S6 verdicts.
Decision-card readiness on a constructed release is D-209's gate, not this one.

### D-209 — Formula parser: single-quoted string literals (the 'Closed Won' gap)

**Context.** Salesforce formula syntax accepts BOTH quote styles for text literals; admin-written
validation rules overwhelmingly use single quotes (`ISPICKVAL(StageName, 'Closed Won')`). The D-107
tokenizer (`primeqa/semantic/formula/parser.py`) opens a string only on `"` — a `'` falls to the
unexpected-character branch → `NotParsed` → the D-107 verified-vs-caveated gate marks every such
negative **caveated** (configuration-check badge) even when the rule is trivially derivable. Logged
as an open residual since D-203; on the live env-59 org it currently blinds the deriver to
`Opportunity.Contract_Value_Required_On_Closed_Won` and `Lost_Reason_Required_On_Closed_Lost`.
Build 2 of the 2026-06-11 ratified list.

**Decision.** Tokenizer-only fix: either quote character opens a string literal terminated by the
SAME character, with the existing backslash-escape handling. No grammar, node, deriver, or
canonicalization change — a single-quoted literal produces a byte-identical `Literal(value,
"string")` to its double-quoted twin, so claim identity hashes are unaffected (re-generation on
already-claimed rules dedups; only the derivability verdict improves). Fail-loud posture preserved
(unterminated strings still `NotParsed`).

**Effect.** Single-quoted comparison/ISPICKVAL formulas become parseable → their negatives clear
the D-107 gate as verified Layer-2 (behavioral) instead of caveated, wherever a violating value
derives. No migration; no API change.

---

---