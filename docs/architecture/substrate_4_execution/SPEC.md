# Substrate 4 — Execution Engine — SPEC

**Status:** First vertical realized — **metadata-inspection**, built + merged + live-proven against the sandbox (bridge → executor → result store → finalize → run path). F1 / F2 / F3 realized; data verticals since: the 1-step create-rejected negative (D-110.2), the positive create-and-verify (D-115), provisioning + cleanup (F6, D-196), the enqueue loop (D-197), and the **2-step update/delete-rejected negative** (D-203 — setup create through the F6 machinery → rejected mutation → 4-way grading → teardown always), and **N-create positive chains with cross-step references** (D-205). See the footer Status block, `DEFERRED_ITEMS.md`, and `EVOLUTION.md`.

**Last substantive update:** 2026-05-27 (first-vertical phase close-out)

---

## Purpose

Substrate 4 is PrimeQA's **execution engine**: it takes the **recipes** Substrate 2 represents (and Substrate 3 generates), runs them against a real Salesforce org, and **captures what actually happened** as durable evidence. It is the bridge from a *represented* test to a *run* test.

The substrate boundary is sharp: **S4 captures truth — including the grounded outcome — but does not *interpret* it.** Execution runs the recipe, captures rich evidence (what was sent, what came back, what state changed, in what order, what errored), and **evaluates the recipe's grounded assertion** → a **grounded run outcome** (`passed` / `failed` / `errored` / `skipped`; §4) — *grounded* because it tests the specific S3 claim, not because the value is "verified" (that is the claim's static posture, a separate layer — §4). That outcome *plus* the evidence is S4's captured truth. What S4 does **not** do is *interpret* the outcome — failure classification, root-cause attribution, explanation, and clustering are Substrate 6 (Observation & Interpretation), which reasons over the evidence to decide what the outcome *means*.

The v1 mistake was not *rendering* an outcome — it was rendering an **ungrounded** one: the `expect_fail` runtime flag-flip, a pass/fail verdict divorced from any semantic claim. S4's outcome is **grounded** — it verifies the specific S3-generated claim the recipe operationalizes (§3 / F1). Capturing the grounded outcome is not interpretation; attributing *why* it came out that way is.

Position in the stack:

```
S3 (Generation) --writes--> S2 recipe (test_recipes)
                                  |
                            [ S4 bridge ]
                                  v
                  S4 execution (this substrate) --captures--> evidence (S4-owned)
                                  |                                  |
                          posture callback                     raw evidence
                                  v                                  v
                       S2 (coverage / posture)                 S6 (interpretation)
```

## 1. What S4 is (and is not)

- **Is:** the runtime that executes an S2 recipe against an org and records the observed truth.
- **Is:** evidence-first — capture is rich and extensible; meaning is deferred to S6.
- **Is not:** an interpreter (S6), a generator (S3), or a remediation agent. S4 captures a failure's truth; it does not fix it (F7).
- **Is not:** the v1 executor. v1 `execute_step` is a shallow per-step REST runner welded to a pass/fail + `expect_fail` semantic and the `run_step_results` model. S4 reuses v1's *mechanical* primitives (§3) but owns the orchestration, the outcome interpretation, and the result model.

## 2. Components

1. **S2-recipe → executable-plan bridge.** Consume S2's **typed `RecipeRead`** (via the Coordinator's `select_recipe_for_execution`, per-tenant) and **narrow + validate + project** it into an executable plan — it does *not* re-decode raw JSONB. The JSONB↔Pydantic decode is S2's, accessed through the Coordinator: the **S4↔S2 read-through boundary** (every S4 read of S2 goes through a Coordinator interface — slices 2–4 included, not just the bridge). The contract boundary — S4's input is an S2 recipe, never a v1 `TestCaseVersion.steps` blob.
2. **Per-`recipe_kind` executors.** Dispatch on `recipe_kind`: `metadata-recipe` (Metadata/Tooling read + assert — the first vertical), then `data-recipe` (CRUD), `ui-recipe` (browser), `event-subscription-recipe`, `callout-intercept-recipe`. Each kind has a distinct runtime; the executors share the reused mechanical primitives (§3) but own their step semantics.
3. **The edge→live-read translator.** A recipe's `fields_to_capture` carries **S1-edge vocabulary** (e.g. `APPLIES_TO`), not raw Salesforce fields. Verifying it against the live org means translating the edge into a live Tooling query — e.g. `APPLIES_TO` (ValidationRule → Object) becomes a `ValidationRule` query scoped to the subject Object (`WHERE EntityDefinition.QualifiedApiName = <Object>`). The translator is **S4-owned, finite, edge-keyed** (one mapping per TIER_1 edge this vertical needs). **The reusable unit is the encoded translation knowledge** — which Tooling object, which columns, how the edge maps to a scoped query — which is *substrate-neutral*; it is **not the S1-sync fetcher**, which carries sync-world assumptions (bulk two-phase fetch, syncability filtering, normalize/materialize) S4 must not inherit (D-108.1). Two concerns stay **separate**: **(i) edge→SOQL translation** (the translator) and **(ii) authenticated Tooling transport** — a **thin S4-local client** (authenticated read + pagination + typed error mapping, *nothing more*). The transport client must **never** accumulate entity semantics, edge logic, metadata interpretation, or traversal policy — that would recreate shadow metadata infrastructure inside S4. It **lives in the executor**: the bridge's plan stays semantic (it references the edge); the executor translates to a query at run time.

   **Realization principle (the translator is operational, not semantic).** Edge→SOQL mappings are **operational realization rules, not semantic authority**: the query reflects only what the recipe's assertion carries. The translator adds operational mechanics — the `FROM` object, the API path, pagination — but **never a semantic predicate**. A semantically meaningful filter (active-ness, object identity) **must trace to the recipe/claim, never a translator default.** Concretely, re-examine `AND Active = true`: the emitted inspection recipe asserts plain **`exists`** (predicate=`exists` over the `APPLIES_TO` read), so the slice-2 translation carries **no `Active` filter** — adding one would inject a predicate the recipe never asserted. If active-ness is to be verified, it must be carried by the recipe's assertion (an S3/emission concern), not the translator. Slice 2 verifies where active-ness actually lives and handles it per this principle.
4. **Environment binding + capability matching.** Bind to a target org (connection / credential, sandbox vs production) and match a recipe's `ExecutionEnvironmentBody` capability assumptions against the env's actual capabilities ("pick the recipe that fits") — the built S2 `select_recipe_for_execution` already performs this match. v1's connection / OAuth is reused; rich capability-matching is deferred (F5).
5. **The captured-truth result model (F2).** S4-owned, evidence-first (§4).
6. **The S2 posture callback.** After a run, S4 reports **posture** to S2 via the built `report_run_outcome(actor='s4', …)` surface — the **run outcome** (`passed` / `failed` / `errored` / `skipped`) plus latest refs + coverage freshness — never raw evidence. The claim's `verified` / `caveated` posture is an upstream S3 property, *not* a run outcome (§4).

## 3. F1 — the reuse boundary (LOCKED; D-108)

S4 reuses v1's **mechanical, semantically-dumb** primitives and owns the **semantic** layer. The clean seam is the layer *beneath* `execute_step` (code-verified):

**Reuse** (lifted to a neutral shared module where pure — resolving the substrate→v1 dependency direction):
- the REST transport client (`SalesforceExecutionClient` — create / update / delete / query / get / convert → normalized envelope);
- `integrations/` retry / auth-refresh + typed exceptions + the pure `classify_sf_exception` (already neutral);
- the `$var` state-ref resolvers (`_resolve_ref` / `_resolve_refs` / `_resolve_soql_refs` — pure over a `state_vars` dict);
- the `data_engine` factory / template / `generate_value` primitives;
- the cleanup **mechanism** (reverse-order delete + `PQA_%` emergency sweep).

**Own** (S4-native): the recipe-execution orchestration; outcome interpretation (recipe assert / the behavioral expect-rejection — *not* the v1 `expect_fail` flip); the result model (§4 — *not* `run_step_results`); the state-var lifecycle + created-record tracking (re-keyed for cleanup); the recipe→data binding.

**Out (do not reuse):** the `execute_step` monolith; the `expect_fail` shallow-negative semantic; `run_step_results` persistence; `TestCaseDataBinding`'s TC-link.

The lift-to-neutral is a **small, incremental v1 refactor** — done per increment's needs, not up front.

**Slice-2 boundary — where each concern lives (D-108.1):**

| Concern | Owner |
|---|---|
| Credential resolution (env → connection → decrypt → token) | **reused** — v1 `_oauth_token` / D-106.4 path |
| Tooling transport (authenticated read + pagination + typed errors) | **thin S4-local client** (decision 1 = (a); *not* the v1 metadata client) |
| edge→SOQL translation | **S4 operational mapping** (finite, edge-keyed; §2.3) |
| Semantic interpretation (why an outcome came out this way) | **S6** |
| Ontology authority (what an edge / entity *means*) | **S1 / S2** |

**S4 reuses operational credential plumbing, not metadata-sync semantics or semantic execution assumptions.**

## 4. F2 — the result model (LOCKED philosophy; first schema concretized at slice 3; D-108 / D-108.2)

**Evidence-first, S4-owned.** A run captures raw observations *richly* and *honestly*: timestamps + ordering, request / response, before / after state, error surfaces, environment context, and per-step outcomes — an **extensible** schema that grows with the first vertical and richer recipe kinds. The capture breadth is deliberately **not frozen**: it expands as recipe kinds land (e.g. browser traces / screenshots for `ui-recipe`).

**First realized schema — `s4_execution_runs` (slice 3, D-108.2).** The result store is **one kind-agnostic, per-tenant table** (tenant-branch migration; created unqualified with **no `tenant_id` column** — isolation by schema, the substrate-1/-2/-3 convention). Shape:

- **Typed identity / outcome columns (queryable):** `run_id` UUID **PK** · `recipe_id` UUID · `recipe_version_seq` int · `claim_test_id` UUID · `claim_version_seq` int NULL · `environment_id` int · `outcome` · `started_at` · `finished_at` · `duration_ms`.
- **`evidence` JSONB (the captured trace):** the full per-step trace — translated queries, structured filters, returned rows, per-step timings, per-step error surfaces — the **extensible** part that grows with recipe kinds (F2). This is *raw observation* (what the org returned), never semantic data.
- **`outcome` enum:** the column **reuses the existing `run_outcome` PG enum** (`'passed' / 'failed' / 'errored' / 'skipped'`, `create_type=False`) — verified to match the S4 vocabulary *and* slice 4's `report_run_outcome` signature exactly (no v1 `error`-vs-`errored` divergence; no dedicated S4 enum needed). The run column therefore reconciles to the S2 boundary verbatim.

**Schema decision = A (run-entity + JSONB trace), and why it fits the DB philosophy.** The **run is an entity** — its identity + outcome are *typed, queryable columns*, never buried in JSONB. The JSONB holds **only the captured trace** (raw observation), so the no-JSONB-blob rule — which targets the **semantic store** (claims / recipes, where meaning must be columnar + queryable + hashable) — is honored, not bent: execution *truth* is not semantic data. One table serves **all** recipe kinds; only the JSONB grows (F2 extensibility preserved) — CRUD / UI verticals reuse the same identity columns without schema churn.

**B-trigger (A→B is a forward migration, reversible — not a lock).** Promote per-step facts (edge, subject, `row_count`, `held`, …) to a **structured child table** when a *real* per-step query need emerges — S6's concrete query patterns, or CRUD's N-step shape making per-step rows earn their keep. Until then, A pays nothing for child rows the fixed 1-read-1-assert vertical doesn't query. The move is additive (a child table beside the run row), so deferring it costs no rework.

**Posture, not evidence, crosses to S2 — and posture is two orthogonal layers.** S4 sends S2 a compact **posture** (never the raw evidence — that stays S4-owned for S6). Two layers coexist in it and must **never** be conflated or mapped onto each other:

- **Run outcome (S4, live, this run):** `passed` / `failed` / `errored` / `skipped` — the assertion held / didn't hold / couldn't be evaluated / didn't run. This is what S4 *produces*, and it feeds the built `report_run_outcome(actor='s4', …)` → `test_recipe_runtime_state` (latest refs + coverage freshness). It reconciles to the existing S2 surface verbatim.
- **Claim posture (S3, static, set at generation):** `verified` / `caveated` — properties of the *claim* (whether S3 statically verified the formula, D-107). S4 **neither produces nor maps to** these; they are upstream of execution.

The signal that matters lives in the **combination**: a **`verified` claim with a `failed` run** — well-grounded at generation, yet it did not hold against the live org — is exactly what S4 surfaces for **S6** to interpret. S4 records both layers truthfully and renders the grounded run outcome; **S6** interprets the *why* (classification, root-cause, explanation, clustering) — it does not re-derive the outcome, nor collapse the two layers into one verdict.

**No interpretation — even under product pressure.** S4 records truth + the grounded outcome; it does **not** classify, infer reasons, or interpret *absences* — even when a product surface asks "why did it fail?" (that question is **S6's**). Example: a `failed` `exists` can mean the subject Object is **absent**, *or* **present but carries no ValidationRule** — both yield a 0-row read. S4 records the **query + filter + 0-row result** (rich enough that the distinction is recoverable); **S6** decides which "why" it was. S4 never collapses that ambiguity into an inferred cause.

**Mine v1 for lessons, not inheritance.** v1's `run_test_results` / `run_step_results` (api_request / response, before / after state, `comparison_details`, `failure_class`, timings, correlation_id) is a rich source of *operational lessons* about what is worth capturing — S4 adopts the lessons, not the schema (which is welded to v1's `expect_fail` / run model).

## 5. First vertical — metadata-inspection (F3, LOCKED)

The first executable recipe is the only kind S3 emits today: the **inspection-trigger + metadata-recipe** (D-099 / D-107). The executor **live-reads the org** (Metadata / Tooling) and **asserts the grounded claim still holds** (e.g. the ValidationRule `APPLIES_TO` the subject is **present** — the recipe asserts `exists` over `APPLIES_TO`, so slice 2 verifies the relationship is present, with no `Active` filter; whether the claim is *behaviorally* about active-ness is parked as **S4-Q-001**, S3-owned) — an execution-time re-inspection (D-099.3), not a frozen snapshot. It needs no test data (F6) and no browser (F4). It is the thinnest end-to-end vertical that exercises the whole spine: **bridge → executor → evidence capture → posture callback.**

**Slice arc (the plan, not locked contracts):**

1. **Recipe → executable-plan bridge.** Consume S2's typed `RecipeRead` (via `select_recipe_for_execution`, per-tenant) — the JSONB↔Pydantic decode is S2's, accessed through the Coordinator (the S4↔S2 read-through boundary) — and **narrow + validate + project** its `observation_realization` (+ trigger; carrying the linked claim by `claim_test_id`, not resolving it) into a *semantic* executable plan (ordered read + assert over a `LogicalRef`). The plan stays in edge vocabulary; the executor translates at run time.
2. **Executor + assertion evaluation.** The metadata-inspection executor, as S4-owned pieces:
   - **`translator.py`** — edge→SOQL, finite + edge-keyed, **no semantic injection** (operational mechanics only; §2.3).
   - **`credentials.py`** — `resolve_tooling_client(db, environment_id)` via the D-106.4 path (`_oauth_token` → access token, per `auth_flow`).
   - **the thin transport client** — authenticated Tooling read + pagination + typed error mapping (S4-local; §3 boundary). Decision 1 = (a) (D-108.1): S4-local, *not* the v1 metadata client.
   - **`evidence.py`** — `RunEvidence` / `StepEvidence` (in-memory; read-only N/As — no before/after-state, field-diff, or artifacts).
   - **`executor.py`** — walk the plan → translate → live-read → evaluate the `AssertionPredicate` (`exists` now; other predicates deferred) → the grounded **run outcome** (`passed` / `failed` / `errored`) + evidence. The client is **injected** (a stub drives unit tests with no org / no PG; a **gated live integration test** covers the real read).

   **Decision 2 — `APPLIES_TO` query:** scoped (`WHERE EntityDefinition.QualifiedApiName = <Object>`), **live-verified** in slice 2, with the bulk-fetch + client-side filter as a zero-risk fallback. Produces the evidence **in-memory** (slice 3 persists it).
3. **Evidence-first result capture.** The S4-owned result store persists slice 2's in-memory evidence — the **first concrete result-model shape** (§4, schema A; D-108.2):
   - **Migration** — a **tenant-branch** migration chaining off the current head `20260525_0030`, creating `s4_execution_runs` (typed identity/outcome columns + `evidence` JSONB; `outcome` reuses the `run_outcome` enum, `create_type=False`).
   - **`result_store.py`** — the `s4_execution_runs` SQLAlchemy model on the **project `Base`** (unqualified, no `tenant_id` — substrate convention) + `persist_run_evidence(session, evidence) → run_id` (session-provided per substrate convention).
   - **`RunEvidence` gains `run_id: UUID`** — the executor mints `uuid4()` at run start (a small slice-2-shape extension, F2-expected; the run self-identifies from birth). It flows produce → persist (PK) → slice 4's callback, so the run row hands S2 exactly what `report_run_outcome` wants (`run_id`→`last_run_id`, `recipe_id`, `recipe_version_seq`→`last_run_recipe_version_seq`, `outcome`→`last_run_outcome`, `finished_at`→`last_run_at`).
   - **Persistence boundary** — the **executor stays produce-only** (returns in-memory `RunEvidence`, **no DB import**); a **separate persister** writes the row. Slice 2's no-DB unit tests stay untouched; the persister is covered by a governance-DB integration test (+ a fake-session mapping unit test).
4. **Posture callback — the finalize step (D-108.3).** A thin `finalize_run(session, evidence, *, coordinator=None) → RecipeRuntimeState` (in `execution_engine/finalize.py`): `persist_run_evidence(session, evidence)` (slice 3) **then** `report_run_outcome(actor='s4', recipe_id=…, last_run_id=run_id, last_run_at=finished_at, last_run_outcome=outcome, last_run_recipe_version_seq=recipe_version_seq)` on the **same session** — both `flush`, the caller owns the commit, so persist + posture land **atomically**. **No new S2 method, no migration** (the surface is built). This is the S4→S2 **write**-side of the read-through boundary (the read side is D-108.1); the `SemanticTransactionCoordinator` is injectable (default-constructed) so it can be spied in unit tests.

   **Scope boundary:** slice 4 is the **finalize seam**, *not* the production trigger. The full-spine wiring — a worker / route that runs `resolve_tooling_client → build_metadata_inspection_plan → execute_metadata_inspection → finalize_run` against a real recipe + live org in one transaction — is a **separate, deferred** concern (nothing calls the executor yet). It lands after the first vertical's four components are complete.

**Order note:** slice 2's executor produces evidence **in-memory** *before* slice 3's store persists it — so the executor can be built and tested against the live read (or a stub) ahead of the result schema. Slices 2 / 3 / 4 each get their own read-only grounding + HOLD-and-show before building.

## 6. The run path — end-to-end recipe execution (D-108.4)

Slices 1–4 built the spine's components; nothing wired them into a runnable path (the slice-4 grounding found no caller of the executor). The **run path** is that caller — a synchronous "execute this recipe" orchestrator.

**Orchestrator.** `run_recipe_execution(session, test_id, *, environment_id, available_environment=None, client=None, coordinator=None) → RunPathResult` chains the four components:

```
select_recipe_for_execution(session, test_id, available_environment) → RecipeRead | None
  → build_metadata_inspection_plan(recipe)            (bridge, slice 1)
  → resolve_tooling_client(session, environment_id)    (or an injected client)
  → execute_metadata_inspection(plan, client, environment_id)  (executor, slice 2)
  → finalize_run(session, evidence, coordinator)        (persist + posture, slices 3–4)
```

Defaults (all injectable — the executor/finalize discipline): `available_environment` → a **minimal inspection env** (`auth_kind="metadata_api_user"`, the only assumption the emitted inspection recipe makes); `client` → `resolve_tooling_client`; `coordinator` → `SemanticTransactionCoordinator()`. A thin outer `run_recipe_execution_for_tenant(tenant_id, …)` owns the `get_tenant_connection` context + the single commit (the production entry).

**`RunPathResult`** distinguishes two outcomes: **ran** (carries `evidence` + `runtime_state`) vs **no-eligible-recipe** (carries a reason; no run happened — `select_recipe_for_execution` returned `None` because no approved claim / no approved recipe / no environment match).

**Transaction boundary — A (single transaction).** One tenant-scoped session/transaction spans `select → execute → finalize`, committed **once** on clean exit — the `LedgerPersister` idiom (`with get_tenant_connection(tenant_id) as conn: session = Session(bind=conn)`; the context commits atomically). One session suffices for both data domains because `get_tenant_connection` sets `search_path = "tenant_<id>", public` — per-tenant tables (`test_recipes`, `s4_execution_runs`, `test_recipe_runtime_state`) resolve unqualified to `tenant_N`, and the v1 public tables `resolve_tooling_client` reads (`environments`, `connections`) resolve via `public`. The S2 Coordinator's third production caller (this read/select side; `LedgerPersister` and `finalize.py` are the write side).

*Caveat (and its bound):* **A holds the DB transaction open across the live Tooling read** (~1–2 s). Acceptable for this **bounded, low-concurrency synchronous** path. It does **not** generalize: the **async orchestration** (the worker-driven path — the consumer's default `run_fn`, realized D-129 for metadata + D-230.2 for data) **does not hold a DB transaction across external I/O** — it brackets the live read with brief transactions (select+snapshot → execute holding **no DB connection** → persist+posture+interpret), pre-resolving the data path's operational world into a detached `WorldPlan` up front (D-230.2) so even the parent-provisioning chain needs no mid-execute read. Boundary A remains the right call for the sync (UI Run) path only.

**Errored runs still finalize.** The executor catches `SFClientError` → an `errored` `RunEvidence` (it does **not** raise) — an errored run is *truth*, so it persists + reports posture like any other outcome. Only an **unexpected** exception (a code bug, or a fail-loud `UnsupportedPredicateError` / `AssertionResolutionError`) propagates and rolls the transaction back — correct: a half-run from a defect is never persisted.

**Live-test note (inject the client).** The local substrate test DB (alembic-migrated) has **no `environments` / `connections` tables** — those are v1 `migrations/*.sql` public-schema tables, not in the alembic shared branch — so `resolve_tooling_client` cannot run there. The whole-spine live test therefore **injects** a real `ToolingReadClient` (built from `SF_*` env creds, the slice-2 pattern) and bypasses credential resolution (already unit-tested in slice 2). This drives the injectable-`client` parameter — it is necessary, not merely convenient.

## 7. The data-recipe execution path — behavioral negative (D-110.2)

The second vertical (CRUD / `data-recipe`, F4) executes a **behavioral negative**: a `data-recipe` whose `CreateStep` carries `expect_rejection` (D-110.1) — a create the org *should* reject (a validation rule fires). It verifies the prohibition **actually enforces**, the question inspection cannot answer. Built **parallel** to the inspection vertical (not a generalization): it shares the read-through-Coordinator seam + the plan → execute → finalize spine, but projects + evaluates a different shape.

- **Parallel bridge.** `build_data_recipe_plan(RecipeRead)` gates `recipe_kind=="data-recipe"` + `trigger_kind=="data-mutation-trigger"` and projects the `CreateStep` (target_object, field_values, `expect_rejection`) into a new `DataRecipePlan` / `PlannedCreate` — identity carried, not resolved; no raw-JSONB re-decode (the `RecipeRead` boundary). Generalization (one bridge over recipe-kinds) is deferred to recipe-kind-family growth (rule of three).
- **Thin data client (Fork D = build-thin).** `DataMutationClient` (`create` + `delete`), reusing `_oauth_token` + the neutral `integrations.exceptions` — the `ToolingReadClient` precedent; `resolve_data_mutation_client` is the `resolve_tooling_client` analog. `delete` exists only for the §-cleanup below.
- **The grounded 4-way eval (strictly stronger than v1's `expect_fail` sin).** `execute_data_recipe` attempts the create and evaluates against the step's `RejectionExpectation`: rejected **and** `error_code` matches → **`passed`**; succeeds (2xx) → **`failed`** (the prohibition did not enforce — the grounded analog of v1's `expected_fail_unverified`); rejected but `error_code` doesn't match → **`failed`** (rejected for the *wrong* reason — the exact case v1 wrongly flips to passed); couldn't attempt → **`errored`**. The **match-the-code** step is what grounds it (v1's bare flag flipped *any* failure to passed). Match is robust to a multi-error body (match if any error's `errorCode` matches); evidence captures the full body.
- **Evidence (Fork E = extend the JSONB).** A `CreateAttemptEvidence` `StepEvidence` variant: the attempted mutation + the rejection captured (`error_code` / `message` / `http_status` / `matched` / full body) + cleanup (`attempted` / `succeeded` / `record_id`) + timings; `before/after_state` + `field_diff` stay N/A. **No migration, no new persister** — it serializes via `persist_run_evidence`; the result store (§4) + finalize (§5/D-108.3) are **reused unchanged**.
- **Minimal-cleanup (refines F6 / D-110 Fork C).** "A rejected create creates nothing" holds for the **passing** path. The **failing** path (create succeeds) *does* create a record → a **targeted best-effort delete** of that one record (the envelope's `record_id`) — *not* the full F6 machinery. Best-effort: logged-not-fatal, the outcome stays `failed`, the evidence records the attempt. So **F6 is partially touched** (the targeted delete); full provisioning / cleanup with a tracking table stays deferred.
- **Live test (N-4).** The eval's four outcomes are stub-proven (no org). The whole-spine live proof uses a **self-contained deterministic rejection** (a required-field miss → `REQUIRED_FIELD_MISSING`) — reliable, no sandbox dependency, a **mechanism proof** distinct from the product use case (a VR firing). A VR-specific live test is opportunistic (a read-only probe for a firing VR) and otherwise deferred — the spine is already proven.

---

## Status

**First vertical realized — metadata-inspection (2026-05-27).** Built across four design+impl slices + the run path (D-108.1 → D-108.4), merged to `main`, and **proven live** against the Salesforce sandbox (`select → bridge → execute-live → finalize`, grounded outcomes Account/Lead). Realized surface: the recipe→plan bridge (`build_metadata_inspection_plan`), the edge→SOQL translator (`APPLIES_TO`, no semantic injection), the thin S4-local Tooling client (auth read + pagination + typed errors), credential resolution (`resolve_tooling_client` via the D-106.4 path), the executor + `exists` evaluation → grounded run outcome (`passed`/`failed`/`errored`), in-memory evidence capture, the per-tenant result store (`s4_execution_runs`, run-entity + JSONB trace), the finalize step (persist + S2 posture callback, atomic), and the end-to-end run path (`run_recipe_execution` / `…_for_tenant`).

**Second vertical realized — behavioral negative (CRUD / `data-recipe`, 2026-05-27; §7, D-110.1 → D-110.3).** The cross-substrate CRUD programme (S2 → S4 → S3), built PR-based on `phase-5-substrate-4-crud` (PR #5, not yet merged) and **live-proven end-to-end with a real validation-rule rejection**. Realized surface: S2's `expect_rejection` / `RejectionExpectation` recipe-model (D-110.1); the parallel `build_data_recipe_plan` → `DataRecipePlan`/`PlannedCreate` bridge; the thin S4-local `DataMutationClient` (+ `resolve_data_mutation_client`); `execute_data_recipe` — the grounded 4-way create-reject eval (rejected+code-matches → `passed`; success → `failed` + targeted delete; rejected-wrong-code → `failed`; couldn't-attempt → `errored`) — strictly stronger than v1's `expect_fail` sin; `CreateAttemptEvidence` (serialized via the *reused* result store + finalize, no migration); the run-path `recipe_kind` dispatch; minimal-cleanup (targeted best-effort delete, N-5); and **S3's behavioral emission** — `_author_negative` emits the violating create (the D-107-parser-derived `violating_payload`) + `expect_rejection` for a *verified* negative, replacing the inspection re-verify (D-110.3, S3-thin). The S3-thin emission is the live differentiator for VR-enforced prohibitions; required-field population (S3-A) is deferred-not-needed (D-110.3 result).

**F-status:**

- **F1 — reuse boundary: realized** (D-108 / D-108.1). The executor owns orchestration + outcome + result model; it reuses the neutral `integrations/` typed exceptions and the `_oauth_token` credential plumbing. The broader lift-to-neutral of v1's mechanical primitives (transport client, `$var` resolvers, `data_engine`, cleanup) stays **per-increment** — only the inspection vertical's needs were lifted; the CRUD-driven lift is deferred.
- **F2 — evidence-first result model: realized** (D-108 / D-108.2). Schema A (`s4_execution_runs`: typed identity/outcome columns + an extensible `evidence` JSONB trace). The A→B promotion (per-step structured child table) is deferred to its trigger.
- **F3 — first vertical = metadata-inspection: realized** (D-108 / D-108.3 / D-108.4). The full spine, live-proven.
- **F4 — recipe-kind scope: partially realized.** `metadata-recipe` (inspection) **and** `data-recipe` (the behavioral-negative create-rejected, D-110.1 → D-110.3) are built. Deferred: **positive** data-recipes (create→read→assert), update/delete-rejected negatives (need a provisioned record), and `ui-recipe` / `event-subscription-recipe` / `callout-intercept-recipe`.
- **F5 — capability matching: minimal realized.** `select_recipe_for_execution`'s membership match is used (the run path feeds a minimal env advertising both verticals' capabilities); richer capability-fit selection deferred.
- **F6 — test-data provisioning: VERTICAL OPENING (D-196).** Minimal-cleanup is realized (the behavioral negative needs no provisioning; the **targeted best-effort delete** of an unexpectedly-created record (N-5) is built). The full provisioning + cleanup vertical now opens: **F6.1 (realized, `33023d3`)** — a per-tenant `s4_created_records` table + a `CreatedRecordTracker` with **reverse-order** teardown (the cleanup spine, generalizing `_run_positive`'s inline single delete to N records; behavior-neutral for the single-create case); **F6.2 (realized, D-196.1/.2)** — `world.py` gains an **`is_createable` filter** (skip Salesforce-managed required fields — the corpus-grounded fix that makes the live Opportunity recipe runnable + repairs a pre-existing gap) + a new `construct_world` entrypoint that recursively builds required createable **business** lookups (Object-`entity_id` cycle guard + `MAX_PARENT_DEPTH=3`; owner/queue references omitted as Salesforce-defaulted), lifting the §3 fence; **bridge/plan untouched**; the construct path tears down on any exception (no leak). Parent-construction is 3-lens-verified + tested, and **live-proven** (D-227.6, run `1878a105` on env 59, 2026-06-12 — a recursively-provisioned required-lookup parent built before the run's create). **F6.3a (realized, D-196.3)** — bare SF field-name translation at the executor boundary (`_sf_field`/`_sf_fields`/`_sf_soql`): S1 names fields object-qualified (`Opportunity.StageName`) for graph uniqueness; the live REST/SOQL API speaks bare names (`StageName`), so the executor strips the `{sobject}.` self-prefix at the create payload, the read SOQL + captured fields, and the assert lookup (back-compatible: bare names unchanged). **F6.3 (realized, D-197.1)** — the live proof ran 2026-06-10 on env 59 **through the production loop** (enqueue → deployed worker → live org): create 201 → read-back → `equals` held → **passed**, cleanup Salesforce-confirmed, audit row persisted (the F6.1 tenant migration applied to prod en route). Deferred: `defaultedOnCreate` in S1 (principled `OwnerId` distinction); reaper for crash-leaked records (needs pre-teardown brief-tx durability). DECISIONS_LOG D-196, D-196.1, D-196.2, D-196.3.
- **F7 — failure-path / remediation: held (by design).** S4 captures failure-truth and does not remediate; the S4-failure ↔ dormant-agent (G-001) relationship is settled later.

The full deferred ledger is `DEFERRED_ITEMS.md`; the build arc is `EVOLUTION.md`; the one parked question (active-ness, S3-owned) is `OPEN_QUESTIONS.md` S4-Q-001. **Next:** the async/worker orchestration (D-108.4's scaling restructure) or the second (CRUD) vertical.
