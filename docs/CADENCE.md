# Working cadence — the standing rule

> Ratified 2026-06-20 (AK). This is the canonical collaboration model for Claude
> Code on this repo. It replaces the ad-hoc five-HOLD cycle with a tiered model:
> **Tier 0 free · Tier 1 one GO at push · Tier 2 one GO + a watched verify.**
> The always-on rigor below is never relaxed, at any tier.

## Always-on rigor (every tier — CC's own discipline, never traded away)

- **Faithful tests on the real path.** Tests exercise the actual code path and mock
  only the true external boundary (the SF API, the LLM). No test that passes by
  testing a stub of the thing under test.
- **Red-proofs on every safety-critical guard.** Any guard whose failure is
  catastrophic (mass-close, mass-delete, a fail-open) is proven by temporarily
  breaking it, observing the test go red, and reverting — so the guard is shown
  load-bearing, not asserted.
- **Root cause only — no workarounds.** Diagnose the real mechanism; fix the cause.
  If the real fix is large, stop and say so rather than masking the symptom.
- **Runtime claims need a run.** "It passes / it fires / it returns X" is only ever
  reported from an observed run, never from expectation.
- **Code is ground truth.** If a doc, comment, spec, or ledger entry conflicts with
  the code, the code wins — surface the discrepancy, don't reinterpret to fit.
  Mark **verified vs assumed**; never present an inference as confirmed.
- **Commit hygiene.** Author `AK <amjad.khan@teamd.co.in>`; **zero `Co-Authored-By`**;
  never `--no-verify`, never force-push; explicit, minimal staging (only the files
  the change owns; never the never-commit scratch/report files). `DECISIONS_LOG.md`
  is append-only.
- **Stop-and-flag — don't force.** Halt and report (with the verification that
  proves it, plus options + a lean) on any of: **disproportionate setup** (the work
  is a build/campaign, not the asked-for shape), a **surprising finding** (the
  premise is false against the code/data), a **real architectural fork**, or
  **out-of-sequence drift** (the task assumes something not yet true). Do not
  improvise to make a broken premise work.

## The three tiers

### Tier 0 — just do it, report. (No GO.)
Read-only recon, documentation, tests, local checks (suites, type/lint, local
runs). CC does the work and reports. **Docs/tests commit + push freely** — no GO
needed (a docs/tests push is behavior-neutral on deploy).

### Tier 1 — build straight through, one GO at push.
Behavior-changing code (non-irreversible). CC runs the full loop with **no HOLDs in
between**: recon → build → prove (faithful tests + red-proofs + an adversarial pass
on anything safety-touching) → commit to the working branch → report. **One GO from
AK for the push.** Everything up to the push is autonomous.

### Tier 2 — full gates.
Live-org **writes**, prod **deploys of deletion / mutation logic**, **non-trivial
migrations**, anything **irreversible**. CC: build + **red-proof the
catastrophic-failure guard** + report → **AK GOes the push** → **watched verify**
(predict-and-gate *before* any write — compute the intended effect read-only and
abort if it exceeds the predicted blast radius; carry an explicit abort condition)
→ confirm clean.

## What collapsed

| Old | New |
|---|---|
| HOLD before recon, after recon, before build, before commit, before push | **Tier 0:** free · **Tier 1:** one GO (push) · **Tier 2:** one GO (push) + watched verify |

The rigor is the constant; the tier sets only *how many gates* and *whether a
watched verify is required*. When unsure which tier applies, default up (a Tier-1
that touches creds / irreversible state is Tier 2) and say so.
