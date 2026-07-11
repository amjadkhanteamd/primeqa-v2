# FB-V1 — Flow Implementation Record (Wave 0)

The as-built record of the sixteen flow artifacts (fifteen benchmark controls
+ one subflow), and the live verification of each against the design in
[`FLOWS.md`](FLOWS.md). Source of truth for element-level detail is the SFDX
XML in `sandbox_fixtures/pls_fb_benchmark_v1/force-app/main/default/flows/`;
this document is the index.

All flows: `processType=AutoLaunchedFlow`, API v59.0, status **Active**,
deployed 2026-07-11.

Notation: *UTM* = entry conditions with `doesRequireRecordChangedToMeetCriteria`
(“only when a record is updated to meet the condition” — fires on the
transition into the state, not on being in it).

---

## FL01 — `PLS_FB_FL01_Default_Priority`
- **Trigger:** Order, **before-save**, create only. `triggerOrder` 10. No entry filter.
- **Elements:** Decision `Priority_Blank` (`$Record.Priority IsNull`) → Assignment `Set_Default_Priority` (`Priority = Standard`); default outcome ends.
- **Updates:** `$Record` field only (before-save). **Side effects / faults / scheduled paths / subflows:** none.

## FL02 — `PLS_FB_FL02_Normalize_External_Ref`
- **Trigger:** Order, **before-save**, create + update. Entry: `External_Ref IsNull = false`. `triggerOrder` 20.
- **Elements:** Formula `fNormalizedRef = UPPER(TRIM($Record.External_Ref))` → Assignment `Normalize_Ref`.
- **Interaction (designed):** runs before VR01 in the order of execution — repairs case/whitespace so a lowercase-valid ref saves; structurally invalid refs still reach VR01 and reject.

## FL03 — `PLS_FB_FL03_Tier_Banding`
- **Trigger:** Order, **before-save**, create + update. No entry filter. `triggerOrder` 30.
- **Elements:** Decision `Tier_Band`, ordered outcomes (first match wins): `Amount ≥ 250000 → Platinum`; `≥ 50000 → Gold`; `≥ 10000 → Silver`; **default → Bronze** (includes blank Amount). Four Assignments, one per band.

## FL04 — `PLS_FB_FL04_Confirmation_Task`
- **Trigger:** Order, **after-save**, update, UTM `Status = Confirmed`.
- **Elements:** Formula `fDueDate = $Flow.CurrentDate + 3` → Create Records `Create_Confirmation_Task`.
- **Side effect:** one `PLS_FB_Fulfilment_Task__c` (Type Confirmation, Status Open, Due +3d, linked to order).

## FL05 — `PLS_FB_FL05_Cancellation_Sync`
- **Trigger:** Order, **after-save**, update, UTM `Status = Cancelled`.
- **Elements:** Get Records `Get_Open_Tasks` (order's tasks, Status Open, all rows) → Decision `Any_Open_Tasks` → Update Records `Cancel_Open_Tasks` (filter: this order + Status Open → `Status = Cancelled`).
- **Side effect:** set-scoped fan-out; Completed/Cancelled tasks untouched.

## FL06 — `PLS_FB_FL06_Duplicate_Flag`
- **Trigger:** Order, **before-save**, create only. Entry: `External_Ref IsNull = false`. `triggerOrder` 40 (after FL02 — the match must see the normalised value).
- **Elements:** Get Records `Get_Matching_Order` (same External_Ref, `Status ≠ Cancelled`, first row) → Decision `Duplicate_Exists` → Assignment `Flag_Duplicate` (`Duplicate_Flag = true`).

## FL07 — `PLS_FB_FL07_Order_Rollup`
- **Trigger:** **Order Line**, after-save, create + update. No entry filter.
- **Elements:** Get Records `Get_All_Lines` (all lines of `$Record`'s parent) → Loop `Loop_Lines` → Assignment `Accumulate` (`varTotal += Line_Total`; `varCount += 1`) → Update Records `Update_Parent_Order` (parent by Id: `Order_Total = varTotal`, `Line_Count = varCount`).
- **Variables:** `varTotal` Currency(2) = 0, `varCount` Number(0) = 0.

## FL08 — `PLS_FB_FL08_SLA_Stamp`
- **Trigger:** Order, **before-save**, update, UTM `Status = Submitted`. `triggerOrder` 50.
- **Elements:** Formula `fSLADate = $Flow.CurrentDate + 5` → Assignment `Stamp_SLA` (`SLA_Deadline = fSLADate`).

## FL09 — `PLS_FB_FL09_Reopen_Guard`
- **Trigger:** Order, **after-save**, update. Entry: `Status IsChanged = true`.
- **Elements:** Decision `Was_Fulfilled` (**`$Record__Prior.Status = Fulfilled` AND `$Record.Status ≠ Fulfilled`**) → Assignment `Mark_Reopened` (`$Record.Reopened = true`) → Update Records `Update_Order_Record` (`inputReference $Record` — designed same-record write) → Create Records `Create_Reopen_Log` (Audit Log, Kind Reopen).
- **Recursion note:** the self-update changes no Status, so the entry filter blocks the echo.

## FL10 — `PLS_FB_FL10_Stale_Order_Escalation`
- **Trigger:** Order, **after-save**, update, UTM `Status = Submitted`. **No immediate path.**
- **Scheduled path:** `Two_Days_After_Submitted` — +2 days from the trigger event → Get Records `Get_Current_Order` (re-read by Id, **current** state) → Decision `Still_Submitted` → Update Records `Mark_Escalated` (`Escalated = true`) → Create Records `Create_Escalation_Task` (Type Escalation, Status Open, due same day).

## FL11 — `PLS_FB_FL11_Async_Enrichment`
- **Trigger:** Order, **after-save**, update, UTM `Status = Confirmed`. **No immediate path.**
- **Async path** (`pathType AsyncAfterCommit`): Create Records `Create_Async_Log` (Audit Log, Kind AsyncEnrichment) — a separate transaction after commit.

## FL12 — `PLS_FB_FL12_Fulfilment_Orchestrator` (capstone)
- **Trigger:** Order, **after-save**, update, UTM `Status = Fulfilled`.
- **Elements:** Get Records `Get_Open_Task` (first open task) → Decision `Any_Open_Tasks` → *(yes)* **Subflow `Call_Close_Tasks`** (`PLS_FB_SF01_Close_Tasks`, input `OrderId = $Record.Id`) → Decision `Check_Fulfilled_Date` (stamp only if blank — idempotence) → Assignment + Update Records (`$Record.Fulfilled_Date = today`, designed same-record write). *(no)* path joins at the date decision.

## FL13 — `PLS_FB_FL13_Fault_Logged_Ledger`
- **Trigger:** Order, **after-save**, update, UTM `Status = Confirmed`.
- **Elements:** Create Records `Create_Ledger_Entry` (Ledger Entry: code ← the order's **optional** `Ledger_Code`, amount ← Amount) with **fault connector** → Create Records `Create_Fault_Log` (Audit Log, Kind LedgerFault, Detail = `$Flow.FaultMessage`).
- **Fault reachability:** blank `Ledger_Code` on the order ⇒ REQUIRED_FIELD_MISSING on the ledger insert ⇒ fault path; the triggering save still commits.

## FL14 — `PLS_FB_FL14_Approval_Submit`
- **Trigger:** Order, **after-save**, update, UTM `Status = Submitted` **AND** `Amount ≥ 100000`.
- **Elements:** Assignment `Build_Approver_List` (adds `$Record.OwnerId` to text collection `colApproverIds`) → Action `Submit_For_Approval` (core `submit`: objectId, `processDefinitionNameOrId = PLS_FB_Large_Order_Approval`, `nextApproverIds = colApproverIds`, skipEntryCriteria).
- **Side effect:** pending `ProcessInstance` + record lock (approval machinery's objects, not the order's fields).

## FL15 — `PLS_FB_FL15_Confirmation_Email`
- **Trigger:** Order, **after-save**, update, UTM `Status = Confirmed` **AND** `Customer_Email IsNull = false`.
- **Elements:** Action `Send_Confirmation_Email` (core `emailSimple`: address = `$Record.Customer_Email`, subject/body = flow text templates referencing `$Record`).
- **Evidence limit (designed):** no queryable record trace. Co-triggered with FL04/FL11/FL13 on the same transition.

## SF01 — `PLS_FB_SF01_Close_Tasks` (subflow, autolaunched)
- **Input:** `OrderId` (Text).
- **Elements:** Get Records `Get_Open_Tasks` (all open tasks of OrderId) → Loop `Loop_Tasks` → Assignment `Mark_Completed` (item `Status = Completed`; add item to collection `colUpdatedTasks`) → Update Records `Update_Tasks` (`inputReference colUpdatedTasks`) with **fault connector** → Create Records `Create_Closeout_Fault_Log` (Audit Log, Kind CloseoutFault, Detail = `$Flow.FaultMessage`).

---

## Verification (Part 4) — live org truth, 2026-07-11

Method: every synchronously-observable arm driven via the Salesforce API
(create/update/query) against the deployed org, asserted programmatically.
**Result: 46/46 checks passed.** Highlights per design expectation:

| Design expectation | Observed |
|---|---|
| FL01 transformation: posted blank → persisted `Standard`; supplied value untouched | ✅ both arms |
| FL02 order-of-execution: `" fb-123456 "` **saves** as `FB-123456` (flow repairs before VR01); `FB-12` **rejected with VR01's own message**; canonical unchanged | ✅ all three arms |
| FL03 bands + boundaries: 9,999.99→Bronze; 10,000→Silver; 49,999.99→Silver; 50,000→Gold; 249,999.99→Gold; 250,000→Platinum | ✅ 6/6 |
| FL04: exactly one Confirmation task, Open, due RUN_DATE+3; **zero** tasks when created already-Confirmed; **no second task** on unrelated edit while Confirmed | ✅ fire + both suppressions |
| FL05: 2 open tasks → Cancelled; 1 Completed untouched; 0 left Open | ✅ set-scoped |
| FL06: sibling present → flagged; unique ref → clean; **lowercase duplicate flagged** (proves FL02→FL06 ordering) | ✅ 3 arms |
| FL07: 0→1 line total 200/count 1; +line 250.50/2; in-place edit 350.50/2 | ✅ 3 cardinality arms |
| FL08: Submitted → SLA = RUN_DATE+5 | ✅ |
| FL09: legit-path Fulfilled → Draft: flag + Reopen log; never-Fulfilled control: neither | ✅ differential |
| FL10: at save time Escalated=false, no task (**nothing observable in-window, as designed**) | ✅; 2-day drill pending via sentinel ORD-00023 (escalates ~2026-07-13) |
| FL11: AsyncEnrichment log present on first poll after commit (latency below the ~5s polling grain this run — treat as variable, not guaranteed-instant) | ✅ |
| FL12+SF01: open task Completed via subflow, Fulfilled_Date = RUN_DATE, no fault log; short-circuit arm stamps date with zero task writes | ✅ both arms |
| FL13: happy arm ledger entry + no fault log; fault arm **save succeeds**, no ledger entry, one LedgerFault log carrying REQUIRED_FIELD_MISSING | ✅ both arms |
| FL14: 100,000 → pending ProcessInstance; 99,999.99 → none | ✅ boundary pair |
| FL15: send executes without error on the co-triggered save; no record trace (by design) | ✅ (delivery itself unobservable — the point) |

**Not verifiable in-window (recorded honestly):** FL10's scheduled-path firing
(2-day offset — sentinel staged, see [`ORG_FIXTURE.md`](ORG_FIXTURE.md));
SF01's fault connector is dormant in normal operation (no task-level
constraint can fail the update today — see the completeness review).

## Intentional deviations from the design docs

1. **FL15 mechanism**: email alert → Flow core Send Email action (Metadata
   API cannot express an email-field alert recipient). Observable behaviour
   identical. Design docs amended in place with the Wave-0 note.
2. **FL05 shape**: Get → decision → *filtered* Update Records rather than
   loop-modify-collect (that pattern lives in SF01; behaviour byte-identical
   to the design's expected evidence).
3. **Audit `Detail__c`** widened to Long Text Area for untruncated fault
   messages.
4. **`triggerOrder`** pinned on the five before-save Order flows (the design
   left same-event ordering implicit; FL02→FL06 makes it load-bearing).
5. **FL12's second decision** is the Fulfilled-Date idempotence check —
   the design named "amount tier relevant?" as a candidate; the idempotence
   guard adds no unexpected observable behaviour, the tier branch would have.
