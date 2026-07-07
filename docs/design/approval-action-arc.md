# The approval-action arc — proposed design (DRAFT, awaiting GO + design review)

**Status:** PROPOSED 2026-07-07 (the last open item of the req-302 follow-up program;
D-308.1's named "submission-action executor arc" deferral). Not built. This document
is the banked blueprint + the design fork for AK's call — the D-312 pattern, minus
the review panel, which should run before the build.

## What it delivers

The four deferred approval test shapes (lever-ladder TCs):

- **TC-049** — a record with a PENDING approval blocks stage progression
  (submit → attempt the move → expect rejection).
- **TC-035** — approval GRANTED → the progression is accepted
  (submit → approve the workitem → the move succeeds).
- **TC-036** — approval REJECTED → the progression stays blocked
  (submit → reject → the move is refused).
- **TC-034** — the org's own >₹50,00,000 auto-submission fires. **Blocked on the
  open org action**: env-59's `HL_Auto_Submit_Approval` entry criteria are dead
  config (the VR forbids the state the flow triggers on — D-308.1's honest-red);
  test d49719e2 stays red until AK fixes the org. TC-035/036/049 do NOT wait on
  it — the API submit action bypasses the flow.

## The layers (bottom-up)

1. **integrations/sf_client** — two REST calls: `POST /process/approvals/` with
   `{actionType: "Submit", contextId, comment}` (submit) and with
   `{actionType: "Approve"|"Reject", contextActorId?, workitemId, comment}`
   (action); plus the workitem lookup
   (`SELECT Id FROM ProcessInstanceWorkitem WHERE ProcessInstance.TargetObjectId = :id`).
2. **S2 recipe model** — a new `ApprovalActionStep(step_id, action:
   submit|approve|reject, target: '$create-record.id', comment?)` in the
   data-recipe step family. Recipes are OPERATIONAL (non-identity) — additive,
   no hash concerns.
3. **S4** — translator → `PlannedApprovalAction`; the executor runs it between
   setup and the asserted mutation; evidence captures the ProcessInstance +
   Workitem ids. **The teardown law (D-308.1's watch item):** an
   approval-PENDING record refuses delete and locks its parent — teardown must
   RECALL (or resolve) any pending instance the run created before the
   reverse-order delete, and a failed recall is a LOGGED LEAK, never silent.
4. **S3 authoring — the fork (AK's call):**
   - **Design A (the lean):** the arc rides EXISTING claim kinds. An
     acceptance-claim v2 ("the move to Approved is ACCEPTED when
     Approval_Status = Approved") whose recipe interposes submit+approve action
     steps; a prohibition-claim ("the move is REJECTED while approval is
     pending") whose recipe interposes submit only. Grounding triggers the
     interposition when the subject binds an ACTIVE ApprovalProcess (D-308) and
     the claim's conditions reference the approval state. Composes with
     everything shipped: D-293 conditions carry `Approval_Status__c`, D-330's
     predicate-aware alignment already selects `Block_Approved_Without_Approval`,
     D-328 supplies its ₹50L boundary probes. No new claim kind, no new
     canonicalizer.
   - **Design B:** a new `approval-arc-claim` kind (multi-phase, the D-310
     journey class) — richer temporal semantics, but exactly the machinery
     D-310's panel costed as heavy (new canonicalizer + interpreter dispatch +
     per-segment temporal k16) and deferred.
   - **Lean: A.** B's power is only needed when an arc must accumulate state
     across MORE than one approval cycle — none of the four TCs does.
5. **S6** — attribution is approval-blind today (`flows_for_object` filters
   Flow; a D-308.1 named deferral): the arc's failure evidence should name the
   approval process. Small, do it in the same build.

## Review items the panel must pressure-test

- The `_grade_rejected_create` premise-break→failed tension (D-308.1 open) as it
  applies to a rejected UPDATE after an approval action.
- Workitem-assignee identity: the run-as user must BE the assigned approver (or
  hold Modify-All) for Approve/Reject to succeed — an authorization miss must
  grade as a setup rejection, not a claim failure.
- Idempotency/retry: a re-run must not double-submit (query for an existing
  pending instance first).
- The evidence shape for the decision engine (each action step = one evidence
  entry, verdict folds strict-AND as usual).

## Live exit gate

TC-049/035/036 green on env-59 with zero teardown leaks (Opportunities AND
ProcessInstances — the D-308.1 standard), and d49719e2 flipping green once AK
fixes the org's auto-submit entry criteria.
