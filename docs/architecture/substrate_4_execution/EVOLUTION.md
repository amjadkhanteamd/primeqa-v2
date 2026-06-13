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

## 2026-06-01 — Slice 1 positive create-and-verify design (D-115)

The third S4 vertical opened: **positive create-and-verify, directly-set state** — the first *semantic-execution* slice (weight on constructing a valid operational world + policing the boundary, not the create call). Design-only this session (`SLICE_1_POSITIVE_CREATE_VERIFY_DESIGN.md` + DECISIONS_LOG D-115), on the new feature branch `phase-6-substrate-4-positive`.

**Governing boundary (k16, structural).** S4 resolves *operational* validity against the live org but never the recipe's *semantic* meaning: S4's writable set = (object's required fields) − (semantic fields), so S4 *structurally cannot choose the value under test*; and grounding compares observed vs. the claim's targets verbatim, so S4 cannot soften the verification. The value is requirement-sourced (threaded into both Create and Assert); scope-fenced to directly-set state (automation effects / async / entanglement / required-lookup parents / multi-step deferred — k8 / k15).

**Two-sided.** (A) **S3 emission** — `GroundedPositive` + `_author_positive`, built this session (D-115 slice 1 side A; see S3 EVOLUTION); `EMITTABLE += value-claim` + the governance grounding stash held (Option Q). (B) **S4 execution** — the spine to build next: construct the operational world (S1 requiredness → required-field padding) → create-expect-success → **observe as a distinct async-ready phase** → ground `field == V` → structured-trace evidence → teardown as execution-isolation (k14). Closes generate→run for a positive recipe end to end. The read-back resolution convention (`$create-record.id`) authored by side A is defined here, in side B.

## 2026-06-01 — Slice 1 side B built: positive execution spine, per-suite green (D-115 / D-115.2)

S4's positive create-and-verify **execution spine** is built on `phase-6-substrate-4-positive`, design-then-impl, HOLD-and-shown. It **closes generate → run** for a positive recipe — S4 now executes a genuinely S3-emitted positive recipe (proven end-to-end through `author_emission` → bridge → `execute_data_recipe`).

**The two seams the design left to side B, resolved (D-115.2).** Read-resolution = **SOQL substitution**: `refs.resolve_step_refs` rewrites `$create-record.id` → the live record id (fail-loud on an unresolved ref) and the authored SOQL runs **verbatim** via a new data `query()` (by-id retrieve rejected as vestigial). 400-rejection outcome = **disambiguate by offending field**: the **semantic** field named → `failed` (the value is not achievable — the headline finding); only S4's **padding** named / none → `errored` (S4's operational gap). The split is structural off the k16 set-split (the create carries the semantic field only).

**The spine.** `execute_data_recipe` dispatches on `expect_rejection` (present → the D-110.2 negative, unchanged; absent → the positive). The positive path: construct the operational world (`world.resolve_operational_padding` reads S1 `field_details` — required-on-create = `is_nillable=False`, minus calculated, minus lookups, minus the semantic field; type-valid filler + simple-picklist via the value set; any unfillable required field → `errored` *pre-create*) → create-expect-success → **observe as a distinct phase** (read-back; 0 rows → `errored`, *no immediate-consistency assumption*) → ground `field == V` verbatim → **teardown always (k14)** on any 2xx create. `DataReadEvidence` + reused `CreateAttemptEvidence` / `AssertEvidence` serialize through the **unchanged** persister + finalize — **no migration**.

**Wiring + scope.** The run-path data branch injects the S1 requiredness port (`SemanticOrgModel`) **only for the positive plan** (the negative never touches the connection). Verified per-suite: execution_engine **117** (78 prior unchanged + 39 new), generation 119, interpretation 20. **Held (not this slice):** the S3 governance grounding stash (synthesis→intent `{field, expected_value}` + `EMITTABLE += value-claim`) — real value-claims stay `EMISSION_DEFERRED` until it lands (Option Q).

## 2026-06-03 — Phase 3: execution breadth (existence/property) + the async production trigger (D-127 → D-133)

The generation→execution gap, closed for the pure-S4 set, plus the async-trigger foundation. Six slices on `phase-11-substrate-4-execution` (each design/impl), merged to `main` at close. The branch's global phase counter continues `phase-10-substrate-3-breadth`; conceptually this is the S4 phase.

**PART A — execution breadth (pure-S4).** Phase 2 made existence/property/capability/layout emittable + grounded + LLM-reachable, but the S4 translator had **one** edge (`APPLIES_TO`) and **one** predicate (`exists`) — so the new kinds raised `UnsupportedEdgeError`. A verified split drove the scope: **existence + property are self-contained in the recipe** (a self-read of the subject's own metadata; the value rides the predicate) → pure-S4; **capability + layout are under-specified** (the recipe carries one endpoint + the edge type; the second endpoint is env-detail prose) → deferred behind an S3 recipe-enrichment.
- **D-127 existence.** `translate_read` → a **read-shape dispatch**: edge-read (`_EDGE_TRANSLATORS`) vs self-read (`_SELF_READ_BUILDERS` keyed on `entity_type`: Object→`EntityDefinition`, Field→`FieldDefinition`, the qualified `Object.Field` split, reusing v1 sync's proven Tooling SOQL). `exists` already worked — existence ran immediately.
- **D-128 property.** Executor `+= equals / is_null` over a **captured column value** (threaded from the translator via `ToolingQuery.capture_column`, since the metadata assert's `subject_ref` carries no field; `_value_eq` coerces str/int; 0-rows + value-over-presence-only fail safe/loud). A **finite, honest** property→`FieldDefinition` map (`length`/`precision`/`scale`); `is_required` (no faithful column) + `field_type` (describe-vocab vs `DataType`) + the rest **fail-loud** (`UnsupportedPropertyError`) — the "never guesses" discipline.

**PART B — the async production trigger.** S4 executed when called but nothing fired runs; the sync path holds one DB transaction across the live read (Boundary A). Mirrors S3's proven queue/consumer/reaper pattern.
- **D-129 B0.** `run_recipe_execution_async` brackets the live read with **brief transactions** (select TX → execute holding **no DB connection** → persist+posture+interpret TX) via an injectable `session_scope`; the live-proven sync path is untouched (additive). Metadata-path only; a data recipe (reads S1 mid-execute) raises a clear deferral.
- **D-130 B1.** `s4_execution_jobs` (+ attempts) — **the phase's one migration** (`20260603_0010`). Idempotency is a **partial-unique on the active set** (`UNIQUE(test_id, environment_id) WHERE status active`): re-runnable after terminal (execution is repeatable), unlike S3's full-unique "one job ever". `ExecutionJobStore` mirrors `GenerationJobStore` (SKIP-LOCKED claim, fresh-request_id attempts, race-safe terminal guard, reaper). 14 governance-DB integration tests green.
- **D-131 B2.** `process_execution_job_for_tenant` (claim→heartbeat→start_attempt→`run_recipe_execution_async`→complete/on-raise fail) + `run_s4_execution_tick` + `run_s4_reaper_tick`, per-tenant isolation. Injected `client_resolver` (Tooling client, worker-side, resolved up front so no connection enters the async run) + `run_fn` seams. Complete-on-ran, fail-on-raise. 8 governance-DB integration tests green.
- **D-132 B3.** The firing wiring: `worker.py` `_default_s4_client_resolver` + `s4_execution_tick` (into `worker_tick`); `scheduler.py` `s4_reaper_tick` (into `scheduler_tick`). The production loop ships **live + idle** — both ticks no-op on the empty queue until an enqueue source lands (deferred).

**Verified.** `execution_engine` unit + governance-DB integration (jobs 14 + consumer 8) + worker-wiring 3 + the run-path-adjacent generation/representation/interpretation suites — green. One migration, applied to the governance DB via `alembic upgrade tenant@head`. **Deferred (D-133):** capability+layout execution (Option-X recipe enrichment, reopens S2/S3), `is_required`/`field_type`/`matches_pattern` predicates, the data-path async bracketing, and the product enqueue source. Live-sandbox proof deferred (the inspection spine is already live-proven). DECISIONS_LOG D-127…D-133.

## D-196 — F6 test-data provisioning + dependency-aware cleanup (vertical opening)

After the greenfield cutover's Step 5a (the `meta_*` read-cutover) completed and the disposable v1 test
corpus was deleted (D-195.5), the product runs entirely on the substrate (S3 generates, S4 executes).
Growing S4's executable envelope is the path forward, and the substrate's own roadmap names **F6 —
test-data provisioning + cleanup** as the load-bearing next frontier: S4's positive vertical can today
create records with required **scalars** only (`world.py` fences off required lookups — the §3 fence),
so the large class of master-detail/lookup objects is unexecutable.

Vertical phasing (branch `phase-22-substrate-4-provisioning`):
- **F6.1 — cleanup spine.** Per-tenant `s4_created_records` (alembic tenant branch, schema-isolated) +
  a `CreatedRecordTracker` with **reverse-order** teardown, generalizing `_run_positive`'s inline single
  `_best_effort_delete` to N records (audit persisted at `finalize_run`). Behavior-neutral for the
  single-create case; the spine F6.2 fills.
- **F6.2 — parent-lookup provisioning.** `world.py` recursively constructs required master-detail/lookup
  parents (bounded recursion + cycle guard), lifting the §3 fence; the positive vertical reaches
  lookup-needing objects.
- **F6.3 — live proof on env 59** (parent→target→read→assert→reverse-order cleanup, zero PQA_% leak).

Decisions: teardown in-execution + finalize-persisted audit (the crash-recovery reaper needs pre-teardown
brief-tx durability — deferred); minimal F1 lift-to-neutral (extend S4-native `world.py`, lift only the
`PQA_%`/REST create-delete/`classify_failure` primitives); cleanup multi-pass deferred (reverse-order
single-pass first). DECISIONS_LOG D-196.

## D-196.1 — F6.1 cleanup spine realized; F6.2 parent provisioning designed

**F6.1 (realized, `33023d3`).** The cleanup spine landed: per-tenant `s4_created_records` (alembic tenant
`20260608_0010`, schema-isolated) + `provisioning.CreatedRecordTracker` — `record()` accumulates
`(sobject, record_id)` in create order, `teardown(client, delete_fn)` deletes **reversed** (children before
parents) via an injected delete (no executor import cycle). `_run_positive` swaps its inline single
`_best_effort_delete` for the tracker (byte-identical for the single-create case), threads
`RunEvidence.created_records`, and `persist_run_evidence` writes one audit row per created record. 150
execution_engine unit + 2383 broad green; behavior-neutral.

**F6.2 (designed, D-196.1).** A 6-agent read of the live code settled the shape: parent construction is
contained to `world.py` + `data_executor.py` — **bridge/plan untouched** (the positive plan stays a pure
3-step triple; provisioning is a runtime side-effect). A new `world.py` `construct_world` entrypoint drives
the recursion (keeping `resolve_operational_padding` the pure leaf resolver), detecting required parents by
**requiredness × `references_object_entity_id`** (master-detail/lookup are not branched — requiredness is the
discriminator), with an Object-`entity_id` **cycle guard** and a `MAX_PARENT_DEPTH = 3` bound. Forks
resolved: minimal F1 lift, F6.2 unblocks already-emitted lookup-object recipes (assumed; corpus-confirmed at
impl), reverse-order single-pass cleanup. DECISIONS_LOG D-196.1.

## D-196.2 — F6.2 refocus: `is_createable` filter + construct leak fix (corpus-grounded)

The F2 corpus check (read-only env 59 → `tenant_1`) found the "build a parent" premise false for the current
corpus — the one data-recipe (Opportunity) needs no business parent; its required references are owner/audit
(`OwnerId`, `CreatedById`). It also surfaced a pre-existing gap: `world.py` padded on `is_nillable` alone, so
it would set Salesforce-managed fields (`CreatedDate`, `SystemModstamp`) → create rejected. F6.2 refocuses
(user decision): (1) an `is_createable` filter skips Salesforce-managed required fields; (2) `construct_world`
omits owner/queue references (`User`/`Group`) — Salesforce defaults them; (3) the construct path's `except`
widens to all exceptions (an S1 read error mid-build no longer leaks a built parent — the adversarial review's
one real finding); `_best_effort_delete` likewise hardened. The parent-construction recursion (D-196.1) stays,
3-lens-verified + tested, **dormant** until a business-lookup recipe exists. Deferred: `defaultedOnCreate` in
S1 (the principled `OwnerId` distinction). 164 execution_engine + 2756 broad green. DECISIONS_LOG D-196.2.

## D-196.3 — F6.3a: bare Salesforce field-name translation at the executor boundary

The read-only readiness check for the live Opportunity run found one more blocker — pre-existing, not F6.2.
The recipe + padding speak S1's **object-qualified** field names (`Opportunity.StageName`); S1 names every
Field `{Object}.{field}` for graph uniqueness. Salesforce's create/SOQL want **bare** names (`StageName`),
and nothing translated, so a live create/read would be rejected. Fix at the executor — the logical→physical
boundary: three pure helpers (`_sf_field` strips the `{sobject}.` self-prefix; `_sf_fields`/`_sf_soql` apply
it) at the create payload, the read SOQL + captured fields, and the assert's field lookup. Back-compatible
by construction (bare names pass through unchanged → existing tests green). Read-only readiness on real S1:
the live Opportunity create would now be `{StageName, CloseDate, Name}` + `SELECT StageName FROM Opportunity
WHERE Id = '<id>'` — both valid. 165 execution_engine + 22 integration + 2779 broad green. DECISIONS_LOG
D-196.3. Next: the live run on env 59 (org write needs explicit go-ahead + post-run leak check).

## D-197 — S4 enqueue source: the spine + a manual queue endpoint

The execution loop was wired but idle (D-132) — nothing enqueued a job. This opens the enqueue source. The
load-bearing decision: the consumer's default `run_fn` flips from `run_recipe_execution_async` (which **refuses
every data-recipe** — metadata-path-only, D-129) to the **sync** `run_recipe_execution_for_tenant`, which runs
*all* recipe kinds (holding a connection per run — the accepted low-volume interim; the data-path async
bracketing stays deferred-proper). `client_resolver` becomes optional; the default path passes `client=None` so
the sync run fn self-resolves the per-kind client (Tooling/Data) after selection; `worker.py` drops the
Tooling-only `_default_s4_client_resolver` injection (kept defined for the future async path). New
`execution_engine/intake.py` `enqueue_s4_execution` — a thin wrapper over the idempotent
`ExecutionJobStore.create_or_get_job` (mirror of `enqueue_s3_generation`). The manual queue route
(`POST /api/s4-execution-jobs` + status poll) lands on `main` after the substrate merges, moving the
prod-confirm gate to enqueue time. 193 execution_engine green incl. the full offline spine loop
(`enqueue → run_s4_execution_tick → completed`). Deferred: the automated triggers (approval-hook, scheduled
re-verification); the data-path async bracketing. DECISIONS_LOG D-197.

## D-197.1 — F6.3 closed: the first live data-mutation run, through the production loop

2026-06-10, env 59, job 5, run `6aab8882-…`: the approved Opportunity value-claim recipe executed live —
enqueue → the deployed worker tick → the sync run path → real Salesforce. Create HTTP 201 (bare
`{StageName, Name, CloseDate}` payload — D-196.3 live-verified), read-back 1 row, `equals` held → **passed**
(1.4 s); cleanup delete Salesforce-confirmed; `s4_created_records` audit row persisted (the F6.1 tenant
migration applied to prod en route, `20260608_0010`, user-GO'd). En-route finding: the earlier
`invalid_client_id` was environmental — local runs lack `CREDENTIAL_ENCRYPTION_KEY` and
`get_connection_decrypted` silently falls back to ciphertext (latent foot-gun, candidate hardening). F6 is
fully closed: F6.1/F6.2/F6.3a built + merged, F6.3 live-proven through the D-197 queue. DECISIONS_LOG D-197.1.

## D-203 — 5b-1: the 2-step behavioral negative (setup create → rejected update/delete)

The first D-202 re-platform arc. The bridge dispatches on the REJECTION-BEARING step (S2 guarantees
at most one) and projects exactly two negative shapes — the 1-step create-rejected (D-110.2) and the
new 2-step `(PlannedCreate(no expectation), PlannedUpdate|PlannedDelete)` with **positional**
`setup_step_id` binding (no `$ref` machinery); every other shape fails loud. `DataMutationClient`
gains `update` (PATCH, the create-style envelope — a rejection is captured data; 204 → success).
`_run_negative_with_setup`: construct-world + setup create through the F6 machinery (padding +
parents + tracker), the prohibited mutation, the SAME 4-way grading as the 1-step negative, teardown
ALWAYS — a wrongly-successful delete's teardown 404 is recorded best-effort, never raised. Any setup
failure is `errored` (the prohibition was never exercised), never `failed`. New
`Update/DeleteAttemptEvidence` flow through the kind-agnostic persister + run-detail UI unchanged;
the subject's CleanupRecord rides the setup create's evidence (the step that created the record —
the established convention). Zero migrations; zero consumer/worker changes (the sync run path runs
all recipe kinds, D-197); `run.py` unchanged in logic (`steps[0].expect_rejection is None` already
injects S1 for any plan that constructs a world). 40 new unit tests; S4 suites green (190 unit + 28
integration). DECISIONS_LOG D-203.

## D-203.2 — 5b-1 closed: the first live update-rejected run

2026-06-10, env 59, job 9, run `3363f1e4-…` (3.1 s): setup create 201 (padding live-proven incl.
the D-204.2-fixed picklist filler — StageName "Prospecting") → prohibited update PATCH
`{Amount: 10001}` → 400 `FIELD_CUSTOM_VALIDATION_EXCEPTION` matched → **passed**; teardown
Salesforce-confirmed; audit row persisted; S6 `prohibition_enforced` on the update step. Four
latent pre-existing defects fixed en route (D-203.1 / D-204 / D-204.1 / D-204.2 — formula-reader
shape blindness, the attributes contract, isActive-null, edge-walking picklist enumeration).
DECISIONS_LOG D-203.2.

## D-205 — 5b-2: N-create chains with cross-step references (multi-step positives)

The positive vertical lifts from the exact triple to ``CreateStep × N → ReadStep → AssertStep``.
New ``refs.resolve_field_value_refs``: ``$<step_id>.<attr>`` tokens in a create's field VALUES
resolve against the chain's accumulated state (string values only; the SOQL resolver's grammar +
fail-loud discipline) — resolution sits after the padding merge, before bare-ification.
``_run_positive`` is now a loop (per create: construct-world → resolve refs → create → track →
thread state), read-back while records are alive, teardown-always BEFORE grading, then ground.
Cleanup attribution maps teardown records onto each create's evidence **by record id** (provisioned
parents interleave with chain creates). Per D-205's charter correction, S3 multi-step emission is
**gated on multi-object grounding** (no claim kind's grounding names two objects; F6.2 already pads
required parents invisibly) — engine capability ships now, mirroring 5b-1's delete leg. En-route
latent fix: the D-115.2 rejected-create disambiguation compared Salesforce's BARE rejection fields
against QUALIFIED semantic keys — never matched for S3-emitted recipes; the grading call now
bare-ifies. 12 new tests; S4 suites green. DECISIONS_LOG D-205.

## D-205.1 — 5b-2 closed: the first live N-create chain

2026-06-10, env 59, job 10, run `db93ac3a-…` (8.1 s): Account created → Contact created with the
live-resolved `$create-account.id` reference + padding → read-back → assert held → **passed**;
both records torn down reverse-order with per-record cleanup attribution; S6 `value_persisted`.
Residual: the positive attribution prose names the first create's sobject (wording only).
DECISIONS_LOG D-205.1.

## D-230 — 2.4: durable data-path cleanup + async bracketing

2026-06-13. Two durability gaps in the live data path, both shipped.

**Part A (durable cleanup reaper)** — merged `e0c2d6d`, deployed. The `s4_created_records` audit rows
are now WRITE-AHEAD (`StrandedRecordSink`, own committed tx, `environment_id`-tagged, the sole writer;
the finalize-time write removed) so a crash any time after `client.create` leaves a reapable row; in-run
teardown flips `cleaned=true`. `reap_stranded_records` (scheduler `run_s4_cleanup_reaper_tick`) deletes the
genuinely-stranded — gated by a **run-liveness interlock** (`NOT EXISTS` an active `s4_execution_job` for the
env, the adversarial-review BLOCKER fix: the JOB reaper only marks the job dead, the synchronous worker keeps
running), a 7-day give-up window, full-batch WARN. Migration `20260613_0010` (`s4_created_records.environment_id`)
applied to prod tenant_1; pre-deploy NULL-env rows are skipped. Live kill-mid-create proof deferred per AK.

**Part B (async data-path bracketing, D-230.2)** — the D-129/D-197/D-202 residual, retired. `construct_world`
is split into `plan_world` (the S1 read-walk → a detached `WorldPlan` tree) + `build_world` (the SF creates from
that plan, no reads); `construct_world` is the thin wrapper. `run_recipe_execution_async` gained a data branch
that pre-resolves the operational world + resolves the kind-aware client in its select bracket, then executes
holding **no DB connection** — verified against the parent-provisioning chain (no mid-execute read escapes). The
consumer's default `run_fn` flipped sync→async (everything→async); the per-in-flight-job held connection is
retired; the sync path stays the live-proven fallback (UI Run). A 5-reviewer adversarial pass found NO correctness
defects (the no-connection invariant, snapshot completeness, consumer-flip safety, and persist/edges all hold);
the plan/build split also makes the SYNC path create strictly fewer records on a doomed world (identical
`(filler, unfillable)` outcome; one transport-error-on-a-doomed-branch sub-case flips the terminal ErrorSurface to
`UnfillableWorld`, both still `errored`). Zero migrations for Part B. Suites: unit 2554, execution_engine
integration 40. DECISIONS_LOG D-230 / D-230.2.
