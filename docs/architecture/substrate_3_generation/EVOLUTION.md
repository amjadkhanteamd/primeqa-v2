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
