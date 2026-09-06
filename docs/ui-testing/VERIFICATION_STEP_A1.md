# VERIFICATION — Step A.1, apply → re-verify at root

Executed 2026-09-07 on scratch (`plimsol_3a3`; tenant chain
`20260906_0010 → 20260907_0010`) with the REAL S4 consumer driving the
queued jobs and the REAL Flask app for the fixture screenshots. **No
production row was written and no Railway act was performed** (the
production facts cited are read-only measurements). Branch
`step-a1-reverify` (from main @a0f7484, D-480). Merge gated — ONE tenant
migration, ADDITIVE (eight nullable columns + one partial index), no
public migration, no ORM change on the public side → dumpless per D-476;
no ORM window (the tenant columns are read only by the new code).

Re-runnable: `tests/unit/test_repair_reverify.py` (the refusal and settle
tables, the pre-approve pass), `tests/integration/test_repair_reverify.py`
(the wiring on scratch; needs `DATABASE_URL` = `S3A3_TEST_DATABASE_URL` =
scratch and a `JWT_SECRET`).

---

## The mechanism this closes (D-480's finding)

D-236's apply wrote a `generated_unapproved` version (SPEC §7.4) and
enqueued a job the selector could never satisfy (`active`/`approved`
only, `coordinator.py:1682-1689`); the consumer completed the job either
way (`consumer.py:109-112`) and nothing recorded the outcome. On
production every applied recipe edit (six rows) sits on a **deprecated**
claim, and the July auto-applies' re-verifies produced zero runs.

## a. The legitimate state — ruling D5

A human apply of a DERIVED edit now, in ONE transaction: `write_recipe`
(actor s8, `event_context {provenance: gate_apply, proposal_id,
gate_verdict, grounding_rule, decided_by}`) → `promote_recipe_to_approved`
(actor human, `event_context {provenance: gate_apply_approval, …}`) →
the D-223 executability gate (`gate_enqueue`) on the promoted version →
commit. An edit yielding an unexecutable shape is refused whole
(`unexecutable_shape: …`), nothing written — found by the suite itself
when the first fixture recipe was a lone CreateStep and the gate, now
judging an *approved* version, refused it.

DB-real **a**: the written version is `approved` (v1 stays
`generated_unapproved` — never promoted by proxy); provenance
`recipe_s8_rewrite` + `recipe_approved`, the latter carrying
`gate_apply_approval`, the proposal id, `decided_by = 1`, `DERIVED`; the
S2 selector (`select_recipe_for_execution`, `_MIN_AVAILABLE_ENV`, live)
returns version 2; the proposal row reads `applied`,
`reverify_state = queued`, `applied_recipe_version_seq = 2`, the job id.

**The autonomous pass pre-approves.** Promotion is humans-only (D-ε-1)
and an unapproved version never runs (D-064), so with the auto flag ON a
DERIVED, grounded, applicable row becomes `status = 'approved'`
(`payload.auto_approved = true`, audit `ui.repair_auto_approved`) and
**no version is written**; the human's one click writes, promotes and
re-verifies. DB-real **g** + Step A's `test_d2` (moved to the new
semantics); unit: the pass never calls `_apply`.

## b. Refuse the impossible, loudly — rulings D6 + D8

`_apply_refusal` gains `claim_deprecated` (every kind: recipe edit,
rerun, regenerate) and `recipe_moved` (recipe edits: the current version
is not the one the run pinned and the gate classified). Both are
evaluated after the Step A refusals (switch, verdict, grounding) and
before any write. The applicability facts (`_applicability`) are read
from the claim's current version and the run's pinned recipe version.

DB-real **e**: a deprecated claim → the apply returns
`claim_deprecated…`, `claim_status = deprecated`; **nothing written, no
job enqueued**; the panel read carries `claim_status`; the rerun kind
refuses identically. DB-real **f**: a second human version written after
the run → `recipe_moved…`, no third version. Unit: the refusal table
(deprecated refuses every kind; moved refuses recipe edits only; the
Step A refusals still come first).

## c. The outcome is part of the proposal — ruling D7

Migration `20260907_0010`: `applied_recipe_version_seq`,
`reverify_job_id`, `reverify_state CHECK IN (queued, ran, no_run,
refused)`, `reverify_run_id`, `reverify_outcome`, `reverify_verdict`,
`reverify_refusal`, `reverify_settled_at`; a partial index on
`reverify_state = 'queued'`. `_stamp` enters `queued` whenever the
outcome carries a re-verify job (recipe edit and rerun);
`payload.reverify_job_reused = true` when the enqueue returned a job
that predates the apply (the get-or-create semantics of D-130.A).

`settle_reverifies` joins `repair_triage_tick` (per minute, per tenant,
loudly-once on unprovisioned tenants): job `completed` + a run (D-317:
newest run for (claim, env) started at/after the job's creation) → `ran`
with run id / S4 outcome / S6 verdict; `completed` + no run → `no_run`
with `reverify_refusal = no_eligible_recipe` (the silence made loud);
`failed`/`cancelled` → `no_run` with the job's error code; non-terminal
→ wait. Idempotent (`WHERE reverify_state = 'queued'`).

DB-real **b**: the REAL consumer (`process_execution_job_for_tenant`,
`run_fn` injected to persist a run row as the executor would) completes
the queued job; `settle` records `ran / failed / creation_rejected` with
the run id; the pending row was visible on the panel as open work
(`REVERIFY_PENDING` count) and leaves it once settled; a second settle
writes nothing. DB-real **c**: a job whose run_fn returns
`ran=False` → `no_run / no_eligible_recipe`. Unit: the settle table.

## d. The July four, re-examined — `reexamine`

`python -m primeqa.intelligence.repair_gate reexamine --tenant-id N
--user-id U` over applied recipe edits with `reverify_state IS NULL`:
deprecated claim → `refused / claim_deprecated`; not DERIVED → `refused
/ not_derived`; reverted → `refused / reverted`; DERIVED on a live claim
whose applied version is current → promoted (`gate_retro_approval`
provenance, audit `repair.gate_retro_approval`) + re-verify queued.
DB-real **h**: all three dispositions on planted July-shaped rows;
idempotent on a second run.

**On production** (read-only, 2026-09-07): all six applied recipe edits
— 146, 147 (human, SPECULATIVE), 95189, 95195, 465221 (reverted,
SEMANTIC) and 347418 (kept, DERIVED) — sit on **deprecated** claims with
no earlier approved claim version. The expected `reexamine` transcript
at deploy is six recorded `claim_deprecated` refusals and **nothing
turns red**: no legitimate run exists for any of them. That is the
honest answer to "report what actually turns red".

## e. The panel and the run card (screenshots: `step-a1-fixtures/`)

| file | shows |
|---|---|
| `repairs_panel_a1_states.png` | three DERIVED rows: "Applied · re-verify pending (job 4242)" (no Reject on an applied row), "Recipe moved — re-triage on a fresh run", "Claim deprecated — not applicable"; header "… · 1 re-verify pending" |
| `run_detail_card_reverify_settled.png` | "Applied — re-verify ran" with "Re-verify ran: failed · creation rejected" |
| `run_detail_card_reverify_pending.png` | "Applied — re-verify pending (job 4242)" |

These are additions the A.1 brief asked for in words ("recorded on the
proposal", "loud refusal"); they had **no AK-seen mock**, so under the
standing UI rule this push HOLDs with the screenshots for review before
merge. No Step A element moved.

## f. Suites (D-468) at the implementation commit

- **Unit: 5004 passed** (the new `test_repair_reverify.py` table + the
  Step A files).
- **DB-real: 91 passed, 2 skipped, 1 red** across fifteen suites (the
  thirteen + Step A's 15 + A.1's 8). The red is the report-slice
  runs-list window artefact ledgered at D-480 (scratch holds more
  processing runs than the 50-row window; not this slice).
- **Pages: 5 passed. Browser-gated: 63 passed, 11 skipped.**

## g. Merge classification (for the runbook)

| migration | content | class |
|---|---|---|
| tenant `20260907_0010_repair_reverify.py` | eight nullable columns + one partial index on `repair_proposals` | ADDITIVE → dumpless (D-476) |

No public migration; no public ORM change; the new columns are read
only by the new code → no ORM window in either direction. Deploy-day:
merge → four services → `reexamine --tenant-id 1 --user-id 1` → the six
refusals → report.

## Residual, stated plainly

- No job→run FK still; the D-317 resolution is reused (bounded by the
  job's `created_at`). A `run_id` stamp on the job is S4 work.
- The selector's walk-back (a deprecated CURRENT claim with an older
  approved version still runs) is S2 behaviour untouched here; A.1
  refuses at the repair boundary.
- `promote_recipe_to_approved` would un-deprecate a deprecated recipe if
  asked (D-226 is caller-side); A.1 only ever promotes the version it
  just wrote or the version an applied row names.
