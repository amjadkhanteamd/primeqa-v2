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
