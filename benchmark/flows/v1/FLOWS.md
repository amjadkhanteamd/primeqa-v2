# FB-V1 — The Fifteen Controls

One entry per benchmark flow. Complexity rises through seven levels; each flow
exercises **one** major capability (the capstone FL12 composes deliberately).
Field names reference [`FIXTURE_SKETCH.md`](FIXTURE_SKETCH.md); the exact
flow-element definitions are authored in Wave 0 and live in the (future) SFDX
source. Architectural predictions per flow are indexed in
[`ARCHITECTURE_EXPECTATIONS.md`](ARCHITECTURE_EXPECTATIONS.md).

Evidence vocabulary (extends VRB-V1's):

- **transformation evidence** — the save succeeds AND the read-back shows a
  value the probe did **not** post, matching the flow's predicted output
  (posted ≠ persisted, *by design*).
- **side-effect evidence** — a read-back on a **different record** than the
  one saved: existence + field assertions on records the flow
  created/updated.
- **suppression control** — the arm where the flow's condition is *not* met,
  proving the effect absent; with no error message to attribute, the
  fire/suppress **differential is the attribution mechanism**.
- **deferred evidence** — an effect whose observation window is after the
  synchronous response (async path, scheduled path).
- **honest evidence-limit classification** — the system states that an effect
  is not observable through its evidence surface, rather than fabricating a
  pass or silently dropping the behaviour.

---

## Level 1 — the evidence-model pivot

### FL01 — `PLS_FB_FL01_Default_Priority`

- **Business scenario:** new orders without a stated priority are treated as
  standard priority.
- **Feature exercised:** record-triggered **before-save** flow (fast field
  update); one Decision (blank check) + one Assignment on `$Record`.
- **Trigger:** Order create.
- **Inputs:** an order posted with `Priority__c` blank (fire arm); an order
  posted with `Priority__c = High` (suppression arm).
- **Expected behaviour:** blank → persisted as `Standard`; supplied value →
  untouched.
- **Expected observable evidence:** transformation evidence on the fire arm
  (posted no value, read back `Standard`); read-back of the *unmodified*
  supplied value on the suppression arm.
- **Why this benchmark exists:** it is the cheapest possible flow, and it
  already breaks the validation-rule world's core assertion — VRB-V1's
  acceptance evidence asserts *the staged value persisted*; here the correct
  persisted value is one the test never staged. If the simplest control
  fails, nothing beyond it is meaningful.
- **Plimsol capability stressed:** the evidence model itself (expected-value
  must be sourced from predicted automation effect, not from the staged
  payload); flow-behaviour grounding (knowing the default exists and what it
  sets).

## Level 2 — before-save logic

### FL02 — `PLS_FB_FL02_Normalize_External_Ref`

- **Business scenario:** external order references are stored in the
  company's canonical form regardless of how they were typed.
- **Feature exercised:** before-save flow (trim + uppercase Assignment using
  text formula), **in deliberate composition with validation rule
  `PLS_FB_VR01_External_Ref_Format`**, which validates the canonical form and
  runs *after* before-save flows in the order of execution.
- **Trigger:** Order create or update where `External_Ref__c` is changed.
- **Inputs:** a lowercase-but-otherwise-valid reference (repair arm); a
  reference invalid even after normalisation (true-reject arm); an
  already-canonical reference (idempotence arm).
- **Expected behaviour:** lowercase input **saves**, persisted uppercase;
  structurally invalid input is rejected by VR01; canonical input persists
  unchanged.
- **Expected observable evidence:** transformation evidence (repair arm);
  attributed rejection à la VRB-V1 (true-reject arm); unchanged read-back
  (idempotence arm).
- **Why this benchmark exists:** the **order-of-execution adversarial
  control**. A system that reasons from the validation rule alone predicts
  the lowercase input is rejected — and is wrong, because the flow repairs it
  first. Observable org behaviour is a composition of automations, not a
  conjunction of constraints.
- **Plimsol capability stressed:** order-of-execution reasoning; predicted
  outcomes as automation-pipeline compositions; coexistence of the VR
  constraint IR with a flow-effect model over the same field.

### FL03 — `PLS_FB_FL03_Tier_Banding`

- **Business scenario:** orders are classified into commercial tiers by
  order value.
- **Feature exercised:** before-save flow with a **multi-outcome Decision**
  (four ordered outcomes + default) assigning `Tier__c` from `Amount__c`
  bands.
- **Trigger:** Order create, and update where `Amount__c` is changed.
- **Inputs:** one amount per band, plus the boundary values at each band edge
  (the exact thresholds live in the flow's decision conditions — the
  requirement will not name them; deriving them from flow metadata is the
  test).
- **Expected behaviour:** each band's amount persists the band's tier; each
  boundary lands on the side the decision's comparison operators dictate.
- **Expected observable evidence:** transformation evidence per band arm;
  boundary pairs proving each edge (the VRB-V1 minimal-increment discipline,
  applied to decision conditions).
- **Why this benchmark exists:** VRB-V1's DecisionBranchCoverage was proven
  shape-driven on VR formulas (VR03 → VR07 generalisation). This control asks
  the same question one representation away: can branch coverage, boundary
  derivation, and minimally violating witnesses feed from a **flow decision
  element** instead of a VR formula?
- **Plimsol capability stressed:** flow-metadata constraint extraction
  (decision conditions → constraint IR); N-way exclusive branch coverage with
  outcome-order semantics; boundary witness derivation from a new source.

## Level 3 — after-save side effects

### FL04 — `PLS_FB_FL04_Confirmation_Task`

- **Business scenario:** confirming an order creates a fulfilment task so
  operations picks it up.
- **Feature exercised:** record-triggered **after-save** flow; entry
  condition `Status = Confirmed` with **"only when a record is updated to
  meet the condition"**; **Create Records** (one `PLS_FB_Fulfilment_Task__c`,
  Type `Confirmation`, `Due_Date__c` from a formula = trigger date + offset).
- **Trigger:** Order update entering Confirmed.
- **Inputs:** an order driven Draft → Submitted → Confirmed (fire arm); an
  order *created already* in Confirmed state, if creatable, or updated
  within Confirmed on an unrelated field (suppression arms — the transition
  semantics say no task).
- **Expected behaviour:** exactly one task appears on the fire arm, correctly
  linked and populated; zero tasks on the suppression arms.
- **Expected observable evidence:** side-effect evidence (query the task by
  parent, assert Type/link/Due_Date) + a count assertion (exactly one) +
  suppression control (zero tasks when the condition was met at create, not
  by update).
- **Why this benchmark exists:** the first control whose evidence lives on a
  record the test never posted. It also welds VRB-V1's transition semantics
  to the new side-effect evidence: "updated to meet" is `ISCHANGED` wearing
  flow clothing.
- **Plimsol capability stressed:** side-effect evidence (cross-record
  read-back, existence + cardinality); transition IR generalisation to flow
  entry conditions; attribution by fire/suppress differential.

### FL05 — `PLS_FB_FL05_Cancellation_Sync`

- **Business scenario:** cancelling an order cancels its outstanding
  fulfilment work.
- **Feature exercised:** after-save flow; **Get Records** (open tasks for
  this order) + **Update Records** fan-out (all → Status `Cancelled`).
- **Trigger:** Order update entering Cancelled.
- **Inputs:** an order staged with several open tasks and at least one
  already-Completed task, then cancelled.
- **Expected behaviour:** every open task becomes Cancelled; the Completed
  task is untouched.
- **Expected observable evidence:** side-effect evidence over a *set*
  (post-cancellation query: zero Open tasks, Completed count unchanged) —
  set-level assertions, not single-record read-back.
- **Why this benchmark exists:** fan-out is the first effect whose evidence
  is a *predicate over a record set*; it also forces multi-record fixtures
  (the tasks must exist *before* the trigger — staged either directly or via
  FL04's own behaviour, a fixture-design fork to settle in Wave 2).
- **Plimsol capability stressed:** multi-record fixture staging;
  set-valued expected state; scoped-effect reasoning (which records the
  Get Records filter selects — and which it spares).

## Level 4 — data-dependent logic

### FL06 — `PLS_FB_FL06_Duplicate_Flag`

- **Business scenario:** an order that appears to duplicate an existing open
  order is flagged for review.
- **Feature exercised:** flow behaviour conditioned on **pre-existing org
  data**: Get Records (another non-cancelled order with the same
  `External_Ref__c`) + Decision on result presence + flag write.
- **Trigger:** Order create.
- **Inputs:** two-arm differential with the *record under test byte-identical
  in both arms*: create with a pre-staged matching sibling present (fire
  arm); create with no such sibling (control arm).
- **Expected behaviour:** flagged in the fire arm; unflagged in the control
  arm.
- **Expected observable evidence:** transformation evidence on
  `Duplicate_Flag__c` in the fire arm + the clean control arm — a
  **DATA-PRESENCE differential**: the varied dimension is the surrounding
  org data, not the record.
- **Why this benchmark exists:** every VRB-V1 control's outcome was a
  function of the posted record (plus its own history). This is the first
  control whose outcome is a function of *other records* — the expected
  outcome cannot be computed from the payload at all.
- **Plimsol capability stressed:** a new differential dimension
  (data-presence) in the VRB-V1 ContextDifferential family; environment-state
  staging as a first-class fixture concern; isolation *between runs* (a
  leftover sibling from a previous run silently flips the control arm).

### FL07 — `PLS_FB_FL07_Order_Rollup`

- **Business scenario:** an order always shows the current total and count of
  its line items.
- **Feature exercised:** record-triggered flow **on the child object**
  (`PLS_FB_Order_Line__c`, create/update); **Get Records** (siblings) +
  **Loop** + accumulator Assignments + **Update Records** on the **parent**.
- **Trigger:** Order Line create or update.
- **Inputs:** cardinality partitions — an order given its first line (0→1), a
  line added to an order with existing lines (N→N+1), a line's amount
  updated in place.
- **Expected behaviour:** after each child event, the parent's
  `Order_Total__c` / `Line_Count__c` equal the aggregate of all current
  lines.
- **Expected observable evidence:** side-effect evidence on the parent (the
  *computed aggregate* as expected value) across the cardinality partitions.
- **Why this benchmark exists:** loops make the expected value a function of
  a **collection** — the first control where correctness cannot be stated
  without cardinality partitions (the 0/1/N discipline), and the first where
  the triggering record and the asserted record are on *different objects in
  a parent-child graph*.
- **Plimsol capability stressed:** iteration/aggregation reasoning (predicting
  a computed aggregate); cross-object graph fixtures (child probes asserting
  parent state); cardinality partitioning as a new witness axis.

## Level 5 — temporal computation and prior state

### FL08 — `PLS_FB_FL08_SLA_Stamp`

- **Business scenario:** submitting an order stamps the service-level
  deadline the team must meet.
- **Feature exercised:** before-save flow on the Submitted transition;
  **formula resource** with **date arithmetic** writing `SLA_Deadline__c`
  (submission date + a fixed offset defined in the flow).
- **Trigger:** Order update entering Submitted.
- **Inputs:** an order driven into Submitted on the run date.
- **Expected behaviour:** `SLA_Deadline__c` persists as run-date + the
  flow's offset.
- **Expected observable evidence:** transformation evidence whose **expected
  value is itself symbolic** — `RelativeDate(RUN_DATE, k)` on the assertion
  side, with k derived from the flow's formula, materialised at the same
  execution boundary as VRB-V1's temporal inputs.
- **Why this benchmark exists:** VRB-V1's temporal capability made
  *inputs* replay-stable. This control requires replay-stable *expectations*:
  a benchmark rerun months later must assert a different concrete date and
  the same symbolic fact.
- **Plimsol capability stressed:** temporal capability generalised from
  staged values to asserted values; offset extraction from flow formula
  metadata.

### FL09 — `PLS_FB_FL09_Reopen_Guard`

- **Business scenario:** a fulfilled order that gets moved back into an
  active state is marked as reopened and the event is recorded for audit.
- **Feature exercised:** after-save flow using **`$Record__Prior`** in its
  condition logic (prior Status = Fulfilled AND current ≠ Fulfilled) →
  set `Reopened__c` + Create Records (`PLS_FB_Audit_Log__c`, Kind `Reopen`).
- **Trigger:** Order update leaving Fulfilled.
- **Inputs:** two-arm prior-state differential — identical mutation (set
  Status to Confirmed) applied to an order whose history reached Fulfilled
  via the legitimate path (fire arm) vs. one that never was Fulfilled
  (control arm).
- **Expected behaviour:** fire arm: flag set + one audit row; control arm:
  neither.
- **Expected observable evidence:** transformation + side-effect evidence on
  the fire arm; clean suppression on the control arm; the two arms differing
  in exactly one dimension (transition history) — VRB-V1's PRIOR_STATE
  differential, verbatim.
- **Why this benchmark exists:** the direct generalisation probe for the
  transition IR: `$Record__Prior` is `PRIORVALUE` living inside flow logic
  rather than a VR formula. If the transition machinery is representation-
  independent, this control costs nothing new.
- **Plimsol capability stressed:** transition IR sourcing from flow metadata;
  legitimate-path composition (reaching Fulfilled through the org's own
  lifecycle, which after FL12 exists means traversing other automations).

## Level 6 — asynchrony

### FL10 — `PLS_FB_FL10_Stale_Order_Escalation`

- **Business scenario:** orders sitting in Submitted too long are escalated.
- **Feature exercised:** **scheduled path** on a record-triggered flow — N
  days after entering Submitted, if still Submitted: set `Escalated__c` +
  create an Escalation task.
- **Trigger:** Order update entering Submitted (path scheduled for N days
  later).
- **Inputs:** an order driven into Submitted.
- **Expected behaviour (org truth):** nothing observable at save time; the
  effect executes days later, and only if the order is still Submitted.
- **Expected observable evidence:** **none within the run window — by
  design.** The correct system outcome is an honest evidence-limit
  classification: the behaviour is recognised, the test states that its
  effect is scheduled beyond the observation window, and no fabricated pass
  or phantom assertion is produced. (A system that *does* grow deferred
  observation machinery may score this control by observing the scheduled
  execution; the gold standard will admit both outcomes, and rank honest
  refusal above a wrong assertion.)
- **Why this benchmark exists:** the time-travel honesty control. VRB-V1's
  lesson was that false confidence is worse than absence; scheduled paths are
  where a flow-naive system fabricates most easily ("saved fine → pass").
- **Plimsol capability stressed:** recognising future-execution semantics in
  flow metadata; honest classification of unobservable-in-window effects;
  (optionally, as new architecture) deferred-evidence orchestration.

### FL11 — `PLS_FB_FL11_Async_Enrichment`

- **Business scenario:** confirmed orders get enrichment processing recorded
  for audit shortly after confirmation.
- **Feature exercised:** after-save **asynchronous path** ("Run
  Asynchronously") → Create Records (`PLS_FB_Audit_Log__c`, Kind
  `AsyncEnrichment`).
- **Trigger:** Order update entering Confirmed (async path).
- **Inputs:** an order driven into Confirmed.
- **Expected behaviour:** the audit row exists — but only *eventually*
  (a separate transaction, seconds-to-minutes after the save returns).
- **Expected observable evidence:** deferred side-effect evidence — the
  assertion is true at t + ε but false at t. A read-back racing the async
  transaction produces a **flaky false negative**; the evidence protocol must
  poll/wait with a bounded window, and the recorded evidence should state
  the observation delay.
- **Why this benchmark exists:** unlike FL10, this effect IS observable
  within a practical window — it separates "cannot observe" (honesty
  territory) from "cannot observe *yet*" (protocol territory). A system that
  conflates the two either fabricates FL10 or flakes FL11.
- **Plimsol capability stressed:** eventual-consistency evidence protocol
  (bounded polling, race-aware verdicts); distinguishing the two async
  classes in flow metadata (scheduled path vs. async path).

## Level 7 — composition, failure, and interaction surfaces

### FL12 — `PLS_FB_FL12_Fulfilment_Orchestrator` (+ subflow `PLS_FB_SF01_Close_Tasks`) — the capstone

- **Business scenario:** fulfilling an order closes out its open fulfilment
  work and stamps the fulfilment date; problems during close-out are recorded
  rather than blocking fulfilment.
- **Feature exercised:** after-save flow on the Fulfilled transition with
  **multiple Decisions** (any open tasks? amount tier relevant?), a
  **Subflow** call (`PLS_FB_SF01_Close_Tasks`: Get open tasks → **Loop** →
  mark Completed → Update Records) and a **fault connector** on the subflow's
  DML writing an audit row. Stamps `Fulfilled_Date__c`.
- **Trigger:** Order update entering Fulfilled.
- **Inputs:** an order with open tasks driven to Fulfilled (full-path arm);
  an order with no tasks driven to Fulfilled (decision short-circuit arm).
- **Expected behaviour:** full-path arm: all open tasks Completed +
  `Fulfilled_Date__c` stamped; short-circuit arm: date stamped, no task
  writes, no fault row.
- **Expected observable evidence:** composed side-effect + transformation
  evidence per arm; set-level task assertions; absence assertions on the
  short-circuit arm.
- **Why this benchmark exists:** the deliberate composition capstone —
  decisions + loop + subflow + fault handling in one artifact, per the
  benchmark's required complexity endpoint. The behaviour under test spans
  **two metadata artifacts**: attribution must survive composition ("the
  orchestrator, via its subflow, completed these tasks").
- **Plimsol capability stressed:** subflow-aware grounding (following the
  reference, merging the called flow's behaviour into the caller's);
  attribution granularity across composition; compound experiment design
  without combinatorial explosion.

### FL13 — `PLS_FB_FL13_Fault_Logged_Ledger`

- **Business scenario:** confirmed orders post a ledger entry; when posting
  fails, the failure is recorded for audit and the confirmation still stands.
- **Feature exercised:** after-save flow on the Confirmed transition; Create
  Records (`PLS_FB_Ledger_Entry__c`, whose required `Ledger_Code__c` is
  sourced from the Order's **optional** field) with a **fault connector** →
  Create Records (`PLS_FB_Audit_Log__c`, Kind `LedgerFault`).
- **Trigger:** Order update entering Confirmed.
- **Inputs:** the fault key is an ordinary input: order with
  `Ledger_Code__c` populated (happy arm) vs. blank (fault arm) — the
  internal failure is **externally reachable by design**.
- **Expected behaviour:** happy arm: ledger entry exists, no fault row; fault
  arm: no ledger entry, one fault row, **and the order's save still
  succeeded** (the fault was handled, not propagated).
- **Expected observable evidence:** the two-arm exchange — existence flips
  between ledger entry and fault row across arms; plus the save-succeeded
  assertion on the fault arm (a handled fault is not a rejection).
- **Why this benchmark exists:** fault paths are behaviour, not accidents —
  a requirement ("failures are recorded") maps to the fault connector. It
  forces reasoning about *which inputs make an internal element fail*, a
  question no VR ever posed, and it distinguishes handled faults from the
  unhandled-fault rejection shape.
- **Plimsol capability stressed:** fault-reachability reasoning (deriving the
  fault-triggering input from the flow's data mapping + the target object's
  required-field metadata — two metadata sources composed); a new verdict
  vocabulary for handled-fault outcomes.

### FL14 — `PLS_FB_FL14_Approval_Submit`

- **Business scenario:** large submitted orders require managerial approval
  before processing.
- **Feature exercised:** after-save flow on the Submitted transition with an
  amount threshold in its entry/decision conditions → **Submit for Approval**
  core action into the fixture's one-step approval process.
- **Trigger:** Order update entering Submitted with a qualifying amount.
- **Inputs:** a qualifying amount (fire arm); a sub-threshold amount
  (suppression arm) — threshold derived from the flow, not the requirement.
- **Expected behaviour:** fire arm: a pending approval exists and the record
  is locked; suppression arm: no approval, unlocked.
- **Expected observable evidence:** side-effect evidence on the **approval
  machinery's own objects** (a pending `ProcessInstance` for the record;
  lock state), plus the suppression control.
- **Why this benchmark exists:** flows that invoke *other governance
  mechanisms* produce evidence in that mechanism's vocabulary, not the
  triggering object's. It is also the designed bridge to the future
  approval-process benchmark family.
- **Plimsol capability stressed:** platform-object evidence reads
  (ProcessInstance, lock) as first-class assertion targets; threshold
  extraction from flow conditions (FL03's capability, re-tested where the
  effect is non-DML).

### FL15 — `PLS_FB_FL15_Confirmation_Email`

- **Business scenario:** customers receive a confirmation email when their
  order is confirmed.
- **Feature exercised:** after-save flow on the Confirmed transition → Flow
  core **Send Email** action to `Customer_Email__c` (revised at Wave 0 from
  an email alert: the Metadata API cannot declare an email-field recipient
  on an alert; the observable behaviour is identical). **Deliberately
  co-triggered** with FL04 and FL13 (same transition, disjoint effects).
- **Trigger:** Order update entering Confirmed with a customer email present
  (the email-present entry condition keeps address-less orders from faulting
  the send).
- **Inputs:** an order driven into Confirmed.
- **Expected behaviour (org truth):** an email is sent. Nothing about it is
  queryable through the fixture's objects.
- **Expected observable evidence:** **none — by design.** The correct outcome
  is the honest evidence-limit classification: the behaviour is recognised
  and represented, and its evidence is declared out of reach of the API
  observation surface — while the co-triggered FL04/FL13 effects on the very
  same save are still correctly asserted and attributed to *their* flows,
  not to this one.
- **Why this benchmark exists:** the second honesty control, harder than
  FL10: here the *same probe* yields rich observable evidence for two sibling
  flows and none for this one. Partial observability on a single event is
  where effect-to-cause attribution is most tempted to smear.
- **Plimsol capability stressed:** per-effect (not per-probe) evidence
  classification; attribution discipline under co-triggered automations;
  honest coverage accounting (an AC "covered" by an unobservable effect must
  not be counted as trustworthy).
