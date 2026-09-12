# VERIFICATION — Step 6b: layout collapse, part 2 (Releases and Settings)

**Branch** `step-6b-decision-setup` from `main` @ 9e9b175. Design 513a590 (GO
2026-09-12, six leans ratified). Build: this commit. Merge gated on AK seeing
the fixture screenshots (`step-6b-fixtures/`, fifteen, six of them rendered
over REAL PRODUCTION DATA per the D-487 standing correction).

## 0. The sweeps ran FIRST (D-490's lesson, made procedural)

D-490 recorded that a guard only guards if it runs before the thing it guards
is believed. So before any page of this slice was rendered or screenshotted,
both class sweeps ran green: the dict-method-key sweep
(`test_templates_dict_method_names.py`) and the dead-link sweep over every
template (`test_step_6a_routes.py`), plus the gate-logic sweep and the 6a
navigation suite — **29 passed** before the first page was opened. No
D-487-class fault reached a screenshot this time.

## a. RELEASES — the four states, on real data

| state | when | seen |
|---|---|---|
| `recorded` | a decision row, scope current, no run since | the fixture's "Ready to ship" — **GO**, policy v1 |
| `refuses` | an item in scope is not current | "Blocked on drift" — "Evaluate will refuse — 1 item in scope is not current" |
| `not_evaluated` | scope current, nothing recorded | "Nothing planned" |
| `stale` | a decision row, and a run landed after it | "Evidence moved" — "recorded before the latest run", with the recommendation beside it |

All four render on the fixture world. On **production** the same board reads
20 `not_evaluated`, 1 `refuses` and 2 `stale` across 23 releases — including
release 16, which is `stale` because schedule 1 fired after decision 72. That
fourth state was invented for the design and turned out to describe production
on the day it shipped.

**A defect the board found in itself, before the page did.** The first
implementation counted evidence on env 78, which the canonical scope read
excludes as inactive (D-485's interim). Release 16 read "15 items not current"
while its own page said the scope was clean. The board now uses the SAME
`_environments_with_evidence` filter — a list must not contradict the page it
links to. A second, related fix: the refusal count is per (claim, environment)
ITEM because that is what Evaluate names, while the evidence count is per CLAIM
worst-of; mixing them made a release read "1 item not current" beside "1 of 1
check current".

**Best-effort per row (ruling 6)** is asserted, not assumed: with the tenant
read forced to raise, `/releases` still returns 200 and the cell reads
"unavailable".

### The open release, and the Dashboard

The open release keeps everything Steps 4 and 5 built and gains **"Compare
releases"** → `/releases/compare`, pre-filled with the newest processing run.
`/dashboard` redirects to `/releases`; `/dashboard/legacy` still answers, so
nothing is deleted in this slice.

## b. SETTINGS

The sidebar groups exactly as drawn: **General · Release quality · Conformance
setup · Org reference** (asserted per item, not just per group heading).

| page | state |
|---|---|
| Policy | **ACTIVATE on a draft** — the control AK reached for a CLI to use |
| Waivers | list, create (reviewer + reason + expiry all required), revoke |
| Sites & personas | read-only, and says why |
| Surface inventory | read-only, naming the ACTIVE version and the derivation |
| Claim sets | read-only + Approve, the one conformance act that already existed |
| Standards & catalogues | read-only, carrying the coverage link (ruling 5) |
| Custom rules | read-only, 74 rules on production |
| Schedules | a LINK to the Results panel (ruling 2), never a copy |

**Activate is the same code path as the CLI**, proven end to end: through the
page a MEMBER activates a draft, the row records `activated_by = 1`, the
previous active retires, `quality_policy.activate` writes its audit row — and
once frozen, the page's own act refuses.

**A waiver made on the page is the same object the decision tab makes**:
recorded with the real reviewer and actor, audited, and revocable, with the
revocation recording who did it.

**Sites & personas is read-only for a stronger reason than "not in v1"**: a
registration form would be a credential-entry surface, and the web tier holds
no portal cryptography. The page says so, and the test asserts both the
sentence and the absence of any form.

## c/d. The two placement rulings

**The gear IS the fourth item** (ruling 4), now carrying `data-nav-item` so the
claim is testable: the nav renders four hooks for an admin and three for a
viewer. Before this, a test counting nav items found three and had to know
about the exception.

**Coverage lives under Standards** (ruling 5). `/ui-report/coverage` redirects
to `/settings/standards`, and the report itself is at
`/settings/standards/coverage`.

## e. The matrix, asserted with a valid CSRF token

The POST tests prime the CSRF cookie and echo the token, so **the refusal under
test is the TIER gate and not the CSRF one** — without that, a viewer's 403
would have proved only that CSRF works.

| action | viewer | member |
|---|---|---|
| read Releases, the open release, Compare | 200 | 200 |
| read any Settings page | redirected | 200 |
| activate a policy | refused | **allowed, audited** |
| record / revoke a waiver | refused, and nothing written | **allowed, audited** |

## f. Route hygiene

`/ui-report/compare` → `/releases/compare` (query string preserved);
`/ui-report/coverage` → `/settings/standards`; `/dashboard` → `/releases`. The
pre-6b bodies survive at `/dashboard/legacy`. **No route is deleted here** —
the retirement commit is separate, as §f promised.

## g. Suites

| suite | result |
|---|---|
| the sweeps, run first | **29 passed** |
| `tests/unit` | **5097 passed**, 3 failed — the live-parity trio, red only when `DATABASE_URL` points at scratch |
| `tests/integration/test_representation` | **442 passed, 4 skipped** |
| pages, all five suites | **46 passed, 1 skipped** — including the report-page and Step 5 tests updated for the re-homing |
| DB-real corpus | see §i |

Two older tests changed deliberately: the report-page suite now asserts the
tools at their NEW homes, and the Step 5 policy-page test no longer asserts
"read-only", because 6b put Activate on that page and moved it to MEMBER+.

## h. Page cost, measured

| page | queries |
|---|---|
| `/releases` (4 releases) | 27 |
| **`/releases/<id>?tab=decision`** | **106, 1.10 s** |
| `/settings/quality-policy` | 10 |
| `/settings/waivers` | 10 |
| `/settings/standards` | 10 |
| `/settings/claim-sets` | 9 |
| `/settings/sites` | 10 |

The release DETAIL page **crosses the app's own 800 ms slow-request threshold**
and logged itself doing it (`slow_request route=/releases/40 ms=1103.8`). 6b
added one link to that page; the cost is five consoles each opening their own
tenant session and re-reading the same claims — the same family as the
51-query Requirements page. Ledgered, not fixed here.

## i. The screenshots

Fixture world (`step-6b-fixtures/`): `releases_list_four_states`,
`release_open_decision`, `releases_compare`, `settings_policy_with_activate`,
`settings_waivers`, `settings_sites`, `settings_surface_inventory`,
`settings_claim_sets`, `settings_standards`, `settings_custom_rules`,
`releases_list_viewer`.

**Production data, read-only** (D-487), through the guard proven in D-489:
`PRODUCTION_releases_list` (23 cards, 20 not evaluated / 1 refuses / 2 stale),
`PRODUCTION_release_16_decision`, `PRODUCTION_settings_policy`,
`PRODUCTION_settings_waivers`, `PRODUCTION_settings_standards`,
`PRODUCTION_settings_claim_sets`.

**Decision 72 is reproduced exactly as recorded**, which is the brief's test:

```
recommendation  go
policy          Plimsol default v1
plan            9ea0c522-be54-49bc-bf6c-cef6d7fb5a6e
recorded        2026-09-11T06:01
final decision  GO by Amanda Rivera · 2026-09-11T06:02 · reason: GO GO GO
```

with Compare and the targets block both present, and Activate correctly ABSENT
on production because the seed policy there is active, not draft.

## §i DB-real corpus

**111 passed, 7 skipped, 1 failed** on a cleaned scratch — the failure is the
known `test_report_slice::test_a_runs_list_carries_both_recorded_runs` window
artefact, ledgered since D-488 and unrelated to 6b. The world remover deleted
what the fixture minted (D-490's lesson): after it, scratch carries zero
releases, zero requirements, zero decisions, no org on 5901, no waivers, and
the seed policy back at DRAFT.
