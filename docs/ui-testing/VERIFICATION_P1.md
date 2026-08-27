# VERIFICATION P-1 — the production authenticated portal run

Executed 2026-08-27 against **production**. This is the run the Phase-2
spike deliberately deferred: an authenticated TOTP scan of the
Experience Cloud DE portal org executed END TO END FROM THE RAILWAY
`browser-worker` SERVICE — real image, real pinned stack, real queue,
real vault decrypt, real evidence to live R2.

Perishable evidence preserved beside this document in
`docs/ui-testing/p1-evidence/`:
`browser-worker-deployment.log.txt` (the service's complete deployment log —
the sole provenance record, see §7) and `guest-control.txt` (the
unauthenticated baseline, regenerated at verification time and otherwise
unreproducible).

---

## 1. Identifiers

| | |
|---|---|
| job | `c70fa8e6-888d-4a6f-9087-f18fd8ef3196` |
| manifest | `8decc64a-2d6c-4cb5-8180-299650633832` |
| claim_set | `c416e9ad-798b-4a22-a3df-97388976f38f` |
| inventory version | 1 (2 surfaces, `persona_scope=customer`) |
| catalogue release | 2 (72 ACTIVE rules, content hash `9b21e667…`) |
| deployment | `45358186…` (`ops(p1): browser-worker start command -> consumer loop`), branch main, 1 replica |
| surfaces | `orgfarm-4399654d2d-dev-ed.develop.my.site.com` `/s/` and `/s/?tabset-398be=2` |

## 2. The act chain, with attribution

1. **Declared** — inventory v1, two surfaces, both `auth_required`, each
   linked to its S1 `Surface` entity (3A-5) at declaration.
2. **Enumerated** — release 2 × inventory v1 × persona `customer` →
   **144 claims + 144 ui-inspection recipes** (72 rules × 2 surfaces),
   all `APPLICABLE` + executable.
3. **Approved** — ONE human act by user 1: 144 claims and 144 recipes
   promoted; **144 attributed provenance events** carrying
   `user_id=1` + `claim_set_id` with `event_actor='human'` (D-ε-1
   intact); one `s2.claim_set.approve` activity row (`member_count=144`).
4. **Enqueued** — through `enqueue_ui_run`:
   `allow: role 'superadmin' (tier SUPERADMIN) >= MEMBER`; `ui.run_enqueued`
   audited with the real actor. Manifest pins: axe 4.13.0 /
   `c24f097bd2f4…` (hash-asserted against the vendored engine),
   playwright 1.62.0, catalogue release 2 / `9b21e667…`,
   `org_env_snapshot_id: null`, `worker_image_digest: null`.
   Auth descriptor: `{"mode": "vault", "persona": "customer"}` — a
   descriptor, never a value.

## 3. The run (from the service's own log)

```
2026-08-27T15:52:33Z  EGRESS_IP=152.55.185.115
2026-08-27T15:52:33Z  browser-worker consumer starting (role=browser-worker)
                      [14 x "tenant N is active but has no provisioned schema — skipped (warned once per process)"]
2026-08-27T16:28:44Z  job c70fa8e6-… attempt=1 surfaces=2 auth=vault
2026-08-27T16:29:04Z    login events: ['LOGIN_SUBMITTED', 'MFA_SUBMITTED']
2026-08-27T16:29:04Z    surface …|/s/?tabset-398be=2|customer|-|- -> OK evidence=REFERENCED
2026-08-27T16:29:10Z    surface …|/s|customer|-|- -> OK evidence=REFERENCED
2026-08-27T16:29:10Z  job c70fa8e6-… succeeded
```

**One login for the two-surface batch**, proven three ways:
exactly one `LOGIN_SUBMITTED` in the complete deployment log; `attempts=1`,
`reaps=0`, both result rows `attempt=1`; and — data-side — **neither
observation records a `launch` timing phase**, which `scan_page` emits
only when it creates its own browser, so both surfaces reused the single
authenticated context.

*Scope of that claim (recorded honestly):* it holds **per job attempt**.
`SESSION_LOST` is retryable, so a job that failed mid-batch and was
re-claimed would log in again. Nothing of the sort occurred here.

## 4. Provenance — the Chromium-151 discriminator

Both observations record `browser_version = 151.0.7922.34`. This
laptop's entire Playwright cache holds only Chromium `139.0.7258.5` and
`145.0.7632.6` (venv playwright 1.54.0 against the repo pin 1.62.0).
**No local environment could have produced these rows.** Corroborating:
`PORTAL_FERNET_KEY` exists on the `browser-worker` service alone — on no
other service, in no local file, in the single Railway environment — and
the job's `{"mode":"vault"}` descriptor routes credential resolution
through that key. `claim_one` is an atomic `UPDATE … FOR UPDATE SKIP
LOCKED` that increments `attempts`; the service printed `attempt=1` and
the row still reads `attempts=1`, so the service was the sole claimant
and sole writer.

## 5. Authentication — the guest-control contrast

| | guest (control) | the run (authenticated) |
|---|---|---|
| `/s/` response | 1,406-byte redirect shell containing `/s/login?ec=302&startURL=%2Fs%2F` | full portal page |
| axe passes | 27 | **46** (`/s`), **45** (tabset) |
| elements | 15 | 54 / 60 |
| screenshot | — | 23,800 B / 30,628 B |

A login-redirect stub cannot produce 46 axe pass evaluations. The site
lock is intact and the scan saw authenticated content.

**Not proven by the FAILs:** the guest login page also produces a
`region` violation, so the two FAILs are not themselves evidence of
authentication. The fingerprint, element counts, pass counts and
screenshots carry that claim.

## 6. Evidence

Both surfaces `REFERENCED`, each with 2 keys + 2 checksums + 2 sizes and
a `verified_at` stamp; keys tenant-prefixed from the session context
(DE-19), e.g.

```
tenant_1/8decc64a-…/c70fa8e6-…/orgfarm-…|/s|customer|-|-/1/screenshot.png
tenant_1/8decc64a-…/c70fa8e6-…/orgfarm-…|/s|customer|-|-/1/observation.json
```

All four objects were independently re-verified in R2 after the run:
they exist, and their real sizes and sha256 digests match the recorded
values.

## 7. Secrets — zero, scanned both channels

**Database** (manifests, job payload + `error_text`, result observations
+ `evidence_detail`, verdicts, `activity_log` details): 0 Fernet
ciphertext (`gAAAAA`), 0 credential-shaped keys
(`password|passwd|totp|otpauth|secret|fernet`), 0 email-shaped strings.

**Service log**: 0 of every shape scanned — Fernet ciphertext,
email-shaped, base32 runs ≥16, 6-digit codes, credential words, and any
`Credentials(` repr. The only authentication trace in the log is the
event NAMES `['LOGIN_SUBMITTED','MFA_SUBMITTED']`.

Zero `ui.login_failed` and zero `ui.tenant_boundary_refused` rows exist.

## 8. Egress — the P-2/D9 data point

**`152.55.185.115`**, printed at worker boot. ONE observation.
**Stability across restarts is unmeasured**; P-2/D9 remains a plan
decision and this run contributes its first data point only.

---

## 9. THE MATERIAL CAVEAT — "142 PASS" is NOT 142 verified checks

The processor wrote **144 verdicts: 142 PASS, 2 FAIL**, 0 unmapped
engine ids, 0 members left unjudged. The accounting is sound — 72
members × 2 surfaces, a bijective engine→rule binding (72 → 72, no
fan-out), `UNIQUE(job_id, test_id)`, no silent drops. **The meaning of
PASS is not.** Independently confirmed against production and the
vendored engine:

**(a) 3 PASS verdicts were engine INCOMPLETE.**

```
/s                    incomplete ids: aria-valid-attr-value
/s/?tabset-398be=2    incomplete ids: aria-valid-attr-value, color-contrast

aria-valid-attr-value -> PLM-A11Y-022 -> verdicts: PASS
color-contrast        -> PLM-A11Y-029 -> verdicts: PASS
```

`decide_verdict`'s AUTO branch filters `violations` only and never
consults `incomplete` (`interpretation/ui_conformance.py`). The engine
said it could not determine; the record says the portal passes. Colour
contrast is a core WCAG AA criterion.

**(b) 12 PASS verdicts are for rules that never executed.**
`spike.py:461` calls `axe.run(document)` with no options, so rules
marked `enabled:false` are skipped entirely. The vendored axe 4.13.0
carries **9** disabled-by-default rules, **6 of them bound** to release-2
rules — every one of their 12 verdicts is PASS:

```
aria-roledescription          -> PLM-A11Y-016  2 x PASS
audio-caption                 -> PLM-A11Y-023  2 x PASS
color-contrast-enhanced       -> PLM-A11Y-030  2 x PASS
identical-links-same-purpose  -> PLM-A11Y-042  2 x PASS
meta-refresh-no-exceptions    -> PLM-A11Y-053  2 x PASS
target-size                   -> PLM-A11Y-064  2 x PASS
   (duplicate-id, duplicate-id-active, landmark-complementary-is-top-level
    are also disabled but unbound, so they produce nothing)
```

These 12 can never be FAIL under this configuration. **PLM-A11Y-064
("All touch targets must be 24px large, or leave sufficient space") is a
normative WCAG 2.2 AA criterion reported as passing without being
tested.**

**(c) ≥51 PASS verdicts cannot correspond to any engine pass, and NO
PASS verdict is individually re-verifiable.** The observation stores
`violations` and `incomplete` in full but only a *count* for `passes`,
and discards `inapplicable` entirely. Arithmetic, with the binding
bijection: `/s` has 71 PASS verdicts against at most 46 axe pass entries
(≥25 unmatched); the tabset surface 71 against at most 45 (≥26). Some of
those are legitimately vacuous (no `<audio>` element on the page) — but
the stored record cannot separate "vacuously inapplicable" from "never
run", and cannot re-verify a single PASS after the fact.

**Honest decomposition of the 144:**

| class | count |
|---|---|
| FAIL — engine-attested, real (`region` → PLM-A11Y-071, one per surface) | 2 |
| PASS that should be NOT_DETERMINED — engine said INCOMPLETE | 3 |
| PASS that should be NOT_DETERMINED — rule never executed | 12 |
| PASS with no possible engine attestation (≥, by arithmetic) | ≥51 |
| remainder — engine-attested clean passes | ≤76 |

The defect is in shipped 3A-4 / spike code, not in this run. A fix slice
is opened (`LLD_VERDICT_SEMANTICS.md`). **P-1's acceptance was the
production PATH, not the conformance result.**

## 10. What P-1 did NOT prove

1. It did not prove the portal is accessible (§9).
2. It did not prove WCAG 2.2 AA coverage — six bound normative rules,
   including `target-size`, were never executed.
3. It did not prove **which** persona logged in — only that someone did.
   The fingerprint captured a session user id; nothing binds it to the
   `customer` persona.
4. It did not prove the landed URL — `observation.url` records the
   *requested* URL; a bounce to `/s/login` would still read `/s/`.
5. It did not prove the boot-time fail-closed secret gate. That gate is
   **inert on this service**: `browser-worker` carries no `FLASK_ENV`, so
   `is_production()` is False and `get_portal_fernet_key()` never raises.
   The key was set, so P-1 is unaffected. (The `totp_env` dev-only
   refusal **is** armed — it gates on `PLIMSOL_SERVICE_ROLE`, proven live
   by `role=browser-worker`.) Recorded in the FIX PLAN.
6. It did not prove `NEEDS_HUMAN` — unreachable here (72/72 rules AUTO);
   it has never fired in production.
7. It did not prove multi-tenant behaviour — one provisioned schema.
8. It did not prove retry, re-login, `SESSION_LOST`, reaper or SIGTERM —
   `attempts=1`, `reaps=0`, no restart, nothing failed.
9. It did not prove concurrency, failure handling, or alerting.
10. It did not prove the vault registration ran inside the worker
    environment — `record_event` stores no host or process.
11. It did not prove evidence-bucket isolation: 24 unreferenced spike/dev
    orphans share the production `tenant_1/` prefix.
12. It did not prove the evidence bytes are cryptographically
    attributable to the browser — `put_evidence` digests the bytes it
    uploads. The Chromium-151 discriminator (§4) carries provenance
    instead.

## 11. Method note

The claims above were subjected to an adversarial verification pass:
five independent lenses (provenance, authenticity, evidence, secrets,
verdicts) instructed to REFUTE rather than confirm, plus a synthesis.
C1–C5 survived; C6 was refuted in part, which is §9. The two
load-bearing refutations in §9 were then re-verified directly against
production and the vendored engine before being recorded here.
