# Substrate 2 — Test Representation — SPEC

**Status:** Phase 1 in progress. §2 (deepest invariant) and §3
(archetype representation, including claim-kind taxonomy lock)
resolved per D-051, D-052, and D-053; other sections pending.

**Last substantive update:** 2026-05-16 (Phase 1: deepest invariant
+ archetype representation + claim-kind taxonomy lock)

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
  truth. Multiple values per archetype (locked taxonomy below, per
  D-053). Determines the semantic form of the claim.
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

**Claim-kind taxonomy (locked per D-053).** 16 kinds across 5
archetypes. Refined from the §3 first-draft seeds per TA pushback;
locked here.

*Data-behavior archetype:*

- `value-claim` — record or field has value V under conditions C.
- `state-transition-claim` — record transitions from state S1 to
  state S2 when event E occurs. About the resulting end state of
  the record; mechanism-agnostic.
- `automation-effect-claim` — automation primitive (VR / Flow /
  trigger / process builder) fires and produces effect E when
  triggered by X. About a specific automation behaving correctly;
  the primitive is captured in a sub-discriminator. Distinct from
  `state-transition-claim` by mechanism-specificity: if the test
  would still mean the same thing under a different implementing
  primitive, it's a `state-transition-claim`; if not, it's an
  `automation-effect-claim`.
- `prohibition-claim` — operation O on target T is prohibited
  under conditions C. Covers deletion-prohibited,
  duplicate-prevented, and other system-enforced operation
  restrictions. Distinct from permission's `capability-claim`:
  prohibition is about *system enforcement* (a VR, sharing rule,
  or trigger blocks the operation regardless of user); capability
  is about *user privilege* (this user lacks the right).

*Configuration archetype:*

- `existence-claim` — metadata entity E exists in the org.
- `property-claim` — metadata entity E has property P with value
  V. Covers activation (`active`/`inactive` as property values),
  status, configuration values, and other entity properties.
- `metadata-relationship-claim` — metadata entity E has
  structural relationship R to entity F (e.g., field belongs to
  object, layout assigned to record-type, validation rule on
  object).

*Permission archetype:*

- `capability-claim` — subject S (user/profile/permset) can or
  cannot perform action A on target T under context C. Covers
  field-access (action=read/edit, target=field),
  record-visibility (action=read, target=record), object-level
  CRUD, and other outcome-level capabilities.
- `sharing-rule-claim` — sharing rule R grants or denies access
  of type T to subject S under criteria C. Distinct from
  `capability-claim` by structural focus: capability-claim is
  about the *outcome* (what the user can do); sharing-rule-claim
  is about the *rule mechanism* (what the rule itself declares).

*UI archetype:*

- `element-state-claim` — UI element E has property P with value
  V under page state S. Covers visibility (property=visible),
  enabled-state, displayed-text, displayed-value, and other
  element properties. Provisional — UI archetype is least-mature
  in the roadmap and concrete UI tests may surface new kinds
  that warrant addition under the semantic-form guardrail.
- `navigation-claim` — action A on page P1 takes the user to
  page P2.
- `layout-claim` — page P displays sections and fields in
  arrangement A. About page-level composition, distinct from
  single-element properties.

*Integration archetype:*

- `platform-event-claim` — when trigger T occurs, platform event
  E fires with payload P.
- `outbound-message-claim` — when trigger T occurs, outbound
  (workflow) message M is sent with XML payload P.
- `callout-claim` — when trigger T occurs, outbound HTTP callout
  to endpoint U is made with request R.
- `inbound-effect-claim` — when external system sends inbound
  message M, internal state change C results.

The four integration kinds remain distinct rather than
consolidating into a single `outbound-effect-claim` because their
semantic forms differ — different payload structures and
inspection mechanisms — not merely different implementation
primitives. See D-053.

**Forward compatibility.** Schema accommodates all five archetypes
from day one. Discriminator columns exist; per-archetype semantic
forms come online incrementally as their archetypes ship. v1 may
materialize only the data-behavior archetype's semantic forms; the
foundation must not foreclose the other four.

**Deferred to S2-Q-003 (remaining sub-cycles).**

- Recipe-kind taxonomy (parallel to the locked claim-kind taxonomy
  above; sub-cycle 2).
- Concrete storage shape for the uniform envelope and archetype-
  specific semantic forms — table layout, JSONB schemas, Pydantic
  validation (sub-cycle 3).
- Identity-hash mechanics for `(archetype, claim_kind, claim body,
  semantic conditions)` (sub-cycle 4).
- Validation patterns: what's enforced at app boundary, what at DB
  layer (sub-cycle 5).
- The relationship between the capability-assumption model and
  environment-availability metadata (partially S2-Q-007
  territory).

See `DECISIONS_LOG.md` D-052 and D-053 for rationale and
alternatives considered.

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
