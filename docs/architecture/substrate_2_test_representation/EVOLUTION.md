# Substrate 2 — Test Representation — Evolution Log

Append-only. One entry per session that made substantive changes to
this substrate's docs.

---

## 2026-05-16 — Substrate skeleton created

Initial skeleton. No design decisions yet. Created:

- `BACKGROUND.md` — why this substrate exists, what it replaces,
  what's in and out of S2 scope, relationship to S1 and downstream
  substrates (S3, S4, S6, S8). Includes architectural-ambition
  positioning (S2 as semantic anchor complementary to S1) and
  human-legibility as design principle.
- `SPEC.md` — header + status block + section placeholders for
  Phase 1 (conceptual) and Phase 2 (concrete) design phases.
- `GLOSSARY.md` — empty seed.
- `OPEN_QUESTIONS.md` — seeded with S2-Q-001 through S2-Q-010
  covering the design surfaces identified at Phase 3 kickoff,
  refined by TA pushback to surface (a) assertion as fifth
  invariant candidate, (b) structural vs semantic uniformity split
  in archetype commonality, (c) reproducibility-vs-evolvability
  tension as the lead frame for S1 references, (d) authority over
  meaning as the deeper question under mutation paths.

No `DECISIONS_LOG.md` entries yet. First substantive decision will
be S2-Q-001 (deepest invariant) addressed in Phase 1 SPEC design.

---

## 2026-05-16 — S2-Q-001 resolved: claim as identity-bearing root (D-051)

Filled in SPEC §2 (Deepest invariant) per the resolution that a
PrimeQA test case is fundamentally a structured claim — asserted
system truth plus semantic conditions — realized through replaceable
executable recipes. Five-layer model: two identity-bearing layers
(asserted truth, semantic conditions); three non-identity-bearing
layers (execution realization, execution environment, provenance);
coverage derived. Discipline rule landed explicitly: "if referenced
in the claim → semantic; otherwise operational." Claim structure
intentionally constrained (not a general system-specification
language). Atomic-canonical direction noted, structural shape
pending S2-Q-003.

S2-Q-001 moved to Resolved in OPEN_QUESTIONS.md. D-051 added to
top-level DECISIONS_LOG.md.

Adjacent open questions whose context the resolution updates:
S2-Q-002 (structural uniformity via the five-layer model is now
established), S2-Q-004 (claim references lean pinned; recipe
references lean logical), S2-Q-006 (authority boundary now concrete
— S8 autonomous on the three non-identity layers, human-required
for the two identity-bearing layers).

---

## 2026-05-16 — S2-Q-002 resolved: three orthogonal discriminators with archetype-specific semantic forms (D-052)

Filled in SPEC §3 (Archetype representation) per the resolution
that test cases are classified along three orthogonal
discriminators — `archetype`, `claim_kind`, `recipe_kind`. The
five-layer model from §2 is structurally uniform across all five
archetypes; within each layer the line falls between a uniform
discriminator-bearing envelope and archetype-specific semantic
forms. Guardrail stated: archetypes are classifications, not
storage partitions. Execution environment sharpened from "setup
payload" to "capability assumptions and setup the recipe relies
on" — both in §3 narrative and via a small edit to §2's table
description for consistency. Seeded claim-kind taxonomy (4-6
kinds per archetype) included; lock deferred to S2-Q-003.

S2-Q-002 moved to Resolved in OPEN_QUESTIONS.md. D-052 added to
top-level DECISIONS_LOG.md.

Deferred to S2-Q-003: recipe-kind taxonomy, concrete storage
shape (table layout, JSONB schemas, Pydantic validation),
cross-archetype claim-kind consolidation, capability-assumption
model's interaction with environment-availability metadata.

---

## 2026-05-16 — S2-Q-003 sub-cycle 1: claim-kind taxonomy locked (D-053)

Filled in SPEC §3 with the locked claim-kind taxonomy, replacing
the §3 "Seeded claim-kind taxonomy" subsection. 16 kinds across
5 archetypes. Notable moves from the §3 first-draft seeds:

- Data-behavior: merged VR-firing + Flow-effect into
  `automation-effect-claim`; merged deletion-blocked +
  duplicate-prevention into `prohibition-claim` (renamed from
  `operation-blocked-claim` per TA invariant-orientation
  pushback).
- Configuration: absorbed `activation-claim` into
  `property-claim`.
- Permission: kept Option B (`capability-claim` +
  `sharing-rule-claim` distinct) per TA confirmation.
- UI: absorbed `element-visibility-claim` into
  `element-state-claim`.
- Integration: kept platform-event-claim, outbound-message-claim,
  callout-claim distinct per TA split-pushback (different
  semantic forms, not just different implementation primitives).

Articulated the state-transition vs automation-effect
distinction: state-transition asserts the resulting end state
(mechanism-agnostic); automation-effect asserts a specific
automation firing and its side effects (mechanism-specific).
Dividing test: would the test still mean the same thing under a
different implementing primitive?

Added second guardrail in §3: claim-kinds model semantic forms,
not implementation primitives. Parallel to D-052's "archetypes
are classifications, not storage partitions."

S2-Q-003 entry in OPEN_QUESTIONS.md expanded to enumerate
sub-cycles 1-5 with sub-cycle 1 marked as locked. D-053 added
to top-level DECISIONS_LOG.md.

Next sub-cycle: recipe-kind taxonomy (S2-Q-003 sub-cycle 2).

---

## 2026-05-17 — S2-Q-003 sub-cycle 2: recipe-kind taxonomy locked (D-054); S2-Q-011 opened

Filled in SPEC §3 with the locked recipe-kind taxonomy. Five
kinds: `data-recipe`, `metadata-recipe` (with `metadata-read` and
`metadata-write` modes), `ui-recipe`, `event-subscription-recipe`,
`callout-intercept-recipe`.

Key moves during sub-cycle 2:

- `crud-recipe` renamed `data-recipe` (broader semantic, matches
  the observability-domain pattern of other kinds; "CRUD" was
  leaking implementation-primitive vocabulary into the kind name).
- `metadata-recipe` clarified with `metadata-read` and
  `metadata-write` named sub-discriminator modes — different
  capability assumptions and risk profiles.
- `event-subscription-recipe` vs `callout-intercept-recipe` split
  justified on semantic-vocabulary grounds (Salesforce-event
  payload structure vs HTTP-request structure), not transport
  grounds.
- Inbound injection considered as recipe-kind (Option A) and
  rejected per Option B: it's a triggering pattern, not an
  observability domain.

Added third guardrail in §3: recipe-kinds classify observability
semantics only. Parallel to D-052's archetype classification rule
and D-053's claim-kind semantic-form rule.

S2-Q-011 (Trigger-kind classification) opened as a parallel
architectural question per TA strong-recommend. Refinements
integrated into S2-Q-011's content per TA pushback: internal data
mutation as causal initiation (distinct from `data-recipe`
observation), UI-trigger vs UI-recipe distinction, time-based
trigger importance preserved as first-class, trigger-kind purity
guardrail proposed.

S2-Q-003 sub-cycle 2 marked complete in OPEN_QUESTIONS.md.
D-054 added to top-level DECISIONS_LOG.md.

Next: S2-Q-003 sub-cycle 3 (storage realization) and S2-Q-011
(trigger-kind classification) become parallel design tracks.

---

## 2026-05-17 — S2-Q-011 resolved: trigger-kind taxonomy locked + four-discriminator extension + six-layer model amendment (D-055)

Five interrelated architectural commitments landed in this cycle:

1. Fourth orthogonal discriminator added — `trigger_kind` joins
   archetype, claim_kind, recipe_kind. Extends D-052's
   three-discriminator framing.
2. Six-layer structural model — extends D-051's five-layer model
   with a new "Causal initiation" layer for trigger realization.
3. Terminology supersession — "Execution realization" (D-051)
   renamed to "Observation realization" per D-054's recipe-kind
   purity scope. §2's table updated for consistency. Term may be
   further refined in future cycles.
4. Five trigger-kinds locked: `inbound-trigger`,
   `data-mutation-trigger`, `ui-trigger`, `time-trigger`,
   `configuration-trigger`. Plane column distinguishes runtime
   (four kinds) from model-plane (configuration-trigger).
5. Two new guardrails: trigger-kind purity (parallel to the three
   existing purity guardrails) and trigger-vs-recipe orthogonality
   (about relationship between two discriminators).

Key TA refinements integrated:

- Time-trigger narrowed to "Salesforce mechanisms firing because
  elapsed-time predicates were met" — NOT general async/retry/queue
  semantics (those are downstream behaviors observed by recipes).
- Configuration-trigger elevated to cross-plane structural
  treatment, not just a label: explicit test-runtime risk,
  shared-org coordination requirement, S8-adjacency noted.
- Trigger identity nuance: operational by default, semantic if
  the mechanism is asserted in the claim per D-051's discipline
  rule.
- One primary trigger per test (default) — softer than "one
  trigger per test"; acknowledges composite scenarios while
  favoring the simple case.
- Four-axes summary table added to §3 (TA framing, verbatim).

S2-Q-011 moved to Resolved in OPEN_QUESTIONS.md. D-055 added to
top-level DECISIONS_LOG.md.

Phase 1 of substrate-2 is now substantively complete on the
conceptual side: deepest invariant + archetype representation +
all three taxonomies + four-discriminator framing + six-layer
model. Remaining S2-Q-003 sub-cycles (storage, identity-hash,
validation) move from conceptual to concrete data model.

Next: S2-Q-003 sub-cycle 3 (storage realization).

---

## 2026-05-17 — S2-Q-003 sub-cycle 3 + S2-Q-005 jointly resolved: storage realization + effective-time supersession (D-056, D-057)

Largest combined cycle in substrate-2 work so far. Two tightly
coupled architectural questions resolved together.

**D-056 — Storage realization:**

- Four-table layout: `test_claims`, `test_recipes`,
  `test_provenance`, `test_claim_coverage`.
- Pattern D selected (envelope + JSONB + hot-path typed columns).
  Patterns B and C rejected as direct violations of D-052's
  archetype-classification guardrail.
- Claim/recipe split honors D-051's identity model — claim is
  identity, recipes are first-class operational entities with
  independent lifecycle.
- Discriminator placement: archetype/claim_kind on claims;
  trigger_kind/recipe_kind on recipes.
- Row discriminator as canonical authority; JSONB body `kind`
  field is redundant self-description; row wins on disagreement.
- JSONB body conventions: `body_schema_version` + `kind` as
  top-level keys; body shape dispatched on (row discriminator,
  `body_schema_version`).
- Semantic linkage layer framing for `test_claim_coverage` —
  first-class architectural concern (S2↔S1 bridge), not
  denormalized cache.
- Polymorphic provenance — nullable claim_test_id / recipe_id
  with CHECK constraint; event-kind discriminator distinguishes
  levels.
- App-level coverage derivation by S3 / S8 writers.

Two new guardrails landed in §3:
- *Sixth — Semantic-vs-operational lifecycle distinction.*
  Identity-bearing changes require human authority + invalidate
  approval; operational changes can be S8-autonomous + preserve
  approval.
- *Seventh — Continuity triad.* Stable identifiers, identity_hash,
  and version_seq model three distinct continuities; substrate
  honors this separation throughout.

**D-057 — Lifecycle and versioning model:**

- Effective-time supersession (NOT bitemporal — single time
  dimension; term reserved for future transaction-time
  escalation).
- `version_seq` is canonical supersession authority; `valid_to`
  is denormalized convenience. When they disagree, version_seq
  wins.
- `identity_hash` is semantic equivalence fingerprint — not
  unique, not key; multiple rows may share it across a test's
  version timeline. Operational edits preserve hash; semantic
  edits change it and invalidate approval.
- Canonicalization policy is **governance-critical** —
  sub-cycle 4's scope elevated from "compute hash" to "define
  governance for semantic vs operational edit boundary." Governs
  approval invalidation and S8 authority.
- Recipe-to-claim FK: logical-default (claim_test_id) with
  pinning opt-in (nullable claim_version_seq).
- Coverage current-only; rederived on claim version change.
- Approval state dual-tracked: current on row.status; history
  in provenance.
- No archival in v1 — semantic lineage continuity is
  architecturally more valuable than retention optimization.
  Not cost-driven.
- Replay modes (historical / semantic) supported by storage
  shape; replay engine downstream.
- Reservations: replay-sensitive recipe selection;
  version-granular provenance; reference resolution policies.

§4 (Data model) and §6 (Lifecycle and versioning) sections of
SPEC.md filled with substantive content for the first time;
both previously placeholders.

S2-Q-005 moved to Resolved. S2-Q-003 sub-cycle 3 marked complete;
sub-cycle 4 entry expanded with governance-critical framing.
D-056 and D-057 added to top-level DECISIONS_LOG.md.

Substrate-2 SPEC is now substantively complete on §2, §3, §4, §6.

Next: S2-Q-003 sub-cycle 4 (identity-hash mechanics; now framed
as governance work) and S2-Q-004 (S1 references; coupled with
the just-resolved versioning model).

---

## 2026-05-17 — S2-Q-004 resolved: reference model — hybrid by layer with ontology-enforcement validation (D-058)

Resolved the deepest pressure in S2's reference design:
reproducibility vs evolvability for S1 references. Four candidate
models considered (pinned everywhere, logical everywhere, hybrid
by reference kind, both with explicit conversion); hybrid-by-layer
selected as cleanest cut along the existing semantic-vs-operational
lifecycle boundary.

**Reference model:**

- Identity-bearing layers (asserted_truth, semantic_conditions):
  pinned references **required**. Pinned = entity_id + version_seq.
- Operational layers (causal_initiation, observation_realization,
  execution_environment): logical references **default**; pinned
  **allowed as opt-in** for reproducibility cases.
- Reference shapes: typed JSON objects with `ref_kind`
  discriminator; pinned carries (entity_id, version_seq,
  informational external_id); logical carries external_id only.

**Identity_hash canonicalization:** pinned refs contribute
entity_id only (not version_seq). S8 may bump version_seq forward
on pinned refs when entity evolution is blessed (operational
edit, hash preserved). Hash-changing rewrites require human
authority.

**Coverage:** pinned references from identity-bearing layers only.
Operational dependencies (recipe-derived) belong to the future
operational linkage layer per D-056's marker.

**Cross-layer validation is ontology enforcement.** Substrate-level
commitment, not Pydantic routine. Identity-bearing layers reject
logical refs at write time. Operational layers accept both.
Relaxing this rule is an architectural decision, not a refactor.

**Semantic replay refinement (SPEC §6.8 updated):** semantic
replay follows pinned references forward only via S8-blessed
transitions — entity evolutions S8 has validated as semantically
equivalent (those preserving identity_hash). Unblessed transitions
surface for human review rather than silent forward-resolution.
Makes follow-forward a deliberate capability, not a default.

**external_id drift is multi-mode.** S8's drift detection handles
six modes explicitly: rename, move, replace, namespace shift,
inheritance change, metadata-resolution quirks. Single-mode
framing is insufficient.

**Weighted semantic linkage reservation:** future evolution of
`test_claim_coverage.reference_kind` (currently binary
subject/condition) toward richer weighting preserved as
forward-compat marker.

TA refinements integrated:
- Semantic replay must not casually "follow forward" — S8-blessed
  transitions only
- Operational layers default to logical, allow pinned opt-in (not
  strict reject)
- external_id drift is multi-mode (deeper than name collision)
- Cross-layer validation as ontology enforcement framing
- Weighted semantic linkage reservation

§5 (References to S1 entities) of SPEC.md filled with substantive
content for the first time; previously placeholder. §6.8 (Replay
modes) refined to incorporate S8-blessed forward-resolution.

S2-Q-004 moved to Resolved. D-058 added to top-level DECISIONS_LOG.md.

Substrate-2 SPEC §2, §3, §4, §5, §6 now substantively complete.
Remaining: §1 overview, §7 mutation paths, §8 execution-history
boundary, §9 requirement linkage, §10 outward surfaces, §11 v2.2
disposition.

Next: S2-Q-003 sub-cycle 4 (identity-hash mechanics — governance-critical
canonicalization) now well-constrained by both D-057 (versioning
anchors) and D-058 (reference canonicalization scope).
