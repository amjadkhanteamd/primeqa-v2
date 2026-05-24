# Substrate 3 — Generation Engine — SPEC

**Status:** Phase 1 (design) complete — Themes 1–7 (D-070–D-094). Phase 2 (implementation) complete — D-095–D-106. See §9 for realized state.

**Last substantive update:** 2026-05-24 (§9 Realized State — Phase 2 implementation pass).

---

## Purpose

This spec defines Substrate 3: PrimeQA's generation engine — the substrate that interprets requirements (JIRA tickets in v1; architected to admit other requirement sources later) into substrate-2 test representations (claims plus recipes), grounded in substrate-1's semantic org model.

Design proceeded in two phases, both now complete:

- **Phase 1 (design — complete):** Conceptual shape across seven themes: substrate boundaries, generation request shape, per-archetype strategies, grounded-negative discipline, LLM integration architecture, prompt management, quality envelope. Locked across D-070–D-094 (§§2–8).
- **Phase 2 (implementation — complete):** Concrete implementation surfaces — request types, ledger schema, prompt versioning, eval infrastructure, routing, the production runner. Built across D-095–D-106. **§9 (Realized State) is the authoritative record of what is actually built**; §§2–8 below remain the design-era record, including the theme-close refusal-taxonomy snapshots (6/7/8 kinds) that §9 supersedes with the realized 9.

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

**Resolution.** Per D-077 through D-082 (Theme 3): the five archetypes (data-behavior, configuration, permission, UI, integration) are operationalized within the governance architecture locked in Theme 2. Each archetype is specified along four dimensions — interpretation scope, admissibility-checking shape, recipe-kind selection, refusal dominance — established by the cross-cutting framework in D-077. Theme 3 produces implementation discipline within Themes 1 and 2's architectural commitments; no new substrate-level primitives are introduced.

### 4.1 What Theme 3 closes

Theme 3 resolves per-archetype operationalization. Six D-entries lock the per-archetype strategies:

- D-077: Cross-cutting framework (four dimensions; shared interpretation context; archetype hint as guidance; dismissal_reason by phase).
- D-078: Data-behavior archetype.
- D-079: Configuration archetype.
- D-080: Permission archetype with recipe-kind selection preserving claim semantics.
- D-081: UI archetype with honest v1 scope.
- D-082: Integration archetype with operational-only v1 admissibility.

The convergence emerged from a two-round TA pressure-test loop. Round 1 surfaced six design surfaces. Round 2 surfaced four tighten-now refinements (softened one-pass to shared context; recipe-kind selection preserves claim semantics; archetype hint as guidance not constraint; dismissal_reason by phase not archetype) plus two acknowledgments (UI platform perception consequence; integration interaction-topology admissibility deferred). All four refinements were accepted; the substrate's architectural posture stayed coherent.

### 4.2 Cross-cutting framework

Each archetype is specified along four dimensions:

1. *Interpretation scope* — which S1 entity types the interpretation layer constructs `scoped_neighborhood` from for this archetype.
2. *Admissibility-checking shape* — what concretely constitutes admissibly grounded for each claim_kind in this archetype, given S1's current capability tier.
3. *Recipe-kind selection* — which substrate-2 recipe_kinds are appropriate for which claim_kinds; where the substrate has latitude vs where claim semantics force the selection.
4. *Refusal dominance* — which `RefusalKind` categories tend to dominate in this archetype, informing Theme 7's per-archetype quality envelope calibration.

Three architectural commitments anchor the framework beyond the four-dimensional spec:

- *Shared interpretation context across the batch.* The substrate's reasoning has access to all requirements in the batch when scoping neighborhoods and constructing candidate paths. Cross-requirement awareness for sprint batches is preserved; multi-archetype decomposition where one ticket touches multiple archetypes is supported. *Implementation topology* (one-pass, multi-pass coordinated, planner-style with explicit dependency graph) is resolved in Theme 5, not committed at Theme 3.
- *Archetype hint as guidance, not constraint.* The `archetype_hint` carried in `semantic_context` (D-071) is the interpretation layer's prior. The substrate uses it to bias initial scoping but continues with better-grounded interpretation when grounding signal indicates a different archetype, surfacing the detected archetype in `attempted_interpretation`. Refusal occurs only when reinterpretation is itself ambiguous (existing refusal kinds cover these cases).
- *Dismissal_reason applicability by phase, not by archetype.* The D-076 bounded enum applies uniformly across archetypes. Applicability is governed by reasoning phase — interpretation phase (`ambiguous_target_resolution`, `lower_specificity`), grounding phase (`insufficient_grounding`, `no_grant_supports_capability`, `no_constraint_supports_negative`, `type_incompatibility`, `archetype_mismatch`), governance phase (`policy_threshold_not_met`). Phase is metadata-about-the-taxonomy, not new persisted vocabulary.

See D-077 for the framework and the three architectural commitments.

### 4.3 Data-behavior archetype

*Interpretation scope:* Object-centered. Target Object; Field entities filtered by relevance; ValidationRule entities applicable; Flow entities triggering; Profile/PermissionSet entities granting access.

*Admissibility per claim_kind:* type compatibility + permission grants + picklist membership (value-claim); ValidationRule + permissions + Flow side-effects (state-transition-claim); automation entity existence with effect-tractability (automation-effect-claim); constraint entity Layer 1 admissibility (prohibition-claim, with Layer 2 deferred to formula parser).

*Recipe-kind:* API-execution dominant; UI-execution where requirement specifies UI behavior; metadata-inspection rare.

*Refusal dominance:* underspecified-requirement, ambiguous-reference, ungrounded-claim for prohibition-claim formula-parser cases, low-generation-confidence.

Strongest v1 archetype coverage. Most v1 generation lands here.

See D-078 for full data-behavior strategy.

### 4.4 Configuration archetype

*Interpretation scope:* metadata-entity-centered. Light graph traversal.

*Admissibility per claim_kind:* S1 existence (existence-claim); modeled-property-state (property-claim, with `ungrounded-claim` when property unmodeled at S1 Tier 1); S1 edge presence (metadata-relationship-claim).

*Recipe-kind:* metadata-inspection dominant.

*Refusal dominance:* no-relevant-context, ambiguous-reference, ungrounded-claim for unmodeled properties.

Solid v1 coverage. Simplest admissibility case across archetypes.

See D-079 for full configuration strategy.

### 4.5 Permission archetype

*Interpretation scope:* permission-grant-centered. Object/Field + Profile/PermissionSet + grant edges + assignment edges. Sharing rules + OWD + Apex sharing are S1 Tier 2 and not scoped.

*Admissibility per claim_kind:* grant-level verification (capability-claim, with `low-generation-confidence` refusal when sharing/OWD/Apex-sharing dimensions affect the assertion); sharing-rule-claim structurally weak at v1.

*Recipe-kind selection preserving claim semantics:* metadata-inspection and run-as-execution are not equivalent verification surfaces — they verify *different epistemic truths about reality*. The substrate defaults to metadata-inspection (configured permission state); refuses with disambiguation prompt when the claim's grounding indicates run-as-execution is required (runtime-effective experience); admits run-as-execution as engineer-opt-in via `operational_context`. The substrate does not silently substitute one verification surface for another.

*Refusal dominance:* ungrounded-claim (sharing-rule), low-generation-confidence (complex capability), no-relevant-context.

Usable v1 coverage. The recipe-kind-preserves-claim-semantics commitment is the sharpest architectural commitment in Theme 3.

See D-080 for full permission strategy.

### 4.6 UI archetype

*Interpretation scope:* PageLayout-centered. Lightning Pages, LWC, Aura Components, Dynamic Forms, Flow Screens are S1 Tier 3 and not modeled.

*Admissibility per claim_kind:* PageLayout containment verification (layout-claim); layout-derivable element verification (element-state-claim, with `no-relevant-context` refusal for non-layout-derived elements).

*Recipe-kind:* metadata-inspection for layout-claim; UI-execution (Playwright-driven) for element-state-claim.

*Refusal dominance:* no-relevant-context dominant by wide margin (Lightning composition Tier 3 absence). ungrounded-claim for non-layout-derivable elements.

Minimal v1 coverage. Higher baseline refusal rate is honest about S1 Tier 3 absence, not a quality regression. Theme 7 quality envelope calibrates per-archetype, with UI's higher refusal baseline preserved.

See D-081 for full UI strategy.

### 4.7 Integration archetype

*Interpretation scope:* per claim_kind. PlatformEvent + subscribers (platform-event-claim); OutboundMessage + workflow rule (outbound-message-claim); NamedCredential + RemoteSiteSetting + callout-defining Apex (callout-claim); inbound handler (inbound-effect-claim). Apex partial per S1 Tier 2.

*Admissibility per claim_kind: operational-only at v1.* Verification of integration entity existence and structural connectivity. Does NOT verify cross-system causality, external observability, temporal sequencing, or protocol semantics — these are interaction-topology concerns that require their own architectural framework, reserved for a future substrate-3 cycle.

*Recipe-kind:* API-execution + metadata-inspection-alongside per claim_kind.

*Refusal dominance:* ungrounded-claim (Apex Tier 2 limit), no-relevant-context (org-specific implementation variance), ambiguous-reference.

Scoped v1 coverage. Conceptually the weakest archetype at v1 — interaction-topology admissibility deferred to a future cycle.

See D-082 for full integration strategy.

### 4.8 Forward-compat reservations

Theme 3 carries the following reservations to downstream themes and future substrate-3 cycles:

- *Implementation topology of interpretation across the batch.* Theme 3 commits to shared interpretation context; the orchestration shape (one-pass, multi-pass coordinated, planner-style with explicit dependency graph) is resolved in Theme 5.
- *Run-as-execution upgrade path for permission archetype.* When S1 Tier 2 ships sharing rules, OWD, and Apex sharing modeling, currently-refused complex permission claims may upgrade to grounded run-as-execution recipes. The substrate's v1 refusal-with-disambiguation pattern is the right posture pending Tier 2.
- *Integration archetype interaction-topology admissibility.* V1 ships operational-only admissibility per D-082. A future substrate-3 cycle may add interaction-topology admissibility (cross-system causality, external observability discipline, temporal sequencing semantics, protocol semantics) when integration becomes a larger product surface.
- *S1 Tier 3 lifting for UI archetype.* When Lightning page composition ships (S1 Tier 3), UI archetype coverage expands materially. Currently-refused element-state-claims targeting non-layout-derived elements may upgrade to grounded Lightning-component-aware admissibility.
- *S1 §17 formula parser lifting for data-behavior archetype.* When the formula parser ships (substrate-1 §17), prohibition-claim and state-transition-claim admissibility upgrades from Layer 1 (rule exists and is active) to Layer 2 (formula actually rejects/permits this specific scenario).
- *S1 Tier 2 Apex modeling lifting for integration and data-behavior archetypes.* When Apex modeling ships at S1 Tier 2, automation-effect-claim, platform-event-claim, callout-claim, and inbound-effect-claim admissibility upgrades from existence-only to effect-tractable depth.

---

## 5. Grounded negative test generation

**Resolution.** Per D-083 and D-084 (Theme 4): substrate-3 commits to a grounded-negative discipline preventing the v2 failure mode of plausible-but-ungrounded negatives. Five architectural commitments locked in D-083 plus per-archetype operationalization locked in D-084. Theme 4 adds one new Guardrail, one new refusal kind, one new artifact-level output field — and operationalizes within the Theme 3 archetype strategies.

### 5.1 What Theme 4 closes

Two D-entries lock grounded-negative discipline + per-archetype scope:

- D-083: Grounded-negative discipline with five architectural commitments — S3 Guardrail 3 (requirement-anchored origination); seventh refusal kind with typed internal cause; polarity strictly derived; bounded decomposition; Layer 1 admissibility produces artifact-level visibly degraded trust marker.
- D-084: Per-archetype grounded-negative scope; integration causal-admissibility forward-compat reservation.

Convergence emerged from a single round of TA pressure-test surfacing six surfaces. Round 2 integration accepted all six: four as architectural commitments (Guardrail 3, typed internal cause, polarity derived, bounded decomposition), one as substrate-3 output property (Layer 1 visible trust), one as forward-compat reservation (integration causal admissibility). The TA explicitly signaled convergence after round 2 integration.

### 5.2 Grounded-negative discipline (D-083)

The discipline prevents the v2 failure mode of plausible-but-ungrounded negatives — tests asserting that a scenario "should fail" without grounding the failure in a specific org constraint. Such tests pass spuriously, fail spuriously, and erode trust.

A negative claim asserts *rejection, absence, or inability* rather than *occurrence, presence, or capability*. Negatives appear across every archetype, not as a separate claim_kind, but as a structural property of claim content. For positive claims, admissibility means the org's constraint structure supports the asserted truth. For negative claims, admissibility means a specific org constraint produces the asserted rejection or absence.

To emit a negative claim, the substrate must:

1. Identify specific org constraint(s) that produce the rejection or absence.
2. Verify the constraint(s) are admissibly grounded in S1 (Layer 1 or Layer 2 per constraint type and S1 capability tier).
3. Surface the grounding in `attempted_interpretation.candidate_paths` referencing the constraint(s).
4. Generate a recipe verifying the failure for the asserted reason when execution layer supports this.
5. If no specific org constraint can be identified that should produce the rejection or absence, refuse with `no-admissible-negative-scenario-found` (the seventh refusal kind, §5.3).

The discipline is governed under S3 Guardrail 3.

#### 5.2.1 S3 Guardrail 3 — Requirement-anchored origination

Per D-083 (a): grounding constraints justify candidate negatives derived from requirement interpretation. They do not independently originate negatives the requirement did not semantically imply.

Lineage of substrate-3 guardrails:
- *Guardrail 1* (Theme 1): semantic search space bounded by S1 ontology × substrate-2 taxonomy.
- *Guardrail 2* (Theme 2 D-075): ontology-bound reasoning artifacts. Substrate-3 reasoning may only reference S1 ontology + substrate-2 taxonomy + substrate-3 reasoning vocabulary.
- *Guardrail 3* (Theme 4 D-083): requirement-anchored origination. The substrate may not originate negatives the requirement did not semantically imply, even if grounding constraints exist that would admit them.

Each Guardrail tightens what the substrate may do under what authority. Guardrail 3 is the architectural defense against the substrate's quiet drift from "constrained interpretation engine" (D-070 §2.1) to "exploratory QA generator."

Mechanical enforcement: every candidate carries — in `attempted_interpretation.candidate_paths` — the requirement excerpt(s) from which it was derived. Candidates without traceable origin are rejected as substrate-internal products before grounding-phase admissibility checking.

### 5.3 The seventh refusal kind: `no-admissible-negative-scenario-found`

Per D-083 (b). Anticipated in Theme 2 D-073 as a policy-scope category; Theme 4 ships.

Updated refusal taxonomy at Theme 4 close (7 categories):

| RefusalKind | Category | Origin theme |
|---|---|---|
| `underspecified-requirement` | invalidity (input) | Theme 1 |
| `no-relevant-context` | invalidity (grounding) | Theme 1 |
| `ambiguous-reference` | invalidity (resolution) | Theme 1 |
| `ungrounded-claim` | invalidity (admissibility) | Theme 1 |
| `structural-validation-failure` | invalidity (output) | Theme 1 |
| `low-generation-confidence` | policy (threshold) | Theme 2 (D-073) |
| `no-admissible-negative-scenario-found` | policy (scope) | Theme 4 (D-083) |

Feedback payload:

    no-admissible-negative-scenario-found: {
      cause: <ontology_gap | no_org_constraint | policy_restraint>
      proposed_negative_assertion: <typed structure>
      searched_constraint_dimensions: [<typed list>]
      no_grounding_found_because:    <typed reason from substrate-authorized vocabulary>
      what_would_unblock:            [<optional typed list>]
    }

Three internal causes:

- `ontology_gap` — substrate cannot ground because S1 doesn't model the relevant constraint dimension. The substrate is incapable, not the org.
- `no_org_constraint` — the org genuinely has no constraint producing the asserted rejection. The substrate could ground if a constraint existed.
- `policy_restraint` — a candidate grounding exists but admissibility-confidence threshold not met. Distinct from `low-generation-confidence` (selection-confidence uncertainty).

External refusal_kind stays single. Internal cause preserves semantic granularity for evals, replay, analytics, capability tracking.

Interaction with D-076's `no_constraint_supports_negative` dismissal_reason: the dismissal_reason fires per dismissed candidate during grounding-phase reasoning. The refusal_kind is the outcome-level aggregate when all candidates dismissed for grounding reasons.

### 5.4 Polarity recognition

Per D-083 (c). Polarity is semantic claim identity, not interpretation metadata. The substrate recognizes negatives from claim_kind + content, not from a separate `polarity` field.

Two categories of claim_kinds:

1. *Inherently negative claim_kinds.* `prohibition-claim` (data-behavior). The claim_kind IS the negative semantic.
2. *Content-derived polarity claim_kinds.* `capability-claim`, `existence-claim`, `property-claim`, `metadata-relationship-claim`, `layout-claim`, `element-state-claim`, integration claim_kinds. Polarity determined by claim content (e.g., capability-claim with grant asserted is positive; with grant denied is negative).

The grounded-negative discipline applies to claim instances whose semantic content asserts rejection or absence, recognized from claim_kind + content. Per-archetype recognition rules in §5.7.

No parallel `polarity` field on `candidate_paths`. No risk of inconsistency between claim_kind and a separate polarity field. Substrate-2 claim_kind remains authoritative.

### 5.5 Bounded decomposition discipline

Per D-083 (d). Three-part principle protecting against combinatorial expansion in enterprise orgs with overlapping constraints:

1. *Canonical negative per identifiable failure mode.* An identifiable failure mode is a distinct semantic dimension of negative the requirement implies. The substrate emits one negative per failure mode.
2. *Highest-specificity grounding among admissible alternatives.* When multiple constraints could ground one failure mode, the substrate selects the most specific (most directly addresses the requirement's intent). Dismissed alternatives surface in `attempted_interpretation.dismissal_reasons` with `lower_specificity` (D-076 existing reason).
3. *Bounded candidate enumeration during interpretation.* Top-K candidates per failure mode, K configurable per `governance_context.transparency_policy_version`.

Combined effect: enterprise orgs with overlapping validation rules, layered permissions, and multiple automation gates do not produce many emitted negatives. One canonical negative per failure mode with dismissed alternatives transparently surfaced as `lower_specificity` dismissals. Review UX stays bounded; lineage stays tractable.

### 5.6 Layer 1 / Layer 2 admissibility with artifact-level trust visibility

Per D-083 (e). Substrate-3 commits to artifact-level admissibility-layer visibility for Layer 1 negatives, preventing the false-trust failure mode.

Admissibility layers (from D-078):
- *Layer 1.* Validation rule exists and is active; formula not parsed. The negative test triggers the rule, expecting rejection. Verifies "rule fires." V1 reality for validation-rule-grounded negatives.
- *Layer 2.* Validation rule's formula confirms the rule rejects this specific scenario. Verifies "rule fires because formula evaluates to reject for this input." Post substrate-1 §17 formula parser.

Substrate-3 output schema commitments:

- *`admissibility_layer` field at artifact top level* — `layer_1` | `layer_2`. Not nested in `attempted_interpretation`; structural top level alongside claim, recipe, provenance.
- *Substrate-emitted natural-language caveat in Layer 1 artifacts* — the artifact's narrative includes: "Layer 1 admissibility — validation rule applicability verified; formula-specific rejection logic not parsed."
- *Downstream review UX renders the layer prominently* — UX rendering is product responsibility; substrate-3 provides artifact-level field + explicit natural language.

Other negative groundings (required-field constraint, type incompatibility, permission grant absence, layout INCLUDES_FIELD absence, integration entity absence) achieve Layer 2 admissibility directly at v1; their artifacts carry `admissibility_layer = layer_2` without the caveat.

### 5.7 Per-archetype grounded-negative scope (D-084)

Per-archetype admissibility-layer distribution and grounding sources:

*Data-behavior.* Richest. Groundings: validation rule (Layer 1 → Layer 2 post formula parser); required field (Layer 2); type incompatibility (Layer 2); permission restriction (Layer 2); automation rejection (partial). Canonical negative shape: `prohibition-claim`.

*Configuration.* Cleanest mechanically. Groundings: S1 entity absence (Layer 2); property state absence (Layer 2 if S1-modeled, else `ungrounded-claim`); edge absence (Layer 2).

*Permission.* Leverages D-080 recipe-kind discipline. Groundings: grant absence (Layer 2 within v1 grant-level); sharing-rule absence (structurally weak; refuses with `no-admissible-negative-scenario-found` cause=`ontology_gap`, what_would_unblock=S1 Tier 2 sharing).

*UI.* Narrow at v1. Groundings: INCLUDES_FIELD edge absence (Layer 2); non-layout-derivable element-state (refuses with cause=`ontology_gap`, what_would_unblock=S1 Tier 3 Lightning page composition).

*Integration.* Operational-only-admissible at v1. Groundings: integration entity absence (operational); configuration absence (operational). Causal-admissibility deeper cases reserved (§5.8).

### 5.8 Forward-compat reservations

Theme 4 carries the following reservations to downstream themes and future substrate-3 cycles:

- *Layer 2 admissibility upgrade for validation-rule-grounded negatives.* When substrate-1 §17 formula parser ships, validation-rule-grounded negatives upgrade from Layer 1 to Layer 2 automatically. `admissibility_layer` artifact field value shifts; substrate-emitted caveat no longer present. Non-breaking.
- *Integration negative causal admissibility.* Integration negatives (absence-of-effect, non-firing assertions) categorically different from constraint-admissible negatives — verifying non-firing under specific causal conditions requires temporal observation, causal interpretation, distributed-state reasoning. V1 ships with constraint-admissibility framing capturing the simplest cases. A future substrate-3 cycle may add causal-admissibility framework for the philosophically deeper integration negatives; parallel to D-082's interaction-topology admissibility reservation; will likely converge with it in the same future cycle.
- *Substrate-3 admissibility-confidence calibration for negative `policy_restraint` cause.* The `policy_restraint` cause for `no-admissible-negative-scenario-found` depends on the substrate's admissibility-confidence threshold; Theme 7's quality envelope work calibrates per archetype.

---

## 6. LLM integration architecture

**Resolution.** Per D-085 through D-088 (Theme 5): substrate-3 integrates with the LLM via tool-use, with substrate-3 as a constrained semantic orchestration runtime and the LLM as a bounded cognition provider. Three thin semantic primitives (propose_semantic_intent, select_canonical, emit_outcome) expose substrate-3 to the LLM; substrate-side orchestration is internal and free to evolve through Themes 6/7 calibration. Theme 5 also introduces the eighth refusal kind (`operational-budget-exhausted`) as a third refusal category (operational) and tightens replay equivalence to semantic substance (not operational trace).

### 6.1 What Theme 5 closes

Four D-entries lock the LLM integration architecture:

- D-085: Integration topology — tool-use selected; substrate-3 as constrained semantic orchestration runtime; LLM as bounded cognition provider.
- D-086: Thin tool surface schema — three semantic primitives; substrate-side orchestration internal; substrate is admissibility authority.
- D-087: Two-layer Guardrail enforcement (schema validation + substrate-side semantic governance validation) and clean separation of operational telemetry (`llm_calls`) from semantic provenance (`attempted_interpretation`).
- D-088: Multi-turn statefulness semantics; replay equivalence over semantic substance (refinement of D-071/D-075); eighth refusal kind `operational-budget-exhausted` (operational category).

Convergence emerged from a single round of TA pressure-test surfacing eight architectural issues. Round 2 integration accepted all eight: tool surface reshape from six phase-shaped tools to three thin semantic primitives; substrate as admissibility authority (LLM does not author admissibility); multi-turn statefulness clarified (rejected tool calls operational, not semantic); operational telemetry cleanly separated from semantic provenance; two-layer Guardrail enforcement (schemas necessary but not sufficient); replay equivalence over semantic substance (not operational trace); eighth refusal kind for operational budget exhaustion; substrate-3 reframed as orchestration runtime.

### 6.2 Integration topology and substrate framing (D-085)

**Topology selection.** Tool-use selected over structured JSON and planner-style. Rationale: mechanical Guardrail 2 enforcement at emission boundary; per-operation observability for ledger/eval/replay; decomposed reasoning maps to substrate orchestration phases; incremental correction capability; planner autonomy misaligned with substrate-3's constrained-interpretation-engine mission.

**Substrate-3 as constrained semantic orchestration runtime.** Substrate-3 responsibilities:

- *Orchestration engine* — coordinates reasoning phase pipeline (interpretation → grounding → governance per D-077).
- *Governance engine* — enforces three Guardrails at both schema and semantic levels (§6.4).
- *Admissibility engine* — derives admissibility from S1 + substrate-2 taxonomy + Layer 1/2 discipline (D-083 e); LLM does not author admissibility.
- *Decomposition controller* — enforces canonical-negative-per-failure-mode + highest-specificity + bounded enumeration (D-083 d).
- *Replay controller* — computes identity_hash and explanation_hash over semantic substance (§6.6).
- *Refusal router* — categorizes refusal causes across 8 typed kinds in 3 categories.

**LLM as bounded cognition provider.** LLM responsibilities:

- Semantic intent interpretation — what the requirement implies.
- Selection judgment — when substrate presents multiple admissibly-grounded canonical options.
- Outcome emission — final structured emission.

The LLM does not orchestrate, does not author admissibility, does not categorize dismissals, does not select among refusal kinds. Those are substrate-locus responsibilities.

### 6.3 Tool surface (D-086)

Three thin semantic primitives:

**`propose_semantic_intent(requirement_excerpt, intent_descriptor)`** — LLM proposes what the requirement implies semantically. `requirement_excerpt` is the Guardrail 3 anchor (mandatory). `intent_descriptor` carries typed hints: archetype_hint, target_subject_hint, polarity_hint, failure_mode_framing, claim_kind_hint. Substrate processes by deriving candidates from intent, computing admissibility, recording dismissals — all substrate-internal.

**`select_canonical(candidate_refs, selection_rationale)`** — LLM selects canonical when substrate presents multiple admissibly-grounded candidates per failure mode. `selection_rationale` carries typed rationale_kind (highest_specificity | only_admissible | other_substrate_authorized) and dismissed_alternatives_with_reason (D-076 enum). Auto-skipped when only one admissibly-grounded candidate exists.

**`emit_outcome(outcome_kind, payload)`** — LLM emits final structured outcome per D-072. `outcome_kind` is draft or refusal. Draft payload: claim ref + recipe ref + admissibility_layer (substrate-authored, LLM transcribes). Refusal payload: refusal_kind (D-073 enum, 8 values) + refusal_payload (per-kind typed schema).

**Substrate-side orchestration internal flow.** Per-request lifecycle (not exposed as tools): request receipt → per-requirement orchestration loop (intent solicitation → candidate derivation → admissibility computation → dismissal recording → canonical selection negotiation) → outcome composition → outcome emission solicitation → ledger writes. Candidate derivation, admissibility evaluation, dismissal recording, canonical auto-selection — all substrate-internal. Free to evolve per Themes 6/7 calibration without changing the LLM contract.

**Substrate is the admissibility authority.** `admissibility_layer` is substrate-authored. The LLM never has a tool parameter where it asserts a candidate's admissibility_layer; the substrate computes it. The LLM proposes semantic intent and selects among presented options; the substrate determines what counts as grounding, what layer applies, and which candidates are admissible.

### 6.4 Two-layer Guardrail enforcement (D-087)

Per D-087 (a): typed schemas constrain vocabulary, structure, and references — but do not constrain semantic misuse, shallow grounding, misleading decomposition, or weak requirement anchoring. Schemas are necessary but not sufficient. Guardrail enforcement is two-layered:

**Layer A — Tool-boundary schema validation (necessary).** Validates at emission boundary: substrate-authorized vocabulary at enum positions; structural well-formedness; Guardrail 3 syntactic precondition (requirement_excerpt presence); S1 entity ref existence at current s1_version_seq. Layer A violations are *operational* — they route to substrate-side typed-feedback correction within the same generation or, on persistent violation, to `structural-validation-failure` refusal.

**Layer B — Substrate-side semantic governance validation (sufficient).** Validates during substrate orchestration: Guardrail 1 substantive enforcement (archetype × claim_kind semantically meaningful for referenced subjects); Guardrail 2 substantive enforcement (substrate-3 reasoning artifacts semantically appropriate, not just structurally valid); Guardrail 3 substantive enforcement (requirement_excerpt substantively supports the proposed intent); bounded decomposition substantive enforcement (canonical selection respects highest-specificity discipline); admissibility substantive enforcement (Layer 1 vs Layer 2 assignment respects semantic meaning).

Layer B violations are *semantic findings* — they route to substrate-orchestrated dismissals (recorded in `attempted_interpretation`) or to typed refusals (D-073 taxonomy).

Both layers are required.

### 6.5 Operational telemetry vs semantic provenance (D-087)

Per D-087 (b): clean separation between operational telemetry and semantic provenance.

**`llm_calls` (operational telemetry).** Substrate-3-adjacent table per D-074. Schema: call_id, generation_outcome_id, tool_name, raw_parameters, raw_response, operational_outcome (success | transient_failure | operational_error | rejected_for_correction), attempt_index, timing_start, timing_duration_ms, token_count_input, token_count_output, model_identifier. Used for cost analysis, latency monitoring, error tracking, operational debugging, per-model performance comparison. NOT used for replay determinism, semantic eval, transparency, or refusal analysis.

**`attempted_interpretation` (semantic provenance).** Part of `generation_outcomes` (semantic ledger per D-074). Schema: candidate_paths (each with path_id, archetype, claim_kind, subject_refs, requirement_anchor, admissibility_status, admissibility_layer, dismissal_reason); selected_path_id; dismissed_alternatives_by_reason (D-076 reason → list of dismissed path_ids; bounded set). Used for replay determinism, semantic eval, transparency surfacing, refusal analysis.

Different tables; different code paths; different consumers. The "retires to substrate-2 provenance when get_provenance ships" disposition (per D-074) applies to `attempted_interpretation`, not to `llm_calls`.

### 6.6 Multi-turn statefulness and replay equivalence (D-088)

Per D-088 (a, b): multi-turn statefulness clarified; replay equivalence tightened to semantic substance.

**Rejected tool call categorization.** Schema violations, vocabulary violations, Layer A governance violations, and operational errors (timeout, rate limit) are *operational* — recorded in `llm_calls.operational_outcome = rejected_for_correction`; do not enter semantic provenance. Substrate-derived dismissals and Layer B governance findings are *semantic* — recorded in `attempted_interpretation`. Multi-turn statefulness operates over operational events; semantic identity remains deterministic given semantic_context + governance_context.

**Replay equivalence over semantic substance.** D-075's `explanation_hash` computed over semantic substance, not operational trace:

- *In scope (semantic substance):* set of admissibly-grounded candidates per failure mode (unordered); canonical selection; dismissed_alternatives indexed by dismissal_reason category (category distribution, not specific sequence); admissibility_layer per artifact; outcome kind and outcome payload semantics.
- *Out of scope (operational trace):* ordering of LLM tool calls; specific tokens in intermediate responses; number of Layer A corrections; LLM model identifier; specific timing.

D-071 equivalence algebra: same semantic_context + same governance_context + different operational_context → expected identity_hash + explanation_hash match. "Match" defined over semantic substance. Operational trace variation is permitted and expected; explanation-hash drift events (per D-075) fire on semantic substance divergence only.

This refinement is a tightening of D-075's commitment, not a reversal. Theme 5 specifies the computation rule; D-075 committed to the existence of the hash; D-071 committed to the algebra.

### 6.7 Eighth refusal kind: `operational-budget-exhausted` (D-088)

Per D-088 (c): budget exhaustion is operational incompletion, not structural invalidity. Theme 5 introduces the eighth refusal kind in a new category.

**Updated refusal taxonomy at Theme 5 close (8 kinds across 3 categories):**

| RefusalKind | Category | Origin |
|---|---|---|
| `underspecified-requirement` | invalidity (input) | Theme 1 |
| `no-relevant-context` | invalidity (grounding) | Theme 1 |
| `ambiguous-reference` | invalidity (resolution) | Theme 1 |
| `ungrounded-claim` | invalidity (admissibility) | Theme 1 |
| `structural-validation-failure` | invalidity (output) | Theme 1 |
| `low-generation-confidence` | policy (threshold) | Theme 2 (D-073) |
| `no-admissible-negative-scenario-found` | policy (scope) | Theme 4 (D-083) |
| `operational-budget-exhausted` | operational (incompletion) | Theme 5 (D-088) |

The third category axis (operational) is new. Substantive distinction: invalidity refusals are about content/structure quality; policy refusals are about substrate-deliberate restraint; operational refusals are about substrate-runtime-resource constraints. All three are genuine refusal causes that downstream consumers should distinguish.

**Feedback payload:**

```
operational-budget-exhausted: {
  budget_dimension:            enum (token | time | tool_call_count)
  budget_limit:                typed numeric
  budget_consumed:             typed numeric
  partial_state_at_exhaustion: {
    candidates_proposed:           count
    candidates_admissibly_grounded: count
    canonicals_selected:           count
    requirements_resolved:         count
    requirements_unresolved:       count
  }
  recommended_budget_increase: optional typed numeric
}
```

The `partial_state_at_exhaustion` preserves semantic substance up to the exhaustion point. Replay equivalence applies: replaying with the same budgets should produce equivalent partial state.

### 6.8 Forward-compat reservations

Theme 5 carries the following reservations to downstream themes and future substrate-3 cycles:

- *Per-archetype LLM model routing.* Theme 5 commits that per-archetype model variation is operational, not architectural. The substrate's typed tool surface accepts any model conforming to the tool-use API contract. Theme 6 (prompt management) and Theme 7 (quality envelope) calibrate which models perform best per archetype through `operational_context.llm_model_identifier`.
- *Tool surface evolution.* The three thin semantic primitives are stable durable. Future substrate-3 cycles may extend the surface (adding tools for new substrate operations) without breaking existing tools. Phase-shaped or reasoning-specific tools should remain substrate-internal orchestration.
- *Substrate orchestration evolution.* Candidate derivation algorithms, admissibility evaluation strategies, dismissal recording patterns, and canonical auto-selection logic are substrate-internal — free to evolve through Themes 6/7 calibration without changing the LLM contract.
- *Future operational refusal kinds.* V1 ships one operational refusal kind (`operational-budget-exhausted`). Future operational kinds (e.g., `operational-model-unavailable`, `operational-rate-limit-exhausted`) may be added if Theme 7 quality envelope identifies need. The third category axis is established.
- *explanation_hash computation tuning.* The semantic-substance computation rule may be tuned in Theme 7 quality envelope calibration as substrate-3 observes drift patterns in production. The semantic-substance principle is stable; the precise hash function may evolve.

---

## 7. Prompt management and evaluation

**Resolution.** Per D-089 through D-091 (Theme 6): substrate-3 commits to prompt management with bounded co-evolution, eval suite with two-invariant replay equivalence and drift-as-evolution framework, and single-model-per-batch LLM routing chosen by dominant archetype. Theme 6 introduces no new substrate-level architectural primitives — it operationalizes Themes 1–5 commitments with explicit acknowledgments that prompts are behavior-shaping policy-adjacent surfaces, that model selection is a behavior-shaping operational decision, and that semantic adjudication theory in ambiguous enterprise QA remains unresolved for v1.

### 7.1 What Theme 6 closes

Three D-entries lock prompt management, eval suite, and model routing:

- D-089: Prompt management architecture (versioning, composition, lifecycle); prompts as policy-adjacent surface within bounded governance; prompt-substrate-orchestration as bounded co-evolution.
- D-090: Eval suite architecture (four categories: correctness, quality, performance, drift); two-invariant replay equivalence (identity_hash for semantic continuity; explanation_hash for transparency continuity); drift evaluation framework distinguishing regression from healthy evolution; semantic adjudication theory acknowledged unresolved.
- D-091: Single-model-per-batch routing chosen by dominant archetype; model selection acknowledged as behavior-shaping operational decision; v1 model defaults per archetype; future per-archetype within-batch routing deferred.

Convergence emerged from a single round of TA pressure-test surfacing seven architectural issues. Round 2 integration accepted six convergence items as architectural refinements (separate semantic from transparency continuity; prompts as policy-adjacent; model routing changes semantic behavior; drift as healthy evolution; bounded co-evolution; single-model-per-batch) plus one acknowledged-but-unresolved (semantic adjudication theory). The TA explicitly signaled convergence — "Everything else is strong enough to lock."

### 7.2 Prompt management architecture (D-089)

**Versioning.** Sequential `prompt_template_version` per template file; storage in version-controlled repository at `substrate-3/prompts/`; immutable per version (required for replay determinism); forward-compat with rollback via prompt registry mapping version → content. Per D-071: prompts are operational_context, alongside `llm_model_identifier`, `retry_policy`, `budgets`.

**Composition.** Layered composition: base system prompt + per-archetype fragment (v1 ships data-behavior, configuration, permission; UI and integration deferred) + per-request context + feedback templates for multi-turn correction. `prompt_template_version` references the composed (base + active fragments) content.

**Lifecycle.** Authoring via PR; eval gate via D-090; deployment via merge-to-main; deprecation indefinite (old versions remain available for replay).

### 7.3 Prompts as policy-adjacent surface and bounded co-evolution (D-089)

Per D-089 (d) and (e). Prompts encode admissibility heuristics, refusal tendencies, decomposition preferences — behavior-shaping policy surfaces, not merely contextual guidance.

**Two-layer governance framing:**

- *Substrate governance authority (governance_context, Layer B enforcement).* The substrate enforces what behaviors are *permitted*.
- *Prompts within bounded space (operational_context).* Within the substrate-permitted behavior space, prompts influence what behaviors the LLM *tends toward*.

**Operational discipline:** prompt review includes governance-implications check; major prompt fragment changes treated like architectural changes (eval-gated; Theme 7 re-validated); minor prompt tuning treated like operational changes; prompt-driven behavior shifts documented in EVOLUTION.md.

**Bounded co-evolution.** Prompts and substrate orchestration inevitably co-evolve. The contract is not fully decoupled independent evolution but bounded co-evolution:

- Each side has its own design cycle.
- Changes on either side may require co-evolution on the other.
- Major orchestration changes trigger prompt re-validation; major prompt changes trigger orchestration eval.
- Migration costs explicitly acknowledged.
- Replay corpus shifts during co-evolution; drift signals during planned co-evolution are expected, not regressions.

### 7.4 Eval suite architecture (D-090)

Four categories:

**Correctness evals (structural compliance).** Per-Guardrail Layer A acceptance rate per D-087: by tool (propose_semantic_intent | select_canonical | emit_outcome); by Guardrail (1: S1 entity refs; 2: vocabulary; 3: requirement_excerpt); Layer A correction loops per generation.

**Quality evals (semantic appropriateness).** Layer B substantive correctness rate by Guardrail; per-archetype emission quality; per-claim-kind admissibility precision; per-refusal-kind appropriateness (cause distribution within `no-admissible-negative-scenario-found` per D-083 b); per-archetype Layer 1 vs Layer 2 distribution.

**Performance evals (operational).** From `llm_calls` operational telemetry per D-087 b: cost per generation by archetype; latency per generation by archetype; per-model performance comparison per D-091; budget exhaustion frequency.

**Drift evals (replay).** From semantic ledger. See §7.5 and §7.6 for two-invariant framework and drift judgment discipline.

**Ground truth strategy (D-090 d).** Three sources: curated test corpus (v1 target: 200–500 cases); pilot customer feedback; replay corpus. V1 ground truth quality limits acknowledged: curated corpus small; pilot feedback just starting; replay corpus only meaningful after 3–6 months accumulation. V1 eval rigor calibrated to these limits.

**Eval cadence (D-090 e).** Pre-commit / CI runs subset (correctness fully; quality against curated; performance smoke); pre-release runs full suite with pass thresholds enforced; continuous production samples drift evals; post-pilot feedback processed weekly.

### 7.5 Two-invariant replay equivalence (D-090)

Per round 2 TA integration (item 1): replay equivalence is two invariants, treated distinctly:

- *`identity_hash` (semantic continuity).* Per D-071 + D-088. Same outcome — same emitted claim, same recipe, same outcome_kind, same refusal_kind + payload semantics if refusal. **Strict invariant.** Drift indicates semantic regression; presumption of regression unless explained.
- *`explanation_hash` (transparency continuity).* Per D-075 + D-088. Same `attempted_interpretation` semantic substance — same candidate set, same canonical selection, same `dismissed_alternatives_by_reason` category distribution, same admissibility_layer per artifact. **Weaker invariant.** Drift indicates reasoning trajectory varied; emitted output may still be correct.

Per-archetype drift thresholds calibrated separately by Theme 7 per invariant. identity_hash thresholds tight; explanation_hash thresholds looser and contextualized.

This refines D-088's drift semantics. D-088 committed semantic-substance computation for both hashes; Theme 6 separates their downstream treatment in eval.

### 7.6 Drift as evolution framework (D-090)

Per round 2 TA integration (item 5): drift signals trigger investigation, never auto-failure.

**Drift judgment criteria:**

- *identity_hash drift* — sharp regression signal. Investigated. If output substantively wrong → regression. If output substantively better (sharper refusal, more specific grounding, narrower admissibility) → accepted improvement.
- *explanation_hash drift with identity_hash stable* — weaker signal. Investigated; presumption shifts toward explanation refinement or healthy evolution. Did the substrate get sharper (more targeted dismissal reasons, more specific candidate paths)? Likely healthy.
- *explanation_hash drift with identity_hash drift* — concurrent signals. Both surfaces investigated together.

Drift events surface in EVOLUTION.md annotated with substrate-3 maintainer judgment: `regression` | `evolution` | `neutral`. Patterns inform Theme 7 quality envelope calibration.

Cultural commitment: substrate-3 maintainers explicitly tasked with judging drift, not auto-treating drift as failure. Theme 6 establishes the framework; Theme 7 calibrates judgment thresholds per archetype.

### 7.7 Single-model-per-batch routing (D-091)

Per round 2 TA integration (items 3 and 7): model selection is behavior-shaping; single-model-per-batch in v1 chosen by dominant archetype.

**Model selection as behavior-shaping operational decision.** Different models differ in semantic temperament (refusal aggressiveness, decomposition behavior, grounding conservatism, ambiguity handling). Model identifier is operational_context per D-071; selection affects substrate-observable behavior; Theme 7 quality envelope calibrates per-model behavior expectations.

**Single-model-per-batch.** All requirements in one `GenerationRequest` use the same `operational_context.llm_model_identifier`. Determined by dominant archetype; mixed-archetype batches default to Claude Opus 4.7. Per-customer override preserved.

**V1 model defaults per archetype:**

- Pure data-behavior → Claude Opus 4.7 (complex multi-constraint reasoning).
- Pure configuration → Claude Sonnet 4.7 (S1-lookup-dominant).
- Pure permission → Claude Opus 4.7 (recipe-kind discipline requires sharp judgment).
- Pure UI → Claude Sonnet 4.7 (narrow scope at v1).
- Pure integration → Claude Opus 4.7 (interaction complexity).
- Mixed-archetype → Claude Opus 4.7 (default to capability).

Calibrated by D-090 eval framework on curated corpus before v1 ships. Defaults are operational; refined as production data accumulates.

**Future per-archetype within-batch routing deferred.** V1 commits single-model-per-batch. Post-v1, finer per-archetype routing within batches may be revisited when per-archetype × model behavioral profiles are well-characterized via production data and cross-model coherence within batches can be validated empirically.

### 7.8 Forward-compat reservations

Theme 6 carries the following reservations:

- *Semantic adjudication theory in ambiguous enterprise QA.* Eval framework at v1 measures adherence to substrate-3's architectural commitments rather than canonical semantic correctness. Resolution of semantic validity-as-space (vs validity-as-point) requires production data, longitudinal study, and likely formal work beyond v1 scope. Theme 7 may begin addressing; full resolution belongs to a future substrate-3 cycle.
- *Per-archetype within-batch model routing.* V1 commits single-model-per-batch by dominant archetype. Post-v1 finer routing within batches may be enabled once per-archetype × model behavioral profiles are well-characterized and cross-model coherence within batches validated empirically.
- *UI and integration archetype prompt fragments.* V1 ships base + 3 archetype fragments (data-behavior, configuration, permission). UI and integration prompt fragments deferred to a future Theme 6 calibration cycle when these archetypes have larger production footprint.
- *Drift event judgment framework formalization.* V1 drift events annotated with `regression` | `evolution` | `neutral` by substrate-3 maintainers. Formalized judgment criteria (per-archetype, per-category) may emerge from Theme 7 quality envelope work as longitudinal data accumulates.

---

## 8. Quality envelope

**Resolution.** Per D-092 through D-094 (Theme 7): the quality envelope is a structured calibration framework that calibrates behavioral distributions, never architectural invariants; it is conceptually separated from the operational envelope and defined relative to each archetype's canonical routing profile. Thresholds are evolvable ranges bounded by architectural invariants with hardened evolution-adjudication safeguards. The admissibility-confidence threshold is recategorized as governance_context (semantic risk tolerance). Theme 7 introduces no new substrate-level architectural primitives — it calibrates the locked architecture and protects it from calibration-driven erosion. Theme 7 is the final Phase 1 theme; the architectural invariants it protects are enumerated in `SUBSTRATE_3_WORLDVIEW.md`.

### 8.1 What Theme 7 closes

Three D-entries plus the worldview artifact:

- D-092: Quality envelope framework — calibrates behavioral distributions not architectural invariants; quality envelope vs operational envelope; envelopes relative to canonical routing profile; structure-not-values; per-archetype dimensions and v1 provisional profiles; calibration mechanism and cadence.
- D-093: Threshold evolvability and evolution-adjudication governance — evolvability bounded by architectural invariants; rationalization safeguards; per-archetype drift judgment signatures; validity-space and replay determinism reconciled via reproducibility-versus-acceptability.
- D-094: Admissibility-confidence threshold as governance_context — semantic risk tolerance; quality envelope observes but does not own; resolves the Theme 4 reservation as governance.
- SUBSTRATE_3_WORLDVIEW.md: the canonical registry of governing principles, non-goals, architectural invariants, semantic boundaries, and substrate responsibilities.

Convergence emerged from a single round of TA pressure-test surfacing seven tightening items, all accepted in round 2 integration. Three were load-bearing reshapes: calibration-vs-invariants (item 1); evolution-adjudication governance (item 2); admissibility-confidence recategorized to governance_context (item 3, correcting the opening lean). The TA signaled convergence — "Everything else is strong enough to lock Phase 1."

### 8.2 The quality envelope calibrates behavioral distributions, not architectural invariants (D-092)

Per D-092 (a). Not all dimensions are equally calibratable.

**Architectural invariants (substrate law — NOT envelope surfaces, NOT calibratable):** identity_hash semantic continuity (D-090 b); Guardrail Layer A validity (D-087); refusal transparency presence (D-073); grounding requirements (Guardrail 1, D-070); the three Guardrails, eight-refusal-kind taxonomy, three-context separation, two-layer enforcement, substrate-as-admissibility-authority. Enumerated in SUBSTRATE_3_WORLDVIEW.md. The quality envelope observes invariant compliance (a breach is a bug surfaced by eval) but never tunes invariants.

**Calibratable surfaces (behavioral distributions — quality envelope dimensions):** refusal-rate distribution by semantic category (invalidity + policy); Layer 1 / Layer 2 admissibility distribution; explanation_hash drift threshold (contextual).

Commitment: the quality envelope calibrates behavioral distributions, not architectural invariants. This protects later teams from "tuning" non-negotiable substrate commitments.

### 8.3 Quality envelope vs operational envelope (D-092)

Per D-092 (b). Budget exhaustion is operational incompletion (D-088 operational refusal category), not semantic quality; tracking it as semantic behavior would let tighter budgets falsely read as worse semantic behavior.

- **Quality envelope (semantic):** refusal-rate by semantic category (invalidity + policy); Layer 1 / Layer 2 distribution; explanation_hash drift threshold.
- **Operational envelope (operational):** cost per generation; latency per generation; budget caps (token / time / tool_call_count); `operational-budget-exhausted` rate.

Maps onto D-088's three-category refusal axis: invalidity + policy are semantic (quality envelope); operational is operational (operational envelope). Calibrated independently; the drift-as-evolution framework and evolution-adjudication governance (§8.5) apply to the quality envelope only.

### 8.4 Per-archetype dimensions and v1 profiles (D-092)

**Canonical routing relativity.** Each per-archetype envelope is defined relative to the archetype's canonical routing profile (default model per D-091). Non-canonical model usage produces out-of-scope distributions; per-archetype×model envelopes deferred.

**Structure-not-values.** The envelope is structure (dimensions, derivation, evolution discipline), not fixed gates — v1 has no production data and substrate behavior is expected to improve.

**v1 expected shapes (provisional), from Theme 3 design intent:** data-behavior (refusal-dominant; significant Layer 1 share); configuration (lowest refusal rate; S1-direct); permission (moderate; run-as-disambiguation refusals); UI (higher refusal, honest not regression); integration (higher refusal; operational-only admissibility).

**Calibration cadence:** v1 heuristic initial values → pilot-phase refinement → production calibration (replay corpus, 3–6 months) → ongoing evolution (per §8.5).

### 8.5 Threshold evolvability and evolution-adjudication governance (D-093)

Per D-093 (a–c). Thresholds are expected ranges, not fixed gates (per D-090 c drift-as-evolution). A breach triggers investigation, adjudicated `regression` | `evolution` | `neutral`.

**Bounded by invariants (item 2).** The architectural invariants (§8.2; SUBSTRATE_3_WORLDVIEW.md) are the floor. A drift breaching an invariant is regression by definition — not subject to maintainer judgment. Evolution adjudication must preserve substrate-level architectural commitments even when behavioral distributions shift.

**Safeguards:** recorded rationale tied to a per-archetype evolution signature (not "looks better"); asymmetric scrutiny on lower-refusal shifts (presumption of regression, per fail-loud philosophy); periodic architectural-invariant audit against cumulative shifts; design-cycle-weight review.

**Per-archetype drift judgment signatures:** documented evolution signatures (e.g., data-behavior shift toward Layer 2 as the formula parser ships), regression signatures (e.g., configuration refusal rate rising on clean cases), neutral signatures (environmental change). Full automated formalization remains future.

### 8.6 Validity-space and replay determinism (D-093)

Per D-093 (d). The per-archetype distribution framing is a validity-space framing (validity as space, not point), which is in natural tension with replay determinism.

- **Semantic reproducibility** (replay determinism, identity_hash): an engineering property — same substrate version + same semantic_context + same governance_context yields same emitted output. The substrate deterministically selects one point.
- **Semantic acceptability** (validity-space): a semantic property — which outputs would be correct. Often a space.

V1 resolution: the substrate is deterministic (reproducible) within a wider acceptability space — it picks one point and reproduces that pick. Reproducibility does not require the pick be the only acceptable point. When the substrate should legitimately vary within the validity-space requires the unresolved semantic adjudication theory (D-090 f); v1 commits to reproducibility. The reproducibility-versus-acceptability distinction is the handle for that future work.

### 8.7 Admissibility-confidence threshold as governance_context (D-094)

Per D-094. The admissibility-confidence threshold (governing the `policy_restraint` cause per D-083 b) directly determines what the substrate is willing to assert as truth — semantic risk tolerance, which is governance.

- **Recategorization (item 3):** governance_context per D-071, not operational_context. Same candidate + grounding + topology, different threshold yields different refusal — semantic admissibility policy, not generation quality. Categorically different from D-089 prompts and D-091 model routing (both operational, behavior-shaping diffusely).
- **Semantic risk tolerance:** substrate-authored conservative default (fail-loud philosophy); per-customer governance override (risk-averse customer sets higher threshold).
- **Quality envelope observes, does not own:** the envelope measures the resulting policy-refusal distribution; governance owns the threshold.
- **Replay confirms it:** changing governance_context is expected to change identity_hash (D-088). An operational parameter changing identity_hash would contradict D-088; a governance parameter changing it is correct. Resolves the Theme 4 reservation as governance.

### 8.8 Forward-compat reservations

- **Validity-space vs replay-determinism reconciliation.** V1 commits to reproducibility within a wider acceptability space. Principled non-determinism within the validity-space requires the unresolved semantic adjudication theory (D-090 f); carried via the reproducibility-versus-acceptability distinction.
- **Per-archetype×model quality envelopes.** V1 envelopes are relative to the canonical routing profile. Per-archetype×model envelopes deferred until per-model behavioral profiles are well-characterized via production data.
- **Automated theory of semantic improvement.** V1 ships maintainer-judged evolution adjudication bounded by invariants with documented safeguards. A mechanical theory of semantic improvement remains future.

---

## 9. Realized State (Phase 2)

**Resolution.** Per D-095 through D-106: Phase 2 implemented the architecture §§2–8 lock. This section is the authoritative record of **what is actually built**; §§2–8 remain the design-era record. The realized surface is deliberately a thin vertical — two emittable claim kinds carried end to end — proving the spine, governance, emission, persistence, eval, routing, and runner against real S1/S2, with the remainder of the designed 16-kind × 5-archetype surface deferred and catalogued in `DEFERRED_ITEMS.md`.

### 9.1 Emittable surface — the C-debut (2 of 16 claim kinds)

The debut flipped from the data-behavior value-claim originally sketched (D-097.1) to **configuration metadata-relationship-claim** (D-098), the cleanest Layer-1-complete grounding. Two `(archetype, claim_kind)` pairs are emittable:

- **`configuration` / `metadata-relationship-claim`** (D-098) — emission authored from a verified Tier-1 edge; verified draft, Layer-1-complete, no caveat (D-099).
- **`data_behavior` / `prohibition-claim`** (negative polarity, D-101) — the first **caveated draft**: Layer-1-plausible (a ValidationRule `APPLIES_TO` the subject exists; its formula is unparsed → typed caveat).

The emittable set is the single source of truth in `emission.EMITTABLE`, gated at resolution (D-105). The other 14 designed kinds (value-claim positives, state-transition, automation-effect, configuration existence/property, all permission/UI/integration kinds) are deferred per `DEFERRED_ITEMS.md`.

### 9.2 Outcome spectrum and the realized refusal taxonomy (9 kinds)

Three outcome types ship: **verified draft** (config; Layer-1-complete; no caveat), **caveated draft** (data_behavior negative; Layer-1-plausible; typed `caveat_kind = deeper_verification_layer_unparsed`, persisted as emission-time posture per D-101.3), and **refusal**.

The refusal taxonomy is realized at **nine kinds across three categories** — this supersedes the theme-close 6/7/8 snapshots in §3.4, §5.3, §6.7:

- **Invalidity (5):** `underspecified-requirement`, `no-relevant-context`, `ambiguous-reference`, `ungrounded-claim`, `structural-validation-failure` (D-073).
- **Policy (2):** `low-generation-confidence` (D-073), `no-admissible-negative-scenario-found` (D-083).
- **Operational (2):** `operational-budget-exhausted` (D-088), **`emission-deferred` (D-105)**.

`emission-deferred` is the runtime face of D-097.6: a grounded-but-unbuilt claim_kind refuses gracefully (an honest capability boundary that lifts as kinds land) rather than crashing `finalize_outcome`.

### 9.3 Recipe and trigger taxonomy — `inspection-trigger` (6th kind)

Both emittable shapes author an **inspection recipe** (`metadata_read` + assert-edge-exists). Realizing this reopened substrate-2's trigger taxonomy from five to **six kinds** to add **`inspection-trigger`** (D-099, reopening D-055's lock): the invariant-inspective (no-causal-event) verification mode. Migration `20260522_0010` adds it to the `trigger_kind` enum.

### 9.4 Governance spine and the emittability gate

- **Spine:** `resolve_intent` (data_behavior path + `_resolve_configuration`), `finalize_outcome`, `accept_selection`, `route_refusal`; reasoning phases interpretation → grounding → governance (D-077d).
- **Seam:** `GovernanceCore` over `SemanticOrgModel` — real S1 grounding via single-hop neighborhood walk (D-096.1).
- **Emittability gate (D-105):** resolution gates `PROCEED_TO_EMIT` to `emission.EMITTABLE`; `finalize_outcome` carries a graceful refuse-not-crash backstop. A drift-guard binds `EMITTABLE` to `author_emission`'s dispatch.
- **Layer B is a reject-only sanity floor** (excerpt-anchoring length, D-096.3) — full semantic-support verification is deferred (gated on the same end-state as Layer 2).
- **Multi-candidate flow is dormant:** the ≥2-grounded → `select_canonical` → `accept_selection` path is built and `accept_selection` is implemented + tested, but the `DecompositionController` emits one canonical candidate per failure mode in v1 (D-086 / D-095.3), so the selection turn never fires.

### 9.5 Prompt registry and eval

- **Prompt registry (D-103, realizing D-089 slice 1):** per-version frozen composed prompts (base + the three shipped archetype fragments) with a content-hash guard; `CURRENT = "generation@v1"`. Prompt version threads onto `llm_calls.prompt_version`.
- **Deterministic eval core (D-102, realizing D-090):** scripted tool-turns bypass the LLM to gate correctness; two-invariant replay (identity_hash strict / explanation_hash weaker); drift-as-evolution (never auto-fail).
- **Live ontology-coherence gate (D-104, D-089 slice 2):** real-gateway probes adjudicated against per-probe semantic envelopes (invariant = auto-fail / acceptable-variant = drift / benign = ignored). Periodic and API-key-gated — **not a PR-gate**.

### 9.6 Routing and the production runner

- **`route_model` (D-091 realized via D-106.2):** pure; precedence explicit pin → tenant `always_use_opus` → archetype table → Opus (default-to-capability). Bound once per batch as `model_override` (not the gateway `_CHAINS`). Realized model constants: `router.OPUS = "claude-opus-4-7"`, `router.SONNET = "claude-sonnet-4-5-20250929"` (the §7.7 "Sonnet 4.7" design label predates the pinned id). The §7.7 archetype→model table is realized as designed (configuration / ui → Sonnet; data_behavior / permission / integration / mixed → Opus).
- **`run_generation` (D-106.1):** the in-process production entry point — route → bind the routed `gateway.tool_turn` closure (or a test seam) → `GovernanceCore` over a tenant connection → `GenerationRuntime` with `LedgerPersister`. Abort-on-error; per-requirement-committed isolation (D-096.6). The HTTP/worker layer that wraps it (Jira → request intake, auth, async queue, retry/idempotency) is deferred (D-106.4).

### 9.7 Persistence and migrations

- **`LedgerPersister`:** per-requirement transaction (D-096.6); atomic claim + recipe + ledger write in one tenant-bound Session (D-097.4 / D-099).
- **Ledger schema (D-074):** `generation_requests` / `generation_outcomes` (semantic ledger) + `llm_calls` (operational telemetry, S3-adjacent).
- **Migrations `20260520_0010` → `20260522_0040`:** ledger creation; caveat persistence (`caveat_required` + `caveat_kind`, D-101.3); `llm_calls.prompt_version` (D-103.5); `emission-deferred` added to `refusal_kind` (D-105). Plus substrate-2 `20260522_0010` for `inspection-trigger`.

### 9.8 What §9 supersedes in §§2–8

- The realized refusal taxonomy is **9 kinds**, not the 6/7/8 theme-close snapshots (§3.4, §5.3, §6.7).
- The v1 emittable debut is **configuration metadata-relationship + data_behavior prohibition** (D-098), not the data-behavior-first framing of §2.8 / §4.3.

Everything else in §§2–8 stands as designed; the gap between the designed surface and the realized surface is catalogued in `DEFERRED_ITEMS.md`.

---

## Status

**Phase 1 (design) complete** — Themes 1–7, D-070–D-094 (§§2–8).
**Phase 2 (implementation) complete** — D-095–D-106; realized state in §9; build arc in `EVOLUTION.md` (2026-05-24).
