# Gate 2 Return Package — UI Programme Phase 2 Spike Results
**Date:** 2026-08-23 · **For:** TA sign-off of Gate 2 (conditional approval → full)
**Branch:** `phase-ui-s2-spike` @ `62734e7`, pushed, unmerged. All transcripts live in `docs/ui-testing/` (five LLDs + SPIKE_SCOREBOARD.md); commit history is the audit trail.

---

## 1. The closing question, answered

You conditioned Gate 2 on: *"Can Plimsol execute the same declared surface under the same manifest, reproduce the same result, preserve complete evidence, survive worker failure, correctly distinguish an application change from an environment/tool change, and do all of that without leaking another customer's session or data?"*

| Property | Status | Proof |
|---|---|---|
| Same manifest → same result | **PROVEN** | Arm A ×2 cycles, byte-identical fingerprints, fixtures; guest fingerprint also byte-identical across live runs on the real portal login page |
| Survive worker failure | **PROVEN** | Arm B: SIGKILL mid-batch → heartbeat-lease reclaim → retry → exactly N results, zero duplicates (DB-constraint-enforced) |
| App change vs world change | **PROVEN at spike scope** | Arm C: content mutated outside the manifest → NOT_COMPARABLE with the structural delta named (`named_removed: [["textbox","Email address"]]`), never a phantom regression. Tool/env/client *causal* discrimination (D/E/F) needs release history → Phase 7 by design |
| Complete evidence, never falsely complete | **PROVEN** | Arm J: interrupted upload → EVIDENCE_INCOMPLETE; REFERENCED requires keys+checksums+sizes+verified_at **as a DB CHECK** |
| No cross-tenant leakage | **PROVEN** | Arm I: invisibility both directions across two schemas; deny+audit on all seven key-accepting surfaces; one live cross-tenant hole (CLI sweep) found by the enumeration and closed |
| Credential safety | **PROVEN, live** | Arm G: BAD_CREDENTIAL and MFA_FAILED, single-attempt **code-guaranteed** (all credential-rejection classes permanent; retry reserved for pre-submit navigation), zero result rows, DB hygiene scan clean |

Plus the milestone the spike existed to reach: **the first authenticated Salesforce Experience Cloud scan** — one login, TOTP, session reused across the batch, both surfaces scanned and evidenced, site lock proven twice (302→login + guest/auth fingerprint divergence).

## 2. Your P0s, closed

Round 1: (1) lease+idempotent completion — built and kill-tested; (2) page/test-state context — fingerprint slice live, NOT_COMPARABLE demonstrated; (3) Run Manifest — immutable, early-committed (lifted from the in-repo D-281 idiom), retry = same manifest; (4) session boundary — one job = run × persona × surface set, batch decomposition designed for sharding without manifest change; (5) production feasibility — see §4, partially closed by design.

Round 2: (1) engine-observation vs verdict — enforced in code; the word "verdict" is absent from every worker module, tested; (2) two-part determinism — proven exactly as reformulated (fixture-same + changed-context-NOT_COMPARABLE); (3) causal assessment model — designed, implementation deferred to Phase 7 with release history, as you allowed; (4) execution batch — designed, R1 default one-batch-per-persona operating.

## 3. The model reality falsified — and the correction

The fixture-derived stabilisation ladder (load → networkidle → 500ms DOM-quiet) **cannot converge on live Aura**: the `load` event may never fire (persistent connections) and the DOM never goes mutation-silent (0ms longest quiet gap over 12s). First real Salesforce page falsified it.

Correction, designed then implemented: navigation on `domcontentloaded` with bounded retry; **structural-quiet** as the convergence gate (only node add/remove and name-affecting attribute changes count); networkidle removed; determinism restated as **fingerprint equality across runs** — observation determinism, not page silence. Three further live-only findings fixed the same way (CSP-safe engine injection via main-world evaluate; wait-for-form initial classify; login→MFA transition-race confirm settle). Four adversarial-review rounds hardened the credential path before any live attempt; the MFA_FAILED-retryable resubmission bug was caught **before** it could lock an account.

We regard the falsification as the spike's chief product: the model that survived contact is the one Phase 3A builds on.

## 4. Honest open items (nothing here is hidden in the green)

1. **Production-architecture authenticated run not yet performed.** The pinned stack (playwright 1.62.0 / chromium 151, amd64) was verified in production at step 2.1; the authenticated login has run only on the documented local substitute (1.54.0/macOS). The production form waits on the vault work that gives credentials a legitimate home (PORTAL_FERNET_KEY is declared in the role gate but not provisioned). This is the remaining slice of your P0-5.
2. **Egress IP rotates on the current Railway plan.** Observed changing across redeploys; static IPs are a Pro-plan toggle (decision D9: exemption-first client posture now, Pro upgrade before the first allowlisting conversation).
3. **Tabset surface not yet deterministic** — late-loading tab content; DE-18-class behaviour; a tab-ready wait is named future work.
4. **Navigation flakiness ~1/3 on the dev org**, absorbed by PAGE_NOT_REACHED-retryable without credential spend — by design, but the rate is real.
5. **FIX-1** (fresh tenant-chain provisioning crash, main): open, diagnosis pass in flight; blocks new-tenant provisioning, unrelated to the spike.
6. **Spike tables exist only locally**; production migration happens at merge, per D-285 ordering.
7. **Audit event → activity_log wiring** is named core-layer integration, outside the spike's no-core-imports boundary.

## 5. Security posture delivered

Role-aware boot gate (least privilege: browser-worker validates PORTAL_FERNET_KEY only — cannot mint tokens, cannot decrypt org credentials; unset = byte-identical legacy, tested). Tenant boundary structural (session-derived prefixes — callers cannot express a foreign tenant; deny+audit where keys are accepted). Credential-rejection permanence as law. Bearer-token rule for client-tenant evidence URLs. Secrets env-only, redaction tested, DB hygiene scanned clean.

## 6. Measured cost (first entries)

~1.4s trivial page / ~3.5s heavy SPA per scan (launch amortised ~100ms in-container); live Aura pages 8–15s navigation on the dev org; ~70MB peak RSS. Straight-line commercial unit: 1,500 surface executions ≈ 60–90 min sequential single worker, pre-parallelism. Full per-run costing lands with the production entrypoint.

## 7. What signature unlocks

Phase 3A — the substrate build on proven ground: `conformance-claim` registration (LayoutClaimBody composite-key precedent located), the Plimsol rule registry + engine map, the explicit read-only dispatch flag (D6, at the chokepoint whose docstring already asks for it), enumeration + claim_set approval with real actor attribution, and the result processor where engine observations become verdicts — the boundary the spike kept clean end to end. Arm H (locator NOT-DETERMINED) is Phase 3A's own acceptance arm.

**Request: Gate 2 full approval, with §4 items 1–2 carried as conditions on the productionisation step rather than on the substrate build.**
