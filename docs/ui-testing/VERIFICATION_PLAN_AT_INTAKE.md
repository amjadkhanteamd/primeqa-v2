# AUD-013 containment — the plan requirement enforced at the intake

Branch `plan-at-intake` from `main` @35f89c3. AK's brief (2026-09-15): "enforce
the plan requirement at the INTAKE, not the caller — every S4 enqueue path
refuses an execution with no plan id, loudly, with the reason naming the
missing plan. Legacy rows stay null; no backfill. This one merges before the
audit resumes."

---

## a. DESIGN

### a.1 What was true

D-486 records that every execution starts from a RECORDED plan. The audit's
pass-4 attack #8 enqueued one with none: as an admin on scratch,
`POST /api/s4-execution-jobs {test_id, environment_id}` returned 202 and job 131
sat queued with `plan_id NULL`. The invariant held by convention of the caller.
Three entry points planned first (the release decision tab, the requirement
page's Plan, the schedule); seven did not:

| path | shape | plan carried? |
|---|---|---|
| `POST /api/s4-execution-jobs` | queued | no |
| `POST /claims/<id>/run-async` | queued | no |
| `POST /claims/<id>/run` | **synchronous** — no job at all; the run row is written with NULL | no |
| `POST /releases/<id>/run` | fan-out `enqueue_claims_for_requirements` | no |
| `POST /requirements/<id>/run-substrate` | the same fan-out (route is unlinked from every template) | no |
| `POST /api/webhooks/ci-trigger` | fan-out `enqueue_claims_for_keys` | no |
| repair re-runs (`repair_agent`, `repair_gate`, four sites) | queued | no — the original run's plan was never read |

Two earlier attempts at the attack were refused for OTHER reasons — environment
scope, then "no executable recipe" — and neither refusal mentioned a plan. A
plan-less request was being refused for reasons that would also have been
true; the reason that was true of it first was never named.

### a.2 The chokepoints

There are two, and both now refuse:

1. **`intake.enqueue_s4_execution`** — every queued execution. The plan is
   checked **before** the recipe gate and before any connection is opened.
2. **`run.run_claim_execution_for_tenant`** — the synchronous entry, which
   never touched the queue. It gains `plan_id` and refuses without it, and
   forwards the plan into the run so the run row carries it.

And a third, defensive one: **the worker**. `async_run_claim_execution_for_tenant`
refuses a queued job that arrives without a plan, so a NULL-plan job cannot
run even if one is inserted around the intake. On production there are **zero**
queued, claimed or running jobs today (2,267 completed, 4 failed), so nothing
is stranded.

The refusal is one type, `PlanRequiredError`, with one reason string used
verbatim by every route:

> no plan: an execution starts from a recorded plan (D-486). Plan it from the
> requirement or release page, or let the schedule plan it, then run that plan.

### a.3 What each caller now does

| caller | now |
|---|---|
| jobs API | `409 PLAN_REQUIRED`, before the environment and recipe gates |
| run-async | the inline refusal fragment, before the environment gate |
| sync run | flash + redirect to the claim, before the environment gate |
| release Run | flash + redirect to `?tab=decision`, where Plan lives |
| requirement run-substrate | the htmx notice or flash, before the environment read |
| CI webhook | `409 PLAN_REQUIRED` **after** the signature and production gates — a caller who cannot sign still learns nothing |
| the three fan-outs | raise `PlanRequiredError` before any read (an empty key set still short-circuits to the empty answer first) |
| repair re-runs | pass the ORIGINAL run's plan (`intake.plan_of_run`); a legacy run's re-run is refused by the intake and recorded by the existing catch |
| planner (`execute_plan`) | unchanged — it always passed the plan |

**Precedence is the design.** The plan is checked first everywhere so that
the two refusals the audit saw now name the plan when that is the true reason.

### a.4 The fork I am not deciding: the CI webhook

`WEBHOOK_SECRET` is configured on production, so the webhook is live-capable.
Under this slice a signed CI trigger is **refused** with the plan named. That is
the containment AK asked for; it also means CI-triggered runs are off until the
webhook is taught the schedule's shape — plan under its own authority, then
execute THAT plan. That follow-up is ledgered, not folded in.

### a.5 Non-goals

No backfill: 1,314 legacy runs on production keep `plan_id NULL`. No migration.
The claim-level routes are not re-pointed at a plan-for-one-claim; they refuse.
85 pending repair proposals on production will, when decided, re-run under their
run's plan or be refused if that run is legacy — recorded, not silent.

---

## b. What changed

| file | change |
|---|---|
| `execution_engine/errors.py` | `PlanRequiredError` with the one reason |
| `execution_engine/intake.py` | plan checked first in `enqueue_s4_execution`; `plan_of_run`; the release fan-out carries and requires the plan |
| `execution_engine/run.py` | sync entry takes and forwards `plan_id`, refuses without; worker entry refuses without |
| `intelligence/s4_execution_console.py` | `trigger_claim_run` forwards the plan; both fan-outs require it |
| `views.py` | five routes refuse first |
| `release/routes.py` | the webhook refuses after its gates |
| `intelligence/repair_agent.py`, `repair_gate.py` | four re-run sites inherit the run's plan via `plan_of_run`; the reverify and revert SELECTs now carry `run_id`; the `rerun` branch records a refusal as `{action: rerun, error}` (the shape `recipe_edit` already used) instead of raising |

Code and tests only. No migration.

## c. Guards

`tests/unit/test_plan_required_at_intake.py` (10): the reason names the plan
and the exit; the intake refuses before the recipe gate and before any
connection; with a plan it reaches the gate; the sync entry, the worker and the
three fan-outs refuse; an empty key set still short-circuits; and a D-469
guard reads every `enqueue_s4_execution` call out of the source and asserts each
passes `plan_id=` or sits in `views.py` below a refusal.

Six existing suites updated to the new rule, each a stale test under the
corrected semantics (D-468's triage, done before the slice closes):

| suite | was | now |
|---|---|---|
| `test_intake.py` | enqueued without a plan | carries a plan (the column is a uuid, so the placeholder is one); + a precedence test |
| `test_s4_execution_jobs.py` (live app) | expected 202 | expects `409 PLAN_REQUIRED` |
| `test_auto_triggers.py` | fake enqueue without `plan_id` | fake accepts it; + a plan-less fan-out refuses |
| `test_run_router.py` | two direct runs without a plan | both pass one |
| `test_repair_gate.py` | `_plant_run` inserted plan-less runs | plants under `PLAN` by default; + `test_z`: a LEGACY run (`plan_id=None`) re-run is refused and the record reads "no plan … D-486" |
| `test_repair_reverify.py` | fake run functions without `plan_id` | accept it (the consumer passes the job's plan) |

Every one of those six went red for the reason this slice exists — a plan-less
execution was accepted — and each was made to plant or pass the plan, never to
tolerate its absence. The one absence that is now asserted is the legacy run's,
and it is asserted to be refused.

## d. Verification — attack #8 re-run through every path (scratch)

`scripts/audit/attack8.py`, as the audit's admin and member on the planted
executable claim and the UI-only claim:

| # | path | before this slice | after |
|---|---|---|---|
| a | jobs API, admin, executable claim | **202, job 131, plan NULL** | **409 PLAN_REQUIRED**, names the plan |
| b | run-async, admin | accepted | inline refusal naming the plan |
| c | sync run, admin | would have run | flash naming the plan, redirected |
| d | jobs API, member without env access | 403 "no access to this environment" | **409 PLAN_REQUIRED** — the plan is named first |
| e | jobs API, admin, UI-only claim | 409 "no S4-executable recipe" | **409 PLAN_REQUIRED** — the plan is named first |
| f | CI webhook, correctly signed | would have fanned out | **409 PLAN_REQUIRED** after the signature gate |
| i | release Run button | fanned out | flash naming the plan, redirected to the decision tab |

**The planner's own path still executes and stamps the plan.** With the
planted claim's recipe promoted to approved and the claim linked to requirement
`AUD-013`: `create_plan` (requirement scope, env 4) → `run_plan` → receipt
`s4_jobs=1, refusals=[]` → **job 132 queued with `plan_id 7579a49d…`**. Cancelled
after. Two earlier plans admitted nothing — unapproved recipe, then no
requirement — and both were the planner's honest exclusions, not this refusal.

## e. Suites

| suite | result |
|---|---|
| sweeps first (D-490) | 60 passed |
| unit | **5,149 passed** (10 new) |
| harness / DB-real corpus / pages | see §f |

## f. The D-468 set

| suite | result |
|---|---|
| unit | **5,149 passed** |
| `tests/integration/test_representation` | 455 passed, **5 failed** — all five fail identically on `main` @35f89c3 in a worktree: `test_step_b_staleness_pin` plants runs dated 2026-09-06 against a 168-hour freshness window and `test_step_5_policy` fixes `NOW` and a waiver expiry; on 2026-09-15 they have rotted. Ledgered; not this slice |
| DB-real corpus (24 suites, scratch) | **103 passed, 3 failed** — each red on `main` against the same scratch: `test_phase5_authoring::test_a` (the audit's tenant-2 schema now exists on scratch), `test_report_slice` (the D-488 window artefact), `test_step_5_scope` (the seed policy left active by replay) |
| page suites | **47 passed** |
| `test_repair_gate` + `test_repair_reverify`, alone with `DATABASE_URL` at scratch | **24 passed** (they skip without it) |

Two comparisons were needed to get an honest corpus reading. The first corpus
run overlapped the harness on the same local Postgres and produced 96
connection refusals — an environment failure, discarded. The second ran alone
on the branch and then on `main`, and the failure sets were diffed: six suites
were red on the branch only, all in the repair re-run paths, all for the reason
this slice exists (their fixtures planted plan-less runs), and all were updated
as stale tests under the corrected semantics (§c). Nothing else moved.

## g. Production blast radius, read-only

| fact | value |
|---|---|
| queued / claimed / running S4 jobs today | **0** (2,267 completed, 4 failed) — the worker-side refusal strands nothing |
| legacy runs with `plan_id NULL` | 1,314 — stay NULL, no backfill |
| runs with a plan | 1,567 |
| pending repair proposals | 85 — each re-runs under its run's plan, or records the refusal if that run is legacy |
| `WEBHOOK_SECRET` on production | configured — the CI webhook is live-capable and will now refuse (§a.4) |
