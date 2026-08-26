# VERIFICATION Phase 7 — Detection + Causal Attribution

Executed 2026-08-26 on the scratch DB `plimsol_3a3` (tenant_1 at
`20260826_0010`) plus one LIVE org-environment capture against env-59
(org API real, persistence scratch-only; prod DB untouched).
Re-runnable: unit = `tests/unit/test_representation/
test_phase7_comparison.py`; DB-real = `tests/integration/
test_phase7_comparison.py` gated on `S3A3_TEST_DATABASE_URL`.

## What landed

- Migration `20260826_0010`: `org_environment_snapshots` (immutable,
  hash-keyed), `s6_ui_comparison_runs` (UNIQUE per job pair),
  `s6_ui_verdict_transitions` (per-claim taxonomy + causal JSONB).
- `sf_client.fetch_org_environment()` — platform api version
  (`GET /services/data`), Organization row, InstalledSubscriberPackage
  set (Tooling, completeness-gated: a partial package set would make an
  ENVIRONMENT delta lie by omission).
- `ui_manifest.capture_org_environment_snapshot` + the builder's
  `sf_client` / `org_env_snapshot_id` params; the pin block gains
  `org_env_snapshot_id` and the restored `worker_image_digest`
  (env-provided; None is an honest "not recorded" and None→value counts
  as a moved tool dimension).
- `interpretation/ui_comparison.py` — the DE-18 walk, the transition
  taxonomy, the CONDITIONAL rung 4, DE-13 ranking, immutable
  persistence, `list_transitions`.

## One design-semantics correction found by the tests (flagged)

The first implementation evaluated the CLIENT dimension per claim (via
each claim's own `owner_bundle_ref`), which left sibling claims on the
same surface NOT_COMPARABLE after a structural client change. The GO's
wording is **surface-scoped** ("owning-bundle version change … for that
claim's surface") — corrected: a bundle observed on the surface (either
run's verdict refs) that changed in the window moves the dimension for
EVERY claim on that surface; a claim without its own ref inherits the
surface's bundle evidence marked `"scope": "surface"`.

## The acceptance world (planted-row technique, 3A-4/3A-5 posture)

One claim_set (release 2 = 72 AUTO rules × 1 surface), five jobs with
exactly ONE planted delta per arm; every job processed through the real
3A-4 processor; comparisons through the real comparator.

## Arm D — CLIENT, structural (proving the amendment)

Between runs A→B: the page changed STRUCTURALLY (fingerprint delta,
`named_added [button Buy]`) AND the owning bundle gained a new S1
version in the window AND an image-alt FAIL landed on a `c-*` node
resolving to it. Result — green:
- the image-alt claim: **NEW_FAIL, primary CLIENT_BUNDLE, the bundle
  NAMED, source-hash pair in evidence, the fingerprint delta IN the
  causal evidence, confidence HIGH** (only the bundle moved);
- **NOT_COMPARABLE count 0** — the structural delta classified instead
  of refusing (the conditional rung 4 doing exactly what the amendment
  says); the 71 sibling claims are STILL_PASSING.

## Arm C at verdict level — unchanged behavior

Runs B→C: the page changed again with NO planted dimension → all 72
pairs **NOT_COMPARABLE (`state_changed_unexplained`)** with the
structural delta attached (named added/removed verbatim); zero
transitions minted.

## Arm E — ENVIRONMENT

Runs C→D: a planted package version delta in the snapshot; a new
plain-node label FAIL → **NEW_FAIL, primary ENVIRONMENT_PACKAGE
naming the package, confidence HIGH**; `env_delta.packages.
version_changed` carries the from/to version ids.

## Arm F — TOOL, subtracted

Runs D→E: a planted `axe_sha256` pin delta (sha-only — the version
stays pinned so bindings still resolve); a third FAIL → **NEW_FAIL with
`drift=true`, primary TOOL; the counts ledger reads `NEW_FAIL_drift: 1`
and the regression headline `NEW_FAIL: 0`** — drift subtracted, never
mixed.

## Refusal + idempotence

- Cross-inventory pair → **outcome `refused`**, the reason naming both
  inventory versions and "DECLARED act".
- Re-compare of (A, B): the SAME comparison-run id, transition rows
  byte-identical (UNIQUE-pair UPSERT).

## Live addendum — env-59 org-environment capture

```
live env-59 snapshot 419b3911…: platform_api=67.0
  org={is_sandbox: True, instance_name: CS321,
       organization_type: Enterprise Edition}
  packages=6  hash=e272ecb65fad…
second capture: SAME id (hash-reuse idempotent)
```

The capture path is real end-to-end (prod connection credentials, the
daily-sync path); six installed packages recorded; identical content
reused the row.

## Suites

- Unit: **4,879 passed** (was 4,872; +7 Phase 7).
- DB-real: **17 passed** across all four suites (3A-3, 3A-4, 3A-5,
  Phase 7) — no regression from the pin additions.
- Worker untouched: zero files under `primeqa/browser_worker`; the
  boundary guards stay green.
- One implementation-detail note: the comparison window's bounds are
  the jobs' `enqueued_at` (the jobs table has no `created_at` column).
