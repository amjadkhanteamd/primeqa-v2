# VERIFICATION — Step A, the repair-proposal three-verdict gate

Executed 2026-09-06 on scratch (`plimsol_3a3`; tenant chain upgraded
`20260904_0010 → 20260906_0010`; public `tenant_agent_settings` at
migration 069) with the REAL Flask app for the fixture screenshots.
**No production row was written and no Railway act was performed**
(two read-only prod measurements are cited from the LLD). Branch
`step-a-repair-gate` (design commit `0a04eff` from main @00f8e7d, D-479).
Merge gated — this slice carries ONE tenant migration (ADDITIVE) and ONE
public migration (ADDITIVE + **DESTRUCTIVE**: the two trust thresholds
drop) → **dump-first** at merge per D-476 / D-285. Switch-on is its own
gated act after AK reviews the retro-classification counts and the three
reverts' re-verify outcomes.

Re-runnable: `tests/unit/test_repair_gate.py` (the classifier as a pure
table), `tests/integration/test_repair_gate.py` (the wiring on scratch —
needs `DATABASE_URL` = `S3A3_TEST_DATABASE_URL` = scratch and a
`JWT_SECRET`, because the route tests go through the real app).

---

## The rulings, as implemented

| ruling | where it lives |
|---|---|
| D1 — `rerun` / `regenerate` are DERIVED by construction, `no_recipe_mutation: true` recorded | `repair_gate.classify` K-rules; unit `test_rerun_is_derived…`, `test_regenerate_is_derived…`; DB-real a5 |
| D2 — both trust thresholds dropped | migration 069 (`DROP COLUMN` ×2 + the `trust_bands_sane` CHECK first); `models.py`, `agent_settings.py`, `agent.html` lose them; `_repair_settings` reads no threshold |
| D3 — refused auto-applied edits REVERTED with `gate_retro_revert` provenance; the DERIVED one kept | `repair_gate.revert_refused_auto_applies`; `coordinator.write_recipe(event_context=…)`; DB-real f |
| D4 — R2 includes the sole-active-value clause; two active values → SPECULATIVE | `repair_gate.classify` R2; unit `test_r2_the_sole_active_value…`, `test_r2_two_active_values…`; DB-real g |
| SEMANTIC evaluates first (ratified) | `classify` order; unit `test_touching_an_asserted_field_is_semantic_even_when_r1_would_hold` |
| the auto-apply flag stays OFF through the merge; the new switch defaults OFF | migration 069 default `false`; nothing in this slice flips either flag |

## a. The gate at creation

`triage_new_failures` classifies BEFORE the INSERT
(`repair_agent.py`, the `repair_gate.classify_row` call ahead of the
INSERT that now writes `gate_verdict`, `grounding_source`,
`classified_at`, `classifier_version`). The column is named
**`gate_verdict`** because `repair_proposals.verdict` already holds the
S6 verdict the proposal was triaged from — caught on the first scratch
apply, where `ADD COLUMN IF NOT EXISTS verdict` silently no-op'd and the
CHECK never landed; the migration was corrected before anything left
scratch, and its downgrade drops only the gate columns.

DB-real, planted through the coordinator (a value claim asserting
`Opportunity.Amount`; a data recipe staging qualified keys), the real
triage with the LLM step stubbed to a chosen remedy and the S1 reader
stubbed to planted facts:

- **a1 SEMANTIC**: the remedy removes `Amount`, the asserted field — even
  with S1 attesting R1 — → `SEMANTIC`, `touches_asserted_field`,
  destination `{key: req-302, url: /requirements/302}` resolved from the
  `generated_from` link; the panel read carries the verdict and the
  destination and **no `confidence` key**.
- **a2 SPECULATIVE**: `automation_effect_absent` (the create succeeded,
  no platform error) → `no_platform_error`.
- **a3 DERIVED R1**: `Line_Total__c: __REMOVE__`, the error names the
  field, S1 records `is_createable=false` → grounding `{rule: R1,
  s1_fact: is_createable=false, s1_entity_id, attested_by: error_fields}`.
- **a4 bare staged key** → `SEMANTIC`, `bare_staged_key` (fail closed).
- **a5 K-rerun** → `DERIVED` with `no_recipe_mutation: true`.
- **a6** three shapes in one tick → every row carries a verdict, a
  grounding source and a classifier version. **The tick writes zero
  unclassified rows.**

## b. `agent_enabled=false` gates CREATION

Two ticks with the master switch off → `{proposed: 0, scanned: 0,
disabled: true}` both times, zero rows, exactly ONE warning line (the
loudly-once posture, `_WARNED_DISABLED`).

## c. The refusal is the control

- `decide_proposal(approve=True)` refuses a `SPECULATIVE` row
  (`refused: true`, the verdict named), refuses a `DERIVED` row with an
  empty grounding source, and refuses a grounded `DERIVED` row while the
  switch is OFF ("dormant"). The recipe gains no version in any case.
- Through the real app (superadmin session, CSRF double-submit): an
  apply POST for a SPECULATIVE row lands on the route, is refused, the
  row stays `proposed`, the recipe has one version; the panel renders
  the row with `data-gate-verdict="SPECULATIVE"`, "Open recipe", **no
  "% conf"**, **no "real defects never appear here"**, and the
  verdict-counts header.
- Reject needs no switch (`test_reject_needs_no_switch`).

## d. The auto pass never reads confidence

- A planted `SPECULATIVE` row with `confidence = 0.99`, both flags AND
  the switch ON, sandbox env → `applied 0`, the row stays `proposed`,
  the recipe untouched. The unit twin asserts the SELECT the pass issues
  fetches `gate_verdict` and **not** `confidence`.
- A grounded `DERIVED` row: auto flag ON + switch OFF → dormant (no
  connection opened); switch ON → applied once, `auto_applied = true`,
  the edit landed as recipe version 2 with version 1 preserved.

## e. Retro-classification is idempotent

Two legacy rows planted with NO gate columns (the pre-Step-A shape) →
first `retro` writes both (R2 `matched: default` for the picklist
edit; K-rerun for the rerun), second `retro` writes **zero** and reports
identical counts; a bumped `classifier_version` re-classifies
deliberately.

## f. The D3 revert

A real "edited" recipe version written through the coordinator, an
`applied + auto_applied` row pointing at it with a `SPECULATIVE` retro
verdict, and a `DERIVED` one beside it:

- the SPECULATIVE row → `reverted`: a NEW recipe version whose `steps`
  equal the pre-edit version's (byte-for-byte on scratch, where both
  versions were written by the same schema; on production the create
  step is byte-equal and the read step gains the current schema's
  default `eventual: null` — content-identical, see §m); the provenance event is
  `recipe_s8_rewrite` with `event_data.provenance = "gate_retro_revert"`,
  `proposal_id`, `predicted_verdict = SPECULATIVE`,
  `reverts_version_seq` / `restores_version_seq`; the proposal row
  carries `revert_recipe_version_seq` + `reverted_at`; a re-verify
  `s4_execution_jobs` row exists; one `repair.gate_retro_revert`
  activity_log row;
- the DERIVED row → `kept_derived`;
- a second pass reverts nothing (idempotent on `reverted_at`).

`coordinator.write_recipe` gained the optional trailing `event_context`
kwarg (mirroring the 3A-3 claim-side parameter; `None` keeps every
existing caller byte-identical; the signature-pin test updated to name
it).

## g. The S1 facts reader

A planted org world (a `connected_orgs` row bound to the sandbox env, a
`manual_checkpoint` logical version, `Opportunity` + two Field entities,
`field_details` with `is_createable=false` on one and a picklist set on
the other, two active values with one default): the reader returns
`is_createable=false`, `exists=False` for a ghost field, the active
values `("Home","Personal")` and default `Home` at the org's current
sequence; through the classifier the default value is R2-DERIVED and the
other active value is `chosen_picklist_value` (ruling D4's negative).

## h. The settings page — one home, audited

`/settings/agent` renders `agent_enabled`, `repair_gate_apply_enabled`,
`repair_auto_apply`, `max_fix_attempts_per_run` and **no threshold**;
the llm-usage template carries no `repair_auto_apply` input, only the
link to Settings › Agent; a POST that flips the switch writes exactly
ONE activity_log row (`tenant_repair_gate_apply_enabled`, old/new,
surface) and leaves the other flags untouched.

## i. Fixture screenshots (`step-a-fixtures/`, real browser over the real app on scratch)

| file | shows |
|---|---|
| `repairs_panel_switch_off.png` | the three verdicts in one panel; header counts + "apply actions dormant"; DERIVED → "Apply dormant", SPECULATIVE → "Open recipe", SEMANTIC → "Refused — … Route to requirement req-302" + Dismiss; no percentage |
| `repairs_panel_switch_on.png` | the same panel with the switch ON — only the DERIVED row gains "Approve & apply" |
| `run_detail_card_derived.png` / `run_detail_card_semantic.png` | the run-detail card decided by verdict; the SEMANTIC card names the destination |
| `settings_agent_consolidated.png` | the one home: master switch, the repair gate switch, autonomous apply, the attempt cap; no thresholds |

The panel matches the mock AK approved (verdict column left; action on
the right decided by verdict; no confidence percentage; refusals carry
their destination; header shows verdict counts). No deviation was
needed, so no HOLD was raised.

## j. Suites (D-468) at the implementation commit

- **Unit (the merge gate): 4,992 passed.** One pre-existing pin moved:
  the coordinator signature test now names the appended
  `event_context` kwarg.
- **DB-real: 83 passed, 2 skipped, 1 red across fourteen suites** (the thirteen + this
  slice's fifteen tests). One pre-existing suite adapted:
  `test_scheduler_stale_tenants` patches the settings dict, which now
  carries the switch.
- **Pages: 5 passed. Browser-gated: 63 passed, 11 skipped**
  (`SPIKE_BROWSER=1`).
- The one DB-real red is `test_report_slice.py::test_a_runs_list_carries_both_recorded_runs`,
  a **scratch-replay artefact, not this slice**: the runs list is a
  50-row newest-first window, and scratch now holds 171 processing
  runs of which 56 are newer than B-1's (each replay of the 3A-4 /
  Phase 7 / scheduling suites plants more). The report-slice code is
  untouched by this branch (`git diff main -- ui_report_console.py
  interpretation/ templates/ui_report/` is empty). Its own record
  already noted P-1 falling out of the same window; B-1 has now
  followed. Ledgered for the report slice's owner; not folded in.
- `tests/integration/test_r2_superadmin.py` was updated for the new
  fields (R2-2/3/4) but the script itself is STALE on main independent
  of this slice: it imports `primeqa.runs.cost` (no such module),
  reads `users.preferred_landing_page` and `release_decisions`, and
  needs a Railway password login — on scratch it reports 1/7 (the
  attempt-cap validation passes). Its settings assertions are covered by
  DB-real test h through the real app. Ledgered here, not repaired.

## k. Migrations, classified at pre-flight (for the merge runbook)

| migration | content | class |
|---|---|---|
| tenant `20260906_0010_repair_gate.py` | `repair_proposals` + `gate_verdict` (CHECK), `grounding_source`, `classified_at`, `classifier_version`, `revert_recipe_version_seq`, `reverted_at`; index `(status, gate_verdict)`; column comments | ADDITIVE |
| public `069_repair_gate_settings.sql` | `+ repair_gate_apply_enabled DEFAULT false` — before the deploy | ADDITIVE |
| public `070_repair_gate_drop_thresholds.sql` | `DROP CONSTRAINT trust_bands_sane`; `DROP COLUMN trust_threshold_high, trust_threshold_medium` — after the deploy | **DESTRUCTIVE → dump-first** |

The split closes the ORM window in BOTH directions (found at the merge
pre-flight, 2026-09-07): the old ORM maps the two thresholds and the LLM
gateway (`limits.load_tenant_config`) loads the settings row per call,
so dropping them under old code would fail every LLM call for the
deploy's duration; the new ORM maps the switch, so booting new code
before 069 would fail the same reads. Additive before, destructive after
— no running process ever selects a column that is not there.

## l. Deploy-day sequence (unchanged from the LLD §f)

dump → 069 (additive) → tenant 20260906_0010 → read-backs → merge → four
services → 070 (the destructive drop) → read-backs →
`python -m primeqa.intelligence.repair_gate retro --tenant-id 1` →
`… revert --tenant-id 1 --user-id 1` (the D3 act; re-verify jobs
enqueued; reds accepted as honest) → the counts table + the three
reverts' re-verify outcomes to AK → HOLD → switch-on GO.

## Residual, stated plainly

- Successor-field derivation is not buildable until S1 records lineage;
  every rename/add remedy is SPECULATIVE in v1.
- S1 facts are read at the org's CURRENT sequence (the run stamps no org
  sequence — Phase 2); the grounding records `s1_seq` + `s1_as_of`.
- `automation_effect_*` proposals (19 of 27 on prod) can never be DERIVED
  with today's inputs — no platform error exists when the create
  succeeds.
- `gate_verdict` stays nullable until every tenant has run retro; the
  `NOT NULL` tightening is a follow-up.
- The llm-usage page needs tables scratch does not carry
  (`llm_usage_log`, `llm_models`), so its "no checkbox" assertion is on
  the template source.


## m. Production transcript (2026-09-07, GO #1 + GO #2)

| act | record |
|---|---|
| dump | `prod_pre_stepA_20260907_004151.dump`, 181 MB, 1,121 entries |
| 069 (additive) | `repair_gate_apply_enabled` added, default false |
| tenant `20260906_0010` | six gate columns + CHECK + index + comments; S6 `verdict` intact; head read back |
| merge | `ebb354f` (parents `00f8e7d` + `f018a61`); four services SUCCESS; pre-merge deployments REMOVED |
| scheduler leg | 0 errors before and ~75 s after 070; no new failures to triage (0 rows written, 0 unclassified) |
| web tier | `/settings/agent` 200 (switch input present, no threshold text); `/runs/substrate` 200 (verdict-counts header, 50 UNCLASSIFIED rows in the window, **0 apply forms**, old header gone) |
| 070 (destructive) | `trust_bands_sane` + both thresholds gone; settings row intact (agent on, auto-apply OFF, switch OFF, cap 2) |
| retro | 132 scanned / 132 written; second pass 0 written, identical counts |
| revert | 95189, 95195, 465221 reverted (all SEMANTIC); 347418 kept (DERIVED R1) |
| hygiene | 0 secret-pattern hits over grounding, provenance, audit rows; 0 unclassified rows |
| deltas | claims 838 → 838; recipes 1,006 → 1,009; s4 jobs 632 → 635; provenance 3,700 → 3,703 |

**The counts (all 132 rows):**

| status | kind | DERIVED | SPECULATIVE | SEMANTIC |
|---|---|---|---|---|
| applied | recipe_edit | 1 | 2 | 3 |
| applied | regenerate_from_current_org | 1 | – | – |
| applied | rerun | 47 | – | – |
| proposed | recipe_edit | **0** | 15 | 6 |
| proposed | regenerate_from_current_org | 3 | – | – |
| proposed | rerun | 54 | – | – |

Open recipe-edit reasons: 14 `no_platform_error`, 1
`value_not_recorded_in_picklist` (the "Mortgage" specimen — the
picklist records Home / Personal / Business), 6 `touches_asserted_field`.
S1 read at sequence 249 (env-59's org) for 26 rows and 248 (env-78's)
for 1. **Zero DERIVED among the open recipe edits**: switch-on makes no
recipe edit applicable today; only reruns and regenerates would gain an
apply action.

**The six applied recipe edits:**

| id | auto | verdict | grounding | remedy | disposition |
|---|---|---|---|---|---|
| 146 | human | SPECULATIVE | no_platform_error | `Status__c = Open` | stays (human-applied) |
| 147 | human | SPECULATIVE | no_platform_error | `Status__c = Escalated` | stays (human-applied) |
| 95189 | machine | SEMANTIC | touches asserted `loan_type__c` | drop `Loan_Type__c` | **reverted** → recipe v3 = v1, job 682 |
| 95195 | machine | SEMANTIC | touches asserted `loan_type__c` | drop `Loan_Type__c` | **reverted** → v3 = v1, job 683 |
| 347418 | machine | DERIVED | R1: S1 `is_createable=false` | drop `PLS_FB_Line_Total__c` | **kept** |
| 465221 | machine | SEMANTIC | touches asserted `pls_bm_deal_value__c` | `PLS_BM_Deal_Value__c = 1000` | **reverted** → v3 = v1, job 684 |

465221 classified SEMANTIC, sharper than the LLD's SPECULATIVE
prediction: the chosen value lands on a field the claim asserts.

**The reverts, verified:** each recipe now holds versions 1 (approved),
2 (the machine edit, `generated_unapproved`), 3 (the restore, current).
Version 3's create step equals version 1's byte-for-byte (the removed
`Loan_Type__c` is back; the invented `1000` is gone); the read step
differs only by the current schema's default `eventual: null`; the
assert step is equal. Provenance: `recipe_s8_rewrite` with
`gate_retro_revert`, the proposal id, the predicted verdict,
`restores_version_seq = 1`. Three `repair.gate_retro_revert` audit rows.

**The re-verify outcomes — a finding, reported plainly:** jobs 682–684
completed within ~15 s each as `ran=False, outcome=no_eligible_recipe`.
No run, no red, no green. Two recorded causes: all three claims are
**deprecated**, and every s8-written recipe version is
`generated_unapproved` (`coordinator.py:945`), which the executor never
selects (`run.py:173`). The same is true of July: the four auto-applies'
own re-verifies produced **zero** runs at version 2. The D-236
"apply → re-verify" has been a silent no-op since it shipped. Ledgered
in PLIMSOL_FIX_PLAN.md ("Added 2026-09-07"); not folded into Step A.
