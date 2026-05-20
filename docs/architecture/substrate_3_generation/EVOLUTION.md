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
