# Substrate 1, Phase 1 — Implementation Summary

**Status:** Complete (ready to merge to main)
**Branch:** `phase-1-substrate-1`
**Commits:** 11 (commits `2506651` through `[close-out commit]`)
**Cumulative diff vs main:** 30 files, ~4,700 insertions (including this summary doc)

## Scope delivered

Phase 1 implements the full edges + detail-tables foundation for the Semantic Org Model substrate per SPEC §6 and §9. Three pillars:

### Pillar 1 — Edges table and Tier 1 edge type registry

- `edges` table with bitemporal `valid_from_seq` / `valid_to_seq` columns, JSONB `properties` column, partial unique indexes for STRUCTURAL containment and (defense-in-depth) per-(source, target, edge_type) active uniqueness for non-REFERENCES types
- `primeqa/semantic/edges.py` module with `TIER_1_EDGES` registry: 14 edge types across 4 categories (STRUCTURAL, CONFIG, PERMISSION, BEHAVIOR), 6 with Pydantic property schemas, 8 propertyless
- `validate_edge_properties` boundary function for write-time validation

### Pillar 2 — Detail tables (D-025) for 10 entity types

Each entity type with attributes gets a per-entity-version detail table joined by `entity_id`. Hot columns capture queryable metadata; sparse JSONB attributes live on `entities.attributes` and validate through Pydantic schemas in `primeqa/semantic/entity_attributes.py`.

| Entity Type | Detail Table | Attributes Schema |
|---|---|---|
| Object | object_details | ObjectAttributes |
| Field | field_details | FieldAttributes |
| RecordType | record_type_details | RecordTypeAttributes |
| Layout | layout_details | LayoutAttributes |
| PicklistValue | picklist_value_details | PicklistValueAttributes |
| Profile | profile_details | ProfileAttributes |
| PermissionSet | permission_set_details | PermissionSetAttributes |
| User | user_details | UserAttributes |
| Flow | flow_details | FlowAttributes |
| ValidationRule | validation_rule_details | ValidationRuleAttributes |

Plus two **hot reference tables** (per D-026): `validation_rule_field_refs` (REFERENCES edge source) and `record_type_picklist_value_grants` (CONSTRAINS_PICKLIST_VALUES edge source).

### Pillar 3 — Containment-as-column derivation logic (D-017)

`primeqa/semantic/derivation.py` produces edges as projections of detail-table columns and hot-reference-table rows. Three public APIs:

- `edges_for_entity(entity_id, conn)` — pure read; returns expected edge dicts for an entity at the current state
- `supersede_and_derive(entity_id, new_seq, conn)` — lifecycle primitive; idempotent supersession + insertion in one call
- `verify_derivation_integrity(conn)` — periodic audit returning {missing, extra} discrepancies

All 8 derived edge types per D-019 sourced correctly: BELONGS_TO (5 detail tables), HAS_RELATIONSHIP_TO, HAS_PROFILE, TRIGGERS_ON, APPLIES_TO, REFERENCES, HAS_PICKLIST_VALUES, CONSTRAINS_PICKLIST_VALUES.

## Migrations (14 in `phase-1-substrate-1` beyond Phase 0 foundation)

| # | Revision | Purpose |
|---|---|---|
| 1 | 20260427_0020 | edges table + 14-edge registry |
| 2 | 20260427_0030 | object_details |
| 3 | 20260427_0040 | field_details |
| 4 | 20260427_0050 | record_type_details |
| 5 | 20260427_0060 | layout_details |
| 6 | 20260427_0070 | picklist_value_details |
| 7 | 20260427_0080 | profile_details |
| 8 | 20260427_0090 | permission_set_details |
| 9 | 20260427_0100 | user_details |
| 10 | 20260427_0110 | flow_details (with Tier 2 reservation per D-027) |
| 11 | 20260427_0120 | validation_rule_details + validation_rule_field_refs |
| 12 | 20260427_0130 | field_details ADD COLUMN picklist_value_set_entity_id |
| 13 | 20260427_0140 | record_type_picklist_value_grants |
| 14 | 20260427_0150 | edges defense-in-depth unique active partial index |

## Test coverage

**111 tests, 0 failures, 0 teardown errors.**

- **98 unit tests** (`tests/unit/`, ~1 second runtime, no DB dependency)
  - Per-source edge generators for derivation (15 tests)
  - Entity attributes registry round-trip (48 tests)
  - Edges registry + property schemas (35 tests)
- **13 integration tests** (`tests/integration/`, ~13 minutes runtime against real Railway DB)
  - Per-entity-type derivation including multi-source ValidationRule and RecordType
  - Bitemporal supersession across version_seq boundaries
  - Verify-integrity audit for clean / missing / extra edge scenarios
  - Idempotency on re-derive

Cleanup fixture `cleanup_test_entities` in `tests/integration/conftest.py` performs 8-pass ordered deletion respecting all schema FK dependencies (edges → hot-reference rows → ValidationRule → RecordType → User → other leaf entities → parents → logical_versions).

## Decisions documented during Phase 1

- D-024: 12-week design lock under monolithic execution
- D-025: Detail-table patterns (per-entity-version, hot+JSONB split, Pydantic boundary)
- D-026: Hot reference table pattern (this document)
- D-027: Tier 2 reservation pattern
- D-028: `validate_edge_properties` JSON serialization behavior

## What Phase 1 explicitly does NOT include

- Sync engine (Phase 2): the actual code that calls `supersede_and_derive` from a Salesforce sync batch. `derivation.py` is sync-engine-callable but no caller exists yet.
- Materialized view (Phase 2): the read-side projection over edges + entities for query class consumers.
- Query class (Phase 3).
- v2 cutover (Phase 4) and hardening (Phase 5).

## Ready to merge

The branch passes all 111 tests, all migrations apply cleanly to a fresh tenant_1 schema, and all production modules import cleanly. The commit history is linear and each commit is independently reviewable against the SPEC sections it implements.

Reviewers may want to start at the test suite (`b7c7abb`, the most recent commit) to see the system end-to-end, then walk back through the derivation module (`f0fdec5`), the source-column gap closers (`851958a`), and the detail-table commits in reverse chronological order.
