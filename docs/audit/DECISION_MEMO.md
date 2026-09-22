# Decision memo — the audit's open questions (round 3, part D)

2026-09-21. Read-only throughout: every production figure below was read under
the server-side read-only guard, proven before each read by attempting a
`CREATE TABLE` (and, for the orphan trace, an `UPDATE`) and seeing
`ReadOnlySqlTransaction`. Nothing was deleted, no foreign key was added, no
route was changed. Each item gives the facts, the options and a defended lean.
**AK decides.**

---

## 1. AUD-028 — the seven orphan rows: how were they created?

Seven rows across two tables carry a `run_id` that no `s4_execution_runs` row
matches. They are **two unrelated incidents**, not one leak, and neither is a
race.

### The rows

| table | run id | when | what it says |
|---|---|---|---|
| repair_proposals | 8c09fc83 | 2026-07-10 05:55 | rerun / proposed / not_evaluated |
| repair_proposals | cff08d72 | 2026-07-10 08:33 | rerun / proposed / not_evaluated |
| repair_proposals | f95f9b3b | 2026-07-10 10:42 | rerun / proposed / not_evaluated |
| repair_proposals | f7ef9417 | 2026-07-10 13:17 | regenerate_from_current_org / proposed |
| s4_created_records | 71c0e78a | 2026-09-09 02:11:48 | PLS_BM_Deal__c a0kIp000000lGOPIA2, cleaned |
| s4_created_records | d89c2336 | 2026-09-09 02:19:45 | Case 500Ip000001F9wLIAS, cleaned |
| s4_created_records | d89c2336 | 2026-09-09 02:19:46 | Case_SLA__c a0iIp0000002LpOIAU, cleaned |

### Incident A — September: a run interrupted by a deploy (2 run ids, 3 rows)

**Mechanism, named.** Two execution jobs failed with
`error_code = worker_shutdown`, message **"Worker received shutdown mid-run"**:

| job | test | claimed | failed |
|---|---|---|---|
| 721 | 1419f049 | 02:11:46 | 02:11:48 |
| 778 | 5ddb8c6a | 02:19:44 | 02:19:47 |

The orphan `s4_created_records` rows are written at **02:11:48**, **02:19:45**
and **02:19:46** — inside those two jobs' windows, and no other job failed that
day. Neither test has a run row anywhere in its job's window. Every orphan row
has `cleaned = true`.

So the sequence was: the worker minted a run id, provisioned records in
Salesforce and recorded them, took SIGTERM from the 2026-09-09 deploy, ran its
reverse-order cleanup (which is why `cleaned` is true), and **never reached
finalize, where the run row is written**. D-341's requeue looked after the job;
nothing looks after the provisioning record, whose `run_id` now names a run
that will never exist. 281 runs finished normally in the same two-hour window,
so this is the interrupt path only.

**This can happen again on any deploy** — it is a live hole, not history.

### Incident B — July: a deletion that did not cascade (4 rows)

**Mechanism, named.** All four proposals name claims that **no longer exist**
(`test_claims` count 0 for each), and `s4_execution_runs` holds **no run at all
on 2026-07-09, 07-10 or 07-11** — the table jumps from 07-08 (56 runs) to 07-12
(3 runs). Exactly four of the 140 proposals on production have a vanished
claim, and they are these four. So claims and their runs were deleted (the July
regeneration / dedup work) and `repair_proposals` was not cascaded or cleaned.
All four are still `proposed`, never decided, never applied, `auto_applied =
false` — repair suggestions for tests that no longer exist.

`s6_reinterpretations` has no orphans because its earliest row is 2026-08-01,
after both incidents.

### Options

1. **Delete the seven rows, then add the three foreign keys.** Clean, and the
   class cannot recur. Loses the only record that two runs were cut off
   mid-flight by a deploy, and that a July regeneration left repairs behind.
2. **Add the foreign keys `NOT VALID`** (they then bind all future rows and
   leave these seven alone), and fix the September hole separately.
3. **Fix the September hole first, leave the rows, add the keys later.**
4. **Leave everything; keep the census test honest.** Status quo.

### Lean — option 2, with a sequence

**Add the three foreign keys `NOT VALID` now, and fix the write order that
creates incident A.** `NOT VALID` binds every future insert without touching or
judging the seven rows, so the class is closed going forward and the evidence
survives. The September hole is the part that matters: a provisioning record
should not be able to exist before its run row. Either write the run row when
the run is minted (status `running`) and finalize it, or write the created
records only at finalize. The first is better, because the record's purpose is
to survive a crash.

Then, separately and visibly: the four July proposals should be **closed with a
reason** ("the claim this repairs no longer exists"), not deleted — a repair
proposal is a recommendation, and withdrawing it is the honest act. After that,
`VALIDATE CONSTRAINT` on each key.

**Not recommended: deleting the rows.** They are the only surviving evidence of
both incidents and they harm nothing where they are.

---

## 2. AUD-034 — who calls the 71 unlinked API routes?

**The honest answer: the question cannot be answered from the evidence that
exists, and that is the finding.**

### What I looked for, and what is there

| source | result |
|---|---|
| CI configuration in the repo | **none** — there is no `.github/`, no workflow file, no CI config of any kind |
| production access logs | **none kept**. `observability.py` logs a request only when it takes over 1,000 ms (`slow_request`); there is no access log |
| Railway log retention | the live deployment's stdout only — 13 lines today, since 06:58Z |
| the repo's own callers | measured below |

### The surface today (61 `/api` rules, not the 78 of the audit — some retired since)

| caller | rules |
|---|---|
| the product's own templates and JS | 9 |
| only the repo's scripts (audit and ops tooling) | 8 |
| only the tests | 14 |
| **named by nothing in this repository** | **30** |

The 30 include every act that matters most: `/api/releases/<id>/status-token`,
`.../decisions/<id>/finalize`, `.../evaluate-decision`,
`/api/users/<id>/deactivate`, `/api/sections/<id>/purge`,
`/api/requirements/<id>/purge`.

For context, the only usage evidence the product keeps at all: **72
`activity_log` rows in the last 30 days, from one distinct user**, dominated by
`ui.run_enqueued` (21), `s4.plan.create` (17) and `s4.plan.execute` (13).

### Options

1. **Freeze the 61 as a public contract** and gate-test each one's tier and
   owner rule (the route-authority table already does exactly this).
2. **Prune the 30** that nothing in the repo names.
3. **Log first, decide later**: record method, path and status for every
   request for 30 days, then classify from evidence.

### Lean — option 3, then 2

**Turn on an access log before deciding anything.** Every route here is a live
gate someone must keep honest; deleting one because this repository does not
name it would be deciding from absence, which is the exact failure the audit
kept finding. The cost is small: one `after_request` line that logs
`method path status tenant` at info, plus Railway's retention or a table.

After 30 days the classification is evidence, not inference, and pruning is
safe. Until then every one of the 30 is **UNKNOWN** — and the route-authority
table (D-500) already holds each of them to a declared tier and owner rule, so
they are not ungoverned while we wait.

---

## 3. AUD-025 / AUD-031 / AUD-033 — the orphan surfaces

### `/sections` (AUD-031) — a live page nothing links to

**Facts.** Three live sections, none deleted; they hold 8, 3 and 0
requirements. The page's acts have real history in `activity_log`: 117 section
creates (last 2026-07-06) and 33 soft-deletes (last 2026-04-19). Nothing links
to it: no nav entry, no template href, no redirect. It carries admin acts
(rename, delete, restore, purge). AUD-004's dead "View TCs" link lived on this
page unseen precisely because nobody visits it.

**Lean: LINK IT, from Settings.** It is the only surface that manages the
requirement tree, the tree is real and in use, and the acts have been used.
Retiring it would strand the tree with no management surface. One nav entry
under Settings, beside Users and Groups.

### `POST /plans` — the tenant-wide plan (AUD-033)

**Facts.** `run_plans` on production, by scope: **schedule 12, release 5,
tenant 0**. A tenant-wide plan has **never been recorded on production**. Until
round 2 the route also recorded a plan from an empty form (AUD-021).

**Lean: RETIRE IT.** Never used in the product's life, reachable only by a
crafted POST, and its honest successor exists (plan a release, or let the
schedule plan). If a tenant-wide plan is wanted later it needs a surface and a
confirmation, which is a new slice, not a resurrection.

### `POST /requirements/<id>/run-substrate` (AUD-033)

**Facts.** Jobs without a `plan_id` — the pre-D-486 path this route fed — number
1,094 and **stop on 2026-09-09 06:01**, the day D-486 landed; jobs with a
`plan_id` number 2,335 and continue to today. Since D-494 the route refuses
every call.

**Lean: RETIRE IT.** It has been a refusal for a fortnight, nothing links to
it, and its work is done by the plan path.

### The redirect aliases (AUD-025): `/results`, `/results/<id>`, `/suites/<id>`, `/tickets`

**Facts.** The v1 tables behind them are gone (`test_suites`, `pipeline_runs`,
`test_cases` all `None` on production). Round 3 proved each alias redirects to
a live successor that answers 200.

**Lean: KEEP them, and stop counting them as orphans.** They cost one redirect
each and they protect bookmarks and old links. They are already named as
redirect-only in `tests/integration/test_miss_is_404.py::RETIRED_REDIRECTS`;
the orphan inventory should read that list rather than report them again.

---

## What AK is being asked to decide

| # | question | my lean |
|---|---|---|
| 1 | the seven orphan rows | foreign keys `NOT VALID` now; fix the deploy-interrupt write order; withdraw the four July proposals with a reason; validate later. Do not delete |
| 2 | the 30 API routes nothing names | log every request for 30 days, then prune from evidence. Do not prune now |
| 3 | `/sections` | link it from Settings |
| 4 | `POST /plans` | retire it |
| 5 | `POST /requirements/<id>/run-substrate` | retire it |
| 6 | the four redirect aliases | keep them; mark them redirect-only in the inventory |
