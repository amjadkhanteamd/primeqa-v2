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

## §4.2 Salesforce Tooling Metadata-or-FullName 1-row constraint

**Date:** 2026-05-07
**Step:** 2C
**Source:** PHASE_2_PLAN.md §4.2 implies fetch_validation_rules makes
            one Tooling SOQL call returning all rules with full Metadata.

Discovered at live-test time during Step 2C (ValidationRule) and
confirmed during 2C-extended (RecordType): Salesforce Tooling API
rejects SOQL queries selecting EITHER the `Metadata` field OR the
`FullName` field unless the query returns at most 1 row. Documented
Salesforce constraint, not surfaced in any SOQL syntax checker.
HTTP 400 with errorCode MALFORMED_QUERY: "When retrieving results
with Metadata or FullName fields, the query qualifications must
specify no more than one row for retrieval."

**Resolution:** Two-phase fetch within the affected fetch method.
- Phase 1: Bulk SOQL fetches Id + non-Metadata, non-FullName fields
  for all rows.
- Phase 2: Per-Id SOQL fetches Metadata AND/OR FullName for each
  row (1 row each).
- N+1 round trips for N rows. Sandbox at 61 ValidationRules and
  5+ RecordTypes confirms the pattern.

Implementation: primeqa/integrations/sf_client.py fetch_validation_rules,
with inline comment documenting the constraint. Unit tests include
test_fetch_validation_rules_makes_n_plus_one_calls to lock the contract
structurally.

**Plan implication:** §4.2 should note the two-phase pattern is required
for any entity type whose Tooling representation requires Metadata. Future
entity types (Flow Metadata, etc.) likely face the same constraint.

**Implication for 2C-extended fetch methods:** Any Tooling-API
fetch that needs Metadata or FullName must use the two-phase
pattern. RecordType (already confirmed), Layout, Profile,
PermissionSet, Flow are all expected to fall into this category.
The Tooling representation of these entities returns the rich
data via Metadata; bulk-fetching is structurally impossible
without this pattern.

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

## §4.2 StandardValueSet: reified-column-required SOQL constraint

**Date:** 2026-05-07
**Step:** 2C-extended Method 3 (Picklist value sets)
**Source:** Phase 2 plan §4.2 implies fetch methods can use bulk
            SOQL on Tooling entities. Discovered at live-test time
            that StandardValueSet rejects unfiltered queries.

Salesforce Tooling API requires SOQL queries against
`StandardValueSet` to include a WHERE filter on either `MasterLabel`
or `DurableId`. Documented Salesforce constraint, distinct from the
Metadata-or-FullName 1-row constraint and from the EntityDefinition
subquery limit. HTTP 400 with errorCode MALFORMED_QUERY:
"StandardValueSet: a filter on a reified column is required
[MasterLabel, DurableId]".

Affects only StandardValueSet (verified). GlobalValueSet supports
unfiltered bulk enumeration normally. The constraint reflects that
StandardValueSet is a system-defined catalog of built-in Salesforce
picklist types (~30-40 entries pinned per API version), not a
user-mutable table; Salesforce treats enumeration of it as a
catalog-lookup operation, not a row-scan operation.

**Resolution:** No bulk enumeration step for StandardValueSet.
fetch_standard_value_sets() iterates a hardcoded catalog of
MasterLabels (defined in primeqa/integrations/sf_constants.py)
and issues one Tooling SOQL Metadata fetch per label. The catalog
is pinned to Salesforce API v66.0 (matching sf_client.api_version)
and re-audited when the API version bumps as a tracked
dependency-update activity.

**Implication for sync architecture:** The hardcoded-catalog
pattern is appropriate where Salesforce treats an entity as a
system-defined catalog rather than user-mutable data. Future entity
types with similar semantics (e.g., entity-definition-level system
catalogs) may need the same approach. Discovery-driven enumeration
(walking field metadata for picklistValueSetName references) was
considered and rejected: it inverts the D-037 entity ordering and
produces sync-coverage gaps for SVSes not referenced in the synced
field set.

**Pattern: catalog-pin-and-rebumb.** When SOQL bulk enumeration is
blocked by a reified-column constraint and the entity is system-
defined, prefer the hardcoded catalog approach over discovery.
Pin to the API version; revisit at version bump.

## Salesforce metadata APIs are asymmetric

**Date:** 2026-05-08
**Step:** 2C-extended Method 3 (StandardValueSet catalog work)
**Source:** Architectural observation surfacing across §1, §2, §4
            and the StandardValueSet enumeration cul-de-sac.

The Salesforce metadata-access surface at v66.0 does not present
a uniform retrieval strategy across entity types. Fetch-method
design must accommodate this asymmetry rather than assume a
single pattern. Four categories observed so far:

**Category 1 — Runtime-introspectable types.** REST `describe`
and Tooling SOQL bulk enumeration both work cleanly. Examples:
Object (sobjects/), Field (sobjects/{name}/describe), Layout
(sobjects/{name}/describe/layouts), GlobalValueSet (Tooling
SOQL bulk works for non-Metadata fields).

**Category 2 — Tooling-API-with-Metadata-constraint types.**
Tooling SOQL works but the Metadata-or-FullName 1-row constraint
forces a two-phase fetch (bulk Phase 1 for Ids and metadata-light
fields, per-Id Phase 2 for Metadata/FullName). Examples:
ValidationRule, RecordType, GlobalValueSet (the Metadata path).
Likely upcoming: Profile, PermissionSet, Flow.

**Category 3 — Deployment-artifact types whose discoverability
surfaces customization state, not platform definition.** System-
defined catalogs that Salesforce treats as deployment artifacts
rather than queryable rows. Discoverability surfaces vary in
subtle ways:
- SOAP listMetadata (and sf CLI's invocation of it) returns
  only org-specific customizations, not the platform's built-in
  catalog. A pristine org returns empty (verified in this
  sandbox: zero entries despite SOQL confirming several
  built-ins exist).
- REST endpoints return the type-describe object, not member
  listings.
- SOQL is constraint-blocked (reified-column-required) for
  bulk enumeration; per-label filtering works for some entries
  but not all, with no clear org-state-independent way to know
  which entries are queryable.
- The platform's canonical catalog exists in Salesforce's
  published documentation per release. That documentation IS
  the source of truth.
- Catalog scale at v66.0 may be substantially larger than
  informal expectations: StandardValueSet expanded from a
  sales/service-oriented core (~30-60 entries) to 616 entries
  with the addition of industry-cloud-introduced SVSes (Health,
  Financial Services, Public Sector, Education, etc., which use
  naming conventions like trailing-digit version markers).
  Architectural responses to category 3 must accommodate this
  scale: sanity-check via representative sampling rather than
  full enumeration; consumer-layer fetch methods should support
  optional discovery-driven label restriction (planned for
  Commit 2b's fetch_standard_value_sets signature) so sync runs
  materialize only SVSes the customer's fields actually
  reference, while the canonical catalog remains complete in
  sf_constants.py for attribution and correctness.
Example: StandardValueSet at v66.0. Catalog pinned in
primeqa/integrations/sf_constants.py from the Metadata API
Developer Guide v66.0 (docs/references/salesforce/). Note that
Category 3's "deployment-artifact" framing applies to types
whose canonical catalog lives outside the runtime API; this is
distinct from Category 4 below, where the data IS in the runtime
API but in an unusual schema shape.

**Category 4 — Wide-flat-row types with separate child-permission
entities.** Tooling representation denormalizes "metadata" as
columns directly on the parent row plus separate child-entity
tables for grants. No Metadata complexvalue column, no FullName,
no 1-row constraint. Tooling SOQL bulk works cleanly for the
parent and each child. Discoverability is high — opposite of
Category 3 — but the parent's column count can be very large
(PermissionSet has ~350 boolean Permissions* columns plus
descriptive fields). Child entities (ObjectPermissions,
FieldPermissions) are queryable entity-wide with `ParentId`
joining child grants to parent rows.
Example: PermissionSet at v66.0. Three SOQL queries fetch the
full data: parent row wide-SELECT, all object-level grants,
all field-level grants. Sync layer joins by ParentId.
Special note: PermissionSet has 'Profile'-type rows that are
auto-generated synthetics per Profile, structurally duplicating
Profile's permission data. Sync layer should filter these
(Type = 'Profile') to avoid duplication; fetch returns them
per the transparent-transport-boundary principle.

**Implication for fetch-method design.** Each entity type's
enumeration strategy is a per-type design decision derived from
its category. The hardcoded-catalog-with-audit-discipline pattern
(see §4 and primeqa/integrations/sf_constants.py) is the
codified response for category 3. The two-phase pattern (see §1)
is the response for category 2. Direct REST is the response for
category 1.

**Implication for Phase 3+.** When Phase 3+ adds new entity types
to the model (Apex classes, custom labels, email templates,
etc.), the first design step is determining which category
applies. The live-test-first process (§3) is the primary tool
for that determination.

## SOQL pagination requires explicit handling

**Date:** 2026-05-08
**Step:** 2C-extended Method 4 (PermissionSet child queries)
**Source:** Live-test discovery during fetch_permission_sets
            implementation. ObjectPermissions returned 2,000 of
            2,606 rows; FieldPermissions returned 2,000 of 11,258
            rows. The remainder were silently dropped before the
            caller saw the result.

Salesforce SOQL endpoints (both `/services/data/{v}/query/` and
`/services/data/{v}/tooling/query/`) cap response size at 2000
rows by default. When a query result exceeds this, the response
carries `done: false` plus a `nextRecordsUrl` cursor. Subsequent
GETs on the cursor path return the next page (also up to 2000
rows). Iteration continues until `done: true`.

This is a uniform platform constraint affecting all SOQL methods,
not specific to any entity type or category from §5. Passing the
initial response straight back to the caller silently drops every
row past 2000 — a correctness bug, not a performance issue.

**Resolution:** SalesforceClient._query_all helper centralizes
pagination. All SOQL-issuing fetch methods route through it:
fetch_validation_rules, fetch_record_types,
fetch_global_value_sets, fetch_standard_value_sets,
fetch_profiles, fetch_permission_sets (parent + both child
queries). Direct use of _request for SOQL is now a correctness
bug.

REST methods (sobjects/describe, sobjects/{X}/describe, etc.) are
NOT subject to this constraint — different pagination semantics —
and continue using _request directly.

**Implication for sync architecture:** Fetch methods that pull
high-cardinality entities (FieldPermissions in this discovery,
potentially fields per object on managed-package-heavy orgs,
potentially ValidationRules on rule-heavy customer orgs) will
make multiple round trips per fetch invocation. Sync runtime
budget includes pagination-walk cost: ~0.6s per page at sandbox
latency. For a customer org with 50K FieldPermissions, that's
~25 pagination calls (~15s) for the FieldPermissions query
alone. Within sync budgets but a real factor.

**Why discovered now:** Prior fetch methods worked against
sandbox row counts well below 2000 (max was 71 PermissionSets,
61 ValidationRules). PermissionSet's child entities pushed past
the cap (2606 ObjectPermissions, 11258 FieldPermissions) and
surfaced the bug. Other fetch methods would have hit the same
bug on production-scale orgs without this fix.

## Cross-cutting drift: normalization.py and ENTITY_ORDER

**Date:** 2026-05-12
**Step:** 2C-extended Method 5 (FlowDefinition) introduced
            FlowDefinition as a 12th entity type; normalization.py
            still has 11 normalizers
**Source:** Discovered during Phase 2 step 4 Object phase
            survey — materialize helper would route through
            get_normalize_function('FlowDefinition') and find
            no entry

Substrate-1's primeqa/semantic/normalization.py has type-specific
normalize functions for 11 entity types: Object, Field,
ValidationRule, RecordType, Layout, GlobalValueSet,
StandardValueSet, Profile, PermissionSet, User, Flow.

2C-extended Method 5 (commit bac9e8d) added FlowDefinition as a
separate entity type in ENTITY_ORDER (since it has distinct
semantics from Flow versions — FlowDefinition is the named flow;
Flow rows are versions). FlowDefinition wasn't added to
normalization.py at that time because the normalization module
was a substrate-1 artifact and FlowDefinition was a fetch-method-
level addition.

**Resolution:** When the FlowDefinition phase is implemented
(per PHASE_2_STEP_4_SYNC_DESIGN.md §9 step 3), extend
primeqa/semantic/normalization.py with a normalize_flow_definition
function following the established per-type pattern. Same for
semantic_text.py's _to_text_* router and the presentation module's
per-type adapter (introduced in this design step). Tracked as
a pre-requisite for the FlowDefinition phase cycle.

**Pattern: normalize / semantic_text / presentation as parallel
registries.** When adding new entity types to ENTITY_ORDER,
extend all three modules consistently. The materialize helper's
router lookups (get_normalize_function, get_semantic_text_function,
to_presentation) raise KeyError on missing types; this would
surface at the entity type's first sync but fails too late
to be a clean error path. The discipline is: add ENTITY_ORDER
entry + normalize entry + semantic_text entry + presentation
entry in the same commit. A future test
test_normalize_registry_matches_entity_order codifies the
consistency check.

## PicklistValueSet entity_type unifies GlobalValueSet + StandardValueSet

**Date:** 2026-05-12
**Step:** Phase 2 step 4 PicklistValueSet phase implementation
**Source:** Discovered during PicklistValueSet phase survey —
            substrate-1's _to_text_picklist_value_set reads an
            `is_global_value_set` boolean and the fixture
            demonstrates the both-sources-under-one-entity-type
            pattern

Substrate-1 unifies two Salesforce sObject types under one
entity_type='PicklistValueSet':
- GlobalValueSet records (fetched via fetch_global_value_sets,
  Tooling+Metadata): is_global_value_set=True
- StandardValueSet records (fetched via fetch_standard_value_sets,
  hardcoded 616-entry catalog + per-label Metadata fetch):
  is_global_value_set=False

The unified design lets downstream consumers (Field phase,
attribution queries) treat picklist value sources uniformly —
Field's "values come from value set X" reference resolves
regardless of whether X is global or standard.

**Implementation sequencing:**
- This cycle: GVS source only (sandbox has 0 GVSes, exercises
  empty path)
- Subsequent cycle: SVS source via fetch_standard_value_sets
  iteration over sf_constants.STANDARD_VALUE_SET_LABELS catalog
- Both sources materialize under entity_type='PicklistValueSet'
  via the same batched_materialize pipeline

Pattern: when one substrate-1 entity_type covers multiple
Salesforce source streams, each source has its own fetch +
phase-step but shares the materialize chain (normalize +
presentation + semantic_text). The phase function for a unified
entity_type may need to call multiple fetch methods and
combine streams before calling batched_materialize.

## §8 addendum (SVS source implementation)

SVS records inherit the same FullName-keyed identifier shape
as GVS records. To prevent collisions between a customer-
named GVS (e.g., `'Industry'`) and the SVS catalog entry of
the same name, SVS external_ids are namespaced with `SVS:`
prefix at materialization. GVS external_ids remain
unprefixed (preserving the prior cycle's contract).

The `_source` marker added to raw payloads at the phase
function survives _strip_volatile and contributes to the
normalized hash — a record's source is part of its identity.
One-time GVS supersession on first-sync-after-this-cycle is
expected and harmless.

