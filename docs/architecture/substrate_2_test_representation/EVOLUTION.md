# Substrate 2 — Test Representation — Evolution Log

Append-only. One entry per session that made substantive changes to
this substrate's docs.

---

## 2026-05-16 — Substrate skeleton created

Initial skeleton. No design decisions yet. Created BACKGROUND.md
(why this substrate exists), SPEC.md scaffold, empty GLOSSARY.md,
and OPEN_QUESTIONS.md seeded with S2-Q-001 through S2-Q-010.

---

## 2026-05-16 — S2-Q-001 resolved: claim as identity-bearing root (D-051)

A PrimeQA test case is fundamentally a structured claim —
asserted system truth plus semantic conditions — realized through
replaceable executable recipes. Five-layer model (two
identity-bearing). Discipline rule: "if referenced in the claim →
semantic; otherwise operational."

---

## 2026-05-16 — S2-Q-002 resolved: three orthogonal discriminators with archetype-specific semantic forms (D-052)

Three orthogonal discriminators: `archetype`, `claim_kind`,
`recipe_kind`. Five-layer model uniform across archetypes. First
guardrail: archetypes are classifications, not storage partitions.

---

## 2026-05-16 — S2-Q-003 sub-cycle 1: claim-kind taxonomy locked (D-053)

16 claim-kinds across 5 archetypes. Consolidations include
`automation-effect-claim` (VR + Flow merged), `prohibition-claim`
(deletion-blocked + duplicate-prevention merged), `property-claim`
(absorbing activation-claim). Second guardrail: claim-kinds model
semantic forms, not implementation primitives.

---

## 2026-05-17 — S2-Q-003 sub-cycle 2: recipe-kind taxonomy locked (D-054); S2-Q-011 opened

5 recipe-kinds: `data-recipe`, `metadata-recipe` (with read/write
modes), `ui-recipe`, `event-subscription-recipe`,
`callout-intercept-recipe`. Third guardrail: recipe-kinds classify
observability semantics only. S2-Q-011 (trigger-kind classification)
opened as parallel architectural question.

---

## 2026-05-17 — S2-Q-011 resolved: trigger-kind taxonomy + four-discriminator extension + six-layer model amendment (D-055)

Fourth orthogonal discriminator `trigger_kind`. Six-layer model
(adds "Causal initiation"). Five trigger-kinds locked. Two new
guardrails: trigger-kind purity, trigger-vs-recipe orthogonality.

---

## 2026-05-17 — S2-Q-003 sub-cycle 3 + S2-Q-005 jointly resolved: storage realization + effective-time supersession (D-056, D-057)

Largest combined cycle in substrate-2 work so far. Four-table
layout (test_claims, test_recipes, test_provenance,
test_claim_coverage); Pattern D; effective-time supersession with
`version_seq` canonical; canonicalization policy elevated to
governance-critical; recipe-to-claim FK logical-default; no
archival in v1. Two new guardrails: semantic-vs-operational
lifecycle distinction; continuity triad.

§4 and §6 of SPEC.md filled for the first time. D-056 and D-057
added.

---

## 2026-05-17 — S2-Q-004 resolved: reference model — hybrid by layer with ontology-enforcement validation (D-058)

Hybrid-by-layer reference resolution. Pinned required in
identity-bearing layers; logical default in operational layers.
Identity_hash canonicalizes entity_id only. Cross-layer validation
as ontology enforcement. Semantic replay forward-resolution via
S8-blessed transitions only. external_id drift multi-mode.

§5 of SPEC.md filled. D-058 added.

---

## 2026-05-18 — S2-Q-003 sub-cycle 4 resolved: identity-hash mechanics + governance contract (D-059)

Governance-critical work per D-057's elevation. Hash input scope.
Canonicalization rules (strict, RFC 8785-ish). Versioned
canonicalization policy via `identity_hash_version` column.
Six-rule governance contract including two-gate evaluation for S8
evolution. Semantic projection fields reserved.

§6.3 of SPEC.md expanded. §4.1 `test_claims` extended with
`identity_hash_version` column. D-059 added.

---

## 2026-05-18 — S2-Q-003 sub-cycle 5 resolved: validation layering and the Semantic Transaction Coordinator (D-060); S2-Q-003 fully resolved

Three complementary enforcement layers (DB / Pydantic / Schema).
Reference type hierarchy with `IdentityBearingRef` as distinct
type. Semantic field descriptors via `Annotated[T, Marker]`. The
Semantic Transaction Coordinator elevated to named substrate-level
component. Four read-path error types distinguished.

§4.7 of SPEC.md filled. **S2-Q-003 now fully resolved** across
all five sub-cycles. D-060 added.

---

## 2026-05-18 — S2-Q-006 resolved: mutation paths and authority over meaning (D-061)

Three mutation paths (human / S3 / S8) routed by Coordinator
with per-path authority rules. **S8 invariant reframed to "no
autonomous semantic divergence"** (mechanical, not layer-based).
Identity continuity (test_id) and semantic continuity
(identity_hash) as orthogonal dimensions. Trust boundary
asymmetry: claim approval mechanical via hash; recipe re-approval
as **conservative default**. Linear supersession preserved.
**Current-approved as governance resolution, not status lookup.**
Test-level approval as derived composition. Rollback via
supersession. Four forward-compat reservations.

§7 of SPEC.md filled. §2 cross-reference and §6.3.9 Rule 1 phrasing
refined. §4.7.6 extended with authority-enforcement step. §4.7.8
added `AuthorityViolationError`. D-061 added.

---

## 2026-05-18 — S2-Q-007 through S2-Q-010 jointly resolved: execution-history boundary, requirement linkage, outward surfaces, v2.2 disposition (D-062, D-063, D-064, D-065)

Final design batch — all four remaining open questions resolved
in a single pass. The substrate's internal coherence was fully
locked by D-051 through D-061; this batch settled the boundary
concerns (S4 execution-history, JIRA requirements), the outward
API surface, and the v2.2 transition.

**D-062 — Execution-history boundary against S4:** Last-run
snapshot pattern via new `test_recipe_runtime_state` table. Pure
snapshot — no aggregate statistics, no history. Push-based S4
integration via Coordinator callback. Test-level runtime status
as resolution operation with conservative initial policy.
Multi-recipe outcome resolution has acknowledged architectural
pressure.

**D-063 — Requirement linkage:** External typed reference only.
`test_requirement_links` table with multi-kind linkage. No ticket
content replicated. Registry-based evolution reserved.

**D-064 — Outward surfaces:** Coordinator elevated to **semantic
OS infrastructure**. Five interface groups. **Behavioral contracts
specified per interface** as substrate-level commitments. Three
**resolution-class operations** named as first-class substrate
pattern.

**D-065 — v2.2 disposition:** Per-table disposition. **Intentional
architectural trade-off** framing — short-term v2.2 feature parity
sacrificed for long-term substrate coherence.

Seven TA refinements integrated:
1. Drop `run_count` from runtime state — keep snapshot pure
2. Runtime-state resolution semantics has real pressure; surfaced
   explicitly
3. `external_system` may evolve to registry-based — reserved
4. Coordinator elevated to semantic OS infrastructure framing
5. Behavioral contracts per interface as substrate-level
   commitments
6. MIGRATE decisions as intentional architectural trade-off, not
   pressure point
7. Recipe selection as policy resolution; third resolution-class
   operation

§§8, 9, 10, 11 of SPEC.md filled with substantive content for
the first time. §4.1 extended with `test_recipe_runtime_state` and
`test_requirement_links` tables. §4.2 architectural-roles table
extended. §4.7.5 Coordinator framing updated to semantic OS
infrastructure.

S2-Q-007 through S2-Q-010 all moved to Resolved. Substrate-2 SPEC
§2 through §11 substantively complete; only §1 synthesis remained
as placeholder. Fifteen D-entries committed (D-051 through D-065).

---

## 2026-05-18 — §1 synthesis composing §2–§11

§1 (synthesis overview) filled with substantive content composing
§2–§11 into a readable entry point for the substrate-2
documentation. Conventionally written last; not a tracked open
question.

Synthesis structure:
- §1.1 — the deepest invariant + architectural thesis
- §1.2 — the classification framework
- §1.3 — the data model
- §1.4 — references, lifecycle, mutation
- §1.5 — boundaries (S4, external systems)
- §1.6 — the outward surface (Coordinator) + Coordinator scoping
- §1.7 — the v2.2 transition
- §1.8 — what substrate-2 enables
- §1.9 — reading guide

**Two foundational principles surfaced as explicit framings in
the synthesis (per TA refinement):**

1. **Architectural thesis (§1.1):** "The substrate is designed so
   operational evolution can occur without breaking semantic
   continuity." This is the deepest *practical* commitment that
   all the architectural decisions support — identity_hash
   governance, hybrid-by-layer references, "no autonomous
   semantic divergence," mechanical approval semantics, the
   continuity triad. Every commitment in §3–§7 serves this
   principle: recipes, references, environments, and pinned
   versions can all evolve while the test's meaning stays the
   test's meaning. Without this principle, S8 cannot operate
   autonomously when the org changes; with it, S8 has bounded
   authority to keep the substrate alive without compromising
   approval state.

2. **Coordinator scoping (§1.6):** "The Coordinator governs
   substrate semantics; it does not absorb downstream substrate
   responsibilities." Essential clarification against god-object
   misreading. S3's generation logic, S4's execution machinery,
   S6's interpretation, S8's evolution decisions belong to their
   respective substrates. The Coordinator's wide interface
   surface is substrate-2's surface, scoped to substrate-2's
   semantics. Future substrate boundaries are protected by this
   discipline. As future substrates ship with their own
   coordinators, cross-coordinator coordination patterns may
   emerge; the discipline that each Coordinator stays within its
   substrate's semantic scope is the architectural commitment
   that makes that pattern viable.

Status block updated: all sections substantively complete per
D-051 through D-065; §1 synthesis composing §2–§11.

No D-entry generated for this cycle — synthesis is composition
of existing decisions, not a new architectural decision. The
two foundational principles surfaced in §1 are reframings of
existing commitments, not new commitments.

**Substrate-2 design phase is now complete.** Fifteen D-entries
(D-051 through D-065), eleven SPEC sections, six tables in the
data model (four core + two boundary), seven structural guardrails,
twelve forward-compatibility reservations. Next: implementation.

---

## 2026-05-27 — Expect-rejection model: the operational projection of a prohibition (D-110.1)

First substrate-2 increment of the implementation phase, opened by the CRUD
behavioral-negative programme (D-110, cross-substrate S2→S4→S3). A prohibition
claim asserts a rejection **semantically** — the claim's `RejectionSignal`, which
may carry an `error_field` IdentityBearingRef. To *execute* that prohibition, the
recipe needs the same expectation as an **operational** projection: scalars only,
no identity-bearing refs (operational-layer bodies forbid them). Additive,
greenfield — no identity-hash impact (the expectation rides the operational
recipe, not the identity-bearing claim).

- **`RejectionExpectation`** (`models/primitives.py`) — the operational projection
  of `RejectionSignal`: `error_code` / `error_message_pattern`, at-least-one
  required, frozen, scalars only.
- **`CreateStep.expect_rejection: Optional[RejectionExpectation]`**
  (`models/recipes/data_recipe.py`) — flags a create as a *behavioral negative*
  (the org should reject it); `None` (default) is an ordinary create.
- **`DataRecipeBody._at_most_one_expect_rejection`** — a recipe asserts at most
  one prohibition (0 = ordinary, 1 = behavioral negative); forward-compatible via
  `getattr` so update/delete steps gain the flag without touching the check.

The shape the S4 behavioral-negative vertical (D-110.2) + S3 behavioral emission
(D-110.3) consume.

---

## 2026-06-02 — Phase 1: readiness ratification — the S3/S4-breadth contract is settled (D-121)

Phase 1 of the program roadmap (breadth-first: confirm each substrate complete before the next builds on it). **A ratification, not a build** — S2 is already complete (Phase 4: 22 Coordinator methods, 1148 tests), and verification this cycle found **no gap** for the S3 (generation) + S4 (execution) breadth phases.

**Coverage confirmed.** The 22-method Semantic Transaction Coordinator + the complete taxonomies — `CLAIM_KIND_ENUM` (all **16** claim-kinds), `RECIPE_KIND_ENUM` (**5** recipe verticals), `TRIGGER_KIND_ENUM` (**6** triggers) — cover every breadth call. S3 emitting the remaining 13 claim-kinds (Phase 2) and S4 running any recipe vertical hit no unlisted kind: the kinds are *parameters* to `write_claim` / `write_recipe` / `select_recipe_for_execution`, not per-kind code. S3 routes through `query_equivalent_claims` → `write_claim` → `write_recipe`; S4 through `select_recipe_for_execution` + `report_run_outcome`; the e2e round-trip is already proven (`tests/integration/test_representation/e2e/{lifecycle,s4_boundary,multi_recipe}.py`). A new **taxonomy-contract drift-guard** (`tests/unit/test_representation/test_taxonomy_contract.py`) pins 16/5/6 by set-equality so a future edit that drops / renames / adds a kind fails loud.

**Two handoffs ratified.** (1) The §11 v2.2-table disposition (D-065) stands — `test_suites` / `sections` / `ba_reviews` **MIGRATE** to future catalog / review substrates (a deliberate boundary, not a gap). (2) The provenance retirement (D-074): S3's semantic ledger (`generation_requests` + `generation_outcomes`) retires into S2 `get_provenance` / `get_recipe_provenance` at the **Phase-7 greenfield cutover** (the typed read API is reserved in SPEC §10.2; `test_provenance` rows are already written by every Coordinator mutation); `llm_calls` (operational observability) stays in S3 permanently.

**Forward-compat reserved slots confirmed non-blocking for v1 breadth:** semantic-conditions graphification, the operational-linkage layer, richer runtime-state resolution, run-history-beyond-last-run, recipe-approval auto-preservation, merge / rebase semantics, registry-based `external_system`, replay-sensitive recipe selection. None is hit by S3 / S4 v1 breadth; each reopens with its own consumer.

No product code; deterministic merge gate (Phase 1 touches no Salesforce). See D-121. On `phase-9-substrate-2-readiness`.

## 2026-06-10 — expect_rejection on Update/Delete: the 2-step negative becomes representable (D-203)

The D-110.1 deferral closes: `UpdateStep` / `DeleteStep` (in the union since D-054) gain
`expect_rejection: Optional[RejectionExpectation]` — two optional fields, nothing else. The
at-most-one validator was written forward-compatible ("counted automatically without touching this
check") and needed zero changes: a 2-step negative (setup create with `None` + one flagged mutation)
is valid; two flagged steps stay rejected. `body_schema_version` stays 1 (additive); claim identity is
untouched (recipes are operational layer, excluded from `identity_hash` per SPEC §6.3.1 — proven at
the signature level). Coordinator write/read round-trips the 2-step shape with no write-path change
(no step-kind whitelist exists). Corrects D-202's measured gap ("S2 has no Update/Delete step
models" — wrong against the code; the gap was the flag, not the models). DECISIONS_LOG D-203.
