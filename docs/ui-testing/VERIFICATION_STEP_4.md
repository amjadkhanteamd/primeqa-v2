# VERIFICATION — Step 4, the Run Planner

**Branch** `step-4-run-planner` from `main` @ fb59095. Design b11a67f (GO
2026-09-09 with rulings 1–4). Build: this commit. Merge gated on AK seeing the
fixture screenshots (`step-4-fixtures/`, eleven, from all three entry points).

## 0. Corrections found at build (folded into the LLD)

1. **A failing recipe is named even when its claim survives.** The first
   planner dropped a recipe that failed S4's shape check silently when another
   recipe of the same claim passed; it now records a recipe-level
   `unexecutable_shape` exclusion beside the claim-level one. Found by the
   harness test.
2. **The plan id rides only when a plan exists.** Every finalize / persist call
   site passes `plan_id` conditionally, so the many injected test fakes keep
   their old signatures; the sync run entries (`run_recipe_execution`,
   `run_all_recipes_execution`) needed the parameter too — the unit suite
   found both.
3. **Best-effort writes on savepoints.** The planner's audit row and the pin
   read run under savepoints: the tenant-only harness has no
   `public.activity_log`, and a failed INSERT aborts the whole transaction
   even when Python catches it (the Step 3 lesson, applied).
4. **The legacy `enqueue=` seam stays for the pre-Step-4 schedule tests
   only.** Production passes nothing and takes the plan path; a test that
   injects `enqueue=` keeps the old every-approved-claim behaviour. Stated in
   the firer's docstring.
5. **Jinja: `c.keys` on a dict is the method, not the item** — the plan view
   reads `c['keys']`. Found by the shoot (a 500 on the first plan view).
6. **The CSRF hidden field is empty on a session's very FIRST render** (the
   cookie is minted on that same response); a plain form POST from that first
   page fails 403 until any second navigation. Pre-existing, not Step 4's;
   the shoot warms the cookie; ledgered (FIX PLAN Low).
7. **A key created through the requirement service is classified at create**
   (Step 1's runtime establish); a planted "fixture" requirement therefore
   read `jira` until the Step 1 recorded override set it — the honest path,
   used by the shoot. Not a planner matter.
8. **Four `test_stranded_cleanup` reds on the local harness are pre-existing**
   (the reaper resolves clients through `public.environments`, which the
   harness lacks) — 4 failed on `main` @ fb59095 in a clean worktree too.
   Ledgered (FIX PLAN Low).

## a. THE PLANNER (harness: `test_step_4_planner.py`, 8; scratch: `test_step_4_scope.py`, 3)

| item | proven |
|---|---|
| claims by kind with reasons | requirement scope on env 59: one kind (`data_behavior / value-claim`), the admitted claim carries its keys, link kind (`generated_from`), origins and its two recipes (data + metadata); every exclusion named: `no_eligible_recipe`, `claim_not_approved`, `claim_deprecated`, and env 78 as `env_not_target` with `is_production`, `read_only`, `inactive` on the exclusion |
| hidden origins | a fixture-origin key's claim is excluded `origin_fixture`; `include_hidden` admits it |
| the target's policy as exclusions | on a `read_only` target the data recipe is excluded `read_only_target_admits_inspection_only` and the pair carries the metadata recipe only; a `disabled` target admits nothing (`env_disabled`); an inactive target admits nothing (`env_inactive`) |
| "Run this plan" executes exactly the recorded row, once | the injected enqueue receives exactly the plan's `(test_id, environment_id)` set with `created_by = 7` and the plan id; the row records the execution; a second execution refuses `plan_already_executed` and enqueues nothing; UPDATE of the resolution, UPDATE of `executed_by` after execution, and DELETE all raise at the table |
| the tenant-wide plan | every approved executable claim on the environment, fixtures excluded with the reason — the successor of "Run all approved", recorded |
| the schedule scope (ruling 2) | a schedule with `created_by` and `authorised_by` NULL → `PlanRefused(no_authorising_user)`, **no row written**; after a claim (`authorised_by = 5`) the plan resolves as the tenant-wide template, `planned_by` NULL, `authorised_by` 5, `legacy: true` |
| release targets | declared with actor, idempotent, removal a state change with actor / time / reason, history readable, DELETE refused |
| the conformance lane (Step 3 consumed) | one declared surface of two → personas `[customer]`, one conformance manifest naming the active set, `surface_keys = [A]`, `excluded_surfaces = [B]`, two checks; the other surface excluded `surface_not_declared`; execution hands the filter to the UI enqueue and makes NO S4 job |
| release scope on scratch (public tables) | no target declared → `targets_source = fallback:evidence-active`, env 5901 named, a phantom evidence env excluded `env_not_target`; declare 5901 → `declared`, a NEW plan row; remove → the next plan falls back again; the audit trail names declare / remove and three plan creates |
| the manifest filter through the REAL builder | over an approved scratch set with two surfaces: the manifest's `surfaces` names ONE, `scope.excluded_surfaces` the other, `claim_set_id` unchanged (membership by reference; no new set, no approval) |
| the schedule tick end to end | a schedule without authority: `fire_due_schedules` refuses it (`refused` in the return, `last_refusal` "no authorising user — claim this schedule" on the row, an `s4.schedule.refused` audit row, zero `run_plans`); after `claim(user 1)`: the tick PLANS (`planned_by` NULL, `authorised_by` 1), executes THAT plan, `last_plan_id` on the row, the S4 jobs carry the plan id — count == the execution's un-attached jobs |

## b. EXCLUSIONS, c. SEAMS

Fifteen reason codes with sentences (`planner.REASON_SENTENCES`); the plan
view's Excluded line reads the counts by reason and expands to the list.
Four seam lines rendered verbatim ("not yet — every scoped item" ×2, "every
declared persona", "every eligible recipe").

## d. ENTRY POINTS (the real app on scratch; `shoot_step_4.py`)

| screen | observed |
|---|---|
| `release_decision_tab_targets_and_plan.png` | the decision tab: **Plan** beside Evaluate; the Target environments block with "No target declared — a plan resolves against the active environments that hold evidence… Declare a target to make it a decision" and the declare form |
| `plan_view_release_fallback_targets.png` | Plan → the PLAN view: "not yet run"; What runs (1 kind, 3 claims at that moment); Target environments **(no target declared — the active environments holding evidence)** `data-targets-source=fallback:evidence-active`; Personas "No declared surface on this scope — no conformance lane"; Manifests & pins; Excluded; the four "not yet" seams |
| `release_targets_declared.png` | after Declare: env 5901 listed "declared by AK Scratch · 2026-09-09" with Remove |
| `plan_view_release_declared_target.png` | re-plan: Target environments **(declared on the release)**, pin "seq — (org_unbound)" stated honestly (scratch's env has no org); **Excluded (1 item: 1 origin fixture)** naming the fixture claim and its requirement key; 2 claims · 2 jobs |
| `run_this_plan_confirm.png` | the kit's confirm: "Run this plan? 2 substrate jobs will be queued exactly as shown. Excluded items stay excluded." |
| `plan_view_after_run.png` | "run · by AK Scratch · 2026-09-09T12:32 · 3 jobs"; the button disabled with "already run — plan again to run again" |
| `requirement_plan_button.png` / `plan_view_requirement.png` | the requirement page's **Plan 2 tests** (env select) → "Requirement S4-DEMO-1 · planned by AK Scratch"; 2 claims |
| `schedules_panel_claim_this_schedule.png` / `schedules_panel_claimed.png` | the D-214 panel: "plan: every approved test on gate sandbox (legacy template)", **no authorising user** in red, **Claim this schedule** → "authority: user 7"; the toggle and claim each write an `s4.schedule.<action>` audit row (ruling 4) |
| `run_tests_retired_redirect.png` | `/run` → `/releases?from=run` with the flash "Run Tests has moved: plan a run from a release (the decision tab) or a requirement, look at the plan, then run it." |

`/run` GET and POST redirect; the POST enqueues nothing (`test_step_4_pages.py`,
2). The Railway-DB page test `tests/test_run_tests_page.py` now asserts the
retirement redirect.

## e. ACTOR SEMANTICS

`planned_by` (person) / `authorised_by` (a schedule's borrowed authority) /
`executed_by` — recorded on every plan; the S4 jobs' `created_by` is the
executor (NULL for the tick, whose plan carries `authorised_by`); the UI
trigger carries `plan_id`, `executed_by`, `authorised_by` and the schedule id.

## f. Suites (D-468) at the implementation commit

- **unit: 5,039 passed** (the three `.env`-reading live-parity tests included; the execution-engine unit suite 461 green with the plan-id thread).
- **test_representation (local PG): 429 passed, 3 skipped, 4 deselected** (421 before + the eight Step 4 harness tests).
- **DB-real corpus on clean scratch: 110 passed, 7 skipped, 1 red** across the twenty DSN-gated files (the new `test_step_4_scope.py` included) + `test_scheduler_stale_tenants.py`, with DATABASE_URL / S3A3 / S5 on scratch and the test JWT secret. The red is the report-slice runs-list window artefact ledgered at D-480, not this slice.
- **Pages: 7 passed** (the five report pages + the two Step 4 pages, one invocation). **Browser-gated: 63 passed, 11 skipped** (SPIKE_BROWSER=1).
- S4 integration on the local schema (`tests/integration/execution_engine`):
  39 passed, 4 failed — the four pre-existing `test_stranded_cleanup` reds
  (§0 item 8); the job / consumer / intake tests green with the plan-id thread.

## g. Migration, classified (for the merge runbook)

`20260911_0010` — **ADDITIVE**: two new tables (`run_plans`, `release_targets`)
with their triggers, three nullable columns on `s4_execution_jobs`,
`s4_execution_runs` and six on `s4_run_schedules`; no data write; DROPs in
the downgrade only. Dumpless under D-476. Reader window: old code ignores
the nullable columns (its INSERTs omit them); new code before the migration
would fail at enqueue / persist on `plan_id` and on every plan route —
migration first (D-285), as every step.

## Residual, stated plainly

- The pin on scratch reads `org_unbound` (env 5901 has no connected org);
  on production the pin is the org's current sequence.
- The nav still says "Run Tests" (the slot is Step 6's); the route redirects.
- The first-render CSRF field (§0 item 6) and the harness's missing
  `public.environments` for the reaper tests (§0 item 8) are ledgered.
- Schedule 1 on production stays enabled; under ruling 2 its next fire
  REFUSES until AK claims it on the panel at the merge.

## h. Production transcript (2026-09-09, GO #1 + GO #2)

| leg | observed |
|---|---|
| pre-flight trees | branch @ d5fd9eb clean; `main` = origin/main = fb59095, delta zero; branch 2 ahead |
| pre-flight prod probes (read-only) | neither new table; 0 of the 8 plan columns; tenant head `20260910_0010`; schedule 1 enabled, creator NULL, no authority column yet, **last fired 06:01:33Z today** (its normal cron under the old code — a second plan-less batch; 568 plan-less stamped runs since AK's re-enable, valid evidence); release 16 targets undeclared by construction; 0 queued jobs |
| classification | **ADDITIVE, dumpless (D-476)**: one alembic file — 2× CREATE TABLE IF NOT EXISTS, 8× ADD COLUMN IF NOT EXISTS, 3 functions, 4 triggers, 4 indexes, 2 comments; every DROP the downgrade or an idempotent re-create; zero SQL data-write statements added outside the planner / schedule store / console and their audit rows (the one grep hit is a Python `dict.update`); no backfill |
| the window, proven (reads vs writes) | migration first, old code: safe — the tree at fb59095 names neither table, none of the 8 columns, nor the planner / console (0 files; the one `authorised_by` hit on main is the UI schedule's trigger key). New code first, READS: the plan view and targets block degrade to "unavailable"; the schedules panel degrades to absent (its store selects the new columns). New code first, WRITES: NOT safe — every S4 enqueue inserts `plan_id` (UndefinedColumn: the Plan routes, the API enqueue, the CI webhook, the repair gate) and the scheduler tick's store read fails; in-flight runs still persist (the plan id is passed only when set). The runbook never enters it |
| GO #1 — migration | `alembic … upgrade tenant@head` 11:51:45Z → 11:52:10Z, exit 0, the single step on `tenant_1` |
| GO #1 — read-back | head `20260911_0010`; `run_plans` (12 cols) + `release_targets` (9 cols), 0 rows; 8 of 8 columns, all nullable; CHECKs scope-kind / actor-present / execution-complete / deactivation-complete; triggers no-delete + immutable on both; indexes plan-scope, target-release, active-target unique, runs-by-plan; schedule 1's six new columns NULL; 0 runs / jobs carry a plan id |
| GO #1 — refusal proofs (one rolled-back transaction) | plan DELETE refused; plan identity UPDATE refused; the first execution stamp allowed; a SECOND execution stamp refused naming the first; target DELETE refused; target identity UPDATE refused; target deactivation allowed; rows after rollback 0 / 0 |
| GO #2 — merge + deploy | merge **3729821** (`--no-ff`, parents fb59095 + d5fd9eb, author AK, 0 trailers) pushed 12:05:33Z; four services **SUCCESS** at 3729821 by 12:08:46Z; `/api/_internal/health` 200; last 400 log lines per service: **0** error-class lines on all four |
| read-only proof — release 16 | `/releases/16?tab=decision` 200: **Plan** present, "Run the scope" gone; the **Target environments** block with `data-target-count="0"`, the "No target declared — …" note and the declare form; no refusal block (the scope is current) |
| read-only proof — /run | `GET /run` → 302 `/releases?from=run`; followed with a cookie jar: 200 with the flash "Run Tests has moved: plan a run from a release (the decision tab) or a requirement, look at the plan, then run it." |
| read-only proof — schedule 1 | `/runs/substrate` (admin session) 200: "A schedule fires a recorded plan, under a person's authority."; the row reads "plan: every approved test on Prime QA NEW (legacy template)", **"no authorising user"** (authority attribute empty), **"Claim this schedule"** present, "last fired 2026-09-09T06:01 (before plans)" |
| nothing written | plans 0, targets 0, schedule 1 authority NULL, 0 jobs since the deploy. The schedule was NOT claimed and no plan was run — both AK's acts |

No dump was taken (ADDITIVE, classified). No data act followed the merge. Schedule 1 stays enabled; its next 06:00Z fire (2026-09-10) REFUSES loudly and records the refusal until AK claims it on the panel.

## i. Production defect after the merge — the plan view 500 (2026-09-09 → fixed 2026-09-10)

**What AK saw.** Clicking Plan on release 16's decision tab rendered "Something
went wrong" (a 500). The Plan POST itself succeeded — two plans were recorded
(f52771fd at 12:18:04Z, 1499dd4b at 12:18:25Z: 19 claims, 19 jobs, fallback
targets, four deprecated claims and env 78 excluded), neither executed, zero
jobs. The GET of the plan view failed:

```
Unhandled TypeError on GET /plans/f52771fd-971f-4c3f-a507-53d110aa51dc
  File "/app/primeqa/templates/plans/detail.html", line 153, in block 'content'
  File ".../jinja2/filters.py", line 617, in sync_do_join
TypeError: 'builtin_function_or_method' object is not iterable
```

**Root cause, one sentence.** The template read each exclusion's requirement
keys as `x['keys']`, and on an ENVIRONMENT exclusion — which carries no such
entry — Jinja's subscript falls back to attribute lookup and returns the dict's
own `keys` method, which the join filter cannot iterate. A code defect in the
template; the data shape (evidence on env 78 outside the targets) is ordinary
and the planner recorded it correctly. The same class as build correction 5,
fixed at one line and missed at this one.

**The class, closed.** A sweep of every template for subscript access to a
dict-method name (`keys values items get copy update pop clear`): **5 hits in
2 templates** — `plans/detail.html` lines 78, 148, 150, 153 and
`releases/detail.html` line 147 (`scope_readiness['items']`, latent: the key
is always present there). Every hit now reads `.get(...)`, so an absent entry
is falsy. The reverse form (an attribute read of a method name that is not a
call) had zero hits. Two guards: `tests/unit/test_plan_view_template.py`
renders the plan view — base template stubbed, no Flask, no database — over a
resolution carrying an environment exclusion (plus the executed state and the
no-exclusions state); `tests/unit/test_templates_dict_method_names.py` asserts
no template subscripts or attribute-reads a dict-method name.

**The verification gap, in AK's words.** A route whose only exercise was
scratch, whose production data shape (evidence outside declared targets) is
ordinary. The fixture world on scratch had no evidence on a non-target
environment, so the environment-exclusion branch never rendered in the
screenshots, and the merge proof rendered the decision tab's Plan link, not a
plan view, because creating a plan is a write. **The standing correction: a
merge proof must render the actual page on production data, not just the link
to it.** Closed here two ways: the scratch fixture world gains an INACTIVE,
production, `read_only` environment holding evidence for the scope
(`plant_step_4_world.py`), and both release plan views were re-shot with the
branch rendering — `plan_view_release_fallback_targets.png` /
`plan_view_release_declared_target.png` now show "environment Prod1 (fixture) ·
The environment holds evidence for this scope but is not a target. (Prod1
(fixture), inactive, production, read_only)" under Excluded. After the deploy,
the read-only proof is AK's two existing production plans rendering 200 with
their env-78 exclusion visible — the proof the merge should have had.

**One test fixed on the way.** `test_step_4_scope.py`'s schedule test froze its
"now" at 2026-09-10 06:05Z — tomorrow when written, earlier than the row's own
creation time once that day arrived, so the schedule was never due and the
corpus went red on the day after the merge. Its clock is now the row's own
clock plus a day and an hour. A test defect, not a product one.

**Suites at the fix commit.** test_representation 429 passed / 3 skipped;
DB-real corpus on clean scratch 109 passed / 7 skipped / 2 red before the test
fix (the D-480 report-slice artefact + the schedule test above; the latter 3/3
green after); pages 7; browser-gated 63 / 11 skipped; unit **5044 passed, 3 warnings in 276.12s (0:04:36)**.

**Merge classification.** Code + tests only: two templates, three test files
(two new guards + the clock fix), the docs — no migration, no data write.
WRITE-FREE, dumpless under D-476.
