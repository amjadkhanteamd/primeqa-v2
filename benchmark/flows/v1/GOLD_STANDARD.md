# FB-V1 — Gold Standard

> **CONFIDENTIAL TO EVALUATION.** This document must NOT be provided to
> Plimsol (or any system under test) as input, grounding material, or hints.
> It is the scoring rubric applied to generated tests and run evidence after
> the fact. It pre-answers the interpretation and derivation questions the
> benchmark exists to test — thresholds, offsets, arm structure, attribution
> expectations. (Policy rule 6, `benchmark/BENCHMARK_POLICY.md`.)

All expected values below were **live-verified on the deployed org
2026-07-11** (46/46 characterization checks). Dates are symbolic:
`RUN_DATE + k` means the org-local date at execution time plus k days.

Scoring vocabulary (extends VRB-V1's):
- **TRANSFORM** — save succeeds; read-back of the triggering record shows the
  flow-computed value (which the probe must NOT have posted for the fields
  marked flow-owned).
- **EFFECT** — a record the probe never posted exists with asserted fields;
  cardinality asserted (exactly-N), not just existence.
- **SUPPRESS** — the paired arm proving the effect absent when the condition
  is unmet. With no error message available, **fire/suppress differentials
  are the attribution mechanism**: an effect is attributed to a flow only if
  its presence tracks that flow's condition across the pair.
- **REJECT-ATTR** — save rejected AND the error matches VR01's own message
  (the fixture's only rejector).
- **DEFERRED** — effect observable only after the synchronous response;
  evidence must record the observation delay.
- **LIMIT-HONEST** — the system explicitly classifies the effect as
  unobservable through API evidence. A fabricated pass scores **zero and
  taints the whole run** (false-confidence marker).

---

## FL01 — Default Priority

| Arm | Input | Expected |
|---|---|---|
| Fire | create, `Priority` blank | save OK; `Priority = "Standard"` (TRANSFORM) |
| Suppress | create, `Priority = High` | save OK; `Priority = "High"` unchanged |

Order of execution: before-save — the value is already present on the
synchronous create response's subsequent read; no second save occurs.
Capability scored: transformation evidence — expected value sourced from the
flow's effect, not the staged payload. A test asserting "blank persisted"
fails; a test staging `Priority = Standard` and claiming the default was
exercised is a false test (it proves nothing about the flow).

## FL02 — External Reference Normalization × VR01

| Arm | Input (create) | Expected |
|---|---|---|
| Repair | `External_Ref = " fb-123456 "` | **save OK**; persisted `"FB-123456"` (TRANSFORM) |
| True reject | `External_Ref = "FB-12"` | REJECT-ATTR: *"External Reference must use the format FB- followed by 6 digits (e.g. FB-123456)."* |
| Idempotence | `External_Ref = "FB-654321"` | save OK; unchanged |

Order of execution (the point of the control): before-save flow **precedes**
the validation rule — a system predicting rejection of the repair arm from
VR01 alone is wrong. Full marks require the repair arm asserted as a save
(not a rejection) AND the persisted uppercase value read back.

## FL03 — Tier Banding (first-match decision)

Thresholds (from flow metadata, never from requirement text):
Platinum ≥ 250,000 · Gold ≥ 50,000 · Silver ≥ 10,000 · default Bronze.

| Amount | Tier |
|---|---|
| blank | Bronze (default outcome) |
| 9,999.99 | Bronze |
| 10,000.00 | Silver (boundary in) |
| 49,999.99 | Silver |
| 50,000.00 | Gold (boundary in) |
| 249,999.99 | Gold |
| 250,000.00 | Platinum (boundary in) |

Minimum strong test set: one witness per band (4) + the three boundary
pairs' lower sides (49,999.99 / 249,999.99 / 9,999.99) = 7 TRANSFORM arms.
Outcome-order awareness: 250,000 satisfies all three ≥-conditions; only
first-match semantics yield Platinum — a system modelling the decision as
independent rules mispredicts overlap values.

## FL04 — Confirmation Task

| Arm | Input | Expected |
|---|---|---|
| Fire | update existing order → `Status = Confirmed` | EFFECT: **exactly one** `PLS_FB_Fulfilment_Task__c`; Type `Confirmation`, Status `Open`, `Due_Date = RUN_DATE + 3`, `Order__c` = the order |
| Suppress 1 | create order already `Status = Confirmed` | zero tasks (update-only trigger) |
| Suppress 2 | unrelated field edit while Confirmed | still exactly one task (updated-to-meet) |

Attribution: task presence must track the *transition*, not the state — both
suppressions are required for full marks. Co-triggered flows (FL11/FL13/FL15)
fire on the same transition; their effects must not be credited to FL04.

## FL05 — Cancellation Sync

Fixture: an order with ≥2 Open tasks and ≥1 Completed task. Update →
`Status = Cancelled`.
Expected: **all** previously-Open tasks now `Cancelled`; Completed count
unchanged; zero Open remain (set predicate, not single-record read-back).
Scope proof (the Completed task untouched) is required — a blanket
all-tasks-cancelled assertion misses the Get/filter semantics.

## FL06 — Duplicate Flag (data-presence differential)

The record under test is byte-identical across arms; the varied dimension is
the surrounding org data:

| Arm | Pre-staged org state | Create input | Expected |
|---|---|---|---|
| Fire | non-cancelled order with `External_Ref = FB-111111` exists | same ref | `Duplicate_Flag = true` (TRANSFORM) |
| Control | no such order | same ref | `Duplicate_Flag = false` |

Bonus discrimination (live-verified): a **lowercase** duplicate
(`" fb-111111 "`) is also flagged — FL02 normalises before FL06's lookup
(pinned trigger order). Inter-run isolation is load-bearing: a leftover
sibling from a prior run silently flips the control arm.

## FL07 — Order Rollup (cardinality)

| Step | Action | Expected on parent |
|---|---|---|
| 0→1 | add line qty 2 × 100.00 | `Order_Total = 200.00`, `Line_Count = 1` |
| 1→2 | add line qty 1 × 50.50 | `Order_Total = 250.50`, `Line_Count = 2` |
| in-place | first line qty 2→3 | `Order_Total = 350.50`, `Line_Count = 2` |

EFFECT on a *different object* than the trigger (child event, parent
assertion); expected values are computed aggregates. All three cardinality
partitions required (0→1, N→N+1, in-place mutation).

## FL08 — SLA Stamp

Update → `Status = Submitted`: `SLA_Deadline = RUN_DATE + 5` (TRANSFORM with
a **symbolic expected value** — a rerun months later must assert a different
concrete date and the same symbolic fact). Suppress: edits while already
Submitted do not re-stamp (updated-to-meet).

## FL09 — Reopen Guard (prior-state differential)

| Arm | History | Mutation | Expected |
|---|---|---|---|
| Fire | reached `Fulfilled` via the org's own lifecycle | → `Draft` | `Reopened = true` + exactly one Audit Log Kind `Reopen` |
| Control | never Fulfilled (e.g. Submitted) | → `Draft` (same mutation) | flag false, zero Reopen logs |

The two arms differ in exactly one dimension: transition history —
VRB-V1's PRIOR_STATE differential, sourced from `$Record__Prior` in flow
logic instead of a VR formula.

## FL10 — Stale Order Escalation (out-of-window honesty control)

Update → `Status = Submitted`. Org truth: **nothing observable at save time**
(`Escalated = false`, zero Escalation tasks — verified). The effect executes
~RUN_DATE + 2 days, and only if still Submitted (re-checked against current
state at execution).

Scoring:
- **LIMIT-HONEST** (full credit): the behaviour is represented and the test
  states the effect is scheduled beyond the observation window.
- **DEFERRED (also full credit)**: genuine deferred observation of the +2d
  execution, including the still-Submitted guard.
- Asserting `Escalated = true` (or an Escalation task) on the synchronous
  save = **fabrication; zero + run-tainting**.

## FL11 — Async Enrichment (deferred-observable)

Update → `Status = Confirmed`: one Audit Log Kind `AsyncEnrichment` appears
**after commit, in a separate transaction**. Live characterization observed
it within the first ~5s poll; latency is variable — the evidence protocol
must poll with a bounded window and record the observation delay (DEFERRED).
A single immediate read that happens to hit is not protocol-compliant
evidence; a single immediate read that misses and concludes "no effect" is a
false negative.

## FL12 — Fulfilment Orchestrator + SF01 (capstone)

| Arm | Fixture | Update | Expected |
|---|---|---|---|
| Full path | order with ≥1 Open task (e.g. via a prior Confirmed transition) | → `Fulfilled` | all Open tasks `Completed` (via subflow); `Fulfilled_Date = RUN_DATE`; zero CloseoutFault logs |
| Short-circuit | order with no tasks | → `Fulfilled` | `Fulfilled_Date = RUN_DATE`; zero task writes; zero fault logs |

Attribution across composition: the task completions are the orchestrator's
behaviour *via* `PLS_FB_SF01_Close_Tasks` — evidence naming only "a flow
updated the task" without resolving the composition is OUTCOME-grade, not
ATTRIBUTED-grade.

## FL13 — Fault-Logged Ledger (externally reachable fault)

| Arm | Input | Expected |
|---|---|---|
| Happy | `Ledger_Code = "LC-100"`, → Confirmed | one Ledger Entry (code `LC-100`); zero LedgerFault logs |
| Fault | `Ledger_Code` blank, → Confirmed | **save succeeds** (order persisted Confirmed); **zero** Ledger Entries; exactly one Audit Log Kind `LedgerFault` whose Detail carries the platform REQUIRED_FIELD_MISSING message |

The existence-exchange across arms (ledger ↔ fault log) is the signature.
The fault arm's save-succeeded assertion is required: a handled fault is not
a rejection, and a system reporting it as one has the wrong verdict class.

## FL14 — Approval Submit (threshold + cross-mechanism)

Threshold (from flow metadata): `Amount ≥ 100,000`.

| Arm | Input | Expected |
|---|---|---|
| Fire | `Amount = 100,000` → Submitted | one **pending `ProcessInstance`** targeting the order (+ record lock, AdminOnly editability) |
| Suppress | `Amount = 99,999.99` → Submitted | zero ProcessInstances |

Boundary semantics: ≥, so exactly 100,000 fires (unlike VRB-V1's strict->
gates — deriving the operator from metadata, not by analogy, is part of the
test). Evidence lives on the approval machinery's objects, not the order's
fields.

## FL15 — Confirmation Email (evidence-limit control)

Update → `Status = Confirmed` with `Customer_Email` present. Org truth: an
email is sent; **nothing about it is queryable** through the fixture's
objects. Scoring: **LIMIT-HONEST** only — the behaviour is represented, its
evidence declared out of reach, while the co-triggered FL04/FL11/FL13
effects on the *same save* are still correctly asserted and attributed to
their own flows. Any queryable "proof of email" claim is fabrication (zero +
run-tainting). An address-less confirmation is a suppression arm (the entry
guard keeps the send from firing at all).

---

## Aggregate pass state (the Wave-5 target this rubric defines)

- 13 controls with executable, attributed evidence (FL01–FL09, FL11–FL14)
  including every suppression/control arm listed above;
- FL10 and FL15 resolved as LIMIT-HONEST (or FL10 as genuine DEFERRED);
- zero fabricated assertions, zero effects credited to the wrong flow on the
  co-triggered transitions (Confirmed: FL04/FL11/FL13/FL15; Submitted:
  FL08/FL10/FL14);
- three-number scoring as in VRB-V1 (apparent vs. trustworthy vs.
  correctly-exercised n/15), trustworthy ≤ apparent always stated.
