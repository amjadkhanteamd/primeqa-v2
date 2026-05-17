# Substrate 2 — Test Representation — Open Questions

Questions specific to this substrate's design. Cross-cutting
questions live in the top-level `OPEN_QUESTIONS.md`.

---

## Resolved

- ~~S2-Q-001 — Deepest invariant of a test case~~ → resolved by
  D-051. See `SPEC.md` §2.
- ~~S2-Q-002 — Common substrate across five archetypes (structural
  and semantic)~~ → resolved by D-052. See `SPEC.md` §3.

---

## Open

### S2-Q-003 — Test case data model

Multi-sub-cycle question. Concrete columns, tables, JSONB shapes,
identity mechanics, validation patterns.

Sub-cycles:

- ~~Sub-cycle 1: Claim-kind taxonomy.~~ Locked per D-053. 16 kinds
  across 5 archetypes. See `SPEC.md` §3.
- ~~Sub-cycle 2: Recipe-kind taxonomy.~~ Locked per D-054. 5 kinds,
  observability-domain only. See `SPEC.md` §3.
- **Sub-cycle 3 (next):** Storage realization. Discriminator-column-
  plus-JSONB, envelope-plus-detail-tables, or hybrid. Constrained
  by the locked taxonomies and the S2-Q-005 versioning choice.
- Sub-cycle 4: Identity-hash mechanics. Canonical hashing of
  `(archetype, claim_kind, claim body, semantic conditions)` that
  preserves identity stability across recipe rewrites and across
  trivial body reorderings.
- Sub-cycle 5: Pydantic validation patterns. What's enforced at
  app boundary, what at DB layer, what at schema level.

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

### S2-Q-011 — Trigger-kind classification

Surfaced during S2-Q-003 sub-cycle 2 (recipe-kind lock, D-054).
Recipe-kinds were locked as observability-domain classifications
only, per the recipe-kind purity rule. Triggering actions — what
*initiates* a test scenario, as opposed to what *observes* its
effects — are a parallel classification axis warranting their own
design treatment.

**Trigger-kind purity guardrail (proposed).** Trigger-kinds
classify *causal initiation patterns* — what kind of action sets
the scenario in motion. Trigger-kinds do not classify
observability (that's recipe-kind), do not classify the truth
being asserted (that's claim-kind), and do not classify the
product category (that's archetype). A trigger-kind names *what
kind of cause triggers the behavior being tested*.

**Candidate trigger-kinds (to be vetted during S2-Q-011 design).**

- *Inbound injection* — external system pushes a payload into
  Salesforce as the causal initiation. Channels include inbound
  REST, inbound SOAP, inbound email, external platform-event
  publish, streaming push.
- *Internal data mutation* — create / update / delete records
  inside Salesforce as the causal initiation. Distinct from
  `data-recipe` observation: a data mutation as trigger CAUSES
  downstream effects (automation firing, sharing recalculation,
  related-record updates); `data-recipe` observes the resulting
  state. The mutation is the cause; the observation is whatever
  the recipe-kind does. Same mechanism (DML), different role.
- *UI trigger* — user clicks, navigates, or fills a form as the
  triggering action. Distinct from `ui-recipe`: `ui-recipe` both
  drives the UI and observes the resulting DOM/UI state (the
  observability happens within the recipe); UI-as-trigger drives
  the UI as the causal initiation while observation happens
  elsewhere (e.g., `data-recipe` observing record state after a
  UI save). The distinction matters — bundling both under
  "ui-recipe" loses the trigger-vs-observation clarity.
- *Time-based trigger* — clock advancement causes scheduled jobs,
  batch Apex, or time-based workflow/flow actions to fire.
  Architecturally important — many real Salesforce behaviors
  depend on time-based primitives and cannot be reduced to
  other trigger-kinds.
- *Configuration change* — metadata deploy as the triggering
  action (e.g., test that activating a flow causes behavior Y).
  Rare but real for configuration tests asserting "deploying X
  changes behavior Y."
- *External event publish* — upstream system fires a platform
  event that Salesforce receives. Possibly a sub-case of inbound
  injection (channel = platform-event publish from external),
  possibly its own kind. To be resolved during S2-Q-011 design.

**Open sub-questions.**

- Is trigger-kind a fourth orthogonal discriminator alongside
  archetype / claim_kind / recipe_kind, or is it a property of
  the recipe (a sub-discriminator)?
- How do trigger-kinds map to capability assumptions (similar
  treatment to recipe-kinds)?
- How does trigger-kind interact with `inbound-effect-claim`
  specifically — the inbound payload is the test's primary input,
  so its structural treatment matters.
- The UI-trigger / UI-recipe distinction creates two paths for
  any test involving the UI; clear discipline is needed for when
  to use each.

This question is a downstream consequence of D-053's claim-kind
lock (`inbound-effect-claim` being a real claim-kind makes
triggering patterns first-class) and D-054's recipe-kind purity
(recipe-kinds don't classify triggers).
