# LLD — Step 6a: layout collapse, part 1 of 2 (Requirements and Results)

**Status: DESIGN — HOLD (2026-09-11).** Branch `step-6a-work-surfaces` from
`main` @ db35fa8 (Step 5 live, D-488). Mocks approved by AK. Part 2 (Releases +
Settings) is its own slice; this design leaves those pages' routes untouched.

The collapse is not a re-skin. Today the nav offers eight slots, four of which
are the same work seen from different angles: a requirement's claims live under
Test Library, the same claims awaiting approval live under My Reviews, their
runs live under Results, and their conformance runs live under an orphan
`/ui-report` tree that nothing links to. 6a gives that work two homes —
**Requirements** (what should be true) and **Results** (what happened) — and
makes every other path a redirect.

## 0. Pre-flight facts, cited (read on this branch; production read read-only)

| fact | where |
|---|---|
| `SIDEBAR_ITEMS` holds **10 entries**, 2 disabled (`coverage`, `audit_log`), so **8 render**: Requirements, Run Tests, Results, My Reviews, Dashboard, Test Library, Releases, Settings. Each declares `permission` / `permission_any` / `permission_any_prefix`; `build_sidebar` filters, sorts by section, marks `active` by longest-prefix with `active_also_for`, and sets `section_first`. | `core/navigation.py:60-175, 186-260` |
| Those permission strings are **role-derived capabilities**, not DB rows — the permission-set layer was deleted in D-245 and its tables dropped in 057. `_CAPABILITY_MIN_TIER` maps 26 capabilities to a min tier; `_role_capabilities(role)` returns those at or below the caller's tier, superadmin gets all. The context processor injects `sidebar_items`, `has_permission`, `user_permissions`, `can_see_settings`. | `core/permissions.py:70-160` |
| The nav badge already exists, on **My Reviews only**: `_count_pending_reviews_for` = `SELECT COUNT(*) FROM test_claims WHERE status='draft' AND valid_to IS NULL`, tenant-wide, best-effort → 0. Production today: **8**. | `core/permissions.py:143-149, 165-180`; production |
| `base.html` renders `sidebar_items` in one loop, using `item.url`, `item.active`, `item.badge` (`data-nav-badge="<id>"`). No page names a nav item directly. | `templates/base.html:27-40` |
| `/requirements` already carries the Step 1 identity work: `external_key` per row, origin chips, `HIDDEN_BY_DEFAULT` fixtures hidden with a visible count, referential **gaps** never hidden, an origin filter, plus per-requirement claim counts by status and release chips. | `views.py:2360-2502` |
| `/runs/substrate` carries three lenses (`group` = `requirement` \| `cause` \| `runs`), an outcome filter with the `?status=` alias, a verdict / env / `since` facet set (default 24h), the D-214 schedules panel (admin+), the D-215.1 repair panel (admin+), and Step 2 readiness per row via `readiness_for_pairs`. Its `active_page` is **`test_library`** — a stale label. | `views.py:3856-3985` |
| The conformance surfaces are an **orphan tree**: `/ui-report`, `/ui-report/runs/<job_id>`, `/ui-report/evidence`, `/ui-report/compare`, `/ui-report/coverage`, all `@require_tier(MEMBER)`, `active_page="ui_report"` — a value no nav item carries, so nothing in the product links to them. | `views.py:5221-5294` |
| Existing redirects already in place: `/runs` → `/runs/substrate`, `/runs/<int>` → its substrate successor, `/test-cases` → `/claims`, `/reviews` → `/claims/inbox`, `/tickets` → `/requirements`, `/suites` → …, `/run` → `/releases?from=run` (D-486). | `views.py:5190-5320` |
| Consoles available, no new reads needed: `s3_generation_console.read_requirement_claims / list_claims / count_claims_by_requirement_status / keys_with_claims`; `s4_execution_console.list_runs / runs_overview / requirement_run_health / latest_runs_by_test`; `requirement_surface_console.read_requirement_surfaces` → `{available, active_inventory_version, declared[], counts{surfaces, checks, failures, human_review}}`; `run_plan_console.create_plan`; `ui_report_console.list_processing_runs / run_report`. | those modules |
| **The lane split (Step 5, D-488)**: `release_scope_readiness` now enumerates the FUNCTIONAL lane only — a conformance claim (`archetype = 'ui'`) never runs on S4, so org-axis readiness is meaningless for it. The per-environment substrate cards still count every claim (FIX PLAN Medium, open). | `substrate_decision.py`; D-488 |
| Production state for the band and the badge: orgs bound to an environment are **env-59** (seq 257) and **env-78** (seq 258); the active policy is **Plimsol default v1**; schedule 1 is enabled on env 59 with last plan `f24bae38`; 8 draft claims; **0** NEEDS_HUMAN verdicts and **0** HUMAN_REVIEW members on the active set; 1849 functional runs, 4 conformance processing runs. | production, read-only |
| **`connected_orgs.last_sync_completed_at` reads NULL ("never") for BOTH bound orgs, while `s1_sync_jobs` holds a completed sync at 07:15:06Z today.** The org column is not maintained by the sync engine. | production, read-only |

## a. NAVIGATION — four items

`SIDEBAR_ITEMS` becomes four entries, in this order:

| item | url | gate (new capability) | badge |
|---|---|---|---|
| Requirements | `/requirements` | `view_requirements` (VIEWER) | yes — see below |
| Results | `/runs/substrate` (`active_also_for` `/runs`, `/results`, `/ui-report`) | `view_results` (VIEWER) | — |
| Releases | `/releases` | `view_releases` (VIEWER) | — |
| Settings | `/settings` | `permission_any_prefix: "manage_"` (unchanged) | — |

Removed slots and where their content now lives:

- **Run Tests** — already a redirect since D-486 (`/run` → `/releases?from=run`).
  The slot goes; the route stays.
- **Test Library** (`/claims`) → the Requirements **All claims** tab (§c).
- **My Reviews** (`/claims/inbox`) → the Requirements **Needs review** tab plus
  the nav badge (§c).
- **Dashboard** → Releases, in **6b**. Its content has no home yet, so **the
  route and the page stay exactly as they are**; only the nav chip goes, so the
  bar reads four. `/dashboard` keeps working, keeps its landing-page entry, and
  6b re-homes its content and then retires it. (**Fork 1.**)
- `coverage` and `audit_log` are already `enabled: False` and render nothing;
  they stay in the registry untouched.

### The badge count, defined

**Badge = draft claims awaiting approval + pending human reviews**, one number
on Requirements, linking to the Needs review tab.

1. **Draft claims** — `test_claims` where `status = 'draft'` and
   `valid_to IS NULL`, tenant-wide. This is the existing count, unchanged
   (production: 8).
2. **Pending human reviews** — the Step 5 `human_reviews` axis, reused rather
   than redefined: NEEDS_HUMAN verdicts on the latest processing run of the
   ACTIVE claim set, plus HUMAN_REVIEW members of that set, **minus** any item
   an active waiver covers (production: 0 + 0 − 0 = 0).

The two are summed because both are the same sentence to a human: *something
is waiting for a person*. Each is also shown separately at the top of the
Needs review tab, so the number is never a mystery. Best-effort: a failing read
contributes 0 and the badge never takes the page down. One capability gates
it — a VIEWER sees the count but every act inside the tab is MEMBER.

## b. THE ORG-STATE BAND

A partial, `templates/components/_org_state.html`, rendered directly under the
nav on **Requirements, Results and Releases** (Releases included now because
the band is shared; that is not a Releases redesign, which stays 6b).

One reader, `intelligence/org_state_console.py::read_org_state(tenant_id)`,
best-effort, **cached per request** in `flask.g` so three includes cost one
query path. It returns three groups, each independently `available`:

| group | shows | source |
|---|---|---|
| org | each environment-bound org: name, current sequence, last sync | `connected_orgs` where `environment_id IS NOT NULL`; the sequence from `resolve_current_sequence(session, connected_org_id=…)` (the graded value, not `max(version_seq)`); the last sync from the newest `s1_sync_jobs` row for that org |
| policy | active policy name + version, or "no active policy" | `quality_policy.active_policy` |
| cadence | enabled schedule(s): environment, cron, last plan id, or the refusal | `s4_run_schedules` + `last_plan_id` / `last_refusal` |

Each field degrades on its own: a group that cannot be read renders
"unavailable" in place, never an exception and never a zero pretending to be a
fact. Nothing in the band is clickable in 6a except the plan id.

**Finding, ledgered rather than fixed here:** the band must read the last sync
from `s1_sync_jobs`, because `connected_orgs.last_sync_completed_at` is NULL
for both production orgs while a sync completed today at 07:15Z. Reading the
org column would render "never synced" under a live daily sync. The stale
column is a FIX PLAN item, not a 6a change. (**Fork 5.**)

## c. REQUIREMENTS — the work surface

Three tabs on `/requirements`, selected by `?tab=`:

1. **Requirements** (default, `tab` absent) — the list below.
2. **All claims** (`?tab=claims`) — the Test Library content: the existing
   `list_claims` paginated table with its search and status filter, rendered in
   place. No new read.
3. **Needs review** (`?tab=needs-review`, count in the tab label) — the two
   halves of the badge, each with its own heading and its own act: draft claims
   with per-row Approve (today's `/claims/inbox` body), and pending human
   reviews with a link to the surface and to the Step 5 waiver form. The tab
   label carries the same number as the nav badge.

### The row, per the mock

| column | content | source |
|---|---|---|
| identity | `external_key`, **verbatim**, monospace — never re-derived, never prettified | the row's `external_key` (Step 1) |
| requirement | summary + origin chip + claim count | existing |
| functional | passed / failed / never-run over the requirement's FUNCTIONAL claims | `s4_execution_console.requirement_run_health(tenant_id, keys)` |
| conformance | surfaces · checks · failures · human review — **or "no surface declared"** when no surface is declared. Never `0 / 0 / 0`, because zero checks and no declaration are different facts | `requirement_surface_console.read_requirement_surfaces` |
| readiness | the worst readiness across the requirement's FUNCTIONAL claims × the environments holding evidence, as a Step 2 pill with its sentence | `readiness_for_pairs`, functional lane only |

**The lane split is load-bearing here.** A conformance claim contributes to the
conformance column and to nothing else; it never enters the readiness
computation, so a requirement whose only checks are conformance reads
"no functional check" rather than NEVER RUN. This is D-488's correction applied
at the list level.

Filters: the existing origin filter, plus a **readiness** filter
(CURRENT / STALE / NEVER_RUN / CANNOT_DETERMINE / no functional check).
Fixtures stay hidden by default with the visible count and the one-click
reveal (Step 1 behaviour, preserved verbatim). Referential **gaps** render as
gap rows, never hidden, exactly as today.

### "Plan a run"

The button passes the **filtered set** to the Step 4 planner — and Step 4 has
no scope for a set of requirements. Its scopes are requirement, release,
schedule and tenant. 6a will not invent one (§g: no new semantics). So:

- filter resolves to **exactly one** requirement → `scope_kind="requirement"`
  with that key and the chosen environment, i.e. today's requirement-page Plan;
- filter resolves to **more than one** → the button refuses with a sentence
  naming the count and offers the two honest alternatives it can pass: the
  tenant-wide plan, or narrowing the filter;
- filter resolves to **none** → the button is absent.

A `requirement_set` scope carrying the filtered keys is the recorded successor,
and it belongs to Step 4, not here. (**Fork 2.**)

## d. RESULTS — what happened

`/runs/substrate` keeps its three lenses, its facets and both panels. Two
things change.

### The KIND filter

`?kind=` = `all` (default) | `functional` | `conformance`, spanning both lanes
in one list:

- **functional** rows are `s4_execution_runs` (production: 1849);
- **conformance** rows are `s6_ui_processing_runs` (production: 4) — one row
  per processing run, which is the unit a person recognises.

The two are merged newest-first by their own timestamps. The grouped lenses
(`requirement`, `cause`) stay functional-only, because both group by a
functional claim's requirement and cause; the kind filter is meaningful on the
flat `runs` lens and says so when another lens is active.

### The columns

| column | functional row | conformance row |
|---|---|---|
| run | run id + claim title | job id + claim set + engine/version |
| outcome | `outcome` enum | verdict counts (PASS / FAIL / NEEDS_HUMAN / NOT_DETERMINED) |
| readiness | Step 2 org-axis pill + sentence | **never the org axis** — the processing run's currency: the inventory version and claim set it graded, and whether it is the latest run for that set |
| from | the plan: `schedule N` when the plan's `scope_kind='schedule'`, else the plan id, else **"no plan (pre-planner)"** | the job payload's recorded scope, else "no plan (pre-planner)" |

**"From" is honest about a real asymmetry.** Step 4 stamped `plan_id` on
`s4_execution_jobs` and `s4_execution_runs`; it did **not** add the column to
the UI-inspection job table, so a conformance run's provenance is only what
`payload.scope` recorded. Where that is absent the cell reads "no plan
(pre-planner)" and does not guess. `plan_id` on the UI job is the recorded
successor and belongs to Step 4. (**Fork 3.**)

The CANNOT_DETERMINE sentence renders once beneath the list whenever any row
carries that state, as it does today per row title.

### Re-homing the conformance run view

`/ui-report/runs/<job_id>` → **`/runs/conformance/<job_id>`**, the same
template and console call, now reachable from a Results row. `/ui-report`
(the index) → `/runs/substrate?kind=conformance`. The three tools —
`/ui-report/compare`, `/ui-report/coverage`, `/ui-report/evidence` — have no
home in the mock and are **kept at their URLs untouched**; `evidence` is a
fragment endpoint the run view calls, so moving it would be churn. Their home
is a later slice, stated rather than improvised.

The repair panel stays exactly where it is, with its admin gate and its
dormant-first switch.

## e. CAPABILITIES

Six additions to `_CAPABILITY_MIN_TIER`, view at VIEWER and manage at MEMBER,
no DB permission sets:

```
view_requirements  VIEWER      manage_requirements  MEMBER
view_results       VIEWER      manage_results       MEMBER
view_releases      VIEWER      manage_releases      MEMBER
```

They gate **presentation** — nav visibility and whether a control renders. The
enforcement stays the route decorators (`require_tier`), unchanged. The matrix:

| page / action | viewer | member | admin |
|---|---|---|---|
| Requirements list, All claims, Needs review (read) | yes | yes | yes |
| approve a draft claim | no | yes | yes |
| declare / unlink a surface | no | yes | yes |
| Plan a run | no | yes | yes |
| Results list, all lenses, both kinds (read) | yes | yes | yes |
| open a run / conformance run (read) | yes | yes | yes |
| schedules panel | no | no | yes |
| repair decisions | no | no | yes |
| Releases (read) | yes | yes | yes |
| unauthenticated, any of the above | → `/login` | | |

Today a VIEWER sees **no** Requirements item at all, because the slot is gated
on `run_single_ticket` (MEMBER). That is the bug this section fixes: reading
the work is a viewer's right; acting on it is not.

## f. ROUTE HYGIENE

Every moved or removed path answers, with a one-line note on the destination
naming what moved:

| old | new | form |
|---|---|---|
| `/claims` | `/requirements?tab=claims` | 302 |
| `/claims/inbox` | `/requirements?tab=needs-review` | 302 |
| `/reviews` | `/requirements?tab=needs-review` | 302 (was → `/claims/inbox`) |
| `/test-cases`, `/test-cases/<id>` | `/requirements?tab=claims` | 302 (was → `/claims`) |
| `/ui-report` | `/runs/substrate?kind=conformance` | 302 |
| `/ui-report/runs/<job_id>` | `/runs/conformance/<job_id>` | 302 |
| `/results`, `/runs`, `/runs/<int>`, `/run`, `/tickets`, `/suites` | unchanged | already redirects |
| `/claims/<uuid>` and every claim action | **unchanged** | the detail page is the drill-down; only the LIST is re-homed (**Fork 6**) |
| `/dashboard` | **unchanged** | 6b re-homes it |

**Retirement plan.** The old URLs answer for **one release cycle** — through
6b and its D-entry. When 6b lands, `/claims`, `/claims/inbox`, `/reviews`,
`/test-cases` and `/ui-report` are deleted in one commit and their absence is
recorded; anything still linking to them by then is a defect the 6b verification
must find. A dead-link sweep (`tests/unit/test_no_dead_internal_links.py`)
asserts that no template links to a path that neither resolves nor redirects.

## g. NON-GOALS

Releases and Settings beyond the shared band (6b); no new semantics — no new
planner scope, no new readiness state, no new verdict; no worker or scheduler
change; no conformance or policy change; no migration (6a adds **no** schema);
the `/ui-report` tools keep their URLs.

## Blast radius

| touched | how |
|---|---|
| `core/navigation.py` | `SIDEBAR_ITEMS` → four; new capability gates; `active_also_for` for `/ui-report`; landing map entries for the removed URLs |
| `core/permissions.py` | six capabilities; the badge count becomes drafts + pending reviews and moves to the Requirements item |
| `intelligence/org_state_console.py` | new, read-only |
| `views.py` | `/requirements` gains tabs; `/runs/substrate` gains `kind`; `/runs/conformance/<job_id>` added; five redirects changed or added; `active_page` on the runs list corrected from `test_library` |
| templates | `requirements/list.html` (tabs + columns), `runs/s4_list.html` (kind + from), `components/_org_state.html` (new), `base.html` (band include) |
| not touched | every console's semantics, the planner, the policy, the worker, the claim detail page, the repair panel, Releases and Settings |

## h. Verification plan (the brief's list, restated)

1. nav renders exactly four items; the badge equals drafts + pending reviews
   with each half shown on the tab; a failing read yields no badge, not a 500.
2. the permission matrix per page and per action — viewer reads and sees no
   act, member acts, admin sees the panels, unauthenticated → `/login`.
3. the conformance summary reads **"no surface declared"** on SQ-205 (no
   declaration exists on production) and the surfaces/checks/failures split on
   a requirement that has one (planted on scratch).
4. readiness lanes: a conformance claim never reads NEVER RUN from the org
   lane, on both the Requirements row and the Results row.
5. the kind filter returns both run types, and the grouped lenses say the
   filter does not apply.
6. every old route redirects, with the note rendered; the dead-link sweep is
   green.
7. the org band renders all three groups and degrades honestly (proved by
   pointing each group at a failing read).
8. fixture screenshots of **every** state — populated, empty, fixture-hidden
   with the count, a gap row, "no surface declared", the refusing Plan button,
   both kinds in Results, the band unavailable — plus a **production-data
   render** per the D-487 standing correction.
9. the full merge-gate + D-468 set.

## Forks for the GO

1. **Dashboard**: remove the nav chip in 6a while leaving `/dashboard` fully
   working, so the bar reads four now and 6b re-homes the content. The
   alternative is five items until 6b. Lean: remove the chip, keep the route.
2. **"Plan a run" on a multi-requirement filter**: refuse with a sentence
   naming the count (and offer the tenant-wide plan) rather than invent a
   scope. Lean: refuse; record `requirement_set` as a Step 4 successor.
3. **Conformance provenance**: show `payload.scope` where it exists, else
   "no plan (pre-planner)"; do not infer. Lean: yes; `plan_id` on the UI
   inspection job is a Step 4 successor.
4. **Requirements readiness column**: worst-of across the requirement's
   functional claims × evidence environments, one pill. The alternative is a
   per-environment breakdown in the row. Lean: worst-of, with the environments
   named in the title attribute.
5. **Last sync source**: read `s1_sync_jobs`, not
   `connected_orgs.last_sync_completed_at`, which is NULL on production under a
   live daily sync. Lean: the job table; ledger the stale column.
6. **Claim detail stays at `/claims/<uuid>`**: only the list is re-homed.
   Moving detail URLs would break every existing deep link for no gain. Lean:
   stays.

## Rulings (AK, 2026-09-11 — GO on the design)

All six leans ratified as written:

1. **Dashboard**: the nav chip goes in 6a; `/dashboard` keeps working, keeps
   its landing entry, and 6b re-homes its content and retires it.
2. **"Plan a run"**: one requirement plans; more than one REFUSES with the
   count and offers the tenant-wide plan; `requirement_set` is a Step 4
   successor, not a 6a invention.
3. **Conformance provenance**: show the job payload's recorded scope, else
   "no plan (pre-planner)". Never inferred.
4. **Requirements readiness**: worst-of across the requirement's FUNCTIONAL
   claims × the environments holding evidence, one pill, environments named in
   the title attribute.
5. **Last sync**: read `s1_sync_jobs`; the stale org column is ledgered.
6. **Claim detail** stays at `/claims/<uuid>`; only the list is re-homed.

Two ledger entries recorded with the design (FIX PLAN, 2026-09-11):

- `connected_orgs.last_sync_completed_at` is NULL on both bound orgs while
  syncs complete daily — a data-hygiene defect for its own slice; the band
  reads the sync job table meanwhile.
- Execution jobs carry `plan_id`; UI inspection jobs do not. The lanes'
  provenance is asymmetric, and a Step 4 successor should stamp the inspection
  job so "from" reads the same on both.
