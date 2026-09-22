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

## Part C — first-screen defects, dead buttons, the rest of quarantine

| finding | what changed | proof |
|---|---|---|
| AUD-042 | the org's message is unescaped ONCE at the read (`org_rejection_message`, both return sites); the landing page's flaky list joins with the character `·` in markup terms | `test_org_message_unescaped_once.py` (production's `&amp;#8377;` becomes `₹`; unescaped once, never twice); the escaping gate is a gate now (safe-marked expressions exempt), the round-3 strict marker gone |
| AUD-043 | a `RelativeDate` value reads in words — "the run date", "5 days after the run date", "1 day before the run date" — wherever a claim's asserted value renders (`relative_date_in_words`, before and inside a LiteralValue) | `test_relative_date_in_words.py`; the planted hostile-tenant page test passes, its strict marker gone |
| AUD-053 | the Remove-target and Unlink-surface forms carry a required reason input (the AUD-041 shape) | CLASS GUARD `test_no_hidden_empty_reason.py`: any hidden `reason` input with an empty value, in any template — **RED on main naming exactly the two forms**; shown able to fail on a planted one and to ignore a visible or prefilled reason |
| AUD-046/048/050/051 | see Part C's quarantine table above | every suite green in its own process; the registry is EMPTY |

**AUD-052 — every suite cleans up what it plants.** One remover
(`tests/integration/_ui_world.py::remove_claim_set_world`) removes a UI
claim-set world from the leaves up, keyed by the ids a suite captured (the
comparison transitions and runs, verdicts, processing runs, inspection results
and jobs, manifests by `payload->>'claim_set_id'`, schedules, link claims,
members and their claims across every test-id-keyed table, the sets, the
inventory members and inventories, the materialised surface entities, and the
materialisation's logical version — whose name is the inventory NUMBER, which
is `MAX+1` and reused, so it had to go with the inventory). Two suites use a
"new since the test began" teardown (the services they drive commit), two
remove by the ids their world holds, one removes its module's set, the
tenant-isolation suite stops swallowing its own cleanup failure (the
mechanism of its leak), and the authority-triage world sweeps its own shape
at setup (five `TRIAGE-SURF-*` requirements from crashed runs were on
scratch). Measured per suite, each in its own process, before and after:

| suite | before (claims / sets / inventories / entities / releases / manifests) | after |
|---|---|---|
| test_3a4_processor | +1 / +1 / +1 / 0 / 0 / +1 | 0 |
| test_3a5_entities | +72 / +1 / +1 / +2 / 0 / +1 | 0 |
| test_phase7_comparison | +144 / +2 / +2 / +4 / 0 / +7 | 0 |
| test_prod_vault | +144 / +2 / +2 / +2 / 0 / +3 | 0 |
| test_tenant_isolation | 0 / 0 / 0 / 0 / +1 (+1 requirement, +1 tenant, +1 section) | 0 |
| test_ui_schedules | 0 / +1 / 0 / 0 / 0 / +3 | 0 |
| test_authority_triage (REPORT_PAGES) | five TRIAGE-SURF worlds on scratch | 0 |

Scratch was then swept once of what earlier measurements (and fifteen past
tenant-isolation runs) had left, back to the morning's counts.

THE ONE-PROCESS-TWICE PROOF: see the closing section.

## Part D — the decision memo, as ruled

| ruling | what changed | proof |
|---|---|---|
| AUD-034 log requests | `migrations/074_api_request_log.sql` (ADDITIVE, dumpless) + `primeqa/shared/request_log.py`: every `/api` request → the time, tenant/user/role from the token the gate read, caller kind (anonymous / cookie / bearer / webhook), method, the url_map RULE (never the path), endpoint, status, duration, the rule's declared minimum tier (`primeqa/core/authz_gates.min_tier_by_rule`, the oracle's walk moved into the package). Never a body, query string, header or token. A bounded queue flushed every 2 s as one INSERT on a daemon thread and at exit (no request pays the write; the disclosed loss is one flush window on a hard kill; drops counted). Kept 30 days: the scheduler's `api_request_log_prune_tick`. NO route pruned. | `tests/unit/test_api_request_log.py` (9: only /api; rule not path; no token/body/query; caller kinds; flush as one INSERT and never raising; the bounded queue; every live rule's tier known; the prune window) + `tests/integration/test_api_request_log.py` (DB-real on scratch: rows land through the real engine; the prune keeps 30 days) |
| AUD-031 link /sections | a "Sections" item in the Settings sidebar after Groups, for every signed-in user | screenshot-gated (below); the dead-link sweep and navigation gates green |
| AUD-033 retire two routes | `POST /plans` and `POST /requirements/<id>/run-substrate` answer 410 with one line naming the successor (`views.RETIRED_ROUTE_NOTE`); the authority table's rows read RETIRED | `tests/unit/test_retired_routes_410.py` — **RED on main (6 of 8)**: every signed-in tier gets the 410 and the successor; an anonymous caller still meets the login redirect |
| AUD-025 keep the redirects | `REDIRECT_ONLY` in `tests/unit/test_route_authority_table.py` — the five bookmark aliases and their successors; the miss sweep reads the id-taking ones from that one source | the unit proof: each answers a redirect to its successor for a signed-in viewer |

## The one-process-twice proof (AUD-052), both halves

The whole `tests/integration` tree, twice in ONE Python process, with the
residue counts read before and after:

```
RUN 1: 1255 passed, 15 skipped, 26 deselected in 158.86s   exit 0
RUN 2: 1255 passed, 15 skipped, 26 deselected in 145.03s   exit 0
before: requirements 0 | releases 4 | sections 1 | users 6 | environments 3 | groups 3 | connections 0 | shared_links 0 | claims 23359 | claim_sets 448 | s4_runs 3 | run_plans 4 | waivers 0 | surface_links 0 | inventories 392 | policies 1 | repair_proposals 0 | entities 528
after:  identical, every count
```

**Identical results: yes. Zero residue: yes.** (Round 3 had met the first half
only.) The first attempt of this run had shown 47 reds and one-row residue in
seven public tables — Part A's new key refusing the boundary-gate and
repair-gate worlds' proposals (they plant their run now), and a helper my
route retirement had sliced out of `views.py` (restored; the undefined-names
gate caught it). Both fixed at root before the proof above.

## Guards, each proven red first

| guard | red where |
|---|---|
| `test_running_rows_invisible` (4) | main: 39 readers named |
| `test_run_row_before_provisioning` (7) | main: ImportError (the mechanism does not exist) |
| the three census gates in `test_constraints_triage` | main: the keys absent |
| `test_no_hidden_empty_reason` (2) | main: both AUD-053 forms named |
| `test_retired_routes_410` (8) | main: 6 of 8 |
| `test_api_request_log` (9) + the DB-real test | main: ImportError |
| `test_relative_date_in_words` (6) | main: ImportError |
| `test_org_message_unescaped_once` (3) | main: 3 failed |
| `test_template_escaping` (2, the gate now) | the strict marker of round 3 flipped |
| `REDIRECT_ONLY` proof (5) | new in the authority table; the aliases had been counted as orphans |
| `test_quarantine_registry` at 0 | holds the list empty; a new entry is a decision |

## Migration classifications

| migration | class | at the merge |
|---|---|---|
| tenant `20260922_0010` (run row before provisioning: enum label, nullable finished_at + CHECK, three keys) | REJECTING | dump-first: `s4_execution_runs`, `s4_created_records`, `repair_proposals`, `s6_reinterpretations`; apply; read back the keys' validity and the CHECK; prove the NOT VALID key refuses a new orphan in a rolled-back transaction |
| public `074_api_request_log.sql` | ADDITIVE | dumpless (a new table) |
| the data act (four July proposals withdrawn) | DATA, gated | AK's GO; dump `repair_proposals` first; `withdraw_july.py --go --actor=<AK's user id>` |

## The screens AK is being asked to approve (`round4-fixtures/`)

Fixture screenshots (scratch, a planted world, an admin):

| screen | file |
|---|---|
| Settings sidebar with the new **Sections** item after Groups (AUD-031) | `settings_sidebar_sections.png` |
| Release decision tab: a declared target with the **Remove** form and its reason field (AUD-053) | `release_decision_remove_target_with_reason.png` |
| Requirement page: a declared conformance surface with the **Unlink** form and its reason field at the foot of the page (AUD-053) | `requirement_unlink_surface_with_reason.png` (full page; the panel is the last block) |

Production-data renders (D-487; a local server over the production database,
read-only at the server side, AK's own user):

| screen | what it shows | file |
|---|---|---|
| Settings sidebar | the Sections item on production data | `PRODUCTION_settings_sidebar_sections.png` |
| Release 16 decision tab | unchanged shape — production holds no declared target, so no Remove form renders; the form is the fixture's | `PRODUCTION_release_16_decision.png` |
| Requirement 320 | unchanged shape — production holds no declared surface link, so no Unlink form renders; the form is the fixture's | `PRODUCTION_requirement_320_surfaces.png` |

The merge is gated on **"screens approved"**.
