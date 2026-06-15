# 5b pre-drop checklist (D-221 R5) — run ON THE DAY, before migration 053

> **COMPLETED — ARCHIVED 2026-06-15.** Migration 053 was applied 2026-06-14
> (DECISIONS_LOG D-221.5; all 20 v1 product tables dropped cleanly, post-drop
> smoke green). This checklist's purpose is discharged; retained as the audit
> record of how the irreversible drop was gated.

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
   /runs/substrate + /claims render; one claim run end-to-end green. Add a
   superadmin /settings/llm-usage + admin /settings/my-llm-usage render check
   (D-238 zeroed their v1-table queries; confirm they don't 500).
8. Close entry in DECISIONS_LOG (D-221.5) with all outputs + AK GO quote.

---

## Pre-verified evidence (D-238, 2026-06-14 — re-confirm on the day)

- **Item 1 (zero rows):** all 20 drop-set tables = 0 rows (read-only census, prod).
- **Item 2 (stability):** schedule id=1 (env 59, `0 6 * * *`) fired 06-12/13/14 at
  06:00 UTC; each fire window had **0 `errored`** outcomes (15p/1f, 15p/1f, 14p/1f —
  the single daily red is the honest SQ-205 finding, not infra).
- **Item 3 (coverage):** ✅ MET. 7 of 8 active requirements have an approved claim;
  **SQ-210 (#287) is WAIVED (D-239, AK)** — its junction object `OpportunityContactRole`
  isn't in the synced org model, so the engine correctly refused it `ungrounded-claim`;
  coverage gap consciously accepted, revisit if the sync scope is widened.
- **Item 4 (code references) — settled by D-238.** No live route queries a drop-set
  table. The census still lists referencing files; every remaining reference is
  **consciously accepted** as one of:
  - a docstring / code comment / Jinja comment naming a table (no query),
  - a Jinja template variable or a JS user-message string (templates never query),
  - the v1 execution + test-management ORM **model/repository modules**
    (`execution/models.py`, `execution/repository.py`, the v1 `test_management`
    model bits, `ExecutionSlot` + the dead `execution/routes.py` `get_slots`):
    these map to dropped tables but are **never queried** — harmless after the drop.
    Deleting them is the D-238 residual (import ripple, out of scope for readiness).

  The three live-query BREAKS the audit found (the LLM-usage dashboards' quality-proxy
  + correction-rate metrics) were retired to zero-shape in D-238 — no table query
  remains on any live path.
