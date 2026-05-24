# Substrate-3 Worldview

A distilled statement of substrate-3's governing principles, non-goals, architectural invariants, semantic boundaries, and responsibilities. This is not a chronological record (see EVOLUTION.md) nor an implementation specification (see SPEC.md). It is the canonical reference for what substrate-3 is and is not — the document future implementation work should read first to avoid rediscovering implicit assumptions.

Authored at Phase 1 closeout (Themes 1–7, D-070 through D-094).

## 1. Governing principles

- **Constrained semantic orchestration runtime.** Substrate-3 is not a wrapper around an LLM. It is a runtime that bounds LLM cognition within architectural commitments (D-085). The substrate orchestrates; the LLM contributes bounded cognition.
- **LLM as bounded cognition provider.** The LLM proposes semantic intent, makes selection judgments, and emits outcomes. It does not orchestrate, author admissibility, categorize dismissals, or select refusal kinds (D-085, D-086).
- **AI as enrichment, not source of truth.** Deterministic extraction and structured intermediate forms precede LLM contribution. AI-generated artifacts remain reviewable, editable, versionable.
- **Fail loud over hallucinate.** When the substrate cannot ground a claim or resolve an interpretation, it refuses with a typed reason rather than fabricating. Refusal is a first-class outcome (D-072, D-073).
- **Deterministic reasoning before LLM interpretation.** Admissibility, decomposition, and governance are substrate-determined; the LLM's role is bounded to where genuine semantic judgment is needed.
- **Substrate as authority.** Admissibility is substrate governance truth, not LLM-authored interpretation (D-086). The substrate determines what counts as grounding, what layer applies, which candidates are admissible.
- **Root cause, not symptom.** Diagnosis precedes remediation; remediation addresses underlying mechanism. No workarounds, no silent fallbacks.

## 2. Non-goals

- **Not a general-purpose logic language.** Claim-kinds are semantic assertion forms, not a Turing-complete logic substrate. Prefer fewer top-level kinds with richer sub-discriminators; avoid taxonomy explosion.
- **Not an exploratory QA generator.** Guardrail 3 (D-083): the substrate generates only what the requirement implies. It does not independently originate negatives the requirement did not imply.
- **Not a raw Salesforce metadata mirror.** Substrate-1 models meaning, relationships, assertions, dependencies — not API payloads. Substrate-3 reasons over that semantic model.
- **Not autonomous plan-then-execute.** Planner-style autonomy is rejected (D-085); it expands the validation surface without proportionate value and is misaligned with the constrained-interpretation mission.
- **Not a clicking-automation tool.** The primary value is semantic understanding, failure attribution, and structured behavioral claims — not automated UI manipulation.

## 3. Architectural invariants (canonical registry)

These are substrate law. They are NOT calibration surfaces and NOT subject to the quality envelope (D-092 a). Evolution adjudication (D-093) must preserve every invariant in this registry even as behavioral distributions shift; a drift that breaches an invariant is regression by definition.

- **identity_hash semantic continuity (D-090 b).** Same substrate version + same semantic_context + same governance_context yields the same emitted output. Reproducibility is an engineering property of the substrate.
- **Guardrail Layer A validity (D-087).** Tool-boundary schema validation always holds: substrate-authorized vocabulary at enum positions, structural well-formedness, S1 entity-ref existence, requirement_excerpt presence.
- **Refusal transparency presence (D-073).** Every refusal carries its typed structured payload. Refusal is never opaque.
- **Grounding requirements (Guardrail 1, D-070).** Admissibility requires grounding in actual org metadata. Ungrounded claims are refused, not asserted.
- **The three Guardrails.** Guardrail 1 (semantic search space, D-070); Guardrail 2 (reasoning artifacts, D-075); Guardrail 3 (requirement-anchored origination, D-083).
- **Nine-refusal-kind taxonomy across three categories.** Invalidity (5), policy (2), operational (2) — D-073, D-083, D-088, D-105. Categories are semantically distinct and must remain so. (Authored as eight at Phase 1 closeout; the ninth, `emission-deferred`, landed in Phase 2 per D-105 — the operational category gained a second kind.)
- **Three-context separation (D-071).** semantic_context, governance_context, operational_context are distinct axes. semantic + governance determine identity_hash; operational variation preserves it.
- **Two-layer enforcement (D-087).** Layer A (schema, necessary) + Layer B (substrate-side semantic governance, sufficient). Schemas alone do not prevent semantic misuse.
- **Substrate as admissibility authority (D-086).** admissibility_layer is substrate-authored; the LLM never asserts it.
- **Reproducibility property (D-093 d).** The substrate is deterministic within a wider acceptability space — it picks one point in the validity-space and reproduces that pick.

## 4. Semantic boundaries

- **semantic_context** — per-request semantic inputs: requirements, S1 version, the substantive content the generation reasons over.
- **governance_context** — substrate governance policy, including semantic risk tolerance (the admissibility-confidence threshold per D-094). Determines what the substrate is willing to assert as truth. Identity-bearing: changing governance_context is expected to change identity_hash.
- **operational_context** — operational inputs that do not bear semantic identity: `prompt_template_version` (D-089), `llm_model_identifier` (D-091), `retry_policy`, `budgets`. Operational variation preserves identity_hash.
- **Calibratable surfaces vs architectural invariants (D-092 a).** The quality envelope calibrates behavioral distributions (refusal-rate by semantic category, Layer 1/2 distribution, explanation_hash drift threshold). It never calibrates the §3 invariants.
- **Quality envelope vs operational envelope (D-092 b).** Quality envelope is semantic (invalidity + policy refusal rates, Layer distribution, explanation_hash drift). Operational envelope is operational (cost, latency, budgets, operational-budget-exhausted rate). Calibrated independently.
- **Reproducibility vs acceptability (D-093 d).** Semantic reproducibility (engineering property, replay determinism) is distinct from semantic acceptability (semantic property, validity-space). The substrate is reproducible within a wider acceptability space.
- **Semantic provenance vs operational telemetry (D-087 b).** attempted_interpretation (semantic provenance, in the generation_outcomes ledger) is distinct from llm_calls (operational telemetry, substrate-3-adjacent).

## 5. Substrate responsibilities

The six engine roles (D-085):

- **Orchestration engine** — coordinates the reasoning phase pipeline (interpretation → grounding → governance, D-077).
- **Governance engine** — enforces the three Guardrails at both schema and semantic levels (two-layer enforcement, D-087).
- **Admissibility engine** — derives admissibility from S1 + substrate-2 taxonomy + Layer 1/2 discipline (D-083). The substrate is the admissibility authority.
- **Decomposition controller** — enforces canonical-negative-per-failure-mode + highest-specificity + bounded enumeration (D-083).
- **Replay controller** — computes identity_hash and explanation_hash over semantic substance (D-088); surfaces drift events (D-075, D-090).
- **Refusal router** — categorizes refusal causes across nine typed kinds in three categories with typed payloads (D-073, D-083, D-088, D-105).

The LLM contributes bounded cognition (semantic intent, selection judgment, outcome emission) within this runtime. The substrate is the locus of architectural authority.
