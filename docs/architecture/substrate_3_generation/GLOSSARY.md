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
