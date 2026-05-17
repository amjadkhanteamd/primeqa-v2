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
