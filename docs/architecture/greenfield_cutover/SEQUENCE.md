# Greenfield Cutover — SEQUENCE

The cutover's **ordered, gated steps** (the sequencing law, D-146). Each step has an **entry-gate** (what must hold before it starts), an **exit-gate** (what proves it done), and a **rollback**. The ordering is **reversible-before-irreversible, additive-before-substitutive** — and the one irreversible act, the **`meta_*` drop, is strictly last** and gated on a clean parallel-run window.

This is the order the execution phases follow. **Nothing here is built in Phase 7** (the design phase, D-145/D-146); each step becomes its own future phase. The "work" bullets fold in every "deferred to the cutover" item across the substrate docs — see the coverage table at the end.

> **Invariant.** No step that removes a v1 read-path or drops a table runs before its substrate replacement is proven *additive → flagged → parity-validated*. Steps 0–4 are each reversible; step 5 is not — its entry-gate is the safety.

---

## Step 0 — S1-sync production trigger *(the hard prerequisite)*

Make S1 the *live* metadata source: fire `primeqa/sync/` (`SyncEngine.run_sync`) against live Salesforce per tenant on a refresh cadence, populating `entities`/`edges`/details in the per-tenant schema. **Additive** — v1 still reads `meta_*` throughout; nothing of v1's behaviour changes.

- **Entry-gate:** the S1 query interface + sync writer exist (✓ realized — `primeqa/semantic/query.py`, `primeqa/sync/`); a per-tenant Salesforce connection is available.
- **Work:** wire a production trigger (a scheduler tick and/or an on-demand route) that runs `SyncEngine.run_sync` per tenant + records a refresh cadence + sync-run correlation. *(Carries: the S1-sync prod-trigger gap — the readiness audit's #1 blocker.)*
- **Exit-gate:** `entities`/`edges` populated from live orgs for the pilot tenants; the cadence runs; a parity probe (S1 entity/field counts vs `meta_*`) agrees within tolerance.
- **Rollback:** trivial — S1 is additive; disable the trigger, v1 is untouched.

## Step 1 — Relocations *(zero-risk; independent of step 0)* — ✅ DONE (D-148)

Move S5 to its own top-level package, the one concrete relocation the cutover owns.

- **Entry-gate:** none (pure refactor).
- **Work:** `primeqa/intelligence/knowledge/` → `primeqa/knowledge/`; decide + apply whether `primeqa/intelligence/llm/feedback_rules.py` moves with it; update the live import graph (`test_plan_generation.py`, `generation.py`, the gateway, the tests). *(Carries: S5 relocation + the `feedback_rules` move — D-134.)*
- **Exit-gate:** imports updated; the full suite green; no behaviour change.
- **Rollback:** revert the move.
- **Landed (D-148):** `git mv` (renames preserved) + the import rewrite across the 6 importers; **`feedback_rules.py` STAYED** in `intelligence/llm/` (wrapped via `LearnedRulesProvider` — moving it would churn 13 importers for no gain). A `system_rules.py` `__file__`-relative default-path fix (`..`×3 → ×2 for the up-one-level move) was caught + fixed by the suite. 29 relocation tests green; app import OK.

## Step 2 — Additive substrate consumers *(no v1 removal)*

Make the dormant substrate outputs **visible** alongside v1 — additive UI/read surfaces, nothing removed.

- **Entry-gate:** the substrates produce data (✓ S6/S8 via the run-path + recompute; S3/S4 outputs require step 0 so they ground against live S1).
- **Work:** wire the S6 read API (`read_interpretation` / `list_interpretations`) into a run-detail interpretation view; surface S8 grounding-validity verdicts + the S6 clustering reads (`cluster_*`) in a dashboard/route; show S3/S4 substrate outputs. Settle the **S5→S3 forward-seam** here (the "v1-vs-substrate generation direction settles at the cutover"). *(Carries: S6 UI consumer + always-on trigger — D-137; the clustering release-grain + dashboard consumer — D-137; the S5→S3 forward-seam — D-134.)*
- **Exit-gate:** substrate outputs render in the product (additively), reviewed for parity-of-meaning with the v1 surfaces.
- **Rollback:** hide the additive surfaces.

## Step 3 — v1 read-path switch *(flagged; `meta_*` still populated)*

Switch the v1 metadata *reads* to S1, behind per-tenant flags, with `meta_*` still populated as the fallback — the start of the parallel run.

- **Entry-gate:** step 0 (S1 populated + on a cadence).
- **Work:** route the generation metadata context (`primeqa/intelligence/generation.py`), the validator CRUDQ (`validator.py`), and preflight staleness (`primeqa/runs/preflight.py`) to read S1 entities/attributes when the per-tenant flag is on; `meta_*` remains the flag-off path. *(Carries: the v1 read-path replacement — D-012 / D-003.)*
- **Exit-gate:** flagged reads work + agree with the `meta_*`-sourced reads for the pilot tenants.
- **Rollback:** flip the flag back to `meta_*` (per tenant).

## Step 4 — Parallel-run validation

Run both stacks; prove S1-sourced reads equal `meta_*`-sourced reads over a window; land the remaining additive seams.

- **Entry-gate:** step 3 (the flagged reads live).
- **Work:** measure parity (S1 vs `meta_*`) across generation/validation/preflight over a validation window; fold S6 verdicts into the GO/NO-GO decision (additive); retire the S3 semantic ledger into S2 provenance once `get_provenance` ships (`test_provenance` rows already written; `llm_calls` stays). *(Carries: GO/NO-GO folding — D-111; the S3-ledger retirement — D-074.)*
- **Exit-gate:** a clean parity window — no divergence — across the rollout tenants; the GO/NO-GO + ledger seams landed.
- **Rollback:** extend the window or revert the flags to `meta_*`.

## Step 5 — The `meta_*` drop *(LAST · irreversible)*

Retire v1 metadata: drop `public.meta_*` + the DROP tables in one migration; remove the v1 metadata module + the read-path flags. S1 is the sole metadata source.

- **Entry-gate (the safety):** a clean parallel-run window (step 4) **and** S1 verified as the production data source (D-012).
- **Work:** one migration dropping `meta_versions` / `meta_objects` / `meta_fields` / `meta_validation_rules` / `meta_flows` / `meta_triggers` / `meta_record_types` / `meta_sync_status` + the DROP tables (`test_case_versions`, `requirements`, `metadata_impacts`, D-065); delete `primeqa/metadata/` + the read-path flags; `migrations/` for `meta_*` becomes archive-eligible. *(Carries: the `meta_*` re-sync completion + drop — D-012.)*
- **Exit-gate:** `meta_*` gone; the product runs on the spine; the metadata suite is retired/rewritten to S1.
- **Rollback:** **none past this point** — the entry-gate is the only safety. (Optionally archive `meta_*` as a snapshot for audit before the drop.)

---

## Work-list coverage

Every "deferred to the cutover" item across the substrate docs, mapped to its step — so the scattered deferrals are one tracked checklist (audit: each substrate `DEFERRED_ITEMS.md` "Phase-7 / cutover" line resolves to a row here).

| Deferred item | Source | Step |
|---|---|---|
| S1-sync production trigger (populate `entities` from live Salesforce) | readiness audit / D-012 | **0** |
| S5 relocation `intelligence/knowledge/` → `primeqa/knowledge/` ✅ **done (D-148)** | D-134 | **1** |
| `feedback_rules.py` move (part of the same relocation) | D-134 | **1** |
| S6 user-facing UI/dashboard consumer + always-on trigger | D-137 | **2** |
| S6 clustering release-grain view + dashboard/route consumer | D-137 | **2** |
| S5→S3 generation forward-seam (settles at the cutover) | D-134 | **2** |
| S8 grounding-validity verdict surface | D-143 (implied) | **2** |
| v1 read-path switch → S1 (generation / validator / preflight) | D-012 / D-003 | **3** |
| Folding S6 verdicts into v1's GO/NO-GO | D-111 / D-137 | **4** |
| S3 semantic-ledger retirement → S2 provenance (`get_provenance`) | D-074 | **4** |
| `meta_*` re-sync completion + the `meta_*` / DROP-tables drop | D-012 / D-065 | **5** |

**Explicitly NOT in the cutover** (post-cutover, per the SPEC §5): the MIGRATE tables (`test_suites` / `sections` / `suite_test_cases` / `ba_reviews` → future substrates, D-065); `llm_calls` (stays in S3, D-074); S4 F2/F4–F7 + the S8 mechanics phase.

---

## Status

**Authored Phase 7 (2026-06-03, D-146).** The gated sequence + the consolidated work-list are fixed. Execution is later phases, each running one step against its entry-gate; the `meta_*` drop is the final, irreversible step gated on a clean parallel run.
