# Substrate 3 — Generation Engine — SPEC

**Status:** Phase 1 in progress (Theme 1 complete; Themes 2–7 pending).

**Last substantive update:** 2026-05-19 (Theme 1 — substrate boundaries and architectural posture).

---

## Purpose

This spec defines Substrate 3: PrimeQA's generation engine — the substrate that interprets requirements (JIRA tickets in v1; architected to admit other requirement sources later) into substrate-2 test representations (claims plus recipes), grounded in substrate-1's semantic org model.

Design proceeds in two phases:

- **Phase 1 (in progress):** Conceptual shape across seven themes: substrate boundaries, generation request shape, per-archetype strategies, grounded-negative discipline, LLM integration architecture, prompt management, quality envelope.
- **Phase 2 (deferred):** Concrete implementation surfaces — request types, ledger schema, prompt versioning, eval infrastructure, retry mechanics. Lands when Phase 1 design is complete and Substrate 3 implementation is scheduled in the product roadmap.

See `BACKGROUND.md` for why this substrate exists. See `PRECONDITIONS.md` for the ground state at Phase 1 design start. See `OPEN_QUESTIONS.md` for design surfaces under deliberation. See `DECISIONS_LOG.md` D-070 onward for full design rationale.

---

## 1. Synthesis

Reserved. This section composes §2 through §N once all Phase 1 themes are resolved, mirroring substrate-2 SPEC §1 (written last as composition of the substantive sections).

---

## 2. What S3 is and isn't — substrate boundaries and architectural posture

**Resolution.** Per D-070: substrate-3 is a constrained interpretation engine bounded by S1 ontology and substrate-2 taxonomy, with refusal as first-class output. Seven structural commitments anchor the substrate (§2.7 enumerates).

### 2.1 What S3 is

S3 is the substrate that takes structured requirements as input — JIRA tickets in v1, with the architecture admitting other requirement sources later (sprint specs, conversational prompts via S7, change-spec inputs from S1 diff) — and produces substrate-2 records as output: identity-bearing claims (asserted truth + semantic conditions, with `IdentityBearingRef` typed references), one or more operational recipes (causal initiation + observation realization + execution environment, with `OperationalRef` typed references), and the coverage edges that derive from the claim's S1 references.

S3 interprets requirements through substrate-1's ontology and topology, not by translation. A JIRA ticket saying "test the discount approval flow" cannot be literally translated; the system must locate the relevant Flow, infer its triggering Object, walk to the validation rules and profile grants that constrain the approval, and decide which claims this requirement asserts among many structurally-valid possibilities. The work is ontological (which entities exist, what kinds they are) and topological (how they connect through S1's edge graph). The LLM mediates between natural-language input and typed output; the grounding inference is structured query work over S1's bitemporal graph.

S3 is schema-bounded. Substrate-2 locked sixteen claim kinds across five archetypes, five trigger kinds, and five recipe kinds. S3's LLM output must validate against this registry; it cannot invent new claim shapes, recipe forms, or trigger kinds outside the locked vocabulary.

S3 is LLM-mediated, not LLM-defined. Per the AI usage philosophy, LLMs are an enrichment layer over deterministic primitives. The interpretation layer is structured (ontology + topology queries against S1). Schema validation is deterministic (substrate-2 Pydantic + DB constraints). The LLM's role is mediating between unstructured input and structured output; structural validation rejects what the LLM proposes outside the bounded space.

S3 is user-driven, batch-capable, review-gated per `PRIMEQA_PRODUCT_DEFINITION.md` Rule 8. An engineer explicitly selects requirements and asks for generation; sprint-batch is a first-class workflow; every output is reviewed before execution eligibility. No background generation, no auto-approval.

### 2.2 What S3 is not

S3 is not the S1 retrieval engine. S3 calls S1's query primitives but does not own how S1 stores or retrieves. Retrieval strategy (which entities to feed the LLM for a given requirement) is S3-territory; retrieval mechanics are S1-territory.

S3 is not a test execution engine. Recipes are static artifacts S3 produces; S4 interprets and runs them when it ships. Conflating execution with generation was Architecture 4's first structural mistake (per `archive/ARCHITECTURE_4_NOTE.md`); the substrate decomposition is the explicit correction.

S3 is not a test evolution engine. S8 owns autonomous rewrites when S1 evolves. The v1 transitional reality (S8 does not yet exist) is addressed in §2.6.

S3 is not a failure attribution or explanation engine. S6 owns that. S3's output quality cannot be assessed via execution outcomes at v1, which shapes the quality envelope (Theme 7).

S3 is not a knowledge system. S5 owns Domain Packs, system rules, and learned facts. S3 consumes from S5; it does not author or curate knowledge.

S3 is not a test catalog, suite manager, or BA review workflow. Per substrate-2 D-065, those concerns live in future orthogonal substrates.

S3 is not a schema-design owner. Substrate-2 locked the claim/recipe/trigger taxonomy. S3 produces against that schema; gaps S3 discovers in the schema route to substrate-2 design cycles, not S3 invention.

S3 is not a general logic or specification language. Substrate-2 §2 commits to constrained claim structure; S3 inherits and reinforces that constraint.

S3 is not its own canonical historian. With `get_provenance` and `get_recipe_provenance` reserved per substrate-2 SPEC §10.2, S3 cannot rely on substrate-2 to remember its generation history. The architectural response is the S3-owned generation ledger (§2.6, schema deferred to Theme 2).

### 2.3 Relationship to substrate-2's authority model

S3's authority position under D-061: autonomous-but-bounded actor. S3 can write claims in `status='draft'` and recipes in `status='generated_unapproved'`. S3 cannot promote either to `approved`. S3 cannot diverge `identity_hash` from an existing approved version of the same `test_id`; attempts raise `AuthorityViolationError`. Same-hash regeneration is mechanically no-op: substrate-2 returns `was_noop=True` and emits no new version, no provenance event.

This shapes the design profoundly. There is no "ship and fix later" path; every claim S3 emits either enters the human-review queue (draft) or no-ops (semantic equivalent already exists). The substrate-level approval gate is the trust loop, and S3's output bar must be calibrated to that gate.

Dedup is an architectural concern. Before writing, S3 must check `query_equivalent_claims` to determine whether a semantically-equivalent claim already exists for the same `test_id`. If yes and the hash matches, S3 should not attempt the write — substrate-2 will no-op it, but the LLM cost and latency were wasted. Dedup discipline shapes the generation request pipeline (Theme 2) and the cost envelope (Theme 7).

### 2.4 Admissible grounding

Every claim S3 emits is admissibly grounded in S1: not merely that referenced entities exist, but that the org's actual constraint structure — as modeled by S1's current capability tier — supports the claim's assertion.

Existence-checking catches reference invention: a claim referencing a Field that doesn't exist in S1 fails substrate-2's `IdentityBearingRef` validation. Admissibility-checking goes further. A `value-claim` asserting `Account.AnnualRevenue = "high"` passes existence (AnnualRevenue exists on Account) but fails admissibility (AnnualRevenue is a Currency field; "high" is not a Currency value). A `prohibition-claim` asserting "creating Opportunity with Stage='Closed Won' is rejected" passes existence but fails admissibility unless some validation rule in this org actually rejects this creation.

V1 admissibility-checking is rigorous on what S1 currently exposes:

- Type compatibility (Currency, Text, Date, Boolean, Reference, etc.)
- Picklist value set membership (via `CONSTRAINS_PICKLIST_VALUES`)
- Permission grants (via `GRANTS_OBJECT_ACCESS`, `GRANTS_FIELD_ACCESS`)
- Layout containment (via `INCLUDES_FIELD`)
- Profile and PermissionSet assignment (via `HAS_PROFILE`, `HAS_PERMISSION_SET`)

V1 admissibility-checking degrades cleanly on the surfaces S1 has deferred:

- Validation-rule formula semantics — `REFERENCES` edges (ValidationRule → Field) are registered but not populated, pending the Salesforce formula parser (substrate-1 §17). Full constraint-rule admissibility cannot be machine-verified at v1. Claims grounded in validation-rule constraints are higher-confidence when the constraint can be parsed; the architecture accommodates both layers and progressively deepens when the formula parser lands.
- StandardValueSet content matching — picklist value sets for standard fields are not yet linked (substrate-1 §22). Picklist admissibility for custom-field picklists is fully checked; for standard fields, admissibility is partial.

This is honest. Theme 4 (grounded negatives) operationalizes admissibility-checking per archetype.

### 2.5 Failure-mode philosophy and the refusal taxonomy

Substrate-1's posture: fail loud over hallucinating; never invent metadata. Substrate-2's posture: no autonomous semantic divergence; mechanical invariants over judgmental. Substrate-3's analog:

> **S3 refuses to produce output rather than producing structurally valid but semantically wrong claims.**

Refusals are first-class product surface. A typed, actionable refusal is better product than confident-but-wrong output. The architectural defense against the platform's deep failure mode — confident wrongness in generation — is refusal infrastructure that the engineer experiences as informative.

Five refusal categories named in Theme 1 (typed `RefusalKind` taxonomy extends through Theme 2):

- `underspecified-requirement` — the requirement itself lacks specificity to ground anywhere in S1.
- `no-relevant-context` — the requirement is specific enough but does not connect to anything in this org's S1.
- `ambiguous-reference` — the requirement references something that disambiguates to multiple S1 entities without further input from the engineer.
- `ungrounded-claim` — the proposed claim would not be admissibly supported by S1's current constraint structure.
- `structural-validation-failure` — LLM output cannot be coerced to a valid substrate-2 body shape after bounded retry.

A sixth — `no-admissible-negative-scenario-found` — is anticipated for Theme 4.

Each refusal carries actionable feedback: not "could not generate" but a typed structured payload that tells the engineer what specifically is missing or ambiguous and what would unblock it. Refusals can be partial: S3 may generate three claims successfully and refuse one within the same batch. The protocol shape is a `GenerationOutcome` union admitting drafts, refusals, and partial outcomes; resolved in Theme 2.

Review bar is **verification, not co-authoring**. Output below the verification bar refuses. This forbids the slow-erosion failure mode where review degrades into repair work.

### 2.6 V1 transitional realities

Three honest acknowledgments about v1 reality versus steady-state architecture:

**S8 absence.** Recipe evolution is S8's responsibility in steady state. V1 has no S8; recipe quality is fully owned by S3 (at emission time) plus humans (in review and during the test's lifetime, manually). The architectural design bar for S3-emitted recipes is "S8-evolvable from day one" — structurally clean enough that S8 can incrementally rewrite under `identity_hash` preservation when it ships, without a repair pass first. Higher S3 design rigor up front; lower migration cost when S8 lands.

**S6 absence.** Generation-time output quality cannot be assessed via execution attribution feedback at v1, because no execution attribution exists. The quality envelope (Theme 7) leans on structural validation, eval suites, and human review signals. When S6 ships, the substrate-2 `report_run_outcome` callback path enables attribution feedback to inform S3's interpretation layer — closing the gap between generation-time and execution-time domain-truth checking.

**S3-owned generation ledger.** `get_provenance` / `get_recipe_provenance` are reserved per substrate-2 SPEC §10.2 and not realized in Phase 4. S3 carries its own generation ledger in v1: actor, prompt version, retrieval set, refusal categories, partial-outcome map, prior attempt linkage. Ledger schema is forward-compatible to substrate-2's eventual provenance shape; on the commit substrate-2 ships those interfaces, the ledger retires cleanly into substrate-2 provenance. Ledger schema design and lifecycle are first-order Theme 2 concerns.

### 2.7 Seven structural commitments

Per D-070:

1. Interpretation through ontology + topology, not translation.
2. Admissible grounding, not reference-existence.
3. Autonomous-but-bounded actor under substrate-2's authority model.
4. Verification bar, not co-authoring; refuse rather than emit weakly.
5. Refusal as first-class product surface, typed and actionable.
6. **S3 Guardrail 1:** semantic search space bounded by S1 ontology × substrate-2 taxonomy; LLM operates conservatively within bounds.
7. Failure-loud philosophy: S3 refuses to produce output rather than producing structurally valid but semantically wrong claims.

### 2.8 V1 archetype-coverage reality

Substrate-3's four-discriminator architecture (archetype × claim_kind × trigger_kind × recipe_kind) supports all five product archetypes at full strength. V1 product coverage is layered against current S1 Tier 1:

- **Data-behavior:** strong. Claims fully separable from recipe operational shape; all four claim-kinds (`value-claim`, `state-transition-claim`, `automation-effect-claim`, `prohibition-claim`) operationally tractable. Most v1 generation happens here.
- **Configuration:** solid. Claims reference S1 metadata entities that S1 fully models. All three claim-kinds (`existence-claim`, `property-claim`, `metadata-relationship-claim`) supported.
- **Permission:** usable. Both `capability-claim` and `sharing-rule-claim` map cleanly to S1's profile/permission-set/object/field entities. `capability-claim` has multiple recipe-kind realizations (run-as-execution vs metadata-inspection) — design question for Theme 3.
- **UI:** minimal. Lightning page composition is S1 Tier 3 and not implemented. V1 UI claims are limited to `layout-claim` (which fields appear in which sections) and partial `element-state-claim` constrained to layout-derivable elements. Full UI archetype coverage is blocked until S1 Tier 3 ships.
- **Integration:** scoped. The four claim-kinds (`platform-event-claim`, `outbound-message-claim`, `callout-claim`, `inbound-effect-claim`) are well-defined but org-specific implementation varies. V1 may ship a subset; full coverage operationalized in Theme 3.

The architecture forecloses none; v1 product reality is honest about the current S1 ceiling. Themes 3 and 4 operationalize the per-archetype design.

---

## 3. Generation protocol and governance architecture

**Resolution.** Per D-071 through D-076 (Theme 2): substrate-3's input/output protocol is the `GenerationRequest` → `GenerationOutcome` pair, embedded in a three-axis context separation (semantic / governance / operational), supported by a two-surface ledger architecture, and constrained by an ontology-bound reasoning vocabulary that makes transparency a governed substrate artifact. Section §3.10 lists forward-compat reservations carried out of Theme 2.

### 3.1 What Theme 2 closes

Theme 2 resolves the substrate's input/output protocol, governance architecture, and reasoning-vocabulary commitments. Six D-entries lock the architectural commitments:

- D-071: Generation request shape; three-axis context separation; typed regeneration lineage.
- D-072: GenerationOutcome protocol; binary draft/refusal kinds; no-silent-drops invariant.
- D-073: RefusalKind taxonomy; invalidity-vs-policy distinction; refusal-as-governed-behavior.
- D-074: Two-surface ledger architecture; typed cross-substrate provenance commitment.
- D-075: S3 Guardrail 2 (ontology-bound reasoning artifacts); transparency as governed substrate artifact; explanation_hash.
- D-076: Dismissal_reason taxonomy as substrate-3 reasoning vocabulary.

The convergence emerged from a three-round TA pressure-test loop. Round 1 surfaced seven refinements over the initial protocol sketch. Round 2 surfaced eight deeper substrate pressures including the meta-question "how much of S3's reasoning process becomes governed substrate truth." Round 3 collapsed the remaining surfaces into one architectural commitment — transparency is a governed substrate artifact — which the substrate accepted in full.

The resulting design is heavier than the substrate's first framing suggested. It is also more defensible against the architectural pressures the loop surfaced.

### 3.2 Generation request and three-axis context separation

Substrate-3's unit of work is a `GenerationRequest` carrying one or more typed external requirement references, three orthogonal context axes, and an optional regeneration lineage with typed deltas.

The three context axes are conceptually distinct and operationally separated:

- *semantic_context* captures what admissible world state was visible: Domain Packs invoked, system rule version active, S1 `version_seq` pinned, archetype hint. Two requests with the same `semantic_context` saw the same semantic world.
- *governance_context* captures what behavioral policy regime was active: refusal policy version, dismissal taxonomy version, transparency policy version. Two requests with the same `governance_context` are governed by the same thresholds, taxonomies, and stability rules.
- *operational_context* captures how generation was mechanically executed: prompt template version, model identifier, retry policy, budgets.

The separation enables a clean equivalence algebra. Same semantic + same governance + different operational → expected substrate-2 `identity_hash` match and substrate-3 `explanation_hash` match. Same semantic + different governance → expected behavior change. Different semantic → expected divergence. This algebra is the substrate's mechanical primitive for reasoning about regeneration semantics.

Regeneration lineage is typed by the `prior_request_id` binary discriminator plus a `regeneration_kind` enum carrying five values across two categories: semantic-continuity edges (clarification, grounding_evolution, requirement_change) and operational edges (model_experimentation, eval_replay, failure_recovery). Semantic-continuity edges participate in substrate-2 provenance lineage when `get_provenance` ships; operational edges stay substrate-3-adjacent and do not migrate.

See D-071 for full request shape and lineage discipline.

### 3.3 GenerationOutcome protocol and the no-silent-drops invariant

The output protocol is the `GenerationOutcome` discriminated union with binary `outcome_kind`: draft or refusal. Every requirement in a request is explicitly resolved by exactly one outcome row. The substrate enforces this structurally — a request cannot be marked complete until every requirement has one outcome. This is the architectural defense against the failure mode where N drafts are produced for M requirements with N < M, invisible to engineers.

Draft outcomes can carry any combination of newly-written claims, newly-written recipes, and references to existing claims that satisfy the requirement via dedup. The pure-dedup case is a draft outcome with empty `claims_written` and non-empty `equivalent_existing`. Mixed cases are normal. Dedup is not a separate outcome category because the requirement is resolved — the satisfying claim already exists.

Refusal outcomes carry a flat list of typed refusals (one outcome may carry multiple `RefusalKind` entries when multiple categories apply). Each refusal carries `refusal_kind`, `refusal_policy_version`, `refusal_schema_version`, and a typed feedback payload.

Every outcome — draft or refusal — carries a mandatory `attempted_interpretation` artifact and an `explanation_hash` derived from it. Drafts carry transparency artifacts so reviewers see why this draft and not other admissible candidates. Refusals carry them so engineers see what the substrate considered before refusing.

See D-072 for protocol shape and field discipline.

### 3.4 RefusalKind taxonomy and refusal-as-governed-behavior

Substrate-3 commits to a typed `RefusalKind` taxonomy of six categories at Theme 2 close (seventh anticipated for Theme 4). The architectural distinction is between *invalidity refusals* (the proposed claim is structurally wrong) and *policy refusals* (the proposed claim is structurally fine but doesn't meet a governance threshold):

- Five invalidity categories: `underspecified-requirement` (input), `no-relevant-context` (grounding), `ambiguous-reference` (resolution), `ungrounded-claim` (admissibility), `structural-validation-failure` (output).
- One policy category at Theme 2: `low-generation-confidence` (threshold).
- One policy category anticipated for Theme 4: `no-admissible-negative-scenario-found` (scope).

Refusals are governed behavior. Each carries a `refusal_policy_version` from the request's `governance_context`, and the substrate exposes refusal replay as a first-class operation: "would this requirement still be refused under policy v2?" is queryable without rerunning generation.

Each refusal kind has a typed feedback payload shape (see D-073 for per-kind structures). Feedback payloads are typed — not free-form text — because UX consistency, eval comparability, refusal replay under policy changes, and cross-substrate provenance typing all require structural fields.

V1 ships flat-list refusal multiplicity; hierarchy or sequencing reserved as forward-compat.

See D-073 for taxonomy and feedback payload shapes.

### 3.5 Two-surface ledger architecture

Substrate-3 carries two structurally separate ledger surfaces:

- *Semantic ledger* (S3-owned, retires to substrate-2 provenance when `get_provenance` ships): `generation_requests` and `generation_outcomes` tables. Carries the semantic audit trail. Migrates cleanly to substrate-2 provenance via typed cross-substrate provenance (§3.9).
- *Operational observability* (S3-adjacent, stays substrate-3): `llm_calls` table. Carries operational telemetry. Does NOT migrate to substrate-2.

The two surfaces are linked by `outcome_id`. Eval joins both; UX surfaces typically read only the semantic ledger; operational monitoring reads only the observability surface. Atomicity discipline: semantic write succeeds first; operational write can be lossy without compromising substrate guarantees.

The separation resolves three architectural concerns: lifecycle alignment, substrate-2 migration boundary (clean — substrate-2 absorbs semantics, not telemetry), and distinct access patterns.

See D-074 for full schema and forward-compat reservations.

### 3.6 Ontology-bound reasoning artifacts (S3 Guardrail 2)

Substrate-3 reasoning artifacts persisted in substrate state may only reference semantic concepts authorized by S1's ontology and substrate-2's taxonomy. They may not introduce durable semantic concepts outside this authorized set.

This is S3 Guardrail 2, extending Theme 1's Guardrail 1 from "the LLM's semantic search space is bounded" to "the substrate's own reasoning vocabulary is bounded." Every concept in `attempted_interpretation` is either an S1 entity (via `IdentityBearingRef`), a substrate-2 taxonomic value (archetype, claim_kind from locked enums), or a substrate-3 reasoning-vocabulary value (`dismissal_reason` from D-076's bounded enum).

The LLM may generate invalid alternatives during its reasoning, but the substrate only persists alternatives that pass the same admissibility check as the selected path. Invalid LLM-proposed alternatives cost LLM latency (recorded in `llm_calls`) but never reach substrate state. This is fail-loud-over-hallucinating applied to the reasoning artifact, not just the output.

Guardrail 2 prevents substrate-3 from drifting into shadow semantic authority. S1 still owns constraint structure; substrate-2 still owns claim shape; substrate-3 owns reasoning over both, but in their vocabulary, not its own.

See D-075 for Guardrail 2 statement and enforcement discipline.

### 3.7 Transparency as governed substrate artifact

`attempted_interpretation` is not a debug surface. It is substrate-grade governed behavior with its own equivalence relation (`explanation_hash`), its own stability commitment, and its own substrate-2 forward-compatibility through typed cross-substrate provenance.

The `explanation_hash` is computed by canonical ordering over the substrate-authorized typed fields of `attempted_interpretation`. It is mechanical, reproducible, and comparable across runs.

The substrate commits to stable `explanation_hash` under invariant `(semantic_context, governance_context)`. When regeneration produces a different `explanation_hash` than its lineage parent under invariance, the substrate emits a typed explanation drift event into the semantic ledger. Drift events are typed by drift_kind (structure / composition / reasoning_path) and detection is mechanical (hash inequality). Categorization of drift (regression / evolution / acceptable variation) is judgmental and deferred to S3-Q-008.

V1 ships structured-only `attempted_interpretation`. Free-form prose surfaces in reasoning artifacts are forward-compat reserved (explanation canonicalization for prose is heavier architecture for a later iteration). Explicit transparency policy version machinery is deferred to Theme 6.

This is the substrate-3 analog of substrate-2's D-051: identity is mechanical, not judgmental. For substrate-3, **explanation is mechanical, not judgmental, within substrate-authorized vocabulary.**

See D-075 for governed-artifact commitment and explanation_hash canonicalization.

### 3.8 Dismissal_reason as substrate-3 reasoning vocabulary

The `dismissal_reason` enum is substrate-3's reasoning vocabulary — the only new semantic vocabulary substrate-3 introduces beyond what S1 and substrate-2 already authorize. It is governed under substrate-2's `claim_kind` discipline: bounded, locked through deliberate D-entries, extended only through substrate-3 design cycles.

V1 bootstrap is 8 entries across 5 categories: TOPOLOGY (insufficient_grounding, no_grant_supports_capability, no_constraint_supports_negative), ONTOLOGY_INVALIDITY (type_incompatibility, archetype_mismatch), RANKING (ambiguous_target_resolution, lower_specificity), GOVERNANCE (policy_threshold_not_met), CONFIDENCE (reserved with no v1 entries — confidence considerations enter as `low-generation-confidence` refusal kind rather than dismissal reason).

Each entry's typed `category` meta-property is part of the locked taxonomy. Dismissed candidates may carry multiple non-exclusive reason_codes. V1 is unordered and unweighted; ordering and severity weighting reserved as forward-compat.

The taxonomy participates in `explanation_hash` canonicalization, eval comparison, refusal feedback payloads (`ungrounded-claim` refusals may cite dismissal_reasons), and cross-substrate provenance (`dismissal_taxonomy_version` exposed at semantic ledger row level for substrate-2 provenance continuity).

See D-076 for full taxonomy and discipline.

### 3.9 Typed cross-substrate provenance (substrate-2 forward-commitment)

The semantic ledger's row-level schema discipline is the substrate-2 forward-commitment: `refusal_kind`, `refusal_policy_version`, `refusal_schema_version`, `explanation_hash`, and `dismissal_taxonomy_version` are exposed at the row level, NOT buried inside JSONB.

When substrate-2's `get_provenance` ships, these fields migrate as typed provenance event columns. Substrate-2 uses them for: provenance filtering by `refusal_kind`; `refusal_policy_version` drift detection along a test_id's lineage; `explanation_hash` drift detection across regenerations; decisions about whether two refusal events are comparable under their schema versions.

Substrate-2 does NOT interpret `feedback_payload` contents or `attempted_interpretation` internals — those remain substrate-3-typed. The boundary is precise:

> **Substrate-3 owns refusal and explanation *semantics*. Substrate-2 owns refusal and explanation *provenance continuity* through typed structural metadata fields.**

This is typed cross-substrate provenance, scoped precisely. It is a substrate-2 design surface for the cycle when `get_provenance` is designed; substrate-3's commitment now is to expose the typed fields correctly so substrate-2's eventual interface has clean inputs.

See D-074 for cross-substrate provenance schema discipline.

### 3.10 Forward-compat reservations

Theme 2 carries the following reservations to downstream themes and post-Phase-1 evolution:

- *Explanation canonicalization for prose surfaces.* V1 ships structured-only `attempted_interpretation`. Prose surfaces require canonicalization rules to extend; deferred to a later iteration.
- *Explicit governance versioning machinery.* `refusal_policy_version`, `dismissal_taxonomy_version`, `transparency_policy_version` all ship at 'v1' with implicit calibration. Explicit version bump mechanics and replay-under-version-migration deferred to Theme 6.
- *Refusal multiplicity hierarchy or sequencing.* V1 ships flat-list refusals. Causality DAG or repair-path ordering reserved; schema accommodates non-breakingly.
- *Dismissal_reason ordering and weighting.* V1 ships unordered and unweighted. Primary-vs-supporting reasons or severity values reserved; schema accommodates non-breakingly.
- *CONFIDENCE category dismissal_reason entries.* No v1 entries; category placeholder reserved.
- *Operational observability archival policy.* V1 keeps full granularity. Archival/aggregation policy named when storage pressure surfaces.
- *Operational observability substrate boundary.* The `llm_calls` table is substrate-3-adjacent in v1. Provisional commitment — may eventually move to a future observability substrate.
- *Substrate-2 `get_provenance` interface design.* The cross-substrate typed provenance commitment is a substrate-2 design surface for that cycle.
- *S3-Q-008 extended:* Semantic equivalence policy under operational variation now covers both `identity_hash` and `explanation_hash` divergence. Categorization framework deferred to Themes 5 and 7.

---

## 4. Per-archetype generation strategies

Reserved for Theme 3.

---

## 5. Grounded negative test generation

Reserved for Theme 4.

---

## 6. LLM integration architecture

Reserved for Theme 5.

---

## 7. Prompt management and evaluation

Reserved for Theme 6.

---

## 8. Quality envelope

Reserved for Theme 7.

---

## Status

Theme 1 complete (D-070). Themes 2–7 pending.
