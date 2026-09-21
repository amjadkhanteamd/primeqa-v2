# Verification — Round 3 (the audit's remaining classes), 2026-09-21

Branch `round3` from main @2d18726. Five parts, one commit each, one HOLD
before any merge. **Standing rule (AK): no UI change lands without AK seeing
it** — every page Part A changes is shown twice below: a fixture screenshot
over the planted hostile shapes, and a production-data render (D-487: the
actual page over production data, read-only through the proven guard). The
merge is gated on AK's "screens approved".

## Part A — what a pilot client hits first

The class: a page that reads an ABSENCE where there is a FACT. No run
rendered as a 500; a lapsed acceptance rendered as "no waivers"; a retired
plan rendered as "no tests yet"; a missing stamp described as legacy; a
status the product speaks but cannot write.

| Finding | What changed | Proof |
|---|---|---|
| AUD-007 | `dashboard.html`: the pass-rate card renders an empty state ("— · no run yet") when the rate is None; the number and its colour only when a run exists | tenant 2 (no run) lands on a 200 with `pass-rate-empty`; RED on main: `Unhandled TypeError on GET /` |
| AUD-015 | `/settings/waivers` lists EVERY waiver in the tenant (`quality_policy.all_waivers`; the page had listed tenant-wide ones only — a release-scoped acceptance, active or lapsed, was invisible), in three sections: Active, **Lapsed** (accepted by whom, recorded by whom and when, lapsed on which date, "not counted"), Revoked (by whom, when, why) | the four planted states listed by state with names and dates; RED on main: the lapsed and the release-scoped active rows absent |
| AUD-041 (new) | both Revoke forms (the register and the decision tab) sent a HIDDEN EMPTY reason — since D-497 refuses an empty reason, the buttons could never revoke; each now carries a required reason field | the form test (no hidden reason, a required field) and a revocation through the form landing with its reason; RED on main |
| AUD-022 | the per-requirement count read counts deprecated claims under their own key and never in the total; the board row reads "no live test case · N deprecated" (all retired) or shows an "N deprecated" chip beside the live chips; the page header reads "(0 tests · 5 deprecated)" and the empty state "No live test case · 5 deprecated (retired from the plan…)" | the all-deprecated and the mixed requirement; RED on main ("no tests yet") |
| AUD-017 | `readiness.py`: the unstamped sentence states the FACT — "this run carries no environment stamp" — and adds "it predates run-level stamping" only when the run's own date is earlier than Step 2's deploy (D-484, 2026-09-08 06:14Z); the resolvers carry the run's timestamp | a run dated today: no clause; a run dated 2026-09-01: the clause; RED on main (every unstamped run called legacy) |
| AUD-018 | the dead read `claim_sets.status == 'revoked'` in `approve_claim_set` is deleted (lean: DELETE — members are revoked one by one through `revoke_member`, which deprecates the claim; a whole-set revoke would deprecate every claim of a set in one act, an act nobody asked for; the state could only arrive by hand-editing the table) | `tests/unit/test_status_vocabulary.py`: every status literal the product reads on `claim_sets` and `quality_policies` has a writer; RED on main (`'revoked'` read, never written) |

THE GUARD for the class — `tests/integration/test_round3_pages.py` (21):
plants the shapes (tenant 2 with no run; five claims all deprecated on one
requirement, two live + one deprecated on another, a claim with two unstamped
runs, four waivers across three states), renders every list page over them as
the real tiers (`/`, `/requirements`, `/runs/substrate`, `/releases`,
`/settings/waivers`, `/settings/claim-sets` — tenant 2 and tenant 1), asserts
the count shown equals the count planted, and removes the shapes (residue
asserted 0). **RED on main: 10 of 21** (the landing 500, the register, the
board, the header, both stamping sentences, the tenant-2 landing in the sweep,
the counts).

### Fixture screenshots (`round3-fixtures/`)

| Screen | File |
|---|---|
| Landing page of a tenant with no run — "Pass Rate —, no run yet" (AUD-007) | `landing_zero_run_tenant.png` |
| Waivers register — Active (2) with the reason field beside Revoke, Lapsed (1) with who/when/lapsed-on, Revoked (1) with by-whom and why (AUD-015, AUD-041) | `settings_waivers_active_lapsed_revoked.png` |
| Release decision tab — the Revoke form with its reason field (AUD-041) | `release_decision_revoke_with_reason.png` |
| Requirements board — "no live test case · 5 deprecated" and "2 approved · 1 deprecated" (AUD-022) | `requirements_board_all_deprecated.png` |
| Requirement page header "(0 tests · 5 deprecated)" and the empty state (AUD-022) | `requirement_all_deprecated_header.png` |
| Requirement page header "(2 tests · 1 deprecated)" (AUD-022) | `requirement_mixed_header.png` |
| Run detail, unstamped run dated today — "carries no environment stamp", no legacy clause (AUD-017) | `run_detail_unstamped_today.png` |
| Run detail, unstamped run dated 2026-09-01 — "…: it predates run-level stamping" (AUD-017) | `run_detail_unstamped_legacy.png` |
| Results list for the legacy environment (the planted run listed) | `runs_list_legacy_env.png` |

### Production-data renders (D-487), read-only

A local server over the production database with `default_transaction_read_only=on`
at the server side (the guard proven by attempting CREATE first, as in every
read of this programme); tokens minted locally; AK's own user.

| Screen | What it shows | File |
|---|---|---|
| Landing page | the rate renders (94.0%) — production has runs, so the numeric branch; the empty branch is the fixture's | `PRODUCTION_landing.png` |
| Waivers register | "No waiver has been recorded." — production holds no waiver in any state (0 rows), the honest render | `PRODUCTION_settings_waivers.png` |
| Requirements board | the "N deprecated" chips beside live chips on req-320 (79 deprecated, 33 live), req-302 (38 / 79), req-315 (15 / 48) | `PRODUCTION_requirements_board_deprecated_chips.png` |
| Requirement 320 header | "(33 tests · 79 deprecated)" | `PRODUCTION_requirement_320_header.png` |
| Run 562682d5 (2026-08-04, unstamped, its pair's latest) | "…carries no environment stamp: it predates run-level stamping" — the legacy clause where it is TRUE; production holds no unstamped run after Step 2 (0), so the clause-less branch is the fixture's | `PRODUCTION_run_detail_legacy_unstamped.png` |
| Release 16 decision tab | unchanged shape (no waiver on release 16, so no Revoke form renders) | `PRODUCTION_release_16_decision.png` |

Seen on the production renders and RECORDED, not fixed here (new, low):
**AUD-042** — HTML entities double-escaped in two places (the run page's
"Salesforce said" message shows `&amp;#8377;` where the org wrote `&#8377;`;
the landing page's flaky list shows `&middot;` literally); **AUD-043** — a
claim title on the landing page carries a raw relative-date object
(`{'$relative_date': {'anchor': 'RUN_DATE', 'offset_days': 5}}`) instead of
a rendered value.

## Part B — housekeeping

| Finding | What changed | Proof |
|---|---|---|
| AUD-001 | `scripts/authz_inventory.py` derives every gate from the RUNNING app: it walks each endpoint's `__wrapped__` chain and closure cells and recognises a gate by the CODE OBJECT its decorator produces, never by a name (which `wraps` copies and an alias hides). Census: 211 rules, 129 `login_required`, 91 web ladder gates, 72 API, 52 auth-only, 9 anonymous | the three aliased routes report `require_auth`; RED on main, where they read "(none — auth/login only or open)" |
| AUD-044 (new) | the dead-link sweep matched `[^/]+` for every rule parameter, so `/claims/inbox` matched `/claims/<uuid:test_id>` and two links on the landing page were called alive while the router 404s them. The resolver walks a target against a rule segment by segment, each parameter tested with its own converter; both links point at `/requirements?tab=needs-review` | the repo sweep: 0 dead before (blind), 2 dead after the fix, 0 after the repair; the live 404 |
| AUD-005 | four templates nothing renders deleted; the dead bulk-generate half of `requirements_list.js` deleted (no element it names has existed since D-165; it called a route retired in 7fd518f) | the sweep's target count drops with them; the row-actions half stays and is used by the requirement page |
| AUD-040 | already corrected in round 2 (D-500) | the contract asserted against the live app |

Guards, each proven red first: `tests/unit/test_authz_oracle.py` (5),
`tests/unit/test_no_dead_links.py` (+2 converter plants), and
`tests/unit/test_documented_contracts.py` (5 — every `primeqa/...` path
CLAUDE.md names exists, every component the kit lists is rendered, and the
status endpoint answers what the doc claims). RED on the audited tree 8990025.

**No UI change here needs a screenshot except the dashboard's two repointed
links, which are shown in Part A's `landing_zero_run_tenant.png` (the card now
reads "Pending Reviews" and lands on the Requirements "Needs review" tab).**

## Part C — zero known reds

| | before | after |
|---|---|---|
| unit | 1 red | 0 (5,516 passed, 1 xfail) |
| harness (`tests/test_*.py`) | 7 reds (2 collection errors) | 0 (112 passed) |
| integration, one process | 150 reds | 0 (1,055 passed, 185 skipped) |
| **total** | **157** | **0** |

The contamination was measured, not guessed: after the generation suite the
tenant engine was still bound to `primeqa_test_governance_main_22184` with 0
approved claim sets, against 369 on scratch. Both suites now restore the ambient
binding after every test. Fixtures whose setup failed left residue that collided
with the next plant, so the round-2 and round-3 worlds sweep their own markers
first.

**The proof AK asked for:** identical results twice in one process — two
`pytest.main` runs in one Python process, 1,055 passed / 185 skipped / exit 0
both times. **Zero residue is NOT met**: six suites leak on every pass (~540
claims, 10 claim sets, 9 inventories, 12 entities, 1 release), measured per file
and recorded as AUD-052.

61 tests are quarantined in 18 registry entries against 8 findings
(AUD-045..AUD-051), each skipped with its finding, owner and mechanism printed.
`tests/unit/test_quarantine_registry.py` holds the list honest.

## Part D — the decision memo

`docs/audit/DECISION_MEMO.md`, read-only throughout. AUD-028's seven rows are
two named incidents (a deploy SIGTERM between provisioning and finalize; a July
deletion that did not cascade). AUD-034 cannot be answered from evidence that
exists — there is no CI configuration and no access log — so the lean is to log
first. AUD-031 link it, AUD-033 retire both, AUD-025 keep the aliases.

## Part E — the audit's own coverage gaps

The link graph reads `htmx.ajax` targets and any `data-*` path (267 targets, 0
dead; the drawer's `/claims/<uuid>/panel` is in the graph by name now). The two
defects only production data reached are planted in the hostile tenant and
asserted, marked `xfail(strict=True)` against AUD-042 and AUD-043.
`AUDIT_2026-09.md` carries a round-3 coverage statement.

## The screens AK is being asked to approve

Nine fixture screenshots and six production-data renders, all in
`docs/ui-testing/round3-fixtures/`, listed in Part A's two tables above. The
merge is gated on **"screens approved"**.
