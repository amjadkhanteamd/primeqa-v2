# VERIFICATION — Step 2, contemporaneity

Executed 2026-09-08 on the local-PG substrate test database (the
`test_representation` rollback session, tenant_1 at the substrate head
incl. `20260909_0010`) for the DB-real reproductions, on scratch
(`plimsol_3a3`) for the scope read, the migration and the fixture
screenshots over the REAL Flask app, and read-only against production for
the facts cited. **No production row was written and no Railway act was
performed.** Branch `step-2-contemporaneity` (from main @0f34e82, D-483);
design 8eed097. Merge gated — ONE tenant migration (ADDITIVE) → dumpless
per D-476; no public migration; no ORM window.

Re-runnable: `tests/unit/test_run_readiness.py` (the resolver's state
machine, the sentences, the composer refusal, the D9 ladder),
`tests/integration/test_representation/test_step_2_contemporaneity.py`
(the seven DB-real items, local PG), `tests/integration/test_step_2_scope.py`
(the release-scope read, scratch).

---

## 0. The facts, cited, and two corrections to my own pre-flight

- The executor computed the org sequence in the SELECT bracket to pin the
  world and discarded it (`run.py:651-690` → `data_executor.py:317`); the
  execute bracket holds no connection (`run.py:612-640`) — a later read is
  impossible by design. The stamp rides the prep tuple.
- Legacy runs: **727** on tenant 1 (633 in env 59, 94 in env 78), all on
  covered claims (727/727); the brief's 726 plus one since the plan.
- The drift detector executes SELECTs only (`metadata_drift.py:318-659`).
- `test_claim_coverage`: 922 rows over 393 claims; 659 approved claims,
  215 with coverage, the 444 uncovered ones have no runs.
- **Correction 1 (found at build):** the inventory cut HAS an in-repo
  caller — `claim_sets.create_inventory_version` (:112) calls the mint;
  my LLD §0 said "no in-repo caller" (the grep missed an unqualified
  import). The org is now a required parameter of the cut itself, and
  the five suites that cut inventories plant one.
- **Correction 2 (found at build):** S1's own CHECK
  `entities_org_only_for_sync` (`20260622_0020`: "a non-sync
  'manual_curation' entity is legitimately org-less") refuses an org on
  a declared Surface entity. The defect was the tenant-wide CHECKPOINT
  at MAX+1, and that is what binds; the Surface entities stay org-less
  by S1's rule and anchor at the bound checkpoint's sequence. LLD §d
  amended; test 7 asserts both halves.
- **The design's invariant held under test:** the world's pin and the
  stamp are one value — `_prepare_async_execute` captures
  `OrgStamp(org, seq)` once and hands `seq` to
  `plan_data_recipe_world(at_seq=…)`; a limited session (a unit fake)
  cannot fail a run — the stamp read is guarded and the run is written
  unstamped, honestly (found by `test_run_all`'s raising-probe case,
  which counted three errored probes instead of one until the guard).

## a. THE STAMP (DB-real 1)

`s4_execution_runs.connected_org_id` + `org_version_seq` (tenant
`20260909_0010`, ADDITIVE, index on `(claim_test_id, environment_id,
finished_at DESC)`). The select bracket's prep tuple carries
`OrgStamp(org, seq)` with `seq == resolve_current_sequence(session,
org).current_seq`; `_stamp_evidence` sets both fields on the frozen
`RunEvidence`; `persist_run_evidence` writes both columns (omitted when
absent — the `executing_identity` discipline); the row reads `(org, seq)`
back. An environment with no org → the prep carries `None` and the row
is written with NULLs. Every path is stamped at the ONE chokepoint
(`_execute_for_kind`, after the executor returns): metadata, data,
run-all, the synthesized errored probe; the sync path captures in its
own bracket.

## b. TARGETING (DB-real 2)

Two claims stamped at S; the org moves to S+1 by closing an UNCOVERED
entity → both CURRENT (`stamp S, current S+1`); a COVERED read of claim
1 closes at S+2 → claim 1 STALE naming `entity_closed`, claim 2 CURRENT.
Ruling R-ground, in the same test: the engine's `grounding.stale` reads
True for claim 1 and False for claim 2 through the same predicate
(`covered_reads_changed`), and the evidence suite's global-staleness
test became the targeted one (the org moving 5 → 10 no longer stales a
verdict whose reads did not change; closing a covered read does).

## c. THE VOCABULARY (DB-real 3; unit)

NEVER_RUN "No run in this environment."; CANNOT_DETERMINE / unstamped
"Freshness unknown — this run predates run-level environment stamping.
Run again to establish current readiness." (the TA's Fork-3 wording,
verbatim); CANNOT_DETERMINE / no_coverage "This test's reads are not
recorded, so its currency cannot be determined."; STALE names its reads
with "org seq S → C"; CURRENT "Current against org seq C.". The
claim-version note stays separate (`version_currency`, untouched).

## d. THE ENGINE (DB-real 4, 5; unit)

A never-run claim beside a green one → `cannot_determine`, the readiness
line "1 claim(s) ungraded: 1 never run in this environment — unknown
blocks GO and CONDITIONAL GO", `metrics.readiness = {never_run 1, current
1, ungraded 1}`, blockers 0 — unknown blocks, it does not condemn. D9 over
real rows: a failed run beside a never-run claim → `no_go` with
`ungraded 1`; STALE → `conditional_go`; a row with no readiness at all →
`cannot_determine` (fail closed). R-ungrounded: a current run with no
grounding verdict is ungraded, AND the `grounding_integrity` line names
it ("3 with no grounding verdict for this org") — it never reads "all
intact" over an unknown (found on the fixture card and fixed). The
`has_runs` criterion stays a recorded fact and is no longer a graded
blocker.

**Production count for R-ungrounded (read-only, 2026-09-08): zero** — 659
of 659 approved claims hold a verdict at their approved version for both
orgs; 88 of 88 release-scope claims too. The S8 grounding tick
(`scheduler.py:254-271`, cap 100 per org per tick) keeps it at zero;
"Run the scope" therefore does not enqueue grounding in this step.

## e. EVALUATE REFUSES; RUN THE SCOPE (scratch; unit; the real app)

`release_scope_readiness` on scratch: two approved claims with no runs →
`non_current 2`, both NEVER_RUN with `environment None`; one stamped
CURRENT run → `non_current 1`; the other's UNSTAMPED run → CANNOT_DETERMINE
with the TA sentence; `readiness_for_pairs` agrees. The composer
(unit): a non-current scope returns `refused / scope_not_current` with
the items and **writes no decision row and never asks the engine**; a
current scope proceeds. The API route returns **409 `SCOPE_NOT_CURRENT`**
with the items (observed on the real app: `API evaluate: 409
SCOPE_NOT_CURRENT`); the web route flashes the refusal and redirects to
the decision tab. "Run the scope" is the existing `POST
/releases/<id>/run` surfaced beside Evaluate with its environment picker.

## f. THE LEGACY 727 (DB-real 6; production at merge)

An unstamped run reads CANNOT_DETERMINE / unstamped; the resolver
module holds no UPDATE of the column (asserted on its source); the NULL
count before == after. On production the 727 are read-only measured at
merge pre-flight (the column does not exist there until deploy day —
the three `.env`-loading live-parity unit tests read production and are
red on this branch for exactly that reason; they are deselected here
and re-run green after the migration lands).

## g. THE MINT (DB-real 7)

`create_inventory_version(…, connected_org_id=org)` →
`materialize_surface_entities(…, connected_org_id=org)` writes an
ORG-BOUND `logical_versions` checkpoint; the Surface entity stays
org-less (S1's CHECK) and anchors at that checkpoint's sequence; the
org-less call refuses with `ValueError`. The four org-less rows and the
six org-less Surface entities on tenant 1 are untouched, now-inert
history.

## h. THE SCREENS (the approved mock; `step-2-fixtures/`)

Over the REAL app on scratch (`flask-scratch` :5055, superadmin JWT with
the dev secret; the launch config reverted, never committed): one
requirement `STEP2-1` → four approved claims in env "gate sandbox", one
per state; one release holding it.

| file | shows |
|---|---|
| `runs_list_readiness_column.png` | OUTCOME and READINESS as two columns: three `passed` runs reading CURRENT / STALE / CANNOT DETERMINE |
| `run_detail_stale_sentence.png` | the STALE pill beside the outcome chip; "1 thing(s) this test reads changed after this run (org seq 453 → 454): Field Opportunity.Amount-…" |
| `run_detail_cannot_determine_sentence.png` | the CANNOT DETERMINE pill; the TA's sentence verbatim |
| `requirement_claims_readiness_column.png` | the claim list with a readiness cell per claim for "gate sandbox": CURRENT / STALE / NEVER RUN / CANNOT DETERMINE |
| `release_decision_refusal_and_run_the_scope.png` | "Evaluate will refuse — 3 items in scope are not current" listing key · environment · pill · sentence; **Run the scope** with its environment picker beside **Evaluate**; the card's readiness line "4 claim(s) ungraded: 1 never run…, 1 run without a stamp, 3 with no grounding verdict for this org — unknown blocks GO and CONDITIONAL GO"; grounding integrity naming the three |

Two things the fixture also shows and this step does NOT close: the
Substrate-evidence panel beneath the card still says "No claims at risk
— all grounded claims are intact" over "4 not computed" (the console's
own line — ledgered), and the `/runs/substrate` requirement lens (the
default) carries no readiness (the runs lens does — ledgered).

## i. Suites (D-468) at the implementation commit

- **Unit: 5,036 passed, 3 deselected** (the `.env`-loading live-parity
  trio: they read production, which lacks the column until deploy day —
  re-run at merge).
- **test_representation (local PG): 415 passed, 3 skipped** (the seven
  new Step 2 items included).
- **DB-real corpus on scratch: 104 passed, 7 skipped, 1 red** across the
  eighteen DSN-gated files (the new `test_step_2_scope.py` included).
  The red is the report-slice runs-list window artefact ledgered at
  D-480, not this slice. One interrupted run earlier left three
  test-planted S5 rules on scratch (PLM-A11Y-075/076/077, created
  2026-09-08 — my own killed run, not a product fault) and a fixture org
  on environment 5901; both removed, and the two suites that tripped on
  them (s5 seed 74/74, repair-gate 15/15) read green on the clean rerun.
- **Pages: 5 passed. Browser-gated: 63 passed, 11 skipped** (SPIKE_BROWSER=1).

## j. Merge classification (for the runbook)

| migration | content | class |
|---|---|---|
| tenant `20260909_0010_run_org_stamp` | two nullable columns + one index on `s4_execution_runs`; no existing row touched | ADDITIVE → dumpless (D-476), before deploy |

No public migration. No ORM window: the OLD code neither reads nor
writes the two columns (nullable); the NEW code writes them on every new
run and reads them for readiness. Deploy-day: migration → merge → four
services → the next run stamps itself; the legacy 727 measured read-only
(all NULL, all CANNOT_DETERMINE); no data act, no backfill (Fork 3); the
live-parity trio re-run green.

## Residual, stated plainly

- The 444 uncovered approved claims read CANNOT_DETERMINE / no_coverage
  the moment they run; regeneration records coverage (D-353).
- The stamp is the sequence at the START of the select bracket; a sync
  landing between the bracket and the Salesforce call is recorded as
  "planned at S", honestly.
- Readiness reads the LATEST run per (claim, env) regardless of claim
  version; the version note rides beside it.
- A best-effort write on the requirement page (`record_view`) left the
  session's transaction aborted on a failure and 500'd the whole page;
  the except now rolls back — a real fix found while shooting the
  fixture, not a Step 2 feature.
