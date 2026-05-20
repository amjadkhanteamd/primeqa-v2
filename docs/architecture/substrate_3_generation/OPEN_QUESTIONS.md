# Substrate 3 — Generation Engine — Open Questions

Questions specific to S3's design. Cross-cutting questions live in the top-level OPEN_QUESTIONS.md.

---

## Resolved

### Theme 1 (2026-05-19)

- ~~S3-Q-001 — Substrate boundaries, architectural posture, failure-mode philosophy~~ → resolved by D-070 (constrained interpretation engine bounded by S1 ontology and substrate-2 taxonomy; refusal as first-class output; seven structural commitments).

### Theme 2 (2026-05-19)

- ~~S3-Q-002 — Generation request shape, output protocol, refusal taxonomy, generation ledger~~ → resolved by D-071 through D-076 (Theme 2). Request shape with three-axis context separation; binary draft/refusal outcome protocol with no-silent-drops invariant; six-category RefusalKind taxonomy with invalidity-vs-policy distinction; two-surface ledger architecture (semantic + operational observability) with typed cross-substrate provenance; ontology-bound reasoning artifacts (S3 Guardrail 2) with transparency as governed substrate artifact; dismissal_reason taxonomy as substrate-3 reasoning vocabulary.

### Theme 3 (2026-05-19)

- ~~S3-Q-003 — Per-archetype generation strategies~~ → resolved by D-077 through D-082 (Theme 3). Cross-cutting framework with four dimensions per archetype, shared interpretation context, archetype hint as guidance, dismissal_reason applicability by phase; per-archetype strategies for data-behavior, configuration, permission (with recipe-kind selection preserving claim semantics), UI (with honest v1 scope), integration (with operational-only v1 admissibility).

---

## Open — to be addressed during subsequent themes

### S3-Q-004 — Grounded negative test generation

Theme 4. What "grounded" means concretely per archetype. How the formula-parser deferral shapes v1 grounded-negative scope for claims grounded in validation rules. Structural shape of the `no-admissible-negative-scenario-found` refusal category.

### S3-Q-005 — LLM integration architecture

Theme 5. Tool-use vs single-shot structured JSON (the deferred Q-004 from top-level OPEN_QUESTIONS lives here). Model selection per archetype. Retry / repair strategies. Cost / latency envelope per generation. How the semantic-search-space guardrail (S3 Guardrail 1) shapes prompt design and retrieval scope.

### S3-Q-006 — Prompt management and evaluation

Theme 6. Prompt versioning. Eval infrastructure for generation quality AND refusal quality. Drift detection. How the generation ledger surfaces into eval comparison.

### S3-Q-007 — Quality envelope

Theme 7. What "good S3 output" looks like measurably. Acceptance thresholds for emission quality and refusal quality on parallel dimensions. How quality is observed at v1 without S6.

### S3-Q-008 — Semantic equivalence policy under operational variation

Themes 5 and 7. Detection of semantic divergence under invariant `(semantic_context, governance_context)` is mechanical via substrate-2 `identity_hash` (D-059) and substrate-3 `explanation_hash` (D-075). Categorization of divergence (regression / acceptable drift / semantic evolution / operational variation) is judgmental and requires either rules-based heuristics or human review.

Specifically:

- When same semantic + same governance + different operational (e.g., model upgrade) produces different `identity_hash`, what categorization framework applies?
- When same semantic + same governance produces different `explanation_hash` while `identity_hash` remains stable, what categorization framework applies? (Transparency drift without output drift.)
- What thresholds — operationally calibrated — distinguish acceptable variation from regression?

The substrate has the detection primitives (typed drift events into the semantic ledger). The categorization framework is the open question.

Addressed in Theme 5 (model behavior characterization under operational variation) and Theme 7 (quality envelope thresholds for emission, refusal, and explanation stability).

---

## Forward-compatibility reservations

Recorded during Theme 1 for downstream consideration:

- **Generation ledger ↔ substrate-2 provenance handoff.** When substrate-2 ships `get_provenance` / `get_recipe_provenance` (currently reserved per substrate-2 SPEC §10.2), S3's ledger retires into substrate-2 provenance. Schema forward-compat is a Theme 2 sub-task.
- **S6 attribution feedback loop.** When S6 ships, its `report_run_outcome`-driven attribution can inform S3's interpretation layer. S3's interpretation layer should be designed with a clean extension point for this; no v1 dependency.
- **S8 recipe-evolution handoff.** Recipes S3 emits must be S8-evolvable from day one. Theme 3 (per-archetype strategies) operationalizes what this means structurally.
- **S1 admissibility ceiling lifting.** When the formula parser ships and `REFERENCES` edges populate (substrate-1 §17), admissibility-checking automatically deepens for validation-rule-grounded claims. When StandardValueSet detection ships (substrate-1 §22), picklist admissibility deepens for standard fields. Both extensions are non-breaking to S3 design.
- **Explanation canonicalization for prose surfaces.** V1 ships structured-only `attempted_interpretation` (per D-075). When LLM-generated rationale text is added as a transparency surface in some future iteration, explanation canonicalization rules must extend to cover prose. Forward-compat reservation; no v1 dependency.
- **Operational observability substrate boundary.** The `llm_calls` table is substrate-3-adjacent in v1 per D-074. Provisional commitment — operational observability may eventually move to a future observability substrate without breaking substrate-3 semantics.
- **Implementation topology of interpretation across the batch.** Theme 3 (D-077) commits to shared interpretation context across the batch — cross-requirement awareness for sprint batches is preserved; multi-archetype decomposition is supported. The orchestration shape that delivers this (one-pass, multi-pass coordinated, planner-style with explicit dependency graph) is resolved in Theme 5.
- **Run-as-execution upgrade path for permission archetype.** Theme 3 (D-080) commits to recipe-kind selection preserving claim semantics; v1 defaults to metadata-inspection with refusal-with-disambiguation when run-as-execution is required. When S1 Tier 2 ships sharing rules, OWD, and Apex sharing modeling, currently-refused complex permission claims may upgrade to grounded run-as-execution recipes.
- **Integration archetype interaction-topology admissibility.** Theme 3 (D-082) ships integration archetype with operational-only v1 admissibility (existence + structural connectivity). A future substrate-3 cycle may add interaction-topology admissibility — cross-system causality, external observability discipline, temporal sequencing semantics, protocol semantics — when integration becomes a larger product surface. Substrate-3 architectural slot reserved.
