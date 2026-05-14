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

## §9: SVS-catalog re-fetch in PicklistValue phase

**Date:** 2026-05-12
**Step:** PicklistValue phase implementation (cycle 3 of 12)
**Source:** Live test wall-clock observation — PicklistValue
            phase re-fetches both GVS and SVS streams to extract
            nested values, redundant with PicklistValueSet phase's
            earlier fetch of the same data

PicklistValue values come nested inside the GVS/SVS records that
PicklistValueSet phase already fetched. Re-fetching in
phase_picklist_value is ~7 min wall-clock on this sandbox (the
616-entry SVS catalog with per-label Metadata fetch).

Possible optimization: thread parent records from PicklistValueSet
phase into PicklistValue phase via SyncContext caching.

Architectural cost:
- SyncContext gains per-entity-type caching
- Phase ordering becomes explicit (currently any phase can run
  independently with its own fetches)
- Need to clear cache when sync_run completes to avoid leaking
  between runs

~~Not implemented now. Deferred until either (a) total sync
wall-clock becomes a customer-visible problem, or (b) we have
multiple phases with redundant fetches and a uniform solution
is worth designing.~~

**RESOLVED 2026-05-13** — Profile phase audit (corrections-log
§19) added a second wall-clock pressure point (588KB live test
wall-clock spent on the §9 re-fetch alone). The optimization
is now cheap to land — SyncContext is a non-frozen dataclass;
no scaffolding needed for "uniform caching" since SVS is the
only re-fetched stream today (GVS is a single bulk Tooling
call; cheap regardless of N).

### Implementation
- `primeqa/sync/context.py`:
  `svs_metadata_cache: dict[str, dict] = field(default_factory=dict)`.
  Keyed by SVS FullName (e.g., 'AccountSource' →
  `{standardValue: [...]}`). Per-SyncContext lifetime; no
  cross-run leakage (a fresh ctx is constructed per
  sync_run).
- `primeqa/integrations/sf_client.py`:
  `extract_picklist_value_payloads_from_metadata(parent_external_id, metadata, value_list_key) → list[dict]`
  module-level helper. Single source of truth for the
  value-extraction transform that PV phase used to inline.
  Accepts either `standardValue` (SVS) or `customValue` (GVS)
  as `value_list_key`.
- `primeqa/sync/phases.py`:
  - `phase_picklist_value_set` now writes each fetched SVS
    record's Metadata into `ctx.svs_metadata_cache[FullName]`
    as a side effect of its existing fetch loop. GVS records
    are NOT cached (no benefit).
  - `phase_picklist_value` now reads `ctx.svs_metadata_cache`
    directly when populated, calling the extraction helper
    per cache entry. When the cache is empty (PV-in-isolation
    test or resumed sync), falls back to
    `fetch_standard_value_sets(labels=None)` with an INFO
    log so the rare case is visible. The GVS path always
    re-fetches (cheap).

### Wall-clock impact
- Before: PVS fetches 616 SVS records (~6 min) + PV refetches
  the same 616 records (~6 min) = ~12 min of redundant SVS
  fetching per sync. Across both syncs in the live test:
  ~24 min total SVS fetch time.
- After: PVS fetches 616 records (~6 min); PV reads from
  cache (~10-20 sec for ~600-entry dict iteration +
  materialize). Across both syncs: ~12 min total SVS fetch
  time.
- Live integration test wall-clock: ~13:33 (post-Profile,
  pre-§9) → expected ~10-11 min after §9 (drop of ~2-3 min
  per sync × 2 syncs).

### Coherence with existing design
- Phase ordering already explicit via ENTITY_ORDER (no new
  constraint).
- Cache is per-SyncContext (one instance per sync_run); no
  cross-run leakage by construction. No clear-on-completion
  hook needed.
- Fallback path keeps PV phase runnable in isolation
  (test scenarios + future resumed-sync work).
- Tests at unit level (3 PVS cache-population tests + 2 PV
  cache-consumption tests + 1 renamed PV fallback test +
  4 SyncContext cache tests + 9 extraction-helper tests = 19
  new tests).

## §10: HAS_PICKLIST_VALUES deferred — REST describe doesn't expose GVS refs

**Date:** 2026-05-12
**Step:** Field phase (4 of 12) — first edge-writing phase
**Source:** Field phase live probe of standard picklist fields
            (Account.Industry, Account.AccountSource) showed no
            valueSet/valueSetName key in REST describe response

Substrate-1's TIER_1_EDGES has HAS_PICKLIST_VALUES (Field →
PicklistValueSet), but the REST sObject describe endpoint
(fetch_fields_for_object) does NOT expose value-set references.
Standard picklist fields expose inline picklistValues only;
global-value-set-referencing fields expose the same inline view.

Detecting GVS references requires the Tooling API path:
  SELECT Id, Metadata FROM CustomField WHERE Id = '...'
per the §1 Metadata 1-row constraint. The CustomField record's
Metadata.valueSet.valueSetName carries the GVS FullName.

Substrate-1 does not have fetch_custom_field_metadata today.
Adding it is a Category-2 (two-phase Tooling+Metadata) fetcher
similar to fetch_validation_rules.

Additionally, standard-value-set references (e.g., Account.
AccountSource → SVS:AccountSource) are not directly exposed by
any single API; detection would require content-matching field
picklistValues against each SVS's standardValue list.

**Resolution:** HAS_PICKLIST_VALUES deferred to a dedicated
cycle that adds fetch_custom_field_metadata. Sandbox has 0
GVSes, so the deferral has zero observable test impact today;
field_details.picklist_value_set_entity_id stays NULL for all
Field rows until that cycle lands. Schema accommodates the
future fill (column already nullable).

**Pattern for future cycles:** REST describe is the "fast path"
for Salesforce metadata but is incomplete for several
cross-entity relationship details. When the sync needs a piece
of metadata that REST doesn't expose, expect a Category-2
Tooling+Metadata fetcher to be the answer.

## §11: Edge supersession semantics — property-less edges use identity-based diff

**Date:** 2026-05-12
**Step:** Field phase (4 of 12) — first edge-writing phase
**Source:** Survey of TIER_1_EDGES revealed BELONGS_TO,
            HAS_PICKLIST_VALUES, HAS_RELATIONSHIP_TO all have
            properties_schema=None; substrate-1's
            validate_edge_properties accepts only empty dict
            for these.

Substrate-1's TIER_1_EDGES has both:
- Property-less edge types (BELONGS_TO, HAS_RELATIONSHIP_TO,
  HAS_PICKLIST_VALUES, HAS_PROFILE per the validate function
  docstring) — properties_schema=None; the edge accepts only {}
- Property-bearing edge types (INCLUDES_FIELD with positional
  field-order metadata, GRANTS_OBJECT_ACCESS with permission
  flags, etc.) — properties_schema defines required fields

For property-less edges, edge identity is the tuple
(source_id, target_id, edge_type). There is no per-edge
properties dict to hash. Supersession is binary:
- In incoming sync set, not in existing-active set → INSERT
- In existing-active set, not in incoming set → close
  (valid_to_seq = current logical_version_seq - 1)
- In both sets → no-op (unchanged)

No properties_hash column needed; sync_engine doesn't compute
hashes for these edges. Set-difference is sufficient.

When property-bearing edge types land in their respective phase
cycles (Layout for INCLUDES_FIELD; Profile / PermissionSet for
GRANTS_*), the supersession algorithm extends with hash-compare
semantics for the properties dict, mirroring entity supersession.
At that point, adding a last_seed_hash column to the edges table
may make sense; for now, deferred per YAGNI (the column doesn't
benefit any current edge type and would just be unused weight).

## §12: Edges containment-unique index narrowed to BELONGS_TO

**Date:** 2026-05-12
**Step:** Field phase (3 of 12) — first phase to write
            multi-target STRUCTURAL edges
**Source:** Live test failed with UniqueViolation on
            idx_edges_unique_containment when Field phase
            attempted to write multiple HAS_RELATIONSHIP_TO
            edges from a single polymorphic-reference Field
            (Task.WhoId → [Contact, Lead], Lead.OwnerId →
            [User, Group], etc.)

Substrate-1's migration `20260427_0020_phase1_edges.py`
created idx_edges_unique_containment with partial filter
`WHERE edge_category = 'STRUCTURAL'`. The accompanying
comment stated the intent: "Prevents duplicate BELONGS_TO
entries for the same source at the same version."

The filter over-generalized. Both BELONGS_TO and
HAS_RELATIONSHIP_TO are STRUCTURAL category in
TIER_1_EDGES. The original intent (one-parent-per-child
containment uniqueness for BELONGS_TO specifically) wasn't
captured precisely in the filter.

HAS_RELATIONSHIP_TO is multi-target by design: polymorphic
references in Salesforce (WhoId → Contact|Lead, etc.) need
multiple edges from one source Field at one valid_from_seq.

**Resolution:** Migration 20260512_0030 narrows the filter
to `WHERE edge_type = 'BELONGS_TO'`. Active-edge uniqueness
for all edge types remains enforced by
idx_edges_unique_active_non_references (keys on
source_entity_id, edge_type, target_entity_id — which
correctly distinguishes multi-target edges by their
distinct target_entity_id values).

**Pattern for future cycles:** When adding new STRUCTURAL
edge types to TIER_1_EDGES, verify whether the new type is
single-target-by-design (like BELONGS_TO) or multi-target
(like HAS_RELATIONSHIP_TO). Single-target types should
reference idx_edges_unique_containment by being added to
its partial filter; multi-target types should NOT.

## §14: Substrate-1 internal contradiction — CONSTRAINS_PICKLIST_VALUES target type

**Date:** 2026-05-12
**Step:** RecordType phase (5 of 12) — survey before
            implementation
**Source:** Found during survey of substrate-1's edge writers
            for the RecordType phase

Substrate-1's TIER_1_EDGES registry and runtime derivation
code disagree on the target entity type for the
CONSTRAINS_PICKLIST_VALUES edge:

- `primeqa/semantic/edges.py` TIER_1_EDGES registry says:
    CONSTRAINS_PICKLIST_VALUES
      source: RecordType
      target: PicklistValueSet (coarse — one edge per
                                RT × value set)
- `primeqa/semantic/derivation.py::_edges_from_record_type_row`
  writes target_entity_id from a row in
  record_type_picklist_value_grants, which keys on
  `picklist_value_entity_id` — i.e., target is a
  PicklistValue, not a PicklistValueSet (fine — one edge
  per RT × individual value)

No CHECK constraint enforces target.entity_type matches the
registry's declared target_entity_types. At runtime, the
derivation writer's behavior is what actually ships.

Implications:
1. Sync engine CONSTRAINS_PICKLIST_VALUES writes (deferred —
   see below) must choose one model and resolve the
   contradiction.
2. record_type_picklist_value_grants junction table (PK on
   RT × PicklistValue) supports fine model; coarse model
   would use a different schema.
3. Cardinality differs dramatically: fine model produces
   ~5-20 edges per RT (one per active picklist value); coarse
   model produces ~1-5 edges per RT (one per constrained
   value set). For 5 RTs, that's the difference between
   ~50-100 edges and ~5-25 edges.

**Resolution deferred.** RecordType phase ships without
CONSTRAINS_PICKLIST_VALUES until:
- fetch_custom_field_metadata Tooling fetcher (§10) lands,
  enabling picklist→value-set identity resolution
- Substrate-1 design owner picks a model (coarse vs fine)
  and the contradicting code path is reconciled

Likely resolution path: fine model wins because
record_type_picklist_value_grants already exists and is
consistent with derivation.py. The registry's declared
target should change to PicklistValue. But this is
substrate-1's decision, not sync's.

## §15: layout_type sourced from Salesforce, not invented

**Date:** 2026-05-12
**Step:** Layout phase (6 of 12)
**Source:** Survey of substrate-1's fetch_layout_names()
            during Layout phase implementation

Substrate-1's `layout_details.layout_type` column is
VARCHAR(20) NOT NULL with no DB default. The Layout phase
sources the value from Salesforce's Tooling Layout.LayoutType
field, NOT a hardcoded sync-layer convention.

Tooling Layout.LayoutType values verified in this sandbox at
v66.0:
- `Standard` — page layouts (returned by REST
  /sobjects/{name}/describe/layouts; the dominant case)
- `GlobalQuickActionList` — the special variant backing the
  global quick-actions menu (EntityDefinitionId='Global'; no
  parent sObject)

The Layout phase filters out `GlobalQuickActionList`-type
rows because they have no parent Object to link to via
`layout_details.object_entity_id` (NOT NULL FK to entities).
Standard layouts are materialized; quick-action-list layouts
are skipped with a WARN log per the lenient-tolerance
pattern (corrections-log §6).

Pattern: when a Salesforce-provided field already populates a
detail-table column's domain, source the value from Salesforce
rather than invent a sync-layer enumeration. The substrate-1
`fetch_layout_names()` docstring documents the LayoutType
discrimination so consumers (including future phases for
CompactLayout / SearchLayout if those are added) follow the
same convention.

Future cycles adding Compact Layout or Search Layout support
will need different fetchers (different Tooling tables:
CompactLayout, SearchLayout). Their LayoutType values will
similarly come from Salesforce — sync layer remains a
pass-through.

## §16: Layout-source ASSIGNED_TO_PROFILE_RECORDTYPE deferred

**Date:** 2026-05-12
**Status: RESOLVED 2026-05-14** (§16-resolution cycle — see
RESOLUTION block at the end of this entry)
**Step:** Layout phase (6 of 12)
**Source:** Survey of TIER_1_EDGES for Layout-source edge types

TIER_1_EDGES has a third Layout-source edge type beyond
BELONGS_TO and INCLUDES_FIELD:
  ASSIGNED_TO_PROFILE_RECORDTYPE: Layout → Profile
                                  (property-bearing, category=CONFIG)

Properties schema (AssignedToProfileRecordtypeProperties):
  record_type_entity_id: UUID
  is_default: bool

Deferred this cycle. Profile entities don't exist yet (Profile
phase pending). Pre-wiring the edge spec would create code
that silently skips all edge writes (resolver returns None for
every target). Wait until Profile phase lands; wire then.

The property requires resolving a RecordType entity_id for the
record_type_entity_id field. RecordType entities DO exist (this
cycle is post-RT cycle d91e777), so the RT-resolution piece is
available. The blocker is purely the Profile target.

When Profile phase lands:
1. Add ASSIGNED_TO_PROFILE_RECORDTYPE spec to Layout's
   EDGE_SPECS entry
2. Extractor walks the Tooling Layout-Profile-RT assignments
   table (likely Tooling ProfileLayout) — fetcher TBD
3. Properties extractor resolves RT external_id to entity_id
   via make_parent_resolver

Pattern: when adding a new entity type whose edges target
another entity type that hasn't been implemented yet, defer
the edge spec until both ends are real. Avoids speculative
unused code paths that produce silent zero-edge writes.

### §16 RESOLUTION (2026-05-14)

Implemented via the §16-resolution cycle on
phase-2-substrate-1-sync. Two commits:
- **Precursor (P6)** — `fetch_profile_layouts` substrate-1
  Tooling fetcher (commit 98f8f8c)
- **Main** — phase_layout wiring +
  ASSIGNED_TO_PROFILE_RECORDTYPE edge_spec

**Data source.** Tooling `ProfileLayout` sObject (~3,131 rows
in sandbox: 18 profiles × ~174 layouts). Bulk-queryable in a
single SOQL — no per-Profile iteration.

The originally-anticipated sources DON'T expose this data
(corrected the §16 deferral note's "fetcher TBD" / "Tooling
ProfileLayout — fetcher TBD"):
- REST `describe/layouts` (`fetch_layouts_for_object`) returns
  `recordTypeMappings` (RecordType ↔ Layout) but carries NO
  Profile reference.
- Tooling `Profile.Metadata` (`fetch_profiles`) OMITS
  `layoutAssignments` — it's a Metadata-API-retrieve-only
  field. Live probe: 0/18 sandbox profiles had it.

`ProfileLayout` is the canonical bulk source; it's Tooling-only
(the Data API rejects it, HTTP 400).

**Design decisions:**

1. **NULL-RecordTypeId rows skipped.** `ProfileLayout` rows
   with `RecordTypeId=NULL` are legitimate Salesforce metadata
   (Profile X uses Layout Y by default when no RecordType
   applies). But `AssignedToProfileRecordtypeProperties.
   record_type_entity_id` is REQUIRED (not Optional) — the edge
   type is, by name and schema, scoped to RT-bound assignments.
   `_decorate_layouts_with_profile_assignments` filters NULL-RT
   rows out (with an INFO-logged count). If consumers later
   need "Profile uses Layout X as default for Object Y," that's
   a different edge type — out of scope for §16. Scope
   clarification, not deferral (same shape as §21's TRIGGERS_ON
   record-trigger scoping).

2. **`is_default` cross-referenced from `recordTypeMappings`.**
   `ProfileLayout`'s bulk SOQL doesn't expose a default flag.
   The "default" semantics live in
   `recordTypeMappings[].defaultRecordTypeMapping` (from
   `describe/layouts`, which phase_layout already fetches).
   phase_layout builds an `(LayoutId, RecordTypeId) → is_default`
   map from data already in memory — no extra round-trip.

3. **Profile + RecordType resolution via the entities table.**
   phase_layout reads `attributes->>'Id'` on synced `Profile`
   and `RecordType` entity rows to build:
   - `profile_id → profile external_id` (the edge's target;
     resolved to a Profile entity by the materialize layer's
     parent_resolver)
   - `recordtype_id → recordtype entity_id` UUID (the required
     `record_type_entity_id` property — a forward reference
     pre-resolved in-phase, since the edge_specs extractor has
     no parent_resolver; same pattern as the User cycle's
     `assigned_by_user_entity_id`).
   Mirrors the User cycle's entities-table-derived maps. No new
   ID-resolution fetcher needed.

**Decoration timing.** The `_profile_layout_assignments` marker
is injected AFTER `batched_materialize` (not before, like the
other phases' markers). It is edge-extraction input, NOT Layout
entity state — keeping it out of the Layout's `attributes`
JSONB decouples the Layout entity hash from ProfileLayout /
RecordType-entity-id churn. `normalized_payloads` (computed
after decoration) carries it for edge extraction only.

**Cardinality.** ~3,131 ProfileLayout rows; the actual edge
count after filtering (Profile ∈ synced AND RecordType
non-NULL AND RecordType ∈ synced) is modest — possibly small
or zero if the sandbox's user-defined RecordTypes don't
intersect the ProfileLayout rows. Like Profile's
GRANTS_FIELD_ACCESS, most ProfileLayout rows silently skip
(non-synced layouts/RTs, or NULL-RT). The integration test's
regression floor is `>= 0`.

**Retracts** the deferral note's "fetcher TBD" and
"Properties extractor resolves RT external_id to entity_id via
make_parent_resolver" — the extractor has no parent_resolver;
the resolution happens in-phase, and the fetcher is the new
`fetch_profile_layouts` (not a make_parent_resolver call).

## §17: ValidationRule REFERENCES edge deferred — formula parser unbuilt

**Date:** 2026-05-12
**Step:** ValidationRule phase (7 of 12)
**Source:** TIER_1_EDGES survey during ValidationRule cycle

TIER_1_EDGES has a third VR-source edge type beyond
BELONGS_TO and APPLIES_TO:
  REFERENCES: ValidationRule → Field (property-bearing)

REFERENCES carries ReferencesProperties:
  reference_type: 'read' | 'priorvalue' | 'ischanged' | 'isnew'
  is_priorvalue: bool
  is_ischanged: bool
  is_isnew: bool

Substrate-1 has a validation_rule_field_refs junction table
parallel to record_type_picklist_value_grants. Writing
REFERENCES requires:

1. Salesforce formula language parser (tokenize PRIORVALUE,
   ISCHANGED, ISNEW, bare field references, dotted
   relationship traversals like Owner.Name)
2. Field-name disambiguation (bare 'Amount' implies parent-
   Object's Amount; 'Account.Industry' is qualified)
3. Junction-table writer (similar to deferred
   record_type_picklist_value_grants)

None of these exist today. Building them is its own focused
cycle.

Deferred. Pattern matches §10 (HAS_PICKLIST_VALUES needs
fetch_custom_field_metadata), §14 (CONSTRAINS_PICKLIST_VALUES
needs §10 + design resolution), §16 (ASSIGNED_TO_PROFILE_RT
needs Profile entities) — each waits for unbuilt
infrastructure.

## §18: Bulk-fetcher child phases require explicit parent-scope filter

**Date:** 2026-05-12
**Step:** RecordType + ValidationRule cycles (5 + 7 of 12) — bug
            surfaced by first live integration test against local
            Postgres
**Source:** phase_validation_rule failed with
            "Cannot resolve parent Object 'sfFma__FeatureParameterBoolean__c'
            for ValidationRule 'FullNameUpdatePrevention'"

Object phase filters Objects by syncability rules (per design
doc decision Option C narrowest: queryable AND searchable AND
NOT deprecated AND NOT customSetting). Managed-package
internal Objects like sfFma__* (Salesforce internal Feature
Parameter framework) are excluded.

Child phases that fetch entities via bulk Tooling SOQL
(no per-Object iteration) must filter their fetched payloads
against the syncable-Objects set; otherwise their detail
mappers fail with FK-resolution errors when child entities
reference filtered-out parents.

Affected phases:
- phase_record_type (cycle d91e777): latent — sandbox's 5
  RTs happen to be on standard Objects; bug would trigger
  on orgs with RTs on managed-package Objects
- phase_validation_rule (cycle a9f4322): triggered live
  by the sfFma__FeatureParameterBoolean__c VR

Pattern (Option A — explicit per-phase filter):
1. Phase function queries entities table for the
   synced-Object api_name set at phase start (via
   _synced_object_api_names helper in phases.py)
2. Filters raw payloads against the set before
   materialization
3. Logs the count of skipped entities for visibility

Alternatives considered (and rejected):
- Softening detail mappers to return None on unresolvable
  parent: spreads concern across mappers; loses materialize
  layer's FK-mismatch safety property (would mask real
  ordering bugs)
- Materialize layer skip-on-missing-parent hook: hidden
  behavior; would silently skip legitimate ordering issues

Why this matters for future contractors:
- Per-Object-iteration phases (Field, Layout) inherit the
  filter implicitly — they only fetch children for synced
  Objects
- Bulk-fetcher child phases (RT, VR, future similar) need
  explicit filter
- When adding a new entity type, check whether its fetch
  pattern is per-parent or bulk; bulk fetchers of child-of-X
  entities need the explicit X-scope filter

Not applicable to:
- Org-level entity phases (Profile, PermissionSet, User,
  Flow) — these aren't child-of-Object
- Per-Object-iteration phases (Field, Layout) — implicit
  filter


## §19: Edge target-scope filtering — silent skip with observability

Date: 2026-05-13
Step: Profile phase (8 of 12) — audit during cycle
Source: Q1+Q2+Q3 audit during Profile cycle live verification

Audit of `phase_profile`'s first live run surfaced that
`materialize_edges_for_entities` silently skips edges whose
`target_external_id` doesn't resolve to a materialized
`entity_id`. This is the documented design across all phases
(Field's HAS_RELATIONSHIP_TO; Profile's GRANTS_OBJECT_ACCESS,
GRANTS_FIELD_ACCESS):

- Targets that don't resolve are silently skipped
- Prevents orphan edges
- Doesn't fail the whole sync on legitimate scope misses
  (e.g., managed-package internal Objects filtered by Object
  phase's syncability rules)

Audit numbers from sandbox live test (18 Profiles):
- GRANTS_OBJECT_ACCESS: 628 candidate targets → 505 written,
  123 skipped (~19.6% skip rate)
- GRANTS_FIELD_ACCESS: 10,582 candidate targets → 10,217
  written, 365 skipped (~3.4% skip rate)
- Total: 11,210 candidate → 10,722 written, 488 skipped (~4.4%)

Heaviest skip Profiles in sandbox:
- Admin: 39 OP + 44 FP = 83
- System Administrator (non-API): 5 + 65 = 70
- Analytics Cloud Integration User: 47 + 14 = 61

Skipped targets correspond to managed-package internal entities
(`sfFma__*`, Analytics Cloud-specific Objects, etc.) that the
Object phase correctly excludes from syncable scope.

**Resolution this cycle:** add observability to silent skip.
Skipping behavior preserved (correct for legitimate scope
cases); `PhaseResult.edges_skipped_by_type` now tracks count per
`edge_type`; debug-level log emits on each skip with target
context. No change to entity hashes (Profile entity continues
to hash full `Metadata`; only edges respect synced scope).

Implementation:
- `primeqa/sync/result.py`:
  `edges_skipped_by_type: dict[str, int] = field(default_factory=dict)`
  + `record_skipped_edge(edge_type)` method
- `primeqa/sync/materialize.py`:
  `materialize_edges_for_entities` increments the counter +
  emits `logger.debug(...)` on each `parent_resolver → None` skip

Cross-cutting fix: applies to every phase using the shared
materialize path (Field, RecordType, Layout, ValidationRule,
Profile, future PermissionSet/User/Flow). Profile cycle's
audit numbers (488 skips at 4.4%) are surfaced automatically
in any future phase's `PhaseResult` as well.

**Future work tracked:** a more substantive observability cycle
could distinguish three skip categories:
1. Target is in known-non-syncable set (managed-package
   internals filtered by Object phase) — expected, no concern
2. Target was in syncable set but got superseded mid-run —
   likely concurrent sync race; warrants warn-level log
3. Target `external_id` is malformed — sync code bug; warrants
   error-level log

Current implementation logs all skips at debug level. The
distinction above requires correlation with synced-set
membership at the time of skip; cheap to add when needed.


## §20: FlowDefinition is NOT a materialized entity — resolves substrate-1 design contradiction

Date: 2026-05-14
Step: FlowDefinition cycle (would-be 11 of 12) — survey phase
Source: Survey for FlowDefinition phase surfaced design-level
        contradiction between SPEC.md §9 and ENTITY_ORDER

Substrate-1 had an internal contradiction about whether
FlowDefinition is a Tier-1 entity:

- SPEC.md §9: "10 Tier-1 entity types" (Flow is in the list;
  FlowDefinition is not)
- TIER_1_ENTITIES, _NORMALIZERS, _TO_TEXT, TIER_1_EDGES: no
  FlowDefinition (matches SPEC.md)
- DB schema: no flow_definition_details table (matches SPEC.md)
- But: ENTITY_ORDER listed 12 entries including FlowDefinition
- And: the earlier corrections-log entry ("Cross-cutting drift:
  normalization.py and ENTITY_ORDER", 2026-05-12) tracked
  "extend normalization.py / semantic_text.py / presentation
  with flow_definition functions... Tracked as a pre-requisite
  for the FlowDefinition phase cycle."

**Resolution: SPEC.md wins. FlowDefinition is NOT a materialized
entity.**

Rationale:

1. SPEC.md §9's "10 Tier-1 entity types" is design-locked
   authoritative spec. The four substrate-1 registries
   (TIER_1_ENTITIES, _NORMALIZERS, _TO_TEXT, TIER_1_EDGES) and
   the DB schema all already match it — only ENTITY_ORDER and
   the design-doc's hardcoded-order snippet had drifted.
2. Substrate-1's bitemporal supersession IS the native
   versioning mechanism for Flow's versioning needs:
   - Flow entity keyed by DeveloperName (stable identity)
   - Each new version deployment → supersedes the prior Flow
     record with new attributes (api_version, manageable_state,
     is_active, version_number, etc.)
   - Entity history IS version history
   - diff-window queries surface flow-level changes natively
3. Modeling FlowDefinition + Flow as separate entities would
   replicate versioning semantics on top of the bitemporal
   layer that already provides them.
4. Consumer query patterns work with the Flow-as-supersession
   model (existence, diff, activation state, version history).

ENTITY_ORDER corrected from 12 → 11 entries. The 11 are all
entity-materializing phases: the 10 Tier-1 detail-table entity
types per SPEC §9 (Object, Field, RecordType, Layout,
ValidationRule, Flow, Profile, PermissionSet, User,
PicklistValue) PLUS PicklistValueSet — an entity-materializing
phase that intentionally has no detail table (its full shape
lives in entities.attributes JSONB; see the Object-cycle
corrections-log entry). FlowDefinition removed from ENTITY_ORDER
(and therefore from the PHASE_REGISTRY comprehension that builds
no-op phases from it).

fetch_flow_definitions remains as a substrate-1 Tooling fetcher
— it provides parent context (DeveloperName, ActiveVersionId,
ManageableState) that the Flow phase consumes to:
- Identify which Flow version is active
- Decorate Flow records with stable identity (DeveloperName
  from FlowDefinition rather than per-version FullName)
- Set Flow's manageable_state attribute correctly

**Retracts:** the earlier "Cross-cutting drift" entry's
resolution that FlowDefinition needs entity plumbing
(_normalize_flow_definition, _to_text_flow_definition, a
TIER_1_ENTITIES entry, a flow_definition_details table). That
resolution was written from the sync-layer side without
reconciling against SPEC.md §9. No such plumbing will be added;
the drift is resolved by removing FlowDefinition from
ENTITY_ORDER, not by promoting it to an entity.

**Implication for phase count:** the structural-sync phase
sequence is 11 entity-materializing phases (Object → Flow),
not 12. The Flow phase (next cycle) is the last entity phase;
enrichment (design doc §9 step 4) runs as a separate stage
after the structural phases, not as an ENTITY_ORDER member.


## §21: TRIGGERS_ON scope — record triggers only per substrate-1 design

Date: 2026-05-14
Step: Flow phase (final entity phase, 11/11) — survey
Source: Live probe of sandbox flows surfaced PlatformEvent-
        triggered flows that fall outside TRIGGERS_ON's
        property schema

This is a SCOPE CLARIFICATION entry, not a deferral. Distinct
from §10, §14, §16, §17 (deferred for unbuilt
infrastructure / entities / parsers) — TRIGGERS_ON is fully
implemented within substrate-1's defined scope.

Substrate-1's TriggersOnProperties schema allows only
record-trigger types via its `trigger_type_known` validator:

    BeforeSave, AfterSave, BeforeDelete, AfterDelete

Per its docstring: "Autolaunched and screen flows have no
trigger_type — they don't produce TRIGGERS_ON edges in the
first place."

Salesforce supports additional trigger paradigms beyond
record triggers:

- Platform Events — Metadata.start.triggerType='PlatformEvent';
  the start.object is a Platform Event (api name ends `__e`),
  which is not `queryable AND searchable` and is therefore
  excluded by Object phase's `_is_syncable_object` filter
- Scheduled triggers — time-based, no object target
- Data Cloud Segments, DataGraphs — other non-record targets

These are deliberately out of scope for TRIGGERS_ON per
substrate-1's design intent. Future cycles can extend support
via either:

  Option 1: Extend TriggersOnProperties' allowed set AND extend
            the Object entity to include Platform Events (relax
            the `queryable AND searchable` filter for the `__e`
            suffix).
  Option 2: Add new edge types (TRIGGERS_ON_PLATFORM_EVENT,
            TRIGGERS_ON_SCHEDULE, …) with type-specific target
            entity types.

Either option is a focused substrate-1 enhancement cycle.

**Implementation in this cycle:** edge_specs.py defines
`_RECORD_TRIGGER_TYPE_MAP` (the four record-trigger types).
`_flow_triggers_on_targets` returns an empty list when
`Metadata.start.triggerType` is not in that map — PlatformEvent /
Scheduled / Autolaunched / Screen flows produce no TRIGGERS_ON
edge. They are correctly OUT OF SCOPE, not silently skipped:
the extractor never feeds a non-record trigger_type to the
Pydantic schema's validator, so no validation error can arise.
The presentation adapter (`_to_presentation_flow`) and detail
mapper (`_map_flow_details`) apply the identical record-trigger
gate, so `flow_details.trigger_type` /
`triggers_on_object_entity_id` and the semantic_text trigger
fields stay consistent with the edge.

**Sandbox observation:** this sandbox has 13 FlowDefinitions and
14 Flow versions, all ProcessType=AutoLaunchedFlow. Of the 2
flows with a `Metadata.start.object`, both are
PlatformEvent-triggered. Zero record-triggered flows exist;
therefore zero TRIGGERS_ON edges materialize. The implementation
is correctness-complete and unit-tested across the record-trigger
types — a future org with record-triggered flows produces
TRIGGERS_ON edges automatically without code change.

