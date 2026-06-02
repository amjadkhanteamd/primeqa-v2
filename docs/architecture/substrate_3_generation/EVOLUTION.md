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

## 2026-05-19 — Theme 7 (quality envelope) complete — PHASE 1 COMPLETE

Three D-entries committed (D-092 through D-094) plus the new SUBSTRATE_3_WORLDVIEW.md artifact, locking the quality envelope and closing Phase 1 of Substrate-3 design. Theme 7 introduces no new substrate-level architectural primitives — it calibrates the locked architecture and protects it from calibration-driven erosion. Round 2 TA integration accepted all seven tightening items; three were load-bearing reshapes (calibration-vs-invariants; evolution-adjudication governance; admissibility-confidence recategorized to governance_context).

D-092 locks the quality envelope framework. The headline commitment: the quality envelope calibrates behavioral distributions (refusal-rate by semantic category, Layer 1/2 distribution, explanation_hash drift threshold), never architectural invariants (identity_hash continuity, Layer A validity, refusal transparency, grounding, the three Guardrails, the taxonomy, three-context separation, two-layer enforcement, substrate-as-authority) — those are substrate law, enumerated in SUBSTRATE_3_WORLDVIEW.md. Quality envelope conceptually separated from the operational envelope (cost/latency/budgets/operational-refusal-rate) so operational tuning does not pollute semantic calibration. Envelopes defined relative to each archetype's canonical routing profile. Structure-not-values: provisional v1 shapes from Theme 3 design intent, refined through pilot and production calibration.

D-093 locks threshold evolvability and evolution-adjudication governance. Thresholds are evolvable ranges (per drift-as-evolution, D-090 c), bounded by architectural invariants — a drift breaching an invariant is regression by definition, not subject to maintainer judgment. Hardened safeguards against rationalizing regression as evolution: recorded rationale tied to per-archetype evolution signatures, asymmetric scrutiny on lower-refusal shifts (presumption of regression per fail-loud philosophy), periodic architectural-invariant audit against cumulative shifts, design-cycle-weight review. Per-archetype drift judgment signatures partially formalize the Theme 6 deferral. Validity-space and replay determinism reconciled via the reproducibility-versus-acceptability distinction: the substrate is deterministic (reproducible) within a wider acceptability space.

D-094 recategorizes the admissibility-confidence threshold as governance_context — semantic risk tolerance, not operational. The threshold determines what the substrate is willing to assert as truth; same candidate + grounding + topology, different threshold yields different refusal. The replay test confirms it: changing this parameter is expected to change identity_hash, which an operational parameter must never do (D-088) but a governance parameter correctly does. The quality envelope observes the resulting policy-refusal distribution but does not own the threshold. Resolves the Theme 4 forward-compat reservation as a governance resolution. Corrects the Theme 7 opening lean (operational), strengthening the three-context separation.

SUBSTRATE_3_WORLDVIEW.md created as the Phase 1 closeout capstone: a distilled, non-chronological, non-implementation reference enumerating governing principles, non-goals, architectural invariants (the canonical registry D-092/D-093 reference as the calibration floor), semantic boundaries, and the six substrate responsibilities. The document future implementation work reads first.

SPEC §8 (quality envelope) substantively written with eight subsections (§8.1 through §8.8). OPEN_QUESTIONS.md updated: S3-Q-007 and S3-Q-008 resolved; Open section closed (all Phase 1 design questions, S3-Q-001 through S3-Q-008, resolved); Theme 4 admissibility-confidence reservation marked resolved by D-094; three new forward-compat reservations appended. GLOSSARY.md seeded with 15 Theme 7 terms.

TA-review-loop pattern. Single round of pressure-test with substantial round 2 integration — seven tightening items, all accepted. Convergence items: separate calibration surfaces from architectural invariants (item 1); strengthen evolution-adjudication safeguards (item 2); admissibility-confidence as governance_context (item 3, correcting the opening lean); envelopes relative to canonical routing profile (item 4); separate operational metrics from semantic-quality calibration (item 5); acknowledge validity-space weakens replay determinism (item 6); add the Phase 1 worldview artifact (item 7). The TA signaled convergence — "Everything else is strong enough to lock Phase 1."

Convergence pattern across Phase 1. Theme 2: three rounds; Theme 3: two rounds; Themes 4–7: one round + integration each. The maturing pattern reflects an architecture that stopped drifting after Theme 3 and thereafter converged in a single substantial integration per theme.

PHASE 1 COMPLETE. Substrate-3's full architecture is locked across Themes 1–7 (D-070 through D-094, 25 D-entries):

- Theme 1 (D-070): constrained interpretation engine; Guardrail 1; archetype × claim_kind × trigger_kind × recipe_kind; fail-loud philosophy.
- Theme 2 (D-071–D-076): request/outcome protocol; three-context separation; refusal taxonomy with typed payloads; llm_calls observability; Guardrail 2; explanation_hash; dismissal_reason vocabulary.
- Theme 3 (D-077–D-082): reasoning phases; per-archetype operationalization across five archetypes.
- Theme 4 (D-083–D-084): grounded-negative discipline; Guardrail 3; seventh refusal kind; admissibility_layer; bounded decomposition.
- Theme 5 (D-085–D-088): tool-use integration; substrate-as-orchestration-runtime; three thin semantic primitives; two-layer enforcement; telemetry/provenance separation; semantic-substance replay; eighth refusal kind (operational category).
- Theme 6 (D-089–D-091): prompt management with bounded co-evolution; eval suite with two-invariant replay and drift-as-evolution; single-model-per-batch routing.
- Theme 7 (D-092–D-094): quality envelope (calibration vs invariants; quality vs operational envelope; canonical-routing relativity); threshold evolvability and evolution-adjudication governance; admissibility-confidence as governance_context. Plus SUBSTRATE_3_WORLDVIEW.md.

Three Guardrails; eight dismissal_reasons across three reasoning phases; eight refusal kinds across three categories; per-archetype operationalization; grounded-negative discipline; tool-use LLM integration with substrate as orchestration runtime; two-layer enforcement; two-invariant replay with drift-as-evolution; prompt management with bounded co-evolution; single-model-per-batch routing; quality envelope protecting architectural invariants from calibration pressure. The next step is the single merge of `phase-1-substrate-3` to main, closing Phase 1. Phase 2 (operational details and implementation) opens thereafter.

## 2026-05-24 — Phase 2 build arc (D-095 through D-106) — PHASE 2 COMPLETE

Phase 2 implemented the architecture Phase 1 locked. The realized surface is a deliberately thin vertical — two emittable claim kinds carried end to end against real S1/S2 — proving the spine, governance, emission, persistence, eval, routing, and runner rather than building all sixteen claim kinds at once. SPEC §9 (Realized State) is now the authoritative "what is built"; `DEFERRED_ITEMS.md` consolidates the gap to the designed surface. Twelve D-entries (D-095–D-106).

**Realization + governance-core (D-095, D-096).** D-095 framed the implementation slicing under the S1/S2 boundaries actually shipped (the read subset of `SemanticOrgModel`; substrate-2's Coordinator). D-096 built the governance core: the admissibility engine (single-hop neighborhood, D-096.1), the refusal router, mechanical `explanation_hash` over the typed `attempted_interpretation` (D-075 adapted to the shipped shape), Layer B as a reject-only sanity floor (excerpt-anchoring length, not full semantic verification — D-096.3), and the per-requirement persistence transaction (D-096.6). The full replay/regeneration controller stayed deferred (D-096.4).

**Emission + the C-debut (D-097, D-098, D-099).** D-097 established that the substrate authors the substrate-2 claim + recipe bodies (Guardrail 2 / D-097.5), the semantic-completeness caveat registry (D-097.3), atomic claim+recipe+ledger persistence (D-097.4), and recorded negative semantic verification as a first-class future milestone (D-097.6). D-098 flipped the emission debut from the originally-sketched data-behavior value-claim (D-097.1) to **configuration metadata-relationship-claim** — the cleanest Layer-1-complete grounding (a verified Tier-1 edge), making it the honest first vertical. D-099 reopened substrate-2's locked five-kind trigger taxonomy to add a sixth, **`inspection-trigger`** — the invariant-inspective (no-causal-event) verification mode the metadata-inspection recipe needs — and locked atomic emission persistence in one tenant-bound Session.

**The Phase-3 carve-out (D-100).** Made the deferral boundary explicit and honest: the formula parser → Layer-2 verified negatives (the Phase-3 headline); the expect-rejection recipe observation mode; the S1 detail-read + value-claim positives; the remaining archetypes (permission, UI, integration). This is the spine of `DEFERRED_ITEMS.md`.

**The third outcome type — caveated draft (D-101).** Shipped the `data_behavior` prohibition-claim negative: a Layer-1-plausible emission (a ValidationRule applies; its formula is unparsed), which fires the caveat path for the first time. The caveat is persisted as emission-time epistemic posture — a typed `caveat_kind` + `caveat_required` on the outcome and the ledger row (D-101.3) — not derived on read, so a future Layer-2 rollout cannot silently rewrite the posture of older artifacts.

**Eval (D-102, D-103, D-104).** D-102 built the deterministic eval core (D-090 v1): scripted tool-turns bypass the LLM to gate correctness; two-invariant replay (identity_hash strict / explanation_hash weaker); drift-as-evolution (never auto-fail). D-103 realized D-089 prompt management slice 1 — a per-version frozen composed prompt with a content-hash guard, `CURRENT = "generation@v1"`, retiring the inline system prompt. D-104 added the live ontology-coherence gate (D-089 slice 2): real-gateway probes adjudicated against per-probe semantic envelopes (invariant = auto-fail / acceptable-variant = drift / benign = ignored), periodic and key-gated, never a PR-gate.

**Refuse-not-crash (D-105).** Closed a production crash: `resolve_intent` would `PROCEED_TO_EMIT` for any grounded claim, but `finalize_outcome` authored only the two emittable kinds and raised `NotImplementedError` for the rest — invisible in eval (the value-claim probe sits on a bare org → no-grounding refusal), but a grounded value-claim in a real org would abort the batch. The fix: a single emittable source of truth (`emission.EMITTABLE`), an admissibility gate that refuses grounded-but-unbuilt kinds with the **ninth refusal kind, `emission-deferred`** (operational category — the runtime face of D-097.6), and a graceful `finalize_outcome` backstop. The WORLDVIEW invariant moved from eight refusal kinds to nine.

**Production runner + routing (D-106).** Realized D-091 model routing as a pure `route_model` (explicit pin → tenant `always_use_opus` → archetype table → Opus default; bound once per batch as `model_override`, not the gateway `_CHAINS`) and the in-process production entry point `run_generation` — route → bind the routed gateway closure (or a test seam) → `GovernanceCore` over a tenant connection → `GenerationRuntime` with `LedgerPersister`. The live-eval gateway-binding pattern generalized into one shared closure builder (live pins a model; the runner routes). Abort-on-error with per-requirement-committed isolation; the HTTP/worker layer wrapping it (intake, auth, queue, retry) is the production-integration phase (D-106.4).

PHASE 2 COMPLETE. Realized: the C-debut (configuration metadata-relationship + data_behavior prohibition — 2 of 16 claim kinds); the three-type outcome spectrum (verified draft / caveated draft / refusal) with nine refusal kinds across three categories; the six-kind trigger taxonomy; the governance spine with the emittability gate; prompt registry + deterministic eval core + live ontology-coherence gate; single-model-per-batch routing + the in-process runner; atomic per-requirement persistence over migrations 0010–0040. Doc close-out: SPEC §9 (realized state) + status flips, this entry, `DEFERRED_ITEMS.md`, and the WORLDVIEW eight→nine refusal-kind correction. The branch (`phase-2-substrate-3`) merges cleanly to main (append-only DECISIONS_LOG D-095–D-106; main's D-071/D-087 errata in a non-overlapping region).

## 2026-05-25 — D-107 phase: formula parser → verified negatives (four slices) — PHASE-3 INCREMENT

The first Phase-3 landing and the differentiation headline (D-100.1): **static Layer-2 verification** for validation-rule negatives. An S1 sub-feature feeding an S3 phase, sliced S1-first on one branch (`formula-parser-verified-negatives`).

**Slice 1 — formula parser (S1).** A hand-written recursive-descent SF validation-rule formula parser (`primeqa/semantic/formula/`) → typed AST, fail-loud `NotParsed` sentinel. Fork 1 = A (shared pure library, re-parse at use, no persisted AST); Fork 3 = hand-written recursive descent (no dependency, deterministic); Fork 4 = lives in `primeqa/semantic/` (S1 owns the VR formula data; S3 imports it).

**Slice 2 — REFERENCES extraction (S1, closes §17 for same-object).** A sync-time writer (`primeqa/sync/validation_rule_refs.py`) parses `errorConditionFormula`, resolves same-object refs, writes `validation_rule_field_refs` (`read` / `priorvalue` / `ischanged`, multi-row per field); `derivation.py` emits the REFERENCES edges. `references_status` is 4-state with an honest `pending` default. Re-sync is version-scoped (the formula is in `hash_normalized`). Cross-object dotted refs deferred → `partial`.

**Slice 3 — violating-value derivation (S3).** `derive(ast) → VerifiedNegative | NotDerivable` (`primeqa/generation/verified_negative.py`): the certainty bar (D-107.1) — derive a create-time / single-object violating field assignment only when certain; org-state, cross-object, field-to-field, non-numeric ordering, `NOT(ISBLANK/ISPICKVAL)`, bare boolean → not-derivable (caveated fallback).

**Slice 4 — emission integration (S3, Option C).** Fork 2 resolved = 2a: a derivable formula drops the caveat and marks `LAYER_2`; otherwise the Layer-1-plausible caveated draft (D-101) is unchanged. The verified-vs-caveated line **is** the derivable/not-derivable line. **Option C:** `derive()` is the gate; the violating payload is *not* persisted (persisting it would shift the identity-bearing `ProhibitionClaimBody`'s `identity_hash` — a D-090(b) break), deferred to the D-100.2 behavioral recipe. Per-emission caveat (`requires_caveat(claim_kind, verified)`); `EmissionBundle.admissibility_layer` carries the marker; `finalize_outcome` reads it. Drift-guard: `LAYER_2 ⟺ caveat-dropped`. Eval corpus gains `verified-prohibition-negative`.

**Static, not behavioral.** Layer 2 here is static (a violating input is derivable with certainty); the recipe stays *inspection*. The behavioral construct-and-observe half is D-100.2 (deferred). Realized state: SPEC §10. Decisions: DECISIONS_LOG D-107 (+ slice-2 / Fork-2 = 2a / slice-4 amendments). Deferrals: `DEFERRED_ITEMS.md` (2026-05-25 note) + S1 `PHASE_2_PLAN_corrections.md` §17.

## 2026-05-25 — D-106.4 phase: production integration (five slices) — PHASE-3 INCREMENT

Wraps the in-process `run_generation` core (D-106.1) in the **service layer** that makes generation pilot-drivable. One branch (`s3-production-integration`), five slices. **Fork A = mirror, not reuse** (an S3-owned queue sharing no code with the v1 `generation_jobs` — the substrate never depends on the legacy v1 runtime); **Fork B = two-layer idempotency** (job-level get-or-create + a fresh `request_id` per attempt).

**Slice 1 — job model + idempotency.** Per-tenant `s3_generation_jobs` (+ `s3_generation_job_attempts`), `UNIQUE (requirement_key, s1_version_seq)`; `create_or_get_job` + `start_attempt` (mint a fresh `request_id`, no PK collision; attempt lineage is job-level — B-job, so the ledger's `prior_request_id` stays semantic).

**Slice 2 — intake (caller-fed).** `resolve_current_s1_version` (pins `MAX(version_seq)`) + `build_generation_request` (fresh single-requirement). The substrate receives requirement text, never fetches it (option B).

**Slice 3 — worker consumer.** Per-tenant claim (`SELECT … FOR UPDATE SKIP LOCKED`) → `run_generation` → complete/fail, in `worker_tick`, resilient per-tenant. The api_key is environment→connection-scoped, resolved worker-side (the job pins `environment_id`); the substrate core stays api_key-param-pure.

**Slice 4 — enqueue endpoint (the layer split).** v1-side `resolve_requirement` (reads `public.requirements`) + thin `views.py` routes bridge to the substrate `enqueue_s3_generation`. Substrate pure / caller-fed; the v1 read stays v1-side. The **first full vertical** (enqueue → consume → complete → ledger) goes green.

**Slice 5 — stale-job reaper.** Scheduler-hosted `s3_reaper_tick`; stale `claimed`/`running` → `failed` via a race-safe `fail()`, with a generous timeout (the consumer has no mid-run heartbeat).

**Pilot-drivable end-to-end**, with idempotency, per-tenant isolation, and stale-job recovery. A *static* service layer — abort-on-error (D-106.3); best-effort-continue + scale mitigations deferred. Realized state: SPEC §11. Decisions: DECISIONS_LOG D-106.4 (+ slice 1–5 amendments). Deferrals: `DEFERRED_ITEMS.md` (2026-05-25 D-106.4 note).

## 2026-05-27 — Behavioral-negative emission (D-110.3, S3-thin)

`_author_negative` now emits the **behavioral** recipe for a *verified* negative: the violating **create** whose `field_values` are the D-107 parser's already-derived `violating_payload` (previously computed in `_formula_verifies` and discarded under Option C) + `expect_rejection` (`RejectionExpectation(error_code="FIELD_CUSTOM_VALIDATION_EXCEPTION")`, the projection of the claim's `RejectionSignal`) + a `data-mutation-trigger`. This **replaces** the inspection re-verify (behavioral subsumes structural for a verified negative — it tests that the VR *enforces*, not merely that it is *configured*). **Caveated** negatives (non-derivable formula) stay inspection.

The S3 leg of the cross-substrate S4 CRUD vertical (S2 → S4 → S3; D-110.1 → D-110.3), on `phase-5-substrate-4-crud` (PR #5). The claim `identity_hash` is **stable** across verified/caveated — the violating payload lives in the recipe, never the claim (the Option-C invariant, verified by a `compute_identity_hash` test). Live necessity experiment: a violating-value-only create on a real managed-package VR returned `FIELD_CUSTOM_VALIDATION_EXCEPTION` (no `REQUIRED_FIELD_MISSING` short-circuit), the full S3 → S2 → S4 spine matched → `passed`. S3-thin is the live differentiator; required-field population (S3-A) is deferred-not-needed for VR-enforced prohibitions. Eval corpus `verified-prohibition-negative` updated to the behavioral recipe.

## 2026-06-01 — Positive value-claim emission-authoring (D-115 slice 1 side A)

The first *positive* data emission: `author_emission` can now author a create-and-verify recipe for a **value-claim** — the recipe S4 slice 1 side B will execute. `GroundedPositive` (target object + field + value) carries the value **verbatim** from the value-claim's `expected_value` — never derived or invented (contrast `GroundedNegative`, whose violating value is *derived* via D-107). `_author_positive` emits `ValueClaimBody(subject=<field>, expected_value=V)` + a data recipe: `CreateStep(field_values={field: V})` (no `expect_rejection`) → `ReadStep` (read the created record back) → `AssertStep(equals, field == V)`. The CreateStep carries the **semantic field only** — S4 fills the operational required-field padding at execution (the k16 boundary). `author_emission` dispatch + a dormant `finalize_outcome` read of `grounded_positive` complete it; the S2 model was already sufficient (`ValueClaimBody` + `CreateStep`/`ReadStep`/`AssertStep` + the `equals` predicate all pre-existed — no S2 change).

**Option-Q (the planning-pass coupling finding) — `EMITTABLE += value-claim` deferred to land with the held grounding stash.** A side-A planning pass found that `EMITTABLE` drives `resolve_intent`'s proceed-gate, so adding value-claim there *without* the grounding stash would make resolve `PROCEED_TO_EMIT` — crashing the propose-only emission-deferred test (`FakeToolTurn` over-call) and burning an emit LLM call in prod. So the **author-capability lands now**; `EMITTABLE += value-claim` + the governance grounding stash (the synthesis→intent `{field, expected_value}` contract) are **held together**. Until they land, a real grounded value-claim keeps deferring `EMISSION_DEFERRED` gracefully at resolve. See `DEFERRED_ITEMS.md` + DECISIONS_LOG D-115 / D-115.1. On `phase-6-substrate-4-positive`.

## 2026-06-01 — Value-claim grounding stash: production-reachable positive (D-115.3, Option Q resolved)

The middle of D-115's positive value-claim. Side A authored the emission; side B (`b76020b`) built the S4 execution spine — but `resolve_intent` could not build a `GroundedPositive` from a real intent, so the positive was unreachable. This slice closes it (the held Option-Q coupling): a field-and-value `GroundedPositive` is grounded + stashed during `resolve_intent`, and `EMITTABLE += ("data_behavior","value-claim")` opens the proceed-gate. The mechanism was pre-wired — `finalize_outcome` already read `state.grounded_positive`, and `target_subject_hint` already accepts `field_name` / `expected_value` (as the negative's `operation` rides it). **No S2 model change, no tools-schema change, no migration.**

**Verify-at-grounding.** A value-claim asserts `field == V`, so grounding now verifies the **named** field: `_evaluate_positive` gains a `field_hint` and grounds iff a Field whose `sf_api_name == field_hint` `BELONGS_TO` the object; an unknown named field → `insufficient_grounding` → `UNGROUNDED_CLAIM` (mirrors the negative's "no constraint"). Other positive claim_kinds keep the object-level any-field proxy.

**Refusal taxonomy + Option Q resolved.** field exists + value → `PROCEED_TO_EMIT` (emits the value-claim + the create→read→assert recipe side A/B own); field exists, **no value** → `EMISSION_DEFERRED` (S3 never fabricates — D-115 §2, grounded-then-deferred at the stash); field absent → `insufficient_grounding`. The gate-flip lands **in lockstep** with the stash + the drift-guard map (`_EMITTABLE_SHAPES += value-claim`, kept `== EMITTABLE`) + the integration test — so "a gated PROCEED is always authorable" holds, and side A's dormancy guard flips to assert the resolved state. Proven end to end: `test_draft_vertical.test_positive_value_claim_emitted_end_to_end` resolves → emits → persists a value-claim + a `data-recipe` (`data-mutation-trigger`).

**Scope — mechanism + tests.** The positive is reachable the instant the LLM supplies `field_name` + `expected_value`; the propose-prompt guidance + a live eval probe are a deferred follow-up (mirrors the negative — mechanism first, eval probe separate). Generation unit 119 + integration 55 green. DECISIONS_LOG D-115.3. On `phase-6-substrate-4-positive`.

## 2026-06-01 — Value-claim live reach: prompt generation@v2 + eval probe (D-115.4)

The last gap of D-115's positive value-claim, closed. D-115.3 made it reachable *if* the LLM supplies `field_name` + `expected_value` — but the frozen prompt `generation@v1` never told it to. This slice ships a new frozen prompt version that does, + an eval-corpus probe.

**Prompt `generation@v2`.** Frozen prompt versions are immutable + SHA-256 hash-guarded (D-103.1), so the change authors a *new* version: edit the working source (`base.md` title + `fragments/data_behavior.md`'s "Positives" bullet → supply `field_name` as the **fully-qualified `Object.Field`** name + `expected_value` verbatim, and never invent a value), `compose_working()` freezes `versions/generation_v2.md`, the registry records its hash + bumps `CURRENT`. v1 stays byte-frozen (a pinned-v1 request still resolves it); v2 differs only in the value-claim bullet + the title.

**Prompt-only, strict (Q1).** S1 stores field names qualified (`Account.Status`) and grounding does exact-match, so the prompt carries the burden of qualifying — the grounding is untouched (no leniency). A bare unqualified field would miss, which the live probe is positioned to catch.

**Eval probe — offline + live (Q2).** A `value-claim-positive-draft` corpus case (mirroring `verified-prohibition-negative`): an **offline scripted probe** (a value-claim intent with a qualified field + value → `draft` / `value-claim` / `data-recipe`) — deterministic, CI-gating; and a **`live` block** (a real requirement + envelope invariants) — the real-LLM confirmation, periodic (skipped without `ANTHROPIC_API_KEY`).

**Verified.** Offline eval 15 + prompt registry 6 (v1 frozen-hash intact, v2 recorded, CURRENT → v2) + generation unit 119 + integration 55 green. The live probe is authored + skipped — the positive value-claim's end-to-end LLM reach is proven on a periodic live run, as the verified-negative's is. No migration, no governance / S2 change. DECISIONS_LOG D-115.4. On `phase-6-substrate-4-positive`.

## 2026-06-02 — Config breadth: existence + property emission (Phase 2 slice 1, D-122)

The first Phase-2 (S3 breadth) slice — the emittable surface grows **3 → 5**. The two configuration kinds D-098.4 deferred to the S1 detail-read increment (now shipped, Phase 0 / D-119–D-120): **`existence-claim`** ("X exists") and **`property-claim`** ("Field X is required / has length N"). Both **Layer-1-complete, no caveat** (reading the metadata IS the verification, D-079), mirroring `_author_config`.

**Emission.** New S2 bodies `ExistenceClaimBody` / `PropertyClaimBody` — additive (`CLAIM_KIND_ENUM` already held both, D-121; they deserialize via the flat `ClaimBody` union, **no migration**). `GroundedExistence` / `GroundedProperty` + `_author_existence` / `_author_property` reuse `_inspection_recipe` (generalized with a predicate: `exists` for existence; `equals` / `is_null` for property). `EMITTABLE += {existence, property}`, kept lockstep with the `_EMITTABLE_SHAPES` drift-guard.

**Grounding.** `_resolve_configuration` opens beyond metadata-relationship: existence grounds via `get_entities` (absent → `no_relevant_context`); property via `get_entity_details` (an S1-modeled detail column carrying the asserted value → ground; unmodeled → `ontology_gap`; mismatch → ungrounded). **Invent-nothing:** the property value is READ from S1, never the assertion on faith. `check_refs_exist` extended to accept the flat `{entity_type, sf_api_name}` subject ref (it assumed source/target).

**Verified.** 179 generation tests green (4 emit probes + the drift-guard authoring both kinds + a new end-to-end **existence** integration test on real seeded S1: resolve → check-refs → ground → emit → persist). The integration test surfaced two real wiring bugs (the `check_refs_exist` subject branch; the intent-key shape) — both fixed. **Follow-ons:** property's *grounded* integration test (a seeded `field_details` row); the prompt live-reach (slice 1b — the LLM proposing these). No S1 change, no migration. DECISIONS_LOG D-122. On `phase-10-substrate-3-breadth`.
