# LLD Step 1 — provenance + identity (Fork 1 ratified)

Status: RULED 2026-09-07 (AK GO) — build follows on this branch.
Rulings: **R-probe** (the probe identity lands `probe` by an EXPLICIT
recorded override, reason + cited entry in the backfill data — never by
prose); **R-jira-gap** (`SQ-206` / `SQ-211` stay `CANNOT_CLASSIFY` until a
re-import decorates them and sets `jira` — a Jira key is a fact of the
row, not of the string); **072 dump-first** regardless of the
zero-collision proof (a data write on the requirements table is insured,
D-285 MIGRATE-FIRST).
Branch: `step-1-provenance` (from main @771793b, D-482).
Derives from: D-479 (the unified sequence; Step 1 = "provenance +
identity: `external_key` ruled (Fork 1); origin representable
(`fixture` / `probe` / `CANNOT_CLASSIFY` backfill); default views hide
fixtures with a visible hidden-count; one identity format; dangling
keys rendered as referential gaps"), the surface plan §6 (Phase 1,
"built on the wrong table" — the picker reads link keys, not rows) and
Fork 1 (lean B, ratified: the link key is first-class identity; origin
is a separate field), D-166 (S3 writes `generated_from` links), SPEC S2
§9 (requirement links), D-476 / D-285 (dump policy, MIGRATE-FIRST).

**Thesis.** Today one free-text column, `test_requirement_links.
external_key`, holds keys from four populations (Jira `SQ-205`, derived
`req-282`, fixture `REQ-ARC-*`, and the same shapes with their rows
gone), origin is not representable for the ones that never had a
`requirements` row, and every surface derives a display string of its
own (`#282`, `req-282`, "Requirement #282"). Step 1 makes the key the
identity — one column, verbatim, on every surface — puts origin on a
separate identity object established once and never re-keyed, renders a
key whose decorated record is missing as a referential gap, and hides
fixture / probe identities from the default views behind a visible
count.

**The counts in the brief are the PICKER's view, corrected here** (§0):
the identity population is **42**, of which the picker shows 34 (its
join to approved claims drops 8); the referential gaps are **four**, not
one (`req-280`, `req-282`, `SQ-206`, `SQ-211`) and every one of them was
made by `purge_requirement`'s hard DELETE leaving the link rows behind.

**The TA invariants (verbatim; each is enforced below and tested):**

1. immutable once established;
2. unique within tenant scope;
3. origin represented separately;
4. display id is not identity;
5. a missing decorated record is a referential gap, not a missing
   identity;
6. migration must never silently re-key an existing object.

---

## 0. Pre-flight — the sites, cited, and the counts corrected

| site | fact |
|---|---|
| `migrations/003_test_management.sql:32-33` | `requirements.source varchar(20) CHECK (source IN ('jira','manual'))` — the ROW's provenance; not widened (origin lives on the identity, §b) |
| `test_requirement_links` (alembic `20260518_1014`; prod DDL read back) | PK `(test_id, external_system, external_key, link_kind)`; `external_system` enum has ONE value `jira` (every key rides it, including `req-N` and fixtures); `link_kind ∈ {generated_from, verifies, related_to}`; index on `(external_system, external_key)` |
| the ONE runtime link writer | `generation/persistence.py:164` — S3 writes `generated_from` with `outcome.requirement_ref.key`; the six `verifies` links on `req-302` were written by a human through the coordinator directly (no route) |
| the ONE key derivation | `s3_enqueue._requirement_to_ref` (:23) `key = jira_key or f"req-{id}"`; twinned by `decision_composer.external_keys_for_requirements` (:38) — six callers in views.py, the composer, the release views |
| the picker | `s4_execution_console.list_runnable_requirements` — distinct link keys with ≥1 APPROVED claim; `run/index.html:63` renders `req-N` as `#N`; `:64` renders a missing summary as "Untitled — re-sync from Jira" |
| `/requirements` | `views.py:2457-2568` lists `requirements` ROWS (`RequirementRepository.list_page`, filters `section_id / source / is_stale / coverage`); `requirements/list.html:96` "Requirement #N" fallback; `:58-61` the `source` filter |
| `/requirements/<id>` | `views.py:2570-2727`; `requirements/detail.html:6,11,43` "#N" / "Requirement #N"; `:56` derives `req-N` |
| releases | `releases/detail.html:312` derives `req-N` (unchanged this step; §e) |
| manual create | `test_management/routes.py:205-207` → `service.create_requirement(**kwargs)` → `repository.create_requirement` (:166) stores a user-typed `jira_key` with NO uniqueness check (only the Jira import checks `find_by_jira_key`, :259, among live rows) |
| purge | `repository.purge_requirement` (:296) is a HARD DELETE; the link rows survive — that is how the gaps below were made (activity_log: `soft_delete` 280/282 on 2026-07-06, `purge` 281/297/319) |

**Counts, corrected (production, read-only, 2026-09-07).** The brief's
"34 live link keys — 7 req-N (1 dangling), 6 Jira, 21 fixture/probe" is
the PICKER's view (keys with ≥1 approved claim). The identity
population is larger:

| population | identities | in the picker (≥1 approved) |
|---|---|---|
| `req-N` | 8 (280, 282, 283, 301, 302, 315, 320, 322) | 7 (280 has 0 approved) |
| Jira `SQ-*` | 7 (205, 206, 207, 209, 210, 211, 212) | 6 (211 has 0 approved) |
| fixture `REQ-(ARC|D299|D300|L7A–G)-*` | 27 | 21 |
| **total** | **42** distinct `(external_system, external_key)`; 408 link rows | 34 |

Referential gaps (a key with claims and NO `requirements` row): **four**,
not one — `req-280` (1 link), `req-282` (2), `SQ-206` (1), `SQ-211` (1).
`requirements` rows on tenant 1: 11 (5 jira, 6 manual incl. the probe
row 322 "Probe lifecycle integrity for PLS TA …"), 0 deleted, every row
has links. Across all 15 tenants: 25 rows, 0 deleted, 0 live duplicate
`jira_key`, 0 rows whose `jira_key` collides with another row's
`req-<id>`, 0 `jira_key` shaped like `req-N`. Only tenant 1 has a
substrate schema (the link table exists in `tenant_1` alone).

The fixture keys' writers are NOT in the repo (the lever-ladder and
live-eval scratch scripts, never committed — D-299.2, D-300.2, D-302,
D-304.1, D-305.1, D-333, D-428 record them); classification by prefix is
therefore declared data in the backfill (§b), cited to those entries.

## a. IDENTITY — the key, verbatim, everywhere

**Rule.** The identity of a requirement is `(external_system,
external_key)` as stored on `test_requirement_links` — the key is
first-class; `requirements.id` is a row id and is never displayed. The
DISPLAY form is the key itself, verbatim; nothing derives a second
string. One format on every surface = "the key as stored".

| current form | ruling |
|---|---|
| `SQ-205` (Jira) | stays; identity = `SQ-205` |
| `req-282` (derived from a row id at generation) | stays as the identity it already is; displayed `req-282` |
| `#282` / "Requirement #282" (display-only derivations at `run/index.html:63`, `requirements/list.html:96`, `requirements/detail.html:6,11,43`) | DIES — replaced by the key |
| `REQ-ARC-*`, `REQ-D299-*`, `REQ-D300-*`, `REQ-L7A–G-*` | stay as identities with `origin = fixture` |

**One identity column on the row.** `requirements.external_key text`
(public migration 072, §f): the key the row DECORATES. Backfilled for
every existing row as `COALESCE(jira_key, 'req-' || id)` — exactly the
key `_requirement_to_ref` derives today, so no row changes key (invariant
6, asserted by the before/after key-set test, §g). New rows set it at
creation: Jira import → the Jira key; manual create without a key →
`req-<id>` (assigned after the INSERT, the same value as today); manual
create WITH a typed key → that key (the affordance in §c is this path).
`_requirement_to_ref` and `external_keys_for_requirements` read
`external_key` first (fallback to the old derivation only for a row
that predates 072 in a test database). `jira_key` stays as Jira's own
reference for re-sync; it is no longer an identity.

**Unique within tenant scope (invariant 2).** Two constraints, both
proven collision-free by the pre-flight dry-run (§0):
`UNIQUE (tenant_id, external_key) WHERE deleted_at IS NULL` on
`requirements` (a live row decorates at most one identity per tenant;
a soft-deleted row keeps its key without blocking a re-decoration), and
the identity object's primary key `(external_system, external_key)` in
the tenant schema (schema-per-tenant IS tenant scope). The manual-create
hole (a typed key with no check) closes: the unique index refuses the
second live row, and the service returns the ValidationError the Jira
import already raises. A CHECK `external_key !~ '^req-[0-9]+$' OR
external_key = 'req-' || id` forbids a typed key from spoofing another
row's derived namespace (0 such rows today).

**Immutable once established (invariant 1).** The identity object's key
columns are refused on UPDATE by a tenant-schema trigger
(`requirement_identities_immutable`); `requirements.external_key` is
excluded from every update path (`update_requirement` never accepts it;
a repository test asserts the column is not in the writable set).
Re-import of a Jira requirement whose row was purged decorates the
EXISTING identity (§c) — it never mints `SQ-206'`.

## b. ORIGIN — a separate identity object, classified by evidence

**The object.** A new tenant-schema table (S2 — the link table's home;
SPEC §9 grows a "requirement identity" concept) `requirement_identities`:

| column | meaning |
|---|---|
| `external_system external_system NOT NULL` | the link's system enum (`jira` — the only value; the enum's name is a known wart, not renamed here) |
| `external_key text NOT NULL` | the key, verbatim; PK with the system |
| `origin text NOT NULL CHECK (origin IN ('jira','manual','fixture','probe','CANNOT_CLASSIFY'))` | provenance of the identity — never of a row |
| `origin_evidence jsonb NOT NULL` | the rule that classified it (`{"rule": "requirement_row", "requirement_id": 302, "source": "manual"}`, `{"rule": "fixture_prefix", "prefix": "REQ-L7D-", "cited": "D-305.1"}`, `{"rule": "declared", "by": "s3", ...}`, `{"rule": "override", "reason": "...", "decided_by": <user>}`, `{"rule": "none"}`) |
| `established_at timestamptz NOT NULL DEFAULT now()`, `established_by text NOT NULL` | who established it: `backfill@v1`, `s3`, `jira_import`, `human:<user_id>` |
| `classifier_version text NOT NULL` | `origin@v1` |

Why not a column on `requirements` (the surface plan's first idea): 31
of the 42 identities have no row, so a row column cannot represent
them (§0). Why not a column on the link rows: origin belongs to the
identity, not to each of its 408 (claim, key) pairs — one identity, one
origin (invariant 3). Decoration is NOT a column here: an identity is
decorated iff a live `requirements` row with that `external_key` exists
in the tenant (one read, no dual write, nothing to drift).

**Classification by evidence (the backfill; never guessed):**

| evidence | origin |
|---|---|
| key matches a declared fixture prefix `^REQ-(ARC\|D299\|D300\|L7[A-G])-` (data in the script, cited to the D-entries in §0) | `fixture` |
| a live `requirements` row with this key and `source = 'jira'` and a `jira_key` | `jira` |
| a live `requirements` row with this key and `source = 'manual'` | `manual` |
| anything else — a key with claims and no row, no declared prefix | `CANNOT_CLASSIFY` |

**Dry-run counts on tenant 1 (read-only, 2026-09-07):**

| origin | identities | of which gaps (no row) |
|---|---|---|
| `fixture` | 27 | 27 (by design — fixtures never had rows) |
| `jira` | 5 | 0 |
| `manual` | 6 | 0 |
| `CANNOT_CLASSIFY` | 4 | 4 (`req-280`, `req-282`, `SQ-206`, `SQ-211`) |
| **total** | **42** | 31 |

Two rulings for AK inside this table:

- **R-probe — RULED: apply the override.** The one probe identity
  (`req-322`, decorated by `requirements` 322, `source = 'manual'`,
  "Probe lifecycle integrity for PLS TA …", the D-457
  `fixture/pls-ta-probe` campaign) classifies `manual` by the evidence
  rule — a summary is prose, not evidence. It lands `probe` through an
  EXPLICIT override carried as DATA in the backfill script
  (`_OVERRIDES`), each entry naming its key, its origin, its reason and
  its cited entry, and recorded as
  `{"rule": "override", "reason": ..., "cited": "D-457"}` in
  `origin_evidence`. Post-ruling counts: fixture 27 / jira 5 / manual 5
  / probe 1 / CANNOT_CLASSIFY 4.
- **R-jira-gap — RULED: `CANNOT_CLASSIFY`.** `SQ-206` / `SQ-211` are
  Jira-SHAPED keys whose rows are gone. A Jira key is a fact of the
  ROW, not of the string, so the shape alone classifies nothing; they
  render as gaps (§c) and a re-import from Jira decorates them and sets
  `jira` at that moment (`origin_evidence.rule = "requirement_row"`).
  Classifying the shape would be a guess about a deleted record.

**Backfill = an idempotent script**, `python -m
primeqa.test_representation.identity_backfill --tenant-id N [--apply]`:
reads the union of DISTINCT link keys and live row keys (`COALESCE(
jira_key, 'req-'||id)`), classifies, prints the per-origin table + the
gap list; `--apply` inserts `ON CONFLICT DO NOTHING` (second run: 0
inserts, same counts) and writes one `activity_log` row
(`identity.backfill`, the counts). The re-key rule is asserted by the
script itself: `set(before keys) == set(identity keys after)`, or it
refuses to commit. Runtime writers after the switch: S3's link write
establishes the identity if absent (origin from the row that the
generation request came from — `jira` / `manual`; a caller-declared
`requirement_ref.origin` of `fixture` / `probe` is honoured and
recorded as `declared`; a key with neither → `CANNOT_CLASSIFY`, loudly
logged); Jira import and manual create establish/decorate; purge NEVER
touches an identity (the row goes; the identity and its claims stay —
it becomes a gap, honestly).

## c. REFERENTIAL GAPS — rendered as gaps, never as missing identities

A gap = an identity with ≥1 claim and no live decorating row. It renders
on `/requirements` as a gap row (the approved mock): the key verbatim,
the origin chip (`CANNOT_CLASSIFY` slate "?" for the four; `fixture`
amber for the 27 — but fixtures sit behind the hidden toggle, §d), the
claim counts the identity already has, the text "no requirement record",
and the affordance **"Create requirement record"** (MEMBER+). The
affordance DECORATES: `POST /requirements/decorate` with the identity
key creates a `requirements` row with `external_key = <that key>`,
`source = 'manual'`, `jira_key = NULL`, summary from the form — the
identity keeps its key and origin (`CANNOT_CLASSIFY` stays until a
human sets it — the same POST offers the origin picker, recorded as an
`override` with the user). It never mints a new key: a decorated
`req-282` has a row whose `id` is new (say 330) and whose
`external_key` is `req-282`; `_requirement_to_ref` returns `req-282`.
The picker's "Untitled — re-sync from Jira" fallback (`run/index.html:
64`) becomes the gap chip; the picker still lets a gap run (running is
about claims, not rows).

## d. DEFAULT VIEWS — fixtures and probes hidden, counted, one click away

- `/requirements`: the list stays row-paginated for DECORATED
  identities (the existing repository + filters), with two additions
  rendered from ONE identity read per request: a **gaps block** (never
  hidden — a gap is a defect to see, not noise) and a **hidden-count
  strip**: "27 fixture · 1 probe hidden — show". `?origin=fixture` /
  `?origin=probe` / `?show_hidden=1` reveal them as identity rows (key,
  chip, claim counts, no row actions). The `source` filter
  (`list.html:58-61`, `views.py:2479-2480`) is REPLACED by `origin`
  with the five values; `source` stays a row field on the detail page.
  Default = `origin ∉ {fixture, probe}` — the probe row 322 drops from
  the default list (its key is in the hidden set) and returns under
  `origin=probe`.
- `/run` picker: `list_runnable_requirements` joins the identity table
  for origin; default hides `fixture` / `probe` rows; the strip "21
  fixture hidden — show" (the picker's own count: identities with
  approved claims); `?show_hidden=1` or the origin filter shows them;
  the key column renders the key verbatim; the "select all" JS acts on
  visible rows only (already does, `:115-120`).
- The origin chip on both lists and on the requirement detail header:
  `jira` blue, `manual` gray, `fixture` amber, `probe` violet,
  `CANNOT_CLASSIFY` slate with "?". `data-origin` on every row for the
  tests.

## e. NON-GOALS (held exactly)

No staleness (Step 2). No requirement→surface link (Step 3). No
navigation change. No conformance change. Not this step, named:
`releases/detail.html:312` keeps deriving `req-N` for the release's
requirement list (a decorated row, so the value equals `external_key` —
identical output; the read moves to `external_key` in Step 3/5 when the
release surface is touched); the release LIST's fixture noise (surface
plan §6 item 4) and the `external_system` enum's misnomer are ledgered.

## f. Migrations, classified at pre-flight (for the merge runbook)

| migration | content | class → policy |
|---|---|---|
| tenant `20260908_0010_requirement_identities` | new table + CHECK + PK + the immutability trigger; no existing rows touched | ADDITIVE → dumpless (D-476); before deploy |
| public `072_requirements_external_key.sql` | `ADD COLUMN external_key text`; `UPDATE ... SET external_key = COALESCE(jira_key, 'req-'||id) WHERE external_key IS NULL` (25 rows, every value = today's derived key); partial UNIQUE index `(tenant_id, external_key) WHERE deleted_at IS NULL`; CHECK on the `req-N` namespace | the UPDATE is a DATA WRITE and the index/CHECK CAN REJECT rows → **potentially destructive** → **DUMP-FIRST, RULED** (AK, 2026-09-07): the dump is taken regardless of the zero-collision proof; the dry-run is still re-run at merge pre-flight and must read 0 collisions / 0 namespace violations / 25-of-25 values equal to the derived key before the migration runs (today: 0 / 0 / 25-of-25). D-285 MIGRATE-FIRST |
| the ORM | `Requirement.external_key` mapped; the OLD code never reads or writes it and the column is nullable → no ORM window in either direction; the new code needs it present → 072 before deploy |
| the backfill | `identity_backfill --apply` runs AFTER deploy (the identity table exists; the runtime writers are live) — dry-run counts reviewed here, re-run at pre-flight |

## g. Verification (VERIFICATION_STEP_1.md, on GO)

1. **No re-key**: the key set (links ∪ live rows) before 072 + backfill
   equals the identity key set after; every row's `external_key` equals
   its pre-072 derived key (row-for-row); the script refuses to commit
   on inequality (a planted mismatch proves the refusal).
2. **Origin counts match the dry-run**: 27 / 5 / 6 / 4 (with R-probe:
   27 / 5 / 5 / 1 / 4); a second `--apply` inserts 0 and reports the
   same counts.
3. **A planted duplicate key is refused**: a second live row with an
   existing `external_key` → the unique index; a typed key `req-999`
   on a row whose id is not 999 → the CHECK; an UPDATE of an identity's
   key → the trigger; the manual-create route returns the validation
   error.
4. **The gap row renders** for `req-282` (and the other three) with the
   chip and the affordance; decorating it creates a row with
   `external_key = 'req-282'` and no new identity (identity count
   unchanged; the gap count drops by one).
5. **Fixtures hidden by default** on `/requirements` and `/run`; the
   hidden count reads 27 (list) / 21 (picker); present under the
   filter; the probe row hidden by default and present under
   `origin=probe`; the picker's `#N` gone (`req-282` verbatim).
6. **Runtime establishment**: an S3 link for a new key establishes an
   identity with the row's origin; a declared `fixture` is honoured; an
   unknown key lands `CANNOT_CLASSIFY` with a log line; purge leaves
   the identity and the gap appears.
7. The D-468 set (unit / DB-real / pages / browser-gated); fixture
   screenshots over the real app on scratch (list with the strip and a
   gap row; the picker with the strip; the detail header chip).
