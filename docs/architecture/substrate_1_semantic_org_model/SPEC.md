# Substrate 1 — Semantic Org Model — SPEC

**Status:** Phase 2 complete. Phase 3 (operational details: refresh scheduling, observability, tenant onboarding) deferred.

**Last substantive update:** 2026-04-25 (Phase 2 — storage, data model, diff engine, query interface)

**Supersedes:** the flat "metadata context" pattern in current generation code.

---

## Purpose

This spec defines the Semantic Org Model: PrimeQA's per-tenant, queryable representation of a Salesforce org's structure, configuration, behavior, and change history.

Design has proceeded in two phases:
- **Phase 1 (locked):** Conceptual shape — what S1 is, what it answers, lifecycle, multi-tenancy, versioning, sync strategy, tiered capability model, derived edges by archetype, cross-tenant boundary.
- **Phase 2 (locked, this commit):** Concrete data model, storage backend, query interface, diff engine.
- **Phase 3 (deferred):** Operational details — refresh scheduling, observability, tenant onboarding, schema migrations.

---

## 1. What Substrate 1 IS

A continuously evolving, per-tenant graph that represents a Salesforce org — enriched with derived relationships that capture how entities depend on each other.

**Concretely:**
- One model per tenant, authoritative
- Event-sourced with logical version checkpoints
- Behavior graph with derived edges (computed at sync time)
- Tiered capability: TIER_1 (structure + validation parsing), TIER_2 (behavior interpretation), TIER_3 (deep semantics)
- Consumers query stable logical versions, not live state

**See also:** BACKGROUND.md for why this substrate exists and what's NOT in scope.

---

## 2. What the Model Must Answer

(See Phase 1 spec sections 2.1-2.6 for full categorization by consuming substrate.)

Summary: data behavior reasoning (S3, S4), explainability via diff (S6), impact analysis (S8), pattern derivation (S5), conversational query (S7).

---

## 3. Lifecycle

(See Phase 1 spec sections 3.1-3.4.)

Summary: per-tenant onboarding creates a new schema; steady state runs background + on-demand sync; reads happen against logical version snapshots; offboarding drops the schema.

---

## 4. Versioning

### 4.1 Event-sourced model
The model is fundamentally an append-only change log. Each event records timestamp, logical version (if any), entity affected, change type, before/after values, source.

The "current model" is a materialized view computed from the event stream. Historical versions are reconstructable from the stream.

### 4.2 Logical version markers — version_seq + version_name
Per D-016, versions have two identifiers:
- `version_seq BIGINT` — monotonically increasing per tenant. Used in queries.
- `version_name VARCHAR(100)` — human-readable. Used in logs and UI.

Names follow `<type>-<timestamp>-<sequence>` (e.g., `deploy-20260425-001`, `manual-20260425-amjad-pre-demo`).

Logical versions are coarse-grained — created at deploys, sandbox refreshes, manual checkpoints, and scheduled milestones. Not every event creates a logical version.

### 4.3 Consumer binding
Every test generated, test executed, and result produced records the `version_seq` it was based on. This is the foundation of explainability.

### 4.4 Storage
Stored in `logical_versions` table (see §6.1). `version_seq` from this table is referenced as foreign key from `entities.valid_from_seq`, `entities.valid_to_seq`, `edges.valid_from_seq`, `edges.valid_to_seq`, `change_log.version_seq`.

---

## 5. Storage Backend (Phase 2)

### 5.1 Choice: Postgres with graph-friendly design

Per D-014. Storage is PostgreSQL. The model is structured as a true graph using two canonical patterns: an `entities` table holding all nodes, and an `edges` table holding all derived relationships uniformly.

### 5.2 Three commitments (must be enforced)

1. **Edges are canonical.** Every derived relationship lives in the `edges` table. New edge types add `edge_type` values, never new tables.
2. **Traversal is SQL-only.** Consumers never pull entities into application memory to traverse them. Recursive CTEs handle traversal at the database layer.
3. **Optimization stays in Postgres.** Hot queries get materialized views or denormalized columns within Postgres. No in-memory caches at application layer.

### 5.3 Per-tenant isolation: schema-per-tenant

Per D-015. One database, one schema per tenant (`tenant_<integer_id>`), plus a `shared` schema for cross-tenant control-plane data.

```
Database: primeqa
├── Schema: tenant_42
│   ├── entities, edges, change_log, logical_versions
│   ├── object_details, field_details, ... (10 detail tables)
│   ├── validation_rule_field_refs
│   └── effective_field_permissions (materialized view)
├── Schema: tenant_43
│   └── (same structure)
└── Schema: shared
    ├── tenants, users, billing, system_metadata
    └── (control plane only)
```

### 5.4 Connection resolver

Canonical access function:

```python
@contextmanager
def get_tenant_connection(tenant_id: int):
    schema_name = _resolve_schema_name(tenant_id)
    conn = engine.connect()
    try:
        trans = conn.begin()
        # Both settings transaction-scoped (auto-reset on commit/rollback)
        conn.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
        conn.execute(text('SET LOCAL app.tenant_id = :tid'), {"tid": str(tenant_id)})
        yield conn
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()
```

**Operational discipline:**
- `SET LOCAL` (not `SET`) for transaction-scoped settings
- Connection pool checkin hook resets search_path defensively
- Development environment validates search_path took effect (catches PgBouncer transaction-mode issues)
- Workers, scripts, scheduled tasks pass `tenant_id` explicitly — no ambient context
- Admin operations have dedicated entry points: `admin_iterate_all_tenants()`, `admin_run_in_shared_schema()`

### 5.5 Migration framework

Alembic configured with `version_table_schema` set to the tenant's schema. Each tenant has its own `alembic_version` table tracking migration state.

Migrations run in two modes:
- **Sequential:** Iterate tenants, run migration on each. Simple, slow at scale.
- **Parallel:** Bounded concurrency (e.g., 4 tenants at a time). Faster, requires care.

Migrations are idempotent. Code is defensive about partial migration states (tenants 1-72 migrated, 73-1500 not yet).

---

## 6. Foundation Tables (Phase 2)

### 6.1 logical_versions

```sql
CREATE TABLE logical_versions (
    version_seq BIGSERIAL PRIMARY KEY,
    version_name VARCHAR(100) NOT NULL UNIQUE,
    version_type VARCHAR(40) NOT NULL,   -- 'genesis', 'deploy_detected', 'sandbox_refresh', 'manual_checkpoint', 'scheduled_milestone'
    description TEXT,
    parent_version_seq BIGINT REFERENCES logical_versions(version_seq),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by_sync_run_id UUID
);

CREATE INDEX idx_versions_type_created 
    ON logical_versions(version_type, created_at DESC);
```

### 6.2 entities

```sql
CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type VARCHAR(40) NOT NULL,
    sf_id VARCHAR(18),
    sf_api_name VARCHAR(255),
    display_name VARCHAR(255),
    attributes JSONB NOT NULL DEFAULT '{}',
    
    valid_from_seq BIGINT NOT NULL REFERENCES logical_versions(version_seq),
    valid_to_seq BIGINT REFERENCES logical_versions(version_seq),
    
    tenant_id INT NOT NULL DEFAULT current_setting('app.tenant_id')::INT,
    
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_synced_at TIMESTAMP NOT NULL,
    
    CONSTRAINT entities_validity_range CHECK (valid_to_seq IS NULL OR valid_to_seq > valid_from_seq),
    CONSTRAINT entities_tenant_assertion CHECK (tenant_id = current_setting('app.tenant_id')::INT),
    CONSTRAINT entities_attributes_is_object CHECK (jsonb_typeof(attributes) = 'object')
);

-- Current-state indexes
CREATE INDEX idx_entities_current_type ON entities(entity_type) WHERE valid_to_seq IS NULL;
CREATE INDEX idx_entities_current_sf_id ON entities(sf_id) WHERE valid_to_seq IS NULL;
CREATE INDEX idx_entities_current_api_name ON entities(entity_type, sf_api_name) WHERE valid_to_seq IS NULL;

-- As-of-version indexes
CREATE INDEX idx_entities_version_range ON entities(valid_from_seq, valid_to_seq);
CREATE INDEX idx_entities_type_version ON entities(entity_type, valid_from_seq);

-- Uniqueness invariant
CREATE UNIQUE INDEX idx_entities_unique_active 
    ON entities(sf_id) WHERE valid_to_seq IS NULL AND sf_id IS NOT NULL;
```

### 6.3 edges

```sql
CREATE TABLE edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_entity_id UUID NOT NULL REFERENCES entities(id),
    target_entity_id UUID NOT NULL REFERENCES entities(id),
    edge_type VARCHAR(60) NOT NULL,
    edge_category VARCHAR(20) NOT NULL CHECK (
        edge_category IN ('STRUCTURAL', 'CONFIG', 'PERMISSION', 'BEHAVIOR')
    ),
    properties JSONB NOT NULL DEFAULT '{}',
    
    valid_from_seq BIGINT NOT NULL REFERENCES logical_versions(version_seq),
    valid_to_seq BIGINT REFERENCES logical_versions(version_seq),
    
    tenant_id INT NOT NULL DEFAULT current_setting('app.tenant_id')::INT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    CONSTRAINT edges_validity_range CHECK (valid_to_seq IS NULL OR valid_to_seq > valid_from_seq),
    CONSTRAINT edges_no_self_loop CHECK (source_entity_id != target_entity_id),
    CONSTRAINT edges_tenant_assertion CHECK (tenant_id = current_setting('app.tenant_id')::INT),
    CONSTRAINT edges_properties_is_object CHECK (jsonb_typeof(properties) = 'object')
);

-- Current-state traversal
CREATE INDEX idx_edges_current_source ON edges(source_entity_id, edge_type) WHERE valid_to_seq IS NULL;
CREATE INDEX idx_edges_current_target ON edges(target_entity_id, edge_type) WHERE valid_to_seq IS NULL;
CREATE INDEX idx_edges_current_type ON edges(edge_type) WHERE valid_to_seq IS NULL;
CREATE INDEX idx_edges_current_category ON edges(edge_category, source_entity_id) WHERE valid_to_seq IS NULL;

-- As-of-version traversal
CREATE INDEX idx_edges_source_version ON edges(source_entity_id, edge_type, valid_from_seq);
CREATE INDEX idx_edges_target_version ON edges(target_entity_id, edge_type, valid_from_seq);

-- Containment uniqueness (only for STRUCTURAL containment edges)
CREATE UNIQUE INDEX idx_edges_unique_containment 
    ON edges(source_entity_id, edge_type, valid_from_seq) 
    WHERE edge_category = 'STRUCTURAL';
```

### 6.4 change_log

```sql
CREATE TABLE change_log (
    id BIGSERIAL PRIMARY KEY,
    change_type VARCHAR(30) NOT NULL,
    -- Values: entity_created, entity_field_modified, entity_attributes_modified, 
    --         entity_deleted, edge_created, edge_properties_modified, edge_deleted,
    --         detail_field_modified, detail_added, detail_removed
    
    target_table VARCHAR(20) NOT NULL,
    target_id UUID NOT NULL,
    before_state JSONB,
    after_state JSONB,
    changed_field_names TEXT[],
    
    version_seq BIGINT NOT NULL REFERENCES logical_versions(version_seq),
    sync_run_id UUID,
    
    tenant_id INT NOT NULL DEFAULT current_setting('app.tenant_id')::INT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    CONSTRAINT change_log_tenant_assertion CHECK (tenant_id = current_setting('app.tenant_id')::INT)
);

CREATE INDEX idx_change_log_target ON change_log(target_id, version_seq);
CREATE INDEX idx_change_log_version ON change_log(version_seq);
CREATE INDEX idx_change_log_sync_run ON change_log(sync_run_id);
CREATE INDEX idx_change_log_type_version ON change_log(change_type, version_seq);
CREATE INDEX idx_change_log_field_names ON change_log USING GIN(changed_field_names);
```

---

## 7. Containment vs Edges (Phase 2)

Per D-017. Containment is column on detail table (authoritative). Edge is derived projection.

**Rule:** Containment edges (STRUCTURAL category) are auto-generated at write time from columns. Never independently written. Prevents divergence between two representations of same fact.

Applies to:
- Field → Object (field_details.object_entity_id)
- RecordType → Object (record_type_details.object_entity_id)
- Layout → Object (layout_details.object_entity_id)
- ValidationRule → Object (validation_rule_details.object_entity_id)
- Flow → Object trigger (flow_details.triggers_on_object_entity_id)
- User → Profile (user_details.profile_entity_id)
- Field → Object via lookup (field_details.references_object_entity_id) → HAS_RELATIONSHIP_TO edge

**Layout structure:** Sections are NOT entities. `Layout INCLUDES_FIELD Field` edges with structured properties (section_name, section_order, row, column, is_required, is_readonly).

---

## 8. Edge Types (Phase 2)

Per D-019. 14 edge types in Tier 1, registered as code-level constant `TIER_1_EDGES`:

```python
TIER_1_EDGES = {
    # STRUCTURAL (2)
    'BELONGS_TO': {category: 'STRUCTURAL', derived_from_column: True, ...},
    'HAS_RELATIONSHIP_TO': {category: 'STRUCTURAL', derived_from_column: True, ...},
    
    # CONFIG (4)
    'INCLUDES_FIELD': {category: 'CONFIG', derived_from_column: False, ...},
    'ASSIGNED_TO_PROFILE_RECORDTYPE': {category: 'CONFIG', derived_from_column: False, ...},
    'CONSTRAINS_PICKLIST_VALUES': {category: 'CONFIG', derived_from_column: True, ...},
    'HAS_PICKLIST_VALUES': {category: 'CONFIG', derived_from_column: True, ...},
    
    # PERMISSION (5)
    'GRANTS_OBJECT_ACCESS': {category: 'PERMISSION', derived_from_column: False, ...},
    'GRANTS_FIELD_ACCESS': {category: 'PERMISSION', derived_from_column: False, ...},
    'INHERITS_PERMISSION_SET': {category: 'PERMISSION', derived_from_column: False, ...},
    'HAS_PROFILE': {category: 'PERMISSION', derived_from_column: True, ...},
    'HAS_PERMISSION_SET': {category: 'PERMISSION', derived_from_column: False, ...},
    
    # BEHAVIOR (3)
    'TRIGGERS_ON': {category: 'BEHAVIOR', derived_from_column: True, ...},
    'APPLIES_TO': {category: 'BEHAVIOR', derived_from_column: True, ...},
    'REFERENCES': {category: 'BEHAVIOR', derived_from_column: True, ...},
}
```

8 of 14 edges are derived from columns. 6 are independently written. Properties schemas are application-layer Pydantic models per edge type.

(See DECISIONS_LOG D-019 for full edge type registry with source/target type constraints.)

---

## 9. Entity Detail Tables (Phase 2)

Per D-018. 10 Tier 1 entity types with corresponding detail tables. Detail tables do NOT carry `tenant_id` (only canonical tables do).

**Tables:**
- `object_details`
- `field_details` (with hot containment column `object_entity_id`)
- `record_type_details`
- `layout_details`
- `validation_rule_details` + `validation_rule_field_refs` (hot reference table)
- `flow_details` (Tier 1: existence + trigger; Tier 2 columns reserved nullable)
- `profile_details`
- `permission_set_details`
- `user_details`
- `picklist_value_details`

(See substrate's IMPLEMENTATION.md or migration files for full DDL.)

---

## 10. Permission Modeling (Phase 2)

Per D-020.

**Storage:** Permission grants as edges with property matrix. One edge per (Profile/PermissionSet, Field) with `properties: {can_read: bool, can_edit: bool}`.

**Effective permissions materialized:**

```sql
CREATE MATERIALIZED VIEW effective_field_permissions AS
SELECT 
    u.entity_id AS user_entity_id,
    f.entity_id AS field_entity_id,
    bool_or(g.properties->>'can_read' = 'true') AS can_read,
    bool_or(g.properties->>'can_edit' = 'true') AS can_edit
FROM ... (full view definition in migration);

CREATE UNIQUE INDEX idx_effective_perms_user_field 
    ON effective_field_permissions(user_entity_id, field_entity_id);
```

Refreshed via `REFRESH MATERIALIZED VIEW CONCURRENTLY` after sync. Reflects "current state as of last refresh." For as-of-version queries, consumers query underlying tables.

**Sync frequency:** Operational data (User, HAS_PERMISSION_SET edges) syncs at higher frequency than structural metadata. Sync is entity-scoped.

---

## 11. Diff Engine (Phase 2)

Per D-021. Three query primitives:

```python
@dataclass
class TraversalSpec:
    direction: Literal['inbound', 'outbound', 'both', 'none'] = 'none'
    max_depth: int = 1
    edge_categories: list[str] | None = None
    edge_types: list[str] | None = None

@dataclass
class Change:
    change_id: int
    version_seq: int
    change_type: str
    target_table: str
    target_id: UUID
    before_state: dict | None
    after_state: dict | None
    changed_field_names: list[str]
    sync_run_id: UUID | None
    created_at: datetime

class DiffEngine:
    def diff_for_entities(
        self,
        entity_ids: list[UUID],
        from_seq: int,
        to_seq: int,
        traversal: TraversalSpec | None = None,
    ) -> DiffResult:
        """Changes affecting these entities between versions."""
    
    def diff_impact(
        self,
        changed_entity_id: UUID,
        at_seq: int,
        direction: Literal['inbound', 'outbound', 'both'] = 'inbound',
        max_depth: int = 3,
        edge_categories: list[str] = ...,  # REQUIRED
    ) -> ImpactResult:
        """Entities affected by a change to this entity."""
    
    def diff_window(
        self,
        from_seq: int,
        to_seq: int,
        entity_types: list[str] | None = None,
        change_types: list[str] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[Change]:
        """All changes between two versions, paginated, deterministically ordered."""
```

**Behavior commitments:**
- Direction control on entity-scoped and impact diffs
- Edge category filter required (not optional) on impact diff
- Raw structured Change objects (no interpretation layer)
- Deterministic ordering across all queries
- Purged versions raise VersionNotFoundError, no fallback

**Performance targets:**
- Entity-scoped diff for 10 entities × 1000 version_seq range: <100ms
- Impact diff at depth 3, 50K entities: <500ms
- Time-window diff returning 1000 changes: <200ms

---

## 12. Query Interface (Phase 2)

Per D-022. Minimal contract enforcing invariants. Full ergonomics emerge during Substrate 3 design.

**Principles:**
1. Version-aware access only (every primitive takes version context)
2. Centralized edge traversal (no consumer writes recursive CTEs)
3. Explicit edge filtering (`edge_categories` required)
4. Explicit direction (`inbound | outbound | both`)
5. No raw SQL across boundary

**Five primitives:**

```python
class SemanticOrgModel:
    def __init__(self, conn: Connection):
        """Conn from get_tenant_connection() — already scoped."""
    
    def get_entities(self, entity_type: str, at_seq: int, 
                     filters: dict | None = None) -> list[Entity]:
        """Lookup entities of a given type at a point in time."""
    
    def get_related(self, entity_id: UUID, edge_types: list[str],
                    direction: str, at_seq: int) -> list[RelatedEntity]:
        """Single-hop relationships."""
    
    def traverse(self, start_ids: list[UUID], edge_categories: list[str],
                 direction: str, max_depth: int, at_seq: int,
                 edge_types: list[str] | None = None) -> list[TraversedEntity]:
        """Multi-hop graph traversal. edge_categories REQUIRED."""
    
    def query_entities(self, entity_type: str, at_seq: int,
                       conditions: dict) -> list[Entity]:
        """Query by attribute conditions (eq/in/gt/lt/like via dict syntax)."""
    
    def current_version_seq(self) -> int:
        """Get current version_seq for explicit 'current state' queries."""
    
    # Diff primitives (§11)
    def diff_for_entities(self, ...) -> DiffResult: ...
    def diff_impact(self, ...) -> ImpactResult: ...
    def diff_window(self, ...) -> list[Change]: ...
```

**`at_seq` is required everywhere.** No `None` default for "current state." Consumers call `current_version_seq()` first, then pass.

**What's NOT designed (deferred to Substrate 3):**
- Per-entity-type helpers
- Domain shortcuts
- Query DSL
- Caching strategy
- Bulk operations

---

## 13. Tiered Capability Model

Per D-010 / D-013. (See Phase 1 spec section 7 for full tiering.)

**Tier 1 (this commit):**
- All entity types and detail tables in §9
- All 14 edge types in §8
- Validation rule formula parsing (D-013)
- Permission modeling per §10
- Versioning, change log, diff engine

**Tier 2 (future):**
- Flow logic interpretation (entry conditions, record updates, decision branches)
- Permission inheritance computation
- Sharing rule evaluation
- Approval process modeling

**Tier 3 (future):**
- Apex behavior analysis
- Complex sharing edge cases
- Lightning page composition
- Managed package internals

---

## 14. What's Deferred to Phase 3

- Refresh scheduling specifics (intervals, triggers, partial vs. full)
- Observability (metrics, logging, alerts)
- Tenant onboarding sequence
- Schema migration strategy at scale (parallel runners, failure handling)
- Cleanup and retention policy for change_log

---

## 15. Glossary

See GLOSSARY.md.

---

## End of SPEC (Phase 2 complete)

Substrate 1 is now design-complete on the dimensions that block Substrate 3. Phase 3 operational details will be designed when implementation surfaces real questions.
