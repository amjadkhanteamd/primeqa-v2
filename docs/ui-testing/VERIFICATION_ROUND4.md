# Verification — Round 4, 2026-09-22

Branch `round4` from main @70cfeb9. Four parts, one commit each, one HOLD before
any merge. Production is read-only under the proven guard throughout (CREATE /
INSERT / UPDATE attempted and refused before every read), except the one data
act of Part A, which is separately gated on AK's GO. Every UI change carries a
fixture screenshot and a production-data render (D-487); the merge waits for
AK's "screens approved".

## Part A — AUD-028, the deploy-time orphan (the live defect, done first)

**The mechanism, as built.** The run row now exists BEFORE the first record is
provisioned. `StrandedRecordSink.run_opened` writes the row in the `running`
state (outcome `running`, `finished_at` NULL) in its own committed transaction,
called by the data executor at the point the world is about to be constructed
— before any Salesforce create — and FAIL-LOUD: a run that cannot record itself
does not provision. Finalize COMPLETES that row (`persist_run_evidence` updates
a running row in place; a finalized run_id still raises on a second insert —
the D-108.3 layer is unchanged). Three closers make sure no row stays running:

| who | when | what the row says |
|---|---|---|
| the executor (`execute_data_recipe`) | a raise out of an opened run — the worker's SIGTERM → `KeyboardInterrupt` (D-341), or a fault | `errored`, error surface `worker_shutdown` / the exception class, "Worker received shutdown mid-run" |
| the run-all loop | a probe that raised is COUNTED from its closed row, never synthesized a second time | — |
| the job reaper (`reap_stale_jobs`) | a row still running past the heartbeat timeout (a worker killed without its handler) | `errored`, error surface `stale_timeout` |

**A running row is invisible to the product** until finalize — the visibility a
run had before the write-ahead row existed. Every reader of `s4_execution_runs`
carries `finished_at IS NOT NULL` (`result_store.FINALIZED`): 39 statements in
13 files, from S1's readiness resolver to S6's batch reader to every
intelligence console. The gate `tests/unit/test_running_rows_invisible.py`
reads every SQL string constant in `primeqa/` through the AST (implicit
concatenation and f-strings included) and fails on any reader without the
predicate — **RED on main naming all 39**, and shown able to fail on a planted
bare reader.

**The schema (tenant migration `20260922_0010`, REJECTING → dump-first at the
merge).** `run_outcome` + `'running'`; `finished_at` nullable with the CHECK
`(finished_at IS NULL) = (outcome::text = 'running')`; the keys
`fk_s4_created_records_run` and `fk_repair_proposals_run` **NOT VALID —
permanently, by AK's ruling** (VALIDATE would need the seven orphans deleted or
rewritten, and both destroy evidence; the reason is in the migration's
constraint comments); `fk_s6_reinterpretations_run` valid (zero orphans on
production). The seven rows are NAMED EXCEPTIONS in the run_id census ledger
(`tests/integration/test_constraints_triage.py::NAMED_ORPHANS`), and three
gates hold them: the two keys are asserted NOT VALID and the third valid (a
VALIDATE by anyone flips the gate red); every orphan run_id in the two tables
must be a named one (a new orphan is a new incident); a NEW row naming no run
is refused by the NOT VALID key (proven by attempt, and the same row naming a
real run accepted).

**The deploy-interrupt proof, on scratch.** A child process was the worker: it
installed the worker's own SIGTERM handler, claimed a real queued s4 job
through the real consumer, and ran it through the real executor with the real
sink; only the org was a stub whose first create answered at once and whose
second create blocked. The parent waited for the first created-record row,
then sent SIGTERM at +1.5 s:

```
RESULT job: status=failed error_code=worker_shutdown
RESULT runs for the claim: 1 -> [(run 4cda8722, 'errored', finished=True, 'worker_shutdown', 'Worker received shutdown mid-run')]
RESULT created records of the run: [('001PROOF001', cleaned=False, run 4cda8722)]
RESULT orphan created-record rows in the tenant: 0
PROOF PASSED
```

Zero orphans, one failed run carrying the shutdown reason, the provisioned
record pointing at a run that exists (the reaper's to reclaim). The proof's
rows were removed.

**Guards, each red first.** `test_running_rows_invisible` (39 named on main);
`tests/unit/execution_engine/test_run_row_before_provisioning.py` (7: the
order — `run_opened` before the client's first create; the return; the
interrupt-close; a sink that cannot open refuses before any create; the
negative path opens nothing; finalize completes the opened row; a finalized id
still inserts and so still fails loud) — ImportError on main (the mechanism
does not exist); `tests/integration/execution_engine/test_run_row_write_ahead.py`
(5, DB-real: the running row and the CHECK by attempt; finalize completes;
the shutdown closes and the key holds; the reaper; the readers blind);
the three census gates.

**The data act — prepared, NOT run (AK's GO at the HOLD).** The four July
proposals on production, read under the guard:

| id | run_id | claim | kind | status | decided_by | created |
|---|---|---|---|---|---|---|
| 139162 | 8c09fc83 | 11346f4b | rerun | proposed | — | 2026-07-10 05:55 |
| 141035 | cff08d72 | 2a8fbc8b | rerun | proposed | — | 2026-07-10 08:33 |
| 142531 | f95f9b3b | f01568c0 | rerun | proposed | — | 2026-07-10 10:42 |
| 144261 | f7ef9417 | 9c97c4dd | regenerate_from_current_org | proposed | — | 2026-07-10 13:17 |

The planned write (`scratchpad/r4/withdraw_july.py`, dry-run proven on scratch
against four planted orphans and rolled back; on production it runs only with
`--go`, dump-first):

```
UPDATE tenant_1.repair_proposals
   SET status = 'withdrawn', decided_by = :actor, decided_at = now()
 WHERE id IN (139162, 141035, 142531, 144261) AND status = 'proposed'   -- 4 rows
+ one public.activity_log row per proposal (action repair_proposal.withdrawn,
  entity repair_proposal/<id>, user_id :actor, details {reason, run_id,
  claim_test_id, decision D-502})
```

Every other column, run_id included, stays as it is. The reason (AK's words —
"the claim and run it names no longer exist; withdrawn as evidence of a
non-cascading deletion, AUD-028") lives on the audit row: the table has no
reason column, and adding one is AK's call, not this round's. The actor is
AK's user id (user 1, superadmin on production — AK confirms). `withdrawn`
leaves the repair inbox (it lists `proposed`/`approved`) exactly as `rejected`
does; the apply-guard trigger fires only for `approved`/`applied`.

**Production baselines (read-only).** PG 18.6; tenant_1 head 20260921_0010;
runs 4,687; created records 4,305 (orphans 3); repair_proposals 140 (orphans
4; statuses applied 55 / proposed 85); s6_reinterpretations 50 (orphans 0);
rows with `finished_at` NULL today: 0.

## Part B — the three quarantine product questions

| finding | the verdict, with its evidence |
|---|---|
| **AUD-047** (first, read-only) | **Scratch was right; the two suites were stale.** Production: every vector column is `vector(1024)` and every stored vector is 1024 — entities 31,943 non-null, flow_details 73, validation_rule_details 125; pgvector 0.8.2. Scratch: 1024. The product's embedder: voyage-3 at 1024. The suites hand-built 1536-dim literals tagged `openai/text-embedding-3-small` — the schema's FIRST shape, replaced by migration 20260514_0010 (D-049). They build 1024 and name voyage-3 now: 19 passed, six released. **No product defect on production.** |
| **AUD-045** | **The seed was stale; generation is right.** The refusal was specific: "no org automation produces Order__c.Status__c='Activated' on create (no Flow with that effect…)". Since D-318 the resolvers bind a Flow by the EFFECT its parsed Metadata produces; the D-210 seed's Flows carried no Metadata. With the effects seeded (a same-record assignment, an Order_Log__c create, a filtered parent update reached from start), 24 of 26 flipped to DRAFT; the two left were refusal-text drifts to earlier, stricter reasons (the absence lane's "verified trigger pair"; the placeholder value's "verifiable effect"). The repair-agent round trip had two more product rules its world never showed — the repair gate switch (a public table the harness never carried) and AUD-013's plan on the re-run. Vertical 68, generation tree 215. **A real LLM generation on scratch was not possible**: scratch holds zero connections (no LLM credential) and no production-shaped S1 (one seeded entity per org). Production, read-only, is the evidence generation does not refuse too much on real data: the outcome ledger holds 157 drafts against 26 refusals (2026-06: 30/7, 07: 122/19, 08: 5/0). |
| **AUD-049** | **The reaper is not broken; the world it was shown was.** The D-245 Phase 4 guard reads the env's run policy from `public.environments` — a table the governance harness never carried (it migrates the tenant schema only), so the guard's read failed, every env was "unresolvable" and 0 was reaped: the guard working, the suite blind to it. Round 4's key added the second drift (a record names a run that exists). The suite shims the guard's table where absent, plants its runs and environments, and asserts the guard by name (a read_only env, a production env and an unknown env are never reaped): 13 passed. Production, read-only: 4,245 of 4,305 created records are cleaned; the 60 uncleaned rows are pre-D-230 rows with no environment, never reaped by design; the reapable backlog is empty. |

## Part C — quarantine, so far (the rest follows)

Every quarantined suite was fixed at root or moved to its ruling; the registry
is EMPTY and `test_quarantine_registry` holds it at 0. The verdicts:

| finding | what was wrong | the fix |
|---|---|---|
| AUD-046 (11) | three suites scoped their planted rows by the pre-per-org column `last_synced_from_org_id` while the reconcile, the enrichment counters and the org-status compute read `connected_org_id` (D-323..D-325); REGEX(…) is parsed since D-384 so it cannot stand for "unparsed" | plants set `connected_org_id`; the unparsed fixture is an unknown function (44 passed) |
| AUD-048 (4) | the decision's freshness check read the fixture runs at 382 h old — fixed calendar dates | now-relative dates, order kept (6 passed) |
| AUD-050 (3) | a fixed NOW rotted a waiver into the past; the composer reads its pre-conditions over its own session, invisible to a rolled-back world | NOW live, the one literal derived; the pre-condition read pointed at the suite's session (8 passed) |
| AUD-051 (7) | seven expectations older than the rulings that changed them; two custom runners that could not name a red | moved to the rulings (D-494 twice, the tenant-schema contract, the read's real contracts, a declared identity admitted); both runners are plain pytest and named their reds at once — a missing superadmin, two retired checks, and a crashed run's 15 fixture users at the tenant cap (the suite sweeps its own shape now) |

A run-on-purpose flag was added for the work: `PLIMSOL_RUN_QUARANTINED=1` runs
a quarantined test and marks it, never as the gate.

Recorded, not fixed: the release-run route's code after its D-494 refusal is
dead (`views.py`, the SEC-4 branch is unreachable); five `TRIAGE-SURF-*`
requirements from the round-2 authority world are residue on scratch (AUD-052).
