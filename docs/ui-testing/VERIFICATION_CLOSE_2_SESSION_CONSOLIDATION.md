# Close 2 — session / query consolidation

Branch `session-consolidation` from `main` @9a224e0. AK's brief: one session per
request; the claim read shared across the consoles that currently re-read it.
Targets — the release decision tab and the Requirements page. Step 2a's parity
discipline applies: **the existing per-console behaviour is the DEFINITION, and
consolidation is an optimisation of it.** Stop and hold if the fix would change
what any console RETURNS rather than how many sessions it opens.

---

## a. DESIGN

### a.1 What the pages actually did

A render opened one tenant connection **per console**. Measured over production
data (release 16, tenant 1) before any change:

| surface | queries | tenant connections |
|---|---:|---:|
| release decision tab | 213 | 11 |
| Requirements | 50 | 6 |
| releases list | 30 | 3 |
| runs list | 28 | 4 |

Two reads were performed repeatedly inside a single render:

* **the claim evidence assembly** — the substrate verdict card and the Step 5
  quality preview each assemble the same `(keys x environment x org)` evidence;
* **the manual quarantine map** — read once by the decision path and again,
  through its own connection, by every console that defaulted the argument.

Two more were read twice because two surfaces wanted the same number: the
**org-state band** and the **attention badge** (the sidebar's count and the
Requirements page's own count).

*(The 106-query figure in the brief was measured on the scratch fixture world.
Production release 16 carries more scope, and the counter used here counts every
statement on every engine. Before and after are measured with the same counter,
so the delta is sound even though the absolute differs from the brief's.)*

### a.2 The mechanism

`primeqa/semantic/read_scope.py` — a context manager that yields **one** tenant
`Session` for a read-only render, plus a `memo` keyed on that session.

Three limits are deliberate and written into the module:

1. **Reads only.** `get_tenant_connection` commits on clean exit, so sharing one
   connection across a request that also WRITES would batch those writes into a
   single transaction and change when they land. Write paths keep opening their
   own connection, exactly as before.
2. **Explicit, not ambient.** The view opens the scope and passes the session
   down through `session=` seams. Nothing reaches for a global. A console called
   without a session behaves exactly as it always did — that is the definition,
   and it is still reachable and still tested.
3. **Best-effort about acquisition only.** If the substrate cannot be reached the
   scope yields `None` and every console falls back to what it did before. An
   exception raised by the RENDER belongs to the render and propagates.

### a.3 Where the memo can and cannot be trusted

A memo hit may only serve a call whose inputs are **identical**. The key carries
every argument, including the resolved quarantine map, and `external_keys` is
keyed in its **given order**, not sorted — a different order misses the memo
rather than risking a wrong hit. Missing the memo costs a query; a wrong hit
would be a semantics change, which the brief forbids.

Two consequences are strengthenings rather than regressions, and are recorded as
such:

* the quarantine map is read **once per render**, so two consoles on one page can
  no longer disagree about a pin made mid-render;
* the org sequence is resolved **once per render**, so a sync landing mid-page can
  no longer give two consoles two different readiness verdicts for one claim.

### a.4 Non-goals

Batching the per-claim reads. Four loops issue one query per claim
(`get_current_approved_claim`, `_read_claim_runs`, `read_grounding_validity`,
`covered_reads_changed`). Fixing those is Step 2a's shape applied again — a
different slice with its own parity proof. It is named in §g, not folded in here.

---

## b. What changed

`read_scope.py` is new. Nine files gained or used a `session=` seam; no console's
query text changed, and no console's return shape changed.

| file | change |
|---|---|
| `semantic/read_scope.py` | new: `read_scope`, `memo`, `in_scope`, `conn_of` |
| `intelligence/substrate_decision.py` | claim evidence memoised; the quarantine read rides the scope |
| `sync/readiness.py` | the org sequence resolved once per scope |
| `core/permissions.py` | attention badge: a `session=` seam and one read per request |
| `intelligence/quarantine.py` | `manual_states` takes a session |
| `intelligence/org_state_console.py` | `org_state_for_request` takes a session |
| `intelligence/requirement_identity_console.py` | `identity_overview` takes a session |
| `intelligence/s3_generation_console.py` | `count_claims_by_requirement_status` takes a session |
| `views.py` | both target views open one scope and hand it to every console |

---

## c. Two defects found in this slice's own code

**c.1 The scope masked the render's exception.** The first shape wrapped the
whole `yield` in `try/except Exception: yield None`. A render that raised had its
exception thrown into the generator, caught, and answered with a second `yield` —
a `RuntimeError` in place of the real failure. Acquisition and render are now
separated: only the acquisition is best-effort. Guarded by
`test_a_failing_render_propagates_and_still_closes`.

**c.2 The memo believed a test double.** `in_scope` tested for the marker
attribute being non-`None`. A `MagicMock` answers every `getattr`, so four
sequence-resolver tests received the mock's attribute instead of a read. The
memo now requires a real `dict`. Guarded by `test_a_test_double_is_not_a_scope`.
Both were caught by tests, not by inspection.

**c.3 The stack was bound inside the try.** `_read = _ExitStack()` sat in the
body while `_read.close()` sat in the `finally`. Any earlier failure made the
`finally` raise `UnboundLocalError`, **masking the real error** — a scratch page
reported a name error instead of the missing table underneath it. Both views now
bind the stack before the `try`, and a guard reads the AST to keep it there.

---

## d. Sweeps first (D-490)

Run BEFORE any page was rendered or believed:

| sweep | result |
|---|---|
| dict-method template names (D-487) | pass |
| dead-link / route hygiene | pass |
| navigation (6a + 6b) | pass |
| no gate logic in templates | pass |

47 passed.

---

## e. Suites

| suite | result |
|---|---|
| unit | **5,125 passed** |
| `tests/integration/test_representation` | **442 passed**, 4 skipped |
| DB-real corpus (24 suites on clean scratch) | **104 passed**, 49 skipped, **1 failed** |
| page suites (`REPORT_PAGES=1`) | **46 passed**, 1 skipped |

The single red is `test_report_slice::test_a_runs_list_carries_both_recorded_runs`
— the window artefact ledgered since D-488. **Proven pre-existing, not asserted:**
the same test fails identically on `main` @9a224e0 in a worktree.

The whole of `tests/integration` was also run on both trees and the failure sets
compared: **every test red on the branch is red on `main`**, and none is red only
on the branch. Those suites need a live database and a running app, which this
environment does not provide.

**D-469's obligation** — this slice adds one keyword-only parameter to ten
readers and passes it from two views, which is precisely the shape D-469 warns
about. The guard is mechanical, not prose: `test_close_2_call_sites.py` reads the
real signatures with `inspect` and the real call sites out of `views.py` with the
AST, and fails if a console is called without the render's session or if either
view moves its stack back inside the `try`.

New guards: 4 files, 38 tests.

---

## f. The production before/after

Both trees run **in-process against production data** under the read-only
connection option, so `main` and the branch are measured by one harness.

**The read-only guard was proven, not accepted on its status report** (the
standing rule): `CREATE TABLE`, `INSERT` and `UPDATE` each raised
`ReadOnlySqlTransaction`; `SELECT` returned 23 releases. GET only throughout.

### f.1 Cost

| surface | queries before | after | connections before | after | seconds before | after |
|---|---:|---:|---:|---:|---:|---:|
| release decision tab | 213 | **156** | 11 | **2** | 138.3 | **97.3** |
| Requirements | 50 | **33** | 6 | **1** | 44.4 | **22.5** |
| releases list | 30 | 30 | 3 | 3 | 25.9 | 25.2 |
| runs list | 28 | 28 | 4 | 4 | 28.8 | 29.3 |

The two untargeted pages are unchanged on every axis — that is the control.

**On the seconds column.** These captures run from a laptop through the public
proxy, where one round trip costs roughly 600 ms, so wall time here tracks the
query count almost exactly and is not production's own latency. The durable
numbers are the query and connection counts.

The decision tab's remaining connections are the render's own scope and the
sidebar badge; Requirements is down to **one connection for the whole page**.

### f.2 Parity — the diff is silent

Four surfaces captured on both trees, normalised, hashed.

The volatile set was established **empirically, not assumed**: `main` was
captured three times, and the fields that differed between runs of the same code
are the only fields normalised — the per-request CSRF token, the live-preview
timestamp, and relative times ("20h ago" became "21h ago" as an hour boundary
passed mid-capture, and it moved in **opposite** directions across before and
after, which is what proves it is elapsed time rather than code).

| surface | main (3 runs) | branch | verdict |
|---|---|---|---|
| decision tab | `d48a0df08ed08351` | `d48a0df08ed08351` | identical |
| releases list | `a70f8f831972006e` | `a70f8f831972006e` | identical |
| Requirements | `65072b581b690cdc` | `65072b581b690cdc` | identical |
| runs list | `7c103ac22922bf8b` | `7c103ac22922bf8b` | identical |

`diff` is silent on all four. Nothing any console returns has changed.

---

## g. The named next slice, and the ledger

**Next slice — the per-claim loops.** The decision tab's remaining 156 queries
are dominated by four reads issued once per claim:

| read | queries |
|---|---:|
| `coordinator.get_current_approved_claim` | 23 |
| `s4_execution_console._read_claim_runs` | 23 |
| `result_store.read_grounding_validity` | 19 |
| `readiness.covered_reads_changed` | 19 |

84 of 156 in four loops, over 23 claims. This is Step 2a's shape exactly, and it
wants Step 2a's treatment: one set-based read per loop, with element-for-element
parity as the acceptance test. It is a slice, not a ledger item.

**Ledgered.** The sidebar's attention badge is one tenant connection on every
page. It cannot join a view's scope because the context processor runs outside
the view, and giving it an ambient scope was refused by design (§a.2 limit 2).
The Requirements page avoids it only because the view reads the badge inside its
own scope first. The honest fix is for the badge to become part of a page's
declared reads rather than a template-time side effect.

**Ledgered.** The releases list (3 connections) and the runs list (4) were not
targeted and are untouched. The same seams would close them.
