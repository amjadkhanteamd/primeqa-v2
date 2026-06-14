# Flagship: the Release Intelligence Loop, live on a real Salesforce org

**Theme #1 — "prove it on a real org."** This is the showcase: PrimeQA's whole
loop — requirement → AI-generated test claim → approval → live execution against a
real Salesforce org → interpretation → risk score → **GO/NO-GO recommendation with
explainability** — running end-to-end, unattended, every day, against the live
env-59 sandbox ("Prime QA NEW", tenant 1).

It is not a demo rigged to pass. On 2026-06-14 it recommended **NO-GO** — for one
real, explained reason — and that is the point: the system tells you the honest
truth about a release and *why*.

## The proof (env-59, 2026-06-14, 06:00–06:03 UTC)

The daily scheduled run executed the whole approved corpus against the live org —
**15 runs, 14 passed, 1 failed** — exercising the full verdict surface:

| Run (`run_id`) | Requirement | Verdict | Outcome |
|---|---|---|---|
| `fe4484f5` | SQ-205 | asserted_metadata_present | passed |
| `ef3fed27` | SQ-207 | **state_transitioned** | passed |
| `d99e2b71` | SQ-206 | value_persisted | passed |
| `2141f751` | SQ-207 / SQ-211 | asserted_metadata_present | passed |
| `d8ce95e7` | SQ-205 | **automation_triggered** | passed |
| `ded5d3e0` | SQ-211 / req-280 / req-283 | **prohibition_enforced** | passed |
| `fb3efb3b` | req-283 | value_persisted | passed |
| `4c01603c` | SQ-207 | value_persisted | passed |
| `ad412896` | SQ-205 | asserted_metadata_present | passed |
| `6ea40b8f` | SQ-205 | automation_triggered | passed |
| `334aa038` | SQ-205 | automation_triggered | passed |
| `60a44caa` | SQ-209 | asserted_metadata_present | passed |
| `af741265` | SQ-205 | asserted_metadata_present | passed |
| `f51877cd` | req-282 | value_persisted | passed |
| `761bc7b2` | SQ-205 | **state_not_transitioned** | **failed** |

Each passing run created, queried, and cleaned up real records in the live org
(audited in `s4_created_records`); the worker decrypted the org's OAuth tokens and
authenticated to Salesforce — a local machine can't (the `CREDENTIAL_ENCRYPTION_KEY`
is Railway-only), so this only happens through the deployed worker.

## The decision

Computed by the substrate decision engine (`compute_substrate_decision`) over that
live evidence — no human in the loop:

```
NO-GO   ·   risk medium   ·   pass rate 93.3% (14 / 15)   ·   grounding intact
Blocked by SQ-205 — an active Flow (SQ205_Escalation_Effects) triggers on
Escalation, but the asserted effect was not observed — an entry condition may be
unmet, or the Flow's logic changed since generation.
```

The 93.3% pass rate is below the 95% gate, so the recommendation is NO-GO. The
**explainable NO-GO** (D-237) names the blocking requirement and surfaces the S6
attribution sentence on the hero — so the headline is "*here is what's blocking the
release and why*", not a bare percentage.

The lone red is a genuine finding, not a bug: SQ-205's requirement text expects the
Case to reach status **"In Escalation"**, but the org's escalation Flow moves it to
**"Escalated"**. The system caught a real requirement-vs-org mismatch and refused to
wave the release through. (Resolving it is a product call — amend SQ-205's Jira text
to "Escalated" and regenerate, or keep the red as the recorded finding.)

## Where to see it

- **Live, continuous** — `/dashboard` (release-owner) computes the GO/NO-GO over
  every approved claim in env-59 and refreshes as runs land. `/shared/<token>`
  exposes the same hero read-only for stakeholders.
- **Named, point-in-time** — `scripts/flagship_release_seed.py` mints a durable
  `Release` ("Flagship — env-59 live proof (D-237)") over the same corpus and
  records one `ReleaseDecision`; view at `/releases/<id>?tab=decision`, which
  renders the substrate recommendation + the same explainable blocker. Run it once:

  ```bash
  python scripts/flagship_release_seed.py            # dry run — previews the decision
  python scripts/flagship_release_seed.py --commit   # create the release + record it
  ```

  (It resolves 7 of the 8 requirements — SQ-206's managed requirement row is absent
  in the public table, a separate historical-corpus artifact; the env-level
  dashboard keeps the full 8-requirement view. The decision is the same NO-GO.)

## Why this matters

The category isn't "a test runner." It's *decision-making for releases*. This
flagship is the whole thesis in one screen: real org, real AI-authored tests, real
execution, an honest recommendation, and — with D-237 — an explanation a human can
act on without drilling. The engine was already proven; this makes the proof
legible.

## Reproducibility

The proof regenerates itself: the Railway scheduler fires the env-59 corpus daily at
06:00 UTC (`s4_run_schedules` id=1), so the dashboard's GO/NO-GO is always computed
over fresh, real evidence — not a frozen snapshot.
