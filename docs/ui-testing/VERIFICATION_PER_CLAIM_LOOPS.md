# The per-claim loops — the decision tab's last N+1 family

Branch `per-claim-loops` from `main` @87e727d. Named as the next slice in D-492:
four reads issued once per claim account for 84 of the decision tab's remaining
156 queries. Step 2a's parity discipline applies in full — **the existing
per-claim behaviour is the DEFINITION, and the set-based form is an optimisation
of it, never a second opinion.**

---

## a. DESIGN

### a.1 The grounding, which changed the shape of this slice

The four loops do not all need new queries written. Three of them already have
an exact batch sibling in the same class, written and tested, that no caller
wired in:

| per-claim read | batch sibling | state |
|---|---|---|
| `coordinator.list_tests_by_requirement` | `list_tests_by_requirements` | exists, unused here |
| `coordinator.get_current_approved_claim` | `get_current_approved_claims` | exists, unused here |
| `result_store.read_grounding_validity` | `read_grounding_validity_bulk` | exists, unused here |
| `s4_execution_console._read_claim_runs` | none | **must be written** |
| `readiness.covered_reads_changed` | the SQL exists inside `resolve_run_readiness_bulk` | **must be extracted** |

All five per-claim calls live in two functions:

* `release_substrate_console._assemble_release_substrate` — the decision tab's
  substrate evidence panel. It loops over the release's claims and issues three
  reads each, after looping over the release's requirement keys for a fourth.
  Its own module docstring already claims "One tenant connection + a shared
  session (not N `read_claim_runs` calls)". The code does exactly N. The
  docstring described the intent; nobody made the code match it.
* `substrate_decision._assemble_claim_evidence_uncached` — already set-based for
  claims, approvals, grounding and runs. One call stayed in the loop:
  `covered_reads_changed`.

### a.2 What gets built

1. **`_read_claim_runs_bulk(session, test_ids, *, limit=50)`** — new. A window
   function partitioned by claim reproduces the singular query's per-claim
   `LIMIT`, returning `{test_id: [row, ...]}` with each list in the singular's
   order.
2. **`covered_reads_changed_bulk(session, triples)`** — extracted from
   `resolve_run_readiness_bulk`, which then calls it. **One implementation, two
   callers** — the alternative is a second copy of a predicate that decides
   staleness, and two copies of that rule would be the defect, not the win.
3. The two loops rewired to the five batch forms.

### a.3 The one place the batch and the loop can genuinely disagree

`_assemble_release_substrate` falls back, for a claim with no approved version,
to `list_grounding_validity(session, test_id=tid)[-1]`. That list is **bounded at
200 rows** and ordered ascending, so for a claim with more than 200 verdict rows
`[-1]` returns the 200th row, not the latest. `read_grounding_validity_bulk`
returns the true latest.

This is a latent difference, so it is settled explicitly rather than absorbed:

* **Measured on production, read-only.** The maximum verdict rows for any claim
  is **2**. **Zero** claims exceed the 200 bound. The shapes coincide today.
* **The slice still preserves the definition.** `read_grounding_validity_bulk`
  gains an optional `unpinned_list_bound`; the console passes 200 and so
  reproduces `[-1]` exactly, including above the bound. The default is `None`,
  so `substrate_decision`'s existing use is untouched.
* **The latent defect is ledgered, not fixed here.** A claim that accumulates
  more than 200 verdicts would read a stale "latest". That is a correctness
  slice with its own proof, and folding it in would make this one a semantics
  change — which the brief forbids.

### a.4 Determinism, and the honest note about it

`_CLAIM_RUNS_SQL` orders by `finished_at DESC` alone. Two runs of one claim
finishing at the same instant have an arbitrary relative order, and the console
reads `runs[0]`. A window function need not break the tie the same way, so
parity would be untestable. Both forms therefore gain `run_id DESC` as a
tiebreak.

Measured on production, read-only, before deciding: **zero** claims have two
runs sharing a `finished_at`, and **zero** runs have a NULL `finished_at` (which
would sort first under `DESC`). Nothing observable changes; an unspecified order
becomes specified. This is the same move Step 2a made when it added
`ORDER BY 1, 2, 3` to the singular changed-reads query.

The per-claim `LIMIT 50` is likewise preserved rather than assumed away: the
maximum runs for any claim on production is **30**, so the bound never truncates
today, and the bulk reproduces it per claim regardless.

### a.5 Parity is the acceptance test

Not sampling. For each shape below, the loop and the batch run over the same
session and their results are asserted **equal, element for element**, including
list order and every dict key:

| shape | why it is in the set |
|---|---|
| no claims | the empty short-circuit |
| one claim | the degenerate batch |
| many claims | the ordinary case |
| a claim with no approved version | the `list_grounding_validity[-1]` fallback |
| a claim with no grounding row at all | `not_computed` |
| a claim with no runs | `never_run` |
| a claim shared by two requirements | the dedupe on `test_id` |
| a deprecated claim | D-219 retirement from the corpus |
| one claim, two orgs, different verdicts | worst-of on the pinned read, latest on the fallback |
| two runs at the same instant | the tiebreak |
| more runs than the per-claim limit | the bound |
| **more than 200 verdict rows on one claim** | the only shape where the forms could diverge |

### a.6 Non-goals

No semantics change, no new columns, no migration — this slice is code and tests
only. The remaining reads on the decision tab that are NOT per-claim loops stay
as they are.

---

## b. What changed

Code and tests only. **No migration, none expected, none written** — this slice
adds no column, no table and no DDL of any kind.

| file | change |
|---|---|
| `intelligence/s4_execution_console.py` | new `_read_claim_runs_bulk`; the singular's tie broken by `run_id` |
| `sync/readiness.py` | new `covered_reads_changed_bulk`, extracted; `resolve_run_readiness_bulk` now calls it |
| `evolution/result_store.py` | `read_grounding_validity_bulk` gains `unpinned_list_bound`; the bound named once as `list_bound()` |
| `intelligence/release_substrate_console.py` | the per-claim loop replaced by four set-based reads |
| `intelligence/substrate_decision.py` | the last per-claim read replaced by one |

The evidence console's module docstring claimed it avoided N per-claim reads
while the code performed exactly N. The docstring now describes what the code
does, because the code now does it.

## c. Sweeps first (D-490)

Run before any page was rendered or believed: dict-method template names, dead
links and route hygiene, both navigation sweeps, no gate logic in templates, and
close 2's call-site guard. **60 passed.**

## d. Parity — the acceptance test

`tests/integration/test_representation/test_per_claim_loops_parity.py` carries
`_assemble_by_loop`, the pre-slice function body **copied verbatim from main
@87e727d**, and asserts the shipped function equals it element for element over
a world holding every shape in §a.5. **18 tests, all passing.**

Two of them exist to keep the suite honest rather than merely green:

* `test_the_bound_reproduces_the_list_read_ABOVE_the_bound` plants a claim with
  more verdict rows than the bound and asserts three things: the bounded batch
  equals the list read, the UNBOUNDED batch **differs** from it, and the two
  differ in the expected direction. Without the middle assertion the test would
  pass while exercising nothing.
* `test_the_panel_costs_a_FIXED_number_of_queries` measures both forms. The
  set-based panel must stay at or below six queries; the reference loop must
  still cost about three per claim, or the thing being compared against is not
  the code that shipped.

## e. Suites

| suite | result |
|---|---|
| unit | **5,138 passed** |
| `tests/integration/test_representation` | **460 passed**, 4 skipped |
| DB-real corpus (24 suites, clean scratch) | **104 passed**, 49 skipped, **1 failed** |
| page suites (`REPORT_PAGES=1`) | **45 passed**, 2 skipped |

The single red is the `test_report_slice` window artefact ledgered since D-488,
proven identically red on `main` in a worktree.

**A second red appeared and was triaged, not absorbed.**
`test_step_5_scope` failed asserting the seed policy is `draft`; scratch held it
`active`. It reproduced identically on `main` against the same database, so it
is not this slice. The cause is replay: that suite activates the seed policy and
uses it, and never restores it, so a second run of the corpus on the same
scratch database reddens it. Scratch was restored and the corpus returned to
104/1. The non-idempotency is ledgered.

## f. The production before/after

Both trees run in-process against production data under the server-side
read-only connection option. The guard was **proven, not accepted on its status
report**: `CREATE TABLE`, `INSERT` and `UPDATE` each raised
`ReadOnlySqlTransaction`; `SELECT` returned 25 releases. GET only throughout.

### f.1 Cost

| surface | queries before | after | connections |
|---|---:|---:|---:|
| release decision tab | 156 | **69** | 2 (unchanged) |
| releases list (control) | 30 | 30 | 3 |
| Requirements (control) | 33 | 33 | 1 |
| runs list (control) | 28 | 28 | 4 |

Wall time on the decision tab, over the public proxy where a round trip costs
roughly 600 ms: 84.4 s to 50.3 s. The durable number is the query count.

Across close 2 and this slice together the decision tab has gone from **213
queries and 11 tenant connections to 69 and 2**.

### f.2 What remains

Nothing on the decision tab is a per-claim loop any more. The largest single
contributor to the remaining 69 issues **six** queries, and
`_read_claim_runs` no longer appears at all.

### f.3 Parity — and the honest account of getting there

The first comparisons were not clean, and the reason is worth recording because
it nearly read as a regression.

Three surfaces matched immediately. The runs list did not — and neither did two
consecutive captures of **main**. The org-state band showed `seq 258` on the
first three pages of a run and `seq 260` on the fourth: a live production sync
landed *between two page renders of a single capture*. The decision tab then
disagreed in the same way, on a per-claim `@ S1 seq` line.

So the captures were interleaved until the data stopped moving:
main, then branch, then main again. The second and third agreed exactly, which
proves the earlier differences were the sequence advancing and not the code. A
final adjacent pair over all four surfaces then gave:

| surface | main | branch | verdict |
|---|---|---|---|
| decision tab | `7163820ff098939f` | `7163820ff098939f` | identical |
| releases list | `a598f8919e3d77e9` | `a598f8919e3d77e9` | identical |
| Requirements | `6e6d78642339ee7b` | `6e6d78642339ee7b` | identical |
| runs list | `c3408364ad6eb4bf` | `c3408364ad6eb4bf` | identical |

`diff` is silent on all four. Nothing any console returns has changed.

**The method this adds to close 2's.** Capturing a baseline three times catches
fields that are volatile *within* a run. It does not catch a value that is
stable within a run and moves *between* runs, which is what a daily sync looks
like. For that, interleave the trees and require an adjacent pair to agree.

## g. Ledgered

* **`list_grounding_validity(...)[-1]` is not the latest verdict above the row
  bound.** The list is bounded at 200 and ordered ascending, so a claim with
  more than 200 verdicts reads its 200th as "latest". Preserved exactly here so
  this slice stays an optimisation; the maximum on production today is 2 rows
  per claim, so nothing is currently wrong. It is a correctness slice of its own.
* **`test_step_5_scope` is not idempotent on a scratch database.** It activates
  the seed policy and never restores it, so the corpus reddens on a second run
  against the same database. The world-remover discipline (delete what the
  fixture minted) applies to state a fixture MUTATES, not only to rows it
  inserts.
