# VERIFICATION — Step 5, the Release Quality Policy

**Branch** `step-5-quality-policy` from `main` @ 36228de. Design 4ce0303 (GO
2026-09-10, six forks ratified). Build: this commit. Merge gated on AK seeing
the fixture screenshots (`step-5-fixtures/`, eight — seven on the scratch
world, one rendered on REAL PRODUCTION DATA per the D-487 standing
correction).

## 0. Corrections found at build (folded into the LLD)

1. **A conformance claim was being graded as a functional check with no
   run.** Step 2's scope readiness enumerated every claim in the release
   against the S4 environments; a conformance claim never runs on that lane
   (its evidence is a browser processing run), so it read NEVER_RUN for ever
   and Evaluate refused a healthy release. The lanes are split:
   `release_scope_readiness` reads the FUNCTIONAL lane, and the policy's
   conformance axis grades the browser plane — a conformance claim with no
   verdict on the latest processing run is ungraded THERE, named on the
   grading line. Found by the shoot (the fixture release refused with two
   "NEVER RUN" conformance rows). The D-198 per-environment cards still
   count every claim; ledgered (FIX PLAN Medium) rather than folded in,
   because it moves that card's numbers.
2. **The ACTIVE map set lives in PUBLIC tables (065/066), not the tenant
   schema.** The level read (`s5_standard_maps` × `s5_standard_map_sets`)
   runs under a savepoint and the evidence carries `levels_available`; the
   tenant-only harness passes `rule_levels=` instead. A missing level is
   recorded as `unmapped` on the conformance line, never silently as AA.
3. **The policy is marked USED before the decision row is written.** The
   grading runs in the tenant transaction and the decision row in the public
   one; the conservative order is to freeze the policy first — a
   used-but-unrecorded policy is merely frozen early, a recorded decision
   under an unfrozen policy would be the real defect.
4. **A waiver's expiry is required at the table, not only in the form.**
   `expires_at` is NOT NULL with `CHECK (expires_at > created_at)`, so
   "no expiry, no waiver" (ruling 4) holds against a direct INSERT too.
5. **The decider's name is read with a plain SELECT, not the ORM row.** The
   `User` model carries columns the scratch copy lacks; loading the whole row
   to print a name took the decision tab down with a 500 on the scratch shoot
   (the D-487 class: a render must not depend on more than it shows). The read
   is now two columns under try/except.
6. **The template gate sweep is scoped to the decision surfaces.** Four
   pre-existing pass-rate colour bands (dashboard ×3, the S4 run list) compare
   a metric to a number for a TEXT COLOUR on non-decision pages; the sweep
   asserts zero on the decision surfaces and pins those four by name, so a
   fifth cannot appear unnoticed.

## a. THE POLICY OBJECT (§a) — harness `test_step_5_policy.py`, 7

| item | proven |
|---|---|
| the seed is a DRAFT with ten rules (ruling 3) | `created_by` NULL, ten rules in the seeded order, rule 10 carries parameter 95, rule 5 is item-scoped; `active_policy` is None; a draft REFUSES to grade |
| a human activates it | `activate` sets status/actor/time, audits `quality_policy.activate`; a second activation refuses ("only a draft activates") |
| a new version retires the previous | v2 activates → v1 `retired`, both audited, one ACTIVE per tenant enforced by a partial unique index |
| the vocabulary is closed | the dataclass refuses an unknown axis / effect / scope, a condition not admitted on that axis, a missing or unwanted parameter; the TABLE refuses the same (an admitted-pairs CHECK and an effect CHECK) — proven by direct INSERT |
| rules change only on a DRAFT | an INSERT of an eleventh rule on the ACTIVE policy raises at the trigger |
| a USED version refuses edit | after `mark_used`: name, note, status→draft, a rule UPDATE and a rule INSERT all raise; retirement is the one state change still admitted |
| nothing is ever deleted | DELETE on `quality_policies`, `quality_policy_rules`, `quality_waivers` all raise |

## b. THE ENGINE (§b) — unit `test_quality_policy_engine.py`, 11

The ladder, over stubbed evidence (pure, no DB):

| evidence | recommendation | why |
|---|---|---|
| clean | `go` | no rule blocked, reviewed or conditioned; seven lines, all "no rule triggered"; grading reads "Nothing ungraded." |
| a functional failure | `no_go` | rules 1 + 10 named on the functional line, effect BLOCK (graded → NO GO) |
| a level-A conformance FAIL | `no_go` | rule 2 |
| a level-AA conformance FAIL | `conditional_go` | rule 3, effect CONDITIONAL |
| NEEDS_HUMAN pending | `cannot_determine` | rule 4, effect REVIEW ("a REVIEW") |
| ungraded present | `cannot_determine` | rule 8, effect BLOCK ("an ungraded BLOCK") — **and with a CONDITIONAL beside it, still `cannot_determine`; with a graded failure beside it, `no_go` (D9)** |
| an active waiver | `go` | rule 5 is item-scoped: it allows THAT item and never moves the release on its own |
| tool drift only | `go` | rule 6 |
| environment drift | `cannot_determine` | rule 7 (REVIEW) |
| a production target ungraded on metadata | `no_go` | rule 9 (§d), a graded BLOCK |
| pass rate 90 vs the parameter 95 | `no_go` | rule 10; with NO counted run the condition does not fire (a rate that does not exist is not "below") |

Ruling 2 is the split: a BLOCK from a graded observation is `no_go`; a BLOCK
from `ungraded_present`, or any REVIEW, is `cannot_determine`. D9 and D-482
both hold, and no evidence path can produce GO or CONDITIONAL GO over an
unknown.

## c. WAIVERS (§c, rulings 4 + 5) — harness + scratch

| item | proven |
|---|---|
| reviewer + reason + expiry required | the service refuses an empty reason and a missing expiry; the TABLE refuses an INSERT with no `expires_at` (NOT NULL) and one that expires before it was created |
| a waiver flips ONE item | a failing functional claim leaves the functional observation ("1 waived"), the waivers line reads ALLOW naming rule 5, and the release is `go` again |
| its expiry un-flips it | graded at `now + 8 days` the same rows read `no_go` and the waivers line says "1 expired, not counted" |
| revocation is the state change | `revoke_waiver` → state `revoked`, the item counts again; DELETE raises; a substance UPDATE raises; a revoked waiver cannot be un-revoked |
| never "resolved" | with a NEEDS_HUMAN waiver the human-reviews line reads **"0 pending, waived by PrimeQA Admin until 2026-09-24: PLM-A11Y-064 on …"** — the actor and the date, never the word "resolved" (ruling 5's guard, asserted in the test and visible in the screenshot) |

## d. PRODUCTION TARGETS (§d) — harness

A declared `is_production` + `read_only` target, with the plan's exclusions
in hand:

- a data check the plan EXCLUDED there by reason is **not ungraded** — the
  environments line reads "… · 1 data checks not run here by policy
  (read-only) · not ungraded", and no ungraded item names that pair;
- an inspection check the plan ADMITTED there with no current run fires rule
  9 → BLOCK → `no_go`, and the line says "1 inspection check(s) admitted but
  never run here".

Step 4's exclusion and Step 5's grading therefore agree by construction: the
census reads the plan's exclusions before it reads absence.

## e. FINAL DECISION (§e) — scratch `test_step_5_scope.py` + the shoot

- the decision row records **policy id + version + plan id** (migration 073's
  columns), and the policy's `first_used_at` is set in the same act;
- the human's record lands BESIDE it: recommendation GO, final decision NO GO
  by the actor with the reason, both rendered on the card;
- the web form gates the override: a word more permissive than the
  recommendation needs a reason AND the Admin tier ("Record GO with reason" is
  the only path to a GO over a non-GO); a word at or below it is a MEMBER's
  record. The existing Admin API path is untouched.

## f. GATE LOGIC LIVES ONLY IN THE POLICY — `test_templates_no_gate_logic.py`

Grep-asserted: no template on the decision surfaces compares a metric to a
number, reads a criteria threshold, or assigns an effect / recommendation
word; the four pre-existing colour bands elsewhere are pinned by name.

## g. Suites run (all on this build)

| suite | result |
|---|---|
| `tests/unit` (whole) | **5056 passed, 3 failed → 3 green on re-run.** The three (`test_run_router::test_real_approved_claims_route_single_dormant`, `test_strategy::test_single_matches_live_latest_run_rule`, `test_substrate_decision_compute::test_assembled_verified_matches_live_passed_rule`) are the LIVE-PARITY tests: they read the real database through `DATABASE_URL`, and the sweep had that pointed at scratch. Re-run against the real database they are **3 passed** — parity holds on this build. |
| `tests/integration/test_representation` (the local harness) | **436 passed, 3 skipped** |
| the DB-real corpus (21 files incl. `test_step_5_scope.py`) | **111 passed, 7 skipped, 1 failed** — the one red is `test_report_slice::test_a_runs_list_carries_both_recorded_runs`, which asserts a run from 2026-08-31 sits inside a fixed 50-row window that now has 140 newer rows in front of it on scratch. It fails on accumulated data, not on code, and both Step 5 fixture runs were removed before the corpus ran. Ledgered (FIX PLAN Medium). |
| pages (`REPORT_PAGES=1`, own invocation) | **9 passed** — report pages, Step 4 pages, Step 5 pages |

## h. The screenshots (`step-5-fixtures/`)

The scratch world: a release with a declared target, three functional checks
(two pass, one FAIL — stamped, grounded, CURRENT), two conformance checks on
an approved active set with a processing run (one PASS, one NEEDS_HUMAN), and
an executed plan.

| file | what it proves |
|---|---|
| `decision_tab_refused_no_active_policy.png` | ruling 3: with the seed still a DRAFT the card refuses by sentence — "No active quality policy — activate one (Settings → Quality policy; CLI in v1) before a release grades." No silent default. |
| `decision_tab_no_go_named_failure.png` | the six lines + grading over real rows: functional BLOCK (rules 1, 10) with the failure NAMED, conformance "1 pass · 1 needs human", human reviews REVIEW (rule 4), environments naming the declared target and the executed plan, "Nothing ungraded." → **NO GO** |
| `decision_tab_after_functional_waiver_review_pending.png` | the waiver flips the functional item; the human review still REVIEWs |
| `decision_tab_go_after_waivers.png` | both waivers applied → **GO**, the human-reviews line reading "0 pending, waived by … until …" |
| `decision_recorded_with_policy_and_plan.png` | Evaluate RECORDED the decision: "recommendation GO · policy Plimsol default v1 · plan 62411e16" |
| `decision_final_beside_recommendation.png` | the human's NO GO with its reason, beside the GO recommendation — never replacing it |
| `settings_quality_policy_read_only.png` | §f: the active policy's ten rules read-only, "first used … · immutable", and the CLI named as the v1 authoring surface |
| **`PRODUCTION_release_16_decision_card.png`** | **the D-487 standing correction, applied at BUILD time** — see §i |

## i. The production-data render (D-487)

The card is rendered over **real production data**, read-only, before the
merge:

- the production database is read through the TCP proxy with
  `default_transaction_read_only = on` set on every connection before any
  statement — a write would ERROR, not land. Nothing was written; the
  credential was fetched into memory and never printed, never in argv.
- production carries no policy tables yet (the tenant migration lands in the
  merge runbook), so the render uses the SEED policy exactly as the migration
  writes it and the empty waiver set a fresh tenant has. **Every other input
  is production's**: release 16 "Parity Window 1", its 19 functional checks
  (19 counted, pass rate 100.0%), the executed plan **9ea0c522** (AK's own
  run of 10:17Z), the latest conformance run 368f09eb, comparison 022d8cc0
  with `environment moved`, the fallback target "Prime QA NEW" at 19 of 19
  current, "Nothing ungraded." → recommendation **GO**.
- the render is the ACTUAL partial through the app's Jinja environment with
  the app's own stylesheet, not a mock.

The merge proof re-renders the same card live on the deployed app after the
migrations; this build-time render is what the D-487 correction asks for at
push: the page, on production data, not the link to it.

## j. What this step does NOT do (§f)

No layout collapse (Step 6); no policy authoring UI beyond the read-only view
(authoring is CLI in v1, said on the page); no persona comparison; no backfill
of old decision rows (their policy columns stay NULL — "decided before
policies"); `releases.decision_criteria` untouched.

## k. Post-merge transcript (2026-09-11)

**Merge** c0c0c76 (parents 36228de + 067153c), pushed to `main`; author AK,
zero `Co-Authored-By`.

### k.1 Migrations, applied BEFORE the deploy

| migration | classification | ruling | applied |
|---|---|---|---|
| tenant `20260912_0010` | ADDITIVE, **not write-free** (two seed INSERTs) | **DUMPLESS** — every written row is a NEW row in a NEW table, in DRAFT, referenced by nothing; no pre-existing row is read, altered or deleted (every UPDATE/DELETE token in the file sits inside a trigger definition) | head `20260911_0010` → `20260912_0010` |
| public `073` | ADDITIVE (3 nullable columns, no default, no constraint, no data write) | dumpless | three columns present, NULL on the one existing decision row |

### k.2 Read-backs and 25 refusal proofs

Structure: three tables, six indexes including the one-active partial unique
index, seven CHECKs on the rules table, four triggers. The seed is present and
**DRAFT** with ten rules, `created_by` NULL, `first_used_at` NULL, zero active
policies, zero waivers.

Every refusal was **attempted for real** inside one rolled-back transaction —
the closed vocabulary (7), the waiver rules (4), never-deleted on all three
tables with rows present (3), waiver immutability (2), one active policy (1),
draft-only rules (1), used-policy immutability (6) — and the one admitted
change, a used policy retiring, was shown allowed. 25 as expected, 0 not.
After rollback production carries exactly what the migration wrote.

**Three first-pass proofs were WORTHLESS and are recorded as such**: they
matched zero rows, so no row trigger fired and they scored as passes. An empty
waiver table; a draft policy "changed" to draft; a rule edit against a policy
with no rules. The harness now takes a precheck row-count and reports
`INVALID PROOF — matches 0 rows`. This is AK's standing rule earning its keep
on the same day it was given.

### k.3 Deploy

Four services SUCCESS on the merge commit. Health 200, `error_rate 0.0`,
`errors_total 0`. Zero error-class lines in 400 log lines per service.

### k.4 The read-only proof, GET only, on the deployed app

| read | result |
|---|---|
| `/releases/16?tab=decision` | **200**, the quality card present |
| the card, policy DRAFT | "No active quality policy — activate one (Settings → Quality policy; CLI in v1) before a release grades." |
| evidence lines rendered | **0** — see the gap below |
| recorded decision | "No decision recorded yet" |
| waivers on the release | 0 |
| `/settings/quality-policy` | **200**, seed as the only version, draft, 10 rules, "No active policy — no release grades until a human activates one" |
| Step 4 surfaces still render | plan view, `/runs/substrate`, `/requirements` all 200 |
| written by the act | nothing |

**The gap, stated plainly.** The brief asked for the six lines AND the policy
still DRAFT. Those cannot both appear today: the card gates the entire evidence
block on an active policy, so a draft policy yields the refusal alone. The
ENGINE does assemble them — read-only over live production data it produces all
six lines plus grading for release 16:

```
functional     19 functional check(s) in scope · 19 with a counted run · pass rate 100.0%
conformance    0 conformance check(s) in scope · latest run 368f09eb (WCAG22) · 0 pass
regressions    comparison 022d8cc0: 0 new conformance failure(s) · environment moved
waivers        0 active waiver(s), 0 applied
human_reviews  0 pending · no human review outstanding
environments   targets (fallback:evidence-active): Prime QA NEW: 19 of 19 checks current
               · plan 9ea0c522 executed 2026-09-10T10:17
grading        Nothing ungraded.
```

and would read **GO** on activation. Rendering those observations with a
"not graded — no active policy" banner before activation is a one-template
change. It is offered, not made.

### k.5 Pending AK acts

1. Activate the seed policy (CLI or Settings). Until then nothing grades.
2. Then Evaluate on a release, to record the first decision citing a policy
   and a plan.
