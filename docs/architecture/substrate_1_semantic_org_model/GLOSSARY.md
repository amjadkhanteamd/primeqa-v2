# Substrate 1 — Semantic Org Model — Glossary

Terms defined specifically for this substrate.

---

**Behavior graph.** The form of S1's data model: entities plus derived edges that represent relationships and dependencies, computed at sync time. Contrast with "metadata cache," which stores only raw entity attributes.

**Bitemporal columns.** `valid_from_seq` and `valid_to_seq` on entities and edges tables. Define the version range during which a row is considered valid. `valid_to_seq IS NULL` means currently valid.

**Canonical foundation tables.** The four tables that form S1's data model: `logical_versions`, `entities`, `edges`, `change_log`. All other tables (detail tables, materialized views) hang off these.

**Capability level.** The S1 model exposes a `capability_level` attribute (TIER_1, TIER_2, or TIER_3) so consumers know what they can rely on.

**Change log.** The append-only event stream that is the foundation of S1's event-sourced model. Every meaningful change to the org produces an event with structured before_state, after_state, and changed_field_names.

**changed_field_names.** Array column on `change_log` capturing which keys differ between before_state and after_state. Indexed via GIN for targeted "find changes to attribute X" queries.

**Connection resolver.** The function `get_tenant_connection(tenant_id)` that is the canonical entry point for tenant-scoped database access. Sets `search_path` and `app.tenant_id` via `SET LOCAL`.

**Containment edges.** Edges of category STRUCTURAL that represent containment relationships (Field BELONGS_TO Object, etc.). Auto-generated from column references in detail tables; never independently written.

**Cross-tenant boundary policy.** Three-tier policy governing what can be shared across tenants: Tier 1 raw data strictly private, Tier 2 derived patterns safe to share, Tier 3 aggregated statistics safe to share. Anonymized examples explicitly forbidden.

**Defensive tenant_id assertion.** Column on canonical tables (entities, edges, change_log) that defaults to `current_setting('app.tenant_id')::INT` and is enforced via CHECK constraint. Acts as a safety net against schema-routing bugs. Not used for filtering or routing.

**Derived edge.** A relationship in the graph that is computed at sync time, not directly present in Salesforce metadata. Example: `flow_modifies_field` is derived from parsing flow XML.

**Derived-from-column edge.** A subset of derived edges where the relationship is captured authoritatively in a detail table column (e.g., `field_details.object_entity_id`). The edge is auto-generated whenever the column is written.

**Diff engine.** First-class subsystem of S1 with three query primitives: `diff_for_entities`, `diff_impact`, `diff_window`. Returns raw structured Change objects.

**Direction control.** On diff and traversal queries: `inbound | outbound | both | none`. Distinguishes "what depends on me" (inbound) from "what I depend on" (outbound).

**Edge category.** Discriminator on edges: STRUCTURAL, CONFIG, PERMISSION, BEHAVIOR. Enables filtered traversal during impact analysis. Required parameter on `diff_impact` and `traverse`.

**Edge as invariant.** The mindset that derived edges represent relationships that must always be true in the graph, not features that consumers want.

**Effective permissions.** Per-(User, Field) computed access aggregating Profile + assigned PermissionSets + inherited PermissionSets, taking most-permissive. Materialized as `effective_field_permissions` view; refreshed after sync.

**Entity-scoped sync.** Sync model where different entity types have different schedules. Structural metadata (Objects, Fields, Layouts) syncs less frequently than operational data (Users, PermissionSetAssignments).

**Event-sourced model.** S1's storage model: an append-only change log captures every change. Historical states are reconstructable from the change log.

**Logical version.** A named, coarse-grained checkpoint corresponding to a meaningful event (deploy, sandbox refresh, manual checkpoint, scheduled milestone). Identified by `version_seq` (BIGINT, monotonic per tenant) and `version_name` (human-readable).

**On-demand sync.** Sync mode that refreshes a specific slice of the model on request. Contrast with background sync.

**Per-tenant authoritative model.** Each tenant has its own complete model. No data crosses tenant boundaries within S1.

**Property matrix edge.** An edge whose properties JSONB carries a structured access matrix. Example: `GRANTS_FIELD_ACCESS` with `properties: {can_read: bool, can_edit: bool}` — one edge per (subject, field) pair, not separate edges per access type.

**SET LOCAL.** PostgreSQL command for transaction-scoped settings. Used in connection resolver for both `search_path` and `app.tenant_id`. Auto-resets on transaction commit/rollback. Works correctly under PgBouncer transaction-mode pooling.

**Schema-per-tenant.** Isolation strategy: one Postgres database, one schema per tenant (`tenant_<integer_id>`), plus a `shared` schema for control-plane data. Provides genuine isolation without per-tenant database overhead.

**Sync milestone.** A user-triggered or system-triggered checkpoint creating a logical version marker.

**Tiered capability model.** S1 evolves in tiers — Tier 1 (foundation: structure + validation parsing), Tier 2 (behavior interpretation), Tier 3 (deep semantics).

**Traversal direction.** See "Direction control."

**TraversalSpec.** Dataclass parameter to `diff_for_entities` specifying optional graph traversal: direction, max_depth, edge_categories, edge_types.

**Version_seq.** BIGINT, monotonically increasing per tenant. Used in queries for fast comparison. Companion to `version_name` (human-readable).
