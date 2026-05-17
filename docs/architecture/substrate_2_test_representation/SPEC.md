# Substrate 2 — Test Representation — SPEC

**Status:** Phase 1 in progress. §2 (deepest invariant) and §3
(archetype representation) resolved per D-051 and D-052; other
sections pending.

**Last substantive update:** 2026-05-16 (Phase 1: deepest invariant
+ archetype representation)

---

## Purpose

This spec defines Substrate 2: PrimeQA's canonical data structure
for a test case.

Design proceeds in two phases:

- **Phase 1 (in progress):** Conceptual shape — what S2 is, its
  deepest invariant, archetype representation, lifecycle, mutation
  paths, relationship to S1 and downstream substrates.
- **Phase 2 (pending):** Concrete data model — tables, columns,
  JSONB shapes, references, versioning, execution-history boundary.

See `BACKGROUND.md` for why this substrate exists. See
`OPEN_QUESTIONS.md` for design surfaces currently under deliberation.

---

## 1. What Substrate 2 IS

(Placeholder. Lands after §3 — archetype representation — fully
defines the shape of "a test case" across the five archetypes.)

---

## 2. Deepest invariant — what a test case essentially represents

**Resolution.** Per D-051: a PrimeQA test case is fundamentally a
structured claim — an *asserted system truth* scoped by the
*semantic conditions* under which it should hold, realized through
*one or more executable recipes*.

**Five-layer model.** A test case decomposes into five layers, two
of which are identity-bearing:

| Layer                  | Role                                                     | Identity-bearing? |
| ---------------------- | -------------------------------------------------------- | :---------------: |
| Asserted system truth  | What the test claims is true (the THEN)                  | YES               |
| Semantic conditions    | Under which conditions the truth should hold (WHEN)      | YES               |
| Execution realization  | The procedure of a particular recipe                     | NO                |
| Execution environment  | Capability assumptions and setup the recipe relies on    | NO                |
| Provenance             | How the test came to exist                               | NO                |

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
claim-kind taxonomy — is pending S2-Q-003 (test case data model).

**Direction (not locked).** Canonical claim units remain atomic
internally; human-facing test concepts may aggregate multiple
atomic claims under one UX-visible test envelope. Whether claim is
structurally allowed composition (AND, sequence) or whether
aggregation lives only at the user-facing layer is pending
S2-Q-003.

**Downstream consequences for adjacent open questions.**

- *S2-Q-002 (commonality across archetypes).* The five-layer model
  is structurally uniform across all five archetypes; only the
  *form* of claim and recipe varies per archetype.
- *S2-Q-004 (S1 references).* References inside the claim are
  intent-bearing and lean toward pinning (preserves meaning);
  references inside the recipe are operational and lean toward
  logical resolution (preserves liveness). Final reference model
  pending S2-Q-004.
- *S2-Q-006 (authority).* The authority boundary is now concrete:
  S8 has autonomous authority over the three non-identity-bearing
  layers; changes to either identity-bearing layer require human
  authority.

See `DECISIONS_LOG.md` D-051 for rationale and alternatives
considered.

---

## 3. Archetype representation — common substrate, archetype-specific semantic forms

**Resolution.** Per D-052: a PrimeQA test case is classified along
*three orthogonal discriminators* — `archetype`, `claim_kind`, and
`recipe_kind`. The five-layer model from §2 is structurally uniform
across all five archetypes; within each layer, the boundary between
common and archetype-specific falls *inside the layer*: a uniform
discriminator-bearing envelope holds an archetype-specific *semantic
form*.

**Three orthogonal discriminators.**

- **`archetype`** — the coarse product category: `data_behavior`,
  `configuration`, `permission`, `ui`, or `integration`. Five values
  total. Determines which kinds of recipes are typically applicable
  and which S4 executor handles execution.
- **`claim_kind`** — the fine-grained semantic type of the asserted
  truth. Multiple values per archetype (seeded taxonomy below).
  Determines the semantic form of the claim.
- **`recipe_kind`** — the kind of executable procedure: e.g., CRUD
  steps, metadata query, run-as context, browser interaction, event
  capture. Determines the semantic form of the recipe and what
  capability assumptions it carries.

*Orthogonal* means independent: the three axes vary independently,
not in a nested hierarchy. Not every combination is meaningful in
practice, but constraints on which combinations are supported live
at the application layer, not the schema layer. This permits
forward compatibility — a future recipe_kind for an existing
claim_kind requires no schema change.

**Archetypes are classifications, not storage partitions.** The
discriminator names a conceptual category. It does not entail
per-archetype tables, per-archetype migrations, or any other
storage commitment. Whether storage is one envelope table with
discriminator columns plus archetype-specific bodies, an envelope
table plus detail tables per archetype, or some other shape is
fully S2-Q-003. The storage realization must support the
conceptual classification; it does not have to mirror it.

**Structural commonality (A).** Every layer of the five-layer model
is uniformly present in every archetype. Within each layer, the
line between uniform and archetype-specific falls as follows:

| Layer                  | Uniform across archetypes                              | Archetype-specific                                                |
| ---------------------- | ------------------------------------------------------ | ----------------------------------------------------------------- |
| Asserted system truth  | claim_kind discriminator, identity hash, subject refs  | semantic form of the claim body                                   |
| Semantic conditions    | conditions metadata, condition-kind discriminator      | semantic form of condition body (varies with claim_kind)          |
| Execution realization  | recipe_kind discriminator, step ordering               | semantic form of step content (CRUD ≠ metadata ≠ browser ≠ ...)   |
| Execution environment  | environment-kind discriminator                         | capability assumptions and setup the recipe relies on             |
| Provenance             | fully uniform — no archetype-specific provenance       | none                                                              |

Coverage is fully derived from claim and semantic-conditions
references; its uniformity follows automatically.

**Semantic commonality (B).** Established by D-051 and reinforced
here. Every test in every archetype is a structured claim —
asserted system truth scoped by semantic conditions — realized
through one or more replaceable executable recipes. The discipline
rule, identity model, coverage-derivation rule, and authority model
from D-051 apply uniformly. No archetype is an exception to the
conceptual model.

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

**Seeded claim-kind taxonomy.** Starting material for S2-Q-003 to
refine, merge, split, and finalize. Not locked.

*Data-behavior archetype:*
- `value-claim` — field/record has value V under conditions C
- `state-transition-claim` — when X happens, record transitions to
  state S
- `vr-firing-claim` — Validation Rule Z fires for input X
- `flow-effect-claim` — Flow F produces effect E when triggered by
  X
- `deletion-blocked-claim` — record cannot be deleted under
  conditions C
- `duplicate-prevention-claim` — cannot create record matching
  criteria X

*Configuration archetype:*
- `existence-claim` — entity X exists in the org
- `property-claim` — metadata entity X has property P with value V
- `activation-claim` — entity X is active/inactive
- `metadata-relationship-claim` — metadata entity X has structural
  relationship to Y

*Permission archetype:*
- `capability-claim` — user/profile X can/cannot perform action Y
  on Z
- `field-access-claim` — profile X has read/edit access to field Y
- `record-visibility-claim` — user X can/cannot see record Y
- `sharing-claim` — sharing rules grant/deny access to user X

*UI archetype:*
- `element-visibility-claim` — element X is/is-not visible under
  state Y
- `element-state-claim` — element X has property P (enabled, value,
  text) under state Y
- `navigation-claim` — action X takes user to page Y
- `layout-claim` — page X displays sections/fields in arrangement Y

*Integration archetype:*
- `outbound-message-claim` — when X happens, outbound message Y is
  sent with payload Z
- `platform-event-claim` — when X happens, platform event Y fires
  with payload Z
- `inbound-effect-claim` — when external system sends X, internal
  state changes to Y
- `callout-claim` — when X happens, outbound HTTP callout to Y
  occurs

Cross-archetype consolidation is likely on lock — `existence-claim`
may unify between configuration (does the field exist?) and
data-behavior (does the record exist?). UI's `element-state-claim`
may split into narrower kinds once concrete UI tests surface.
S2-Q-003 will refine.

**Forward compatibility.** Schema accommodates all five archetypes
from day one. Discriminator columns exist; per-archetype semantic
forms come online incrementally as their archetypes ship. v1 may
materialize only the data-behavior archetype's semantic forms; the
foundation must not foreclose the other four.

**Deferred to S2-Q-003.**

- Recipe-kind taxonomy (parallel to the claim-kind seed above).
- Concrete storage shape for the uniform envelope and archetype-
  specific semantic forms (table layout, JSONB schemas, Pydantic
  validation).
- Cross-archetype consolidation of claim-kinds that may unify.
- The relationship between the capability-assumption model and
  environment-availability metadata (partially S2-Q-007 territory).

See `DECISIONS_LOG.md` D-052 for rationale and alternatives
considered.

---

## 4. Data model

(Placeholder. Phase 2.)

---

## 5. References to S1 entities

(Placeholder. Pending S2-Q-004.)

---

## 6. Lifecycle and versioning

(Placeholder. Pending S2-Q-005.)

---

## 7. Mutation paths (human edit, S3 regenerate, S8 autonomous rewrite)

(Placeholder. Pending S2-Q-006. Note: authority boundary established in §2.)

---

## 8. Execution-history boundary against S4

(Placeholder. Pending S2-Q-007.)

---

## 9. Requirement linkage

(Placeholder. Pending S2-Q-008.)

---

## 10. Outward surfaces (consumed by S3, S4, S6, S8)

(Placeholder. Pending S2-Q-009.)

---

## 11. Disposition of v2.2 test-management tables

(Placeholder. Pending S2-Q-010.)
