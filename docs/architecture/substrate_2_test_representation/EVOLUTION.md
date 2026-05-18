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

Final batch — all four remaining open questions resolved in a
single pass. The substrate's internal coherence was fully locked
by D-051 through D-061; this batch settles the boundary
concerns (S4 execution-history, JIRA requirements), the outward
API surface, and the v2.2 transition.

**D-062 — Execution-history boundary against S4:** Last-run
snapshot pattern via new `test_recipe_runtime_state` table (per
recipe, not per recipe version). Pure snapshot — no aggregate
statistics, no history (S4 holds full history). S4 reports run
outcomes via Coordinator callback (push-based; S2 never queries
S4). Test-level runtime status as **resolution operation**
composing recipe-level state with conservative initial policy
("any failure → failing; all pass → passing; otherwise mixed").
Multi-recipe outcome resolution has acknowledged architectural
pressure; raw recipe state exposed for consumers needing
different composition policies. Two forward-compat reservations:
richer runtime-state resolution, run history beyond last-run.

**D-063 — Requirement linkage:** External typed reference only.
New `test_requirement_links` table with multi-kind linkage
(`generated_from` / `verifies` / `related_to`). No ticket
content replicated; JIRA remains source of truth. `external_system`
as typed identifier today (enum); registry-based evolution
reserved. Three forward-compat reservations: registry-based
external systems, sprint/release associations, bidirectional
sync.

**D-064 — Outward surfaces:** Semantic Transaction Coordinator
elevated to **semantic OS infrastructure** rather than
substrate-internal component. Five interface groups: write
(actor-aware, authority-enforced), read (current-approved vs
latest distinction), equivalence and discovery, runtime state
(per D-062), provenance. **Behavioral contracts specified per
interface** (idempotency, authority, atomicity, error,
concurrency, asymptotics) as substrate-level commitments. Three
**resolution-class operations** named as first-class substrate
concept: `get_current_approved_claim` (per D-061),
`get_test_runtime_status` (per D-062),
`select_recipe_for_execution` (per D-064 + D-057 reservation).
Resolution operations distinguished from lookups by composition
over substrate rules. Wire format unspecified at substrate
level; behavioral contracts are not. Three forward-compat
reservations: cross-substrate Coordinator concerns, API
versioning, behavioral contract evolution.

**D-065 — v2.2 disposition:** Per-table disposition for v2.2
test-management tables:
- `test_cases` → ABSORB (into new test_claims + test_recipes)
- `test_case_versions` → DROP (effective-time supersession
  replaces)
- `requirements` → DROP (external typed reference replaces
  per D-063)
- `metadata_impacts` → DROP (S8 territory, not S2's)
- `sections`, `test_suites`, `suite_test_cases`, `ba_reviews`
  → MIGRATE to future orthogonal substrates (test catalog,
  review workflow)

**Intentional architectural trade-off** framing: short-term v2.2
feature parity sacrificed for long-term substrate coherence.
The MIGRATE-targeted concerns represent separate substrates'
responsibilities; absorbing them into S2 would compromise the
substrate boundary that makes the platform architecture coherent.
Gap is real, acceptable, intentional.

TA refinements integrated across the batch:
1. Drop `run_count` from `test_recipe_runtime_state` — keep
   snapshot pure (statistics belong to S4 or S6)
2. Runtime-state resolution semantics has real architectural
   pressure; surfaced explicitly in §8.5 rather than buried as
   edge case
3. `external_system` may evolve to registry-based — reserved as
   forward-compat with schema-shape commitment of typed
   identifier (enum or FK)
4. Coordinator elevated to **semantic OS infrastructure** framing
   (was "named substrate-level component")
5. **Behavioral contracts per interface** as substrate-level
   commitments (idempotency, authority, atomicity, error,
   concurrency, asymptotics)
6. MIGRATE decisions as intentional architectural trade-off, not
   pressure point — reframed as deliberate commitment
7. Recipe selection as **policy resolution** (not deterministic
   lookup); third resolution-class operation alongside D-061's
   current-approved and D-062's runtime status

Resolution-class operations emerge as a recognized pattern in
this batch: Coordinator-level operations that compose substrate
rules rather than executing simple queries. The pattern is named
in D-064 to prepare for future resolution operations (S6
attribution clustering, S8 evolution prioritization, etc.)
without re-inventing the architectural slot.

§4.1 of SPEC.md extended with `test_recipe_runtime_state` and
`test_requirement_links` tables. §4.2 architectural-roles table
extended with the two new tables (marked as boundary tables).
§4.7.5 Coordinator framing updated to semantic OS infrastructure.
§§8, 9, 10, 11 of SPEC.md filled with substantive content for
the first time; previously placeholders. Status block updated.

**S2-Q-007 through S2-Q-010 all moved to Resolved.** Substrate-2
SPEC §2 through §11 substantively complete; only §1 synthesis
remains as placeholder (conventionally written last). **Fifteen
D-entries committed** (D-051 through D-065). Substrate-2's design
phase is complete.

The substrate's six-table layout (`test_claims`, `test_recipes`,
`test_provenance`, `test_claim_coverage`,
`test_recipe_runtime_state`, `test_requirement_links`) carries
the four core tables of internal coherence plus two boundary
tables of external interface. The Coordinator is semantic OS
infrastructure with five interface groups, three resolution-class
operations, and explicit behavioral contracts. Twelve forward-compat
reservations across all decisions provide the deliberate evolution
path.

Next: implementation work. The substrate design is complete; what
remains is shipping it. The four substrates that depend on
substrate-2 (S3, S4, S6, S8) build against the Coordinator's
interface and behavioral contracts.
