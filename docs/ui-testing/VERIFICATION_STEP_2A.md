# VERIFICATION — Step 2a: the bulk readiness resolver made actually bulk

**Branch** `step-2a-bulk-readiness` from `main` @ db35fa8. A performance fix to
canonical Step 2 code, cut as its own slice before Step 6a resumes. No separate
LLD: the design is the section below.

## Design

### Why this exists

`resolve_run_readiness_bulk` was bulk in name only. It looped, and its own
docstring stated the assumption that made that acceptable:

> Per-pair reads today (the pair sets on every page are small — a release's
> claims × its environments)

Step 6a's Requirements list breaks that assumption. Measured on production:
**231 functional claims across 34 requirements**, so a 20-row page spans about
**136 claims**, and one requirement (req-302) alone has **79**. At four queries
per pair that is **over 500 round trips to render one page** — the N+1 the
codebase warns about, on a surface the approved mock puts readiness on.

### What changes, and what does not

**`resolve_run_readiness` remains THE DEFINITION of readiness.** The bulk path
is an optimisation of it, never a second opinion. The same five rules in the
same order, the same predicates, the same vocabulary, the same refusal reasons:

1. no run in this environment → `NEVER_RUN`
2. the latest run carries no stamp → `CANNOT_DETERMINE / unstamped`
3. the org's current sequence refuses → `CANNOT_DETERMINE` with its reason
4. the claim records no reads → `CANNOT_DETERMINE / no_coverage`
5. a covered read changed after the stamp → `STALE` with the reads named,
   else `CURRENT`

Three per-pair reads become three set-based reads over the whole pair list:

| read | before | after |
|---|---|---|
| the latest run per (claim, environment) | one query per pair, `ORDER BY finished_at DESC LIMIT 1` | one query, `DISTINCT ON (claim, env) … ORDER BY claim, env, finished_at DESC` over an `unnest` of the pairs |
| does the claim record what it reads | one query per pair | one query, `claim_test_id = ANY(:claims)` |
| which covered reads moved after the stamp | one query per pair | one query over an `unnest` of `(idx, claim, org, since)` triples, keyed by the caller's pair INDEX so two pairs on the same claim with different stamps stay apart |

The org's current sequence is still resolved once per distinct org, exactly as
the loop already did. Total: **3 queries + 1 per distinct org, whatever the
pair count.**

### How R-ground is preserved exactly

R-ground (D-484) says staleness is targeted through what a claim reads — never
"the org moved, so everything is stale". The bulk changed-reads query carries
the single-pair predicates **verbatim**: a read counts as changed only when the
claim's OWN covered entity closed after THAT run's stamp, or an edge on that
entity opened or closed after it, in THAT run's org. Two pairs of the same
claim with different stamps get different `since` values because the triple
carries each pair's own stamp. An org change touching none of a claim's reads
is invisible here, as before. No new staleness definition exists, and the lanes
are untouched: readiness is the org axis, and a conformance claim records no
org reads, so it reads `CANNOT_DETERMINE / no_coverage` exactly as it did — the
fact that made D-488 split the lanes.

### The one deliberate difference

Both paths now carry `ORDER BY` on the changed-reads query. The row SET is
unchanged; only its ORDER becomes defined. Previously the order was whatever
PostgreSQL returned, so the reads named in a STALE sentence could vary between
two runs of the same code. Making it deterministic is what lets parity be
asserted element for element, and it makes the rendered sentence stable. The
dedupe helper is now shared by both paths so they cannot drift.

### Migration

**None.** No schema change, no data write, no new column, no new table. The
slice is code and tests only.

## Parity — the acceptance test

`tests/integration/test_representation/test_step_2a_bulk_parity.py`, six tests
(a seventh is the gated measurement below). Equality is asserted over the full
`as_dict()` **plus** `changed_reads` with its order **plus** the rendered
`sentence` — nothing a caller can read may differ.

| test | asserts |
|---|---|
| the fixture spans every state | the world really produces CURRENT, STALE, NEVER_RUN and CANNOT_DETERMINE, with each refusal reason present (`unstamped`, `no_coverage`, `org_never_synced`) — a parity test over one state would prove nothing |
| bulk equals per-pair, element for element | over all ten pairs at once |
| parity pair by pair in isolation | each pair alone, so the bulk path cannot depend on its neighbours |
| parity on the pathological shapes | the empty input (`[]` and `None`), a claim that never ran in an environment nothing ran in, duplicate pairs collapsing to one key, a claim whose latest run is in a DIFFERENT org from its other run, and the STALE claim's named reads matching with order included |
| parity survives input order | the same pairs reversed give the same result |
| a fixed number of round trips | the bulk path stays at or under 8 queries for the fixture set, and fewer than the loop |

The fixture world spans two orgs, three orgs' worth of sequence states, two
environments, both lanes (a functional claim and a `ui`-archetype conformance
claim), a claim with its coverage rows deleted, a legacy unstamped run, and an
org that never synced.

**Result: 6 passed.**

## Behaviour unchanged elsewhere

Captured over PRODUCTION data, read-only, on main's resolver and then on this
branch's, with wall-clock fields scrubbed (they differ between two runs of the
same code):

| consumer | captured |
|---|---|
| release 16's decision preview | the Step 5 assembler + engine: recommendation `go`, 7 evidence lines |
| the release scope check | 19 claims, 0 non-current, environments [59] |
| one runs-list page | 18 pairs, all CURRENT |

```
sha256 before: 643466ff3e391d465dd8555018d2e3ac
sha256 after : 643466ff3e391d465dd8555018d2e3ac
diff: no output — IDENTICAL
```

Byte-identical, 20504 bytes each.

## Measured: a Requirements-shaped page

`STEP_2A_MEASURE=1 pytest … ::test_measure_a_requirements_shaped_page` builds
140 claims, each with coverage and a stamped CURRENT run, on the local harness
so wall time measures the CODE rather than a network:

| path | queries | wall time |
|---|---|---|
| per-pair (before) | **560** | 0.337 s |
| bulk (after) | **4** | 0.011 s |

Identical results, asserted in the same test. 140× fewer round trips, 30×
faster on a sub-millisecond local socket. The query count is the number that
travels: on production the app pays its own round trip 560 times instead of 4.
For scale, the same 560 round trips measured through the TCP proxy used for
read-only probes (409 ms round trip) would take about four minutes, which is
why the Step 6a readiness column could not be rendered from here at all.

## Suites

| suite | result |
|---|---|
| `tests/integration/test_representation` (readiness's own home) | **442 passed, 3 skipped** (436 before + the 6 new parity tests) |
| `tests/unit` | **5056 passed, 3 failed** — the live-parity trio, which reads the real database through `DATABASE_URL` and fails whenever that points at scratch. Re-run against the real database below. |
| the DB-real corpus | see §suites-detail |
| pages | see §suites-detail |

## §suites-detail

| suite | result |
|---|---|
| `tests/integration/test_representation` | **442 passed, 3 skipped** |
| `tests/unit` | **5056 passed**; the 3 live-parity tests re-run against the real database: **3 passed** |
| DB-real corpus (21 files) | **111 passed, 7 skipped, 1 failed** — the failure is `test_report_slice::test_a_runs_list_carries_both_recorded_runs`, the known pre-existing window artefact (its 2026-08-31 baseline now sits behind 140 newer rows in a fixed 50-row window). Unrelated to readiness; ledgered since D-488. |
| pages (`REPORT_PAGES=1`) | **9 passed** |
| the gated measurement | 1 passed (see above) |

## What this unblocks

Step 6a's Requirements list can now carry the readiness column the approved
mock specifies, at 4 queries per page instead of 500+. Three existing surfaces
get the same reduction for free: the runs list (20 pairs per page), the release
scope check (a release's claims × its environments — 19 pairs on release 16),
and the Step 5 evidence assembler, which calls the resolver once per target
environment.

## Post-merge transcript (2026-09-11)

**Merge** 97a9699 (parents db35fa8 + f70dce4), pushed to `main`; author AK,
zero `Co-Authored-By`. Classified **WRITE-FREE** with evidence: zero migration
files, zero write verbs added to runtime code, eight added SQL statements all
SELECT, one runtime file touched. Dumpless. No migration, so no code/schema
window exists in either direction and the reader risk is nil by construction —
the six call sites in `substrate_decision.py` needed no edit, the signature and
return shape being unchanged.

**Deploy.** Four services SUCCESS on the merge commit. Health 200,
`error_rate 0.0`. Zero error-class lines in 400 log lines per service.

**The identity proof.** The three consumers captured over production data,
read-only, three times — on main's resolver, on the branch's, and again after
the merge and deploy:

```
sha256 before merge (main's loop) : 643466ff3e391d465dd8555018d2e3ac
sha256 branch (bulk)              : 643466ff3e391d465dd8555018d2e3ac
sha256 after merge + deploy       : 643466ff3e391d465dd8555018d2e3ac
```

20504 bytes each, `diff` silent. Release 16's decision preview (`go`, 7 lines),
the release scope check (19 claims, 0 non-current, environments [59]) and one
runs-list page (18 pairs, all CURRENT) are byte-identical across all three.

**The rendered pages, GET only, on the deployed app** (the D-487 standing
correction):

| page | result |
|---|---|
| `/runs/substrate?group=runs&since=30d` | **200** in 4.2 s, **20 rows carrying readiness pills**, all CURRENT |
| `/releases/16?tab=decision` | **200** in 2.4 s, quality card at **GO**, six evidence lines, no scope refusal |
| `/requirements` | **200** |

Screenshots: `step-2a-fixtures/LIVE_runs_list_readiness_after_2a.png`,
`step-2a-fixtures/LIVE_release_16_decision_after_2a.png`.

Recorded as **D-489**. Step 6a resumes from here with the readiness column
intact per the approved mock.
