# Substrate 4 — Execution Engine — Evolution Log

Append-only. One entry per session that made substantive changes to this substrate's docs or code.

---

## 2026-05-26 — Substrate opened; foundational design (D-108)

Substrate 4 opened — the execution engine: it takes the recipes S2 represents (and S3 generates), runs them against a real Salesforce org, and captures what actually happened as durable evidence. The substrate boundary is sharp: **S4 captures truth — including the grounded outcome — but does not interpret it** (classification / root-cause / explanation / clustering is S6). The v1 mistake was not *rendering* an outcome but rendering an **ungrounded** one (the `expect_fail` flag-flip); S4's outcome is grounded — it verifies the specific S3 claim the recipe operationalizes.

`SPEC.md` written (§Purpose → §5). D-108 committed: **F1** (v1 reuse boundary — reuse the mechanical primitives at the layer *beneath* `execute_step`, own orchestration + outcome + result model), **F2** (evidence-first, S4-owned result model; schema deliberately not locked), **F3** (first vertical = metadata-inspection) locked, TA-reviewed; **F4–F7** triaged (defer ui / event / callout; minimal capability-match; no test-data until CRUD; S4 captures failure-truth, does not remediate).

**The grounded-outcome / interpretation boundary** was sharpened in review: S4 *does* render a grounded run outcome (`passed`/`failed`/`errored`/`skipped`) — capturing it is not interpretation; attributing *why* it came out that way is (S6). The first-vertical grounding surfaced two refinements (an edge→live-read translator; the posture vocabulary is two orthogonal layers) and one win (the S2 posture surface `report_run_outcome` / `select_recipe_for_execution` already exists).

## 2026-05-27 — First vertical built, merged, live-proven (D-108.1 → D-108.4)

The metadata-inspection vertical built across four design+impl slices + the run path, each design-then-impl, each HOLD-and-shown, then merged to `main` (`46839b4`) and **proven live against the Salesforce sandbox**.

**Slice 1 — recipe→plan bridge (D-108.1 framing).** `build_metadata_inspection_plan(RecipeRead) → MetadataInspectionPlan`: narrow + validate + project an S2 recipe into a semantic, S1-edge-vocabulary plan (ordered read + assert over a `LogicalRef`). Established the **S4↔S2 read-through boundary** — S4 consumes S2's typed `RecipeRead` via the Coordinator (`select_recipe_for_execution`), never re-decoding raw JSONB.

**Slice 2 — the executor (D-108.1).** Translator (edge→SOQL, `APPLIES_TO`) + thin S4-local Tooling client (auth read + pagination + typed errors, reusing the neutral `integrations/` exceptions) + credential resolution (`resolve_tooling_client` via the D-106.4 `_oauth_token` path) + `exists` evaluation → grounded run outcome + in-memory evidence. Decision 1 = (a): an **S4-local thin client** (reject the S4→v1 inversion; defer the neutral lift). The **operational-realization principle** locked: edge→SOQL mappings are operational realization, **not semantic authority** — the query reflects only what the recipe asserts, so the `APPLIES_TO` translation carries **no `Active` filter** (the recipe asserts plain `exists`; active-ness parked as S4-Q-001, S3-owned). Decision 2 (scoped query) **live-verified** against the sandbox — no bulk fallback built.

**Slice 3 — the result store (D-108.2).** Schema **A** (run-entity typed identity/outcome columns + an extensible `evidence` JSONB captured-trace), per-tenant `s4_execution_runs` (tenant-branch migration; `outcome` reuses the existing `run_outcome` enum, verified to match slice 4's `report_run_outcome` exactly). The run is an entity (queryable columns); JSONB holds only the raw observation — the no-JSONB-blob rule targets the semantic store, and execution truth is not semantic data. The A→B promotion (per-step child table) is recorded with its trigger, reversible. The executor stays produce-only; a separate persister writes — the slice-2 no-DB unit-test boundary held.

**Slice 4 — the finalize step (D-108.3).** `finalize_run(session, evidence, *, coordinator=None)`: persist the evidence then `report_run_outcome(actor='s4', …)` on the same session — atomic (both flush, the caller owns the commit). Completes the **S4→S2 write boundary** (the read side was slice 1). The **idempotency model is two layers**: persist is fail-loud on a duplicate `run_id` (PK — runs are never silently duplicated, each mints a fresh `run_id`); the no-op idempotency is `report_run_outcome`'s first-write-wins on `last_run_id` (a posture-only retry). The loose "re-finalize → no-op" shorthand was corrected to this two-layer model in both the SPEC ledger and the code docstring.

**Run path (D-108.4).** `run_recipe_execution(session, …) → RunPathResult` chains select → bridge → resolve-client → execute → finalize; `run_recipe_execution_for_tenant(tenant_id, …)` owns the `get_tenant_connection` context + the single commit. **Transaction boundary A** (one tenant-scoped session/transaction across the whole path, the `LedgerPersister` idiom) — one session spans both data domains because `search_path = "tenant_<id>", public` (per-tenant S2/S4 tables + v1 `environments`/`connections`). A holds the DB transaction across the bounded live read — sync-path-acceptable; the async restructure (brief-transaction bracketing) is deferred. `RunPathResult` distinguishes **ran** from **no-eligible-recipe**. The S2 Coordinator now has three production callers: `LedgerPersister` (write), `finalize.py` (write), `run.py` (read). The **inject-client** parameter is necessitated by a schema gap (the local substrate test DB has no `environments`/`connections`), so the whole-spine live test injects a real `ToolingReadClient`.

**Live proof.** The whole spine ran end-to-end against the real sandbox (`select → bridge → execute-LIVE → finalize`), discriminating real org state: Opportunity → `passed` (3 VRs), Account / Lead → `failed` (0 VRs) — grounded outcomes, no interpretation, both result rows persisted, posture reported.

Merged `s4-execution` → `main` (`--no-ff`, `46839b4`); gate 1317 passed (sandbox/live opt-in); branch safe-deleted.

## 2026-05-27 — First-vertical phase close-out

SPEC §Status flipped from "Phase 0 opening" to the realized first vertical (F1/F2/F3 realized; F4–F7 status enumerated). `DEFERRED_ITEMS.md` + `EVOLUTION.md` created (this file). `OPEN_QUESTIONS.md` S4-Q-001 (active-ness, S3-owned) carried forward.

## 2026-05-27 — CRUD phase opened (PR-based, forks open)

The second S4 vertical — CRUD / `data-recipe` (data mutation) — opened on the feature branch `phase-5-substrate-4-crud`, built **PR-based** per the CONVENTIONS working agreement (substrate work → feature branch → merge to main via PR; the inspection vertical's direct local merge was the deviation). D-109 records the landscape grounding (read-only): the phase is **cross-substrate** (unlike inspection's S4-only) — it needs S2 (recipe-model expect-rejection, for the negative), S3 (data-recipe emission — none today), and S4 (data executor + provisioning + cleanup + result-model extension). Five forks are **open** (polarity, cross-substrate sequencing, provisioning/cleanup lift, data-client lift, result-model extension) — leans noted, resolved next into the PR. No code yet.

## 2026-05-27 — Second vertical realized: behavioral negative (CRUD / data-recipe), live-proven (D-110.1 → D-110.3)

The cross-substrate CRUD programme — the behavioral negative (a create the org rejects) — built across S2 → S4 → S3 on `phase-5-substrate-4-crud` (PR #5, not yet merged), each step design-then-impl, HOLD-and-shown.

**S2 (D-110.1).** `RejectionExpectation` (operational, scalar-only — the projection of the claim's identity-bearing `RejectionSignal`) + `CreateStep.expect_rejection` + the at-most-one invariant. Additive v1 (greenfield). The projection exists because operational bodies forbid `IdentityBearingRef` (`_verify_no_identity_bearing_refs`); reusing `RejectionSignal` would trip it.

**S4 (D-110.2).** Parallel-not-generalize: `build_data_recipe_plan` → `DataRecipePlan`/`PlannedCreate`; the thin `DataMutationClient` (build-thin, the `ToolingReadClient` precedent); `execute_data_recipe` — the grounded 4-way create-reject eval (the match-the-`error_code` step is what makes it grounded, strictly stronger than v1's bare `expect_fail` flip); `CreateAttemptEvidence` serialized via the **reused** result store + finalize (no migration); the run-path `recipe_kind` dispatch; N-5 minimal-cleanup (targeted best-effort delete on unexpected success). Mechanism spine proven live via a deterministic `REQUIRED_FIELD_MISSING` rejection.

**S3 (D-110.3, S3-thin).** `_author_negative` now emits the **behavioral** recipe for a *verified* negative — the violating create whose `field_values` are the D-107 parser's already-derived `violating_payload` (previously computed + discarded under Option C) + `expect_rejection` — replacing the inspection re-verify. Caveated negatives (non-derivable formula) stay inspection. The claim's `identity_hash` is **stable** across both (the payload lives in the recipe, never the claim — verified by a `compute_identity_hash` test).

**The live necessity experiment.** A violating-value-only create on a real managed-package VR (`Contract_is_Required`, `ISBLANK(...)`) returned HTTP 400 with **only `FIELD_CUSTOM_VALIDATION_EXCEPTION`** (no `REQUIRED_FIELD_MISSING`) — the object enforces required-ness via VRs, so the create trips the rule immediately. The full S3 → S2 → S4 spine computed **`passed`** (multi-error match), no record created. **S3-thin is the live differentiator; S3-A (required-field population) is deferred-not-needed** for VR-enforced prohibitions. Committed as a gated VR-rejection live test.

**Honest scope:** behavioral covers the parser's derivable-formula subset (verified); caveated stay inspection (widening = parser's future work). The platform-required short-circuit is inferred-not-proven (SF returns all errors → likely surfaces the VR alongside, still matches); confirmable via a standard-object VR. The derivable sandbox VRs are managed-package; a standard-object product-demo VR is sandbox-content.
