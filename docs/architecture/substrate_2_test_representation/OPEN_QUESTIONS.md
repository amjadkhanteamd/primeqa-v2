# Substrate 2 — Test Representation — Open Questions

Questions specific to this substrate's design. Cross-cutting
questions live in the top-level `OPEN_QUESTIONS.md`.

---

## Resolved

- ~~S2-Q-001 — Deepest invariant of a test case~~ → resolved by
  D-051. See `SPEC.md` §2.
- ~~S2-Q-002 — Common substrate across five archetypes (structural
  and semantic)~~ → resolved by D-052. See `SPEC.md` §3.
- ~~S2-Q-003 — Test case data model~~ → fully resolved across
  five sub-cycles:
  - Sub-cycle 1 (D-053): claim-kind taxonomy locked (16 kinds)
  - Sub-cycle 2 (D-054): recipe-kind taxonomy locked (5 kinds)
  - Sub-cycle 3 (D-056): storage realization (four-table layout,
    Pattern D)
  - Sub-cycle 4 (D-059): identity-hash mechanics and governance
    contract
  - Sub-cycle 5 (D-060): validation layering and the Semantic
    Transaction Coordinator

  See `SPEC.md` §3 (taxonomies), §4 (data model and validation
  layering), §6.3 (canonicalization mechanics).
- ~~S2-Q-004 — References to S1 entities: reproducibility vs
  evolvability~~ → resolved by D-058. Hybrid by layer: pinned
  required in identity-bearing layers, logical default with
  pinned opt-in in operational layers. Cross-layer validation
  is ontology enforcement. Identity_hash canonicalizes
  `entity_id` only from pinned references. Coverage derived
  from identity-bearing layer pinned refs only. Multi-mode
  `external_id` drift detection is S8's responsibility.
  Semantic-replay forward-resolution conditioned on S8-blessed
  transitions. See `SPEC.md` §5.
- ~~S2-Q-005 — Lifecycle and versioning model~~ → resolved by D-057.
  Effective-time supersession with `version_seq` canonical authority,
  `identity_hash` as semantic equivalence fingerprint, logical-default
  recipe FK, current-only coverage, no archival (lineage-continuity
  rationale). See `SPEC.md` §6.
- ~~S2-Q-011 — Trigger-kind classification~~ → resolved by D-055.
  5 kinds locked, four-discriminator framing extended, six-layer
  model amended (rename: "execution realization" → "observation
  realization"). See `SPEC.md` §3.

---

## Open

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

Note: authority boundary now mechanically anchored by D-057 +
D-059 + D-060 — S8 can autonomously do anything that preserves
`identity_hash` AND `identity_hash_version` AND passes Gate 2
(entity-evolution semantic compatibility). Cannot do anything
that fails either gate without escalation. The Semantic
Transaction Coordinator is the routing point for human-edit /
S3-regenerate / S8-rewrite paths. S2-Q-006 sub-questions on
identity continuity and trust boundary remain.

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
for S3 to enforce against it. APIs interact with the substrate
through the Semantic Transaction Coordinator (per D-060); API
shape derives from the Coordinator's write/read interfaces.

### S2-Q-010 — Disposition of v2.2 test-management tables

Once S2-Q-001 through S2-Q-009 land, walk each v2.2 table:

- `sections` — keep / drop / absorb.
- `requirements` — keep / drop / absorb (depends on S2-Q-008).
- `test_cases` — keep schema / rewrite / absorb (depends on
  S2-Q-003).
- `test_case_versions` — keep / drop (effective-time supersession
  per D-057 replaces this) / rewrite.
- `test_suites`, `suite_test_cases` — likely keep; suite-membership
  is curation, not test-representation.
- `ba_reviews` — depends on whether review is an S7 concern
  (separate substrate) or has structural ties to S2 versioning.
- `metadata_impacts` — likely absorbed into S1-diff-driven evolution
  (S8 territory) rather than reproduced in S2.

This question generates a `DECISIONS_LOG.md` entry on resolution.
