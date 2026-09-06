# LLD Step A.1 — apply → re-verify, at root

Status: BUILT on the 2026-09-07 GO (design + build, one run); pushed for
review; merge gated. Transcript: VERIFICATION_STEP_A1.md.
Branch: `step-a1-reverify` (from main @a0f7484, D-480).
Derives from: D-480 (Step A; the finding this closes — FIX PLAN "Added
2026-09-07"), D-236 (the apply → re-verify path that never ran), SPEC S2
§7.4 (conservative re-approval: every new recipe version is
`generated_unapproved` regardless of actor) and its commitment "no
autonomous execution — `generated_unapproved` recipes are ineligible for
`select_recipe_for_execution` until a human promotes" (D-064), D-ε-1
(promote is humans-only), D-226 (never silently un-deprecate), D-317
(the job→run resolution the workspace already uses).

**Thesis.** D-236 wrote a recipe version and enqueued a job, then called
that "re-verify". Nothing could run: the written version is
`generated_unapproved` by SPEC §7.4, the selector runs `active`/`approved`
versions only, the job completes either way, and nobody records what
happened. Root cause is a missing *state*: an applied DERIVED edit never
became an executable version through a legitimate act. A.1 supplies the
act (the human apply IS the approval, recorded), refuses the impossible
loudly (deprecated claim, moved recipe), and makes the outcome part of
the proposal — apply is not done until re-verify has spoken.

---

## 0. The mechanism, cited (from the six-reader map, 2026-09-07)

- Selection: `_matching_recipes_for_execution` (coordinator.py:1643-1707)
  keeps `valid_to IS NULL AND status IN ('active','approved')` recipes
  (1682-1689) of the claim's highest-version **approved** row
  (`get_current_approved_claim`, 1460-1499 — it walks back through
  history, so a claim whose CURRENT version is deprecated still resolves
  if an older approved version exists, 1484-1486). Returns `None` →
  `RunPathResult(ran=False, reason="no_eligible_recipe")`
  (run.py:213-220, 611-624). No caller can pin a recipe version
  (run.py:185-196 …; jobs.py:30-34).
- Status: `write_recipe` copies `decision.new_status`, always
  `generated_unapproved` (coordinator.py:945; authority.py:243-299, SPEC
  §7.4). `promote_recipe_to_approved(session, *, actor, recipe_id,
  version_seq, event_context)` is humans-only (D-ε-1, coordinator.py
  2359-2363), in place, emits `recipe_approved` with `event_context`
  folded into `event_data` (2381-2403).
- Jobs: `enqueue_s4_execution(*, tenant_id, test_id, environment_id,
  created_by=None)` is get-or-create on the ACTIVE `(test_id,
  environment_id)` job (jobs.py:94-112) — a second enqueue while one is
  active returns the SAME job. The consumer completes the job whether or
  not anything ran (consumer.py:109-112; jobs.py:173-175); `error_code`
  stays NULL on `ran=False`. No job→run FK; the workspace resolves
  "newest run for (claim, env) since the job's creation"
  (`read_latest_run_for`, s4_execution_console.py:636-662; D-317).
- Repair: `_apply_recipe_edit` edits the LATEST recipe
  (repair_agent.py:174-196 via `get_recipe_latest`) while the gate
  classified the run's PINNED version (repair_gate.py:421-427); stamps
  only `{action, recipe_id, new_version_seq, s4_job_id}`
  (repair_agent.py:660-671); applied rows never render (the panel lists
  `proposed`/`approved` only).
- Production, 2026-09-07 (read-only): **all six applied recipe edits sit
  on deprecated claims** (146, 147, 95189, 95195, 347418, 465221 — none
  with an earlier approved claim version); 24 of the 51 open DERIVED
  reruns and 17 of the 21 open recipe edits sit on deprecated claims
  too. Pressing "Approve & apply" on those today completes a job that
  runs nothing.

## a. The legitimate state — ruling D5

**The gate verdict + the human's approve IS the approval act.** On a
human apply of a DERIVED `recipe_edit`, in ONE transaction:

1. `write_recipe(actor="s8", …, event_context={provenance:
   "gate_apply", proposal_id, gate_verdict, grounding_rule, decided_by})`
   — the edit, as today, now attributed;
2. `promote_recipe_to_approved(actor="human", recipe_id, version_seq,
   event_context={provenance: "gate_apply_approval", proposal_id,
   gate_verdict: "DERIVED", grounding_rule, decided_by})` — the SAME
   human act, recorded as `recipe_approved` with the gate's grounding
   beside it. Never for SPECULATIVE/SEMANTIC (the apply refusal runs
   first); never silent (the provenance names the proposal, the verdict,
   the rule and the human).
3. The selector then finds the version (`approved`, `valid_to IS NULL`).

**The autonomous pass cannot supply the act.** D-ε-1 makes promotion
humans-only and SPEC/D-064 forbids autonomous execution of an unapproved
version; attributing the promotion to whoever armed the switch would
fake a human act. Lean, implemented: with `repair_auto_apply` ON, the
pass **no longer writes a recipe version**. For a DERIVED, grounded,
applicable row it marks the proposal `status = 'approved'` (the existing
vocabulary: gate-approved, awaiting the human apply), audits
`ui.repair_auto_approved`, and the human's one click does write +
promote + re-verify. Autonomy becomes "pre-approve", never "mutate and
hope". (`repair_auto_apply` stays OFF on tenant 1 regardless.)

## b. Refuse the impossible, loudly — ruling D6 + D8

`_apply_refusal` (the control, both paths) gains two recorded reasons:

- **`claim_deprecated`** — the claim's CURRENT version is `deprecated`
  (`get_latest_claim`). A recipe on a deprecated claim is a test that
  was withdrawn; applying, re-running or regenerating it is applicable
  in name only. Refused for every kind, before any write, and the row
  records `reverify_state='refused', reverify_refusal='claim_deprecated'`
  when the refusal is met on an APPLIED row (the one-time re-examination,
  §d). The panel renders "Claim deprecated — not applicable" in place of
  the action.
- **`recipe_moved`** — the current recipe's `version_seq` differs from
  the run's pinned `recipe_version_seq` the gate classified against. The
  verdict no longer describes the recipe that would be edited. Refused;
  the row keeps its verdict; the panel says so.

## c. The outcome is part of the proposal — ruling D7

Additive tenant migration `20260907_0010_repair_reverify.py`:

| column | meaning |
|---|---|
| `applied_recipe_version_seq INTEGER` | the version the apply wrote AND promoted |
| `reverify_job_id INTEGER` | the S4 job the apply enqueued (or found active — `payload.reverify_job_reused = true` when the job predates the apply) |
| `reverify_state TEXT CHECK IN ('queued','ran','no_run','refused')` | queued → settled |
| `reverify_run_id UUID`, `reverify_outcome TEXT`, `reverify_verdict TEXT` | the run (D-317 resolution: newest run for (claim, env) started at/after the job's creation), its S4 outcome, its S6 verdict |
| `reverify_refusal TEXT` | `claim_deprecated` / `recipe_moved` / `no_eligible_recipe` / the job's `error_code` |
| `reverify_settled_at TIMESTAMPTZ` | when the state left `queued` |

**Settling.** `settle_reverifies(tenant_id)` joins `repair_triage_tick`
(per minute, per tenant, the loudly-once posture): for every row with
`reverify_state='queued'`, read the job (`ExecutionJobStore.get_job`);
`completed` → resolve the run → `ran` (run_id, outcome, verdict) or, if
no run exists, `no_run` with `reverify_refusal='no_eligible_recipe'` —
the silence made loud; `failed`/`cancelled` → `no_run` with the job's
`error_code`; `queued`/`claimed`/`running` → wait. Idempotent: a settled
row is never re-settled.

**Visibility.** `list_proposals` now also returns `applied` rows whose
`reverify_state = 'queued'` (open work: "applied — re-verify pending",
with the job id) and the header carries a fourth count. Settled rows
leave the panel; their outcome stays on the row and on the run-detail
card ("re-verify: ran → failed / passed", or the refusal).

## d. One-time evidence — the July four, re-examined

`python -m primeqa.intelligence.repair_gate reexamine --tenant-id 1
--user-id 1`: for every `applied` recipe edit with `reverify_state IS
NULL` (the pre-A.1 shape): claim deprecated → record
`refused / claim_deprecated` (no write, no job); else DERIVED → promote
the applied version with provenance `gate_retro_approval` (the human
named by `--user-id` owns that act) and enqueue + queue a re-verify;
else (not DERIVED) → `refused / not_derived`. Idempotent on
`reverify_state`. On production the six applied rows all sit on
deprecated claims (§0), so the expected transcript is six recorded
refusals and **nothing turns red** — the honest answer to "report what
actually turns red" is that no legitimate run exists for any of them.

## e. Verification (VERIFICATION_STEP_A1.md)

DB-real on scratch: applied DERIVED edit → the written version is
`approved` with a `recipe_approved` event carrying `gate_apply_approval`
+ the proposal id → the S2 selector returns THAT version → a re-verify
job is queued and recorded → the settle pass records `ran` with the run
id/outcome/verdict once a run exists, and `no_run / no_eligible_recipe`
when the job completes without one; SPECULATIVE apply still refused
(no write, no promotion); deprecated claim → `claim_deprecated` refusal
before any write, for recipe_edit AND rerun; `recipe_moved` refusal;
the auto pass on a DERIVED row marks `approved` and writes no version;
`reexamine` over planted July-shaped rows: deprecated → refusal,
DERIVED-on-live-claim → promoted + queued, idempotent on a second run.
Unit: the refusal table, the settle transition table. Full D-468 set.

## f. Residual, stated plainly

- No job→run FK still (D-317 resolution reused; the job's `created_at`
  bounds it). A `run_id` stamp on the job is S4 work, not this slice.
- The selector's walk-back (a deprecated CURRENT claim with an older
  approved version still runs) is S2 behaviour this slice does not
  change; A.1's `claim_deprecated` refusal is at the repair boundary.
- `promote_recipe_to_approved` un-deprecates a deprecated recipe version
  if asked (D-226 is caller-side); A.1 only ever promotes the version it
  just wrote.
