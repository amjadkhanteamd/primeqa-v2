# Dogfood matrix log

The session records the perturb-and-restore protocol requires
(`perturb_and_restore_protocol.md` §4). One entry per perturbation window.
Append-only.

## Session 2026-06-12 (~03:30–04:15 UTC) — first negative-column captures

AK signed off the protocol §2 list this session. Three windows run; all
restores verified by a green re-run; one consolidated S1 sync ritual (see
deviations). All windows finished >1.5h before the 06:00 UTC schedule fire.

### P1 window 1 — Flow off (SQ205_Create_Case_SLA)
- Baseline: FlowDefinition `300Ip000000CyxrIAC`, ActiveVersionId
  `301Ip000000ERHUIA4` (v1 active). Perturb: Tooling PATCH
  `activeVersionNumber: 0` (the sf source-deploy path was skipped by source
  tracking — "Unchanged"; the Tooling PATCH is the reliable toggle).
- Observation: claim `3f6466bd` (Case→Case_SLA child-of-trigger) → run
  `bb73e714` **errored / not_evaluated** — NOT the expected
  `automation_not_triggered`. **Finding #1**: a 0-row SIDE-EFFECT read with an
  `equals` assert short-circuited to RecordNotObserved. Restored FIRST (per
  protocol), then fixed as **D-227.7** (f40f840): the 0-row short-circuit now
  applies only to self-observations (`WHERE Id = '$…'`); side-effect reads
  grade. Supersedes the D-210 errored pin.
- Restore: PATCH `activeVersionNumber: 1` → ActiveVersionId back to baseline;
  re-run green (`automation_triggered`).

### P1 window 2 — Flow off again (post-D-227.7 deploy)
- Same perturb. Observation: run `0f9eeeed` **failed /
  `automation_not_triggered`** — the verdict's first live capture. ✓
- Restore + re-run green (`automation_triggered`).

### P3 window — VR formula edit (Opportunity.Amount)
- Artifact: VR `03dIp000000CvskIAC` ("Amount"), picked at execution per the
  §2 P3 discretion (it is the VR the approved update-rejected claim `71583230`
  exercises). Baseline recorded byte-exact: formula `Amount  > 10000`
  (note the double space), message "Amount should be greater than 10000",
  errorDisplayField Amount, active true.
- Perturb: formula → `Amount  > 999999`; **S1 sync (job 29, seq 58)** before
  the run — without it S6 would evaluate the STALE formula and grade the
  accepted update as `enforcement_gap`, a false defect signal (the protocol's
  P3 row previously claimed drift-without-sync; corrected here).
- Observation: run `d8fba8fa` **failed / `prohibition_not_enforced`** — the
  verdict's first live capture ✓. Cause: `vr_formula_indeterminate`
  (Close_Date_Cannot_Be_Future) — **Finding #2**: the attribution's
  indeterminate-check outranks the evaluable-not-violated (drift)
  determination, and an UNRELATED VR whose formula references `TODAY()`
  (NonEvaluable) masked the precise `vr_formula_drift` on the Amount VR. The
  refinement (grade the claim's GROUNDING VR first — its formula is captured
  at generation, D-107) is queued for the 2.3 cause-attribution arc.
- Restore: formula back byte-exact; **S1 sync (job 30, seq 59)** — synced
  formula verified `Amount  > 10000`; re-run green (`prohibition_enforced`).

### Deviations (logged per §1)
- P1's two windows were not individually synced: the Flow toggle's net
  metadata state was unchanged at each restore, S1 never observed the off
  state, and runs read the LIVE org, not S1. The session-close sync (job 30,
  seq 59) captured the final (= baseline) state.
- The protocol's P1 named "Case_Escalation_Flow" — the fixture Flows are
  `SQ205_Create_Case_SLA` / `SQ205_Escalation_Effects`; the former was used.

### P2 — ON HOLD (needs an AK may-touch extension)
The signed §2 P2 artifact (`Contract_Value_Required_On_Closed_Won`) is not
exercised by any approved behavioral claim — toggling it flips nothing (the
caveated inspection claim's APPLIES_TO read deliberately carries no Active
filter, S4-Q-001). The `vr_inactive` cause capture needs the **Opportunity
"Amount" VR** toggled instead (the claim that flips is `71583230`). Awaiting
AK's one-line approval to extend §2.

### Matrix scoreboard after this session
| Verdict | Live-proven |
|---|---|
| automation_triggered / **automation_not_triggered** | ✓ / ✓ (new) |
| prohibition_enforced / **prohibition_not_enforced** | ✓ / ✓ (new) |
| state_transitioned / state_not_transitioned | ✓ / ✓ (e87c2666) |
| value_persisted | ✓ |
| cause `vr_inactive` | pending P2 extension |
| cause `vr_formula_drift` | masked by Finding #2 — re-capture after 2.3 |
| FLS `platform_constraint` (CF-1) | pending 0.3 (AK user) |
