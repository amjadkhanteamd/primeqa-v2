# Substrate 4 — Execution Engine — SPEC

**Status:** Phase 0 (foundational design) — opening. F1 / F2 / F3 locked (DECISIONS_LOG D-108); F4–F7 triaged. First vertical (metadata-inspection) is the next build.

**Last substantive update:** 2026-05-26 (substrate opened)

---

## Purpose

Substrate 4 is PrimeQA's **execution engine**: it takes the **recipes** Substrate 2 represents (and Substrate 3 generates), runs them against a real Salesforce org, and **captures what actually happened** as durable evidence. It is the bridge from a *represented* test to a *run* test.

The substrate boundary is sharp: **S4 captures truth — including the grounded outcome — but does not *interpret* it.** Execution runs the recipe, captures rich evidence (what was sent, what came back, what state changed, in what order, what errored), and **evaluates the recipe's grounded assertion** → a **grounded outcome** (verified / failed / caveated / error). That outcome *plus* the evidence is S4's captured truth. What S4 does **not** do is *interpret* the outcome — failure classification, root-cause attribution, explanation, and clustering are Substrate 6 (Observation & Interpretation), which reasons over the evidence to decide what the outcome *means*.

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

1. **S2-recipe → executable-plan bridge.** Read a `test_recipes` row (+ its trigger/recipe bodies, per-tenant) and resolve it into an executable plan. The contract boundary — S4's input is an S2 recipe, never a v1 `TestCaseVersion.steps` blob.
2. **Per-`recipe_kind` executors.** Dispatch on `recipe_kind`: `metadata-recipe` (Metadata/Tooling read + assert — the first vertical), then `data-recipe` (CRUD), `ui-recipe` (browser), `event-subscription-recipe`, `callout-intercept-recipe`. Each kind has a distinct runtime; the executors share the reused mechanical primitives (§3) but own their step semantics.
3. **Environment binding + capability matching.** Bind to a target org (connection / credential, sandbox vs production) and match a recipe's `ExecutionEnvironmentBody` capability assumptions against the env's actual capabilities ("pick the recipe that fits"). v1's connection / OAuth is reused; rich capability-matching is deferred (F5).
4. **The captured-truth result model (F2).** S4-owned, evidence-first (§4).
5. **The S2 posture callback.** After a run, S4 reports **posture** back to S2 (executed / verified / failed / caveated; latest refs; coverage freshness) — never raw evidence (§4).

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

## 4. F2 — the result model (LOCKED philosophy; schema NOT locked; D-108)

**Evidence-first, S4-owned.** A run captures raw observations *richly* and *honestly*: timestamps + ordering, request / response, before / after state, error surfaces, environment context, and per-step outcomes — an **extensible** schema that grows with the first vertical and richer recipe kinds. The schema is deliberately **not locked**: capture breadth expands as recipe kinds land (e.g. browser traces / screenshots for `ui-recipe`).

**Posture, not evidence, crosses to S2.** S4 *determines* the **grounded outcome** (it evaluated the recipe's assertion, §Purpose) and sends it to S2 as a compact **posture**: executed; the grounded outcome (verified / failed / caveated / error); latest version refs; coverage freshness — never the raw evidence. The rich evidence *and* the uninterpreted outcome stay S4-owned; **S6** interprets that evidence — the *why* behind the outcome (classification, root-cause, explanation, clustering) — it does not re-derive the outcome.

**Mine v1 for lessons, not inheritance.** v1's `run_test_results` / `run_step_results` (api_request / response, before / after state, `comparison_details`, `failure_class`, timings, correlation_id) is a rich source of *operational lessons* about what is worth capturing — S4 adopts the lessons, not the schema (which is welded to v1's `expect_fail` / run model).

## 5. First vertical — metadata-inspection (F3, LOCKED)

The first executable recipe is the only kind S3 emits today: the **inspection-trigger + metadata-recipe** (D-099 / D-107). The executor **live-reads the org** (Metadata / Tooling) and **asserts the grounded claim still holds** (e.g. the ValidationRule `APPLIES_TO` the subject is present and active) — an execution-time re-inspection (D-099.3), not a frozen snapshot. It needs no test data (F6) and no browser (F4). It is the thinnest end-to-end vertical that exercises the whole spine: **bridge → executor → evidence capture → posture callback.**

---

## Status

**Phase 0 (foundational design) — opening (2026-05-26).** F1 (reuse boundary), F2 (evidence-first result model), F3 (first vertical = metadata-inspection) locked (D-108); F4–F7 triaged (defer ui / event / callout; minimal capability-match; no test-data until the CRUD increment; S4 captures failure-truth, does not remediate). Next: design + build the metadata-inspection executor + its evidence-first capture.
