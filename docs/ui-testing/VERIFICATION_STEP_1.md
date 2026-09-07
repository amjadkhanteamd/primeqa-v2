# VERIFICATION — Step 1, provenance + identity

Executed 2026-09-07 on scratch (`plimsol_3a3`; tenant_1 at the tenant
chain `20260907_0010 → 20260908_0010`, public at 072) with the REAL
service / repository / coordinator paths and the REAL Flask app for the
fixture screenshots, and read-only against production for the census
and the backfill dry run. **No production row was written and no
Railway act was performed.** Branch `step-1-provenance` (from main
@771793b, D-482); design bb557ee. Merge gated — ONE tenant migration
(ADDITIVE) and ONE public migration (a data write + a rejecting index)
→ **dump-first, RULED** (AK).

Re-runnable: `tests/unit/test_requirement_identity.py` (the pure
classification table), `tests/integration/test_step_1_identity.py`
(the wiring on scratch; needs `DATABASE_URL` = `S3A3_TEST_DATABASE_URL`
= scratch).

---

## 0. The counts, corrected on the record

The brief's "34 live link keys — 7 req-N (1 dangling), 6 Jira, 21
fixture/probe" is the PICKER's view: `list_runnable_requirements` joins
to approved claims, which drops 8 keys. The identity population is
larger, and the gaps are more numerous.

| population | identities | in the picker |
|---|---|---|
| `req-N` | 8 | 7 (`req-280` has 0 approved) |
| Jira `SQ-*` | 7 | 6 (`SQ-211` has 0 approved) |
| fixture `REQ-*` | 27 | 21 |
| **total** | **42** distinct `(external_system, external_key)`; 408 link rows | 34 |

**Four referential gaps, not one**: `req-280`, `req-282`, `SQ-206`,
`SQ-211`. Every one was made the same way — `purge_requirement`
(`repository.py:296`) is a hard DELETE and the link rows survive it
(activity_log: `soft_delete` 280/282 and `purge` 281/297/319, July
2026). The interface called that "Untitled — re-sync from Jira".

## a. IDENTITY — the key, verbatim (invariants 1, 2, 4, 6)

`requirements.external_key` (072) carries the identity each row
decorates, backfilled `COALESCE(jira_key, 'req-' || id)` — **exactly**
what `_requirement_to_ref` derived before, so no row changes key. Both
key builders now route through ONE function
(`identity.key_for_requirement_row`), which prefers the column and
falls back to the identical derivation for a pre-072 row. Proven:
`req-282 → req-282`, `SQ-205 → SQ-205`, composer `['req-282', 'SQ-205',
'req-9']`.

The display form IS the key (`identity.display_key`). Every `#N`
derivation is gone: `run/index.html:63` (`#{{ r.key[4:] }}`),
`requirements/list.html` ("Requirement #N"), `requirements/detail.html`
(title, breadcrumb, heading) and `generation_run.html` (title,
breadcrumb, heading). A grep for `'#' ~ req.id` / `r.key[4:]` over
`primeqa/templates/` returns only an `href` in the release list — a
locator, not a display.

Uniqueness and immutability, all DB-real:

| refusal | mechanism | test |
|---|---|---|
| a second live row on one key | partial UNIQUE index `uq_requirements_tenant_external_key` | 3, 3b |
| a typed key spoofing another row's `req-N` namespace | CHECK `requirements_external_key_namespace` | 3c |
| re-keying an established identity | trigger `requirement_identities_immutable` | 3d |
| the friendly refusal before the index | `service._refuse_key_collision` via `repository.find_by_external_key` | 3 |

The manual-create hole is closed: `create_requirement` accepted any
typed `jira_key` with no check (only the Jira import called
`find_by_jira_key`).

## b. ORIGIN — a separate object, classified by evidence (invariant 3)

`requirement_identities` (tenant `20260908_0010`): PK `(external_system,
external_key)`, `origin` CHECK over the five values, `origin_evidence`
JSONB, `established_at/by`, `classifier_version = origin@v1`. Decoration
is a READ (a live row carrying the key), never a column — nothing to
drift. 31 of the 42 identities have no row, which is why origin cannot
live on `requirements`.

`classify_origin` is pure and ordered: explicit override → declared
fixture prefix → the live row's source → `CANNOT_CLASSIFY`. Every
verdict carries the rule that produced it. The ten fixture prefixes are
DECLARED DATA, each cited to the entry that records its campaign
(D-299.2, D-300.2, D-302, D-304.1, D-305.1, D-333, D-428) — the
scripts that minted those keys were never committed.

**Production dry run (read-only, `--tenant-id 1`, no `--apply`)**, with
both rulings applied:

| origin | identities |
|---|---|
| fixture | 27 |
| jira | 5 |
| manual | 5 |
| probe | 1 |
| CANNOT_CLASSIFY | 4 |
| **total** | **42** (31 gaps; `would_insert` 42) |

- **R-probe** (RULED): `req-322` lands `probe` only through the explicit
  `_OVERRIDES` entry carrying its reason and `cited: D-457`. Without it
  the same shape is plain `manual` — asserted both ways in the unit
  table. This is the sole difference from the pre-ruling counts
  (manual 6 → 5, probe 0 → 1).
- **R-jira-gap** (RULED): `SQ-206` / `SQ-211` classify `CANNOT_CLASSIFY`,
  not `jira` — a Jira key is a fact of the ROW, not of the string. A
  re-import decorates them and sets `jira` at that moment
  (`service.import_jira_requirement` → `_establish_identity`).

The backfill (`python -m primeqa.test_representation.identity_backfill
--tenant-id N [--apply]`) is idempotent and runnable BEFORE either
migration (it probes for the column and the table), so the counts are
reviewable at merge pre-flight. `--apply` writes one `activity_log`
row (`identity.backfill`).

## c. NO RE-KEY — the guard is the script's own precondition (invariant 6)

Before writing anything, the backfill asserts that the key set it plans
equals the key set the tenant already has (link keys ∪ live row keys);
after writing it asserts the identity table grew by exactly the planned
keys and lost none. Either way it raises `RekeyRefused` and names the
difference. DB-real 1: the sets match; a planted drop and a planted
invention each raise, naming the key. DB-real 1b: every live row's
`external_key` equals its pre-072 derivation, row for row.

**A correction found by the tests.** The read-back guard first compared
the whole identity table against the tenant's CURRENT key set. That
fires falsely on an identity whose row was purged and whose links were
removed — which is the design (6c). The guard now compares against
`established_before ∪ planned`: the run added exactly what it planned
and dropped nothing.

## d. REFERENTIAL GAPS and the DEFAULT VIEWS (invariant 5)

The console reports EVERY gap; the view shows the ones that are
defects. A fixture's missing record is its normal state — fixtures never
had rows, which is why Fork 1 chose lean B — so a gap on a hidden origin
rides behind the toggle with its origin. A gap on any SHOWN origin is
never hidden. On production the default gap block is therefore exactly
the four the brief names, all `CANNOT_CLASSIFY`.

Observed on the REAL app over the planted world (5 identities: 1 jira +
1 manual decorated, 2 fixture gaps, 1 `CANNOT_CLASSIFY` gap):

| screenshot | shows |
|---|---|
| `requirements_default_hidden_and_gaps.png` | strip "2 fixture requirements hidden from this view — show"; gaps block "Referential gaps (1)" with `SQ-902`, the `? unclassified` chip, "no requirement record", "1 test · 1 approved" and **Create requirement record**; two decorated rows `req-32` (manual) and `SQ-901` (jira) with keys verbatim and origin chips |
| `requirements_show_hidden.png` | all 5 identities, all four origins present |
| `run_picker_default_hidden.png` | strip "2 fixture/probe requirements hidden from this list"; three rows keyed `SQ-901` / `SQ-902` / **`req-32`** (not `#32`); `SQ-902` reads "no requirement record" where the old fallback said "Untitled — re-sync from Jira"; **"Run all approved · 7 tests"** with "Includes 2 hidden fixture/probe requirements." |

**A finding, made honest rather than changed.** "Run all approved" calls
`enqueue_all_approved_claims`, which enqueues every approved claim in
the tenant — including the identities hidden from the list. Hiding rows
while the button silently ran them would be the exact dishonesty this
step exists to remove. The count stays the true total (7 over 4 visible
tests) and the button now says what it covers. Changing the button's
scope is an execution-semantics change and is NOT in this step.

The `source` filter is replaced by `origin` on `/requirements`
(`source` remains a row field on the detail page).

## e. RUNTIME ESTABLISHMENT

| path | behaviour | test |
|---|---|---|
| S3 link write (`persistence.py`) | establishes the identity in the SAME transaction as the claim + link; a caller-declared origin is honoured and recorded `{"rule": "declared"}`; an unplaceable key lands `CANNOT_CLASSIFY` with a warning | 6, 6b |
| manual create / Jira import (`service`) | establishes (best-effort — a tenant with no substrate schema still gets its row) | 2, 6c |
| decorate (`/requirements/decorate`, MEMBER+) | creates the row carrying the identity's own key; **no key minted, no identity added**; an optional origin is recorded as a human override with its actor | 4 |
| purge | leaves the identity and its origin standing; the identity becomes a gap | 6c |
| a second sighting | `ON CONFLICT DO NOTHING` — an established identity is never re-classified behind the operator | 6 |

## f. Suites (D-468) at the implementation commit

- **Unit: 5,030 passed** (the 16 new in `test_requirement_identity.py`).
- **DB-real: 103 passed, 7 skipped, 1 red** across the seventeen
  DSN-gated files (the 12 new in `test_step_1_identity.py`). The red is
  the report-slice runs-list window artefact ledgered at D-480 (the B-1
  run falls outside the 50-row window on scratch), not this slice.
- **test_representation (local PG): 408 passed, 3 skipped.**
- **Pages: 5 passed. Browser-gated: 63 passed, 11 skipped** (SPIKE_BROWSER=1).

## g. Migrations, classified (for the merge runbook)

| migration | content | class |
|---|---|---|
| tenant `20260908_0010_requirement_identities` | new table + CHECK + PK + index + immutability trigger; nothing existing touched | ADDITIVE → dumpless (D-476), before deploy |
| public `072_requirements_external_key.sql` | ADD COLUMN; UPDATE 25 rows to their own derived key; partial UNIQUE index; namespace CHECK | data write + a rejecting index → **DUMP-FIRST, RULED** (AK, regardless of the zero-collision proof), before deploy |

Pre-flight dry-run to re-run at merge (today's values): 0 live duplicate
keys across all 15 tenants, 0 rows whose `jira_key` collides with
another row's `req-<id>`, 0 `jira_key` shaped like `req-N`, 25-of-25
backfilled values equal to the derived key. Only tenant 1 has a
substrate schema, so only tenant 1 gets identities; the public column
covers all 25 rows.

**The ORM window**: the new column is nullable and the OLD code neither
reads nor writes it, so 072 before the deploy is safe in both
directions; the new code needs it present. The tenant table is read only
by new code.

Deploy-day: dump → 072 → tenant `20260908_0010` → read-backs → merge →
four services → `identity_backfill --tenant-id 1 --apply` (the counts
above, reviewed) → read-back.

## Residual, stated plainly

- The `external_system` enum has one value, `jira`, and every key rides
  it — including `req-N` and the fixtures. The misnomer is untouched
  here (renaming an enum every link row references is its own slice).
- `releases/detail.html:312` still derives `req-N` for the release's
  requirement list. Every row there is decorated, so the value equals
  `external_key` — identical output. The read moves when Step 3/5
  touches that surface.
- The release LIST's own fixture noise (surface plan §6 item 4) is not
  in this step.
- Origin is established once and only a human override changes it; a
  fixture campaign that starts declaring its origin will be honoured
  from its first link.
