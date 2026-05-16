# Substrate 2 — Test Representation — SPEC

**Status:** Skeleton. No design content yet. Design will proceed in
phases per the S1 precedent.

**Last substantive update:** 2026-05-16 (skeleton committed)

---

## Purpose

This spec will define Substrate 2: PrimeQA's canonical data structure
for a test case.

Design will proceed in two phases:

- **Phase 1 (pending):** Conceptual shape — what S2 is, its deepest
  invariant, archetype representation, lifecycle, mutation paths,
  relationship to S1 and downstream substrates.
- **Phase 2 (pending):** Concrete data model — tables, columns, JSONB
  shapes, references, versioning, execution-history boundary.

See `BACKGROUND.md` for why this substrate exists. See
`OPEN_QUESTIONS.md` for design surfaces currently under deliberation.

---

## Sections (placeholders — to be filled as design lands)

1. What Substrate 2 IS
2. Deepest invariant — what a test case essentially represents
3. Archetype representation — common substrate, archetype-specific
   bodies
4. Data model
5. References to S1 entities
6. Lifecycle and versioning
7. Mutation paths (human edit, S3 regenerate, S8 autonomous rewrite)
8. Execution-history boundary against S4
9. Requirement linkage
10. Outward surfaces (consumed by S3, S4, S6, S8)
11. Disposition of v2.2 test-management tables

Surfaces are tracked in `OPEN_QUESTIONS.md` until decided; on
resolution they move into the relevant section above and (where they
constitute architectural decisions) generate `DECISIONS_LOG.md`
entries.
