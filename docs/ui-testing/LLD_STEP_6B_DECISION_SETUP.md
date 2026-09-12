# LLD — Step 6b: layout collapse, part 2 of 2 (Releases and Settings)

**Status: DESIGN — HOLD (2026-09-12).** Branch `step-6b-decision-setup` from
`main` @ 9e9b175 (Step 6a live, D-490). Mocks approved by AK: the Releases list
with evidence and decision per row, the open release showing the policy-cited
recommendation and its six evidence lines with "Compare releases", and the
Settings page with a grouped sidebar and a policy view carrying Activate.

6a gave the daily work two homes. 6b gives the *decision* one home and the
*setup* another, and finishes the collapse: after it, every nav item is a place
a person goes on purpose, and the Dashboard — an executive view assembled
before the decision engine existed — stops being a fifth opinion.

## 0. Pre-flight facts, cited (read on this branch)

| fact | where |
|---|---|
| The Releases LIST is a card grid of name, version tag, status chip, description and target date. It carries **no evidence and no decision** — a release that would refuse to evaluate looks identical to one recommending GO. | `templates/releases/list.html:22-50` |
| The open release already carries, from Steps 4 and 5: the target-environments block with declare/remove, recent plans, the scope-refusal block, the Step 5 quality card (policy + plan + six lines + grading + waivers + the final-decision form) and the substrate verdict card. | `templates/releases/detail.html`; D-486, D-488 |
| `/dashboard` is `@require_tier(VIEWER)`, env-scoped (not release-scoped), and renders from `get_substrate_dashboard_data`: `latest_run` aggregate, `state` + `state_reason`, `risk`, `gates` (**always `[]`** — dead), `substrate_checks` (= the decision's `reasoning`), `blockers` (grouped by requirement and cause), `ticket_grid` + `ticket_counts`, and `trends` (daily pass rate, last 5 days with runs). | `views.py:246-285`; `substrate_dashboard.py:176-292` |
| `_trends` reads `s4_execution_runs` grouped by day **for one environment_id** — an environment fact, not a release fact. | `substrate_dashboard.py:160-175` |
| The phase-7 comparison and the coverage report live in the orphan tree at `/ui-report/compare` and `/ui-report/coverage`, both `@require_tier(MEMBER)`, both left in place by 6a because the 6a mock gave them no home. `coverage_report(tenant_id, job_id)` answers "N of M criteria, per standard" — a property of the ACTIVE map set and the catalogue. | `views.py:5264-5294`; `ui_report_console.py:283-316` |
| The Settings sidebar is one flat "Settings" list (General, Connections, Environments, Groups, Users, Quality policy, LLM usage) plus a "Tools" group (Ask, Substrate Insights, Org Model, Knowledge) and a superadmin group. No grouping by purpose. | `templates/settings/base.html:19-100` |
| `/settings/quality-policy` is **read-only by design** (§f of 6a). Activation is CLI: `python -m primeqa.intelligence.quality_policy --tenant-id N activate <id> --user-id N`. AK had to use it to activate the seed. | `views.py:5059-5066`; D-488 |
| The policy store already exposes everything the UI needs: `active_policy`, `list_policies`, `activate` (draft → active, retiring the previous, audited with the real actor), `retire`, `record_waiver`, `revoke_waiver`, `waivers_for_release`, and `policy_overview`. **No new service is needed for §b's two acting pages.** | `intelligence/quality_policy.py`, `quality_decision_console.py` |
| **The Conformance-setup pages the mock draws do NOT exist today.** Claim sets have exactly one route — `POST /claim-sets/<id>/approve` — and no list page. Surface inventory, sites and personas, standards and catalogues, and custom rules exist as SERVICES only (`surface_links`, `knowledge/rule_registry`, `criterion_catalogue`, `rule_lifecycle`, `cust_authoring`) with no UI at all. | `views.py:3281`; `primeqa/knowledge/`, `test_representation/surface_links.py` |
| Schedules are the D-214 panel inside `/runs/substrate`, admin-gated, with its create / toggle / claim POSTs and the Step 4 authority affordances. | `views.py:3856-3985`, `s4_schedule_create`, `s4_schedule_update` |
| The Settings **gear** carries `title="Settings"`, is gated on `can_see_settings`, and sits outside the horizontal strip; `base.html` excludes `item.id == 'settings'` from the strip. 6a fixed the ungated user-menu link beside it. | `templates/base.html:65,88`; D-490 |

## a. RELEASES — the decision's one home

### The list

Each card gains two facts, from one bulk read (`intelligence/releases_board_console.py`,
new, read-only, one tenant session for the page):

- **evidence**: the release's requirement count, its functional check count and
  how many are current — the same lane split 6a uses, so a release whose only
  checks are conformance says so rather than reading empty;
- **decision state**, which is the LIVE answer, not merely the last row:

| state | when | shown as |
|---|---|---|
| the recorded recommendation | a decision row exists AND the scope is current | `GO` / `CONDITIONAL GO` / `NO GO` / `CANNOT DETERMINE` with its date, policy version and plan |
| **CANNOT DETERMINE (scope not current)** | `release_scope_readiness` reports non-current items | the count of items that are not current, and "Evaluate will refuse" |
| not evaluated | no decision row, scope current | "not evaluated" with the Evaluate affordance |
| stale decision | a decision row exists but evidence has moved since it | the recommendation, marked "recorded before the latest run" |

The third and fourth are the states the current list cannot express at all.

### The open release

Unchanged where Steps 4 and 5 already built it. Two additions:

- **"Compare releases"** opens the phase-7 comparison, re-homed from
  `/ui-report/compare` to **`/releases/compare`**, pre-filled with this
  release's latest conformance run as the candidate. The console call and the
  template are unchanged; only the home and the pre-fill are new.
- the plan citation already renders in the quality card; the list links to it.

### What the Dashboard contributes, item by item

The brief asks what survives and what is redundant. Enumerated against the
release page as it stands after Step 5:

| dashboard element | verdict |
|---|---|
| hero GO/NO-GO + `state_reason` | **redundant** — the quality card's recommendation and its `because` line say it with the policy named. Dropped. |
| `risk` level | **redundant** — the substrate verdict card renders risk score and level on the same tab. Dropped. |
| `substrate_checks` (per-check reasoning) | **redundant** — that IS the substrate card's reasoning list. Dropped. |
| `blockers` grouped by requirement and cause | **redundant** — the quality card names failures and the substrate card renders "what's blocking the release". Dropped. |
| `gates` | **dead** — always `[]` in code. Dropped. |
| `ticket_grid` + `ticket_counts` | **redundant since 6a** — the Requirements list now carries per-requirement functional, conformance and readiness. Dropped, with the release page linking to Requirements filtered to this release's scope. |
| intelligence summary | **already its own page** (`/substrate-insights`), regrouped under Settings → Org reference. Dropped from here. |
| **`trends`** (daily pass rate, last 5 days) | **the only survivor** — and it is an ENVIRONMENT fact, not a release fact (`_trends` groups runs by day for one `environment_id`). It moves to **Results**, whose subject is runs over time, not to Releases. (**Fork 3.**) |

So the Dashboard folds in by being *absorbed*, not copied: six of its eight
elements already exist on the release page in better form, one is dead, and one
belongs on Results.

`/dashboard` becomes a **redirect to `/releases`**, and the landing-page entry
resolves there. **No route is deleted in this slice.** The five 6a redirect
paths plus `/dashboard` and the two re-homed `/ui-report` paths are deleted
together in ONE retirement commit at the end of the cycle — stated in §f of the
6a design and honoured here.

## b. SETTINGS — the setup's one home

The sidebar groups as the mock draws:

| group | items |
|---|---|
| **General** | General, Connections, Environments, Groups, Users, LLM usage & plan (existing pages, regrouped only) |
| **Release quality** | **Policy** (rebuilt), **Waivers** (new) |
| **Conformance setup** | Sites & personas, Surface inventory, Claim sets, Standards & catalogues, Custom rules, Schedules |
| **Org reference** | Org Model, Knowledge, Substrate Insights, Ask (existing pages, regrouped only) |

### Release quality — the two ACTING pages

- **Policy** (`/settings/quality-policy`, rebuilt): the ACTIVE version with its
  rules, marked frozen and showing `first_used_at`; every other version listed
  with its state; **Activate on a draft** — the control AK had to reach for a
  CLI to use. It calls `quality_policy.activate` exactly as the CLI does, so
  the act is one code path: draft → active, the previous active retired, both
  audited with the real actor. MEMBER+. The CLI stays, and the page says so.
  Rule EDITING remains CLI (§f).
- **Waivers** (`/settings/waivers`, new): every waiver with its state (active /
  expired / revoked), item, axis, reviewer, reason and expiry; create with
  reviewer + reason + expiry all required; revoke. Same service the decision
  tab uses, so a waiver made in either place is the same object. MEMBER+.

### Conformance setup — five READ pages and one link

These do not exist today, in any form. Each is a thin read over an existing
service, **read-only in v1**, because the acts they would carry (registering a
site, cutting an inventory, authoring a rule) are CLI or service-level today
and giving them buttons is a separate design:

| page | reads | why read-only in v1 |
|---|---|---|
| Sites & personas | the inventory's distinct sites and persona scopes | **vault registration stays CLI**: a site's credentials go through the vault, and a UI that collects them would be a credential-entry surface — the one thing this programme does not build. Said on the page. |
| Surface inventory | `ui_surface_inventories` + members, which version is ACTIVE and why (the derivation, per D-485) | cutting an inventory is an S3 act with provenance |
| Claim sets | `claim_sets` with status, member counts, inventory version — and **Approve**, the one act that already exists and is already audited | it is the only conformance act with a route today |
| Standards & catalogues | `s5_standard_map_sets` (state, standard, version, activated_at) and the criterion catalogue, plus **the coverage report** (§d) | map-set lifecycle is S5's, with its own ratification path |
| Custom rules | the rule registry's rules with their lifecycle state | authoring is S5's grammar, not a form |
| Schedules | **a link to the Results panel**, not a copy (**Fork 2**) | the panel acts on runs, is admin-gated and already carries the Step 4 authority affordances; duplicating it would fork a working surface |

### Org reference

Existing pages, regrouped, unchanged: Org Model, Knowledge, Substrate Insights,
Ask.

## c. THE SETTINGS-GEAR FINDING

**Lean: the gear IS the fourth item.** It is the existing convention, it is
already gated on `can_see_settings`, and 6a fixed the ungated link beside it.
The one line of markup is to give the gear `data-nav-item="Settings"`, so the
"four items" claim is *testable* rather than asserted — today a test counting
nav items finds three and has to know about the exception. The registry is
unchanged. Screenshotted either way.

## d. COVERAGE — where it lands

**Lean: Settings → Standards & catalogues.** `coverage_report` answers "N of M
criteria covered, per standard" — that is a property of the ACTIVE map set and
the catalogue, and it changes when a map set is ratified, not when a release
ships. A release's coverage question is already answered on its own page, by
the quality card's conformance line ("N surfaces · N checks · N failing"). Put
catalogue coverage where the catalogue is, and leave the release to speak about
itself.

## e. CAPABILITIES

As 6a established: view at VIEWER, manage at MEMBER, enforcement at the route.

| page / action | viewer | member | admin |
|---|---|---|---|
| Releases list + open release (read) | yes | yes | yes |
| Evaluate, declare a target, record a final decision | no | yes | yes |
| Compare releases (read) | yes | yes | yes |
| Settings → Policy (read) | no (Settings is `manage_`-gated) | yes | yes |
| **Activate a draft policy** | no | **yes, audited** | yes |
| Settings → Waivers (read) | no | yes | yes |
| **Create / revoke a waiver** | no | **yes, audited** | yes |
| Conformance setup (read) | no | yes | yes |
| Approve a claim set | no | yes | yes |
| Schedules (the linked panel) | no | no | yes |

Both new acts are audited with the real actor id — `quality_policy.activate`
and `quality_waiver.record` / `.revoke` already write those rows.

## f. NON-GOALS

No new semantics. No policy authoring beyond activate — rule editing stays CLI
in v1. No vault UI, and no credential entry anywhere. No worker or scheduler
change. **No route deletions**: the retirement commit is separate and comes at
the end of the cycle. No migration.

## Blast radius

| touched | how |
|---|---|
| `intelligence/releases_board_console.py` | new, read-only |
| `intelligence/conformance_setup_console.py` | new, read-only (the five setup reads) |
| `views.py` | the releases list gains the board; `/releases/compare` added; `/dashboard` becomes a redirect; six Settings routes added; the policy page gains an Activate POST |
| templates | `releases/list.html`, `releases/detail.html` (Compare link), `settings/base.html` (the groups), six new settings templates, `base.html` (one attribute on the gear) |
| not touched | every console's semantics, the planner, the policy engine, the worker, Requirements and Results as 6a shipped them |

## g. Verification plan

1. the release list shows **all three** decision states over real data;
2. the open release reproduces **decision 72** exactly as recorded;
3. the targets block declares and re-plans;
4. **Activate on a draft** works and is audited; the frozen version refuses;
5. a waiver created on the decision tab flips its item, and its expiry
   un-flips it (the Step 5 assertion, re-run through the new page);
6. every re-homed route redirects;
7. the member / viewer matrix per page;
8. **the dead-link and dict-method sweeps run BEFORE any page is believed**
   (D-490's lesson, made procedural);
9. fixture screenshots page by page, plus a production-data render (D-487);
10. the full merge-gate + D-468 set.

## Forks for the GO

1. **Slice size.** Conformance setup is five pages that do not exist in any
   form, over five services, plus two acting pages and the Releases work. Lean:
   build it as designed, with the five setup pages strictly read-only and thin
   — but if the slice should stay small, Conformance setup splits cleanly into
   6c and 6b ships Releases + Release quality + the regrouped sidebar.
2. **Schedules: link, not re-home.** Lean: link to the Results panel; record
   moving it as a successor for when schedules gain per-release cadences.
3. **Trends go to Results, not Releases.** They are an environment fact.
4. **The gear is the fourth item**, with a test hook rather than a new label.
5. **Coverage under Standards**, not under Releases.
6. **The release list's live decision state** costs a scope-readiness read per
   release on the page. After D-489 that is 3 queries + 1 per org each; for a
   20-release page that is real. Lean: compute it for the page's releases only,
   in one bulk pass, and state the measured cost in the verification — the same
   discipline 6a's board followed.

## Rulings (AK, 2026-09-12 — GO on the design)

All six leans ratified:

1. **Build as designed**; the Conformance-setup pages are strictly READ-ONLY.
2. **Schedules: link, not re-home.**
3. **Trends move to Results** — an environment fact, not a release fact.
4. **The gear is the fourth item**, with a test hook so "four items" is
   testable rather than asserted.
5. **Coverage under Settings → Standards.**
6. **Measure the list's per-release readiness cost and state it.** Each row's
   read is BEST-EFFORT: an odd release renders "unavailable" on that cell and
   never fails the list.

### Recorded: why Sites & personas is read-only

A registration form for a site would be a **credential-entry surface** — it
would collect the credentials a site is reached with. This programme does not
build one, on any page, for any role. The strengthening the TA ratified is
stronger than "not in v1": **the web tier holds no portal cryptography.** Key
material and portal secrets live in the vault and are reached by the CLI and
the worker; a browser session never handles them and no route accepts them. The
page therefore READS the inventory's sites and persona scopes and says plainly
that registration is a CLI act, and why.

### Recorded: the Dashboard absorption, justified rather than asserted

`/dashboard` is not "moved". It is absorbed, and the enumeration is the
argument. Of its eight elements:

- **six are already present on the release page in better form** — the hero
  verdict and its reason (the quality card's recommendation names the POLICY
  that produced it), risk (the substrate verdict card), the per-check reasoning
  (that card's reasoning list), the blockers (the quality card's named failures
  plus the substrate card's blocking list), the ticket grid (redundant since 6a
  put functional, conformance and readiness on every Requirements row), and the
  intelligence summary (its own page, regrouped under Org reference);
- **one is dead in code** — `gates` is `[]` on every path in
  `substrate_dashboard.py`, so the "Quality Gates" heading has rendered an
  empty section for as long as it has existed;
- **one is re-homed** — `trends`, which groups runs by day for a single
  `environment_id` and is therefore an environment fact; it goes to Results.

Nothing is duplicated, and nothing that a person relies on is dropped without
a named successor. `/dashboard` redirects to `/releases`; the route and the
landing entry survive until the single retirement commit at the end of the
cycle.
