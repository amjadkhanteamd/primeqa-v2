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

## 3. Generation request shape

Reserved for Theme 2.

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
