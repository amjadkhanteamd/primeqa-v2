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

## 2026-05-19 — Theme 4 (grounded negative test generation) complete

Two D-entries committed (D-083 and D-084) locking grounded-negative discipline + per-archetype scope. Theme 4 adds one architectural Guardrail (the third), one refusal kind (the seventh, anticipated since Theme 2), and one substrate-3 artifact-level output field (`admissibility_layer`); operationalizes per archetype within Theme 3's locked strategies.

**D-083** locks the grounded-negative discipline with five architectural commitments integrated from round 2 TA convergence: (a) S3 Guardrail 3 — requirement-anchored origination — preventing the substrate's drift from constrained interpretation engine to exploratory QA generator; (b) seventh refusal kind `no-admissible-negative-scenario-found` with typed internal cause distinguishing ontology_gap / no_org_constraint / policy_restraint under a single external refusal kind; (c) polarity strictly derived from claim_kind + content with no parallel `polarity` field, preserving substrate-2 claim_kind as authoritative semantic identity; (d) bounded decomposition discipline (canonical-negative-per-failure-mode + highest-specificity grounding + bounded candidate enumeration); (e) Layer 1 admissibility produces artifact-level visibly degraded trust marker via top-level `admissibility_layer` field + substrate-emitted natural-language caveat. This is the substrate's defense against the v2 failure mode of plausible-but-ungrounded negatives.

**D-084** locks per-archetype grounded-negative scope. Data-behavior is richest (validation rule Layer 1 → Layer 2 post formula parser; required-field, type-incompatibility, permission-restriction at Layer 2; partial automation rejection). Configuration is mechanically cleanest (S1 is substrate of truth for entity / property / edge absence). Permission leverages D-080's recipe-kind discipline (grant absence Layer 2; sharing-rule absence refuses with `ontology_gap` cause). UI is narrow (INCLUDES_FIELD edge absence Layer 2; non-layout-derivable refuses with `ontology_gap`). Integration ships with constraint-admissibility framing capturing simplest cases only — causal admissibility for the philosophically deeper integration negatives reserved as forward-compat (parallel to D-082's interaction-topology admissibility reservation; will likely converge with it).

SPEC §5 (grounded negative test generation) substantively written with eight subsections (§5.1 through §5.8) covering the discipline, the seventh refusal kind, polarity recognition, bounded decomposition, Layer 1/Layer 2 admissibility with artifact-level trust visibility, per-archetype scope, and forward-compat reservations.

OPEN_QUESTIONS.md updated: S3-Q-004 resolved by D-083 and D-084; three new forward-compat reservations appended (Layer 2 admissibility upgrade for validation-rule-grounded negatives; integration negative causal admissibility; substrate-3 admissibility-confidence calibration for `policy_restraint` cause).

GLOSSARY.md seeded with 12 Theme 4 terms covering the new vocabulary (admissibility layer, `admissibility_layer` artifact field, bounded decomposition, canonical negative, causal admissibility forward-compat, cause typing, grounded-negative discipline, identifiable failure mode, Layer 1 visible trust marker, the seventh refusal kind, polarity recognition, requirement-anchored origination as S3 Guardrail 3).

**TA-review-loop pattern.** Single round of pressure-test before convergence — the tightest cycle in Phase 1 to date. Round 1 surfaced six surfaces: four convergence items (anti-constraint-mining guardrail; internal refusal-cause separation; polarity as authoritative parallel semantics; bounded negative decomposition principle); one product-risk concern (Layer 1 false trust); one philosophical incompleteness acknowledgment (integration causal admissibility). Round 2 integration accepted all six: four as architectural commitments locked in D-083, one as substrate-3 output property (artifact-level trust marker, also in D-083 (e)), one as forward-compat reservation (D-084 + OPEN_QUESTIONS). The TA explicitly signaled convergence — "Everything else is strong enough to lock."

**Convergence pattern acceleration.** Theme 2 needed three rounds; Theme 3 needed two; Theme 4 needed one round + integration. The pattern reflects architectural maturity — Themes 1 and 2 established the substrate boundaries, governance architecture, and reasoning vocabulary; Theme 3 operationalized per archetype within them; Theme 4 added one Guardrail + one refusal kind + one output field, all within the locked architectural vocabulary. Remaining themes (5–7) continue this trajectory: operationalization within architectural commitments, not new architectural primitives.

**Architectural posture.** Substrate-3's mission integrity is now structurally protected by three Guardrails: Guardrail 1 (semantic search space), Guardrail 2 (reasoning artifacts), Guardrail 3 (requirement-anchored origination). Together they bound what the substrate may search over, what vocabulary the substrate may reason with, and what may originate candidates. The substrate cannot drift into shadow ontology, cannot reason outside substrate-authorized vocabulary, and cannot originate generation from anything other than requirement interpretation. Theme 4 closes the third structural defense.

## 2026-05-19 — Theme 5 (LLM integration architecture) complete

Four D-entries committed (D-085 through D-088) locking the LLM integration architecture. Theme 5 selects tool-use as the integration topology and reframes substrate-3 as a constrained semantic orchestration runtime with the LLM as a bounded cognition provider. Three thin semantic primitives expose substrate-3 to the LLM; substrate-side orchestration is internal and free to evolve through Themes 6/7 calibration. Theme 5 also introduces the eighth refusal kind (`operational-budget-exhausted`) as a third refusal category axis (operational) and tightens replay equivalence to semantic substance.

**D-085** locks integration topology and substrate framing. Tool-use selected over structured JSON and planner-style for mechanical Guardrail 2 enforcement at emission boundary, per-call observability, phase mapping, and incremental correction capability. Substrate-3 reframed as constrained semantic orchestration runtime — orchestration engine, governance engine, admissibility engine, decomposition controller, replay controller, refusal router. LLM reframed as bounded cognition provider contributing semantic intent, selection judgment, and outcome emission.

**D-086** locks the thin tool surface schema. Three semantic primitives: `propose_semantic_intent` (LLM proposes what the requirement implies; substrate derives candidates and computes admissibility internally), `select_canonical` (LLM selects when substrate presents multiple admissibly-grounded candidates), `emit_outcome` (LLM emits final structured outcome per D-072). Substrate-side orchestration internal to substrate-3, free to evolve. Substrate is the admissibility authority — `admissibility_layer` is substrate-authored, not LLM-asserted.

**D-087** locks two-layer Guardrail enforcement and clean telemetry-provenance separation. Layer A (schema validation at tool boundary) is necessary; Layer B (substrate-side semantic governance validation during orchestration) is sufficient. Schemas alone do not constrain semantic misuse; substantive Guardrail enforcement requires substrate-side semantic validation. Operational telemetry (`llm_calls`) cleanly separated from semantic provenance (`attempted_interpretation`) — different tables, different code paths, different consumers. `llm_calls` permanently substrate-3-adjacent; `attempted_interpretation` retires to substrate-2 provenance when get_provenance ships per D-074.

**D-088** locks multi-turn statefulness semantics, replay equivalence over semantic substance, and the eighth refusal kind. Rejected LLM tool calls categorized: schema/vocabulary/Layer A violations and operational errors are operational (not semantic history); substrate-derived dismissals and Layer B findings are semantic (recorded in `attempted_interpretation`). Replay equivalence tightened: `explanation_hash` computed over semantic substance (set of admissibly-grounded candidates, canonical selection, dismissed alternatives by category, admissibility_layer, outcome semantics), not operational trace (ordering, tokens, Layer A corrections, model_identifier). Eighth refusal kind `operational-budget-exhausted` introduces third refusal category axis (operational), distinct from invalidity and policy. Typed payload preserves semantic substance up to exhaustion point.

SPEC §6 (LLM integration architecture) substantively written with eight subsections (§6.1 through §6.8) covering the integration topology and substrate framing, tool surface, two-layer Guardrail enforcement, telemetry-provenance separation, multi-turn statefulness and replay equivalence, eighth refusal kind, and forward-compat reservations.

OPEN_QUESTIONS.md updated: S3-Q-005 resolved by D-085 through D-088; five new forward-compat reservations appended (per-archetype LLM model routing; substrate-3 tool surface evolution; substrate orchestration algorithm evolution; future operational refusal kinds; `explanation_hash` semantic-substance computation tuning).

GLOSSARY.md seeded with 15 Theme 5 terms covering the new vocabulary (the three thin tool primitives, the two enforcement layers, the operational-vs-semantic separation, the substrate-as-orchestration-runtime framing, the eighth refusal kind, the operational category axis, semantic substance, operational trace, substrate as admissibility authority, tool-use as integration topology).

**TA-review-loop pattern.** Single round of pressure-test with substantial round 2 integration — eight architectural issues surfaced, all eight accepted. Tool surface reshape (six phase-shaped tools → three thin semantic primitives); substrate as admissibility authority (LLM does not author admissibility); multi-turn statefulness clarified; operational telemetry separated from semantic provenance; two-layer Guardrail enforcement (schemas necessary but not sufficient); replay equivalence over semantic substance; eighth refusal kind introducing third category axis; substrate-3 reframed as orchestration runtime with LLM as bounded provider. The TA explicitly signaled convergence — "I would not do broad additional cycles."

**Convergence pattern continues to mature.** Theme 2: three rounds; Theme 3: two rounds; Theme 4: one round + integration; Theme 5: one round + substantial integration. The pattern reflects architectural maturity AND the depth of Theme 5's pushbacks — Theme 5 had more architectural reshape than Theme 4 despite the same round count, because Theme 5's surfaces (LLM integration boundary, substrate-LLM authority division, semantic-vs-operational separation) directly touch substrate-3's mission integrity.

**Architectural posture.** Substrate-3's full architecture is now in place across Themes 1–5: three Guardrails (mission integrity); eight reasoning-phase dismissal_reasons (D-076 vocabulary); eight refusal kinds across three categories (invalidity, policy, operational); per-archetype operationalization (Theme 3); grounded-negative discipline (Theme 4); LLM integration via tool-use with substrate as orchestration runtime (Theme 5). Remaining themes — Theme 6 (prompt management + eval) and Theme 7 (quality envelope) — operationalize within this locked architecture rather than introducing new architectural primitives. Substrate-3 design is structurally complete.

The substrate-3 framing has clarified materially. Theme 5 made explicit what was implicit since Theme 1: substrate-3 is not a "wrapper around an LLM" but a constrained semantic orchestration runtime that bounds LLM cognition within architectural commitments. Themes 6 and 7 work within this runtime.

## 2026-05-19 — Theme 6 (prompt management and evaluation) complete

Three D-entries committed (D-089 through D-091) locking the prompt management architecture, eval suite, and LLM model routing. Theme 6 operationalizes Themes 1–5 commitments without introducing new substrate-level architectural primitives. Round 2 TA integration accepted six convergence items as architectural refinements plus one acknowledged-but-unresolved (semantic adjudication theory).

**D-089** locks prompt management architecture. Sequential `prompt_template_version` per template; immutable per version (replay determinism); layered composition (base + 3 v1 archetype fragments: data-behavior, configuration, permission; UI and integration deferred). Prompts explicitly acknowledged as policy-adjacent surface — behavior-shaping within substrate-bounded governance, not merely contextual guidance. Prompt-substrate-orchestration as bounded co-evolution — each side has its own design cycle but substantive changes require co-evolution; migration costs explicit.

**D-090** locks eval suite architecture. Four categories: correctness (Layer A acceptance per Guardrail per tool), quality (Layer B substantive correctness, per-archetype emission quality, per-refusal-kind appropriateness), performance (cost, latency, per-model comparison, budget exhaustion frequency), drift (replay-based, per two-invariant framework). Two-invariant replay equivalence: identity_hash (semantic continuity; strict invariant; presumption of regression on drift) vs explanation_hash (transparency continuity; weaker invariant; presumption of refinement on drift when identity_hash stable). Drift-as-evolution framework — drift triggers investigation, not auto-failure; substrate-3 maintainers tasked with judgment annotation (`regression` | `evolution` | `neutral`). Ground truth strategy: curated corpus (200–500 cases) + pilot feedback + replay corpus; v1 quality limits explicitly acknowledged. Semantic adjudication theory in ambiguous enterprise QA acknowledged unresolved; forward-compat reservation.

**D-091** locks single-model-per-batch LLM routing chosen by dominant archetype. Model selection explicitly acknowledged as behavior-shaping operational decision — different models differ in semantic temperament (refusal aggressiveness, decomposition style, grounding conservatism, ambiguity handling). V1 defaults: data-behavior / permission / integration / mixed → Claude Opus 4.7; configuration / UI → Claude Sonnet 4.7. Per-customer override preserved. Per-archetype within-batch routing deferred — future Theme 6 calibration cycle when per-archetype × model behavioral profiles are well-characterized and cross-model coherence within batches validated empirically.

SPEC §7 (prompt management and evaluation) substantively written with eight subsections (§7.1 through §7.8) covering prompt management, prompts as policy-adjacent surface and bounded co-evolution, eval suite architecture, two-invariant replay equivalence, drift as evolution, single-model-per-batch routing, and forward-compat reservations.

OPEN_QUESTIONS.md updated: S3-Q-006 resolved by D-089 through D-091; four new forward-compat reservations appended (semantic adjudication theory; per-archetype within-batch model routing; UI and integration prompt fragments; drift event judgment framework formalization).

GLOSSARY.md seeded with 15 Theme 6 terms covering the new vocabulary (behavior-shaping operational decisions, bounded co-evolution, dominant-archetype selection, drift event judgment, eval ground truth, healthy architectural evolution, policy-adjacent surface, prompt fragment, prompt registry, replay corpus, semantic adjudication theory unresolved, semantic continuity, single-model-per-batch routing, transparency continuity, two-invariant replay equivalence).

**TA-review-loop pattern.** Single round of pressure-test with substantial round 2 integration — seven architectural surfaces identified; six accepted as convergence items; one (semantic adjudication theory) acknowledged unresolved but not blocking v1. Convergence items: separate semantic continuity from transparency continuity (item 1); prompts as policy-adjacent surface (item 2); model routing as behavior-shaping (item 3); drift as healthy evolution (item 5); bounded co-evolution (item 6); single-model-per-batch (item 7). The TA explicitly signaled convergence — "Everything else is strong enough to lock."

**Convergence pattern.** Theme 2: three rounds; Theme 3: two rounds; Theme 4: one round + integration; Theme 5: one round + substantial integration; Theme 6: one round + substantial integration. Themes 4–6 share the pattern of single-round pressure-test with substantial integration response. Each theme's round 2 integration responds to TA convergence items as architectural refinements without re-opening the locked architecture from prior themes.

**Architectural posture.** Substrate-3's full architecture is now in place across Themes 1–6. Theme 7 (quality envelope) remains — the calibration framework that operationalizes per-archetype thresholds against the eval framework Theme 6 established. Substrate-3 design surface no longer introducing new architectural primitives; remaining work is calibration discipline.

Substrate-3's complete architectural inventory through Theme 6:

- Three Guardrails (mission integrity): semantic search space (Theme 1), reasoning artifacts (Theme 2), requirement-anchored origination (Theme 4).
- Eight dismissal_reasons across three reasoning phases (D-076 vocabulary, dismissal_reason applicability by phase per D-077).
- Eight refusal kinds across three categories (invalidity / policy / operational per D-073, D-083, D-088).
- Per-archetype operationalization (Theme 3 five archetypes with four-dimensional spec).
- Grounded-negative discipline (Theme 4 Guardrail 3 + canonical-negative-per-failure-mode + Layer 1/2 admissibility).
- LLM integration via tool-use with three thin semantic primitives (Theme 5).
- Two-layer Guardrail enforcement (Theme 5 Layer A schema + Layer B substantive).
- Substrate-3 as constrained semantic orchestration runtime (Theme 5).
- Two-invariant replay equivalence + drift-as-evolution framework (Theme 6).
- Prompt management with bounded co-evolution + policy-adjacent surface acknowledgment (Theme 6).
- Single-model-per-batch routing with model-as-behavior-shaping (Theme 6).

Theme 7 calibrates the quality envelopes that operationalize this architecture against per-archetype thresholds.
