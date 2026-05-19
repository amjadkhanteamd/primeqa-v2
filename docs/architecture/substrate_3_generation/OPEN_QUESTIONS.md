# Substrate 3 — Generation Engine — Open Questions

Questions specific to S3's design. Cross-cutting questions live in the top-level OPEN_QUESTIONS.md.

---

## Resolved in Theme 1 (2026-05-19)

- ~~S3-Q-001 — Substrate boundaries, architectural posture, failure-mode philosophy~~ → resolved by D-070 (constrained interpretation engine bounded by S1 ontology and substrate-2 taxonomy; refusal as first-class output; seven structural commitments).

---

## Open — to be addressed during subsequent themes

### S3-Q-002 — Generation request shape

Theme 2. What does S3 take as input? Structural shape of a generation request — single requirement, batch of requirements, sprint-scoped batch with cross-requirement context. How requests carry forward state across regenerations. How requests invoke or accept Domain Pack context.

Sub-questions surfaced in Theme 1:

- Typed `RefusalKind` discriminator: full taxonomy and per-category actionable-feedback shape.
- S3-owned generation ledger: schema, lifecycle, substrate-2 forward-compat sanity check (so eventual retirement into substrate-2 provenance is a clean migration, not a forklift).
- `GenerationOutcome` protocol union: drafts, refusals, partial outcomes — the wire shape that accommodates all three uniformly.

### S3-Q-003 — Per-archetype generation strategies

Theme 3. How S3 generates for each of the five archetypes. Per-archetype admissibility-checking shape. The `capability-claim` recipe-kind selection question (run-as-execution vs metadata-inspection vs both). The UI archetype's honest constraints (Layout-level only at v1). Integration archetype's per-org-implementation variance handling.

### S3-Q-004 — Grounded negative test generation

Theme 4. What "grounded" means concretely per archetype. How the formula-parser deferral shapes v1 grounded-negative scope for claims grounded in validation rules. Structural shape of the `no-admissible-negative-scenario-found` refusal category.

### S3-Q-005 — LLM integration architecture

Theme 5. Tool-use vs single-shot structured JSON (the deferred Q-004 from top-level OPEN_QUESTIONS lives here). Model selection per archetype. Retry / repair strategies. Cost / latency envelope per generation. How the semantic-search-space guardrail (S3 Guardrail 1) shapes prompt design and retrieval scope.

### S3-Q-006 — Prompt management and evaluation

Theme 6. Prompt versioning. Eval infrastructure for generation quality AND refusal quality. Drift detection. How the generation ledger surfaces into eval comparison.

### S3-Q-007 — Quality envelope

Theme 7. What "good S3 output" looks like measurably. Acceptance thresholds for emission quality and refusal quality on parallel dimensions. How quality is observed at v1 without S6.

---

## Forward-compatibility reservations

Recorded during Theme 1 for downstream consideration:

- **Generation ledger ↔ substrate-2 provenance handoff.** When substrate-2 ships `get_provenance` / `get_recipe_provenance` (currently reserved per substrate-2 SPEC §10.2), S3's ledger retires into substrate-2 provenance. Schema forward-compat is a Theme 2 sub-task.
- **S6 attribution feedback loop.** When S6 ships, its `report_run_outcome`-driven attribution can inform S3's interpretation layer. S3's interpretation layer should be designed with a clean extension point for this; no v1 dependency.
- **S8 recipe-evolution handoff.** Recipes S3 emits must be S8-evolvable from day one. Theme 3 (per-archetype strategies) operationalizes what this means structurally.
- **S1 admissibility ceiling lifting.** When the formula parser ships and `REFERENCES` edges populate (substrate-1 §17), admissibility-checking automatically deepens for validation-rule-grounded claims. When StandardValueSet detection ships (substrate-1 §22), picklist admissibility deepens for standard fields. Both extensions are non-breaking to S3 design.
