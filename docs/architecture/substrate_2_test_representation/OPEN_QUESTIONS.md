# Substrate 2 — Test Representation — Open Questions

Questions specific to this substrate's design. Cross-cutting
questions live in the top-level `OPEN_QUESTIONS.md`.

---

## Open

### S2-Q-001 — Deepest invariant of a test case

What is a PrimeQA test case most essentially? Candidates surfaced in
`PRIMEQA_PRODUCT_DEFINITION.md` §4.3 and `PLATFORM_VISION.md`
§"Substrate 2":

- A *scenario* — declared situation with actors, preconditions,
  expected outcome. (A4 leaning.)
- A *covered slice of the semantic model* — set of S1 entities the
  test exercises. (S6 / S8 leaning.)
- An *execution recipe* — sequence of actions that can be run.
  (PLATFORM_VISION's "executable, human-readable, evolvable"
  leaning.)
- An *assertion / invariant / expected truth-condition* — a claim
  about the system that should hold (e.g. "When `Stage = Closed
  Won`, `Amount` is required"). Stable across execution-recipe
  rewrites, UI changes, generation regenerations; the assertion
  often outlives any particular procedure that tests it.
  (Specification leaning.)
- A *transcript of an LLM generation* — structured record of what
  was generated, why, against what context. (v2.2 leaning.)

These framings are not exclusive — a test case may layer all five.
The question is which is the ROOT / CENTER from which the others
derive their shape. Pick the deepest invariant; the structure
follows. Address before Phase 1 SPEC begins.

Linked: S2-Q-006 (the invariant choice constrains who has authority
to mutate which parts of a test case).

### S2-Q-002 — Common substrate across five archetypes (structural and semantic)

`PLATFORM_VISION.md` mandates: tests "across all five archetypes
using a common substrate — not five different representations." Two
layers hide inside this question and need to be answered
independently:

**A. Structural commonality.** What schema-level shape is shared
across all archetypes — envelope, provenance fields, lifecycle,
reference encoding, identity column.

**B. Semantic commonality.** What CONCEPTS are shared across all
archetypes — intent, assertion, executionability, coverage,
claim-about-truth.

These can diverge. v2.2 has schema uniformity (every
test_case_version has the same `steps` JSONB + `referenced_entities`
array) without semantic uniformity (the CRUD-step shape distorts
configuration / permission / UI / integration tests, because each
archetype has a fundamentally different concept of "what the test
asserts"). Schema uniformity without semantic uniformity is the
wrong kind of common substrate; semantic uniformity without schema
uniformity creates integration pain.

Decide both layers explicitly. The likely shape candidate is
scenario declaration + archetype-specific body + common envelope —
but where the line falls between common and archetype-specific *and
on which layer* is itself the design.

### S2-Q-003 — Test case data model

Given S2-Q-001 and S2-Q-002: concrete columns, tables, JSONB shapes.
Story view vs mechanical view (or both). Step ontology — if "steps"
survive S2-Q-002 as a unifying concept.

### S2-Q-004 — References to S1 entities: reproducibility vs evolvability

The central design tension in S2's reference model:

- *Pinned references* (entity UUID at specific `version_seq`) are
  **reproducible but brittle**. A test generated at v47 can be
  reconstructed exactly as it was meant; but it breaks if the
  referenced entity is later renamed or restructured.
- *Logical references* (`(entity_type, external_id)` resolved
  through S1's query interface) are **evolvable but semantically
  driftable**. The reference keeps working as the org changes; but
  it may now refer to something subtly different from what the test
  originally meant.

This trade-off is probably the deepest pressure in S2's reference
design. Candidate resolutions:

- *Pinned everywhere.* Tests reproduce exactly; S8 mass-updates
  references on each org change.
- *Logical everywhere.* Tests evolve transparently; S6 has to
  detect "the entity moved underneath me" cases.
- *Hybrid by reference kind.* Direct citations pinned (preserves
  intent); traversal-derived references logical (preserves
  liveness).
- *Both, with explicit conversion.* Every reference carries both
  a pinned UUID and a logical identity; consumers choose semantics
  per query.

Sub-question: how references cover both directly-cited entities and
traversal-derived entities (e.g., a Validation Rule that references
a cited Field).

Linked: S2-Q-005 (versioning model constrains which resolution is
natural — bitemporal S2 favors pinned-by-default; version-immutable
aggregate may favor logical-by-default); S2-Q-006 (S8's rewrite
scope is constrained by how much pinning has been done).

### S2-Q-005 — Lifecycle and versioning model

- Bitemporal supersession like S1 (new entity row per change, no
  in-place mutation).
- Version-immutable aggregate like v2.2 (`current_version_id` +
  immutable `test_case_versions`).
- Hybrid.

Has to land alongside S2-Q-003 because reference semantics from
S2-Q-004 depend on this choice.

### S2-Q-006 — Mutation paths and authority over meaning

A test case can change via at least three paths:

- Human edit.
- S3 regeneration (same JIRA ticket, new model version, different
  LLM output).
- S8 autonomous rewrite (field renamed, validation rule changed,
  flow deactivated).

The procedural question — what changes in the database — is easy.
The deeper question is: **who has authority to redefine the meaning
of a test?** If S8 rewrites steps after a field rename, did the test
remain "the same test"? The procedural answer ("same row, new
contents") is easy. The semantic answer — *is this still the test
the QA approved last sprint* — is hard, and product trust depends
on it.

The sub-questions:

- *Identity continuity.* Across each mutation path, what makes the
  test-after-mutation "the same test" as the test-before? Stable
  identifier? Unchanged assertion (if S2-Q-001 lands on assertion)?
  Unchanged scenario? Unchanged coverage set?
- *Trust boundary.* Which mutations preserve approved-state and
  which invalidate it? Human edit invalidates approval (re-review).
  Does S8 auto-rewrite? Does S3 regenerate?
- *Ownership authority.* Who can authorize each mutation path?
  Human authority is clear for edits; less clear who authorizes
  S8's autonomous changes — implicit (any field rename grants S8
  rewrite authority) or explicit (per-test opt-in).

Closely linked to S2-Q-001: if a test case is fundamentally an
*assertion*, S8 rewrites the recipe (the way it tests) but cannot
rewrite the assertion (what it tests). If a test case is
fundamentally a *recipe*, S8 has wider latitude. The invariant
choice constrains the authority model; the two questions cannot be
answered independently.

### S2-Q-007 — Execution-history boundary against S4

What does S2 store about runs:

- Run-id references only (S4 holds all evidence).
- Per-version pass/fail summary (denormalized into S2).
- Last-run snapshot (latest result inline, history via references).
- Some other shape.

S6's failure-attribution and S8's evolution decisions both depend on
how easily S2 can answer "when did this test last pass" and "what
was the most recent failure."

### S2-Q-008 — Requirement linkage

Where do JIRA tickets live:

- First-class entity in S2 (mirrors v2.2's `requirements` table).
- Separate substrate.
- External typed reference only (S2 stores `jira_key` + version,
  doesn't store ticket body).

How are sprint / release / project associations modeled.

### S2-Q-009 — Outward surfaces (for S3, S4, S6, S8)

What does S2 expose:

- Insert/update API for S3.
- Read API for S4 (the executor's data source).
- Read API for S6 (intent + coverage + last-run for attribution).
- Read/write API for S8 (target of autonomous rewrites).

Schema-enforcement contract: how S3's LLM output is validated against
S2's schema is an S3 concern, but S2 has to publish enough structure
for S3 to enforce against it.

### S2-Q-010 — Disposition of v2.2 test-management tables

Once S2-Q-001 through S2-Q-009 land, walk each v2.2 table:

- `sections` — keep / drop / absorb.
- `requirements` — keep / drop / absorb (depends on S2-Q-008).
- `test_cases` — keep schema / rewrite / absorb (depends on
  S2-Q-003).
- `test_case_versions` — keep / drop (if bitemporal) / rewrite
  (depends on S2-Q-005).
- `test_suites`, `suite_test_cases` — likely keep; suite-membership
  is curation, not test-representation.
- `ba_reviews` — depends on whether review is an S7 concern
  (separate substrate) or has structural ties to S2 versioning.
- `metadata_impacts` — likely absorbed into S1-diff-driven evolution
  (S8 territory) rather than reproduced in S2.

This question generates a `DECISIONS_LOG.md` entry on resolution.
