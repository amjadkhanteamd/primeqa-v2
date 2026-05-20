# Substrate 3 — Generation Engine — Evolution Log

Append-only. One entry per session that made substantive changes to this substrate's docs.

---

## 2026-05-19 — Substrate skeleton and baseline

`BACKGROUND.md` and `PRECONDITIONS.md` established. PRECONDITIONS captures the ground state at S3 Phase 1 design start (entity / edge inventory inherited from substrate-1; Coordinator surfaces and body registry inherited from substrate-2; 9 open questions punted to Phase 1 design).

D-069 (S3 design begins ahead of substrate-1's deferred-item resolution) committed.

## 2026-05-19 — PRECONDITIONS §2.1 corrected (commit 42cae2d)

`get_provenance` was incorrectly listed as an S3-consumed Coordinator surface; corrected per substrate-2 SPEC §10.2 which reserves both `get_provenance` and `get_recipe_provenance` (not realized in Phase 4). Correction notes the real S3 design constraint: iterative-regeneration history is not available from substrate-2 at v1; S3 must work from version-row inspection or carry its own generation ledger.

Editorial corrections-style commit; no D-entry.

## 2026-05-19 — Theme 1 (substrate boundaries) complete

D-070 committed: S3 is a constrained interpretation engine bounded by S1 ontology and substrate-2 taxonomy, with refusal as first-class output.

Seven structural commitments locked: (1) interpretation through ontology + topology, (2) admissible grounding, (3) autonomous-but-bounded under substrate-2 authority, (4) verification bar not co-authoring, (5) refusal as first-class product surface, (6) S3 Guardrail 1 (semantic search space bounded), (7) failure-loud philosophy.

Five refusal categories named (`underspecified-requirement`, `no-relevant-context`, `ambiguous-reference`, `ungrounded-claim`, `structural-validation-failure`); sixth (`no-admissible-negative-scenario-found`) anticipated for Theme 4.

Three Theme 2 design surfaces established (not resolved): typed `RefusalKind` discriminator with actionable feedback shape; S3-owned generation ledger with substrate-2 forward-compat; `GenerationOutcome` protocol union admitting drafts, refusals, partial outcomes.

V1 archetype-coverage reality made honest: data-behavior strong, configuration solid, permission usable, UI minimal (Layout-level only until S1 Tier 3), integration scoped.

SPEC §2 (substrate boundaries and architectural posture) substantively written. SPEC §1 reserved for end-of-Phase-1 synthesis. OPEN_QUESTIONS.md S3-Q-001 resolved; S3-Q-002 through S3-Q-007 opened for downstream themes. GLOSSARY.md seeded with Theme 1 terms.

TA-review-loop pattern established: opening hypothesis sharpened through seven critical refinements before D-070 locked. Refinements (1) interpretation not translation, (2) admissibility not existence, (3) underspecified-requirement refusal category, (4) verification not co-authoring, (5) refusal as product surface, (6) ledger promotion, (7) semantic-search-space guardrail — all accepted and integrated.

## 2026-05-19 — Theme 2 (generation protocol and governance architecture) complete

Six D-entries committed (D-071 through D-076) locking substrate-3's input/output protocol, governance architecture, and reasoning-vocabulary commitments.

**D-071** locks generation request shape with typed regeneration lineage (five `regeneration_kind` values across semantic-continuity and operational categories) and three-axis context separation (semantic / governance / operational), producing a clean equivalence algebra for reasoning about regeneration semantics.

**D-072** locks the `GenerationOutcome` protocol with binary draft/refusal kinds, dedup as a form of draft outcome (not a third category), the no-silent-drops invariant (every requirement explicitly resolved), and mandatory `attempted_interpretation` on every outcome.

**D-073** locks the `RefusalKind` taxonomy at six categories at Theme 2 close (seventh anticipated for Theme 4) with the invalidity-vs-policy architectural distinction: five invalidity refusals plus `low-generation-confidence` as a policy-threshold refusal. Refusals are governed behavior carrying `refusal_policy_version` from `governance_context`; refusal replay is a first-class substrate operation.

**D-074** locks the two-surface ledger architecture (semantic ledger separated from operational observability) and the typed cross-substrate provenance commitment: substrate-2 absorbs typed refusal and explanation structural metadata as queryable provenance event columns, but does not interpret payload or interpretation internals. Substrate-2 forward-commitment recorded for the eventual `get_provenance` design cycle.

**D-075** locks S3 Guardrail 2 (ontology-bound reasoning artifacts — substrate-3 reasoning may only reference S1+S2 authorized vocabulary) and the architectural position that transparency is a governed substrate artifact (Position B). Establishes the `explanation_hash` mechanism as substrate-3's parallel to substrate-2's `identity_hash`: mechanical, reproducible, comparable across runs. Explanation drift events typed and emitted on hash inequality under invariant context.

**D-076** locks `dismissal_reason` as substrate-3's reasoning vocabulary: 8 entries across 5 categories (TOPOLOGY, ONTOLOGY_INVALIDITY, RANKING, GOVERNANCE, CONFIDENCE-reserved), governed under substrate-2's `claim_kind` discipline. The only new semantic vocabulary substrate-3 introduces beyond S1 and substrate-2 authorized sets.

SPEC §3 (generation protocol and governance architecture) substantively written with ten subsections (§3.1 through §3.10) covering the Theme 2 architectural commitments and forward-compat reservations.

OPEN_QUESTIONS.md updated: S3-Q-002 resolved by D-071–D-076; S3-Q-008 opened (semantic equivalence policy under operational variation, extended to cover both `identity_hash` and `explanation_hash` divergence; addressed in Themes 5/7).

GLOSSARY.md seeded with 18 Theme 2 terms covering the governance architecture, protocol shapes, equivalence mechanisms, and ledger structure.

**TA-review-loop pattern.** Three rounds of pressure-test before convergence. Round 1: seven refinements over the initial protocol sketch (collapse regeneration modes; dedup as draft form; keep `underspecified` and `no-relevant-context` distinct; separate operational telemetry from semantic ledger; refusal multiplicity reservation; lineage as semantic continuity infrastructure; semantic vs operational context separation). Round 2: eight deeper substrate pressures including the meta-question "how much of S3's reasoning process becomes governed substrate truth." Round 3: collapsed remaining surfaces into one architectural position — transparency as governed substrate artifact — accepted in full. Theme 2 is the heaviest structural theme of Phase 1 because it locks the substrate's governance architecture; subsequent themes operationalize within it.

**Workflow shift.** From Theme 2 onward, design themes commit to a long-lived phase branch (`phase-1-substrate-3`) with merge to main at Phase 1 completion. Per-theme FF-merges (used for Theme 1) replaced by accumulating commits on the phase branch, per substrate-2 Phase 4 precedent. Theme 2 is the first commit on the new phase branch.

## 2026-05-19 — Theme 3 (per-archetype generation strategies) complete

Six D-entries committed (D-077 through D-082) operationalizing the five archetypes within the locked governance architecture from Themes 1 and 2. Theme 3 produces implementation discipline; no new substrate-level architectural primitives introduced.

**D-077** locks the cross-cutting per-archetype framework: four dimensions per archetype (interpretation scope × admissibility-checking shape × recipe-kind selection × refusal dominance), shared interpretation context across the batch (with implementation topology resolved in Theme 5), archetype hint as guidance not constraint (refuses only when reinterpretation is itself ambiguous), and dismissal_reason applicability by reasoning phase (interpretation / grounding / governance) not by archetype.

**D-078** locks data-behavior archetype strategy. Strongest v1 archetype coverage. Object-centered interpretation scope; per-claim-kind admissibility (type compatibility, permission grants, ValidationRule Layer 1 admissibility with Layer 2 deferred to formula parser, automation effect-tractability); API-execution recipe dominant; refusal dominance shaped by input quality.

**D-079** locks configuration archetype strategy. Solid v1 coverage. Metadata-entity-centered scope; per-claim-kind admissibility (S1 existence, modeled-property-state, S1 edge presence); metadata-inspection recipe dominant; refusal dominance shaped by S1 coverage gaps.

**D-080** locks permission archetype strategy with the explicit architectural commitment that recipe-kind selection preserves claim semantics. Metadata-inspection and run-as-execution are not equivalent verification surfaces — they verify different epistemic truths about reality. The substrate defaults to metadata-inspection (configured permission state); refuses with disambiguation prompt when run-as-execution is required (runtime-effective experience); admits run-as-execution as engineer-opt-in via operational_context. The substrate does not silently substitute one verification surface for another. This is the sharpest architectural commitment in Theme 3.

**D-081** locks UI archetype strategy with honest v1 scope. PageLayout-centered interpretation; layout-derivable element admissibility; higher baseline refusal rate (no-relevant-context dominant) honest about S1 Tier 3 absence; Theme 7 calibrates UI quality envelope separately.

**D-082** locks integration archetype strategy with operational-only v1 admissibility. Existence + structural connectivity verification only; cross-system causality, external observability, temporal sequencing, protocol semantics deferred as interaction-topology admissibility framework for a future substrate-3 cycle when integration becomes a larger product surface.

SPEC §4 (per-archetype generation strategies) substantively written with eight subsections (§4.1 through §4.8) covering the cross-cutting framework, each of the five archetypes, and forward-compat reservations.

OPEN_QUESTIONS.md updated: S3-Q-003 resolved by D-077–D-082; three new forward-compat reservations appended (implementation topology of interpretation across the batch; run-as-execution upgrade path for permission archetype; integration archetype interaction-topology admissibility).

GLOSSARY.md seeded with Theme 3 terms covering the four-dimensional framework, the three reasoning phases, the verification-surface-preservation principle, layout-derivability for UI, operational-only admissibility for integration, and Layer 1 admissibility for ValidationRule-based claims.

**TA-review-loop pattern.** Two rounds of pressure-test before convergence. Round 1: six initial design surfaces across the five archetypes plus cross-cutting framework. Round 2: four tighten-now refinements accepted (soften one-pass interpretation to shared context; recipe-kind selection preserves claim semantics; archetype hint as guidance not constraint; dismissal_reason by phase not archetype) plus two acknowledgments (UI archetype platform perception consequence; integration interaction-topology admissibility deferred). The TA explicitly signaled convergence after round 2 — no broad additional cycles needed; only targeted tightening.

**Platform perception observation.** V1's archetype coverage skews backend (data-behavior strong, configuration solid, permission usable) and away from UI (minimal until S1 Tier 3). This is honest about v1 reality and architecturally extensible, but produces a perception pattern in early evaluation and demo contexts worth recognizing in product strategy. Acknowledged here as substrate narrative, not codified as architectural commitment.

**Architectural posture.** Theme 3 is the first theme that produces operationalization within locked architecture rather than new architectural primitives. Themes 1 and 2 established the substrate boundaries, governance architecture, and reasoning vocabulary. Theme 3 specified how each archetype operationalizes within them. Subsequent themes — Theme 4 (grounded negatives), Theme 5 (LLM integration), Theme 6 (prompt management), Theme 7 (quality envelope) — continue this operationalization pattern. The substrate-3 architectural design is now mature enough that remaining themes are implementation discipline rather than architectural design.
