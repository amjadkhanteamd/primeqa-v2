# Substrate 2 — Test Representation — SPEC

**Status:** All sections substantively complete per D-051 through
D-065. §1 (synthesis overview) composing §2–§11. D-203 closes the
D-110.1 deferral: `UpdateStep`/`DeleteStep` carry `expect_rejection`
(the 2-step behavioral negative is representable; still body schema
v1, claim identity untouched).

**Last substantive update:** 2026-05-18 (§1 synthesis)

---

## Purpose

This spec defines Substrate 2: PrimeQA's canonical data structure
for a test case.

Design proceeded in two phases:

- **Phase 1 (complete):** Conceptual shape — what S2 is, its
  deepest invariant, archetype representation, lifecycle, mutation
  paths, relationship to S1 and downstream substrates.
- **Phase 2 (complete):** Concrete data model — tables, columns,
  JSONB shapes, references, versioning, execution-history boundary,
  outward surfaces.

See `BACKGROUND.md` for why this substrate exists. See
`OPEN_QUESTIONS.md` for design surfaces (now all resolved). See
`DECISIONS_LOG.md` D-051 through D-065 for full design rationale
and alternatives considered.

---

## 1. What Substrate 2 IS

**Substrate 2 — Test Representation — is PrimeQA's canonical data
structure for what a test case is and what truths it asserts.**
It owns the structured representation of test cases: the
identity-bearing claims, the operational recipes that realize
them, the lifecycle by which they evolve, and the interfaces
through which other substrates work with them.

It is *not* a test execution engine, generator, or interpreter —
those are S4 (execution), S3 (generation), and S6 (interpretation),
each a separate substrate with its own concerns. The platform
philosophy is that execution captures truth, intelligence interprets
truth, and representation owns identity. Substrate 2 owns identity.

### 1.1 The deepest invariant

A PrimeQA test case is fundamentally a **structured claim** — an
asserted system truth scoped by the semantic conditions under
which it should hold, realized through one or more replaceable
executable recipes.

This is the substrate's deepest invariant. It decomposes test
cases into six layers (see §2):

- Two **identity-bearing layers** — *asserted system truth* (the
  THEN) and *semantic conditions* (the WHEN). Changing these
  layers changes which test this *is*.
- Four **operational layers** — causal initiation, observation
  realization, execution environment, and provenance. Changing
  these does not change the test's identity; they describe how
  the test is realized, not what it asserts.

Coverage of S1 entities is *derived* from the claim, not authored
separately. Recipes are *replaceable*: S8 can rewrite them when
the org evolves, S3 can regenerate them with different choices,
S4 can select among them based on environment availability —
without changing what the test means.

The boundary between identity-bearing and operational content is
governed by a single discipline rule: **if a value or entity is
referenced inside the claim, it is semantic and identity-bearing;
otherwise it is operational and lives in the recipe.**

**The substrate is designed so operational evolution can occur
without breaking semantic continuity.** This is the substrate's
architectural thesis. Every commitment in §3–§7 — identity_hash
governance, hybrid-by-layer references, the "no autonomous
semantic divergence" invariant, mechanical approval semantics,
the continuity triad — serves this principle: recipes,
references, environments, and pinned versions can all evolve
while the test's meaning stays the test's meaning. Without this
principle, S8 cannot operate autonomously when the org changes;
with it, S8 has bounded authority to keep the substrate alive
without compromising approval state.

### 1.2 The classification framework

Test cases are classified along **four orthogonal discriminators**
(see §3):

- `archetype` — coarse product category (data_behavior /
  configuration / permission / ui / integration)
- `claim_kind` — semantic type of the asserted truth (16 kinds
  locked across 5 archetypes)
- `trigger_kind` — kind of causal initiation (5 kinds, including
  the cross-plane configuration-trigger)
- `recipe_kind` — observability domain of the recipe (5 kinds)

The four axes vary independently — not nested hierarchically.
Each answers a different semantic question: what truth, what
initiates, how observed, what domain. The taxonomies are
deliberately constrained; each is bounded by a purity guardrail
that prevents implementation-mechanism distinctions from
contaminating semantic-form classification.

Seven structural guardrails govern how the substrate evolves:
archetypes are classifications not storage partitions;
claim-kinds model semantic forms not implementation primitives;
recipe-kinds and trigger-kinds maintain similar purity;
trigger-kind and recipe-kind are orthogonal; identity-bearing and
operational layers have distinct lifecycle semantics; and stable
identifiers, `identity_hash`, and `version_seq` model three
distinct continuities (organizational, semantic, supersession).

### 1.3 The data model

The substrate uses a **six-table layout** (see §4) — four core
tables for internal coherence plus two boundary tables for
external interfaces:

- `test_claims` — identity-bearing claims; one row per
  `(test_id, version_seq)`
- `test_recipes` — first-class operational entities; independent
  versioning
- `test_provenance` — append-only history, polymorphic across
  claim and recipe events
- `test_claim_coverage` — semantic linkage layer connecting S2
  to S1 entities
- `test_recipe_runtime_state` — S4 boundary; last-run snapshot
  per recipe (no aggregate statistics)
- `test_requirement_links` — external system boundary; typed
  references to JIRA et al.

The storage pattern is **Pattern D**: envelope columns (row
discriminators, identity fields, status) plus typed JSONB bodies
per layer plus selected hot-path typed columns. Body shape is
dispatched by `(row_discriminator, body_schema_version)` to
Pydantic models.

Validation operates across **three complementary enforcement
layers** — not hierarchical:

- **DB layer** — un-bypassable structural invariants
  (discriminator enums, FK/PK integrity)
- **Pydantic layer** — semantic content validation, ontology
  enforcement via type hierarchy
- **Schema layer** — per-body type definitions and semantic
  field descriptors

Cross-layer reference rules are implemented structurally:
`IdentityBearingRef` (a distinct type, not alias) for
identity-bearing layer fields; `OperationalRef =
Union[PinnedRef, LogicalRef]` for operational layer fields.
Cross-layer violations fail Pydantic validation as type
mismatches.

The **Semantic Transaction Coordinator** is the substrate's
coordination point — elevated (per §10) to semantic OS
infrastructure. All API-driven writes route through it; it
maintains consistency invariants across the six tables, the
multiple body schemas, and the three validation layers within
atomic transactions.

### 1.4 References, lifecycle, and mutation

The substrate references S1 entities through a **hybrid-by-layer**
model (see §5):

- Identity-bearing layers **require pinned references**
  (`entity_id` + `version_seq`)
- Operational layers **default to logical references**
  (`external_id` only) with pinned permitted as opt-in

Cross-layer reference validation is **ontology enforcement** —
not a Pydantic convention. Logical references in identity-bearing
layers are write-time errors; the substrate's commitment to the
semantic-vs-operational lifecycle distinction is structural.

Versioning is **effective-time supersession** with `version_seq`
as canonical authority (see §6). The substrate is not bitemporal —
single time dimension; the term is reserved should transaction-time
tracking ever be added. `identity_hash` is a semantic equivalence
fingerprint (not unique, not a key); operational edits preserve
it, semantic edits change it.

Canonicalization is **governance-critical** (see §6.3). The
policy mechanically determines: S8's autonomous-rewrite authority
boundary, approval invalidation rules, and cross-test semantic
equivalence reasoning. The policy itself is versioned via
`identity_hash_version`; six rules govern the substrate-level
consequences of canonical-form decisions.

Mutations route through three formal paths (see §7):

- **Human edit** — authority of human identity
- **S3 regeneration** — autonomous-but-bounded; hash-changing
  writes produce drafts
- **S8 autonomous rewrite** — bounded by **no autonomous semantic
  divergence**

S8's invariant is mechanical, not layer-based: S8 *can* mutate
identity-bearing layer content (e.g., `version_seq` bumps in
pinned refs), provided the canonical form is preserved. What S8
cannot do is cause hash divergence. The hash-preservation rule
operates *within* identity-bearing layers, not as a fence around
them.

Claim approval is governed by hash change (mechanical, per D-057
/ D-059). Recipe re-approval is a **conservative default** —
every new recipe version requires explicit re-approval, not
because recipes are fundamentally different from claims but
because the substrate currently lacks a mechanical detection
mechanism for recipe-edit-preserves-behavior. Future evolution
could relax this default.

Three Coordinator operations have emerged as **resolution-class**
— composing substrate rules rather than executing simple
queries:

- `get_current_approved_claim` — governance resolution over
  version history
- `get_test_runtime_status` — policy resolution over multi-recipe
  outcomes
- `select_recipe_for_execution` — policy resolution over
  environment, priority, replay mode

Resolution operations are named as a substrate-level pattern;
future resolutions inherit the architectural slot rather than
reinventing it.

### 1.5 Boundaries

Substrate-2 maintains two external boundaries through dedicated
tables:

**The S4 boundary** (see §8) — execution-history is owned by the
future execution substrate. S2 holds a **last-run snapshot per
recipe** (no history, no aggregate statistics) for hot-path
resolution operations; S4 holds the full evidence. S4 reports
outcomes via Coordinator callback (push-based; S2 never queries
S4). Test-level runtime status is a resolution operation
composing recipe-level state with conservative initial policy;
multi-recipe outcome resolution has acknowledged architectural
pressure that future evolution may address.

**The external requirements boundary** (see §9) — substrate-2
links to requirement-management systems (JIRA, Linear, Azure
DevOps) via **external typed references only**. No ticket content
is replicated; the external system remains source of truth.
Multi-kind linkage (`generated_from` / `verifies` / `related_to`)
captures genuinely distinct relationships. Registry-based
evolution of `external_system` identification is reserved.

Both boundaries follow the same principle: **substrate-2 holds
only what it needs for its own coherence; other systems own what
they own.**

### 1.6 The outward surface

The Semantic Transaction Coordinator is **semantic OS
infrastructure** (see §10) — the kernel through which all
substrate operations route. Consuming substrates (S3, S4, S6, S8)
interact with substrate-2 exclusively through Coordinator
interfaces. Direct DB queries bypass the Coordinator's invariants
and may return results that violate substrate guarantees.

**The Coordinator governs substrate semantics; it does not absorb
downstream substrate responsibilities.** S3's generation logic,
S4's execution machinery, S6's interpretation, S8's evolution
decisions — these belong to their respective substrates. The
Coordinator's wide interface surface should not be read as
god-object architecture; it is the substrate-2 surface, scoped to
substrate-2's semantics. Future substrate boundaries are protected
by this discipline. As future substrates ship with their own
coordinators, cross-coordinator coordination patterns may emerge
(per §10.6); the discipline that each Coordinator stays within
its substrate's semantic scope is the architectural commitment
that makes that pattern viable.

The Coordinator exposes **five interface groups** organized by
consumer concern, not by consuming substrate:

1. **Write interfaces** — actor-aware, authority-enforced
2. **Read interfaces** — current-approved vs latest distinction
3. **Equivalence and discovery interfaces** — semantic
   equivalence, S1-entity reverse lookup, requirement-based
   discovery
4. **Runtime state interfaces** — S4 reporting, runtime-state
   snapshot reads, test-level status resolution
5. **Provenance interfaces** — historical event queries

Each interface declares **behavioral contracts**: idempotency
keys, authority requirements, atomicity guarantees, error
contracts, concurrency semantics, performance asymptotics.
Behavioral contracts are substrate-level commitments, not
implementation conventions; wire format (Python-direct, gRPC,
REST) is downstream of the contracts.

### 1.7 The v2.2 transition

PrimeQA v2 supersedes v2.2's bundled test-management schema (see
§11). Per-table disposition:

- `test_cases` and `test_case_versions` → **ABSORB** / **DROP**
  (replaced by `test_claims` + `test_recipes` + effective-time
  supersession)
- `requirements` → **DROP** (external typed reference replaces;
  no content replicated)
- `metadata_impacts` → **DROP** (S1-diff-driven evolution in S8
  covers this)
- `sections`, `test_suites`, `suite_test_cases`, `ba_reviews` →
  **MIGRATE** to future orthogonal substrates (test catalog,
  review workflow)

The MIGRATE dispositions create an explicit gap: substrate-2 v1
doesn't handle sections, suites, or BA reviews. This is **not a
pressure point — it is a deliberate architectural commitment.**
The substrate's coherence is more valuable than short-term v2.2
feature parity. Each MIGRATE-targeted concern represents a
separate substrate's responsibility; absorbing them into S2 would
compromise the substrate boundary.

The gap is real, acceptable, intentional.

### 1.8 What substrate-2 enables

When substrate-2 ships, the platform gains:

- A canonical data structure for test cases that is semantically
  queryable, structurally validated, and human-reviewable
- A versioning model that distinguishes organizational, semantic,
  and supersession continuity
- An identity model that allows S8 to evolve recipes autonomously
  while preserving approval state
- A coverage model that connects test claims to S1 entities,
  enabling impact analysis and evolution propagation
- A boundary against S4 that lets execution and representation
  evolve independently
- An outward surface (the Coordinator) that other substrates
  build against with documented behavioral contracts

S3 generates against substrate-2's schema. S4 reads recipes for
execution and reports outcomes back. S6 reads claims, coverage,
and runtime state for failure attribution. S8 reads coverage to
detect affected tests when S1 evolves and writes hash-preserving
recipe rewrites and pinned-ref version bumps.

The substrate is designed to ship first and enable everything
downstream. Twelve forward-compatibility reservations across the
fifteen design decisions provide deliberate evolution paths
without committing v1 to futures it doesn't yet have.

### 1.9 Reading this document

Sections §2 through §11 are substantive and self-contained; each
can be read independently once §1 is understood. The natural
reading order:

- §2 — deepest invariant (the substrate's foundation)
- §3 — classification (taxonomies and guardrails)
- §4 — data model (tables, validation, Coordinator)
- §5, §6 — references and lifecycle (how content evolves over time)
- §7 — mutation paths (who can change what, and when)
- §8, §9 — boundaries (S4 and external systems)
- §10 — outward surface (the Coordinator interface)
- §11 — v2.2 transition (migration disposition)

The full design rationale lives in `DECISIONS_LOG.md` entries
D-051 through D-065. Each section references the relevant
D-entries; readers wanting the alternatives considered or
pressure-point pushback should consult those.

---

## 2. Deepest invariant — what a test case essentially represents

**Resolution.** Per D-051: a PrimeQA test case is fundamentally a
structured claim — an *asserted system truth* scoped by the
*semantic conditions* under which it should hold, realized through
*one or more executable recipes*.

**Six-layer model.** A test case decomposes into six layers, two
of which are identity-bearing:

| Layer                   | Role                                                       | Identity-bearing? |
| ----------------------- | ---------------------------------------------------------- | :---------------: |
| Asserted system truth   | What the test claims is true (the THEN)                    | YES               |
| Semantic conditions     | Under which conditions the truth should hold (WHEN)        | YES               |
| Causal initiation       | How the triggering cause is operationally realized         | NO (default)      |
| Observation realization | The procedure that observes and asserts                    | NO                |
| Execution environment   | Capability assumptions and setup the recipe relies on      | NO                |
| Provenance              | How the test came to exist                                 | NO                |

*Note: the five-layer model originally established in D-051 was
extended to six layers in D-055 with the addition of the "Causal
initiation" layer. The "Observation realization" layer was
originally named "Execution realization" in D-051 and renamed per
D-055 to better reflect D-054's recipe-kind purity scope.*

*Coverage* is derived from the claim (the union of S1 entities
referenced by the asserted truth and the semantic conditions), not
authored separately.

**Identity-bearing means:** changing this layer changes which test
this *is*. Two records with identical asserted truth and identical
semantic conditions are the same test, regardless of which recipes
realize them, what environment they happen to use, or which JIRA
ticket generated them.

**Replaceability of recipes.** Because recipes are not
identity-bearing, S8 (Evolution) can rewrite recipes autonomously
when the org evolves (e.g., field rename propagation) without
invalidating the test's approved state. S3 (Generation) regenerating
the same claim with different recipe choices produces the same test,
not a new one. S4 (Execution) may select among multiple available
recipes based on environment availability (UI recipe vs API recipe
for the same claim).

**Discipline rule.** The boundary between semantic conditions and
execution environment is governed by a single rule:

> *If a value or entity is referenced inside the claim, it is
> semantic and identity-bearing. Otherwise it is operational and
> lives in the recipe.*

Example: a test claiming "Service Rep users cannot edit
AccountNumber" carries *Service Rep* and *AccountNumber* as semantic
conditions (changing either changes the claim). The specific user
"Bob with employeeId 472" who happens to be logged in during
execution is environment — Alice would do equally well.

**Claim structure is intentionally constrained.** S2 is not a
general system-specification language. Claim structure is bounded
by three forces:

- Human legibility (per `BACKGROUND.md`): claims must be readable
  without execution traces.
- Machine queryability (S6 attribution, S8 evolvability): claims
  must be analyzable by tooling, not arbitrary expression trees.
- Archetype coherence: each archetype has a small natural
  vocabulary of claim-kinds (value-claim, existence-claim,
  capability-claim, state-claim, event-claim); a general logic
  language would dissolve those into anonymous predicates.

The specific structural shape of the claim — fields, types, the
claim-kind taxonomy — is defined in §4 (data model).

**Direction (locked in §4).** Canonical claim units remain atomic
internally; human-facing test concepts may aggregate multiple
atomic claims under one UX-visible test envelope. Whether claim is
structurally allowed composition (AND, sequence) or whether
aggregation lives only at the user-facing layer remains a design
direction; the §4 data model does not foreclose either.

**Downstream consequences for adjacent open questions.**

- *S2-Q-002 (commonality across archetypes).* The six-layer model
  is structurally uniform across all five archetypes; only the
  *form* of claim and recipe varies per archetype.
- *S2-Q-004 (S1 references).* References inside the claim are
  intent-bearing and lean toward pinning (preserves meaning);
  references inside the recipe are operational and lean toward
  logical resolution (preserves liveness). Resolved per D-058
  as hybrid-by-layer; see §5.
- *S2-Q-006 (authority).* The authority boundary is mechanical:
  S8 has autonomous authority bounded by **hash preservation**
  (no autonomous semantic divergence). S8 can mutate any layer,
  including identity-bearing layers, provided the mutation
  preserves canonical form. Mutations producing semantic
  divergence (hash change) require human authority. See §7 and
  D-061.

See `DECISIONS_LOG.md` D-051 and D-055 for rationale and
alternatives considered.

---

## 3. Archetype representation — common substrate, archetype-specific semantic forms

**Resolution.** Per D-052 (extended by D-055 to four discriminators):
a PrimeQA test case is classified along *four orthogonal
discriminators* — `archetype`, `claim_kind`, `trigger_kind`, and
`recipe_kind`. The six-layer model (per D-051, extended by D-055)
is structurally uniform across all five archetypes; within each
layer, the boundary between common and archetype-specific falls
*inside the layer*: a uniform discriminator-bearing envelope holds
an archetype-specific *semantic form*.

**Four orthogonal discriminators.**

- **`archetype`** — the coarse product category: `data_behavior`,
  `configuration`, `permission`, `ui`, or `integration`. Five values
  total. Determines which kinds of recipes are typically applicable
  and which S4 executor handles execution.
- **`claim_kind`** — the fine-grained semantic type of the asserted
  truth. Multiple values per archetype (locked taxonomy below, per
  D-053). Determines the semantic form of the claim.
- **`trigger_kind`** — the kind of causal initiation that sets the
  test scenario in motion. Five values (locked taxonomy below, per
  D-055). Determines the operational realization of the cause.
- **`recipe_kind`** — the kind of executable procedure that observes
  and asserts. Five values (locked taxonomy below, per D-054).
  Determines the observability domain of the recipe.

**Substrate ontology at a glance.** Each discriminator answers a
fundamentally different semantic question:

| Axis | Semantic question |
|---|---|
| `claim_kind` | What truth? |
| `trigger_kind` | What initiates evaluation? |
| `recipe_kind` | How is truth observed? |
| `archetype` | What operational domain? |

*Orthogonal* means independent: the four axes vary independently,
not in a nested hierarchy. Not every combination is meaningful in
practice, but constraints on which combinations are supported live
at the application layer, not the schema layer. This permits
forward compatibility — a future trigger_kind or recipe_kind for
an existing claim_kind requires no schema change.

**Archetypes are classifications, not storage partitions.** The
discriminator names a conceptual category. It does not entail
per-archetype tables, per-archetype migrations, or any other
storage commitment. The storage realization (§4) is a single
envelope-plus-JSONB pattern (Pattern D) that supports the
conceptual classification without mirroring it.

**Claim-kinds model semantic forms, not implementation primitives.**
A second guardrail, parallel to the archetype guardrail above. A
new claim-kind is warranted when it names a different *kind of
truth being asserted* — a different semantic form. A new claim-kind
is *not* warranted when it names a different *Salesforce mechanism*
that realizes the same semantic. Validation-rule-firing and
flow-firing share `automation-effect-claim` (the semantic is "an
automation produced an effect"; the mechanism difference is
captured in a sub-discriminator). Platform-event vs
outbound-message vs callout get separate claim-kinds (different
payload structures and observables, not just different
implementation mechanisms). The test for a proposed new claim-kind:
"Does this name a different *kind of truth*, or just a different
*mechanism*?" Different mechanism alone → not a new claim-kind.
Different truth → maybe a new claim-kind.

**Recipe-kinds classify observability semantics only.** A third
guardrail, parallel to the archetype and claim-kind guardrails
above. A recipe-kind names what a procedure observes and how it
asserts — not what triggers the scenario being tested. Triggering
actions are a separate classification axis, classified by
trigger-kind.

**Trigger-kinds classify causal-initiation semantics only.** A
fourth purity guardrail. A trigger-kind names what kind of cause
initiates the test scenario — not what's observed (that's
recipe-kind), not what's asserted (that's claim-kind), not the
product domain (that's archetype). This guardrail prevents triggers
from absorbing observation logic or implementation-technology
distinctions that belong elsewhere.

**Trigger-kind and recipe-kind are orthogonal.** They classify
different aspects of operational realization — causal initiation
vs observability — and must not be conflated. The same trigger
can be observed via multiple recipes (a `data-mutation-trigger`
followed by both a `data-recipe` AND a `ui-recipe` observing
different effects). The same recipe-kind can observe effects of
multiple trigger-kinds (a `data-recipe` observes effects of
inbound-triggers, data-mutation-triggers, configuration-triggers
alike). Collapsing these axes loses meaningful structural
distinctions and forces artificial pairings.

**Semantic-vs-operational lifecycle distinction.** A sixth guardrail.
Identity-bearing layers (asserted truth, semantic conditions) and
operational layers (causal initiation, observation realization,
execution environment, provenance) have distinct lifecycle semantics.
Changes to identity-bearing layers require human authority and
invalidate prior approval state — the test's meaning is redefined.
Changes to operational layers can be S8-autonomous and preserve
approval state — the test's meaning is unchanged, only its
realization. Storage shape, mutation paths, and authority model
must consistently honor this distinction. See D-056 for structural
treatment and D-057 for versioning treatment.

**Stable identifiers, identity_hash, and version_seq model three
distinct continuities.** A seventh guardrail. The substrate
separates three forms of continuity that most systems collapse
into "latest test version":

- **Stable identifiers** (`test_id`, `recipe_id`) model
  *organizational continuity* — "this is the same artifact across
  its lifetime, regardless of how it evolved."
- **`identity_hash`** models *semantic equivalence* — "this means
  the same thing as that, regardless of which row it is."
- **`version_seq`** models *supersession order* — "this version
  came after that one, regardless of timestamps."

Modeling these separately allows organizational, semantic, and
operational continuity to evolve independently. Storage shape
(D-056) and versioning model (D-057) consistently honor this
separation.

**Structural commonality (A).** Every layer of the six-layer model
is uniformly present in every archetype. Within each layer, the
line between uniform and archetype-specific falls as follows:

| Layer                   | Uniform across archetypes                              | Archetype-specific                                                |
| ----------------------- | ------------------------------------------------------ | ----------------------------------------------------------------- |
| Asserted system truth   | claim_kind discriminator, identity hash, subject refs  | semantic form of the claim body                                   |
| Semantic conditions     | conditions metadata, condition-kind discriminator      | semantic form of condition body (varies with claim_kind)          |
| Causal initiation       | trigger_kind discriminator, trigger metadata           | semantic form of trigger content (varies with trigger_kind)       |
| Observation realization | recipe_kind discriminator, step ordering               | semantic form of step content (varies with recipe_kind)           |
| Execution environment   | environment-kind discriminator                         | capability assumptions and setup the recipe relies on             |
| Provenance              | fully uniform — no archetype-specific provenance       | none                                                              |

Coverage is fully derived from claim and semantic-conditions
references; its uniformity follows automatically.

**Semantic commonality (B).** Established by D-051 and reinforced
here. Every test in every archetype is a structured claim —
asserted system truth scoped by semantic conditions — realized
through one or more replaceable executable recipes, initiated by
one or more triggers. The discipline rule, identity model,
coverage-derivation rule, and authority model from D-051 apply
uniformly. No archetype is an exception to the conceptual model.

**Execution environment as capability assumptions.** Sharpening §2:
the execution-environment layer is not merely "operational setup"
but the recipe's set of *capability assumptions* — what the recipe
assumes is available in order to run. Examples across the
archetypes:

- A UI recipe assumes a browser is available.
- A run-as permission recipe assumes the executor can impersonate
  users.
- A CRUD recipe assumes write permissions on target objects.
- An integration recipe assumes outbound-message inspection.
- Some recipes assume sandbox vs production.
- Some recipes assume specific Salesforce edition features.

This matters operationally because (a) S4's recipe selection
matches the available environment against each recipe's capability
assumptions, and (b) the same claim's UI recipe and API recipe
carry different capability profiles — "which can run here" becomes
a function of environment availability rather than a property of
the claim itself.

**Claim-kind taxonomy (locked per D-053).** 16 kinds across 5
archetypes.

*Data-behavior archetype:* `value-claim`, `state-transition-claim`,
`automation-effect-claim`, `prohibition-claim`.

*Configuration archetype:* `existence-claim`, `property-claim`,
`metadata-relationship-claim`.

*Permission archetype:* `capability-claim`, `sharing-rule-claim`.

*UI archetype:* `element-state-claim`, `navigation-claim`,
`layout-claim`.

*Integration archetype:* `platform-event-claim`,
`outbound-message-claim`, `callout-claim`, `inbound-effect-claim`.

See `DECISIONS_LOG.md` D-053 for per-kind definitions, the
state-transition vs automation-effect distinction, the integration
split rationale, and the consolidations from §3 first-draft seeds.

**Recipe-kind taxonomy (locked per D-054).** Five recipe-kinds,
each classifying an observability domain: `data-recipe`,
`metadata-recipe` (with `metadata-read` and `metadata-write`
modes), `ui-recipe`, `event-subscription-recipe`,
`callout-intercept-recipe`.

The split between `event-subscription-recipe` and
`callout-intercept-recipe` rests on a semantic-vocabulary
distinction, not a transport distinction. Inbound injection is
intentionally not a recipe-kind; it's classified by trigger-kind.

See `DECISIONS_LOG.md` D-054 for per-kind sub-discriminators,
capability assumptions, and the inbound-injection rationale.

**Data-recipe expect-rejection (D-110.1).** A `data-recipe`
mutation step (`CreateStep` first; update/delete deferred) may
carry an `expect_rejection` flag — the operational expression of a
behavioral negative ("this mutation should be rejected"). It is a
field *on the mutation step* (intrinsic to the operation's
outcome, self-describing), not a separate step kind. The flag
carries a **`RejectionExpectation`** primitive — scalars only
(`error_code` / `error_message_pattern`, ≥1 required), with **no
`IdentityBearingRef`**. This is the **operational projection** of
the claim's `expected_rejection: RejectionSignal`: the claim
(identity-bearing layer) carries the *semantic* assertion; the
recipe (operational layer) carries its projection — the two are
authored from one grounded source, the recipe dropping /
stringifying the claim's identity-bearing `error_field`. The
projection is the semantic/operational boundary asserting itself —
operational bodies forbid `IdentityBearingRef`
(`_verify_no_identity_bearing_refs`), so the recipe cannot reuse
`RejectionSignal` directly. Invariant: **at-most-one**
`expect_rejection` per recipe (0 = non-negative, 1 = behavioral
negative; ≥2 rejected) — at-most-one, not exactly-one, so positive
data-recipes (zero) remain valid. Additive on `data-recipe` v1
(greenfield; optional field, no version bump).

**Trigger-kind taxonomy (locked per D-055).** Five trigger-kinds.
The Plane column distinguishes runtime-plane triggers from
model-plane triggers.

| Trigger-kind | Plane | Causal initiation domain |
|---|---|---|
| `inbound-trigger` | runtime | External system pushes payload into Salesforce |
| `data-mutation-trigger` | runtime | DML on records inside Salesforce |
| `ui-trigger` | runtime | User-driven UI action |
| `time-trigger` | runtime | Salesforce mechanisms firing because elapsed-time predicates were met |
| `configuration-trigger` | **model** | Metadata deploy as causal initiation; mutates the org model |

**Trigger-kind identity nuance.** Trigger-kind is *operational by
default* and not identity-bearing. However, D-051's discipline rule
applies: if the trigger mechanism is *semantically asserted* in
the claim, the mechanism becomes part of semantic conditions and
IS identity-bearing.

**One primary trigger per test (default).** A test has one
primary trigger — the causal initiation most directly tied to the
claim's WHEN. Multi-primary trigger scenarios are possible but
rare and usually indicate the test should decompose into multiple
tests.

See `DECISIONS_LOG.md` D-055 for per-kind sub-discriminators,
capability assumptions, and the cross-plane configuration-trigger
treatment.

**Forward compatibility.** Schema accommodates all five archetypes
from day one. Discriminator columns exist; per-archetype semantic
forms come online incrementally as their archetypes ship.

See `DECISIONS_LOG.md` D-052, D-053, D-054, D-055, D-056, and
D-057 for rationale and alternatives considered.

---

## 4. Data model

**Resolution.** Per D-056: substrate-2 uses a four-table layout —
`test_claims`, `test_recipes`, `test_provenance`, `test_claim_coverage`
— extended per D-062 with `test_recipe_runtime_state` and per
D-063 with `test_requirement_links`. Pattern D throughout: envelope
+ JSONB + selected hot-path typed columns. The claim/recipe split
honors D-051's identity model: claims hold the two identity-bearing
layers, recipes are first-class operational entities, both
versioned independently per D-057.

### 4.1 Tables

**`test_claims`** — identity-bearing claim. One row per (test_id,
version_seq).

- `test_id` UUID — stable organizational identifier (constant across versions)
- `version_seq` int — monotonic supersession counter (canonical per D-057)
- `valid_from`, `valid_to` timestamps — informational effective-time window
- `archetype` enum, `claim_kind` enum — canonical row discriminators
- `asserted_truth` JSONB — body for the THEN
- `semantic_conditions` JSONB — body for the WHEN
- `identity_hash` text — semantic equivalence fingerprint (not unique; not a key)
- `identity_hash_version` int — canonicalization policy version that produced this hash (per D-059)
- `status` enum — current approval state (draft / approved / deprecated)
- `created_at`, `updated_at` timestamps
- PK: `(test_id, version_seq)`
- Indexes: `(test_id) WHERE valid_to IS NULL`; `(identity_hash, identity_hash_version)` for equivalence queries (scoped to policy version)

**`test_recipes`** — first-class operational entities. One row per
(recipe_id, version_seq). Independent versioning from claims.

- `recipe_id` UUID — stable organizational identifier
- `version_seq` int — monotonic supersession counter
- `valid_from`, `valid_to` timestamps — informational
- `claim_test_id` UUID — logical FK to `test_claims.test_id`
- `claim_version_seq` int NULL — optional pinning to specific claim version (per §6 pinning semantics)
- `trigger_kind` enum, `recipe_kind` enum — canonical row discriminators
- `causal_initiation` JSONB — body for trigger realization
- `observation_realization` JSONB — body for recipe procedure
- `execution_environment` JSONB — body for capability assumptions + setup
- `priority` int — recipe-selection ordering
- `status` enum — active / deprecated / generated_unapproved / approved
- `created_at`, `updated_at` timestamps
- PK: `(recipe_id, version_seq)`
- Indexes: `(recipe_id) WHERE valid_to IS NULL`; `(claim_test_id) WHERE valid_to IS NULL`

**`test_provenance`** — append-only history. Polymorphic across
claim-level and recipe-level events.

- `id` UUID PK
- `claim_test_id` UUID NULL — FK to `test_claims.test_id` (claim-level event)
- `recipe_id` UUID NULL — FK to `test_recipes.recipe_id` (recipe-level event)
- CHECK constraint: exactly one of (`claim_test_id`, `recipe_id`) is non-NULL
- `event_kind` enum — claim_created / claim_edited / claim_regenerated / claim_approved / claim_deprecated / recipe_created / recipe_edited / recipe_s8_rewrite / recipe_approved / recipe_deprecated / recipe_priority_changed
- `event_data` JSONB
- `event_actor` text
- `event_at` timestamp
- Indexes: `(claim_test_id) WHERE claim_test_id IS NOT NULL` and `(recipe_id) WHERE recipe_id IS NOT NULL` — partial-index pair supporting per-target history lookups (added in Track A's migration beyond this section's literal text; future SPEC update)

**`test_claim_coverage`** — semantic linkage layer connecting S2
claims to S1 entities. Current-only; rederived on each claim
version change per D-057.

- `claim_test_id` UUID — FK to `test_claims.test_id`
- `entity_type` text — 'field', 'object', 'flow', 'profile', etc.
- `entity_id` UUID — FK to S1 entity
- `reference_kind` enum — subject / condition
- PK: `(claim_test_id, entity_id, reference_kind)`
- Indexes: `(entity_id, entity_type)` for reverse lookups (S6/S8)

**`test_recipe_runtime_state`** — last-run snapshot per recipe.
One row per `recipe_id` (NOT per recipe version). Updated by S4
via Coordinator callback (per D-062 §8.3). Pure snapshot; no
aggregate statistics; no history (S4 holds full history).

- `recipe_id` UUID PK — FK to `test_recipes.recipe_id`
- `last_run_id` UUID — opaque reference into S4
- `last_run_at` timestamp
- `last_run_outcome` enum — passed / failed / errored / skipped
- `last_run_recipe_version_seq` int — which recipe version was actually run
- `last_pass_at` timestamp NULL — when did this recipe last pass (NULL if never)
- `last_failure_at` timestamp NULL — when did this recipe last fail
- `updated_at` timestamp
- Indexes: `(last_run_outcome)`, `(last_run_at)` for status-based queries

**`test_requirement_links`** — external typed reference to
requirement-management systems. Multi-kind linkage permitted
(test may be `generated_from` one requirement AND `verifies`
another). No content replicated; external system is source of
truth.

- `test_id` UUID — FK to `test_claims.test_id`
- `external_system` enum — `jira` (extensible: `linear`, `azure_devops`, etc.; reserved for registry-based evolution per D-063 §9.5)
- `external_key` text — e.g., `PROJ-1234`
- `external_version` text NULL — optional version/revision identifier
- `link_kind` enum — `generated_from` / `verifies` / `related_to`
- `linked_at` timestamp
- `linked_by` text — actor (S3, human user, etc.)
- PK: `(test_id, external_system, external_key, link_kind)`
- Indexes: `(external_system, external_key)` for reverse lookups

### 4.2 Architectural roles

The six tables map to architectural concerns:

| Table | Role |
|---|---|
| `test_claims` | Identity-bearing semantic content |
| `test_recipes` | Operational realizations (first-class entities per refinement 5 of sub-cycle 3 design) |
| `test_provenance` | Append-only history; polymorphic across claim and recipe events |
| `test_claim_coverage` | **Semantic linkage layer** — connects S2 to S1; supports S6 attribution, S8 evolution detection, coverage discovery surfaces |
| `test_recipe_runtime_state` | **S2/S4 boundary** — last-run snapshot per recipe; opaque references to S4 for full evidence |
| `test_requirement_links` | **S2/external boundary** — typed references to requirement-management systems (JIRA, etc.); no content replicated |

The four core tables represent substrate-2's internal coherence.
The two boundary tables represent substrate-2's interfaces to
other systems — S4 for execution evidence, JIRA (and future
systems) for requirements.

### 4.3 Discriminator placement and authority

Discriminator columns per D-052 (extended by D-055):

- `archetype`, `claim_kind` live on `test_claims`.
- `trigger_kind`, `recipe_kind` live on `test_recipes`.

**Row discriminator is canonical authority.** JSONB bodies carry
a `kind` field as redundant self-description; the row discriminator
wins on any disagreement. Write-time validation enforces
`body.kind == row.discriminator`.

### 4.4 JSONB body conventions

Every JSONB body carries two top-level keys: `body_schema_version`
(int) and `kind` (string). Body shape per (`row discriminator`,
`body_schema_version`) is defined by Pydantic models per §4.7.

### 4.5 Coverage derivation strategy

App-level derivation. The S3 generator and S8 evolver are the only
writers of `test_claims`; both update `test_claim_coverage` rows
as part of the claim write. Coverage rederivation on version
change follows the delete-and-replace pattern per D-057.

The reference-bearing list fields that drive coverage —
`subject_fields` (state-transition claims), `affected_fields`
(automation-effect claims), and the `conditions` list on
semantic-conditions bodies — carry `ArraySemantics.SET`. Coverage
extraction emits one row per `(entity_type, entity_id, reference_kind)`
triple regardless of source order; canonicalization for
`identity_hash` (§6.3) sorts SET-marked lists before hashing.
Reordering these fields in the body therefore changes neither
coverage rows nor the identity_hash.

### 4.6 Forward-compatibility markers

Two architectural directions reserved without being built:

- **`semantic_conditions` graphification.** Current shape is flat
  JSONB. If conditions develop compositional structure, this may
  evolve to a relational or graph representation.
- **Operational linkage layer for recipes.** Parallel to
  `test_claim_coverage` but for recipe-level operational
  dependencies. Forward-compatible territory, not realized today.

See `DECISIONS_LOG.md` D-056 for rationale and alternatives
considered.

### 4.7 Validation layering and the Semantic Transaction Coordinator

**Resolution.** Per D-060: substrate-2's validation operates
across three complementary enforcement layers, coordinated
through the Semantic Transaction Coordinator (elevated per D-064
to semantic OS infrastructure; see §10).

#### 4.7.1 Three complementary enforcement layers

| Layer | Scope | Bypass characteristic | Evolution speed |
|---|---|---|---|
| DB | Structural invariants (discriminator enums, FK/PK integrity, CHECK constraints) | Un-bypassable | Slow (migration overhead) |
| Pydantic | Semantic content validation (body shape, cross-field rules, ontology enforcement, reference shapes) | Bypassable by raw SQL | Fast (code change) |
| Schema | Per-body type definitions, semantic field descriptors, discriminator dispatch | (not directly enforcing) | Fast (code change) |

Substrate-critical invariants are deliberately **double-enforced**
across layers; the redundancy is intentional.

#### 4.7.2 Pydantic model organization

Two-level discriminator dispatch: row discriminator selects the
family of body models; `body_schema_version` selects the specific
version within that family. Pydantic 2 discriminated unions
throughout.

#### 4.7.3 Reference type hierarchy with semantic role preservation

```python
class PinnedRef(BaseModel):
    ref_kind: Literal["pinned"]
    entity_type: str
    entity_id: UUID
    version_seq: int
    external_id: str

class LogicalRef(BaseModel):
    ref_kind: Literal["logical"]
    entity_type: str
    external_id: str

class IdentityBearingRef(PinnedRef):
    """Distinct type, not alias — preserves semantic-role marker."""
    pass

OperationalRef = Annotated[
    Union[PinnedRef, LogicalRef],
    Field(discriminator="ref_kind"),
]
```

The hybrid-by-layer rule (per D-058 §5.1) is implemented as
structural type enforcement: identity-bearing layer body models
declare fields with `IdentityBearingRef`; operational layer body
models declare fields with `OperationalRef`. Cross-layer
violations fail Pydantic validation as type mismatches.

#### 4.7.4 Semantic field descriptors

Per-field semantic metadata uses `Annotated[T, Marker]`
consistently. Today's marker: `ArraySemantics.SET`. Future markers
(hash-contribution annotations, identity-contribution annotations)
follow the same pattern.

#### 4.7.5 The Semantic Transaction Coordinator

The Semantic Transaction Coordinator is **semantic OS
infrastructure** for substrate-2 (per D-064 framing; see §10). It
coordinates: body-shape consistency, row-body discriminator
consistency, cross-layer ontology consistency, canonicalization-hash
consistency, coverage-claim consistency, provenance-event
consistency, mutation-path routing and authority enforcement,
runtime-state updates from S4, and resolution-class operations.

All API-driven writes route through the Coordinator. Direct DB
writes bypass Pydantic-layer invariants; DB-layer invariants still
apply.

#### 4.7.6 Write-flow orchestration

Canonical write sequence: discriminator validation → body dispatch
→ body validation → body-row consistency → cross-layer ontology
validation → cross-body validation → canonicalization → hash
computation → coverage extraction → authority enforcement → DB
write transaction.

Any step's failure rolls back the transaction.

The body-row discriminator-consistency step (step 4) is
structurally defensive for typed Pydantic body input — concrete
body classes pin `kind` via `Literal[...]`, so a body whose
internal `kind` disagrees with the row discriminator cannot be
constructed in the first place; the inconsistency is caught at
the registry-dispatch step (step 2) instead. The step is
preserved for canonical ordering and for the dict-input variant
where the check is reachable. The same reasoning applies to the
recipe-side cross-body validation step (§4.7.6, recipes), where
step 5 (cross-layer ontology) is structurally defensive against
`IdentityBearingRef` leaking into operational-layer bodies —
Pydantic's `OperationalRef` union resolution prevents that case
at construction.

#### 4.7.7 Read-path semantics

Read flow: row fetched from DB → JSONB body parsed → Pydantic
model dispatched → body validated → returned as typed Pydantic
object.

Hash is not recomputed on read within a given
`(identity_hash, identity_hash_version)` regime. Pinned-ref
resolution is lazy. Hash audit operations run as separate
maintenance jobs.

#### 4.7.8 Read-path error types

Five error categories with distinct handling:

| Error type | Cause | Severity |
|---|---|---|
| `SchemaIncompatibilityError` | No Pydantic model exists for `(kind, body_schema_version)` | Graceful degradation |
| `BodyCorruptionError` | Model exists but body fails validation | Incident |
| `OntologyViolationError` (write-time) | Cross-layer reference-kind rule violated | Architectural rejection |
| `AuthorityViolationError` (write-time) | Writing actor lacks authority for this mutation given hash-change status | Architectural rejection |
| `ValidationError` (Pydantic standard) | Routine field validation failure (write-time) | Soft rejection |

#### 4.7.9 Migration handling

Body-schema-version migration and canonicalization-policy
migration are both governance-level operations: explicit, audited,
recorded in provenance. Migration authors provide new Pydantic
models, migration functions, and canonical-form preservation
declarations per D-059 Rule 5.

See `DECISIONS_LOG.md` D-060 for rationale and alternatives
considered.

---

## 5. References to S1 entities

**Resolution.** Per D-058: substrate-2 uses **hybrid-by-layer**
reference resolution. Identity-bearing layers require pinned
references; operational layers default to logical references,
with pinned permitted as opt-in.

### 5.1 The hybrid-by-layer rule

| Layer | Reference kind | Rule |
|---|---|---|
| Asserted system truth | pinned | **required** |
| Semantic conditions | pinned | **required** |
| Causal initiation | logical | **default**; pinned opt-in |
| Observation realization | logical | **default**; pinned opt-in |
| Execution environment | logical | **default**; pinned opt-in |
| Provenance | (no entity references in body) | n/a |

The rule maps directly onto the sixth guardrail
(semantic-vs-operational lifecycle distinction).

### 5.2 Reference shapes

References live inside JSONB bodies as typed objects with
`ref_kind` discriminator (`pinned` or `logical`). Pinned carries
`entity_type` + `entity_id` + `version_seq` + informational
`external_id`. Logical carries `entity_type` + `external_id` only.

### 5.3 Identity_hash canonicalization

Pinned references contribute `entity_id` only to the hash input;
`version_seq` is operational metadata and is excluded. Logical
references do not appear in identity-bearing layers (rejected by
validation per §5.5).

Consequence: S8 can update pinned-ref `version_seq` forward (when
entity evolution is blessed; see §5.7) with hash preserved and
approval state intact. Full canonicalization mechanics in §6.3
per D-059.

### 5.4 Coverage derivation

`test_claim_coverage` extracts pinned references from
identity-bearing layers only. Operational layer references are
NOT in coverage; they are operational dependencies (D-056's
reserved operational linkage layer).

### 5.5 Cross-layer reference validation as ontology enforcement

Per D-060 §4.7.3, cross-layer validation is implemented
structurally via Pydantic type hierarchy. Identity-bearing layers
reject logical references at write time as type mismatches.
Operational layers accept both kinds.

### 5.6 external_id drift modes

S8's drift detection handles six modes explicitly: rename, move,
replace, namespace shift, inheritance change,
metadata-resolution quirks. Pinned references survive most drift
modes; logical references are evolvable but drift-vulnerable. See
D-058 §5.6.

### 5.7 Reference semantics under replay modes

Per D-057's reserved replay modes (§6.8):

- **Historical replay:** pinned refs resolve at pinned
  `(entity_id, version_seq)`; logical refs resolve at historical
  S1 state.
- **Semantic replay:** logical refs resolve to current S1; pinned
  refs follow forward only via S8-blessed transitions per D-058
  + two-gate evaluation per D-059 §6.3.9 Rule 3.

### 5.8 Forward-compatibility reservations

Three architectural directions reserved without action in v1:
weighted semantic linkage; operational linkage layer for recipes;
reference resolution policies.

See `DECISIONS_LOG.md` D-058 for rationale and alternatives
considered.

---

## 6. Lifecycle and versioning

**Resolution.** Per D-057: substrate-2 uses **effective-time
supersession** — single time dimension, `version_seq` as canonical
supersession authority, `valid_to` as denormalized convenience.

### 6.1 Invariant hierarchy

`version_seq` defines supersession truth; `valid_to` is denormalized
convenience. In any scenario where they disagree, `version_seq`
ordering is authoritative.

### 6.2 The continuity triad

Three distinct continuities (per seventh guardrail in §3): stable
identifiers (organizational), `identity_hash` (semantic
equivalence), `version_seq` (supersession order).

### 6.3 `identity_hash` semantics, canonicalization, and governance contract

`identity_hash` is the **semantic equivalence fingerprint**.
Operational edits preserve hash; semantic edits change it and
invalidate approval.

Per D-059, canonicalization mechanics and the resulting governance
contract are defined as follows.

#### 6.3.1 Hash input scope

Four components: `archetype`, `claim_kind`, canonicalized
`asserted_truth` JSONB, canonicalized `semantic_conditions` JSONB.
Out of scope: `test_id`, `version_seq`, temporal columns, `status`,
`identity_hash` itself, all recipe content, all coverage content,
runtime state, requirement links, and `body_schema_version`.

#### 6.3.2 Canonicalization rules (strict)

Alphabetical recursive key ordering; whitespace stripped between
tokens; UTF-8 case-sensitive; canonical JSON numeric form; null
vs missing distinguished; empty arrays vs missing distinguished;
lowercase boolean literals.

#### 6.3.3 Reference canonicalization

Pinned references canonicalize to `{ "entity_id": "<uuid>",
"entity_type": "<type>" }` only. `version_seq`, `external_id`,
and `ref_kind` are excluded.

#### 6.3.4 Array semantics (schema-declared)

Body schemas declare per-field array semantics as `ordered`
(default) or `set` (sort before hash).

#### 6.3.5 `body_schema_version` handling

Excluded from the hash input. Storage metadata, not semantic
content.

#### 6.3.6 Hash algorithm

SHA-256, hex-encoded → 64-character string stored in
`identity_hash` column. Computed at write time by the Semantic
Transaction Coordinator.

#### 6.3.7 Canonicalization policy versioning

The canonicalization policy itself is versioned via
`identity_hash_version` column on `test_claims`. Hashes are only
directly comparable between rows sharing the same
`identity_hash_version`.

#### 6.3.8 Storage of canonical form

Canonicalized JSONB is stored on the row (not the original
non-canonical input).

#### 6.3.9 Governance contract (six rules)

**Rule 1 — S8 autonomy boundary (no autonomous semantic divergence).**
S8 may autonomously create new claim versions if and only if the
new version's canonical form equals the predecessor's.

**Rule 2 — Approval invalidation.** Hash change → approval
invalidated; new version begins in `draft` status.

**Rule 3 — S8 evolution through entity changes (two-gate
evaluation).** Gate 1 (hash preservation, mechanical) + Gate 2
(entity-evolution semantic compatibility, judgmental) both must
pass for autonomous update.

**Rule 4 — Cross-test semantic equivalence (scoped).** Two claims
with same `identity_hash` AND same `identity_hash_version` are
semantically equivalent under that canonicalization policy.

**Rule 5 — Schema migration discipline.** Body schema migrations
declare canonical-form preservation explicitly.

**Rule 6 — Canonicalization policy migration.** Policy evolution
is governance-level. Re-hashing existing rows under new policy is
an explicit operation.

#### 6.3.10 Semantic projection fields (reservation)

For v1, the entire canonicalized body contributes to the hash.
Future: schema-declared per-field hash-contribution annotation.
Trajectory toward semantic field descriptors framework per D-060
§4.7.4.

#### 6.3.11 Edge cases

Hash collision: cryptographically negligible with SHA-256.
Non-canonical write input: Coordinator canonicalizes before
hashing. Hash computation timing: at write time only; never
recomputed on read within a regime. Policy migration: explicit
governed operation.

See `DECISIONS_LOG.md` D-059 for rationale and alternatives
considered.

### 6.4 Recipe-to-claim FK semantics

Default: logical resolution (recipe's `claim_test_id` references
the claim's `test_id`, not specific version). Optional pinning via
`claim_version_seq` for reproducibility-critical contexts.

The recipe-to-claim FK is **logical-only**: there is no
DB-level foreign-key constraint (recipes reference test_id,
which is not unique in `test_claims` — the PK is
`(test_id, version_seq)`). The Coordinator enforces referential
integrity at write time per §4.7.6 step 6: the recipe's
`claim_test_id` must resolve to an existing claim (any
version_seq); when `claim_version_seq` is provided, that specific
version must exist. Either failure raises
`OntologyViolationError`. See D-α §A6 for the substrate-isolation
rationale.

### 6.5 Coverage rederivation

`test_claim_coverage` is current-only. When a new claim version
supersedes its predecessor, coverage rows are deleted and
rederived.

### 6.6 Approval state lifecycle

Approval state is dual-tracked: current state on
`test_claims.status`; history in `test_provenance`. Approval
invalidation triggers on `identity_hash` change between versions.
Mutation paths and per-path approval impact specified in §7 per
D-061.

**Deprecation is orthogonal to supersession.** The `valid_to`
window closes on **supersession** (a new version_seq for the
same test_id); `status` (`draft` / `approved` / `deprecated`) is
a separate semantic-lifecycle axis. A deprecated row may still
have `valid_to IS NULL` (current valid version, deprecated
status). `get_current_approved_claim` walks back through history
by `status='approved'` filter; it skips deprecated rows
regardless of their `valid_to` state. See D-068 for the
in-place mutation rationale (status changes do not bump
version_seq).

### 6.7 Archival policy

**No archival in v1.** Semantic lineage continuity is more
valuable than retention optimization. Architectural commitment,
not cost-driven decision.

### 6.8 Replay modes (storage support; engine downstream)

Two distinct replay modes supported by the storage shape (replay
engine is S4 territory):

- **Historical replay** — pinned refs resolve at pinned
  `(entity_id, version_seq)`; logical refs resolve at historical
  S1 state.
- **Semantic replay** — logical refs resolve to current S1;
  pinned refs follow forward only via S8-blessed transitions
  (per D-058 + two-gate evaluation per D-059 §6.3.9 Rule 3).

### 6.9 Reservations (forward-compatible directions)

Three architectural directions reserved without action in v1:
replay-sensitive recipe selection, version-granular provenance,
reference resolution policies.

See `DECISIONS_LOG.md` D-057 for rationale and alternatives
considered.

---

## 7. Mutation paths and authority over meaning

**Resolution.** Per D-061: substrate-2 defines three formal
mutation paths (human / S3 / S8), each routed by the Semantic
Transaction Coordinator (§4.7.5) with per-path authority rules.
S8's invariant is **no autonomous semantic divergence** —
mechanically detected by `identity_hash` change. Claim approval
is governed by hash change (mechanical); recipe re-approval is a
**conservative default** awaiting future detection mechanisms.

### 7.1 The three mutation paths

- **Human edit** — A QA engineer, BA, or other authorized human
  directly edits a claim or recipe through the substrate's API.
- **S3 regeneration** — The future generation substrate produces
  new claim or recipe content. S3 is an autonomous-but-bounded
  actor.
- **S8 autonomous rewrite** — The future evolution substrate
  responds to S1 entity changes.

All three paths route through the Semantic Transaction
Coordinator.

**S4 is recognized as a fourth actor for boundary callback only.**
The actor taxonomy is `Literal["human", "s3", "s8", "s4"]`. The
`s4` value is accepted ONLY by `report_run_outcome` (per §8.3);
all other Coordinator methods that take an actor reject `s4`
with `AuthorityViolationError`. S4 is a runtime-state-reporting
callback identity, not a substrate-2 mutation actor in the
sense of the three paths above. See D-066.

### 7.2 Authority model — no autonomous semantic divergence

The substrate's universal autonomy rule (per §6.3.9 Rule 1):
S8 (and S3 on claim writes) may autonomously create new versions
**if and only if the new version preserves canonical form** —
`identity_hash` and `identity_hash_version` both unchanged. The
invariant is **mechanical, not layer-based.** S8 can mutate any
layer including identity-bearing layers (e.g., version_seq bumps
inside `asserted_truth`); what S8 cannot do is cause semantic
divergence.

Per-actor authority scope per D-061 §7.2 table.

### 7.3 Identity continuity and semantic continuity

Identity continuity = stable identifier continuity. Persists
across all mutations.

Semantic continuity = `identity_hash` continuity (scoped to
`identity_hash_version`). Different hash = semantically different
test, even with same `test_id`.

Orthogonal dimensions.

### 7.4 Trust boundary by path

Two asymmetries:

- **Claim approval is mechanical** — governed by `identity_hash`
  change.
- **Recipe re-approval is a conservative default** — every new
  recipe version requires explicit re-approval, not because
  recipes are fundamentally different from claims but because the
  substrate currently lacks mechanical detection for "this recipe
  edit didn't meaningfully change behavior." Future evolution
  could relax this default.

### 7.5 Linear supersession and current-approved resolution

**Linear supersession** — each new version supersedes the prior
in `version_seq`. "Latest" vs "current-approved" as distinct
query notions.

**Current-approved as governance resolution.**
`get_current_approved_claim(test_id)` is a Coordinator governance
operation interpreting version history per substrate rules — not
a simple status lookup.

### 7.6 Test-level approval as derived composition

`get_test_approval_status(test_id)` returns: `fully_approved` /
`claim_approved_recipe_pending` / `draft`. Composition logic
lives in Coordinator; not denormalized to schema.

### 7.7 Edge cases

Concurrent structural writes (DB conflict + retry). Concurrent
semantic conflicts (linear supersession; future merge/rebase
reserved). S3 hash-preserving regeneration as no-op skip.
Cross-test semantic equivalence (query, no auto-merge). S8
detects deleted-entity reference (surfaces for review). Approval
rollback via new draft version, not status mutation.

### 7.8 Forward-compatibility reservations

Four architectural directions reserved without action in v1:
recipe approval auto-preservation; merge/rebase semantics for
concurrent semantic conflicts; provenance streams as named
taxonomy; deprecation taxonomy as sub-status.

See `DECISIONS_LOG.md` D-061 for rationale and alternatives
considered.

---

## 8. Execution-history boundary against S4

**Resolution.** Per D-062: substrate-2's boundary with the future
execution substrate (S4) is the **last-run snapshot** pattern.
S2 holds minimal denormalized state per recipe via the
`test_recipe_runtime_state` table; S4 holds the full evidence and
history. S4 pushes updates to S2 via Coordinator callback. Test-level
runtime status is a **resolution operation** composing recipe-level
state.

### 8.1 The substrate boundary

Platform philosophy distinguishes: **execution captures truth**
(S4's domain), **intelligence interprets truth** (S6's domain),
**representation owns identity** (S2's domain).

S2 must NOT replicate S4's evidence. But S2 benefits from minimal
denormalized state for hot-path queries that would otherwise force
every status query to join with S4.

The boundary commitment: **S2 holds only what it needs for its
own resolution operations; S4 holds everything else.**

### 8.2 The runtime-state snapshot

The `test_recipe_runtime_state` table (defined in §4.1) holds one
row per `recipe_id` (NOT per recipe version). Pure snapshot — no
aggregate statistics, no history.

Per-recipe, not per-recipe-version. Separate table, not columns
on `test_recipes` — the boundary must be visible in the schema.

**Cross-version persistence.** Because the row is keyed by
`recipe_id` alone, a new recipe version (different
`version_seq`, same `recipe_id`) inherits the existing runtime
state row. The row's `last_run_recipe_version_seq` column
records which version was actually run, so consumers detect
"runtime state predates the current recipe content" by
comparing `last_run_recipe_version_seq` to the recipe's current
`version_seq`. A subsequent `report_run_outcome` on the new
version updates this column to reflect the run.

### 8.3 Push-based S4 integration

S4 reports run outcomes via Coordinator callback:

```
coordinator.report_run_outcome(
    actor=S4, run_id, recipe_id, recipe_version_seq,
    outcome, ran_at,
)
```

S2 never queries S4. S4 pushes; S2 ingests. Idempotent on `run_id`.

### 8.4 Test-level runtime status as resolution operation

`coordinator.get_test_runtime_status(test_id)` returns one of:
`passing`, `failing`, `untested`, `mixed`. Conservative initial
policy: "any failure → failing; all pass → passing; otherwise
mixed."

This is **resolution**, not lookup — composing recipe-level state
per substrate rules. Per D-064, resolution-class operations are
first-class substrate concepts.

### 8.5 Multi-recipe outcome resolution has pressure

The composition rule in §8.4 is conservative and initial. Multi-recipe
outcomes have genuine ambiguity. The substrate provides both raw
recipe-level state AND derived test-level composition. Consumers
needing different composition policies compose against the raw
recipe state rather than the substrate's default.

### 8.6 Forward-compatibility reservations

Two architectural directions reserved without action in v1:

- **Richer runtime-state resolution** — recipe priority weighting,
  primary-recipe designation, outcome-aggregation policies.
- **Run history beyond last-run** — for flakiness detection;
  today deferred to S4 or a future flakiness-detection substrate.

See `DECISIONS_LOG.md` D-062 for rationale and alternatives
considered.

---

## 9. Requirement linkage

**Resolution.** Per D-063: substrate-2 links to external
requirement-management systems via **external typed references
only**. No ticket content is replicated in PrimeQA; the external
system remains the source of truth. The `test_requirement_links`
table provides multi-kind linkage (`generated_from` / `verifies`
/ `related_to`).

### 9.1 External typed reference model

Substrate-2's role re requirements is **linkage, not ownership.**
Requirements are external to PrimeQA's domain.

Why no content replication: content goes stale; mission boundary;
sync overhead.

### 9.2 The `test_requirement_links` table

Defined in §4.1. Multi-kind linkage permitted. PK:
`(test_id, external_system, external_key, link_kind)`.

### 9.3 No content replication

The substrate stores **only the link**, never the content.
Downstream consumers query external system's API directly.
Forward-compat: a content-cache layer could be added as a
separate concern without changing substrate-2's commitment.

### 9.4 Multi-kind linkage semantics

The three link kinds:

- **`generated_from`** — S3 generated this test in response to
  this requirement.
- **`verifies`** — This test contributes to verifying this
  requirement.
- **`related_to`** — Loose association catch-all.

A single test may have multiple link kinds to the same
requirement, or one link kind across multiple requirements.

### 9.5 Forward-compatibility reservations

Three architectural directions reserved without action in v1:

- **Registry-based `external_system`** — schema-shape commitment
  today is "typed identifier" (enum or FK depending on stage), not
  an irrevocable type choice.
- **Sprint / release / project associations** — external-system
  concerns; not substrate-2 schema.
- **Bidirectional sync (PrimeQA → external)** — out of
  substrate-2 scope.

### 9.6 Coordinator-side enforcement

`link_requirement` rejects unknown `external_system` values with
`ValueError` (v1 enum membership: `{"jira"}`; extensions require
a migration to add an enum value AND an update to the
Coordinator's allow-list constant). The same method rejects an
unknown `test_id` with `OntologyViolationError` per §6.4's
logical-FK contract. `unlink_requirement` is idempotent on
missing rows (returns `None`). Re-linking with a changed
`external_version` updates that column only; `linked_by` and
`linked_at` are preserved to capture original authorship.

See `DECISIONS_LOG.md` D-063 for rationale and alternatives
considered.

---

## 10. Outward surfaces (consumed by S3, S4, S6, S8)

**Resolution.** Per D-064: substrate-2's outward surface is the
**Semantic Transaction Coordinator**, framed as **semantic OS
infrastructure** rather than as a substrate-internal component.
Five interface groups, each with explicit **behavioral contracts**.
Three Coordinator-level operations named **resolution-class
operations** — first-class substrate concepts. Wire format
unspecified at substrate level; behavioral contracts are not.

### 10.1 The Coordinator as semantic OS infrastructure

The Semantic Transaction Coordinator is **semantic OS
infrastructure** — the kernel through which all substrate
operations route. Consuming substrates interact with substrate-2
exclusively through Coordinator interfaces. Direct DB queries
bypass the Coordinator's invariants and may return results that
violate substrate guarantees.

Consequences of this framing:

- Interface stability is **foundational** — changes ripple to all
  consuming substrates.
- Behavioral contracts (§10.3) are first-class architectural
  commitments, not implementation conventions.
- Future substrates (S1 Coordinator, S4 Coordinator) may form a
  Coordinator family with cross-coordinator concerns.

### 10.2 Five interface groups

Organized by consumer concern, not by consuming substrate. The
Phase 4 implementation (Tracks A–E) realized 22 Coordinator
methods plus 3 free-function authority helpers across these
groups:

1. **Write (7 methods)** — `write_claim`, `write_recipe`,
   `promote_claim_to_approved` (human-only),
   `promote_recipe_to_approved` (human-only), `deprecate_claim`
   (human-only), `deprecate_recipe` (human-only),
   `change_recipe_priority` (any actor except `s4`).

2. **Read (5 methods)** — `get_latest_claim`,
   `get_claim_version`, `list_active_recipes`,
   `get_recipe_latest`, `get_recipe_version`.

3. **Discovery (3 methods)** — `query_equivalent_claims`,
   `list_tests_affected_by_entity` (drives via coverage),
   `list_tests_by_requirement`.

4. **Resolution (3 methods)** — `get_current_approved_claim`,
   `get_test_runtime_status`, `select_recipe_for_execution`.
   Per §10.4, these compose substrate rules into single
   answers; they are first-class substrate concepts, not raw
   lookups.

5. **Boundary (4 methods)** — `report_run_outcome` (S4-only),
   `get_recipe_runtime_state`, `link_requirement`,
   `unlink_requirement`.

Plus 3 authority helpers as free functions:
`check_claim_write_authority`, `check_recipe_write_authority`,
`check_runtime_state_write_authority`. The first two return an
`AuthorityDecision` carrying the policy outcome; the third
raises directly because the s4-only contract has no decision
space.

**Reservations not realized in Phase 4** (forward-compat
placeholders, not commitments revoked):

- `surface_unblessed_transition` (S8-only) — the v1
  Coordinator does not implement this; S8's deletion-detection
  workflow surfaces via existing mutation paths.
- `get_provenance` / `get_recipe_provenance` — provenance is
  written by all mutation methods but no read interface ships
  in Phase 4. Direct queries against `test_provenance` remain
  available; a typed read API can be added in a future track.

Likely consumers (forward-pointer, not a commitment):
`get_provenance` for S3 generation (inspecting prior
generation attempts), `get_provenance` /
`get_recipe_provenance` for S6 intelligence (audit surfaces
over the substrate's mutation history), and
`surface_unblessed_transition` for S8 evolution (the
"I detected drift; needs human review" surface). The
reservations exist so the interface shape is ready when
downstream substrates need it.

### 10.3 Behavioral contracts per interface

Substrate-level commitments, not implementation conventions. Each
interface declares idempotency keys, authority requirements,
atomicity guarantees, error contracts, concurrency semantics, and
performance asymptotics (where commitments are warranted).

See D-064 for per-contract specification.

### 10.4 Resolution-class operations

Three Coordinator interfaces are **resolution-class operations**:

| Operation | Composes |
|---|---|
| `get_current_approved_claim` (D-061) | Status events, deprecation, policy-version scenarios |
| `get_test_runtime_status` (D-062) | Recipe outcomes, approval state, conservative initial policy |
| `select_recipe_for_execution` (D-064) | Environment matching, priority, approval state, replay mode, S8-blessing |

Distinguished from lookups by composition over substrate rules,
governance/policy implications, and future-extensibility. Named
as a substrate-level pattern; future resolution operations will
inherit this slot rather than reinvent it.

The Phase 4 v1 implementations use the **conservative policies**
documented in §7.5 (current-approved walks past drafts and
deprecated rows), §8.4 (test-runtime status: "any failure →
failing; all pass → passing; skipped doesn't downgrade;
generated_unapproved recipes excluded"), and the
membership-based environment matcher described above. §8.5's
acknowledged pressure on multi-recipe composition is unresolved
at v1; §8.6 + §6.9 reserve richer policies for future
evolution.

### 10.5 Wire format reservation

The substrate's commitment is the Coordinator interface and its
behavioral contracts. Concrete wire formats (Python-direct, gRPC,
REST) are deployment concerns. Wire formats may multiply without
changing the substrate's commitment.

### 10.6 Forward-compatibility reservations

Three architectural directions reserved without action in v1:
cross-substrate Coordinator concerns; API versioning; behavioral
contract evolution.

See `DECISIONS_LOG.md` D-064 for rationale and alternatives
considered.

---

## 11. Disposition of v2.2 test-management tables

**Resolution.** Per D-065: each v2.2 test-management table is
dispositioned for the v2 substrate-based architecture. Two tables
are absorbed by substrate-2; three are dropped; four migrate to
orthogonal substrates (TBD); one is dropped in favor of S8
territory. The dispositions reflect an **intentional architectural
trade-off** — short-term v2.2 feature parity sacrificed for
long-term substrate coherence.

### 11.1 Disposition vocabulary

- **ABSORB** — Content moves into substrate-2's new schema.
- **DROP** — Content is not retained in PrimeQA v2.
- **MIGRATE** — Content lives in a separate (TBD) substrate in v2.

### 11.2 Per-table disposition

| v2.2 Table | Disposition | Rationale |
|---|---|---|
| `sections` | MIGRATE | Organizational/curation concern; future "test catalog" substrate. |
| `requirements` | DROP | Per D-063: external typed reference replaces. |
| `test_cases` | ABSORB | Replaced by `test_claims` + `test_recipes` (per D-056). |
| `test_case_versions` | DROP | Replaced by effective-time supersession (per D-057). |
| `test_suites` | MIGRATE | Curation; future "test catalog" substrate. |
| `suite_test_cases` | MIGRATE | Same as `test_suites`. |
| `ba_reviews` | MIGRATE | Workflow concept; future "review workflow" substrate. |
| `metadata_impacts` | DROP | S8 territory; derived from S1 bitemporal history. |

### 11.3 Intentional architectural trade-off

The four MIGRATE dispositions create an explicit gap: substrate-2
v1 doesn't handle sections, suites, or BA reviews. This is **not
a pressure point to be mitigated — it is a deliberate architectural
commitment.**

- Short-term cost: v2.2 features unavailable in v2 until
  orthogonal substrates ship
- Long-term gain: each concern lives in its own substrate with
  clean boundaries

Each MIGRATE-targeted concern represents a *separate substrate's
responsibility*. Absorbing them into S2 would compromise the
substrate boundary.

**The gap is real; the gap is acceptable; the gap is intentional.**

### 11.4 Migration strategy (high-level)

For ABSORB: v2.2 `test_cases` + `test_case_versions` → v2
`test_claims` + `test_recipes` via S3-assisted decomposition.

For DROP: content not migrated; replaced by mechanisms documented
elsewhere (D-063 for requirements; effective-time supersession
for test_case_versions; S8 territory for metadata_impacts).

For MIGRATE: out of substrate-2's v1 scope. Migration deferred
until receiving substrates ship.

**Detailed migration execution** is implementation work
post-Phase-3.

### 11.5 Forward-compatibility reservations

The MIGRATE dispositions create implicit dependencies on future
substrates: test catalog substrate and review workflow substrate.
Both ship later. Substrate-2 v1 ships first.

See `DECISIONS_LOG.md` D-065 for rationale and alternatives
considered.

---

## 12. Implementation notes (Phase 4)

This section captures findings, conventions, and gotchas that
emerged during the Phase 4 implementation (Tracks A through E).
Substrate behavior is defined by §1–§11; this section is
descriptive, not prescriptive.

### 12.0 Phase 4 summary

Substrate-2 Phase 4 implemented the substrate end-to-end across
17 commits. Outcomes:

- **22 Coordinator methods** across the 5 interface groups defined
  in §10.2 (7 write, 5 read, 3 discovery, 3 resolution,
  4 boundary) plus 3 free-function authority helpers.
- **1148 tests** (872 unit + 276 integration; 3 documented skips
  for defence-in-depth paths unreachable for typed Pydantic
  input).
- **Five architectural commitments verified mechanically**:
  - **No autonomous semantic divergence** (D-061 §7.2): S8 cannot
    write hash-changing claims; the authority check rejects.
  - **No autonomous execution** (D-064 + §10.4):
    `generated_unapproved` recipes are ineligible for
    `select_recipe_for_execution` until a human promotes.
  - **Conservative re-approval default** (§7.4): every new recipe
    version requires explicit re-approval regardless of actor.
  - **S3 same-hash no-op** (§7.7): S3 regenerating identical
    canonical form produces a no-op skip (no new version, no
    provenance event).
  - **First-write-wins idempotency** (§8.3): `report_run_outcome`
    preserves the first valid report for a given `run_id`; late
    corrections do not overwrite.
- **18 end-to-end scenarios** across 5 categories (lifecycle,
  version evolution, multi-recipe, S4 boundary, discovery)
  verifying composition correctness across the 22 methods. Zero
  composition bugs surfaced; one test-side fixture-assertion
  alignment was the only adjustment.

The implementation deltas from Phase 3 design are minor and
captured inline in the relevant sections (§4.1, §4.5, §4.7.6,
§6.4, §6.6, §7.1, §8.2, §9.6, §10.2, §10.4); the substrate as
specified in Phase 3 is what was built.

### 12.1 Test convention

Substrate-2's integration tests use **local PostgreSQL with
per-test transactional rollback**. This differs from
substrate-1's against-actual-Railway pattern + prefix-based
cleanup. Rationale: substrate-2's write-flow tests require
transactional isolation for "fails at step N, transaction rolls
back, no partial state" verification, which prefix cleanup
cannot express.

Convention specifics:

- Local PostgreSQL 16.13 + pgvector 0.8.0 + pgcrypto via
  Homebrew.
- Test DB name: `primeqa_test_substrate2` (env var
  `SUBSTRATE_2_TEST_DB_URL` for override).
- Per-test `session` fixture wraps a SQLAlchemy `Connection` in
  a transaction; the fixture's teardown rolls back the
  transaction so no row persists across tests.
- **Flush-not-commit**: end-to-end tests rely on the
  Coordinator's internal `session.flush()` calls to make
  intermediate state visible across steps WITHOUT exiting the
  test's outer transaction. Explicit `session.commit()` calls
  would break per-test rollback isolation, so the e2e suite
  avoids them.
- Full suite (1148 tests) runs end-to-end in ~17 seconds.

See D-067 for the architectural decision; see
`tests/integration/test_representation/_fixtures.py` for the
composed-scenario helpers.

### 12.2 Implementation patterns

Patterns that emerged from the SQLAlchemy + PostgreSQL tech
stack and are worth carrying forward in substrate-2
maintenance:

- **`session.refresh(row)` after raw NOW() UPDATEs.** SQLAlchemy
  ORM's identity map can carry stale data after raw SQL
  UPDATEs. Explicit refresh ensures returned dataclasses
  reflect DB-side timestamps. Used in `promote_*`,
  `deprecate_*`, `change_recipe_priority`, `report_run_outcome`.
- **`ON CONFLICT ... DO UPDATE` for idempotent inserts.**
  `report_run_outcome` and the `arrange_runtime_state` helper
  use this pattern. Cleaner than SELECT-then-INSERT-or-UPDATE
  with equivalent atomicity guarantees.
- **`updated_at` invariance for no-op verification.** In tests,
  after a Coordinator call that should be a no-op, the
  assertion `result.updated_at == prior.updated_at` (with a
  small `time.sleep(...)` between calls so the DB clock could
  have advanced if a write had fired) establishes no-op
  *semantically*, not just "no observable change in this
  microsecond."
- **Lazy imports inside function bodies for circular-dependency
  resolution.** The
  `canonicalization` ↔ `identity_hash` ↔ `canonicalizers`
  triangle resolves by lazy imports inside `canonicalize()`.
  Established in Track C; inherited by Track D.
- **Class-level `__test__ = False` on SQLAlchemy row classes.**
  Prevents pytest's `Test*` collection glob from picking up
  `TestClaim`, `TestRecipe`, etc., as test classes. Set on all
  six row models.

### 12.3 Setup gotchas

Environment-setup details that future developers will need:

- **pgvector extension required.** Substrate-1's tenant
  migrations add `VECTOR(1024)` columns to entities tables. The
  test-DB setup must run
  `CREATE EXTENSION IF NOT EXISTS vector` before invoking
  alembic. The conftest does this; manual setups must mirror.
- **pgcrypto extension required.** Needed by substrate-1's
  tenant migrations for UUID generation. Same setup pattern.
- **Alembic multiple-heads.** The project has two migration
  branches (`shared` and `tenant`). Bare `alembic upgrade head`
  is ambiguous; use `alembic upgrade shared@head` and
  `alembic upgrade tenant@head` explicitly (with
  `-x mode=shared` / `-x mode=tenant -x tenant_id=N`).
- **Local PG version.** Tests verified against PostgreSQL
  16.13 + pgvector 0.8.0 via Homebrew. Version mismatches with
  the migration set may surface as constraint-name or
  enum-handling differences.
