# Substrate 1 — Semantic Org Model — SPEC

**Status:** PARTIAL — Phase 1 (conceptual shape) complete; Phase 2 (data structures, storage) and Phase 3 (operational details) pending.

**Last substantive update:** 2026-04-24 (Phase 1 design session)

**Supersedes:** no prior design. The current flat "metadata context" in generation is an ad-hoc precursor.

---

## Purpose

This spec defines the Semantic Org Model: the data structure, contract, and operational characteristics of PrimeQA's representation of a Salesforce org.

Design proceeds in phases. Each phase ends with a commit and an EVOLUTION.md update.

- **Phase 1 (this commit):** Conceptual shape — what S1 IS, what it answers, its lifecycle, multi-tenancy, versioning, sync strategy, tiered capability model, derived edges by archetype, cross-tenant boundary.
- **Phase 2 (next):** Data structures, storage backend, query interface, diff engine internals.
- **Phase 3 (later):** Operational details — refresh scheduling, observability, tenant onboarding, schema migrations.

---

## 1. What Substrate 1 IS

A continuously evolving, per-tenant graph that represents the structure, configuration, behavior, and change history of a Salesforce org — enriched with derived relationships that capture how the org's entities depend on each other.

**Concretely:**

- One model per tenant. Authoritative for that tenant. No cross-tenant data.
- Event-sourced: an append-only change log captures every meaningful change.
- Versioned at logical checkpoints: deploys, sandbox refreshes, sync milestones, manual checkpoints.
- Behavior graph, not metadata cache: derived edges (computed at sync time) carry the relationships consumers reason about.
- Tiered: capability evolves from Tier 1 (structure + validation parsing) to Tier 2 (behavior interpretation) to Tier 3 (deep semantics).
- Consumers query a stable logical version of the graph, not the constantly-evolving live state.

**See also:** BACKGROUND.md for why this substrate exists and what's NOT in scope.

---

## 2. What the Model Must Answer

Categorized by the consuming substrate. This list shapes the data model and query interface.

### 2.1 For Generation (Substrate 3)
- "What objects exist? What are their fields? Which are creatable, updateable, required?"
- "What validation rules exist on object X, and what fields/conditions do they check?" (Tier 1)
- "What flows trigger on object X, and what records do they create/update?" (Tier 2)
- "What page layouts exist for the Profile + RecordType combination, and what fields do they include?"
- "What's the parent-child relationship between Object X and Object Y?"

### 2.2 For Execution (Substrate 4)
- "Given user U, can they perform action A on object O?"
- "Will updating record R's field F trigger any flow / validation / approval process?"
- "What is the page layout user U sees for record type RT on object O?"

### 2.3 For Interpretation (Substrate 6)
- "What validation rule causes error E on object O?"
- "What changed between version V1 (when this test was green) and version V2 (when it failed)?"
- "Given the current org state, why might this test fail beyond the obvious?"

### 2.4 For Evolution (Substrate 8)
- "Field F was renamed to F'. What tests reference F? What flows? What layouts?"
- "Validation rule R was changed. What tests assumed the old behavior?"
- "Flow F was deactivated. What test scenarios depend on it?"

### 2.5 For Knowledge (Substrate 5)
- "Show me the patterns of validation rules across this org's objects."
- "What permission sets are commonly assigned together?"
(These queries feed S5's tenant-private knowledge; S5 then derives shareable patterns per the cross-tenant boundary in §6.)

### 2.6 For Conversation (Substrate 7)
- All of the above, exposed via a query interface S7 can compose.

---

## 3. Lifecycle

### 3.1 Tenant onboarding
When a tenant connects an org, S1 performs an initial full sync. The model is tagged with the initial logical version (`v_genesis`). The model becomes available to consumers once initial sync is complete.

[Phase 3: detailed onboarding sequence, partial-sync availability, error handling]

### 3.2 Steady state
- **Background sync** runs on a schedule (default TBD; candidates: hourly, nightly, configurable per tenant). Refreshes the full model with delta detection — only changed entities are written to the change log.
- **On-demand sync** runs before critical operations (test generation for a release, regression run before deploy). Consumers can request a fresh slice (e.g., "refresh all flows on Account") without forcing a full sync.
- **Logical version markers** are placed at meaningful events: a deploy detected via metadata change, a sandbox refresh detected via instance reset, a manual user checkpoint, a periodic milestone (e.g., daily).

### 3.3 Read-time
Consumers query "the model as of logical version V". The version is fixed for the duration of their operation — even if background sync writes new events, the consumer's view doesn't shift. This is what makes test runs reproducible against a known org state.

### 3.4 Tenant offboarding
When a tenant is removed, the entire model is dropped. Per-tenant authoritative storage means deletion is a single operation with no cross-tenant residue.

---

## 4. Versioning

### 4.1 Event-sourced model
The model is fundamentally an append-only change log. Each event records:
- Timestamp
- Logical version assigned (if any)
- Entity affected
- Change type (created, updated, deleted)
- Before/after values (or a reference to retrieve them)
- Source (which sync run produced this event)

The "current model" is a materialized view computed from the event stream. Historical versions are reconstructable from the stream.

### 4.2 Logical version markers
Not every event creates a logical version. Logical versions are coarse-grained:
- Detected deploys (metadata-change events that arrive together)
- Sandbox refresh events
- Manual checkpoints created by users (e.g., "freeze before regression run")
- Scheduled milestones (e.g., daily)

Logical versions are named (e.g., `v_2026_04_24_pre_deploy`, `v_genesis`, `v_deploy_42`). Consumers reference versions by name; humans can read them.

### 4.3 Consumer binding
Every test generated, test executed, and result produced records the logical version it was based on. This is the foundation of explainability:

- "Test T was generated against v_2026_04_20."
- "Test T failed in run R, executed against v_2026_04_24_pre_deploy."
- "What changed between v_2026_04_20 and v_2026_04_24_pre_deploy that affects T?" — answered by the diff engine (§5).

### 4.4 Storage
[Phase 2: how events and version markers are physically stored, indexing strategy, retention policy.]

---

## 5. Diff Engine

### 5.1 Purpose
The diff engine answers: "What changed between version A and version B that affects entity E?"

This is the engine of explainability. Substrate 6 (Interpretation) and Substrate 8 (Evolution) both depend on it.

### 5.2 Capabilities
- **Entity-scoped diff:** Given a test case, return all changes between two versions that could affect this test (its referenced fields, objects, validation rules, flows).
- **Impact diff:** Given a change (e.g., "field F was renamed"), return all entities that reference the changed entity directly or transitively.
- **Time-window diff:** Given two versions, return all changes between them, optionally filtered by entity type.

### 5.3 Performance contract
[Phase 2: target latencies for common diff queries.]

### 5.4 Why it's first-class
If consumers compute diffs themselves, they reinvent traversal logic incompatibly. The diff engine encapsulates the graph traversal (transitive closure of "what references what") and the change-log query in one place.

---

## 6. Multi-Tenancy & Cross-Tenant Boundary

### 6.1 Per-tenant authoritative model
Each tenant has its own model. Storage is partitioned by tenant. No code path traverses tenant boundaries within Substrate 1.

### 6.2 Cross-tenant policy
Cross-tenant learning is structurally separate (see Substrate 5, when designed). The boundary policy:

| Tier | What | Cross-tenant? |
|---|---|---|
| Tier 1 | Raw data: formulas, field values, names, descriptions, configurations | **Strictly private** |
| Tier 2 | Derived patterns: "When Stage=Closed Won, Amount typically required" | Safe to share |
| Tier 3 | Aggregated statistics: "73% of orgs have rule shape X" | Safe to share |

**Anonymized formula examples are explicitly forbidden.** Salesforce formulas leak business logic through structure alone; "anonymization" is not a reliable barrier. The bright line is "patterns and statistics yes, examples no."

### 6.3 Where this is enforced
Substrate 1 simply doesn't expose cross-tenant data. Substrate 5 (Knowledge System) is the layer that derives patterns from per-tenant models; that derivation respects the boundary. The enforcement is architectural, not policy-based — by giving each tenant its own model, there's no "data to anonymize" because there's no shared store to begin with.

---

## 7. Tiered Capability Model

### 7.1 Tiers

**Tier 1 — Foundation (must build first):**
- Objects, fields (including create/update/required attributes)
- Relationships (lookup, master-detail, junction)
- Record types and picklist value constraints
- Layouts and layout assignments (Profile + RecordType → Layout)
- Profiles and permission sets (existence + assignment, not yet inheritance computation)
- Flow existence + trigger objects (NOT flow logic interpretation yet)
- Validation rule existence + **formula parsing** (which fields, what conditions)
- Sharing model (existence of sharing rules, not yet evaluation)
- Change log + logical versions + diff engine

**Tier 2 — Behavior interpretation:**
- Flow logic interpretation (entry conditions, record updates, decision branches)
- Permission inheritance computation across profile + permission set chains
- Sharing rule evaluation (given user U and record R, can U see R?)
- Approval process modeling
- Apex trigger references (not bodies)

**Tier 3 — Deep semantics:**
- Apex behavior analysis (static analysis or LLM-assisted interpretation)
- Complex sharing edge cases (criteria-based sharing, account team membership effects)
- Lightning page composition (component visibility per context)
- Managed package internals (where exposed)

### 7.2 capability_level exposure

The model exposes a `capability_level` attribute (TIER_1 | TIER_2 | TIER_3) that consumers query before relying on tier-specific capabilities. Example:

- Substrate 3 generating tests for permission scenarios queries `capability_level`. If TIER_1, falls back to coarse permission checks (does this profile exist?). If TIER_2, uses inheritance-aware checks.

### 7.3 Why validation parsing is Tier 1, not Tier 2

A Tier 1 model that knows validation rules exist but doesn't know what they check is too thin to be useful. Substrate 3 generates tests that randomly fail validation. Substrate 6 explains failures as "validation rule failed" without saying which fields. Validation rule formula parsing is in scope for Tier 1.

What stays in Tier 2: flow logic, permission inheritance, sharing evaluation. These genuinely warrant deferral.

---

## 8. Derived Edges

### 8.1 Mindset
Edges represent invariants the system must reason about, not features the system supports. We enumerate edges based on relationships that must always be true in this graph for any consumer.

### 8.2 Edges by archetype

**Archetype A — Data Behavior:**
- `flow_modifies_field` (flow F updates field G under condition C)
- `object_triggers_object` (operation on O1 produces operation on O2)
- `validation_applies_under_condition` (rule R fires when condition C is met)
- `flow_calls_apex` (flow F invokes apex method M)
- `apex_class_modifies_object` (apex class C creates/updates records of object O)

**Archetype B — Configuration:**
- `layout_assigned_to_profile_recordtype` (layout L is shown for Profile P + RecordType RT)
- `layout_includes_field_for_profile_recordtype` (field F is on layout L for P + RT)
- `record_type_constrains_picklist_values` (record type RT limits picklist values for field F)

**Archetype C — Permissions:**
- `profile_grants_object_access` (profile P grants CRED on object O)
- `permission_set_grants_object_access` (perm set PS grants CRED on object O)
- `permission_set_grants_field_access` (perm set PS grants R/W on field F)
- `profile_inherits_permission_set` (profile P implies perm set PS)
- `sharing_rule_grants_record_access` (rule SR grants visibility from group G to records matching criteria)

**Archetype D — UI:**
- `lightning_page_assigns_component` (lightning page LP includes component C)
- `component_renders_field` (component C displays field F)

**Archetype E — Integration:**
- `outbound_message_fires_on` (outbound message OM is sent on event E for object O)
- `platform_event_published_by` (event PE is published by source S)
- `external_callout_in_apex` (apex class C makes callout to endpoint E)

### 8.3 Tier mapping
Not all edges are Tier 1. The edge taxonomy spans tiers; the model populates edges as its capability_level rises.

| Tier | Edges populated |
|---|---|
| Tier 1 | All structural/configuration edges (Archetypes B, C structural). Flow trigger objects (existence). Validation rule references (from formula parsing). |
| Tier 2 | Flow modifies field (requires logic interpretation). Permission inheritance. Sharing evaluation. |
| Tier 3 | Apex-derived edges. UI composition edges. Integration edges. |

Consumers expecting an edge that's beyond the current capability_level get a NotAvailableAtCurrentTier error, not silent absence.

---

## 9. What's Deferred

The following will be addressed in Phase 2:

- Concrete data model (entity schemas, relationships, attributes)
- Storage backend choice (relational, graph DB, hybrid)
- Query interface (Python API, GraphQL, SQL, custom DSL)
- Diff engine internals (algorithms, performance characteristics)
- Indexing strategy
- Tenant model isolation mechanism (separate schemas, separate databases, row-level isolation)

Phase 3:
- Refresh scheduling specifics (intervals, triggers, partial vs. full)
- Observability (metrics, logging, alerts)
- Tenant onboarding sequence
- Migration path from current "metadata context" approach
- Schema migrations as the model evolves

---

## 10. Glossary

See GLOSSARY.md in this substrate's directory.

---

## End of SPEC (Phase 1 complete)

Phase 2 to be designed in subsequent session.
