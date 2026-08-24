# UI spike scoreboard — COMPLETE (2026-08-23)

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
- **G — credential failure + first authenticated scan (LIVE PROVEN,
  2026-08-23)**: against the Experience Cloud portal of the fresh Developer
  Edition org orgfarm-4399654d2d-dev-ed (NOT env-59 — it has no Experience
  Cloud licences; that is why the DE org exists) — (a)/(a2)
  guest determinism (fingerprint `aecaf4a46fa46481` twice); (b) the FIRST
  authenticated Salesforce scan — ONE login (TOTP), both surfaces
  REFERENCED; LOCK proven (guest `aecaf4a46fa46481` != authenticated
  `41ad9361541974ad`, plus the direct guest 302->/s/login/ redirect);
  (G-1) wrong password -> BAD_CREDENTIAL, zero rows; (G-2) wrong seed ->
  MFA_FAILED, zero rows. SINGLE-ATTEMPT is code-guaranteed: every
  credential-rejection class is permanent (retry == resubmission), so a
  rejected credential is never resubmitted. DB hygiene clean (no password,
  seed, or username in any row). Hardened through 4 adversarial-review
  rounds; CSP-safe axe injection + login->MFA transition-race confirm +
  wait-for-form initial classify were the live-only fixes.

## Spike status: COMPLETE

All ten arms accounted for:

- **PROVEN**: A (determinism), B (kill/reclaim/zero-dup), C (world-change
  honesty), G (credential failure + first authenticated scan + lock),
  I (tenant denial), J (poison cap + evidence-never-falsely-complete).
- **Deferred by design**: H (locator NOT-DETERMINED) → Phase 3A;
  D/E/F (causal candidates) → Phase 7 (need release history).

## Arms pending

- (none for the spike — H and D/E/F are out-of-phase by design, above.)

## Known-open items (session substrate)

- **Tabset late-load nondeterminism**: the `?tabset-398be=2` surface renders
  identically to the base surface under the current settle (its tab content
  loads after structural-quiet). Cross-run differences there are DE-18
  NOT_COMPARABLE by design, not a stabilisation fault; a tab-content-ready
  wait is future work.
- **Nav flakiness (~1/3 on the dev org)**: raw `domcontentloaded` on the DE portal org
  succeeds ~2/3 of the time (8-15s) and otherwise exceeds 20s. Absorbed by
  `PAGE_NOT_REACHED`-retryable (pre-submit, never resubmits a credential) —
  validated live (a failed nav re-claimed and succeeded on a later consume).

## FIX-1 status

Open. Fresh tenant-chain provisioning crashes at revision
`20260817_0010` (autocommit_block vs `alembic/env.py:113` transaction
ownership) — blocks fresh tenant provisioning in production. Local
workaround documented (manual `ALTER TYPE` + stamp); triaged on `main`
in `docs/reviews/PLIMSOL_FIX_PLAN.md` (FIX-1). Needs its own diagnosis
pass; not to be patched inside unrelated work.
