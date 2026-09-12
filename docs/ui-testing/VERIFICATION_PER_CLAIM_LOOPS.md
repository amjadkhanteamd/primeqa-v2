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

*Sections b onward are written as the build proceeds.*
