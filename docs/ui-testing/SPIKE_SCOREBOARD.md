# UI spike scoreboard — after Phase 2.5 (2026-08-23)

Branch: `phase-ui-s2-spike`. Companion to
`LLD_PHASE2_3_QUEUE.md` + `LLD_PHASE2_4_MANIFEST.md`.

## Arms proven

- **A — determinism**: ×2 full cycles, two jobs per manifest each;
  every surface SAME, byte-identical fingerprints per surface (and
  across cycles), identical observation counts. Kill criteria (any
  DIFFERS → stop) never triggered.
- **B — kill/reclaim**: SIGKILL mid-batch → dead lease detected by
  heartbeat, reap to pending (claim-only attempts charging, `reaps`
  counts the death), retry to completion, zero duplicate results —
  duplicates impossible by the `(job_id, surface_key)` constraint.
- **C — world-change honesty**: content mutated behind a fixed manifest
  → the touched surface labels NOT_COMPARABLE with a structural delta
  naming the change (element count, role counts, removed accessible
  name); the untouched surface stays SAME. Never a phantom regression.
- **J-partial — poison cap**: a job at max attempts with a stale
  heartbeat reaps to failed_permanent, never back to pending (closes
  the enrichment reaper's poison-killer gap).
- **J-completion — evidence never falsely complete (2.5)**: interrupted
  upload (unroutable endpoint) → every surface EVIDENCE_INCOMPLETE
  (reached=CAPTURED), zero REFERENCED, batch still succeeds; REFERENCED is
  DB-guarded to require keys+checksums+sizes+verified_at. Proven against
  the live R2 bucket. Orphan sweep reports the crash-window objects.
- **Session substrate (unit-only)**: TOTP login, batch session reuse,
  six-class credential taxonomy, redaction — 25/25 unit tests. Arm G
  (live credential failure) + the first authenticated scan remain
  UNPROVEN until the a–e portal runs (blocked on portal.env).

## Arms pending

- **G — live credential failure + first authenticated scan** → session
  substrate a–e runs; blocked on AK's portal.env confirmation (arm G is
  UNPROVEN at runtime; unit-level only so far).
- **H — locator NOT-DETERMINED** → Phase 3A.
- **I — tenant denial** → 2.6.
- **D/E/F — causal candidates** → Phase 7; need release history.

## FIX-1 status

Open. Fresh tenant-chain provisioning crashes at revision
`20260817_0010` (autocommit_block vs `alembic/env.py:113` transaction
ownership) — blocks fresh tenant provisioning in production. Local
workaround documented (manual `ALTER TYPE` + stamp); triaged on `main`
in `docs/reviews/PLIMSOL_FIX_PLAN.md` (FIX-1). Needs its own diagnosis
pass; not to be patched inside unrelated work.
