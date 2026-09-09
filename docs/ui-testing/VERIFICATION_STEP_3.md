# VERIFICATION — Step 3, the requirement → surface link

**Branch** `step-3-requirement-surface` from `main` @ 383ee2f. Design 27ee7fc
(GO 2026-09-09, five forks on the leans; the inventory lifecycle flag recorded as
fork 3's successor). Build: this commit. Merge gated on AK seeing the fixture
screenshots (`step-3-fixtures/`) against the approved mock.

## 0. Corrections found at build (folded into the LLD)

1. **The approve hook targets the set just approved, not "the latest approved by
   timestamp".** `rematerialise` gained `claim_set_id=`; `approve_claim_set`
   passes the set it approved. In production the two coincide (one approval per
   transaction); inside one transaction they do not (`now()` is constant), and
   the hook's intent is the set being approved. Found by the scratch test.
2. **The HTMX redirect target carries no fragment.** htmx assigns
   `location.href`; a URL differing from the current one only by its fragment
   scrolls instead of navigating, so the page never re-rendered. The HX path
   redirects to the bare page URL; the no-JS 302 keeps the fragment. Found by
   the shoot.
3. **The console has a `session=` seam.** With a caller's session the four
   console functions run on that transaction and do not commit — the scratch
   test runs entirely inside one rolled-back transaction, so the never-delete
   tables receive no test residue.
4. **The active-environment filter is keyed on `tenant_id`.** Both production
   callers pass it; `tenant_id=None` keeps the unfiltered read because the
   tenant-only local harness carries no `public.environments`.
5. **`NEEDS_HUMAN` verdicts fold into "not determined" in the split** (the
   s6 vocabulary is PASS / FAIL / NEEDS_HUMAN / NOT_DETERMINED); the card's
   "human review" count is applicability, not that verdict. Ledgered (Low).

## a. THE LINK TABLE (`20260910_0010`; DB-real 3, 4)

`requirement_surface_links` + `requirement_surface_link_claims` per LLD §a.
Proven on the local harness (`test_step_3_surface_links.py`):

- DELETE on either table raises (trigger); UPDATE of any identity column raises
  (trigger); only the deactivation columns change on unlink (item 3).
- `source='DERIVED'` and `'VERIFIED_DERIVED'` trip the v1 write guard
  `requirement_surface_links_v1_declared_only` on a direct INSERT; a value
  outside the vocabulary trips the vocabulary CHECK; `'DECLARED'` passes both;
  the service refuses the reserved words before the DB is asked (item 4).
- the FK to `requirement_identities` refuses a declaration on a key with no
  identity (`no_identity`, item 6); the FK to the inventory members pins the
  declaration to a recorded surface of a recorded version.

## b. MATERIALISATION (DB-real 1, 2, 5)

- **declare → the links appear where the readers look** (item 1): after one
  declare, `list_tests_by_requirement(link_kind=COVERAGE_LINK_KINDS)` — the
  read `read_requirement_claims` makes — returns exactly the claims on the
  declared surface (three, one of them HUMAN_REVIEW), every link `verifies` /
  `linked_by='human'`; the engine's `_claim_test_ids` returns the same set.
  Nothing on the sibling surface is touched.
- **idempotent** (item 2): a second declare → same link id, `created=False`,
  0 materialised, 3 already linked, one active row, three ledger rows.
- **the link follows the surface, add-only** (item 5): a superseding claim set
  on the same inventory version with one more claim on the surface →
  `rematerialise` writes 1 ledger row and the reader returns four; the ledger
  names the set each link came from; a member revoked from the set keeps its
  link. On scratch (`test_step_3_scope.py`): `approve_claim_set` returns
  `surface_links_rematerialised` (0 before any declaration, 1 after the
  superseding set) and the audit row names the act.
- **unlink** (item 3): an independent human `verifies` link made before the
  declaration is ledgered `created_link=false` and LEFT on unlink; the two the
  declaration created are removed through S2's own API; the declaration row
  reads `active=false`, actor 7, time, reason; the three ledger rows are
  stamped; the readers drop the declaration's claims and keep the independent
  one; unlink on an inactive link is a no-op that says so.

## c. THE CARD + PICKER (scratch console test; the real app)

`read_requirement_surfaces` on scratch with a planted processing run (2 PASS /
1 FAIL on the declared surface): counts `{surfaces 1, checks 3, failures 1,
human_review 1}`; the row carries the display name, `DECLARED`, the actor's
name by lookup (`AK Scratch`), the split `{pass 2, fail 1, not_determined 0}`,
the latest job id; the picker offers the undeclared surface only; unlink
through the console records provenance and empties the card; a link of another
requirement is refused as unknown.

Over the REAL app on scratch (dev server :5055, driven through the page —
the HTMX picker → Declare, the confirm modal → Unlink; `shoot_step_3.py`):

| screen | observed |
|---|---|
| `card_empty_state.png` | "Conformance surfaces (0 surfaces · 0 checks · 0 failing · 0 human review)", "inventory v168", the empty state with the sentence, "+ Declare surface" |
| `picker_over_active_inventory.png` | "Declare a surface from inventory v168": six candidates with display name + canonical key + Declare; after one declaration the picker offers five |
| `card_two_declared_surfaces_with_split.png` | two rows (My Cases, Portal home): **DECLARED** chip, "inventory v168", the canonical key, "by AK Scratch · 2026-09-09", the split "3 pass / 1 fail / 1 not determined / 1 human review", Unlink; count line **"2 surfaces · 10 checks · 2 failing · 2 human review"** |
| `area2_test_plan_lists_conformance_checks.png` | the Test plan card (Area 2) reads "Test plan (10 tests)" with the ten conformance checks — no reader change |
| `unlink_confirm.png` | the kit's confirm modal: "Unlink My Cases?" naming the five checks that leave the plan |
| `card_after_unlink.png` | one row; count line "1 surface · 5 checks · 1 failing · 1 human review"; the Test plan reads 5 tests |

The routes: picker `GET … 200`; declare `POST … 204` + `HX-Redirect` (a full
re-render); the DB after the click: one link row, five ledger rows, five S2
`verifies` links, one `s2.surface_link.declare` audit row. Viewers see the card
read-only (no CTA, no Unlink — `can_manage` gates on MEMBER+ roles).

## d. THE RELEASE-SCOPE INTERIM (scratch; production read-only at merge)

`_environments_with_evidence(session, tids)` unfiltered = `[5901, 999999]`
(a run on an environment id with no row at all); with `tenant_id=1` =
`[5901]`; with env 5901 flipped inactive = `[]` — both the engine's loop and
`release_scope_readiness` enumerate through this one helper. On production
after the merge (read-only): release 16's scope names env 59 only (item 7).

## e. Non-goals held

No DERIVED write path; no planner; no policy; no layout change beyond the
new card; conformance semantics untouched (rules, applicability, verdicts,
standard views); S2's link table and readers untouched; no backfill.

## f. Suites (D-468) at the implementation commit

- **unit: 5,039 passed** (the three `.env`-reading live-parity tests included — they mirror the engine's environment choice with `tenant_id=1` and read production, where the migration is not yet applied; the active-environment filter joins `public.environments`, which exists there).
- **test_representation (local PG): 421 passed, 3 skipped, 4 deselected** (415 before + the six Step 3 harness tests).
- **DB-real corpus on clean scratch: 107 passed, 7 skipped, 1 red** across the nineteen DSN-gated files (the new `test_step_3_scope.py` included) + `test_scheduler_stale_tenants.py`, run with DATABASE_URL / S3A3 / S5 on scratch and the test JWT secret (the repair-gate suite mints its own JWT — without the secret two of its tests fail on `jwt.encode`, an environment fault, not a product one). The red is the report-slice runs-list window artefact ledgered at D-480, not this slice. `test_step_2_scope.py` moved its evidence from environment 5902 (no environments row) to the active 5901: under §d an environment id without an active row is excluded by design.
- **Pages: 5 passed. Browser-gated: 63 passed, 11 skipped** (SPIKE_BROWSER=1).

## g. Migration, classified (for the merge runbook)

`20260910_0010` — **ADDITIVE**: two new empty tables, two trigger functions +
three triggers, three indexes, two comments; no data write; DROPs only in the
downgrade. Dumpless under D-476. The reader window is safe in BOTH directions:
no existing table changes shape; the new code reads its own tables through a
best-effort console (`available=false` before the migration, never a 500) and
the engine's active-env filter joins `public.environments`, which exists.
Migration first regardless (D-285).

## Residual, stated plainly

- Scratch carries 167 replay-planted inventory versions; the "active inventory"
  derivation there picks a residue surface (`rs-…|/x`). Production has v1 / v2
  only. Scratch hygiene, not a product matter; the shoot planted its own
  clean v168 and removed it (scratch-only trigger bypass for FIXTURE removal —
  `remove_step_3_world.py`, `session_replication_role = replica`).
- The `external_system="jira"` misnomer on every requirement link is carried
  (Step 1 FIX PLAN); the new table records the identity's system half.
- `NEEDS_HUMAN` verdicts count under "not determined" in the split (Low).

## h. Production transcript (2026-09-09, GO #1 + GO #2)

| leg | observed |
|---|---|
| pre-flight trees | branch @ 8b85663 clean; `main` = origin/main = 383ee2f, delta zero; branch 2 ahead |
| pre-flight prod probes (read-only) | neither new table present; tenant head `20260909_0010`; requirement links on conformance claims **0**; release 16 scope environments — the DEPLOYED helper: **[59, 78]**, the branch's helper over the same rows: **[59]**; env 78 inactive / production / read_only; active inventory by the derivation: v2 (six members); identities 42 |
| classification | **ADDITIVE, dumpless (D-476)**: one alembic file; 2× CREATE TABLE IF NOT EXISTS, 2× CREATE OR REPLACE FUNCTION, 3× CREATE TRIGGER, 3× CREATE INDEX, 2× COMMENT ON; every DROP is the downgrade or the idempotent trigger re-create; the one "UPDATE" token is `BEFORE UPDATE ON` in a trigger definition; zero data-write statements added to runtime code outside the new tables' own service and its audit rows; no backfill |
| the window, proven | direction 1 (migration first, old code): the deployed tree at 383ee2f names neither table, nor `surface_links`, nor `requirement_surface_console` — **0 files** in `primeqa`, `alembic`, `scripts`. Direction 2 (new code before the migration): safe for every READ (best-effort console → "unavailable"; the engine's filter joins `public.environments`, which exists) but NOT for two WRITES — the `approve_claim_set` hook and a declaration would fail with UndefinedTable. The runbook never enters it |
| GO #1 — migration | `alembic … upgrade tenant@head` 02:08:12Z → 02:08:33Z, exit 0, the single step on `tenant_1` |
| GO #1 — read-back | head `20260910_0010`; both tables (12 / 8 columns), 0 rows; CHECKs vocabulary + v1 write guard + deactivation-complete; FKs identity + member + link + claim set; indexes active-unique + requirement + ledger-test; triggers no-delete ×2 + immutable; both functions; no other tenant schema carries the table |
| GO #1 — refusal proofs (one rolled-back transaction) | DELETE refused ("never deleted — unlink is a state change"); identity UPDATE refused ("immutable once declared"); `source → DERIVED` refused (immutable); direct INSERT `source='DERIVED'` refused by the write-guard CHECK; DECLARED passed both CHECKs; the deactivation UPDATE allowed; rows after rollback 0 / 0 |
| GO #2 — merge + deploy | merge **ed727ac** (`--no-ff`, parents 383ee2f + 8b85663, author AK, 0 trailers) pushed 02:10:54Z; four services **SUCCESS** at ed727ac by 02:13:23Z; `/api/_internal/health` 200; web + browser-worker 0 error-class lines in the last 400 |
| read-only proof — the scope | `release_scope_readiness(1, [req-283, SQ-205, SQ-207, SQ-209, SQ-210])` on production: **environments [59]**, 19 claims, **non_current 0, 19 CURRENT**; the decision tab renders no refusal block and an enabled Evaluate — release 16 is current; evaluating it is AK's act, not this step's. `release_decisions` for 16: 0 rows |
| read-only proof — the card | `/requirements/284` (SQ-205) 200: the "Conformance surfaces" card, count line "0 surfaces · 0 checks · 0 failing · 0 human review", tag "inventory v2", the empty state, "+ Declare surface" present, no "unavailable" text |
| read-only proof — the picker | `GET /requirements/284/surfaces/picker` 200: six candidates over inventory v2 — Contact Support, My Cases, Portal home, Portal home (tabset 2), Search results (declared term: portal), Topic Catalog |
| nothing written | links 0, ledger 0, conformance-claim requirement links 0 after the proofs |
| deploy-watch findings (not this slice) | (1) worker: S4 job 734 (claim 2387f14d, req-302, env 59; enqueued 02:09:35Z by a system actor, `created_by` NULL, BEFORE the merge push) completed 02:13:28Z on the new worker — run cb3a06e8 **outcome failed**, no `failure_category` / `sf_error_code`, **stamped org 902850e3 @ seq 253** (= the org's current sequence; Step 2's stamp holds under the Step 3 worker); the failure notification went to the two admin addresses; the first and only run on that claim. (2) scheduler, once: "llm usage log write failed task=repair_proposal tenant=1: Foreign key associated with column 'llm_usage_log.requirement_id' could not find table 'requirements'" — the best-effort usage write (`intelligence/llm/usage.py:92`) lost one row because the scheduler process has not imported the ORM model that owns `requirements`; ledgered in the FIX PLAN, untouched here |

No dump was taken (ADDITIVE, classified). No data act followed the merge. The schedule reactivation in the brief was conditional on AK's words, which were not given; the D-214 panel was not touched.
