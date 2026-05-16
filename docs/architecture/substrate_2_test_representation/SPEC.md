# Substrate 2 — Test Representation — SPEC

**Status:** Phase 1 in progress. §2 (deepest invariant) resolved
per D-051; other sections pending.

**Last substantive update:** 2026-05-16 (Phase 1: deepest invariant)

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
| Execution environment  | Operational setup a recipe happens to use                | NO                |
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

## 3. Archetype representation — common substrate, archetype-specific bodies

(Placeholder. Pending future cycle.)

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
