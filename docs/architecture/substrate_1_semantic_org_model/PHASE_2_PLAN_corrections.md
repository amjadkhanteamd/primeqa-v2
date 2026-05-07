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

## §4.2 fetch_validation_rules: Salesforce Tooling Metadata 1-row constraint

**Date:** 2026-05-07
**Step:** 2C
**Source:** PHASE_2_PLAN.md §4.2 implies fetch_validation_rules makes
            one Tooling SOQL call returning all rules with full Metadata.

Discovered at live-test time: Salesforce Tooling API rejects SOQL queries
selecting the `Metadata` field unless the query returns at most 1 row.
Documented Salesforce constraint, not surfaced in any SOQL syntax checker.
HTTP 400 with errorCode MALFORMED_QUERY: "When retrieving results with
Metadata or FullName fields, the query qualifications must specify no more
than one row for retrieval."

**Resolution:** Two-phase fetch within fetch_validation_rules.
- Phase 1: Bulk SOQL fetches Id + non-Metadata fields for all rules.
- Phase 2: Per-Id SOQL fetches Metadata for each rule (1 row each).
- N+1 round trips for N rules. Sandbox at 61 rules → 62 calls, ~45s.

Implementation: primeqa/integrations/sf_client.py fetch_validation_rules,
with inline comment documenting the constraint. Unit tests include
test_fetch_validation_rules_makes_n_plus_one_calls to lock the contract
structurally.

**Plan implication:** §4.2 should note the two-phase pattern is required
for any entity type whose Tooling representation requires Metadata. Future
entity types (Flow Metadata, etc.) likely face the same constraint.

## §4.2 fetch_validation_rules Phase 1: EntityDefinition 1000-row subquery

**Date:** 2026-05-07
**Step:** 2C
**Source:** Initial Phase 1 SOQL design used
            `SELECT ..., EntityDefinition.QualifiedApiName FROM ValidationRule`
            for human-readable Object name. Salesforce rejected with
            EXTERNAL_OBJECT_UNSUPPORTED_EXCEPTION (1000-row subquery limit)
            because the join through EntityDefinition spans all
            EntityDefinition rows in the org including managed packages
            (typically thousands).

**Resolution:** Phase 1 SOQL uses `EntityDefinitionId` direct UUID field
instead of the relationship traversal. No join, no subquery limit. Phase 2
sync layer (step 4) resolves EntityDefinitionId → Object entity_id via the
Object describe cache built earlier in the same sync run, leveraging D-037
entity-type ordering (Object syncs before ValidationRule).

Implementation: primeqa/integrations/sf_client.py Phase 1 SOQL is
`SELECT Id, ValidationName, Active, ErrorMessage, ErrorDisplayField,
Description, EntityDefinitionId FROM ValidationRule`. Unit test
test_fetch_validation_rules_phase1_does_not_traverse_entitydefinition is
an explicit regression guard.

**Plan implication:** §4.2 should note that EntityDefinition relationship
traversal is forbidden in Tooling SOQL for managed-package-heavy orgs.
Other entity types whose Tooling representation might tempt similar
joins (Flow's EntityDefinition link, anything joining through standard
enum tables) should use direct ID fields instead.

## Process recommendation: live-smoke-test SOQL before unit-test design

**Date:** 2026-05-07
**Step:** 2C
**Source:** The two Tooling API constraints above were both discovered
            at live-test time, after unit tests with mocked HTTP had
            passed. The unit tests passed because mocked responses don't
            exercise Salesforce's actual constraint enforcement.

**Recommendation:** When implementing future fetch methods (the remaining
8 entity types in 2C-extended, plus any future Tooling-API queries in
Phase 3+), the workflow should be:

1. Draft the SOQL query.
2. Run it directly against the sandbox via curl or sf-cli BEFORE writing
   the fetch method. Confirm it returns a usable response shape.
3. Only after confirming the SOQL works, design the fetch method and
   unit tests against the confirmed response shape.
4. Run live integration tests early (per-method, not at end-of-step) to
   surface API-level constraints before the implementation is complete.

Reasoning: Salesforce Tooling API has many runtime-only constraints
(Metadata 1-row, subquery limits, governor limits, managed-package
semantics) that are not in any documented SOQL grammar checker. Mocked
unit tests cannot surface these. Live testing earlier in the design loop
saves the rewrite cost we paid in 2C (two design iterations across
fetch_validation_rules).

This is a working-agreement update; future fetch-method work should
follow this pattern.
