# VERIFICATION — Step 6a: layout collapse, part 1 (Requirements and Results)

**Branch** `step-6a-work-surfaces` from `main` @ db35fa8, carrying Step 2a
(D-489) via merge c119081. Design b44b55f (GO 2026-09-11, six leans ratified).
Build: this commit. Merge gated on AK seeing the fixture screenshots
(`step-6a-fixtures/`, twelve, including three rendered on REAL PRODUCTION DATA
per the D-487 standing correction).

## 0. Corrections found at build (folded into the LLD)

1. **The readiness column was unaffordable, and that became Step 2a.** The
   approved mock puts a readiness pill on every requirement row; the canonical
   resolver issued three queries per claim-environment pair, and a 20-row page
   spans about 136 claims on production. That HOLD produced D-489, and the
   column is now affordable. 6a resumed with the mock intact.
2. **My own org band tripped the D-487 class.** `_org_state.html` iterated
   `org_state.orgs.items`, and Jinja resolved `.items` to the dict's METHOD —
   a 500 on every page carrying the band. The guard from D-487
   (`test_templates_dict_method_names.py`) catches exactly this; I wrote the
   template and ran the page before running the guard. Fixed at the SOURCE
   rather than in the template: the console's key is now `entries`, because a
   dict key called "items" is the hazard by construction.
3. **The user-menu Settings link was ungated.** The gear beside it is gated on
   `can_see_settings`; the menu link was not, so a viewer was offered a page
   the route refuses. Same gate now — one matrix, not two.
4. **The runs list never selected `plan_id`.** Step 4 stamped it on the run
   row, but the Results projection did not read it, so the new "from" column
   would have said "no plan (pre-planner)" for every row including the planned
   ones. Found because the fixture had a plan-stamped run and the cell still
   read "no plan". Added to the projection and carried into the row dict.
5. **A planted "fixture" requirement reads `jira`.** `create_requirement`
   establishes the identity itself, and Step 1's `establish` is
   `ON CONFLICT DO NOTHING` by design — an identity is never re-classified
   behind the operator's back. The scratch world therefore updates the row
   directly to reach the hidden-by-default state, and says so; on production
   that state arrives the honest way, at establish time.
6. **The bar shows three labels plus a gear, not four labels.** `SIDEBAR_ITEMS`
   holds four, as the brief specifies, but `base.html` has excluded the
   Settings item from the horizontal strip since long before 6a — it renders as
   the gear on the right. That is pre-existing markup and 6a did not change it;
   the registry, the gates and the landing map all carry four.

## a. NAVIGATION — `test_step_6a_navigation.py`, 8

| item | proven |
|---|---|
| four items, in order | the registry renders exactly `requirements, results, releases, settings` |
| the removed slots are gone | `run_tests`, `my_reviews`, `test_library`, `dashboard`, `my_tickets` are absent from the registry |
| the disabled two are untouched | `coverage` and `audit_log` remain, and never render |
| **a viewer now sees the work** | `["Requirements", "Results", "Releases"]` — before 6a a viewer saw NO Requirements item, because the slot was gated on a MEMBER capability |
| the tiers | member and admin as stated; superadmin all |
| the new capabilities | view_* at VIEWER, manage_* at MEMBER |
| **the new capabilities did not widen Settings** | a member sees Settings because it is gated on ANY `manage_` capability and MEMBER already held `manage_test_suites` / `manage_knowledge`; asserted by removing the new three and showing Settings still renders, and that a VIEWER still never sees it |
| the active item follows the path | 13 paths including the redirected ones (`/claims` lights Requirements, `/ui-report/runs/x` lights Results, `/dashboard` lights Releases) |

## b. THE ORG-STATE BAND

Renders on Requirements, Results and Releases, each group independently
available. On production it reads:

```
ORG  env-59 seq 259 · synced 2026-09-11T10:43 | env-78 seq 258 · synced 2026-09-11T07:15
POLICY  Plimsol default v1 · 10 rules
CADENCE 0 6 * * * env 59 · last plan f24bae38
```

The last sync comes from `s1_sync_jobs`, per ruling 5:
`connected_orgs.last_sync_completed_at` is NULL for both bound orgs while syncs
complete daily, so reading the org column would have printed "never synced"
under a live daily sync. On scratch, with no org bound, the same band reads
"no org is bound to an environment", "no active quality policy — nothing
grades", "no enabled schedule" — degrading in place, never failing.

## c. REQUIREMENTS — `test_step_6a_pages.py` + the shoot

| item | proven (scratch fixture world) |
|---|---|
| three tabs | Requirements / All claims / Needs review, each rendering its own body |
| the badge equals the tab | the nav badge and the tab count are the same number, and when nothing waits the badge is ABSENT rather than zero — asserted as an equality with the count, not as a presence |
| identity verbatim | `S6A-HEALTHY` etc. rendered monospace, unaltered |
| functional summary | `4 passed`; `2 passed · 1 failed`; `2 passed · 2 never run` |
| **conformance says "no surface declared"** | on the three requirements with no declaration — never `0 / 0 / 0`; the declared one reads `1 surface · 2 checks · 1 failing · 1 need a human` |
| **the lane split** | the conformance-only requirement reads readiness `NO_FUNCTIONAL_CHECK` ("no functional check to grade"), NOT NEVER RUN |
| fixtures hidden with the count | "1 fixture requirement hidden from this view · show"; revealing takes 4 rows to 5 |
| gaps rendered as gaps | 2 gap rows, never hidden |
| the readiness filter | offered, and an empty result renders its own state |

## d. RESULTS

| item | proven |
|---|---|
| the three lenses | kept, unchanged |
| the kind filter | `all` / `functional` / `conformance`; `conformance` alone hides the functional table, `functional` alone hides the conformance one |
| a grouped lens says the filter does not apply | asserted on the requirement lens |
| both lanes in one list | 15 functional rows + 20 conformance rows on the fixture world |
| **"from"** | `requirement S6A-HEALTHY` (linked to the plan) beside `no plan (pre-planner)`; on PRODUCTION the same column reads **`schedule 1`**, from AK's own fired schedule |
| conformance currency is not the org axis | `latest` / `superseded` against the claim set, with the sentence in the title |
| the conformance run view re-homed | `/ui-report/runs/<id>` → `/runs/conformance/<id>`, same template |
| the repair + schedules panels | kept in place, admin-gated |

## e. THE PERMISSION MATRIX

| page / action | viewer | member | admin | unauthenticated |
|---|---|---|---|---|
| Requirements, All claims, Needs review (read) | 200 | 200 | 200 | → `/login` |
| approve a draft | control ABSENT | offered | offered | → `/login` |
| Results, both kinds, a conformance run (read) | 200 | 200 | 200 | → `/login` |
| Releases (read) | 200 | 200 | 200 | → `/login` |
| schedules / repair panels | absent | absent | present | → `/login` |
| Settings (nav + menu link) | absent | present (pre-existing) | present | → `/login` |

## f. ROUTE HYGIENE — `test_step_6a_routes.py`, 3 + the page suite

Every moved path answers 302 to its new home: `/claims` and `/test-cases` →
`?tab=claims`; `/claims/inbox` and `/reviews` → `?tab=needs-review`;
`/ui-report` → `/runs/substrate?kind=conformance`; `/ui-report/runs/<id>` →
`/runs/conformance/<id>`. `/dashboard`, `/run`, `/runs`, `/tickets`, `/suites`
still answer. The claim detail page did not move.

**The dead-link sweep** walks every template's internal `href` and asserts it
matches a route or a redirect: green.

The three `/ui-report` TOOLS (compare, coverage, evidence) keep their URLs —
they have no home in the 6a mock, and moving them on a guess would be churn.
Stated, not silently deferred.

## g. Suites

| suite | result |
|---|---|
| `tests/unit` | **5079 passed**, 3 failed — the live-parity trio, red only because `DATABASE_URL` pointed at scratch (green against the real database in D-489's run) |
| `tests/integration/test_representation` | **442 passed, 4 skipped** |
| pages (`REPORT_PAGES=1`) | **23 passed**, including the two report-page tests updated for the re-homing |
| DB-real corpus | see §h |

## h. The screenshots

Scratch fixture world (`step-6a-fixtures/`): `requirements_tab_populated`,
`requirements_fixtures_revealed`, `requirements_filtered_empty`,
`requirements_tab_all_claims`, `requirements_tab_needs_review`,
`requirements_viewer_read_only`, `results_both_kinds`,
`results_conformance_only`, `results_grouped_lens_kind_na`.

**Production data, read-only** (D-487): `PRODUCTION_requirements`,
`PRODUCTION_results_both_kinds`, `PRODUCTION_needs_review`. The app ran locally
against production through the guard proven in D-489 — a server-side connection
option under which a write ERRORS rather than lands. GET only; nothing written.
Production renders 10 requirement rows, the band above, the badge at 8, "no
surface declared" on every row (no declaration exists on production yet),
readiness CURRENT and CANNOT_DETERMINE, and the Results "from" column reading
`schedule 1`.

## i. Page cost, measured

Queries per render, counted on scratch so the number is the code's:

| page | queries |
|---|---|
| `/requirements` | 51 |
| `/requirements?tab=claims` | 59 |
| `/runs/substrate` (both kinds) | 46 |
| `/releases` | 16 |

Before Step 2a the Requirements page would have added three queries per claim
on top — over 400 on a production-sized page. It is affordable now, but 51 is
not obviously right either: the identity census, the claim counts, the release
chips and the board are four separate bulk reads that could be one. Ledgered
rather than optimised here.

## §h DB-real corpus

**111 passed, 7 skipped, 1 failed** on a cleaned scratch — the failure is the
known `test_report_slice::test_a_runs_list_carries_both_recorded_runs` window
artefact (a 2026-08-31 baseline behind 140 newer rows in a fixed 50-row
window), ledgered since D-488 and unrelated to 6a.

A seventh build correction, found by that run: **the world remover left the
inventory's own checkpoint behind.** `create_inventory_version` mints a
`logical_versions` row named `surface-materialization-inv<N>`; deleting the
inventory without it collided with the next suite that cuts the same inventory
number, and four 3A-3 tests went red on a unique-name violation. The remover
now deletes the checkpoint and the entities materialised at it. Fixture
hygiene, not product code — but it would have looked like a Step 6a regression
to anyone running the corpus after the shoot.

## j. Post-merge transcript (2026-09-12)

**Merge** efe39a3, pushed to `main`; author AK, zero `Co-Authored-By`.
Classified **WRITE-FREE** (zero migration files, zero alembic diff, zero DDL
and zero SQL write verbs added, 18 added SQL statements all SELECT), dumpless.
No schema change, so no code/schema window in either direction.

**Deploy.** Four services SUCCESS. Health 200, `error_rate 0.0`, zero
error-class lines in 400 log lines per service.

### The read-only production proof, GET only, in TWO sessions

The gate bug is the point, so the same page was rendered under a member and a
viewer:

| | MEMBER (tester) | VIEWER |
|---|---|---|
| `/requirements` | **200** in 5.15 s | **200** in 5.79 s |
| nav labels | Requirements · Results · Releases | Requirements · Results · Releases |
| Settings gear | present | **absent** |
| rows rendered | 10 | 10 |
| readiness (live) | CURRENT, CANNOT_DETERMINE | CURRENT, CANNOT_DETERMINE |
| conformance cells | "no surface declared" | "no surface declared" |
| Needs review tab | 200 | 200 |
| Approve control | offered | **absent** |

Before 6a a viewer saw **no Requirements item at all**. Both sessions show the
same org band:

```
ORG env-59 seq 259 · synced 2026-09-11T10:43 | env-78 seq 258 · synced 2026-09-11T07:15
POLICY Plimsol default v1 · 10 rules
CADENCE 0 6 * * * env 59 · last plan f24bae38
```

### Results, both kinds, with provenance

`/runs/substrate?group=runs&since=7d` renders **200 in 3.24 s** with all three
kind chips. **20 functional rows, every one reading `schedule 1`**, linked to
plan `f24bae38` — AK's own fired schedule, so the "from" column is populated
from real provenance rather than a fixture. **4 conformance rows** carrying
LATEST and SUPERSEDED currency.

### The redirects, live on production

| old | lands on |
|---|---|
| `/claims` | `/requirements?tab=claims` |
| `/claims/inbox` | `/requirements?tab=needs-review` |
| `/reviews` | `/requirements?tab=needs-review` |
| `/test-cases` | `/requirements?tab=claims` |
| `/ui-report` | `/runs/substrate?kind=conformance` |

Screenshots: `LIVE_requirements_tester.png`, `LIVE_requirements_viewer.png`,
`LIVE_results_both_kinds.png`.

Recorded as **D-490**. Part 2 (Releases and Settings) is the next slice.
