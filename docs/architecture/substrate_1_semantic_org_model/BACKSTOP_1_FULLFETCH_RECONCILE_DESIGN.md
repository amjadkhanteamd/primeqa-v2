# Backstop #1 — full-fetch ValidationRule deletion reconcile (Slice 1)

> **Status:** DESIGN — awaiting AK approval. No code yet. Implements D-251.1
> backstop #1. Proposed decision record: **D-253** (drafted at the end of this
> note; append to `DECISIONS_LOG.md` verbatim on the design-commit GO).
>
> **Scope:** ValidationRule only. RecordType / Profile / PermissionSet are a
> tracked **Slice-2** fast-follow (same shape, once the shared `_query_all`
> hardening is proven). User / Flow deferred (need a redesigned present-set).
> NULL-`sf_id` types (Object, Field, Layout, PicklistValue, the SVS half of
> PicklistValueSet) are **never wired** — the reconcile keys on `sf_id`, so for
> them it is a guaranteed no-op (dead code, zero detection value).

## 1. Why (motivation)

1b.2 (D-251) shipped the ValidationRule deletion reconcile **on the delta path
only**. D-251.1 then found, live on env-59, that the 1b.1 skip gate sits *in
front of* the reconcile: a skip runs no phases, and a deletion whose
SetupAuditTrail entry is *behind* the watermark is never swept. The env-59 probe
`Account.PrimeQADeltaProbe` is deleted in Salesforce yet still currently-valid in
the model (53 active VRs vs 52 truly present), because:

- the last *full* sync ran the full-fetch path, which has **no reconcile**, and
- every sync since has correctly **skipped** (no new setup change since the
  watermark).

D-251.1 named the fix as backstop #1: **run the reconcile on the full-fetch path
too.** It is a cheap, watermark-independent `SELECT Id` + diff, so running it on
any full sync (bootstrap, full-fallback, or a forced sweep) catches deletions the
delta path can't reach, and closes the env-59 stuck-probe class permanently.

## 2. The latent hole this ALSO closes (both paths)

Confirmed by reading the primitive: **`reconcile_deletions_by_sf_id`
([`materialize.py:760-779`](../../../primeqa/sync/materialize.py)) has NO internal
empty guard.** Its only short-circuit is `if not absent_entity_ids: return 0` —
which guards the *nothing-absent* steady state, **not** an empty/partial
`present_sf_ids`. So if `present_sf_ids` comes back empty or a strict subset,
*every* currently-active non-NULL-`sf_id` VR for the org is "absent" and gets
mass-closed (entity + all its edges).

The shipped **delta path** reaches the primitive with
`present_ids = fetch_validation_rule_ids()`, which calls `_query_all` **without**
any completeness gate. Two non-raising vectors therefore exist on the shipped
code today:

- **silent partial** — `_query_all`'s defensive break
  ([`sf_client.py:348-351`](../../../primeqa/integrations/sf_client.py)) returns
  the rows-so-far with no exception on a malformed cursor → strict-subset
  `present_ids` → mass-close of the un-listed VRs. The delta path's existing
  `try/except` ([`phases.py:1216-1223`](../../../primeqa/sync/phases.py)) does
  **not** catch this (nothing raises).
- **spurious empty** — a `0-row, done=True` response (transient permission /
  visibility glitch) → `present_ids == set()` → close ALL active VRs.

Both are low-probability, catastrophic, and **unguarded in shipped 1b.2**. This
slice closes them for **both** paths by routing every reconcile through one
fail-closed gate (§3). That is part of the rationale, not a side effect.

## 3. Design

### 3.1 `present_ids` source — dedicated, gated `SELECT Id`

Both branches derive `present_ids` from the existing dedicated
`fetch_validation_rule_ids()` (cheap bulk `SELECT Id FROM ValidationRule`), **not**
from reuse of the materialize payload `raw_vrs`. Rationale for the locked choice:

- isolates the fail-closed completeness requirement to the reconcile path; the
  materialize fetch (`fetch_validation_rules`) is **untouched** (no behavior change
  to adds/mods).
- makes the two paths symmetric — both call the same id-fetch — so the call site
  fully collapses (§3.3).
- cost is one cheap `SELECT Id` on the full-fetch path; the delta path already
  pays it.

### 3.2 `_query_all` fail-closed hardening (localized)

Add an opt-in parameter:
`_query_all(self, path, soql, *, require_complete: bool = False)`. When
`require_complete=True`, the defensive break
([`sf_client.py:348-351`](../../../primeqa/integrations/sf_client.py)) **raises**
(a typed `SFRequestError`/incomplete-pagination error) instead of silently
returning a partial. `fetch_validation_rule_ids()` is the **only** caller that
passes `require_complete=True`. Every other `_query_all` caller keeps the default
(`False`) → **zero blast radius** on the dozen other fetchers; their
silent-partial behavior is unchanged by this slice.

A raised incomplete-pagination error funnels through the phase's `try/except` to
`present_ids = None` → the reconcile is skipped (full-fetch fail-safe). The
materialize adds/mods still proceed (a full-fetch sync without a reconcile = exactly
today's behavior — strictly no worse).

### 3.3 Single collapsed call site + the empty/partial refusal

Restructure the fetch block in
[`phase_validation_rule`](../../../primeqa/sync/phases.py) so each branch sets a
local `present_ids`:

- **delta branch** (`since is not None`): inside the existing `try`,
  `raw_vrs = fetch_validation_rules(modified_since=since)` **and**
  `present_ids = fetch_validation_rule_ids()`; on `except` →
  `since=None, present_ids=None, raw_vrs = fetch_validation_rules()`.
- **full-fetch branch** (`else`): `raw_vrs = fetch_validation_rules()` for
  materialize; then in its own `try`, `present_ids = fetch_validation_rule_ids()`;
  on `except` → `present_ids = None` (materialize still proceeds).

Then collapse the tail gate (today
[`phases.py:1289`](../../../primeqa/sync/phases.py):
`if since is not None and present_ids is not None:`) to a single shared call:

```python
# Provably-complete present-set ⇒ reconcile. `if present_ids:` is truthy ONLY for
# a non-None, NON-EMPTY set, so it refuses BOTH "couldn't establish completeness"
# (None) AND "empty id-fetch" (set()) — never mass-close on empty/partial.
if present_ids:
    reconcile_deletions_by_sf_id(conn, ctx, "ValidationRule", present_ids, result)
```

`present_ids` truthiness is the single "safe to reconcile" signal carrying both
paths. **Fail-safe = fail-safe-to-NO-reconcile** on every doubt (off-list, no
watermark, resume, fetch error, partial, empty).

> **Empty-refusal corner (accepted):** an org that genuinely deletes its *last*
> VR yields `present_ids == set()` → refuse → that final deletion is not
> reconciled until a VR reappears. Bounded and acceptable: a "every row of a type
> vanished" event is far more likely a glitch than a real state, and the cost of
> refusing is only staleness (the same failure mode we already tolerate), whereas
> the cost of trusting empty is a catastrophic mass-close. The user's "empty
> fetch can never mass-close" requirement makes refuse-on-empty the correct call.

### 3.4 The unfiltered-superset trap (the one subtlety)

`present_ids` MUST come from the **unfiltered** `fetch_validation_rule_ids()`
(every current VR Id, a superset of the modeled in-scope VRs), **never** from the
in-scope-filtered `filtered_vrs`
([`phases.py:1235-1247`](../../../primeqa/sync/phases.py)). A VR whose parent
Object merely dropped out of sync scope between syncs must **not** look deleted.
The dedicated id-fetch is already unfiltered (its docstring notes the
superset-safety), so this is preserved by construction — but it is the single trap
a future maintainer could reintroduce, and test §5.6 pins it.

### 3.5 This reverses D-251

D-251 and the in-code comments at
[`phases.py:1283-1288`](../../../primeqa/sync/phases.py) and
[`materialize.py:753-756`](../../../primeqa/sync/materialize.py) assert
"the full-fetch path does NOT reconcile." This slice **reverses** that — a
deliberate, logged behavior expansion (the full-fetch path gains deletion
detection it never had). D-253 records the reversal; the two comment blocks and the
`reconcile_deletions_by_sf_id` / `fetch_validation_rule_ids` docstrings are updated
in the impl commit to say "both paths, gated on a provably-complete present-set."

## 4. Correctness invariants

1. **Reconcile iff provably complete.** The reconcile fires only when `present_ids`
   is a non-None, non-empty set obtained from a `require_complete=True` id-fetch.
   Any partial/empty/errored fetch → no reconcile.
2. **No behavior change to materialize.** `fetch_validation_rules` (adds/mods) is
   untouched; only the reconcile's id-fetch is completeness-gated.
3. **Unfiltered superset.** `present_ids` is the full current `SELECT Id` set,
   never the scope-filtered list.
4. **Org-scoped close** (unchanged, D-251): the reconcile only supersedes VRs
   sourced from `ctx.connected_org_id`.
5. **Atomic** (unchanged): reconcile runs inside the phase transaction with the
   adds/mods.

## 5. Test plan (faithful, real-DB; mocks only the SF boundary)

Extends `tests/integration/semantic/test_delta_reconcile_live.py` and
`tests/unit/sync/test_delta.py`, plus one `_query_all` unit test.

1. **Full-fetch closes a genuinely-deleted VR + its edges.** Seed a VR (+ edges)
   in the model, absent from the full-fetch id-set; run `phase_validation_rule`
   with `since=None`; assert the VR is superseded and its edges closed, survivors
   untouched. (Mirrors the delta reconcile test on the full-fetch branch.)
2. **Red-on-disable (full-fetch).** Same setup, reconcile disabled → the deleted
   VR stays currently-valid; enabling it flips it. Proves the reconcile is the
   mechanism on the full-fetch path.
3. **Full-fetch partial → REFUSE (load-bearing).** Drive the id-fetch into the
   `require_complete` raise (malformed cursor) → `present_ids=None` → assert
   **zero** supersessions even though modeled VRs are absent from the partial set.
   Proves the completeness gate holds.
4. **Full-fetch empty → REFUSE (catastrophic guard).** Id-fetch returns `set()`
   → `if present_ids:` is false → assert **zero** supersessions (NOT all-N-closed).
5. **Unit: hardened `_query_all`.** With `require_complete=True`, a malformed page
   (`done=False`, no `nextRecordsUrl`) **raises**; a well-formed multi-page walk
   still returns the full aggregate; `require_complete=False` (default) is
   byte-for-byte unchanged (still silently breaks) — proving zero blast radius on
   other callers.
6. **Scope-filter trap.** A present-but-out-of-scope-parent VR (in the unfiltered
   id-set, filtered from materialize) stays **active** — proves `present_ids` uses
   the unfiltered superset, not `filtered_vrs`.
7. **Delta-branch empty/partial → REFUSE (latent-hole fix).** On the *delta* path,
   an empty/partial id-fetch → `present_ids` empty/None → assert **zero**
   supersessions. Proves the shipped delta-path mass-close hole (§2) is closed for
   that path too.

Plus: full `tests/unit/` green; semantic-integration green. No migration (pure
code; deploy-safe — worst case on any failure is "full sync, no reconcile" = today).

## 6. Files (planned for the impl slice — for reference, not built)

- `primeqa/integrations/sf_client.py` — `_query_all` gains `require_complete`;
  `fetch_validation_rule_ids` passes `require_complete=True`.
- `primeqa/sync/phases.py` — `phase_validation_rule` fetch-block restructure +
  collapsed `if present_ids:` call site; comment block updated.
- `primeqa/sync/materialize.py` — docstring update on
  `reconcile_deletions_by_sf_id` (no longer delta-only).
- `docs/architecture/DECISIONS_LOG.md` — append D-253.
- tests as in §5.

**Slice-2 robustness note (not this slice):** when RecordType / Profile /
PermissionSet generalize the call site, move the empty-refusal into the shared
primitive (or a shared wrapper) so every type inherits it without re-remembering
the rule.

## 7. Out of scope / deferred

- **RecordType / Profile / PermissionSet** full-fetch reconcile — Slice 2 (same
  complete-if-gated shape + real `sf_id`; each needs its own gated id-fetch and a
  per-type present-set caveat: RecordType = unfiltered Phase-1 ids; PermissionSet
  = the `Type!='Profile'` set or the raw superset; Profile = ~52K-edge blast radius
  makes its empty/partial guard the most load-bearing).
- **User / Flow** — real `sf_id` but no clean complete present-set (User's is a
  Profile-dependent subset; Flow's `sf_id` is a computed chosen-version Id). Need a
  redesigned present-set; deferred.
- **NULL-`sf_id` types** (Object, Field, Layout, PicklistValue, PicklistValueSet
  SVS half) — never wired (no-op).
- **D-251.1 backstop #2** (periodic forced non-skip sweep) — independent, deferred.

---

## Proposed DECISIONS_LOG entry — append verbatim on GO

```markdown
## D-253 — S1 sync backstop #1: full-fetch ValidationRule deletion reconcile + _query_all fail-closed; reverses D-251's "full-fetch does NOT reconcile"

**Context.** D-251 shipped the VR deletion reconcile on the DELTA path only; the
in-code comments (phases.py, materialize.py) assert "the full-fetch path does NOT
reconcile." D-251.1 then found, live on env-59, that the 1b.1 skip gate defers a
deletion indefinitely when it sits behind the watermark: the probe
`Account.PrimeQADeltaProbe` is deleted in SF yet still currently-valid in the model
(53 active vs 52 present), because the only full sync that ran used the
no-reconcile full-fetch path and every sync since correctly skipped. D-251.1 named
the fix as backstop #1. This decision implements it (ValidationRule only).

**Reverses D-251 (deliberate, logged behavior expansion).** The full-fetch path now
ALSO runs the deletion reconcile. It is a cheap, watermark-independent `SELECT Id` +
diff, so any full sync (bootstrap, full-fallback, forced sweep) catches deletions
the delta path cannot reach — closing the env-59 stuck-probe class. The
"delta-only" comments at phases.py:1283-1288 and materialize.py:753-756 and the
`reconcile_deletions_by_sf_id` / `fetch_validation_rule_ids` docstrings are updated
to "both paths, gated on a provably-complete present-set."

**present_ids source — dedicated, gated `SELECT Id`.** Both paths derive
`present_ids` from `fetch_validation_rule_ids()` (the existing cheap bulk
`SELECT Id`), NOT from reuse of the materialize payload `raw_vrs`. This isolates the
completeness requirement to the reconcile and leaves the materialize fetch
(`fetch_validation_rules`) untouched. `present_ids` MUST be the UNFILTERED superset
(never the in-scope-filtered list), so a VR whose parent Object merely dropped out
of scope is not seen as deleted.

**`_query_all` fail-closed (localized).** `_query_all` gains an opt-in
`require_complete=False`. When True, the silent-partial defensive break (done=False
with no nextRecordsUrl) RAISES instead of returning a truncated list.
`fetch_validation_rule_ids()` is the only caller that passes `require_complete=True`;
every other caller keeps the default → zero blast radius. A raise funnels through the
phase try/except to `present_ids=None` → no reconcile.

**Empty/partial = refuse to reconcile.** The reconcile fires from ONE collapsed call
site, `if present_ids: reconcile_deletions_by_sf_id(...)`. Truthiness refuses BOTH
None (completeness unknown) AND `set()` (empty id-fetch), so an empty or partial
fetch can NEVER mass-close. Accepted corner: an org that deletes its very last VR
yields an empty set and that final deletion is deferred until a VR reappears —
bounded staleness, strictly preferable to a catastrophic mass-close. Fail-safe =
fail-safe-to-NO-reconcile on every doubt.

**Latent delta-path fix (shipped-bug closure).** `reconcile_deletions_by_sf_id` has
NO internal empty guard (its only short-circuit guards the nothing-absent steady
state). The shipped 1b.2 delta path reached it with `fetch_validation_rule_ids()`
ungated, so a silent-partial or spurious-empty id-fetch would have mass-closed live
VRs — unguarded in shipped code. Routing BOTH paths through the same
`require_complete` id-fetch + `if present_ids:` gate closes that hole for the delta
path too, not only the new full-fetch path.

**Verification.** Faithful real-DB (mocks only the SF boundary): (1) full-fetch
closes a genuinely-deleted VR + its edges, survivors untouched; (2) red-on-disable
(full-fetch); (3) full-fetch PARTIAL → refuse (require_complete raise →
present_ids=None → 0 supersessions); (4) full-fetch EMPTY → refuse (0, not all-N);
(5) unit: hardened `_query_all` raises on a malformed cursor under
require_complete=True, default-False unchanged; (6) scope-filter trap: a
present-but-out-of-scope-parent VR stays active (unfiltered superset); (7)
delta-branch empty/partial → refuse (latent-hole fix). Full `tests/unit/` green;
semantic-integration green. No migration — pure code, deploy-safe (worst case = full
sync, no reconcile = today's behavior).

**Scope + follow-on.** ValidationRule only. RecordType / Profile / PermissionSet are
a tracked Slice-2 fast-follow (same complete-if-gated + real-`sf_id` shape, once the
`_query_all` hardening is proven; each needs its own gated id-fetch + per-type
present-set caveat — RecordType unfiltered Phase-1 ids, PermissionSet the
`Type!='Profile'` set, Profile's ~52K-edge blast radius makes its guard most
load-bearing). User / Flow deferred (no clean complete present-set — User's is a
Profile-dependent subset, Flow's `sf_id` is a computed chosen-version Id).
NULL-`sf_id` types (Object, Field, Layout, PicklistValue, PicklistValueSet SVS half)
are never wired — the reconcile keys on `sf_id`, so for them it is a guaranteed
no-op. D-251.1 backstop #2 (periodic forced non-skip sweep) remains a separate
deferred item. Slice-2 robustness: move the empty-refusal into the shared primitive
when the call site generalizes.
```
