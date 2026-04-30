# PHASE_2_PLAN.md corrections log

Corrections made during implementation that should fold back into a
future plan revision. These are surface-level errata, not architectural
changes.

## §3.3 sync_runs column type

**Date:** 2026-04-30
**Step:** 1C
**Source:** PHASE_2_PLAN.md §3.3 sync_runs CREATE TABLE block

Plan reads:
    logical_version_seq INT REFERENCES logical_versions(version_seq),

Should read:
    logical_version_seq BIGINT REFERENCES logical_versions(version_seq),

**Reason:** logical_versions.version_seq is BIGINT (Phase 0 foundation).
All other references to it in the schema (entities.valid_from_seq,
entities.valid_to_seq, edges.valid_from_seq, edges.valid_to_seq,
change_log.version_seq, logical_versions.parent_version_seq) are BIGINT.
INT would create an incompatible-type FK that Postgres rejects.

The 1C migration uses BIGINT (correct). The plan source still reads INT
(typo). Plan should be updated in a future revision pass; this file
tracks the discrepancy until then.

## Documentation gap: TIER_1_ENTITIES vs normalization scope

**Date:** 2026-04-30
**Step:** 2A
**Source:** PHASE_2_PLAN.md §4.1 (lists 11 normalize_* functions)
            vs primeqa/semantic/entity_attributes.TIER_1_ENTITIES
            (10 types, no PicklistValueSet)

The plan §4.1 lists 11 normalize functions including
_normalize_picklist_value_set. TIER_1_ENTITIES has 10 types and
deliberately excludes PicklistValueSet because it has no sparse
Pydantic attribute schema (per Phase 1 design — its semantic shape
is "parent container for PicklistValues", with no per-row
attributes worth validating).

These registries answer different questions:
- TIER_1_ENTITIES: which entity types have Pydantic attribute schemas?
- normalize() dispatch: which entity types does Phase 2 sync write?

Phase 2 sync writes all 11 (including PicklistValueSet as parent
containers). normalize() therefore covers 11. The plan §4.1 is
correct. The implementation does NOT import TIER_1_ENTITIES into
normalization.py — these are independent concerns.

The plan could clarify this distinction in a future revision
(a sentence in §4.1 noting "normalize covers all sync-written
entity types, including those without Pydantic attribute schemas
like PicklistValueSet").
