# Greenfield Cutover — SEQUENCE

The cutover's **ordered, gated steps** (the sequencing law, D-146). Each step has an **entry-gate** (what must hold before it starts), an **exit-gate** (what proves it done), and a **rollback**. The ordering is **reversible-before-irreversible, additive-before-substitutive** — and the one irreversible act, the **`meta_*` drop, is strictly last** and gated on a clean parallel-run window.

This is the order the execution phases follow. **Nothing here is built in Phase 7** (the design phase, D-145/D-146); each step becomes its own future phase. The "work" bullets fold in every "deferred to the cutover" item across the substrate docs — see the coverage table at the end.

> **Invariant.** No step that removes a v1 read-path or drops a table runs before its substrate replacement is proven *additive → flagged → parity-validated*. Steps 0–4 are each reversible; step 5 is not — its entry-gate is the safety.

---

## Step 0 — S1-sync production trigger *(the hard prerequisite)* — ✅ BUILT (D-150–D-153; live-proving → ops)

Make S1 the *live* metadata source: fire `primeqa/sync/` (`SyncEngine.run_sync`) against live Salesforce per tenant on a refresh cadence, populating `entities`/`edges`/details in the per-tenant schema. **Additive** — v1 still reads `meta_*` throughout; nothing of v1's behaviour changes.

- **Entry-gate:** the S1 query interface + sync writer exist (✓ realized — `primeqa/semantic/query.py`, `primeqa/sync/`); a per-tenant Salesforce connection is available.
- **Work:** wire a production trigger (a scheduler tick and/or an on-demand route) that runs `SyncEngine.run_sync` per tenant + records a refresh cadence + sync-run correlation. *(Carries: the S1-sync prod-trigger gap — the readiness audit's #1 blocker.)*
- **Exit-gate:** `entities`/`edges` populated from live orgs for the pilot tenants; the cadence runs; a parity probe (S1 entity/field counts vs `meta_*`) agrees within tolerance.
- **Rollback:** trivial — S1 is additive; disable the trigger, v1 is untouched.
- **Landed (D-150–D-153):** the trigger is **built + governance-tested, the engine untouched** (it was finished-but-dormant — zero prod callers). **S0.1 (D-150)** — `connected_orgs.environment_id` (the missing env→sync-target link) + `resolve_sync_sf_client` (reuses v1's `_oauth_token`, pre-seeding the engine's refresh-token-grant client via an additive `access_token` param — the grant mismatch resolved) + `ensure_connected_org_for_environment`. **S0.2 (D-151)** — the `s1_sync_jobs` per-tenant queue + `SyncJobStore` (mirrors `s4_execution_jobs` minus the attempts table; `last_sync_run_id` resume anchor; 45-min reaper). **S0.3 (D-152)** — the consumer (claim → `resolve_sync_sf_client` → `run_sync` → complete/fail via `sync_runs.status`, since the engine captures phase failure in the row); **resume-on-reap** wired (carry-forward seeds the anchor from the org's incomplete `sync_run`; `run_sync(resume_sync_run_id=…)`). **S0.4 (D-153)** — the enqueuer cadence (24 h + prompt resume) + the scheduler ticks (`s1_sync_enqueuer_tick`/`s1_sync_reaper_tick`) + the worker consumer tick (`s1_sync_tick`). Additive: 2 tenant migrations (`20260604_0010`/`0020`) + new `primeqa/sync/{credentials,jobs,consumer}.py` + thin scheduler/worker wiring; **no engine/phase edits.** 29 governance tests (stubbed SF + a fake engine); app/worker/scheduler import. **The exit-gate's live-SF half — `entities`/`edges` from a real org + the `meta_*` parity probe — is ops-deferred** (the `@pytest.mark.sandbox` e2e suites; needs SF creds + ~30 min). So Step 0 lands **built + governance-tested**; S1-goes-live is the ops run.

## Step 1 — Relocations *(zero-risk; independent of step 0)* — ✅ DONE (D-148)

Move S5 to its own top-level package, the one concrete relocation the cutover owns.

- **Entry-gate:** none (pure refactor).
- **Work:** `primeqa/intelligence/knowledge/` → `primeqa/knowledge/`; decide + apply whether `primeqa/intelligence/llm/feedback_rules.py` moves with it; update the live import graph (`test_plan_generation.py`, `generation.py`, the gateway, the tests). *(Carries: S5 relocation + the `feedback_rules` move — D-134.)*
- **Exit-gate:** imports updated; the full suite green; no behaviour change.
- **Rollback:** revert the move.
- **Landed (D-148):** `git mv` (renames preserved) + the import rewrite across the 6 importers; **`feedback_rules.py` STAYED** in `intelligence/llm/` (wrapped via `LearnedRulesProvider` — moving it would churn 13 importers for no gain). A `system_rules.py` `__file__`-relative default-path fix (`..`×3 → ×2 for the up-one-level move) was caught + fixed by the suite. 29 relocation tests green; app import OK.

## Step 2 — Additive substrate consumers *(no v1 removal)* — ✅ BUILT (additive surface, D-155/D-156; run-detail graft + release-grain → Steps 3–4)

Make the dormant substrate outputs **visible** alongside v1 — additive UI/read surfaces, nothing removed.

- **Entry-gate:** the substrates produce data (✓ S6/S8 via the run-path + recompute; S3/S4 outputs require step 0 so they ground against live S1).
- **Work:** wire the S6 read API (`read_interpretation` / `list_interpretations`) into a run-detail interpretation view; surface S8 grounding-validity verdicts + the S6 clustering reads (`cluster_*`) in a dashboard/route; show S3/S4 substrate outputs. Settle the **S5→S3 forward-seam** here (the "v1-vs-substrate generation direction settles at the cutover"). *(Carries: S6 UI consumer + always-on trigger — D-137; the clustering release-grain + dashboard consumer — D-137; the S5→S3 forward-seam — D-134.)*
- **Exit-gate:** substrate outputs render in the product (additively), reviewed for parity-of-meaning with the v1 surfaces.
- **Rollback:** hide the additive surfaces.
- **Landed (D-155 / D-156).** The standalone **`/substrate-insights`** page (the first v1→substrate read bridge, `get_substrate_insights`) surfaces S6 interpretations + cross-run clustering + S8 grounding-validity, additively + best-effort, with empty-states (the stores are empty until Step 0's live-SF run lands data); 35 governance tests. The **S5→S3 forward-seam** was settled — **not wired** (D-156: v1 generation stays as the product path; S3 parallel + downstream-only). **Deferred to Steps 3–4 (a verified premise break):** the **v1-run-grafted S6 panel** + the **release-grain clustering** both need a v1↔substrate run correlation the data model lacks — the two execution worlds are disjoint (no shared key, no write-path), and unifying them is the flagged read-switch / parallel-run, not additive Step 2. The live data + the parity-of-meaning review are ops-deferred (the live half, like Step 0).

## Step 3 — v1 read-path switch *(flagged; `meta_*` still populated)* — ✅ BUILT (generation + validator/linter on S1, D-158–D-162; preflight → Step-5 prereq)

Switch the v1 metadata *reads* to S1, behind the per-tenant `cutover_read_s1` flag, with `meta_*` still populated as the fallback — the start of the parallel run.

- **Landed (D-158–D-162).** A single flag-gated seam — **`MetadataAccessor`** (`primeqa/metadata/accessor.py`; `cutover_read_s1` flag, migration `051`) — routes v1's metadata reads to **`MetadataS1Reader`** (`primeqa/metadata/s1_reader.py`, eager-hydrated through the S1 query interface) when on, `meta_*` when off (best-effort: empty/error S1 → `meta_*`, never raises; so flag-on-but-S1-empty degrades safely in the parallel-run window). **Generation** context (3.2, D-159) + the **validator + linter** CRUDQ (3.4, D-161) read S1; the first sync-engine touch added **`field_details.is_createable`/`is_updateable`** (3.3, D-160, migration `20260604_0030`) so `field_not_createable`/`field_not_updateable` reach parity, and the picklist 2-hop reaches `picklist_value_not_allowed`. A **mid-impl premise break** (D-161.1) was caught + root-caused: S1 stores a field's `sf_api_name` object-qualified (`Account.Name`) where v1 + every test step use bare (`Name`) — the reader now strips the prefix so the validator's field lookup matches (and the 3.2 "parity" test, which had only asserted the reader's self-consistent output, was corrected). 128 governance tests (semantic reader + validator-parity + accessor/mapper units); flag-off → **zero v1 behaviour change**.
- **Entry-gate:** step 0 (S1 populated + on a cadence).
- **Work:** generation context (`primeqa/intelligence/generation.py`) + the validator/linter CRUDQ (`validator.py`) routed to S1 behind the flag; `meta_*` remains the flag-off path. **Preflight staleness (`primeqa/runs/preflight.py`) stays on `meta_*`** — `MetaSyncStatus` per-category health + `MetaVersion.completed_at` have no clean S1 map (S1's model is `sync_runs` phases), and `meta_*` is populated through Steps 3–4 so preflight reads correctly; its S1 cutover is relocated to a **Step-5 prerequisite** (GAP-2). *(Carries: the v1 read-path replacement — D-012 / D-003.)*
- **Exit-gate:** flagged reads work + agree with the `meta_*`-sourced reads for the pilot tenants. *(Built + governance-tested now; the live dual-stack byte-parity probe — real-org S1 vs `meta_*` — is ops-deferred with #119, the same live half as Steps 0/2.)*
- **Rollback:** flip `cutover_read_s1` back to `meta_*` (per tenant).

## Step 4 — Parallel-run validation

Run both stacks; prove S1-sourced reads equal `meta_*`-sourced reads over a window; land the remaining additive seams.

- **Entry-gate:** step 3 (the flagged reads live).
- **Work:** measure parity (S1 vs `meta_*`) across generation/validation/preflight over a validation window; fold S6 verdicts into the GO/NO-GO decision (additive); retire the S3 semantic ledger into S2 provenance once `get_provenance` ships (`test_provenance` rows already written; `llm_calls` stays). *(Carries: GO/NO-GO folding — D-111; the S3-ledger retirement — D-074.)*
- **Exit-gate:** a clean parity window — no divergence — across the rollout tenants; the GO/NO-GO + ledger seams landed.
- **Rollback:** extend the window or revert the flags to `meta_*`.

> **Step 5 was split into 5a / 5b (D-194).** The original Step 5 bundled the `meta_*` metadata
> drop with the v1 product-table drop (`test_case_versions` / `requirements` / `metadata_impacts`,
> D-065). The reader audit found these have wildly different readiness: every `meta_*` reader is
> retired (5a is ready), but the v1 test-authoring + **execution** + agent-repair flow still runs on
> `test_case_versions` (5b is a substrate-execution program, not a reader re-point). Decoupled below.

## Step 5a — The metadata READ cutover *(✅ DONE — D-195.1–.4)*

S1 becomes the sole metadata **read** source. **Re-scoped at 5a.3 (D-195.3):** the *physical* `meta_*`
table drop + the `primeqa/metadata/` module deletion were found **5b-coupled** — `meta_versions` is the
target of a **live NOT-NULL FK** `test_case_versions.metadata_version_id` (runtime-populated on every TCV
insert) + `environments.current_meta_version_id`, and the `MetaVersion` model must stay registered for
that FK to resolve. So `meta_versions` / the model / `MetadataRepository` retire **with
`test_case_versions` (5b)**, not here. 5a delivers the read cutover + the flag retirement + the dead
impact-table drop.

- **Entry-gate — MET (tenant 1):** clean parity window (D-190) · S1 verified as the production read
  source (D-192) · GAP-2 preflight off `meta_*` (D-192) · v1 metadata-sync writer retired (D-193) · the
  S1 read-bridge relocated out of `primeqa/metadata/` (D-191).
- **Delivered:**
  - **5a.1 (D-195.1)** — every live metadata reader S1-only (`MetadataAccessor` S1-only; picker +
    `StepValidator` + generation/validator on S1; `label` added to the S1 reader).
  - **5a.2 (D-195.2)** — the metadata-impact subsystem removed (`/impacts` UI + `MetadataImpact` /
    `ReleaseImpact` models + repo + the RiskEngine impact-scoring + the GO/NO-GO impacts criterion +
    the dead `metadata_bp`); test-plan ranking preserved.
  - **5a.3 (D-195.3)** — retire the `cutover_read_s1` flag: `check_drift` S1-only **in place**
    (`metadata/service.py`; drop the flag gate + the `meta_*` anchor fallback — its live-SF Tooling
    probes are source-agnostic and stay) + delete `cutover_read_s1_enabled` + the
    `TenantAgentSettings.cutover_read_s1` attr (DB column left inert) + the 1 unused module-scope import.
  - **5a.4 (D-195.4 · ✅ APPLIED)** — `migrations/052` dropped the two **fully code-dead** tables
    `release_impacts` + `metadata_impacts` (writers retired D-193; all readers removed in 5a.2) on the
    Railway prod DB; archived first; `meta_versions`/`test_case_versions` verified untouched.
- **Exit-gate:** S1 is the sole metadata **read** source; the `cutover_read_s1` flag is gone; the two
  dead impact tables are dropped.
- **Deferred to 5b (with `test_case_versions`):** the `meta_versions` / `meta_objects` / `meta_fields` /
  `meta_validation_rules` / `meta_flows` / `meta_triggers` / `meta_record_types` / `meta_sync_status`
  table drop + the `metadata_version_id` FK relaxation; the `_oauth_token` + `check_drift` relocations
  out of `primeqa/metadata/`; the canonical-`SalesforceClient` `query_tooling` addition; and the
  deletion of `primeqa/metadata/{models,repository,service,worker_runner,sync_engine}.py`.
- **Rollback:** 5a.1–5a.3 are reversible (git revert). Past the 5a.4 migration there is no rollback —
  archive-first is the only safety.

## Step 5b — The v1 product-table drop: `test_case_versions` / `requirements` *(DEFERRED · substrate-execution program)*

**Finding (D-194):** the v1 test-authoring + **execution** + agent-repair flow is still the live product
and runs on `test_case_versions` — the executor reads `TestCaseVersion.steps` to run against Salesforce
(`worker.py:_run_execute_stage`); the agent fix-and-rerun loop writes new versions; the validation gate
reads `validation_report`. The substrate spine (S2 claims / S3 generation / S4 execution / S6 / S8)
exists and UI Areas 2–5 (D-166–173) added **parallel, mostly-read** surfaces, but there is **no
substrate execution / repair / validation path yet**. So retiring these tables is **not a reader
re-point — it is replacing the v1 execution engine with the S4/S3 spine + a data backfill + a dual-run
cutover.**

- **Entry-gate:** Step 5a done **and** S4 execution at v1 parity **and** every v1
  `requirements`/`test_case_versions` reader retired (the ~28 views/template sites + the release/runs
  resolvers + the intelligence/agent/validation core + the ~17-FK web).
- **Phased (gated on S4 maturing):** A — S4 execution at v1 parity (run S3 recipes, write results);
  B — agent fix-and-rerun + the validation gate on the spine; C — re-point the v1 reader surfaces (test
  library, reviews, `/impacts`→S8, run history/detail, the `/run` 4-mode picker → S4, release
  test-plan); D — backfill v1 `test_case_versions` → S3 recipes; E — dual-run flagged → cutover → drop
  `test_case_versions` + `requirements` + the FK web (`run_test_results`, `agent_fix_attempts`,
  `ba_reviews`, `test_case_data_bindings`, `release_*`, `generation_batches`, `llm_usage_log`,
  `generation_quality_signals`).
- **Not near-term** — substrate-roadmap scope, not Step-5-prep.
- **Rollback:** none past the drop.

---

## Work-list coverage

Every "deferred to the cutover" item across the substrate docs, mapped to its step — so the scattered deferrals are one tracked checklist (audit: each substrate `DEFERRED_ITEMS.md` "Phase-7 / cutover" line resolves to a row here).

| Deferred item | Source | Step |
|---|---|---|
| S1-sync production trigger (populate `entities` from live Salesforce) ✅ **built (D-150–D-153; live-proving → ops)** | readiness audit / D-012 | **0** |
| S5 relocation `intelligence/knowledge/` → `primeqa/knowledge/` ✅ **done (D-148)** | D-134 | **1** |
| `feedback_rules.py` move (part of the same relocation) | D-134 | **1** |
| S6 user-facing UI/dashboard consumer ✅ **standalone surface landed (D-155)**; run-detail graft + always-on trigger → Steps 3–4 | D-137 | **2** |
| S6 clustering ✅ **surfaced tenant-wide (D-155)**; release-grain view → Steps 3–4 (needs the v1↔substrate run key) | D-137 | **2** |
| S5→S3 generation forward-seam ✅ **settled — not wired (D-156)** | D-134 | **2** |
| S8 grounding-validity verdict surface ✅ **landed (D-155)** | D-143 (implied) | **2** |
| v1 read-path switch → S1: generation + validator/linter ✅ **built (D-158–D-162)**; preflight → Step 5 (GAP-2 — no clean S1 freshness map yet) | D-012 / D-003 | **3** |
| Folding S6 verdicts into v1's GO/NO-GO | D-111 / D-137 | **4** |
| S3 semantic-ledger retirement → S2 provenance (`get_provenance`) | D-074 | **4** |
| `meta_*` drop (the 8 metadata tables + the FK web) ✅ **prereqs met (D-189–193); decoupled (D-194)** | D-012 / D-065 | **5a** |
| v1 product-table drop (`test_case_versions` / `requirements`) — substrate-execution program, gated on S4 parity (D-194) | D-065 | **5b** |

**Explicitly NOT in the cutover** (post-cutover, per the SPEC §5): the MIGRATE tables (`test_suites` / `sections` / `suite_test_cases` / `ba_reviews` → future substrates, D-065); `llm_calls` (stays in S3, D-074); S4 F2/F4–F7 + the S8 mechanics phase.

---

## Status

**Authored Phase 7 (2026-06-03, D-146).** The gated sequence + the consolidated work-list are fixed. Execution is later phases, each running one step against its entry-gate; the `meta_*` drop is the final, irreversible step gated on a clean parallel run.
