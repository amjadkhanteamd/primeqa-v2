# Substrate 3 — Generation Engine — Glossary

Terms specific to substrate-3. Cross-cutting terms live in the top-level glossary (when one exists) or in the relevant substrate's glossary.

---

## Theme 1 terms

**Admissibly grounded.** A claim is admissibly grounded in S1 when S1's actual constraint structure (at the current capability tier) supports the claim's assertion — not merely that referenced entities exist. Stronger than reference-existence; weaker than execution-verified.

**Constrained interpretation engine.** S3's architectural framing. The substrate interprets requirements through S1's ontology and topology and emits typed substrate-2 records, operating within a semantic search space bounded by S1's ontology and substrate-2's locked taxonomy. Opposes "freewheeling LLM authorship" or "translation pipeline."

**Generation ledger.** S3-owned record of generation history per requirement and per `test_id`: actor, prompt version, retrieval set, refusal categories, partial-outcome map, prior attempt linkage. V1-only; retires into substrate-2 provenance when substrate-2's reserved `get_provenance` / `get_recipe_provenance` interfaces ship.

**`GenerationOutcome`.** Typed union representing the result of a generation cycle: draft claims and recipes, refusals, or partial outcomes mixing both. Protocol shape resolved in Theme 2.

**Interpretation layer.** The S3 substrate layer that bridges natural-language requirements to S1-grounded semantic context. Structured, deterministic-where-possible inference over S1's ontology and topology. Precedes claim generation.

**Refusal (typed).** A first-class generation output where S3 declines to emit a claim or recipe with a typed `RefusalKind` discriminator and structured actionable feedback. Refusals carry product value (actionable to the engineer) parallel to drafts; they are not failures.

**`RefusalKind`.** Typed discriminator over refusal categories. Theme 1 names five (`underspecified-requirement`, `no-relevant-context`, `ambiguous-reference`, `ungrounded-claim`, `structural-validation-failure`); Theme 4 anticipates a sixth (`no-admissible-negative-scenario-found`); taxonomy extends through Theme 2 design.

**S3 Guardrail 1 — Semantic search space bounded.** The LLM's semantic search space is bounded by S1's ontology × substrate-2's locked taxonomy. The LLM cannot invent semantic content outside this bounded space, and operates conservatively within it (ambiguity refuses or surfaces disambiguation rather than guesses).

**Scoped semantic neighborhood.** The bounded subgraph of S1 entities and edges within which the LLM reasons during claim generation. Produced by S3's interpretation layer; constrains the LLM's referenceable space.

**Verification bar.** S3's calibration target for output quality. Output is at the verification bar when a competent reviewer can affirm or reject it in bounded time without co-authoring. Output below the verification bar refuses rather than emits.

## Theme 2 terms

**`attempted_interpretation`.** Typed structured artifact persisted on every `GenerationOutcome` (per D-075). Captures the substrate's reasoning during generation: scoped neighborhood, candidate paths considered, dismissals with reason codes, and selected path. Substrate-grade governed artifact, not debug surface.

**Dismissal_reason taxonomy.** Substrate-3's bounded reasoning vocabulary enum (per D-076). V1 ships 8 entries across 5 categories (TOPOLOGY / ONTOLOGY_INVALIDITY / RANKING / GOVERNANCE / CONFIDENCE-reserved). Governed under substrate-2's `claim_kind` discipline: bounded, versioned, extended only through deliberate D-entries.

**Equivalence algebra.** The substrate's mechanical primitive for reasoning about regeneration semantics, derived from three-axis context separation (per D-071). Same semantic + same governance + different operational → expected `identity_hash` match and `explanation_hash` match. Same semantic + different governance → expected behavior change by design.

**Explanation_hash.** Canonical hash computed over `attempted_interpretation` under ordered canonicalization (per D-075). Substrate-3's mechanical primitive for explanation equivalence; analog of substrate-2's `identity_hash` for claim identity. Comparable across runs; equality means explanation equivalence.

**Explanation drift event.** Typed substrate event emitted when same `(semantic_context, governance_context)` regeneration produces a different `explanation_hash` than its lineage parent (per D-075). Detection mechanical (hash inequality); categorization deferred to S3-Q-008.

**`GenerationOutcome`.** Substrate-3's output protocol unit (per D-072). Binary `outcome_kind`: draft or refusal. One outcome per (request, requirement) pair; no requirement is silently dropped.

**`GenerationRequest`.** Substrate-3's input protocol unit (per D-071). Carries requirement_refs, three context axes, and optional regeneration lineage with typed deltas.

**Governance_context.** One of three context axes in a `GenerationRequest` (per D-071). Captures what behavioral policy regime was active: refusal_policy_version, dismissal_taxonomy_version, transparency_policy_version. Distinct from semantic_context (what world was visible) and operational_context (how execution proceeded).

**No-silent-drops invariant.** Architectural commitment that every requirement in `request.requirement_refs` is explicitly resolved by exactly one `GenerationOutcome` (per D-072). Enforced structurally — a request cannot be marked complete with unresolved requirements.

**Operational observability surface.** The substrate-3-adjacent `llm_calls` table carrying operational telemetry (per D-074). Does NOT migrate to substrate-2 provenance. Bounded retention by storage/cost considerations.

**Refusal-as-governed-behavior.** Architectural commitment that refusals are governance behavior, not "natural" generation behavior (per D-073). Each refusal carries a `refusal_policy_version` from the request's `governance_context`; refusal replay is a first-class substrate operation.

**`regeneration_kind`.** Typed discriminator on regeneration deltas (per D-071). Five values across two categories: semantic-continuity edges (clarification, grounding_evolution, requirement_change — migrate to substrate-2 provenance) and operational edges (model_experimentation, eval_replay, failure_recovery — stay substrate-3-adjacent).

**S3 Guardrail 2 — Ontology-bound reasoning artifacts.** Substrate-3 reasoning artifacts persisted in substrate state may only reference semantic concepts authorized by S1's ontology and substrate-2's taxonomy (per D-075). They may not introduce durable semantic concepts outside this authorized set. Extends Theme 1's S3 Guardrail 1 from generation output to the substrate's own reasoning vocabulary.

**Semantic ledger.** The substrate-3-owned `generation_requests` + `generation_outcomes` tables carrying semantic audit trail (per D-074). Retires to substrate-2 provenance when `get_provenance` ships, via typed cross-substrate provenance.

**Three-axis context separation.** Architectural commitment separating context into semantic_context (admissible world state), governance_context (behavioral policy regime), and operational_context (execution mechanics) per D-071. Enables clean equivalence algebra and clean substrate-2 migration boundary.

**Transparency as governed substrate artifact.** Architectural position (Position B) committing substrate-3 to substrate-grade governance of its reasoning artifacts (per D-075). The substrate-3 analog of substrate-2's identity discipline: explanation is mechanical, not judgmental, within substrate-authorized vocabulary.

**Two-surface ledger architecture.** Substrate-3's separation of the semantic ledger (substrate-2-migrating) from operational observability (substrate-3-resident permanently) per D-074. Linked by `outcome_id`; eval joins both; UX and ops surfaces read each independently.

**Typed cross-substrate provenance.** Substrate-2 forward-commitment under D-074: substrate-2 absorbs typed refusal and explanation structural metadata (`refusal_kind`, `refusal_policy_version`, `refusal_schema_version`, `explanation_hash`, `dismissal_taxonomy_version`) as queryable provenance event columns, but does not interpret refusal payload or attempted_interpretation internals. Substrate-3 owns semantics; substrate-2 owns provenance continuity.

## Theme 3 terms

**Cross-cutting per-archetype framework.** The four-dimensional spec pattern Theme 3 establishes (per D-077): each archetype is specified along interpretation scope × admissibility-checking shape × recipe-kind selection × refusal dominance. The framework anchors D-078 through D-082's per-archetype strategies.

**Grounding phase.** One of three reasoning phases the substrate's processing passes through (per D-077). Grounding phase performs admissibility checking against S1's constraint structure. Applicable dismissal_reasons from D-076: `insufficient_grounding`, `no_grant_supports_capability`, `no_constraint_supports_negative`, `type_incompatibility`, `archetype_mismatch`.

**Governance phase.** One of three reasoning phases (per D-077). Governance phase applies policy threshold evaluation. Applicable dismissal_reason at v1: `policy_threshold_not_met`. Reserved CONFIDENCE category attaches to governance phase if entries are added.

**Interpretation phase.** One of three reasoning phases (per D-077). Interpretation phase performs scoping of the neighborhood, enumeration of candidate paths, and resolution of references. Applicable dismissal_reasons: `ambiguous_target_resolution`, `lower_specificity`.

**Layer 1 admissibility.** Per D-078, the admissibility level the substrate achieves for ValidationRule-based claims at v1: "the rule exists and is active." Honest about v1 reality. Layer 2 admissibility ("formula actually rejects/permits this specific scenario") requires substrate-1 §17 formula parser.

**Layout-derivable elements.** Per D-081, UI elements whose existence and state can be inferred from PageLayout metadata at S1 Tier 1. Element-state-claims targeting non-layout-derivable elements (Lightning components, Dynamic Forms, custom JS-driven UI) require S1 Tier 3 and refuse with `no-relevant-context` at v1.

**Operational-only admissibility.** Per D-082, the admissibility level integration archetype claims achieve at v1: verification of integration entity existence and structural connectivity, but not cross-system causality, external observability, temporal sequencing, or protocol semantics. Future substrate-3 cycle may add interaction-topology admissibility for these dimensions.

**Phase semantics.** The substrate's reasoning process passes through three phases — interpretation, grounding, governance (per D-077). Phase is documentation of how the bounded dismissal_reason enum applies, not new persisted vocabulary. Phase-based applicability decouples reasoning vocabulary from archetype implementation.

**Recipe-kind selection preserving claim semantics.** Per D-080, the architectural commitment that recipe-kind selection in permission archetype does not silently substitute one verification surface for another. Metadata-inspection (verifies configured permission state) and run-as-execution (verifies runtime-effective experience) are categorically different epistemic claims about reality; the substrate either defaults to the safer mechanically-deterministic surface (metadata-inspection) or refuses with disambiguation prompt rather than silently substituting.

**Shared interpretation context.** Per D-077, the architectural property that the substrate's reasoning has access to all requirements in the batch when scoping neighborhoods and constructing candidate paths. Preserves D-071's batch-capable commitment; enables cross-requirement awareness and multi-archetype decomposition. Implementation topology (the orchestration shape that delivers this property) is resolved in Theme 5.

**Verification surface.** Per D-080, what kind of truth about reality a generated test claims to verify. Metadata-inspection has *configured permission state* as its verification surface; run-as-execution has *runtime-effective experience* as its verification surface. The two surfaces are categorically different and the substrate preserves claim semantics by not silently substituting them.

## Theme 4 terms

**Admissibility layer (Layer 1, Layer 2).** Per D-078 and D-083 (e), the rigor level the substrate achieves for a claim's grounding against substrate-1's constraint structure. Layer 1: the constraint exists and is active (e.g., validation rule applicability verified; formula not parsed). Layer 2: the constraint's semantic content confirms the asserted outcome (e.g., formula evaluates to reject this specific scenario). At v1, validation-rule-grounded claims achieve Layer 1; required-field, type-incompatibility, permission-grant-absence, and INCLUDES_FIELD-edge-absence groundings achieve Layer 2 directly. Layer 2 upgrade for validation-rule grounded claims when substrate-1 §17 formula parser ships.

**`admissibility_layer` (artifact field).** Per D-083 (e), the typed field at substrate-3 artifact top level (`layer_1` | `layer_2`) exposing the grounding rigor to downstream consumers. Not nested in `attempted_interpretation`; alongside claim, recipe, and provenance at full artifact prominence. Substrate-3 commitment to false-trust prevention.

**Bounded decomposition discipline.** Per D-083 (d), the three-part principle protecting against combinatorial expansion of admissibly-grounded candidate negatives in enterprise orgs with overlapping constraints: canonical-negative-per-failure-mode + highest-specificity grounding among admissible alternatives + bounded candidate enumeration during interpretation. Uses D-076's existing `lower_specificity` dismissal_reason; no new vocabulary.

**Canonical negative.** Per D-083 (d), the substrate's emitted negative for a given identifiable failure mode in a requirement. When multiple constraints could ground the same failure mode, the substrate selects the highest-specificity grounding and surfaces dismissed alternatives in `attempted_interpretation.dismissal_reasons` with `lower_specificity`.

**Causal admissibility (forward-compat).** Per D-084 forward-compat, the admissibility framework integration negatives philosophically require — temporal observation, causal interpretation, distributed-state reasoning. Categorically different from constraint admissibility (rejection-by-constraint). V1 ships integration negatives with constraint-admissibility framing capturing the simplest cases (entity absence, configuration absence). Causal admissibility reserved for a future substrate-3 cycle.

**Cause (internal, for `no-admissible-negative-scenario-found`).** Per D-083 (b), the typed internal field on the seventh refusal kind's payload distinguishing three semantic causes under one external refusal kind: `ontology_gap` (substrate cannot ground because S1 doesn't model the relevant constraint dimension), `no_org_constraint` (org genuinely has no constraint producing the asserted rejection), `policy_restraint` (candidate grounding exists but admissibility-confidence threshold not met). Preserves semantic granularity for evals, replay, analytics, capability tracking without proliferating refusal kinds at product surface.

**Grounded-negative discipline.** Per D-083, the substrate-3 commitment that negative claims (asserting rejection, absence, or inability) must be grounded against specific org constraints producing the asserted outcome. Prevents the v2 failure mode of plausible-but-ungrounded negatives — tests asserting "should fail" without grounding the failure in a specific org constraint. If no constraint can be identified, the substrate refuses with `no-admissible-negative-scenario-found`.

**Identifiable failure mode.** Per D-083 (d), a distinct semantic dimension of negative the requirement implies. The substrate emits one canonical negative per failure mode. Requirements explicitly enumerating multiple failure modes get distinct emitted negatives per mode.

**Layer 1 visible trust marker.** Per D-083 (e), substrate-3's commitment to surface Layer 1 admissibility at artifact prominence (artifact-level `admissibility_layer` field + substrate-emitted natural-language caveat in the artifact's narrative), not buried in metadata. Defense against false trust in v1 validation-rule-grounded negatives that are technically grounded but semantically weak.

**`no-admissible-negative-scenario-found`.** Per D-083 (b), the seventh refusal kind in substrate-3's taxonomy. Policy-scope category. Anticipated in Theme 2 D-073; shipped in Theme 4. Carries typed internal `cause` field distinguishing ontology_gap / no_org_constraint / policy_restraint.

**Polarity recognition.** Per D-083 (c), the substrate's mechanism for identifying negative claims from claim_kind + content, not from a separate authoritative `polarity` field. Prevents parallel-semantic-systems fragility (claim_kind saying one thing, polarity saying another). Substrate-2 claim_kind remains authoritative semantic identity; polarity is derived per-archetype recognition.

**Requirement-anchored origination.** Per D-083 (a), the substrate's commitment that candidate negatives must derive from requirement interpretation. Grounding constraints justify candidates derived from requirements; they do not independently originate negatives the requirement did not semantically imply. Formalized as S3 Guardrail 3.

**S3 Guardrail 3.** Per D-083 (a), the third architectural guardrail in substrate-3's lineage — requirement-anchored origination. Lineage: Guardrail 1 (Theme 1; semantic search space bounded by S1 × substrate-2 taxonomy) → Guardrail 2 (Theme 2; ontology-bound reasoning artifacts) → Guardrail 3 (Theme 4; requirement-anchored origination). Each Guardrail tightens what the substrate may do under what authority. Guardrail 3 prevents the substrate's quiet drift from "constrained interpretation engine" to "exploratory QA generator."

## Theme 5 terms

**`attempted_interpretation` (semantic provenance).** Per D-087, the substrate-3 structure recording the substrate's semantic reasoning per generation outcome. Lives in the semantic ledger (`generation_outcomes`). Captures candidate_paths with admissibility status and layer, selected_path_id, and dismissed_alternatives_by_reason. Used for replay determinism, semantic eval, transparency surfacing, refusal analysis. Distinguished from `llm_calls` operational telemetry.

**Bounded cognition provider.** Per D-085, the substrate-3 framing of the LLM's role. The LLM contributes semantic intent interpretation, selection judgment (when needed), and outcome emission within substrate-bounded discipline. The LLM does not orchestrate, does not author admissibility, does not categorize dismissals, does not select among refusal kinds.

**Constrained semantic orchestration runtime.** Per D-085, the substrate-3 framing as the architectural locus of authority. Responsibilities: orchestration engine, governance engine, admissibility engine, decomposition controller, replay controller, refusal router. The substrate is the locus of architectural authority; the LLM is a bounded cognition provider.

**`emit_outcome` (tool).** Per D-086, one of the three thin semantic primitives substrate-3 exposes to the LLM. Final structured outcome emission per D-072 (draft | refusal). Draft payload: claim ref + recipe ref + admissibility_layer (substrate-authored, LLM transcribes). Refusal payload: refusal_kind + refusal_payload per D-073's per-kind typed schema.

**Layer A enforcement (schema validation).** Per D-087, the substrate-3 Guardrail-enforcement layer at the LLM tool emission boundary. Validates substrate-authorized vocabulary at enum positions, structural well-formedness, Guardrail 3 syntactic precondition (requirement_excerpt presence), S1 entity ref existence. Necessary but not sufficient. Layer A violations are operational; route to substrate-side typed-feedback correction or to `structural-validation-failure` refusal on persistent violation.

**Layer B enforcement (semantic governance validation).** Per D-087, the substrate-3 Guardrail-enforcement layer during substrate orchestration. Validates Guardrail substantive enforcement (archetype × claim_kind semantically meaningful for subjects; requirement_excerpt substantively supports proposed intent; canonical selection respects highest-specificity; admissibility_layer respects Layer 1 vs Layer 2 semantic meaning). Sufficient (combined with Layer A). Layer B violations are semantic findings; route to substrate-orchestrated dismissals or typed refusals.

**`llm_calls` (operational telemetry).** Per D-074 and D-087, the substrate-3-adjacent table recording per-tool-call telemetry (timing, token counts, model_identifier, operational outcome). Used for cost analysis, latency monitoring, error tracking, operational debugging. NOT used for replay determinism, semantic eval, transparency, or refusal analysis. Distinguished from `attempted_interpretation` semantic provenance.

**`operational-budget-exhausted` (refusal kind).** Per D-088, the eighth refusal kind in substrate-3's taxonomy. Operational (incompletion) category — a new third axis alongside invalidity and policy. Fires when substrate exhausts a budget dimension (token, time, tool_call_count) before completing reasoning. Typed payload preserves semantic substance up to exhaustion point.

**Operational category (refusal taxonomy axis).** Per D-088, the third refusal taxonomy axis introduced at Theme 5, alongside invalidity (5 kinds) and policy (2 kinds). Operational refusals are about substrate-runtime-resource constraints (budget exhaustion); distinct from invalidity (content/structure quality) and policy (substrate-deliberate restraint).

**Operational trace.** Per D-088, the LLM-side operational events of a generation: ordering of tool calls, specific tokens in intermediate responses, number of Layer A corrections, LLM model identifier, timing, token counts. Out of scope for `explanation_hash` computation; permitted and expected to vary across replays.

**`propose_semantic_intent` (tool).** Per D-086, one of the three thin semantic primitives substrate-3 exposes to the LLM. The LLM proposes what the requirement implies semantically. Carries `requirement_excerpt` (Guardrail 3 anchor, mandatory) and `intent_descriptor` (typed: archetype_hint, target_subject_hint, polarity_hint, failure_mode_framing, claim_kind_hint). Substrate processes by deriving candidates, computing admissibility, recording dismissals — all substrate-internal.

**`select_canonical` (tool).** Per D-086, one of the three thin semantic primitives substrate-3 exposes to the LLM. The LLM selects canonical when substrate presents multiple admissibly-grounded candidates per failure mode. Carries `candidate_refs` and `selection_rationale` (typed rationale_kind and dismissed_alternatives_with_reason). Auto-skipped when only one admissibly-grounded candidate exists; substrate auto-selects.

**Semantic substance.** Per D-088, the architectural commitment for what `explanation_hash` is computed over: set of admissibly-grounded candidates per failure mode (unordered), canonical selection, dismissed alternatives indexed by dismissal_reason category, admissibility_layer per emitted artifact, outcome kind and outcome payload semantics. Distinguished from operational trace. Replay equivalence operates over semantic substance match.

**Substrate as admissibility authority.** Per D-086, the substrate-3 commitment that admissibility is substrate governance truth, not LLM-authored interpretation. `admissibility_layer` is substrate-authored; the LLM never has a tool parameter where it asserts a candidate's admissibility_layer. The substrate computes admissibility from S1 + substrate-2 taxonomy + Layer 1/2 discipline; the LLM proposes semantic intent and selects among presented options.

**Tool-use (integration topology).** Per D-085, the LLM integration topology substrate-3 selected at Theme 5 (over structured JSON and planner-style). LLM produces structured outputs via typed tool invocations; substrate validates at the tool boundary; per-call observability. Mechanical Guardrail 2 enforcement at emission boundary. Selected for vocabulary discipline as emission precondition, per-call observability, phase mapping, incremental correction.

## Theme 6 terms

**Behavior-shaping operational decision.** Per D-091 (a), the substrate-3 framing of model selection (and by extension prompt selection per D-089 d) as operational_context choices that affect substrate-observable behavior — refusal aggressiveness, decomposition style, grounding conservatism, ambiguity handling. The architectural categorization (operational_context per D-071) is unchanged; the behavioral classification is metadata about management discipline.

**Bounded co-evolution (prompts and substrate orchestration).** Per D-089 (e), the substrate-3 commitment that prompts and substrate orchestration evolve through their own design cycles but require co-evolution discipline for substantive changes. Not fully decoupled independent evolution. Major orchestration changes trigger prompt re-validation; major prompt changes trigger orchestration eval. Migration costs explicitly acknowledged.

**Dominant-archetype selection.** Per D-091 (b), the routing mechanism by which substrate-3 selects a single model for a multi-requirement batch. The most prevalent archetype across the batch's requirements determines the model; mixed-archetype batches default to Claude Opus 4.7. Per-customer override via `operational_context.llm_model_identifier` preserved.

**Drift event judgment (`regression` | `evolution` | `neutral`).** Per D-090 (c), the substrate-3 maintainer annotation applied to each replay drift event surfaced in EVOLUTION.md. Drift triggers investigation, never auto-failure. Judgment criteria distinguish substantive regression from healthy architectural evolution (sharper refusals, more specific grounding, narrower admissibility). Cultural commitment to evolution-as-possible rather than mechanical drift-as-failure.

**Eval ground truth (curated corpus + pilot feedback + replay corpus).** Per D-090 (d), the three-source ground truth strategy for substrate-3 eval at v1. Curated test corpus (200–500 maintainer-authored cases); pilot customer feedback; replay corpus (each shipped generation enters mechanically). V1 ground truth quality limits explicitly acknowledged — eval rigor calibrated to these limits; Theme 7 quality envelope work refines as data accumulates.

**Healthy architectural evolution.** Per D-090 (c), drift events that represent improvement rather than regression — sharper refusal behavior, cleaner decomposition, narrower admissibility, better grounding specificity. Distinguished from semantic regression through substrate-3 maintainer judgment. Theme 6's drift framework prevents anti-evolution gravity that would result from auto-treating drift as failure.

**Policy-adjacent surface (prompts).** Per D-089 (d), the substrate-3 acknowledgment that prompts encode admissibility heuristics, refusal tendencies, decomposition preferences — behavior-shaping policy within substrate-bounded governance, not merely contextual guidance. Architectural categorization (operational_context per D-071) unchanged; behavioral classification reflects management discipline (governance-implications review for prompt changes; major fragment changes treated like architectural changes).

**Prompt fragment (per-archetype).** Per D-089 (b), the per-archetype prompt component extending the base system prompt with archetype-specific guidance for grounding sources and admissibility patterns. V1 ships three fragments (data-behavior, configuration, permission); UI and integration fragments deferred.

**Prompt registry.** Per D-089 (a), the substrate-3 versioned content store mapping `prompt_template_version` → composed prompt content. Maintains immutability per version for replay determinism; old versions remain available indefinitely; supports rollback and historical replay.

**Replay corpus.** Per D-090 (d), the eval ground truth source consisting of each shipped substrate-3 generation. Generations enter mechanically; replay evals run continuously against it. Empty at v1 launch; meaningful only after 3–6 months accumulation. Provides longitudinal drift detection signal independent of curated corpus or pilot feedback.

**Semantic adjudication theory (unresolved).** Per D-090 (f) forward-compat reservation. In genuinely ambiguous enterprise QA scenarios, multiple grounded, admissible, requirement-supported generations may differ — substrate-3 currently lacks a theory of canonical semantic correctness in the validity-space sense. V1 eval measures adherence to substrate-3 architectural commitments, not absolute correctness. Full resolution requires production data, longitudinal study, formal work beyond v1 scope.

**Semantic continuity (identity_hash invariant).** Per D-090 (b), the strict replay invariant. Same outcome — same emitted claim, same recipe, same outcome_kind, same refusal_kind + payload semantics if refusal. Drift indicates semantic regression; presumption of regression unless explained. Distinguished from transparency continuity.

**Single-model-per-batch routing.** Per D-091 (b), the v1 LLM routing commitment that all requirements in one `GenerationRequest` use the same `operational_context.llm_model_identifier`. Chosen by dominant archetype across the batch. Preserves cross-archetype consistency within a batch; aligns with D-077's shared interpretation context; simplifies replay determinism validation.

**Transparency continuity (explanation_hash invariant).** Per D-090 (b), the weaker replay invariant. Same `attempted_interpretation` semantic substance — same candidate set, same canonical selection, same `dismissed_alternatives_by_reason` category distribution, same admissibility_layer per artifact. Drift indicates reasoning trajectory varied; emitted output may still be correct. Distinguished from semantic continuity.

**Two-invariant replay equivalence.** Per D-090 (b), the substrate-3 commitment that replay equivalence is two distinct invariants (semantic continuity via identity_hash; transparency continuity via explanation_hash), treated separately in eval. Refines D-088's drift semantics: both hashes computed over semantic substance, but downstream treatment differs (tight thresholds for identity_hash; looser contextual thresholds for explanation_hash).

## Theme 7 terms

**Architectural invariant.** Per D-092 (a), a substrate-level commitment that is substrate law — NOT a calibration surface, NOT tunable by the quality envelope. Enumerated in SUBSTRATE_3_WORLDVIEW.md: identity_hash continuity, Layer A validity, refusal transparency presence, grounding requirements, the three Guardrails, the refusal taxonomy, three-context separation, two-layer enforcement, substrate-as-admissibility-authority, the reproducibility property. Evolution adjudication must preserve every invariant.

**Calibration surface.** Per D-092 (a), a behavioral distribution that the quality envelope calibrates: refusal-rate by semantic category, Layer 1/2 admissibility distribution, explanation_hash drift threshold. Distinct from architectural invariants.

**Canonical routing profile.** Per D-092 (c), the per-archetype default model (D-091 v1 defaults) relative to which a per-archetype quality envelope is defined. Non-canonical (override) model usage produces distributions outside the canonical envelope's scope.

**Quality envelope.** Per D-092, the structured calibration framework specifying which semantic behavioral distributions are calibrated per archetype, how v1 initial values are derived, and how thresholds evolve. Calibrates behavioral distributions, never architectural invariants. Conceptually separated from the operational envelope.

**Operational envelope.** Per D-092 (b), the operational tuning surface tracking cost, latency, budget caps, and `operational-budget-exhausted` rate. Calibrated independently from the quality envelope so operational tuning does not pollute semantic calibration.

**Structure-not-values.** Per D-092 (d), the commitment that the quality envelope is a calibration structure (dimensions, derivation, evolution discipline) with provisional v1 values, not fixed numerical gates — honest about the absence of production data at v1 and the expectation of substrate improvement.

**Threshold evolvability.** Per D-093 (a), the commitment that quality envelope thresholds are expected ranges that shift as the substrate improves, bounded by architectural invariants. A breach triggers adjudication, not auto-failure.

**Evolution adjudication.** Per D-093 (a, b), the maintainer judgment classifying a threshold breach as `regression` | `evolution` | `neutral`, bounded by architectural invariants (a breach of an invariant is regression by definition) and hardened with safeguards (recorded rationale, asymmetric lower-refusal scrutiny, periodic invariant audit, design-cycle-weight review).

**Drift judgment signatures.** Per D-093 (c), documented per-archetype patterns characterizing healthy evolution, regression, and neutral environmental change — the partial formalization of the drift judgment criteria deferred from Theme 6. Full automated formalization remains future.

**Architectural-invariant audit.** Per D-093 (b), the periodic review confirming that accumulated `evolution` shifts have not collectively breached an architectural invariant that no single shift breached. Guards against gradual semantic-center drift.

**Semantic risk tolerance.** Per D-094, the governance policy (the admissibility-confidence threshold) determining how confident the substrate must be before asserting a grounded negative — what the substrate is willing to assert as truth. Governance_context, not operational. Substrate-authored conservative default; per-customer governance override.

**Admissibility-confidence threshold.** Per D-094, the governance_context parameter governing the `policy_restraint` cause (D-083 b). Higher yields more conservative behavior (more refusals); lower yields more permissive. Identity-bearing (changing it is expected to change identity_hash, confirming governance categorization). The quality envelope observes its distributional effect but does not own it.

**Semantic reproducibility.** Per D-093 (d), the substrate-engineering property of replay determinism: same substrate version + same semantic_context + same governance_context yields the same emitted output. The substrate deterministically selects one point. Distinct from semantic acceptability.

**Semantic acceptability.** Per D-093 (d), the semantic property that multiple outputs may be correct for a requirement (validity as a space, not a point). The substrate is reproducible within a wider acceptability space. Distinct from semantic reproducibility.

**Substrate worldview.** The SUBSTRATE_3_WORLDVIEW.md artifact (Phase 1 closeout): the distilled, non-chronological, non-implementation canonical reference for substrate-3's governing principles, non-goals, architectural invariants, semantic boundaries, and responsibilities.
