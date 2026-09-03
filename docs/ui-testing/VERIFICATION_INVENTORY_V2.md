# VERIFICATION — INVENTORY V2 + BASELINE RUN B-2 (the scheduled path's production debut)

Executed 2026-09-03 on **production** (gated: AK confirmed the surface
list, then GO'd the runtime arc; HOLD honoured before this record was
committed). D-474 item 4 — the last pilot-loop item. Branch
`inventory-v2` (docs only — every tenant write below is a RUNTIME row
through the real product paths, not a migration).

The six surfaces are the AK-confirmed subset of the candidate list
(2026-09-03): v1's two portal-home surfaces plus four Customer Service
template pages, all confirmed loading. **My Profile was excluded by
ruling**: its URL embeds a record id (`/s/profile/005…`), making it a
record-context surface — first named candidate for the record-context
inventory revision, with the id to be declared as `record_context_ref`,
never baked into the path.

Dump before any write: `prod_pre_B2_20260903_105114.dump` (pg_dump -Fc,
180 MB, 1,121 entries, verified readable).

---

## The transcript — every act, in order

| # | act | path | record |
|---|---|---|---|
| 0 | dump | `pg_dump -Fc` | `prod_pre_B2_20260903_105114.dump`, 180 MB, 1,121 entries, readable |
| 1 | cut inventory v2 | `create_inventory_version` | version **2**, 6 members, created_by 1; Surface entities materialized 6/6; **v1 untouched** (2 rows intact) |
| 2 | enumerate | `enumerate_claims(release 3 × v2 × customer)` | draft set `e9fda797-f816-45e0-9701-683297571cf9`, **444 members** (74 × 6), 296 created + 148 reused by identity hash, all APPLICABLE+executable; union path rode through honestly empty (0 customer rules on prod); 22.5 min |
| 3 | approve | `approve_claim_set` (actor 1) | 444 claims + 296 recipes promoted; set `approved`, `approved_by=1`; audited `s2.claim_set.approve`; 27 min |
| 4 | schedule | real CLI `ui_schedules create` | schedule 1, cron `29 6 * * *`, auth descriptor `vault`/`customer`, audited with the real actor |
| 5 | **the debut fire** | prod scheduler tick | fired 06:29:28Z; audit `ui.run_enqueued` carries `trigger {scheduled_by_schedule: 1, authorised_by_user: 1}` |
| 6 | manifest | built fresh at fire time | `d876c12b`; `execution.mode="scheduled"`; pins: run set 74 (hash `879fc38d…`, byte-identical to B-1's), axe 4.13.0, release 3, census schema v1; **org_env_snapshot=null** — the named D-477 residual, as designed |
| 7 | scan | browser-worker, job `8769cefb-9a21-418b-9255-802a11e2b5ed` | **succeeded attempt 1**; all 6 surfaces `OK`, evidence `REFERENCED`, census v1 on each; **one login for the batch** (`LOGIN_SUBMITTED, MFA_SUBMITTED`) |
| 8 | retire the one-shot | real CLI `deactivate` | reason recorded, audited; final row: inactive, no error state, 0 skips |
| 9 | process | `process_job` | **444 verdicts**, 0 unmapped ids, 0 no-verdict members; 4 min |
| 10 | compare B-1→B-2 | `compare_processing_runs` | **outcome `refused`**, comparison `2b7f3836`, 0 transition rows |

Operational note, recorded because the transcript must be honest: acts
2, 3 and 9 ran as separate single-transaction background processes
(the B-1 timeout lesson applied — one act per process, verified state
between acts). No partial state existed at any point.

## The verdicts (D-466 clean)

| verdict | reason | count |
|---|---|---|
| PASS | attested | **191** |
| FAIL | violation | **12** |
| NOT_DETERMINED | `rule_inapplicable` | 232 |
| NOT_DETERMINED | `engine_incomplete` | 9 |

Zero legacy/unattested/not-executed reasons. The 12 FAILs: PLM-A11Y-071
(landmark) on **all six** surfaces; PLM-A11Y-030 (contrast-enhanced) on
five; and **PLM-A11Y-064 (target-size) newly FAILs on the search
surface** — a real finding only the grown inventory could see. It
passed both v1 surfaces in B-1, and the rule executes at all only under
the D-466 run-set pin (engine-disabled by default in axe 4.13.0).

## The refusal, verbatim

> cross-inventory comparison refused — baseline is inventory v1,
> candidate v2: an inventory change is a DECLARED act (D-281), not
> drift

Persisted as a refused comparison run (`2b7f3836`, 0 transition rows).
The compare screen renders it word for word. Within-v2 comparison waits
for a second v2 run, per the brief.

## The three screens (prod, minted MEMBER session, all 200)

- **Run view**: `ratified_catalogue` header, run set 74, release 3,
  denominator `21 of 55` complete; all 12 FAIL rules visible; 444-row
  projection paginates 1 of 9; **no `X-Amz-Signature` in any render** —
  the bearer rule held.
- **Compare**: the refusal banner, verbatim.
- **Coverage**: 21/55, 21/50, 19/38 over B-2.

## Hygiene, suites, environment

- Secret scan (`gAAAA|otpauth|passw|totp|secret|bearer|fernet`) over
  every new row class — manifest, job, 6 results, 444 verdicts,
  comparison, schedule, inventory members, 444 set members,
  activity_log — **0 hits**. Browser-worker log: 0 hits. Scheduler log
  at the fire: 0 errors.
- **D-468 suites** (tree `1d07772`; this slice adds no code): unit
  **4,964 passed**; DB-real **68 passed, 2 skipped** across thirteen
  suites — the first pass missed `test_s5_rule_registry.py` (its gate
  is `S5_TEST_DATABASE_URL`, not `S3A3_`) and was re-run to the full
  corpus; pages **5 passed**; browser-gated **63 passed, 11 skipped**
  (`SPIKE_BROWSER=1`).
- Egress this boot: `152.55.184.23` — the **seventh** distinct value.
  The instability finding deepens.
- Tenant writes are RUNTIME rows through the real product paths,
  exactly as classified — the branch carries **no migration and no
  code change**.

## Residual, stated plainly

- Within-v2 comparison: armed, pending the next v2 run (the refusal
  proof needed exactly one cross-inventory pair; a comparable pair
  needs two v2 runs).
- The record-context inventory revision: My Profile is its first named
  candidate (`record_context_ref` declared, never a baked-in id).
- org_env_snapshot=null on scheduled fires — the D-477 residual,
  observed live here exactly as designed.
