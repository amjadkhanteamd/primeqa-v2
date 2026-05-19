# S3 (Generation) — Phase 1 Preconditions

## Purpose

This document captures the ground state at the moment S3 Phase 1
(architectural design) begins. It exists to make S3's inherited
assumptions explicit, so design decisions can reference a clear
"what is true right now" baseline rather than rediscovering it
during conversations.

**Date of articulation:** 2026-05-19
**Substrate-2 state:** Phase 4 complete and merged to main
(commit `656d53f`).
**Substrate-1 state:** Phase 2 sync — 11 of the 12 originally-scoped
Tier 1 entity types live; the 12th (FlowDefinition) was deliberately
unified into the Flow entity per substrate-1 corrections-log §20
(Flow is versioned natively via bitemporal supersession,
`fetch_flow_definitions()` is a Tooling fetcher only). Remaining
open items are sub-feature deferrals: ValidationRule `REFERENCES`
edge population pending a Salesforce formula parser (corrections-log
§17), and standard-field → `StandardValueSet` detection deferred
to its own focused cycle (corrections-log §22).

---

## §1 — What S3 inherits from substrate-1 (Semantic Org Model)

### §1.1 Entity types currently available

Substrate-1 has shipped 11 Tier 1 entity types via Phase 2 sync
(cycles 4–10 plus the User / Flow follow-on work):

- **Object**
- **PicklistValueSet** (unified entity covering both GlobalValueSet
  and StandardValueSet source kinds)
- **PicklistValue**
- **Field**
- **RecordType**
- **Layout**
- **ValidationRule**
- **Profile**
- **PermissionSet**
- **User**
- **Flow** (versioned natively via bitemporal supersession; stable
  identity via `DeveloperName`)

A 12th entity type, **FlowDefinition**, was originally scoped but
was intentionally unified into Flow per corrections-log §20 —
`fetch_flow_definitions()` remains a substrate-1 Tooling fetcher,
not a materialization phase. S3 design should treat Flow as the
canonical "automation" entity; queries that conceptually reach for
"a Flow's prior definitions" walk Flow rows via `version_seq`
instead.

Authoritative source-of-record:
`primeqa/sync/fk_assertion.py` `ENTITY_ORDER`.

### §1.2 Edge types currently available

Substrate-1's 14 Tier 1 edge types are organized into four
categories:

- **STRUCTURAL (2)** — `BELONGS_TO`, `HAS_RELATIONSHIP_TO`.
- **CONFIG (4)** — `INCLUDES_FIELD`,
  `ASSIGNED_TO_PROFILE_RECORDTYPE`, `CONSTRAINS_PICKLIST_VALUES`,
  `HAS_PICKLIST_VALUES`.
- **PERMISSION (5)** — `GRANTS_OBJECT_ACCESS`,
  `GRANTS_FIELD_ACCESS`, `INHERITS_PERMISSION_SET`,
  `HAS_PROFILE`, `HAS_PERMISSION_SET`.
- **BEHAVIOR (3)** — `TRIGGERS_ON`, `APPLIES_TO`, `REFERENCES`.

13 of 14 edge types populate; `REFERENCES` is defined in the
registry but no edges materialize yet (see §1.3). Authoritative
source-of-record: `primeqa/semantic/edges.py` `TIER_1_EDGES`.

### §1.3 What S3 cannot rely on yet

- **`REFERENCES` edges (ValidationRule → Field)** — the registry
  entry exists, but population requires a Salesforce formula
  parser, which is deferred (corrections-log §17). S3 design
  must assume this edge is empty for v1.
- **`HAS_PICKLIST_VALUES` for standard fields → StandardValueSets** —
  GVS-backed custom picklist fields are wired; standard picklist
  fields (e.g., `Account.Industry`) need a content-matching
  heuristic that is deferred to its own focused cycle
  (corrections-log §22). Approximately 95 StandardValueSets
  exist in the org but no edges connect them to standard fields
  yet.
- **A separate FlowDefinition entity** — there is none. Flow IS
  the version history; any S3 reasoning about "the prior
  definitions of a Flow" must walk Flow rows via `version_seq`.

### §1.4 Query interfaces

Per substrate-1 SPEC §2.3, S3 has access to three core query
primitives for traversing the org model:

- **`TraversalSpec`** — declarative directionality, max depth,
  edge category / edge type filters.
- **`Change`** — event-sourced change-log row exposing diffs
  between logical version checkpoints.
- **`DiffEngine.diff_for_entities()`** — produces a `DiffResult`
  across entity_ids between two checkpoints.

All queries are version-aware (substrate-1's `version_seq` +
`version_name` dual identifiers). S3 will use these to:

- Resolve entity references to current `entity_id` values.
- Walk edges to find related entities.
- Maintain snapshot consistency across a generation session.

Note: substrate-1's design-era language sometimes refers to "five
primitives." The delivered Phase 2 shape settled at three
primitives plus two structural concepts (logical versioning +
bitemporal supersession) that enable them. The consumer-facing
query surface is not yet fully shipped at S3 design time —
implementation is deferred per D-023.

---

## §2 — What S3 inherits from substrate-2 (Test Representation)

### §2.1 Consumed Coordinator surfaces

S3 will primarily consume:

- **`write_claim`** — for emitting generated claims with
  `status='draft'` per SPEC §7.1; `actor='s3'`.
- **`write_recipe`** — for emitting recipes with
  `status='generated_unapproved'` per SPEC §7.4.
- **`query_equivalent_claims`** — for "has this claim been
  generated before?" duplicate-detection (using `identity_hash`
  + `identity_hash_version`).
- **`get_latest_claim`**, **`list_active_recipes`** — for
  inspecting existing state before generating.
- **`get_provenance` / `get_recipe_provenance`** — reserved per
  SPEC §10.2; NOT realized in Phase 4 (alongside
  `surface_unblessed_transition`). S3 design must not assume
  these surfaces exist for v1. When implemented, they will
  enable inspection of prior generation attempts on the same
  `test_id` / `recipe_id`; until then, iterative regeneration
  cannot rely on substrate-2's audit trail and must either
  work from `query_equivalent_claims` + `get_latest_claim`
  alone, or carry its own provenance state.

### §2.2 Body model surfaces

S3 produces typed Pydantic body instances matching substrate-2's
registry (16 registered kinds):

- 4 data-behavior claim kinds (`value-claim`,
  `state-transition-claim`, `automation-effect-claim`,
  `prohibition-claim`) plus semantic conditions.
- 5 trigger kinds + 5 recipe kinds + execution environment.

The body shapes are substrate-2's interface; S3 design decides
how to map LLM outputs to these typed shapes.

### §2.3 Authority constraints S3 must respect

Per substrate-2's authority model (D-061, D-064, SPEC §7):

- **No-autonomous-semantic-divergence.** S3 cannot write claims
  whose `identity_hash` differs from an existing approved
  version of the same `test_id`. The substrate rejects this with
  `AuthorityViolationError`; S3 must detect the case before
  writing (typically via `query_equivalent_claims`) and either
  choose a different `test_id` or back off to a no-op.
- **Conservative re-approval default.** Every recipe S3 writes
  lands in `status='generated_unapproved'` regardless of
  content. Human approval is required before execution
  eligibility.
- **S3 same-hash no-op.** If S3 regenerates a claim with
  identical canonical form (same `identity_hash`) to an existing
  version, substrate-2 returns `was_noop=True` and emits no new
  version, no provenance event. S3 should treat this as the
  expected response, not an error.
- **Draft status on writes.** S3-written claims always land in
  `status='draft'` for new tests or when content changes;
  existing approved versions are never auto-promoted.

### §2.4 Reference discipline

S3 must produce `IdentityBearingRef` instances (or its subclasses)
for entity references in claim bodies (`asserted_truth` and
`semantic_conditions`). The substrate enforces this via field-type
discipline; S3's output shapes must align.

For recipe bodies (the operational layer), S3 produces
`OperationalRef` — `PinnedRef` or `LogicalRef` as appropriate
per the layer's hybrid-by-layer rule (substrate-2 SPEC §5.1).

The C1-B canonicalization preserves `identity_hash` across entity
renames; S3 does not need special handling for renames as long
as its references are properly typed.

---

## §3 — What S3 cannot rely on (downstream substrates)

The following substrates do not exist at S3 design time:

- **S4 (Execution)** — no recipe execution capability exists.
  S3-generated recipes are static artifacts until S4 ships.
- **S6 (Intelligence)** — no failure attribution or explanation
  generation. S3's output quality must be assessed by other
  means (human review, structural validation against
  substrate-2's body shapes, eventual S4 execution outcomes).
- **S8 (Evolution)** — no autonomous evolution detection. S3
  cannot expect S8 to rewrite its recipes when underlying
  entities advance.

S3 design should make decisions that are robust to these
substrates NOT existing (i.e., S3 should be useful in isolation),
while leaving clean integration paths for when they ship.

---

## §4 — Open questions framed but not resolved

The following questions are deliberately punted to S3 Phase 1
design:

- LLM provider and model choice (Claude API per project memory;
  specific model versions TBD per archetype / cost-quality
  envelope).
- Generation determinism vs creativity trade-offs.
- Per-archetype generation strategies (data-behavior,
  configuration, permissions, UI, integration).
- Grounded negative-test generation strategy (only scenarios
  supported by actual org metadata per the v1 commitment in
  memory).
- Prompt management, versioning, and evaluation infrastructure.
- Quality metrics and acceptance thresholds.
- Generation-request shape (what does S3 take as input?).
- Failure-mode handling (what happens when generation produces
  invalid output?).
- Cost / latency / quality envelope per generation.

These are the architecturally interesting decisions Phase 1
will resolve.

---

## §5 — References

- Substrate-2 SPEC:
  `docs/architecture/substrate_2_test_representation/SPEC.md`
- Substrate-1 SPEC:
  `docs/architecture/substrate_1_semantic_org_model/SPEC.md`
- Substrate-1 corrections log (cycle-by-cycle implementation
  history, including the §17 formula-parser deferral, §20
  Flow / FlowDefinition unification, §22 StandardValueSet
  deferral): `docs/architecture/substrate_1_semantic_org_model/PHASE_2_PLAN_corrections.md`
- `DECISIONS_LOG.md` D-051 through D-068 (substrate-2 design +
  implementation); D-069 records the design-sequence decision to
  begin S3 ahead of substrate-1's deferred-item resolution.
- Authoritative entity / edge registries:
  `primeqa/sync/fk_assertion.py` (`ENTITY_ORDER`),
  `primeqa/semantic/edges.py` (`TIER_1_EDGES`).
