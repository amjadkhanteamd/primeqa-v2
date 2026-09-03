# VERIFICATION — the scheduling slice (per-release runs without manual invocation)

Executed 2026-09-03 on scratch (`plimsol_3a3`, tenant chain upgraded
`20260903_0010 → 20260904_0010`). **No production row was written and
no Railway act was performed.** Branch `scheduling-slice` (from main
@`6918f29`). Merge gated — this slice carries ONE tenant migration,
classified **ADDITIVE** (one new empty table + one partial index;
touches no existing row or column).

Re-runnable: `tests/integration/test_ui_schedules.py` (scratch).

---

## Design (required — this adds a new actor class to an authz path)

**The briefed leans, each confirmed or argued:**

1. **The tenant table — CONFIRMED**, with recorded-state additions:
   `ui_run_schedules(claim_set_id → claim_sets, cron_expr, auth
   DESCRIPTOR only, active, created_by, last_enqueued_at)` plus
   `last_job_id` (the overlap gate's read), `last_skipped_at` +
   `skips_since_last_run` (skips are counted state, not just log
   lines), `error_state CHECK ('enqueue_failed','dead_authority')`,
   `last_error`, `deactivated_reason/at`. Creation refuses: a
   non-APPROVED set ("scheduling automates RUNS, never approval"), an
   invalid cron, any auth that is not a descriptor
   (`{mode, persona}` — a `mode: password` dies at validation).
2. **The tick — CONFIRMED**: `ui_schedule_tick` joins the scheduler's
   tick tuple (the D-214 pattern: per-tenant try/except, one tenant's
   failure never starves the rest), calling
   `fire_due_ui_schedules(tenant_id)` with the D-214
   lateness-tolerant `is_due` REUSED, not reimplemented. Every fire
   builds a FRESH manifest through `enqueue_ui_run` — census + run-set
   pins current at fire time, the D-461 invariant and the D6 mode
   table untouched.
3. **Actor semantics — CONFIRMED, with one sharpening**: the D-245
   boundary runs against the creator at CREATION; each tick re-checks
   the creator's CURRENT standing (`is_active` AND rank ≥ MEMBER) —
   dead OR DEMOTED authority deactivates the schedule loudly
   (`dead_authority` + audit: "a schedule never runs on dead
   authority"); an `AuthorizationError` raised inside the enqueue
   boundary itself (a demotion racing the tick) takes the same loud
   path. The enqueue is system-as-actor with the schedule reference:
   the `ui.run_enqueued` audit carries
   `trigger: {scheduled_by_schedule, authorised_by_user}` and the
   manifest records `execution.mode = "scheduled"`.
4. **Overlap — CONFIRMED**: previous job still `pending`/`in_progress`
   → SKIP with an audit event + the recorded skip counter; never
   stacks. Skips audit once per tick (bounded by the 60s cadence) and
   the counter makes a long-stuck schedule visible at a glance.
5. **Failure visibility — CONFIRMED**: a failed enqueue records
   `error_state='enqueue_failed'` + `last_error` + a mandatory-log
   audit event; the schedule stays active (an error is not a
   deactivation) and the tick continues to the next schedule.
6. **One lean ARGUED — the org-env snapshot.** "Pinned fresh each
   time" holds for the census and the run set (both resolved per fire
   by the builder). The ORG-ENV snapshot is recorded as `null` in v1:
   the scheduler service carries no portal-org SF client wiring, and
   the builder's existing honest fallback ("not captured", which the
   Phase 7 comparator states rather than guesses) is the correct
   posture until a per-schedule SF-client resolution is designed —
   named residual, not silent scope.

**New actor class, stated precisely**: the scheduler process now calls
the enqueue boundary. It never holds authority of its own — it BORROWS
the creator's, re-validated per fire, and says so in every audit row.

## a. A product gap FOUND AND FIXED by this slice's end-to-end leg

`build_manifest_for_claim_set`'s capability map read only the PLATFORM
release, so a claim set enumerated from a tenant UNION (Part 2's
`cust_release_members`) refused with "member rule PLM-CUST-00002 is not
in release 3 — the set and its pin disagree": **no scheduled (or
manual) run of a union set could ever build a manifest.** Fixed at the
root: the builder's caps now merge the RECORDED union (each custom
member at its own capability, AUTO by construction). Production never
hit this — no union is recorded on prod — and the fix is exercised by
the e2e test below.

## b. The briefed matrix, executed (scratch)

- **Creation gates**: viewer refused at the boundary; bad cron refused;
  a non-approved set refused with "never approval"; `mode: password`
  refused ("descriptor only — never a credential"); a valid create
  audits `ui.schedule_created` with the real actor (user 1).
- **Cadence fires**: a due schedule enqueues ONE job; the fresh
  manifest carries run set 74 + census schema v1;
  `execution.mode = "scheduled"`; the enqueue audit carries the full
  trigger attribution; the schedule row advances
  (`last_job_id`, `error_state` cleared); the same minute does NOT
  fire again.
- **Overlap skip PROVEN**: due again while the job is still pending →
  `skipped_overlap`, ONE job total (never stacked), skip counter = 1,
  `last_skipped_at` set, `ui.schedule_overlap_skipped` audited with the
  previous job named.
- **Dead authority PROVEN**: a probe `tester` creates a schedule, is
  then deactivated → the tick refuses-and-audits
  (`ui.schedule_dead_authority`, "never runs on dead authority"),
  `active=false`, `error_state='dead_authority'`, reason recorded; the
  next tick does not even consider it.
- **Failure visibility PROVEN**: a planted enqueue exception →
  `error_state='enqueue_failed'`, the error text recorded, the audit
  event written, the schedule NOT silently deactivated.
- **End-to-end scheduled run**: fire → the real queue → `claim_one` →
  `consume_job` (scan faked; queue + evidence mechanics real) →
  `succeeded`; the manifest-pinned run set (74) and census config
  reached the scan; `process_job` wrote **(74 + union) × 2** verdicts
  with PASS present — and the scheduled CUSTOM claims decided
  `NOT_DETERMINED(no_match_set)` from the empty census, never PASS:
  the D-471 evaluator holding on the scheduled path.

## c. Suites (D-468)

- **Unit: 4,961 passed** (the D-469 sweep binds the new `trigger=`
  call sites).
- **DB-real: 63 passed** across all twelve suites (+6 scheduling).
  The two skips are the known idempotent-replay guards.
- **Browser-gated: 63 passed, 11 skipped** (no worker delta).

## Residual, stated plainly

- Org-env snapshot on scheduled fires records `null` (§ design point 6)
  until per-schedule SF-client resolution is designed.
- No schedule UI — CLI + service functions (`python -m
  primeqa.execution_engine.ui_schedules create|list|deactivate`,
  non-secret argv); a settings page is report-layer work.
- The scheduler service fires per-minute ticks; sub-minute cadences are
  not meaningful and cron validation does not forbid them — the tick
  cadence is the floor.
- MIGRATE-FIRST at merge: tenant `20260904_0010` only, ADDITIVE (empty
  table + index; nothing existing touched).
