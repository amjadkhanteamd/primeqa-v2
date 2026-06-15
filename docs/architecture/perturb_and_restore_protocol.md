# Perturb-and-restore protocol — env 59 ("Prime QA SFDC" sandbox)

Tier 0.2 of the dogfood development list (G-3 from the matrix audit). The
negative columns of the dogfood matrix — `prohibition_not_enforced`,
`automation_not_triggered`, `vr_inactive` / `vr_formula_drift` causes, S8
grounding drift — can only be exercised by deliberately breaking the sandbox
and watching the engine notice. This document is the **written procedure**:
what gets touched, how, the expected engine observation, and the restore
ritual that returns the org to fixture state.

**Status: ACTIVE — §2 "may touch" list signed off 2026-06-12 (see
`dogfood_matrix_log.md`). P1 and P3 windows have been executed; P2 is on hold
pending an AK may-touch extension.** Execution requires the sf CLI
(`primeqa-sandbox` alias, already authenticated) or AK's own Setup access.

---

## 1. Ground rules (non-negotiable)

1. **One perturbation live at a time.** Never overlap two perturbations —
   verdict attribution must be unambiguous.
2. **Record before touching.** Every perturbation starts by capturing the
   current state of the artifact (`sf data query` / metadata read) into the
   session log, so restore is verified against a recorded baseline, not memory.
3. **Restore immediately after capture.** The window between perturb and
   restore is one engine observation (one run or one sync), not a work session.
4. **Re-sync + re-verify after restore.** Every restore ends with: fresh S1
   sync → confirm the synced state matches the recorded baseline → one green
   re-run of the affected claim. The sandbox is not "restored" until the
   engine itself observes fixture state again.
5. **Fixture-state artifacts only.** Only the artifacts in §2 may be touched.
   Anything else (users, profiles, other objects' rules, org settings) is out
   of scope without a new sign-off.
6. **No data perturbations needed.** All listed perturbations are metadata
   toggles/edits; record data created by runs is cleaned by the engine's own
   teardown (s4_created_records).

## 2. The "may touch" list (AK signs off on exactly this)

| # | Artifact | Perturbation | Restore |
|---|---|---|---|
| P1 | Flow `SQ205_Create_Case_SLA` (the SQ-205 fixture Flow, sandbox_fixtures/sq205/; companion `SQ205_Escalation_Effects`) | Deactivate (Setup → Flows → Deactivate, or deploy with `status: Draft`) | Re-activate; confirm `IsEscalated`/`Last_Escalation_Date__c` stamping resumes on a probe Case |
| P2 | VR `Opportunity.Contract_Value_Required_On_Closed_Won` | Toggle `active=false` | Toggle back `active=true` |
| P3 | VR `Lead.RequireReason`-class rule (any one VR with a derivable formula; exact pick at execution) | Edit the error-condition formula to a non-equivalent comparison (e.g. threshold change) | Restore the recorded original formula text byte-for-byte |
| P4 | Field `Case_SLA__c.Response_Hours__c` (fixture custom field) | Change length/precision (e.g. precision 18→16) | Restore recorded original |
| P5 | Permission set of the **FLS-restricted user** (Tier 0.3 — only after AK creates it) | Remove read on `Case.Last_Escalation_Date__c` | This one is the CF-1 repro target and may be left in place as a standing fixture if AK prefers |

## 3. Per-perturbation expected observations (the matrix columns they close)

- **P1 (Flow off)** → rerun the SQ-205 automation-effect claim → run FAILS,
  S6 verdict `automation_not_triggered`. Also the staged state-transition
  claim on any fixture relying on the Flow.
- **P2 (VR off)** → rerun the Closed-Won prohibition claim → the negative
  create SUCCEEDS where rejection was asserted → verdict
  `prohibition_not_enforced`, cause `vr_inactive` (the D-111.1 cause we have
  never seen live).
- **P3 (formula edited)** → grounding drift needs a fresh S1 sync: only after
  the sync does S8's grounding-validity recompute flag the claim
  (`vr_formula_drift` family). A bare rerun without an intervening sync does
  **not** surface drift — the engine is still grounded against the pre-edit
  formula. (Corrected here per `dogfood_matrix_log.md`; the earlier
  drift-without-sync expectation was wrong.)
- **P4 (field property)** → the property-claim on that field (generate one
  first) flips from passed to failed; S8 grounding drift board shows the
  entity-level change after sync.
- **P5 (FLS)** → run a claim whose create writes the restricted field with
  the restricted user's connection → clean `platform_constraint` cause naming
  the code + field (D-225's deliberate attribution — the CF-1 acceptance).

## 4. The restore checklist (run after EVERY perturbation)

```
[ ] Perturbed artifact restored to the recorded baseline (diff shown in session)
[ ] Fresh S1 sync completed (new version_seq recorded)
[ ] Synced S1 state matches baseline (query the entity/edge attributes)
[ ] Affected claim re-run → green (verdict back to the fixture expectation)
[ ] Session log updated: perturbation id, run ids observed, restore evidence
```

## 5. What this protocol deliberately avoids

- **No production org. Ever.** env 59 only.
- **No user/profile surgery** beyond P5's single permset (AK-created).
- **No deletes** of fixture artifacts — deactivate/edit only, always reversible.
- **No schedule-window overlap**: perturbations pause if the 06:00 UTC daily
  schedule (s4_run_schedules id=1) would fire mid-window — its clean-fire
  streak gates the D-221 R5 DROP and must not be polluted; either finish the
  restore before 06:00 UTC or disable→re-enable the schedule around the window
  (recorded in the session log).
