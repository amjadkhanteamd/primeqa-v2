# VERIFICATION — Phase 5 Part 3: the profile view + refusals beside NOT_COVERED

Executed 2026-09-03 on scratch (`plimsol_3a3`, tenant chain upgraded
`20260902_0010 → 20260903_0010`). **No production row was written and
no Railway act was performed.** Branch `phase-5-part3` (from merged
main @`9f65cc6`). Merge gated.

Re-runnable: `tests/integration/test_phase5_profile.py` (scratch).

---

## Design (LLD-lite — a read-surface slice over recorded data)

This slice adds **no verdict semantics, no grammar change, no worker
delta**. It renders two things Part 2 already records:

1. **`CUSTOM:<profile>` through `standard_view`.** The tenant declares
   its profile as a standard-like set: `cust_profile_sets` (the
   lifecycle object per `(profile_key, revision)`) +
   `cust_profile_criteria` (the customer's guideline HEADINGS, verbatim
   — the denominator). The set rides the same discipline as every set
   in this programme: DRAFT-gated authoring, content frozen from
   REVIEW, one content hash at APPROVED (over the ordered headings),
   single-ACTIVE per profile key enforced by a partial unique index,
   real-actor ratification, `activity_log` on every transition. A rule
   maps a heading through its OWN ratified content
   (`definition.criterion.profile`), so the render joins two RECORDED
   objects and derives nothing (the D-281 posture). An ACTIVE rule
   whose heading the ratified set does not carry is an **orphan rule**
   — surfaced in the view, never dropped and never counted. An unknown
   profile REFUSES rather than rendering emptily.
2. **Refusals beside NOT_COVERED.** Every view — the three public
   standards and every profile — gains a `refusals` block: the
   ledgered refused guidelines, each with its class, the reason in the
   customer's terms, the nearest expressible partial, and its
   timestamp. "What we cannot test for you, and why" is a first-class
   list next to the uncovered criteria (LLD §f: refusal is a feature).
   A schema without the ledger (pre-Part-2) says `available: false`
   rather than pretending an empty list means zero refusals.

Storage: one tenant migration (`20260903_0010`, two tables). Rejected
alternative — widening the public `s5_standard_map_sets` CHECK to admit
profile names — because tenant data lives in the tenant schema (the AK
directive) and profile names must not collide across tenants.

## a. The profile lifecycle (scratch transcript)

- `create_profile_set('acme')` → DRAFT; **an empty set refuses
  APPROVED**; authoring after REVIEW refuses (`requires the SET in
  DRAFT`).
- Revision 2 carries the three headings (`Brand/Targets`,
  `Brand/Labels`, `Brand/Contrast`) → REVIEW → APPROVED
  (`reviewed_by=1`, content hash frozen) → ACTIVE.
- **Single-ACTIVE per key is a DB guarantee**: a second ACTIVE row for
  `acme` is refused by `cust_profile_sets_single_active`.

## b. `CUSTOM:acme` over B-1 (job `471a9c35`)

- Header: `standard: CUSTOM:acme`, `standard_version: profile revision
  2`, profile set id + content hash,
  `denominator_provenance: ratified_profile`,
  `denominator_complete: true` — and the RUN header still rides
  (engine, run-set size 74, release 3): a profile claim is always
  readable as "under THIS engine run".
- Denominator **3**; `Brand/Targets` **AUTOMATED** (contributed by
  PLM-CUST-00001's own ratified content); `Brand/Labels` and
  `Brand/Contrast` **NOT_COVERED**.
- Roll-up over B-1: `criterion_verdict: null` for Brand/Targets —
  **covered-but-undetermined, honestly**: B-1 predates every custom
  rule, so no custom verdict exists and none is invented. The first
  post-Part-2 run decides it.
- Orphan surfacing proven: the view's `orphan_rules` count equals the
  measured count of ACTIVE rules whose heading is unratified;
  `CUSTOM:ghost` refuses with `no ACTIVE profile set`.

## c. Refusals beside NOT_COVERED

On **both** `CUSTOM:acme` and `WCAG22` views: `refusals.available:
true`, the **token-vs-literal refusal visible verbatim**
(`needs_capability_not_captured`, "computed style is post-resolution:
token and hex are byte-identical in the observation") with its
`nearest_expressible` palette-membership partial attached. The note
states the contract: surfaced beside NOT_COVERED, never silence.

## d. Suites (D-468)

- **Unit: 4,961 passed** (no unit surface changed; the Phase 4/5 view
  tests confirm the public path is untouched by the branch).
- **DB-real: 50 passed** across all ten suites (+4 profile). The two
  skips are the Part 2/Part 3 idempotent-replay guards; both tests
  passed on their first runs and the scratch state is their evidence.
- **Browser-gated: 63 passed, 11 skipped** (no worker delta — run for
  D-468).

## Residual, stated plainly

- The refusal list is tenant-global on every view (a refusal is not
  per-standard data); if per-criterion refusal linkage is ever wanted,
  the ledger's `guideline_thread_id` is the join key — a report-layer
  concern, deliberately not schema.
- No UI: data layer + view function only, as the whole phase.
- MIGRATE-FIRST at merge: tenant `20260903_0010` only (no public act).
