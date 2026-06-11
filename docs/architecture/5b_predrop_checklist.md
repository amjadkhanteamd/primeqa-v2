# 5b pre-drop checklist (D-221 R5) — run ON THE DAY, before migration 053

The drop is IRREVERSIBLE. Every line below must pass on the day of execution;
any failure = HOLD and investigate. AK's explicit GO is recorded in the
DECISIONS_LOG close entry alongside the outputs of these checks.

1. **Zero rows, re-verified** (they were zero on 2026-06-11; confirm nothing
   wrote since):
   `python scripts/v1_retirement_census.py` — every table `0 rows`.
2. **Stability window met**: `SELECT id, last_fired_at FROM
   tenant_1.s4_run_schedules` shows ≥3 consecutive daily fires, and the runs
   they enqueued finished without `errored` outcomes caused by
   infrastructure (honest FINDINGS verdicts are fine):
   `SELECT outcome, count(*) FROM tenant_1.s4_execution_runs
    WHERE started_at > now() - interval '4 days' GROUP BY 1;`
3. **Coverage leg settled**: every active requirement has ≥1 approved claim
   or a recorded waiver (SQ-210 decision recorded in the log).
4. **Code references zero**: census reports 0 referencing files for every
   table (docstring mentions excluded), OR each残 reference consciously
   accepted here.
5. **Queues idle + fresh DB backup**: Railway PITR/backup point confirmed
   within the hour before applying.
6. Apply: `psql "$DATABASE_URL" -f migrations/053_drop_v1_product_tables.sql`
7. Post-drop smoke: app/worker/scheduler healthy; / + /dashboard + /run +
   /runs/substrate + /claims render; one claim run end-to-end green.
8. Close entry in DECISIONS_LOG (D-221.5) with all outputs + AK GO quote.
