# FB-V1 — Org Fixture (as built, Wave 0)

The deployed benchmark org, recorded from the actual SFDX source. Supersedes
the design-level [`FIXTURE_SKETCH.md`](FIXTURE_SKETCH.md) as the factual
record; deviations from the sketch are listed at the end. Machine-readable
spec: [`benchmark-v1.json`](benchmark-v1.json). Deployable source:
`sandbox_fixtures/pls_fb_benchmark_v1/` (repo root).

## Deployment record

| Item | Value |
|---|---|
| Date | 2026-07-11 |
| Org alias | `primeqa-sandbox` (username `amjad.khan@teamd.co.in.primeqa`) |
| Org type | Sandbox (also hosts the frozen VRB-V1 fixture — disjoint, no cross-references) |
| Components | **53/53 deployed, 0 errors** (v59.0 metadata) |
| Live verification | 46/46 org-truth checks passed same day ([`FLOW_IMPLEMENTATION.md`](FLOW_IMPLEMENTATION.md) §Verification) |
| Status | **Deployed and verified** (benchmark NOT frozen — Wave 0 only) |

## Objects (5)

All auto-number names, activities/history off, declarative only.

### `PLS_FB_Order__c` — primary (ORD-{00000})

| Field | Type | Written by | Notes |
|---|---|---|---|
| `PLS_FB_Status__c` | Picklist (restricted): Draft *(default)*, Submitted, Confirmed, Fulfilled, Cancelled | tests | lifecycle spine |
| `PLS_FB_Amount__c` | Currency(16,2) | tests | FL03 bands; FL14 threshold |
| `PLS_FB_Priority__c` | Picklist (restricted): Low, Standard, High — **no field default** | tests / **FL01** | the field default is FL01's job |
| `PLS_FB_Tier__c` | Picklist (restricted): Bronze, Silver, Gold, Platinum | **FL03** | read-only FLS |
| `PLS_FB_External_Ref__c` | Text(30) | tests / **FL02** | VR01 validates the normalised form |
| `PLS_FB_Customer_Email__c` | Email | tests | FL15 recipient; FL15 entry guard |
| `PLS_FB_Promised_Date__c` | Date | tests | realism input (no flow reads it) |
| `PLS_FB_SLA_Deadline__c` | Date | **FL08** | read-only FLS |
| `PLS_FB_Duplicate_Flag__c` | Checkbox | **FL06** | read-only FLS |
| `PLS_FB_Escalated__c` | Checkbox | **FL10** | read-only FLS |
| `PLS_FB_Reopened__c` | Checkbox | **FL09** | read-only FLS |
| `PLS_FB_Fulfilled_Date__c` | Date | **FL12** | read-only FLS |
| `PLS_FB_Order_Total__c` | Currency(16,2) | **FL07** | read-only FLS |
| `PLS_FB_Line_Count__c` | Number(18,0) | **FL07** | read-only FLS |
| `PLS_FB_Ledger_Code__c` | Text(20), optional | tests | FL13's fault key: blank ⇒ ledger create fails |

### `PLS_FB_Order_Line__c` — child (LINE-{00000}, sharing ControlledByParent)

`PLS_FB_Order__c` (Master-Detail → Order, relationship `Order_Lines`),
`PLS_FB_Quantity__c` Number(16,2), `PLS_FB_Unit_Price__c` Currency(16,2),
`PLS_FB_Line_Total__c` **formula** Currency = Quantity × Unit Price
(blanks as zero).

### `PLS_FB_Fulfilment_Task__c` — side-effect object (TASK-{00000})

`PLS_FB_Order__c` (Lookup, relationship `Fulfilment_Tasks`), `PLS_FB_Type__c`
Picklist (Confirmation, Escalation), `PLS_FB_Status__c` Picklist (Open,
Completed, Cancelled — no default; writers set it explicitly),
`PLS_FB_Due_Date__c` Date.

### `PLS_FB_Audit_Log__c` — evidence sink (LOG-{00000})

`PLS_FB_Order__c` (Lookup, relationship `Audit_Logs`), `PLS_FB_Kind__c`
Picklist (AsyncEnrichment, LedgerFault, Reopen, **CloseoutFault** — added vs.
sketch for SF01's fault path), `PLS_FB_Detail__c` Long Text Area(32768).

### `PLS_FB_Ledger_Entry__c` — fault target (LED-{00000})

`PLS_FB_Order__c` (Lookup, relationship `Ledger_Entries`),
`PLS_FB_Ledger_Code__c` Text(20) **universally required** (the fault
mechanism), `PLS_FB_Amount__c` Currency(16,2). No flow triggers on this
object.

## Non-flow automation

| Artifact | Detail |
|---|---|
| VR `PLS_FB_VR01_External_Ref_Format` (active) | `NOT(ISBLANK(ref)) && NOT(REGEX(ref, "FB-[0-9]{6}"))` — fires on populated, non-canonical refs. FL02 (before-save) repairs case/whitespace **before** this rule evaluates. |
| Approval process `PLS_FB_Large_Order_Approval` (active) | One step, **ad-hoc approver** (FL14 passes the order's owner explicitly — no hardcoded usernames, org-portable), no entry criteria (the flow owns the gate), no field-update actions, `recordEditability` AdminOnly, final-approval lock. |
| Permission set `PLS_FB_Access` | Full CRUD on all five objects; **flow-computed Order fields + the Line Total formula are read-only FLS** so any value on them is unambiguously automation-produced. |

## The benchmark numbers (live in metadata, never in requirement text)

| Constant | Value | Where it lives |
|---|---|---|
| Tier bands | Platinum ≥ 250,000; Gold ≥ 50,000; Silver ≥ 10,000; else Bronze (incl. blank) | FL03 decision (first-match order) |
| Confirmation task due offset | trigger date + 3 days | FL04 formula |
| SLA offset | submission date + 5 days | FL08 formula |
| Escalation delay | 2 days after entering Submitted | FL10 scheduled path |
| Approval threshold | Amount ≥ 100,000 | FL14 entry conditions |
| Reference format | `FB-` + 6 digits, uppercase | VR01 regex |

## Implementation decisions

1. **Explicit `triggerOrder` on the five before-save Order flows**
   (FL01=10, FL02=20, FL03=30, FL06=40, FL08=50). Load-bearing for one pair:
   FL06's duplicate lookup must see FL02's normalised value (verified live:
   a lowercase duplicate of a canonical ref is flagged). The rest are pinned
   so same-event ordering is never run-dependent.
2. **FL15 uses the Flow core Send Email action, not an email alert.** The
   Metadata API rejects `emailField` as an alert recipient type, so the
   sketched alert+classic-template mechanism is not source-deployable. The
   Send Email action (address = `Customer_Email__c`, subject/body as flow
   text templates) preserves the identical observable behaviour: an email,
   no queryable record trace. FL15 additionally gates on
   `Customer_Email__c` present so address-less confirmations cannot fault
   the send.
3. **FL05 is Get Records → any-open decision → filtered Update Records** —
   the Get exercises the data element and feeds the decision; the update
   applies the same scope filter. (The loop-modify-collect pattern is
   exercised in SF01 instead; duplicating it in FL05 would blur the
   one-capability-per-flow rule.)
4. **Same-record after-save writes** (`$Record` + Update Records) occur in
   exactly three designed places: FL09 (Reopened), FL10's scheduled path
   (Escalated), FL12 (Fulfilled Date). Recursion is bounded: the recursive
   save re-fires only the idempotent before-save flows (FL02/FL03), and each
   writer's entry conditions are false on its own echo (verified live).
5. **FL10's scheduled path re-reads the record** (Get by Id) and decides
   "still Submitted" against **current** state at execution time, not the
   trigger-time snapshot — faithful to the design's "if still Submitted".
6. **Audit `Detail__c` is Long Text Area** (sketch said Text Area) so
   platform fault messages fit untruncated.
7. **No record types, layouts, queues, or custom metadata** — nothing in the
   benchmark needs them (record-type context is VRB-V1's dimension; repeating
   it here would duplicate, not extend, coverage).

## Pre-freeze org state notes

- **FL10 sentinel:** order `ORD-00023` (id `a0pIp0000011KBnIAM`) was driven
  to Submitted on 2026-07-11 and left in place; its scheduled path fires
  ~2026-07-13. Expected on firing: `Escalated__c = true` + one open
  Escalation task. Verify, record in the Wave-4 notes, then delete the
  order. This is the only benchmark record intentionally left in the org.
- All other characterization records were torn down (children first, then
  orders; 23/23 deleted).
