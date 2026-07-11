# FB-V1 — Org Fixture Sketch (design level)

The proposed benchmark org, at the level of detail needed to make the flow
catalogue concrete. **This is a sketch, not the fixture record** — exact field
scales, picklist API names, formula texts, and the SFDX source are authored in
Wave 0 and documented in the (future) `ORG_FIXTURE.md` + `benchmark-v1.json`,
which then become the immutable record. Namespace prefix: `PLS_FB_`.

## Business scenario

**Order fulfilment.** A sales-operations team manages Orders through a
lifecycle (Draft → Submitted → Confirmed → Fulfilled, or Cancelled), with
line items, fulfilment tasks, an audit trail, SLA stamps, escalation of stale
orders, an approval gate on large orders, and customer notifications. Chosen
because it naturally motivates every trigger context and effect type in the
catalogue without contrivance, and it is disjoint from VRB-V1's deal-approval
domain (no accidental cross-benchmark vocabulary overlap).

## Objects (4)

### `PLS_FB_Order__c` — the primary object

| Field (sketch) | Type | Written by | Role |
|---|---|---|---|
| `Status__c` | Picklist: Draft, Submitted, Confirmed, Fulfilled, Cancelled (restricted) | tests | The lifecycle spine; most triggers key on it |
| `Amount__c` | Currency | tests | FL03 banding input, FL14 approval threshold |
| `Priority__c` | Picklist: Low, Standard, High (restricted) | tests / **FL01** | FL01 defaults it when blank |
| `Tier__c` | Picklist: Bronze, Silver, Gold, Platinum (restricted) | **FL03** | Flow-computed classification — tests never write it |
| `External_Ref__c` | Text | tests / **FL02** | FL02 normalises; VR01 validates the *normalised* form |
| `Customer_Email__c` | Email | tests | FL15 recipient; FL06 duplicate key candidate |
| `Promised_Date__c` | Date | tests | Temporal input |
| `SLA_Deadline__c` | Date | **FL08** | Flow-computed date — the temporal assertion target |
| `Duplicate_Flag__c` | Checkbox | **FL06** | Data-dependent branch output |
| `Escalated__c` | Checkbox | **FL10** | Scheduled-path output (not observable in-window) |
| `Reopened__c` | Checkbox | **FL09** | Prior-state-logic output |
| `Fulfilled_Date__c` | Date | **FL12** (subflow) | Composition output |
| `Order_Total__c` | Currency | **FL07** | Child-rollup output — tests never write it |
| `Line_Count__c` | Number | **FL07** | Child-rollup output |
| `Ledger_Code__c` | Text, **optional** | tests | FL13's designed fault key: blank ⇒ the ledger create fails ⇒ fault path |

Flow-computed fields are deliberately **not** test-writable by convention
(and, where practical, excluded from the permission set's edit FLS) so that
any asserted value on them is unambiguously automation-produced.

### `PLS_FB_Order_Line__c` — child (master-detail → Order)

`Quantity__c` (Number), `Unit_Price__c` (Currency), `Line_Total__c`
(flow-readable; may be a formula field). Exists to drive FL07's
loop/aggregation and give cardinality partitions (0 / 1 / N lines) something
real to range over.

### `PLS_FB_Fulfilment_Task__c` — side-effect object (lookup → Order)

`Type__c` (Picklist: Confirmation, Escalation), `Status__c` (Picklist: Open,
Completed, Cancelled), `Due_Date__c` (Date). Created by FL04, cancelled by
FL05, completed by FL12's subflow. A **custom** object rather than standard
`Task` so the fixture stays self-contained and no org-default Task automation
can contaminate isolation.

### `PLS_FB_Audit_Log__c` — evidence sink (lookup → Order)

`Kind__c` (Picklist: e.g. AsyncEnrichment, LedgerFault, Reopen),
`Detail__c` (Text Area). The queryable trace for effects that would otherwise
be invisible or ambiguous: FL11's async write, FL13's fault-path record,
FL09's reopen note. The sink is an honest observability *aid*, not a cheat:
each writing flow's behaviour **is** "write this log entry", stated as such
in the requirement's qualitative language ("recorded for audit").

### `PLS_FB_Ledger_Entry__c` — fault target (lookup → Order)

`Ledger_Code__c` (Text, **required**), `Amount__c` (Currency). Exists so FL13
has a Create Records element that fails for an externally controllable
reason: Order.`Ledger_Code__c` blank → required-field DML failure → fault
connector. No flow triggers on this object.

## Non-flow automation (deliberate, minimal)

| Artifact | Why it exists |
|---|---|
| Validation rule `PLS_FB_VR01_External_Ref_Format` on Order — External_Ref, when present, must match the canonical uppercase format | The **order-of-execution control**, paired with FL02: a lowercase input is repaired by the before-save flow *before* the rule evaluates, so the save **succeeds**. A system reasoning from the VR alone predicts rejection and is wrong. |
| One-step approval process on Order (single approver, no field updates on submit beyond the standard lock) | FL14's target. Kept minimal so the observable evidence is the approval mechanism itself (`ProcessInstance`, lock), not approval side effects. |
| ~~Email alert (template + recipient = `Customer_Email__c`)~~ **Revised at Wave 0:** FL15 uses Flow's core **Send Email** action direct to `Customer_Email__c` (subject/body as flow text templates) | FL15's action target. *Wave-0 finding: a classic email alert cannot declare an email-field recipient through the Metadata API (`emailField` is not a valid `ActionEmailRecipientTypes` value), so the alert+template mechanism is not source-deployable. The Send Email action preserves the identical observable behaviour — an email to the customer address with no record trace.* |
| Permission set `PLS_FB_Access` | Full CRUD on the four test-writable objects + FLS per the write-ownership convention above. |

## Fixture-wide design rules

1. **Single writer per flow-computed field.** No two flows write the same
   field; attribution ambiguity is introduced only where designed
   (FL04/FL15 share a *trigger*, never an output).
2. **Every fault is input-reachable.** Nothing in the fixture requires org
   corruption, data deletion, or metadata tampering to exercise.
3. **No same-record after-save updates.** Field writes to the triggering
   Order happen in before-save flows only (except FL10's scheduled path and
   FL09/FL12, which are documented exceptions on *different* trigger events);
   FL07 writes the parent from the child's transaction. This keeps
   recursion out of V1 per the taxonomy.
4. **Restricted picklists everywhere**, auto-number Names, org-portable
   references (no hardcoded ids in flow conditions) — the VRB-V1 conventions
   carry over unchanged.
5. **Teardown order matters more than in VRB-V1**: side-effect objects
   (tasks, logs, ledger entries) must be deleted before their orders;
   reverse-order cleanup is assumed.
