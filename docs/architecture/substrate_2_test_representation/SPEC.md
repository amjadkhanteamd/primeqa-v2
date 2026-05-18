# Substrate 2 — Test Representation — SPEC

**Status:** §2, §3, §4 (data model, including validation layering),
§5 (S1 references), §6 (lifecycle and versioning, including
canonicalization mechanics), §7 (mutation paths and authority),
§8 (execution-history boundary), §9 (requirement linkage),
§10 (outward surfaces), and §11 (v2.2 disposition) substantively
complete per D-051 through D-065. §1 (synthesis overview) remains
as placeholder, conventionally written after all other sections.

**Last substantive update:** 2026-05-18 (execution-history boundary,
requirement linkage, outward surfaces, v2.2 disposition)

---

## Purpose

This spec defines Substrate 2: PrimeQA's canonical data structure
for a test case.

Design proceeds in two phases:

- **Phase 1 (complete):** Conceptual shape — what S2 is, its
  deepest invariant, archetype representation, lifecycle, mutation
  paths, relationship to S1 and downstream substrates.
- **Phase 2 (complete):** Concrete data model — tables, columns,
  JSONB shapes, references, versioning, execution-history boundary,
  outward surfaces.

See `BACKGROUND.md` for why this substrate exists. See
`OPEN_QUESTIONS.md` for design surfaces (now all resolved).

---

## 1. What Substrate 2 IS

(Placeholder. Synthesis section conventionally written last,
composing the substantive content from §2 through §11 into a
single overview.)

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

The four core tables (`test_claims`, `test_recipes`,
`test_provenance`, `test_claim_coverage`) represent substrate-2's
internal coherence. The two boundary tables
(`test_recipe_runtime_state`, `test_requirement_links`) represent
substrate-2's interfaces to other systems — S4 for execution
evidence, JIRA (and future systems) for requirements.

### 4.3 Discriminator placement and authority

Discriminator columns per D-052 (extended by D-055):

- `archetype`, `claim_kind` live on `test_claims` (describe what
  the test asserts).
- `trigger_kind`, `recipe_kind` live on `test_recipes` (describe
  the operational realization).

**Row discriminator is canonical authority.** The DB-level
discriminator columns are the source of truth. JSONB bodies carry
a `kind` field as redundant self-description for export/debug/backup
purposes, but the row discriminator wins on any disagreement. All
queries dispatch on row discriminator (indexed, typed, authoritative).
All Pydantic validation reads row discriminator to select the model,
then validates body content. Write-time validation enforces
`body.kind == row.discriminator` — drift is a bug.

### 4.4 JSONB body conventions

Every JSONB body carries two top-level keys:

```json
{
  "body_schema_version": 1,
  "kind": "value-claim",
  ...body content...
}
```

- `body_schema_version` (int) — version of the body's schema; per-body-kind trajectory; enables Pydantic-model dispatch during schema evolution.
- `kind` (string) — redundant self-description matching the row discriminator. Self-describing for exports; **row discriminator is authoritative on disagreement**.

Body shape per (`row discriminator`, `body_schema_version`) is
defined by Pydantic models per §4.7.

### 4.5 Coverage derivation strategy

App-level derivation. The S3 generator and S8 evolver are the only
writers of `test_claims`; both update `test_claim_coverage` rows
as part of the claim write. Avoids DB-trigger complexity and
materialized-view staleness. Coverage rederivation on version
change is handled per D-057 (delete-and-replace pattern; old
coverage removed when new claim version supersedes).

### 4.6 Forward-compatibility markers

Three architectural directions reserved without being built:

- **`semantic_conditions` graphification.** Current shape is flat
  JSONB. If conditions develop compositional structure
  (preconditions with dependency relationships, AND/OR/sequence
  composition), this may evolve to a relational or graph
  representation. The flat shape today doesn't foreclose this.
- **Operational linkage layer for recipes.** `test_claim_coverage`
  is *semantic* linkage (claim-derived, supports the substrate's
  S2↔S1 bridge). Recipes also reference S1 entities (target
  objects for mutations, user identities for impersonation, etc.),
  but these are *operational* dependencies, not semantic coverage.
  A parallel operational linkage layer (e.g.,
  `test_recipe_dependencies`) is forward-compatible territory,
  not realized today.

See `DECISIONS_LOG.md` D-056 for rationale and alternatives
considered.

### 4.7 Validation layering and the Semantic Transaction Coordinator

**Resolution.** Per D-060: substrate-2's validation operates
across three complementary enforcement layers, coordinated
through a named substrate-level component (the Semantic
Transaction Coordinator) that maintains consistency invariants
spanning multiple tables, body schemas, and validation layers.

#### 4.7.1 Three complementary enforcement layers

The substrate distributes validation responsibilities across
three layers with distinct scope, evolution speed, and bypass
characteristics. The layers are **complementary, not
hierarchical** — each has a first-class enforcement role and a
distinct scope.

| Layer | Scope | Bypass characteristic | Evolution speed |
|---|---|---|---|
| DB | Structural invariants (discriminator enums, FK/PK integrity, CHECK constraints) | Un-bypassable | Slow (migration overhead) |
| Pydantic | Semantic content validation (body shape, cross-field rules, ontology enforcement, reference shapes) | Bypassable by raw SQL | Fast (code change) |
| Schema | Per-body type definitions, semantic field descriptors, discriminator dispatch | (not directly enforcing) | Fast (code change) |

Substrate-critical invariants are deliberately **double-enforced**
across layers — e.g., discriminator values are both DB enums AND
Pydantic Literal types; this redundancy is intentional and the
coordination cost on enum changes is accepted.

#### 4.7.2 Pydantic model organization

Two-level discriminator dispatch:

- **Level 1 — row discriminator** (`archetype`, `claim_kind` for
  claims; `trigger_kind`, `recipe_kind` for recipes): selects the
  family of body models that applies
- **Level 2 — `body_schema_version`**: selects the specific
  version within that family

Pydantic 2 idiom with discriminated unions:

```python
class ValueClaimBodyV1(BaseModel):
    body_schema_version: Literal[1]
    kind: Literal["value-claim"]
    subject: IdentityBearingRef
    expected_value: ValueExpression

class ValueClaimBodyV2(BaseModel):
    body_schema_version: Literal[2]
    kind: Literal["value-claim"]
    subject: IdentityBearingRef
    expected_value: ValueExpression
    expected_value_format: Literal["raw", "formatted"]

ValueClaimBody = Annotated[
    Union[ValueClaimBodyV1, ValueClaimBodyV2],
    Field(discriminator="body_schema_version"),
]

DataBehaviorClaimBody = Annotated[
    Union[ValueClaimBody, StateTransitionClaimBody, ...],
    Field(discriminator="kind"),
]
```

The Semantic Transaction Coordinator (§4.7.5) dispatches at write
time through these two levels.

#### 4.7.3 Reference type hierarchy with semantic role preservation

Per D-060, reference types distinguish **structural shape** from
**semantic role**:

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
    """Pinned reference that participates in the test's identity.

    Structurally identical to PinnedRef; the distinct type
    preserves the semantic-role information for documentation,
    tooling introspection, and forward-compatibility with
    additional semantic-role markers.
    """
    pass

OperationalRef = Annotated[
    Union[PinnedRef, LogicalRef],
    Field(discriminator="ref_kind"),
]
```

`IdentityBearingRef` is a **distinct type**, not a type alias.
The hybrid-by-layer rule (per D-058 §5.1) becomes **structural
type enforcement**: identity-bearing layer body models declare
fields with `IdentityBearingRef`; operational layer body models
declare fields with `OperationalRef`. Cross-layer rule violations
fail Pydantic validation as type mismatches.

#### 4.7.4 Semantic field descriptors

Per-field semantic metadata uses `Annotated[T, Marker]`
consistently. Today's marker: `ArraySemantics.SET`. Future
markers include hash-contribution annotations (per D-059 §6.3.10
reservation) and identity-contribution annotations. The
discipline: single mechanism via `Annotated`; substrate-level
tooling reads field metadata through Pydantic introspection.

#### 4.7.5 The Semantic Transaction Coordinator

The Semantic Transaction Coordinator is **semantic OS
infrastructure** for substrate-2 (per D-064 framing) — the kernel
through which all substrate operations route, the surface against
which all consuming substrates build, the locus where consistency
invariants and authority rules are enforced.

It coordinates:

- Body-shape consistency (Pydantic per body)
- Row-body discriminator consistency (D-056 §4.3)
- Cross-layer ontology consistency (D-058 §5.5, structurally
  enforced via type hierarchy per §4.7.3)
- Canonicalization-hash consistency (D-059 §6.3)
- Coverage-claim consistency (D-058 §5.4)
- Provenance-event consistency (D-056 §4.1)
- Mutation-path routing and authority enforcement (D-061 §7)
- Runtime-state updates from S4 (D-062 §8)
- Resolution-class operations (current-approved, runtime status,
  recipe selection — D-064 §10.4)

All API-driven writes to `test_claims`, `test_recipes`,
`test_claim_coverage`, `test_provenance`,
`test_recipe_runtime_state`, and `test_requirement_links` route
through the Coordinator. Direct DB writes that bypass the
Coordinator bypass the Pydantic-layer invariants; DB-layer
invariants (substrate-critical structural rules per §4.7.1)
still apply.

#### 4.7.6 Write-flow orchestration

The Semantic Transaction Coordinator's canonical write sequence:

1. **Discriminator validation** — archetype, claim_kind in
   valid enum (DB enum + Pydantic Literal both enforce)
2. **Body dispatch** — select Pydantic model per
   `(claim_kind, body_schema_version)`
3. **Body validation** — Pydantic checks body shape, field types,
   cross-field constraints
4. **Body-row consistency** — body.kind must match
   row.claim_kind (per D-056)
5. **Cross-layer ontology validation** — structural via per-layer
   ref types (`IdentityBearingRef` vs `OperationalRef`)
6. **Cross-body validation** — recipe's `claim_test_id` must
   reference existing claim (FK at DB layer enforces; Pydantic
   may pre-check)
7. **Canonicalization** — produce canonical-form dict per
   D-059 §6.3.2
8. **Hash computation** — SHA-256 of canonical form
9. **Coverage extraction** — pull pinned refs from
   identity-bearing layers per D-058 §5.4
10. **Authority enforcement** — verify the writing actor (per §7)
    has authority for this mutation given hash-change status
11. **DB write transaction** — `test_claims` row (canonical body
    + hash + `identity_hash_version`) + `test_claim_coverage`
    rows + `test_provenance` event

Any step's failure rolls back the transaction. Each step has
structured error types per §4.7.8.

#### 4.7.7 Read-path semantics

Read flow at the substrate's API boundary:

1. Row fetched from DB
2. JSONB body parsed
3. Pydantic model dispatched on
   `(row_discriminator, body_schema_version)`
4. Body validated against model (defense in depth against
   corruption or out-of-band edits)
5. Returned as typed Pydantic object

**Hash is not recomputed on read.** Within a given
`(identity_hash, identity_hash_version)` regime, the stored hash
is trusted. Cross-regime hash comparison requires explicit
re-hashing (per D-059 Rule 6).

**Pinned-ref resolution is lazy.** Reading a row does not
resolve pinned references against S1 — caller decides when to
resolve.

Hash audit operations (periodic verification that stored hashes
re-canonicalize correctly under their `identity_hash_version`
policy) run as separate maintenance jobs, not in the read path.

#### 4.7.8 Read-path error types

Read-path failures distinguish five error categories with
distinct handling:

| Error type | Cause | Severity | Handling |
|---|---|---|---|
| `SchemaIncompatibilityError` | No Pydantic model exists for `(kind, body_schema_version)` | Graceful degradation | Surface raw JSONB with warning; may indicate substrate-library version mismatch or missing migration |
| `BodyCorruptionError` | Model exists for `(kind, body_schema_version)` but body fails validation | Incident | Log + alert; surface degraded result; investigate (storage corruption, out-of-band edit, bug) |
| `OntologyViolationError` (write-time) | Cross-layer reference-kind rule violated | Architectural rejection | Return structured error with explicit ontology framing per D-058 |
| `AuthorityViolationError` (write-time) | Writing actor lacks authority for this mutation given hash-change status | Architectural rejection | Return structured error with explicit authority framing per D-061 |
| `ValidationError` (Pydantic standard) | Routine field validation failure (write-time) | Soft rejection | Return structured error with field-level details |

#### 4.7.9 Migration handling

Body-schema-version migration is governance work, not routine
maintenance. The migration author provides a new Pydantic model,
a migration function `(old_body: V2) -> (new_body: V3)`, and a
canonical-form preservation declaration per D-059 Rule 5.

Canonicalization policy migration (per D-059 Rule 6) follows
the same pattern but operates on `identity_hash_version` rather
than `body_schema_version`. Both are governance-level
operations: explicit, audited, recorded in provenance.

See `DECISIONS_LOG.md` D-060 for rationale and alternatives
considered.

---

## 5. References to S1 entities

**Resolution.** Per D-058: substrate-2 uses **hybrid-by-layer**
reference resolution. Identity-bearing layers (asserted truth,
semantic conditions) require pinned references; operational
layers (causal initiation, observation realization, execution
environment) default to logical references, with pinned permitted
as opt-in for specific reproducibility needs.

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

References live inside JSONB bodies as typed objects.

**Pinned reference** (used in identity-bearing layer bodies;
permitted in operational layer bodies for reproducibility):

```json
{
  "ref_kind": "pinned",
  "entity_type": "field",
  "entity_id": "550e8400-e29b-41d4-a716-446655440000",
  "version_seq": 47,
  "external_id": "Account.AccountNumber"
}
```

**Logical reference** (default in operational layer bodies):

```json
{
  "ref_kind": "logical",
  "entity_type": "field",
  "external_id": "Account.AccountNumber"
}
```

The `ref_kind` field is part of the JSON shape (not implicit by
location) so reference objects are self-describing for exports,
debugging, and external tooling.

### 5.3 Identity_hash canonicalization

`identity_hash` (per D-057) canonicalizes references from
identity-bearing layers as part of the semantic equivalence
fingerprint. The canonicalization rule:

- **Pinned references contribute `entity_id` only** to the hash
  input. `version_seq` is operational metadata and is excluded.
- **Logical references** do not appear in identity-bearing layers
  (rejected by validation per §5.5).

Full canonicalization mechanics are defined in §6.3 per D-059.

### 5.4 Coverage derivation

`test_claim_coverage` extracts pinned references from
identity-bearing layers (`asserted_truth` and
`semantic_conditions`) only. Operational layer references —
whether logical or opt-in pinned — are NOT in coverage; they are
operational dependencies, not semantic content.

### 5.5 Cross-layer reference validation as ontology enforcement

Reference-kind validation is not Pydantic boilerplate; it is
**ontology enforcement** (per D-058 §5.5):

- **Identity-bearing layers reject logical references at write
  time.** Detected violations are write-time errors, not warnings.
- **Operational layers accept both kinds.** Logical is the
  default expectation; pinned is opt-in.
- **The validation lives at the substrate level**, not in
  application convention.

Per D-060 §4.7.3, this ontology enforcement is implemented
structurally via Pydantic type hierarchy — identity-bearing
layer body models declare ref-typed fields with
`IdentityBearingRef`; operational layer body models declare
fields with `OperationalRef`. Cross-layer rule violations fail
Pydantic validation as type mismatches rather than as ad-hoc
validators.

### 5.6 external_id drift modes

Logical references face six drift modes — rename, move, replace,
namespace shift, inheritance change, metadata-resolution quirks.
S8's drift detection must handle each mode explicitly per D-058
§5.6. Pinned references survive most drift modes (the entity_id
is stable); logical references are evolvable but drift-vulnerable.

### 5.7 Reference semantics under replay modes (cross-reference to §6.8)

Per D-057's reserved replay modes (§6.8), references resolve
differently under historical vs semantic replay:

- **Historical replay:** pinned refs resolve at their pinned
  `(entity_id, version_seq)`; logical refs resolve at the
  historical point's S1 state.
- **Semantic replay:** logical refs resolve to current S1
  entities. Pinned refs follow forward to current `version_seq`
  of the same `entity_id` **only via S8-blessed transitions**.

Per D-059 §6.3.9 Rule 3, this blessing is a **two-gate evaluation**:
hash preservation (mechanical) plus entity-evolution semantic
compatibility (judgmental).

### 5.8 Forward-compatibility reservations

Three architectural directions reserved without action in v1:

- **Weighted semantic linkage** (per D-058 §5.8)
- **Operational linkage layer for recipes** (per D-056's marker)
- **Reference resolution policies** (per D-057's marker)

See `DECISIONS_LOG.md` D-058 for rationale and alternatives
considered.

---

## 6. Lifecycle and versioning

**Resolution.** Per D-057: substrate-2 uses **effective-time
supersession** — single time dimension, version_seq as canonical
supersession authority, valid_to as denormalized convenience.

### 6.1 Invariant hierarchy

`version_seq` defines supersession truth. `valid_to` is
denormalized convenience. In any scenario where they disagree,
`version_seq` ordering is authoritative.

### 6.2 The continuity triad

Three distinct continuities modeled separately (per seventh
guardrail in §3): stable identifiers (organizational), identity_hash
(semantic equivalence), version_seq (supersession order).

### 6.3 `identity_hash` semantics, canonicalization, and governance contract

`identity_hash` is the **semantic equivalence fingerprint**.
Operational edits preserve hash; semantic edits change it and
invalidate approval per the semantic-vs-operational lifecycle
guardrail.

Per D-059, canonicalization mechanics and the resulting governance
contract are defined as follows.

#### 6.3.1 Hash input scope

The hash input comprises four components: `archetype`,
`claim_kind`, canonicalized `asserted_truth` JSONB, canonicalized
`semantic_conditions` JSONB. Out of scope: `test_id`,
`version_seq`, temporal columns, `status`, `identity_hash` itself,
all recipe content, all coverage content, runtime state, requirement
links, and `body_schema_version`.

#### 6.3.2 Canonicalization rules (strict)

- Object key ordering: alphabetical, recursive at every level.
- Whitespace: stripped between tokens; preserved inside string values.
- String encoding: UTF-8, no escape variations; case-sensitive.
- Numbers: canonical JSON numeric form.
- Null vs missing: distinguished. Empty arrays vs missing: distinguished.
- Booleans: lowercase JSON literals.

#### 6.3.3 Reference canonicalization

Per D-058 constraint, pinned references in identity-bearing
layers canonicalize to `{ "entity_id": "<uuid>", "entity_type":
"<type>" }` only. `version_seq`, `external_id`, and `ref_kind`
are excluded.

#### 6.3.4 Array semantics (schema-declared)

Per D-059, body schemas declare per-field array semantics as
`ordered` (default) or `set` (sort before hash).

#### 6.3.5 `body_schema_version` handling

Excluded from the hash input. Storage metadata, not semantic
content.

#### 6.3.6 Hash algorithm

SHA-256, hex-encoded → 64-character string stored in
`identity_hash` column. Computed at write time by the Semantic
Transaction Coordinator.

#### 6.3.7 Canonicalization policy versioning

Per D-059, the canonicalization policy itself is versioned via
`identity_hash_version` column on `test_claims`. Hashes are only
directly comparable between rows sharing the same
`identity_hash_version`.

#### 6.3.8 Storage of canonical form

Canonicalized JSONB is stored on the row (not the original
non-canonical input).

#### 6.3.9 Governance contract (six rules)

**Rule 1 — S8 autonomy boundary (no autonomous semantic divergence).**
S8 may autonomously create new claim versions if and only if the
new version's canonical form equals the predecessor's. The
invariant is **no autonomous semantic divergence**, not "no
autonomous mutation of identity-bearing content."

**Rule 2 — Approval invalidation.** Hash change between versions
→ approval invalidated; new version begins in `draft` status.

**Rule 3 — S8 evolution through entity changes (two-gate
evaluation).** Both Gate 1 (hash preservation, mechanical) AND
Gate 2 (entity-evolution semantic compatibility, judgmental)
must pass for autonomous update.

**Rule 4 — Cross-test semantic equivalence (scoped).** Two
claims with same `identity_hash` AND same `identity_hash_version`
are semantically equivalent under that canonicalization policy.

**Rule 5 — Schema migration discipline.** Body schema migrations
declare canonical-form preservation explicitly.

**Rule 6 — Canonicalization policy migration.** Policy evolution
is governance-level. Re-hashing existing rows under new policy
is an explicit operation.

#### 6.3.10 Semantic projection fields (reservation)

For v1, the entire canonicalized body contributes to the hash.
Future: schema-declared per-field hash-contribution annotation
(`semantic` vs `projection`). Trajectory toward semantic field
descriptors framework per D-060 §4.7.4.

#### 6.3.11 Edge cases

- Hash collision (different content, same hash): cryptographically
  negligible with SHA-256.
- Non-canonical write input: Coordinator canonicalizes before
  hashing; canonical form is stored.
- Hash computation timing: at write time only; never recomputed
  on read within a given `(identity_hash, identity_hash_version)`
  regime.
- Policy migration for existing rows: explicit governed operation.

See `DECISIONS_LOG.md` D-059 for rationale and alternatives
considered.

### 6.4 Recipe-to-claim FK semantics

Default — logical resolution (recipe's `claim_test_id` references
the claim's `test_id`, not specific version). Optional pinning
via `claim_version_seq` for reproducibility-critical contexts.

### 6.5 Coverage rederivation

`test_claim_coverage` is current-only. When a new claim version
supersedes its predecessor, coverage rows are deleted and
rederived.

### 6.6 Approval state lifecycle

Approval state is dual-tracked: current state on `test_claims.status`;
history in `test_provenance` rows. Approval-state invalidation
triggers on `identity_hash` change between versions. Mutation
paths and their per-path approval impact are specified in §7
per D-061.

### 6.7 Archival policy

**No archival in v1.** Semantic lineage continuity is more
valuable than retention optimization. Architectural commitment,
not cost-driven decision.

### 6.8 Replay modes (storage support; engine downstream)

Two distinct replay modes supported by the storage shape;
replay engine is downstream (S4 territory). Per D-058:

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

A test case can change through three distinct paths:

- **Human edit** — A QA engineer, BA, or other authorized human
  directly edits a claim or recipe through the substrate's API.
- **S3 regeneration** — The future generation substrate produces
  new claim or recipe content. S3 is an autonomous-but-bounded
  actor.
- **S8 autonomous rewrite** — The future evolution substrate
  responds to S1 entity changes — rewrites recipes, bumps
  pinned-ref `version_seq` when entities evolve compatibly,
  surfaces tests for review when unblessed transitions occur.

All three paths route through the Semantic Transaction
Coordinator.

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

Identity continuity = stable identifier (`test_id`, `recipe_id`)
continuity. Persists across all mutations.

Semantic continuity = `identity_hash` continuity (scoped to
`identity_hash_version`). Different hash = semantically different
test, even with same `test_id`.

Orthogonal dimensions. Identity preservation does not imply
semantic preservation; semantic preservation does not require
identity preservation.

### 7.4 Trust boundary by path

Per D-061 §7.4 table. Two asymmetries:

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

**Current-approved as governance resolution.** `get_current_approved_claim(test_id)`
is a Coordinator governance operation interpreting version
history per substrate rules — not a simple status lookup.
Downstream substrates use Coordinator interface exclusively.

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

- **Recipe approval auto-preservation** (when detection mechanism
  exists)
- **Merge/rebase semantics** for concurrent semantic conflicts
- **Provenance streams as named taxonomy** (current single
  `event_kind` becomes multi-stream)
- **Deprecation taxonomy** as sub-status (current single
  `deprecated` status conflates multiple states)

See `DECISIONS_LOG.md` D-061 for rationale and alternatives
considered.

---

## 8. Execution-history boundary against S4

**Resolution.** Per D-062: substrate-2's boundary with the future
execution substrate (S4) is the **last-run snapshot** pattern.
S2 holds minimal denormalized state per recipe (latest outcome,
last_pass_at, last_failure_at) via the `test_recipe_runtime_state`
table; S4 holds the full evidence and history. S4 pushes updates
to S2 via Coordinator callback. Test-level runtime status is a
**resolution operation** composing recipe-level state.

### 8.1 The substrate boundary

Platform philosophy distinguishes:

- **Execution captures truth** — S4's domain. Run identifiers,
  structured traces, errors, metadata references, contextual
  signals.
- **Intelligence interprets truth** — S6's domain. Failure
  attribution, clustering, semantic explanation.
- **Representation owns identity** — S2's domain. Claims,
  recipes, identity, approval, coverage, provenance.

S2 must NOT replicate S4's evidence — that would conflate
execution with representation. But S2 benefits from minimal
denormalized state for hot-path queries (S6 attribution
ergonomics, S8 evolution prioritization, UX status display) that
would otherwise force every status query to join with S4.

The boundary commitment: **S2 holds only what it needs for its
own resolution operations; S4 holds everything else.**

### 8.2 The runtime-state snapshot

The `test_recipe_runtime_state` table (defined in §4.1) holds one
row per `recipe_id` (NOT per recipe version). It is a pure
snapshot:

- `last_run_id` (opaque reference to S4 — S2 does not interpret)
- `last_run_at`, `last_run_outcome`, `last_run_recipe_version_seq`
- `last_pass_at`, `last_failure_at` (NULL if never)

**No aggregate statistics.** No run counters, no pass-rate
percentages, no flakiness metrics. These belong to S4 (raw data)
or S6 (derived analyses). S2 maintaining them would muddy the
substrate's purpose and add write coordination overhead on every
run report.

**Per-recipe, not per-recipe-version.** Recipes are versioned;
runtime state is not. The `last_run_recipe_version_seq` field
records which version was run, but only the latest outcome is
retained.

**Separate table, not columns on `test_recipes`.** The boundary
must be visible in the schema. Mixing runtime state into the
representation table blurs the substrate boundary.

### 8.3 Push-based S4 integration

S4 reports run outcomes via a Coordinator callback:

```
coordinator.report_run_outcome(
    actor=S4,
    run_id=<UUID>,
    recipe_id=<UUID>,
    recipe_version_seq=<int>,
    outcome=<enum>,
    ran_at=<timestamp>,
)
```

S2 never queries S4. S4 pushes; S2 ingests. This:

- Avoids S2 → S4 dependencies (S2 doesn't need S4's API surface)
- Concentrates write coordination at the Coordinator (atomic
  update of `test_recipe_runtime_state`)
- Allows S4 to batch reports if needed (S2 doesn't care about
  timing as long as the snapshot eventually reflects the latest
  run)

**Idempotency:** the callback is idempotent on `run_id`. Re-reporting
the same run is a no-op (Coordinator detects the duplicate via
`last_run_id` match).

### 8.4 Test-level runtime status as resolution operation

Test-level runtime status is **derived composition**, not stored.
A Coordinator resolution operation:

```
coordinator.get_test_runtime_status(test_id) → TestRuntimeStatus
```

Returns one of:

- `passing` — at least one current-approved recipe has
  `last_run_outcome=passed` AND no current-approved recipe has
  `last_run_outcome=failed`
- `failing` — at least one current-approved recipe has
  `last_run_outcome=failed`
- `untested` — no current-approved recipe has a run
- `mixed` — multiple recipes with conflicting outcomes that don't
  fit the above

This is **resolution**, not lookup — it interprets recipe-level
state per substrate composition rules. Per D-064, resolution-class
operations are first-class substrate concepts; they live in the
Coordinator.

### 8.5 Multi-recipe outcome resolution has pressure

The composition rule in §8.4 is conservative and initial. Multi-recipe
outcomes have genuine ambiguity:

- A claim with API recipe (passed) and UI recipe (failed) — is the
  test passing or failing? Both recipes observe legitimate aspects;
  their outcomes may diverge for substantive reasons.
- A claim with primary recipe (passed) and regression recipe
  (failed) — is the primary's outcome canonical?
- A claim with 3 recipes, 2 passed, 1 errored — what's the status?

The substrate provides:

- **Raw recipe-level state** via direct queries on
  `test_recipe_runtime_state` (full information, no composition)
- **Derived test-level composition** via the resolution operation
  (with conservative initial rule)

Consumers needing different composition policies (e.g., "primary
recipe outcome wins" or "only consider critical-priority recipes")
compose against the raw recipe state rather than the substrate's
default composition.

### 8.6 Forward-compatibility reservations

Two architectural directions reserved without action in v1:

- **Richer runtime-state resolution.** The composition rule in
  §8.4 may evolve toward:
  - Recipe priority weighting in test-level status
  - "Primary recipe" designation (one recipe canonical for status)
  - Outcome-aggregation policies (any-pass / all-pass /
    primary-pass / weighted)
  Substrate exposes raw state today; evolved resolution policies
  layer on top without schema change.

- **Run history beyond last-run.** Some S6 attribution scenarios
  may want "this test has been flaky" (run history with pass/fail
  pattern). Requires querying S4 or building a separate
  denormalized aggregate. Today's snapshot-only model defers this
  to either S4-side queries or a future flakiness-detection
  substrate.

See `DECISIONS_LOG.md` D-062 for rationale and alternatives
considered.

---

## 9. Requirement linkage

**Resolution.** Per D-063: substrate-2 links to external
requirement-management systems (JIRA, etc.) via **external typed
references only**. No ticket content is replicated in PrimeQA;
the external system remains the source of truth. The
`test_requirement_links` table provides multi-kind linkage
(generated-from / verifies / related-to). Future evolution to
registry-based external-system identification is reserved.

### 9.1 External typed reference model

Substrate-2's role re requirements is **linkage, not ownership.**
Requirements (JIRA tickets, Linear issues, Azure DevOps work
items) are external to PrimeQA's domain. PrimeQA tests can
reference them for traceability — "this test was generated from
PROJ-1234," "this test verifies PROJ-5678" — but PrimeQA does
not own or replicate ticket content.

Why no content replication:

- **Content goes stale.** JIRA tickets evolve (description edits,
  status changes, comments added). Replicated content drifts from
  the source of truth.
- **Mission boundary.** PrimeQA is about asserting system truths.
  Project management is a separate concern. Replicating ticket
  content would expand the substrate's responsibility beyond its
  scope.
- **Sync overhead.** Bidirectional or even unidirectional sync
  introduces operational complexity (when to sync, how to handle
  conflicts, etc.) that PrimeQA doesn't need.

### 9.2 The `test_requirement_links` table

Defined in §4.1. Schema summary:

- `test_id` — FK to `test_claims.test_id`
- `external_system` enum — `jira` today; reserved for registry-based
  evolution per §9.5
- `external_key` text — e.g., `PROJ-1234`
- `external_version` text NULL — optional version/revision identifier
- `link_kind` enum — `generated_from` / `verifies` / `related_to`
- `linked_at`, `linked_by` — metadata
- PK: `(test_id, external_system, external_key, link_kind)`
- Index for reverse lookup: `(external_system, external_key)`

**Multi-kind linkage.** A test may be `generated_from` one
requirement (the JIRA ticket that prompted its creation) AND
`verifies` another (an upstream requirement that the test
contributes to verifying). Many-to-many relationship.

**`external_version` field.** Most systems (JIRA, Linear)
don't expose meaningful version identifiers for tickets. The field
is reserved for systems that do (e.g., document-management systems,
formal requirements tools). Likely NULL for JIRA in practice.

### 9.3 No content replication

The substrate stores **only the link**, never the content.
Downstream consumers (S3 prompt context, UX display, S6
attribution) query the external system's API directly when they
need ticket content.

Forward-compat: a content-cache layer could be added as a separate
concern (different substrate or service) without changing
substrate-2's commitment. The `test_requirement_links` table
provides the reference; consumers decide how to resolve.

### 9.4 Multi-kind linkage semantics

The three link kinds capture distinct relationships:

- **`generated_from`** — S3 generated this test in response to
  this requirement. The requirement is the test's origin.
- **`verifies`** — This test contributes to verifying this
  requirement. May be human-authored (manual linkage) or
  S3-derived (S3 traces test back to requirement during
  generation).
- **`related_to`** — Loose association. The test and requirement
  are connected in some way that doesn't fit the above categories.
  Catch-all for ad-hoc relationships.

A single test may have multiple link kinds to the same
requirement (e.g., `generated_from` AND `verifies`), or one link
kind across multiple requirements (e.g., `verifies` for several
upstream requirements).

### 9.5 Forward-compatibility reservations

Three architectural directions reserved without action in v1:

- **Registry-based `external_system`.** Today the enum has hardcoded
  values (`jira` initially; potentially `linear`, `azure_devops`).
  Future may need:
  - Per-tenant external-system configuration (different orgs use
    different in-house tools)
  - Runtime registration of external systems (without migration)
  - Per-system metadata (API endpoints, link format templates,
    content fetch interfaces)
  Evolution path: an `external_systems` registry table replaces
  the enum; `test_requirement_links.external_system` becomes a
  foreign key. The schema-shape commitment today is that
  `external_system` is a *typed identifier* — enum or FK
  depending on stage — not an irrevocable type choice.

- **Sprint / release / project associations.** These are external-system
  concerns (JIRA-side metadata, not PrimeQA-side). If they need to
  be queryable through PrimeQA, that's UX plumbing over external API
  queries, not substrate-2 schema.

- **Bidirectional sync (PrimeQA → external).** Out of substrate-2
  scope. A future integration layer could push PrimeQA test
  results back to JIRA tickets as comments or status updates;
  not the substrate's responsibility.

See `DECISIONS_LOG.md` D-063 for rationale and alternatives
considered.

---

## 10. Outward surfaces (consumed by S3, S4, S6, S8)

**Resolution.** Per D-064: substrate-2's outward surface is the
**Semantic Transaction Coordinator**, framed as **semantic OS
infrastructure** rather than as a substrate-internal component.
The Coordinator exposes five interface groups, each with
explicit behavioral contracts (idempotency, authority, atomicity,
error, concurrency). Three Coordinator-level operations are
named **resolution-class operations** — first-class substrate
concepts that compose substrate rules rather than executing
simple queries. Wire format (Python-direct, gRPC, REST) is
unspecified at the substrate level; behavioral contracts are not.

### 10.1 The Coordinator as semantic OS infrastructure

The Semantic Transaction Coordinator (defined in §4.7.5) is **the
substrate's outward surface.** Consuming substrates (S3, S4, S6,
S8) interact with substrate-2 exclusively through the Coordinator
interface. Direct DB queries bypass the Coordinator's invariants
and may return results that violate substrate guarantees.

The Coordinator is no longer a "substrate-2 component" but
**semantic OS infrastructure** — the kernel through which all
substrate operations route, the surface against which all
consuming substrates build, the locus where consistency
invariants and authority rules are enforced.

Consequences of this framing:

- The Coordinator's interface stability is **foundational** —
  changes ripple to all consuming substrates.
- The Coordinator's behavioral contracts (§10.3) are first-class
  architectural commitments, not implementation conventions.
- Future substrates (S1 Coordinator, S4 Coordinator) may form a
  Coordinator family with cross-coordinator concerns; substrate-2's
  Coordinator is the first instance of what may become a platform
  pattern.

### 10.2 Five interface groups

The Coordinator exposes interfaces organized by consumer concern.
Each group serves multiple substrates:

**(1) Write interfaces — actor-aware, authority-enforced:**

```
coordinator.write_claim(actor, claim_data) → ClaimWriteOutcome
coordinator.write_recipe(actor, recipe_data) → RecipeWriteOutcome
coordinator.promote_claim_to_approved(actor, test_id, version_seq) → ()   [human-only]
coordinator.deprecate_claim(actor, test_id, version_seq) → ()             [human-only]
coordinator.deprecate_recipe(actor, recipe_id, version_seq) → ()          [human-only]
coordinator.surface_unblessed_transition(actor=S8, test_id, reason) → ()  [provenance event only]
```

`ClaimWriteOutcome` includes: `test_id`, `version_seq`, `outcome`
enum (`new_version` / `noop_equivalent` / `authority_violation`),
`identity_hash`, `identity_hash_version`. S3's hash-preserving
regeneration receives `noop_equivalent`; the Coordinator skips
writing.

**(2) Read interfaces — current-approved vs latest distinction:**

```
coordinator.get_current_approved_claim(test_id) → ClaimView | None    [resolution operation]
coordinator.get_latest_claim(test_id) → ClaimView | None
coordinator.get_claim_version(test_id, version_seq) → ClaimView | None
coordinator.list_active_recipes(test_id) → list[RecipeView]
coordinator.select_recipe_for_execution(test_id, context) → RecipeView | None  [resolution operation]
```

`*View` return types are typed Pydantic objects per D-060. Recipes
returned by `select_recipe_for_execution` are selected per
substrate-level policy (§10.4).

**(3) Equivalence and discovery interfaces:**

```
coordinator.query_equivalent_claims(canonical_form, identity_hash_version) → list[test_id]
coordinator.list_tests_affected_by_entity(entity_id) → list[test_id]    [uses coverage]
coordinator.list_tests_by_requirement(external_system, external_key) → list[test_id]
```

**(4) Runtime state interfaces (per §8):**

```
coordinator.report_run_outcome(actor=S4, run_id, recipe_id, recipe_version_seq, outcome, ran_at) → ()
coordinator.get_recipe_runtime_state(recipe_id) → RuntimeStateView
coordinator.get_test_runtime_status(test_id) → TestRuntimeStatus   [resolution operation]
```

**(5) Provenance interfaces:**

```
coordinator.get_provenance(test_id, time_range=None, event_kinds=None) → list[ProvenanceEvent]
coordinator.get_recipe_provenance(recipe_id, time_range=None) → list[ProvenanceEvent]
```

### 10.3 Behavioral contracts per interface

Behavioral contracts are **substrate-level commitments**, not
implementation conventions. Each interface declares:

- **Idempotency** — On what key is the operation idempotent?
  - `write_claim` is idempotent on canonical content (hash-preserving
    regeneration returns `noop_equivalent`)
  - `write_recipe` is idempotent on `(actor, recipe_id, version_seq)`
    for retry scenarios
  - `report_run_outcome` is idempotent on `run_id`
  - `promote_claim_to_approved` is idempotent (already-approved
    version returns success no-op)
- **Authority** — Which actors may call this interface?
  - Per D-061 §7.2 per-actor scope
  - Authority violations raise `AuthorityViolationError`
- **Atomicity** — Which interfaces are transactional?
  - All write interfaces are atomic across the relevant tables
    (test_claims + test_claim_coverage + test_provenance for
    claim writes; test_recipes + test_provenance for recipe writes;
    test_recipe_runtime_state + test_provenance for run-outcome
    reports)
- **Error contracts** — Which error types may be raised?
  - Per D-060 §4.7.8 + `AuthorityViolationError` per D-061
  - Each interface documents its possible error types
- **Concurrency** — How does the interface behave under concurrent
  access?
  - Writes use DB-level conflict detection (per `version_seq` PK
    uniqueness); concurrent writes resolve via retry
  - Reads are consistent within a transaction; cross-transaction
    reads see the latest committed state
- **Performance asymptotics** (where commitments are warranted)
  - Hot-path resolution operations
    (`get_current_approved_claim`, `get_test_runtime_status`,
    `select_recipe_for_execution`) should be O(constant) or O(log n)
  - Discovery operations
    (`list_tests_affected_by_entity`) are O(coverage rows for
    entity) — bounded by entity fan-out

These contracts are part of substrate-2's architectural commitment
and visible at the API boundary.

### 10.4 Resolution-class operations

Three Coordinator interfaces are **resolution-class operations** —
first-class substrate concepts that compose substrate rules
rather than executing simple queries:

| Operation | Resolves | Composes |
|---|---|---|
| `get_current_approved_claim` | Version history → canonical current-approved | Status events, deprecation, policy-version scenarios |
| `get_test_runtime_status` | Recipe runtime state → test-level composition | Recipe outcomes, approval state, conservative initial policy |
| `select_recipe_for_execution` | Multi-recipe + context → selected recipe | Environment matching, priority, approval state, replay mode, S8-blessing |

Resolution operations are distinguished from lookups by:

- **Composition over substrate rules** — not single-row queries
- **Governance / policy implications** — the resolution rules are
  architectural commitments, not implementation choices
- **Future-extensible** — new factors can be added to the
  resolution policy without consumer changes

Resolution operations are named as a substrate-level pattern.
Future resolution operations (S6 attribution clustering, S8
evolution prioritization, etc.) will follow this pattern rather
than reinvent the architectural slot.

### 10.5 Wire format reservation

The substrate's commitment is the **Coordinator interface and its
behavioral contracts**, not a specific wire format. Concrete wire
formats — Python-direct calls (in-process), gRPC (cross-service),
REST (HTTP), or others — are deployment concerns.

Forward-compat: the substrate does not commit to a single wire
format. The Coordinator's interface is the architectural anchor;
wire formats may multiply (e.g., Python-direct for in-process
consumers, gRPC for cross-service consumers) without changing
the substrate's commitment.

### 10.6 Forward-compatibility reservations

Three architectural directions reserved without action in v1:

- **Cross-substrate Coordinator concerns.** As future substrates
  develop their own Coordinators (S1, S4, S6, S8), patterns for
  cross-coordinator coordination may emerge — e.g., distributed
  transactions across substrate boundaries, cross-substrate query
  composition. The "Coordinator family" framing reserves this
  evolution path.

- **API versioning.** Today the Coordinator's interface is single-version;
  changes are breaking. Future may need explicit API versioning
  (consumers pin to interface versions; substrate maintains backward
  compatibility for declared versions). Reserved.

- **Behavioral contract evolution.** Performance asymptotics and
  concurrency guarantees may strengthen as the substrate matures
  (e.g., from "O(log n)" to "O(1) with caching" for hot-path
  operations). Reserved.

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
- **DROP** — Content is not retained in PrimeQA v2 (or is
  replaced by a different mechanism that doesn't require
  migration).
- **MIGRATE** — Content lives in a separate (TBD) substrate in
  v2; not part of substrate-2.

### 11.2 Per-table disposition

| v2.2 Table | Disposition | Rationale |
|---|---|---|
| `sections` | MIGRATE | Organizational/curation concern; not test-representation. Future "test catalog" or "organizational" substrate. |
| `requirements` | DROP | Per D-063 (S2-Q-008), requirements are external typed references. `test_requirement_links` provides linkage; ticket content stays in JIRA. No PrimeQA-side replication. |
| `test_cases` | ABSORB | Replaced by S2's `test_claims` + `test_recipes` (per D-056). Migration: each v2.2 test_case becomes a claim + one or more recipes per the six-layer model. |
| `test_case_versions` | DROP | Replaced by effective-time supersession (per D-057). `version_seq` on `test_claims` / `test_recipes` provides version history without a separate table. |
| `test_suites` | MIGRATE | Suite-membership is curation, not test-representation. Lives in the same future "test catalog" substrate as `sections`. |
| `suite_test_cases` | MIGRATE | Same as `test_suites` — curation layer. |
| `ba_reviews` | MIGRATE | BA review is a workflow concept distinct from substrate-2's approval-state lifecycle (which is mechanical per D-061). Future "review workflow" substrate. |
| `metadata_impacts` | DROP | Absorbed into S1-diff-driven evolution per S8 (future substrate). Not S2's job to replicate; S8 derives impacts from S1's bitemporal history at evolution time. |

### 11.3 Intentional architectural trade-off

The four MIGRATE dispositions create an explicit gap:
substrate-2 v1 doesn't handle sections, suites, or BA reviews.
Teams using v2.2 features in those areas have a feature gap
during transition.

This is **not a pressure point to be mitigated — it is a
deliberate architectural commitment.** The substrate's
coherence is more valuable than short-term feature parity.

- **Short-term cost:** v2.2 features unavailable in v2 until
  orthogonal substrates ship
- **Long-term gain:** each concern lives in its own substrate
  with clean boundaries; future evolution of each concern happens
  independently

Each MIGRATE-targeted concern represents a *separate substrate's
responsibility*, not substrate-2's. Absorbing them into S2 would
compromise the substrate boundary that makes the platform
architecture coherent.

The gap is real; the gap is acceptable; the gap is intentional.

### 11.4 Migration strategy (high-level)

For ABSORB-dispositioned content:

- v2.2 `test_cases` + `test_case_versions` → v2 `test_claims` +
  `test_recipes` via S3-assisted decomposition. Each v2.2 test
  → claim + one or more recipes per the six-layer model. The
  procedural steps in v2.2 become recipe bodies; the asserted
  truth must be extracted, often via LLM-assisted parsing.

For DROP-dispositioned content:

- `requirements` content not migrated; instead,
  `test_requirement_links` is populated from v2.2's
  test-to-requirement relationships.
- `test_case_versions` content not migrated; effective-time
  supersession replaces; v2.2 version history is provenance-only
  (recorded as `claim_created` events in v2 provenance).
- `metadata_impacts` content discarded; S1 + S8 reconstruct as
  needed.

For MIGRATE-dispositioned content:

- Out of substrate-2's v1 scope. Migration deferred until the
  receiving substrates ship. v2.2 tables can be retained
  in-place under separate ownership during transition.

**Detailed migration execution** (data scripts, validation,
rollback procedures) is implementation work post-Phase-3, not
substrate design.

### 11.5 Forward-compatibility reservations

The MIGRATE dispositions create implicit dependencies on future
substrates:

- **Test catalog substrate** (for `sections`, `test_suites`,
  `suite_test_cases`) — organizational/curation concerns
- **Review workflow substrate** (for `ba_reviews`) — workflow
  concerns distinct from substrate-2's mechanical approval
  lifecycle

These substrates ship later. Substrate-2 v1 ships first; the
orthogonal substrates ship in subsequent phases as their scope
becomes clear. The substrate roadmap is sequential and deliberate.

See `DECISIONS_LOG.md` D-065 for rationale and alternatives
considered.
