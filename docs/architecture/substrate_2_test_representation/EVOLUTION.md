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

Filled in SPEC §3 with the locked claim-kind taxonomy. 16 kinds
across 5 archetypes. Notable consolidations: data-behavior
absorbs `automation-effect-claim` (VR + Flow merged) and
`prohibition-claim` (deletion-blocked + duplicate-prevention
merged); configuration absorbs `activation-claim` into
`property-claim`; UI absorbs `element-visibility-claim` into
`element-state-claim`; integration kinds remain distinct on
semantic-form grounds. Added second guardrail in §3: claim-kinds
model semantic forms, not implementation primitives.

S2-Q-003 sub-cycle 1 marked complete. D-053 added to DECISIONS_LOG.md.

---

## 2026-05-17 — S2-Q-003 sub-cycle 2: recipe-kind taxonomy locked (D-054); S2-Q-011 opened

Filled in SPEC §3 with the locked recipe-kind taxonomy. Five
kinds: `data-recipe`, `metadata-recipe` (with `metadata-read` and
`metadata-write` modes), `ui-recipe`, `event-subscription-recipe`,
`callout-intercept-recipe`. Added third guardrail: recipe-kinds
classify observability semantics only. S2-Q-011 (Trigger-kind
classification) opened as parallel architectural question.

S2-Q-003 sub-cycle 2 marked complete. D-054 added to DECISIONS_LOG.md.

---

## 2026-05-17 — S2-Q-011 resolved: trigger-kind taxonomy + four-discriminator extension + six-layer model amendment (D-055)

Five interrelated architectural commitments: fourth orthogonal
discriminator `trigger_kind`; six-layer structural model (adds
"Causal initiation" layer); terminology supersession ("execution
realization" → "observation realization"); five trigger-kinds
locked (`inbound-trigger`, `data-mutation-trigger`, `ui-trigger`,
`time-trigger`, `configuration-trigger`); two new guardrails
(trigger-kind purity, trigger-vs-recipe orthogonality).

S2-Q-011 moved to Resolved. D-055 added to DECISIONS_LOG.md.

---

## 2026-05-17 — S2-Q-003 sub-cycle 3 + S2-Q-005 jointly resolved: storage realization + effective-time supersession (D-056, D-057)

Largest combined cycle in substrate-2 work so far. Two tightly
coupled architectural questions resolved together.

**D-056 — Storage realization:** four-table layout (test_claims,
test_recipes, test_provenance, test_claim_coverage); Pattern D
(envelope + JSONB + hot-path typed columns); claim/recipe split;
row-discriminator-as-canonical-authority; JSONB body conventions
with `body_schema_version`; semantic linkage layer framing;
polymorphic provenance; app-level coverage derivation. Two new
guardrails: semantic-vs-operational lifecycle distinction;
continuity triad.

**D-057 — Lifecycle and versioning:** effective-time supersession
(NOT bitemporal); `version_seq` canonical over `valid_to`;
`identity_hash` as semantic equivalence fingerprint;
canonicalization policy elevated to governance-critical;
recipe-to-claim FK logical-default with pinning opt-in;
current-only coverage; no archival (lineage-continuity
rationale); replay modes reserved; three forward-compat
reservations.

§4 and §6 of SPEC.md filled with substantive content for the
first time. S2-Q-005 moved to Resolved; S2-Q-003 sub-cycle 3
marked complete. D-056 and D-057 added to DECISIONS_LOG.md.

---

## 2026-05-17 — S2-Q-004 resolved: reference model — hybrid by layer with ontology-enforcement validation (D-058)

Hybrid-by-layer reference resolution: pinned required in
identity-bearing layers; logical default with pinned opt-in in
operational layers. Reference shapes with `ref_kind`
discriminator. Identity_hash canonicalizes `entity_id` only.
Coverage from identity-bearing layer pinned refs only.
Cross-layer validation as ontology enforcement. Semantic replay
forward-resolution via S8-blessed transitions only. external_id
drift multi-mode (rename / move / replace / namespace /
inheritance / metadata quirks). Weighted semantic linkage
reservation.

§5 of SPEC.md filled with substantive content. S2-Q-004 moved to
Resolved. D-058 added to DECISIONS_LOG.md.

---

## 2026-05-18 — S2-Q-003 sub-cycle 4 resolved: identity-hash mechanics + governance contract (D-059)

Sub-cycle 4 of S2-Q-003 locked. Governance-critical work per
D-057's elevation: the canonicalization policy mechanically
determines approval invalidation, S8's autonomous-rewrite
authority boundary, and cross-test semantic equivalence
reasoning.

Hash input scope (archetype + claim_kind + canonicalized
asserted_truth + canonicalized semantic_conditions).
Canonicalization rules (strict, RFC 8785-ish; array semantics
schema-declared). SHA-256 hex. Canonicalization policy
versioned via `identity_hash_version` column.

Six-rule governance contract including two-gate evaluation
framing for S8 evolution through entity changes (Gate 1: hash
preservation; Gate 2: entity-evolution semantic compatibility,
S8-design territory). Semantic projection fields reserved.

§6.3 of SPEC.md expanded with substantive content. §4.1
`test_claims` table extended with `identity_hash_version`
column. S2-Q-003 sub-cycle 4 marked complete. D-059 added to
DECISIONS_LOG.md.

---

## 2026-05-18 — S2-Q-003 sub-cycle 5 resolved: validation layering and the Semantic Transaction Coordinator (D-060); S2-Q-003 fully resolved

Sub-cycle 5 of S2-Q-003 locked. Composes prior commitments
(D-051 through D-059) into a coherent validation architecture.

Three complementary enforcement layers (DB / Pydantic / Schema)
— not hierarchical. Pydantic model organization with two-level
discriminator dispatch. Reference type hierarchy with semantic
role preservation: `IdentityBearingRef(PinnedRef)` as distinct
type. Semantic field descriptors via `Annotated[T, Marker]`
uniformly. The Semantic Transaction Coordinator elevated to
named substrate-level component. Hash computation in Coordinator
via pure functions; never recomputed on read within
`(identity_hash, identity_hash_version)` regime. Four read-path
error types distinguished (`SchemaIncompatibilityError`,
`BodyCorruptionError`, `OntologyViolationError`,
`ValidationError`). Migration handling as governance work.

§4.7 of SPEC.md filled with substantive content. §5.5, §6.3.10,
§6.3.11 received small cross-references. S2-Q-003 sub-cycle 5
marked complete; **S2-Q-003 now fully resolved** across all five
sub-cycles. D-060 added to DECISIONS_LOG.md.

---

## 2026-05-18 — S2-Q-006 resolved: mutation paths and authority over meaning (D-061)

Sub-cycle 6 of substrate-2 work. Composition over invention —
the authority machinery was fully locked by D-057, D-059, D-060;
this resolution formalizes the three mutation paths and per-path
rules built on that machinery.

**Three mutation paths** (human / S3 / S8) routed by the
Semantic Transaction Coordinator with per-path authority rules.

**S8 invariant refined to "no autonomous semantic divergence."**
The earlier framing ("S8 cannot mutate identity-bearing
content") was misleading — S8 *does* mutate identity-bearing
JSONB content when bumping pinned-ref `version_seq` forward.
What S8 cannot do is cause semantic divergence (hash change).
The invariant is mechanical, not layer-based. Hash preservation
operates *within* identity-bearing layers, not as a fence
*around* them.

**Identity continuity (test_id) and semantic continuity
(identity_hash, scoped to identity_hash_version)** as orthogonal
dimensions of test continuity across mutations.

**Trust boundary asymmetry:**
- Claim approval: governed by `identity_hash` change (mechanical,
  per D-057 Rule 2 / D-059 Rule 2)
- Recipe re-approval: **conservative default**, not intrinsic
  asymmetry. The substrate currently lacks a mechanical detection
  mechanism for "this recipe edit didn't meaningfully change
  behavior"; without such a mechanism, the safe default is
  re-approval. Future evolution (recipe content hashing, recipe
  approval-preservation declarations) could relax this default.

**Linear supersession preserved.** "Latest" vs "current-approved"
as distinct query notions.

**Current-approved as governance resolution, not simple status
lookup.** `get_current_approved_claim(test_id)` is a Coordinator
governance operation interpreting version history per substrate
rules; downstream substrates must use the Coordinator interface,
not direct DB queries.

**Test-level approval as derived composition** in Coordinator
(`get_test_approval_status(test_id)` → fully_approved /
claim_approved_recipe_pending / draft).

**Rollback via supersession, not status mutation.** Status enum
doesn't permit `approved` → `draft` direct transition; rollback
creates a new draft version that supersedes the prior approved.

**Edge cases handled:** concurrent structural writes (DB
conflict + retry); concurrent semantic conflicts (linear
supersession, future merge/rebase reserved); S3 hash-preserving
regeneration as no-op skip; cross-test equivalence as query
(no auto-merge); claim references deleted entity surfaces for
review; approval rollback via new draft version.

**Four forward-compat reservations:**
- Recipe approval auto-preservation (when detection mechanism
  exists)
- Merge/rebase semantics for concurrent semantic conflicts
- Provenance streams as named taxonomy (current single
  `event_kind` becomes multi-stream)
- Deprecation kinds as sub-status taxonomy (current single
  `deprecated` status conflates multiple states)

TA refinements integrated:
- S8 invariant reframed as "no autonomous semantic divergence"
  (was misleadingly suggesting S8 can't touch identity-bearing
  layers at all)
- Recipe re-approval as conservative default (not intrinsic
  asymmetry)
- Current-approved as governance resolution (not simple lookup)
- Concurrent semantic conflicts merge/rebase reservation added
- Provenance multi-stream taxonomy reservation
- Deprecation taxonomy reservation

§7 (Mutation paths and authority) of SPEC.md filled with
substantive content for the first time; previously placeholder.
§2's S2-Q-006 cross-reference updated to reflect mechanical
authority framing. §4.7.6 write-flow extended with authority
enforcement step. §4.7.8 added `AuthorityViolationError` to
read-path error types. §6.3.9 Rule 1 phrasing refined to
"no autonomous semantic divergence" with explicit explanatory
text. §6.6 cross-reference to §7 added.

S2-Q-006 moved to Resolved. D-061 added to DECISIONS_LOG.md.

Substrate-2 SPEC §2, §3, §4, §5, §6, §7 now substantively
complete. Remaining: §1 overview, §8 execution-history boundary
(S2-Q-007), §9 requirement linkage (S2-Q-008), §10 outward
surfaces (S2-Q-009), §11 v2.2 disposition (S2-Q-010). Eleven
D-entries committed (D-051 through D-061). The substrate's
internal coherence is now fully specified; remaining work is
boundaries with other substrates (S4, JIRA), API surface, and
the v2.2 transition.
