# Substrate 2 — Test Representation — SPEC

**Status:** Phase 1 in progress. §2 (deepest invariant) and §3
(archetype representation, including claim-kind, recipe-kind, and
trigger-kind taxonomy locks; four-discriminator framing; six-layer
model) resolved per D-051 through D-055; other sections pending.

**Last substantive update:** 2026-05-17 (Phase 1: deepest invariant
+ archetype representation + claim-kind taxonomy lock + recipe-kind
taxonomy lock + trigger-kind taxonomy lock + six-layer model
amendment)

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
claim-kind taxonomy — is pending S2-Q-003 (test case data model).

**Direction (not locked).** Canonical claim units remain atomic
internally; human-facing test concepts may aggregate multiple
atomic claims under one UX-visible test envelope. Whether claim is
structurally allowed composition (AND, sequence) or whether
aggregation lives only at the user-facing layer is pending
S2-Q-003.

**Downstream consequences for adjacent open questions.**

- *S2-Q-002 (commonality across archetypes).* The six-layer model
  is structurally uniform across all five archetypes; only the
  *form* of claim and recipe varies per archetype.
- *S2-Q-004 (S1 references).* References inside the claim are
  intent-bearing and lean toward pinning (preserves meaning);
  references inside the recipe are operational and lean toward
  logical resolution (preserves liveness). Final reference model
  pending S2-Q-004.
- *S2-Q-006 (authority).* The authority boundary is now concrete:
  S8 has autonomous authority over the four non-identity-bearing
  layers; changes to either identity-bearing layer require human
  authority.

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

**Recipe-kind taxonomy (locked per D-054).** Five recipe-kinds,
each classifying an observability domain.

- `data-recipe` — Record-level operations via the data API.
  Broader than literal CRUD: covers create/read/update/delete,
  SOQL queries including aggregates, record-action invocations
  (e.g., Submit for Approval), composite/bulk operations, and
  anonymous Apex blocks that operate on data. *Sub-discriminators:*
  API choice (REST / Bulk / Composite), identity context (system /
  run-as user), execution mechanism (direct API / anonymous Apex).
  *Capability assumptions:* data API auth; write permissions on
  target objects.

- `metadata-recipe` — Metadata-level operations via Metadata API
  or Tooling API. *Sub-discriminator modes:* `metadata-read`
  (query metadata existence, properties, permissions matrices;
  non-destructive) and `metadata-write` (create, update, or delete
  metadata entities; destructive — requires coordination in shared
  orgs). *Capability assumptions:* metadata API access for read;
  plus deployment permission for write.

- `ui-recipe` — Browser-driven interaction with Lightning UI.
  Navigate, click, type, observe DOM state, capture screenshots.
  *Sub-discriminators:* framework (Playwright / Selenium /
  Lightning Test Service), identity context (system / run-as via
  session impersonation). *Capability assumptions:* browser
  environment; Salesforce UI authentication; session management.

- `event-subscription-recipe` — Subscribe to and observe
  Salesforce-defined event payloads (platform events, outbound
  messages, change-data-capture streams). *Sub-discriminators:*
  channel type (platform-event / outbound-message / CDC),
  subscription mode (durable / ephemeral). *Capability
  assumptions:* event channel subscription access.

- `callout-intercept-recipe` — Capture Salesforce-initiated HTTP
  callouts. Inspect method, URL, headers, request body, timing;
  optionally return mocked responses. *Sub-discriminators:*
  interception mechanism (mock endpoint / proxy). *Capability
  assumptions:* mock endpoint infrastructure; Named Credential
  configuration access.

The split between `event-subscription-recipe` and
`callout-intercept-recipe` rests on a semantic-vocabulary
distinction, not a transport distinction: event subscription
observes **Salesforce-defined event payloads** (event-firing
assertions on Salesforce-native schemas); callout interception
observes **arbitrary HTTP requests** (HTTP-protocol-level
assertions on method, URL, headers, body). These produce different
assertion vocabularies regardless of underlying transport, and
warrant separate kinds under the semantic-form guardrail.

Inbound injection is intentionally not a recipe-kind. Tests of
`inbound-effect-claim` use `data-recipe` or
`event-subscription-recipe` for observation; the inbound payload
that triggers the scenario is classified by trigger-kind (see
`inbound-trigger` below).

**Trigger-kind taxonomy (locked per D-055).** Five trigger-kinds,
each classifying a causal-initiation pattern. The Plane column
distinguishes runtime-plane triggers (operate within the existing
org model) from model-plane triggers (mutate the org model itself).

| Trigger-kind | Plane | Causal initiation domain |
|---|---|---|
| `inbound-trigger` | runtime | External system pushes payload into Salesforce |
| `data-mutation-trigger` | runtime | DML on records inside Salesforce |
| `ui-trigger` | runtime | User-driven UI action |
| `time-trigger` | runtime | Salesforce mechanisms firing because elapsed-time predicates were met |
| `configuration-trigger` | **model** | Metadata deploy as causal initiation; mutates the org model |

- `inbound-trigger` — External system pushes a payload into
  Salesforce as the causal initiation. *Sub-discriminators:*
  channel (REST / SOAP / inbound email / streaming push / external
  platform-event publish). *Capability assumptions:* inbound
  channel infrastructure (mock endpoint, email gateway simulation,
  etc.) or external system credentials.

- `data-mutation-trigger` — DML on records inside Salesforce as
  the causal initiation. The DML causes downstream effects
  (automation, sharing recalc, related-record updates) that the
  recipe observes. Same mechanism as `data-recipe` but different
  role — cause vs observation. *Sub-discriminators:* operation
  (create / update / delete), identity context (system / run-as
  user), volume (single / bulk). *Capability assumptions:* data
  API auth; write permissions on target objects.

- `ui-trigger` — User-driven UI action (click, navigation, form
  fill) as the causal initiation. Distinct from `ui-recipe`:
  `ui-recipe` both drives the UI and observes UI state internally;
  `ui-trigger` drives the UI as cause while observation happens
  via another recipe-kind. *Sub-discriminators:* action type
  (button click / form submit / navigation / inline edit).
  *Capability assumptions:* browser environment; Salesforce UI
  auth; session management.

- `time-trigger` — Salesforce mechanisms that fire because
  elapsed-time predicates were met: scheduled flows, scheduled
  batch Apex, time-based workflow actions, time-dependent field
  updates. **Not** general async / retry / queue semantics —
  those are downstream behaviors observed by recipes, not
  triggers. The category is narrower than "system progression";
  it covers test-initiated activation of time-dependent firing
  mechanisms. *Sub-discriminators:* mechanism (scheduled-flow
  advancement / batch-Apex manual invocation / time-based-workflow
  stub / Test.setCreatedDate). *Capability assumptions:*
  test-environment support for the specific time-mechanism this
  trigger uses (Salesforce has no general clock-advance primitive;
  each mechanism has its own simulation approach).

- `configuration-trigger` — Metadata deploy as the triggering
  action; the test asserts "deploying X causes behavior Y."
  **Cross-plane:** unlike the four runtime triggers, this one
  *mutates the org model itself* rather than operating within an
  existing model. Three structural consequences follow:

  1. *Test-runtime risk:* can break unrelated tests by changing
     the rules. Runtime triggers are contained within test
     records; configuration changes affect all tests sharing
     the org.
  2. *Shared-org coordination:* cannot run concurrently with
     other tests that depend on the configuration being changed.
  3. *S8-adjacency:* configuration changes are what S8
     (Evolution) responds to. Configuration-trigger tests are
     tests *of the platform's behavior under its own evolution*
     — semantically interesting and architecturally adjacent to
     S8's domain.

  *Sub-discriminators:* deploy target (activation flag / property
  change / entity create-or-delete / permission grant). *Capability
  assumptions:* metadata-write capability; shared-org coordination
  mechanism.

**Trigger-kind identity nuance.** Trigger-kind is *operational by
default* and not identity-bearing — changing how a cause is
realized (DML vs UI button click) does not change what the test
means. However, D-051's discipline rule applies: if the trigger
mechanism itself is *semantically asserted* in the claim (e.g.,
"when external system sends via synchronous REST, the response
includes outcome X within 5 seconds"), the mechanism becomes part
of semantic conditions and IS identity-bearing. Operational by
default, semantic by assertion.

**One primary trigger per test (default).** A test has one primary
trigger — the causal initiation most directly tied to the claim's
WHEN. Other causal-looking actions in the recipe are setup. Composite
scenarios ("update X, advance time, observe Y") still have one
primary trigger — typically the time advance, since the test's
behavior under test depends on time; the update is setup.
Multi-primary trigger scenarios are possible but rare and usually
indicate the test should decompose into multiple tests.

**Forward compatibility.** Schema accommodates all five archetypes
from day one. Discriminator columns exist; per-archetype semantic
forms come online incrementally as their archetypes ship. v1 may
materialize only the data-behavior archetype's semantic forms; the
foundation must not foreclose the other four.

**Deferred to S2-Q-003 (remaining sub-cycles).**

- Concrete storage shape for the uniform envelope and archetype-
  specific semantic forms — table layout, JSONB schemas, Pydantic
  validation (sub-cycle 3).
- Identity-hash mechanics for `(archetype, claim_kind, claim body,
  semantic conditions)` (sub-cycle 4).
- Validation patterns: what's enforced at app boundary, what at DB
  layer (sub-cycle 5).
- The relationship between the capability-assumption model and
  environment-availability metadata (partially S2-Q-007 territory).

Trigger-kind classification — surfaced during sub-cycle 2 as a
parallel concept to recipe-kind — was resolved by D-055 (5 kinds,
four-discriminator extension, six-layer model amendment).

See `DECISIONS_LOG.md` D-052, D-053, D-054, and D-055 for rationale
and alternatives considered.

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
