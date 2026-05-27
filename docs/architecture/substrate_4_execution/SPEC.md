# Substrate 4 — Execution Engine — SPEC

**Status:** Phase 0 (foundational design) — opening. F1 / F2 / F3 locked (DECISIONS_LOG D-108); F4–F7 triaged. First vertical (metadata-inspection) is the next build.

**Last substantive update:** 2026-05-26 (substrate opened)

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

## 4. F2 — the result model (LOCKED philosophy; schema NOT locked; D-108)

**Evidence-first, S4-owned.** A run captures raw observations *richly* and *honestly*: timestamps + ordering, request / response, before / after state, error surfaces, environment context, and per-step outcomes — an **extensible** schema that grows with the first vertical and richer recipe kinds. The schema is deliberately **not locked**: capture breadth expands as recipe kinds land (e.g. browser traces / screenshots for `ui-recipe`).

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
3. **Evidence-first result capture.** The S4-owned result store persists slice 2's in-memory evidence — the **first concrete result-model shape** is designed here (F2-unlocked until then).
4. **Posture callback.** Map the run outcome → `report_run_outcome(actor='s4', …)` → `test_recipe_runtime_state` (coverage freshness). **No new S2 method** (the surface is built).

**Order note:** slice 2's executor produces evidence **in-memory** *before* slice 3's store persists it — so the executor can be built and tested against the live read (or a stub) ahead of the result schema. Slices 2 / 3 / 4 each get their own read-only grounding + HOLD-and-show before building.

---

## Status

**Phase 0 (foundational design) — opening (2026-05-26).** F1 (reuse boundary), F2 (evidence-first result model), F3 (first vertical = metadata-inspection) locked (D-108); F4–F7 triaged (defer ui / event / callout; minimal capability-match; no test-data until the CRUD increment; S4 captures failure-truth, does not remediate). Next: design + build the metadata-inspection executor + its evidence-first capture.
