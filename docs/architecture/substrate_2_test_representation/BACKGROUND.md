# Substrate 2 — Test Representation — BACKGROUND

## Why this substrate exists

PrimeQA's product is built around generating, executing, maintaining,
and interpreting tests. Each of those activities operates on the same
thing: a test case. Without a deliberately-designed canonical
representation of that "thing," the four operating substrates each
end up carrying their own implicit model:

- S3 (Generation) emits whatever JSON shape its prompt happens to
  produce.
- S4 (Execution) interprets whatever shape it's given, by convention.
- S6 (Observation and Interpretation) tries to map raw execution
  failures back to intent it never had a stable handle on.
- S8 (Evolution) tries to rewrite tests with no schema-level guarantee
  of what it's rewriting.

That implicit-model regime is what v2 currently ships. Test cases
exist as `test_case_versions.steps` JSONB rows, with
`referenced_entities` as a flat string array (`"Opportunity.Amount"`,
`"ValidationRule.RequireAmount"`). Generation produces, execution
consumes, and the connection between intent and outcome is held
together by string conventions and the fact that the same module both
generates and executes.

Substrate 2 makes the test case explicit. It is the canonical data
structure that S3 produces, S4 consumes, S6 reads back from, and S8
rewrites. The four substrates can evolve independently because they
meet at S2's contract.

## What S2 is for

Per `PLATFORM_VISION.md` §"Substrate 2" and
`PRIMEQA_PRODUCT_DEFINITION.md` §4.3, S2 captures for each test case:

- **Intent.** Why the test exists; what business behaviour it asserts.
- **Coverage.** Which entities in S1's semantic model the test
  exercises (directly cited and traversal-derived).
- **Relationships to org entities.** The specific Validation Rule it
  triggers, the Flow it exercises, the Field it asserts on — as typed
  references into S1's bitemporal graph, not flat strings.
- **Execution history.** Which runs the test participated in, with
  what outcomes. The boundary between "summary S2 holds" and
  "evidence S4 holds" is open design.
- **Assumptions about org state.** Preconditions, scenario
  declarations, expected initial conditions.
- **Provenance.** Which JIRA ticket generated it, which prompt
  version, which Domain Pack (S5), which logical version of the
  semantic model was active when generated.

These six concerns are heterogeneous and have to fit into a single
representation that spans five archetypes — data-behavior,
configuration, permission, UI, integration — without forcing each
archetype into the wrong shape.

The architectural ambition is broader than test storage. S1 holds the
platform's understanding of org structure — its bitemporal entity
graph. S2 holds the platform's understanding of test intent — what
claims the system makes about the org. Together they form the
platform's grasp of meaning: S1 describes what is, S2 describes what
should be tested as true. Downstream substrates (S6 explanation, S8
evolution, eventual coverage analysis and regression selection)
increasingly anchor on S2 semantics because intent is what humans
engage with day-to-day. S2 is therefore a semantic anchor in its own
right, complementary to S1 — not a passive store sitting downstream
of generation.

## Human legibility as design principle

S2 is intentionally human-legible. QA engineers review tests, humans
edit them, and failure attribution maps execution outcomes back to
test intent in terms a person can read. `PRODUCT_DEFINITION.md` §4.3
and `PLATFORM_VISION.md` (which lists "human-readable" as a substrate
property) both imply this, but the principle deserves explicit
statement here because it constrains downstream design.

Concretely: storage encodings that favor compactness over readability
(binary blobs, opaque graph references, vector-only representations)
are rejected when they obstruct inspection. LLM-generated content
(intent statements, scenario descriptions) is stored as plain text.
Provenance is first-class and queryable, not buried in audit logs.

Machine consumers (S4 executor, S6 attribution) work with the
human-legible form. Performance optimizations apply to indexes and
materializations *around* S2, not to S2's canonical content. This
mirrors S1's detail-table pattern: hot columns are queryable plain
values, sparse details are JSONB readable in `psql`.

## What S2 replaces

S2 supersedes — by re-design, not by direct migration — v2.2's
test-management schema as the test representation. The v2.2 tables
(`test_cases`, `test_case_versions`, `requirements`, `test_suites`,
`suite_test_cases`, `ba_reviews`, `metadata_impacts`) are kept as
customer DATA per D-024; their SCHEMAS are open for migration during
Phase 3 design and the Phase 4 / S3 build.

Three specific inadequacies of v2.2's representation motivate S2:

1. **References are flat strings, not typed entity identities.**
   `"Opportunity.Amount"` is a literal that doesn't survive S1's
   bitemporal model. It can't pin to a logical version. It can't be
   diff-aware. It can't distinguish "the Amount field as it existed
   at sync N" from "the Amount field as it exists now." S1 makes
   entities first-class with UUIDs and external_ids — S2's references
   should be typed and S1-anchored.

2. **The `steps` shape is data-behavior-only.** v2.2's
   `steps[].action ∈ {create, update, query, verify, convert, wait,
   delete}` covers Archetype 1. A configuration test asserts metadata
   existence with no records touched. A permission test asserts what
   user X can do. A UI test is an interaction sequence. An
   integration test is event-capture-based. Forcing any of these into
   a CRUD-step shape distorts them. S2 must offer a representation
   that's common at the right level of abstraction without
   procrustes-stretching one archetype's shape across the others.

3. **Provenance is thin.** v2.2 captures `generation_method` (enum)
   and `confidence_score` (float). Real provenance has structure:
   which JIRA ticket version, which prompt version, which Domain
   Pack, which retrieval set of S1 entities at which `version_seq`.
   S6's failure attribution and S8's evolution decisions both depend
   on this provenance being structured enough to query against.

## What S2 is not

S2 is data structure. It is not pipeline. The following are
explicitly out of S2 scope and belong to other substrates:

- The retrieval pipeline that picks S1 entities for a generation
  prompt — S3.
- The LLM tool-use schema or single-shot JSON shape used during
  generation — S3.
- Schema-enforcement of LLM output against S1 entity existence — S3.
- The execution engine that runs tests — S4.
- Raw per-step execution traces, screenshots, API request/response
  capture — S4.
- Attribution from raw error to S1 entity — S6.
- Failure clustering and explanation generation — S6.
- Review UI and approval workflow — S7 (eventually).
- Knowledge artifacts (Domain Packs, system rules, learned facts) —
  S5.
- Autonomous test rewrites when the org evolves — S8 (the rewrite
  logic, not the rewritable structure).

The line is: if it's *what the test case is*, it's S2. If it's *what
something does to or with* the test case, it's another substrate.

## How S2 relates to other substrates

- **Reads from S1.** S2 holds typed references into S1's entity graph
  (entity UUIDs at logical versions, or logical-identity references
  that resolve through S1's query interface). Coverage and
  entity-relationships are S1-anchored.
- **Produced by S3.** S3 takes a JIRA ticket plus retrieved S1
  context and produces an S2 record. The S2 schema is what S3's
  output is schema-enforced against.
- **Consumed by S4.** S4 takes an S2 record and executes it against
  a Salesforce org. The S2 schema is what S4 builds an executor for.
- **Read back by S6.** S6 takes raw S4 execution evidence and maps
  failures back to the S2 record's intent and coverage, then explains
  the failure in those terms.
- **Rewritten by S8.** S8 takes S1 change-detection signals (a field
  renamed, a validation rule changed) and rewrites affected S2
  records.
- **Influenced by S5.** S2 records carry provenance of which
  Knowledge artifacts (Domain Packs) shaped their generation. S5 is
  not part of S2 but is referenced from it.

## Scope of Phase 3 design

Phase 3 designs S2's data structure: what columns, what tables, what
JSONB shapes, what references, what versioning model, what mutation
semantics. It does not design the substrates that produce, consume,
or interpret S2 records. Those are later phases.

Phase 3 deliverables, per the S1 precedent:

- This `BACKGROUND.md`
- `SPEC.md` — Phase 1 (conceptual shape) and Phase 2 (concrete data
  model) of the design
- `GLOSSARY.md` — S2-specific terms
- `OPEN_QUESTIONS.md` — design surfaces under active deliberation
- `EVOLUTION.md` — session-by-session change log

The substrate ships as docs only in Phase 3. Implementation (the
actual tables, models, migrations) lands when S3 needs them in a
later product-phase.
