# FB-V1 — Salesforce Flow Capability Taxonomy

The feature survey behind the benchmark surface. Salesforce Flow is one
authoring tool over many distinct runtime behaviours; a benchmark that treated
"a flow" as one capability would measure nothing. This taxonomy groups Flow
functionality into capability areas, marks what FB-V1 exercises, and records
*why* the exclusions are excluded.

Legend: **[V1:FLnn]** = exercised by that benchmark flow; **[V2]** = deferred
to a future version with the reason stated; **[implicit]** = exercised as a
supporting element inside other flows rather than as a dedicated control.

---

## Area A — Trigger contexts (when and why a flow runs)

The single most important axis: two flows with identical logic behave
completely differently depending on trigger context, because the context
determines *what the flow can see, what it can do, and when its effects
become observable*.

| Capability | Runtime meaning | FB-V1 |
|---|---|---|
| Record-triggered, **before-save** (fast field update) | Runs after record load, **before** custom validation rules and before the record is written; may only mutate `$Record`; effects are visible in the saved record itself | **[V1:FL01–FL03, FL08]** |
| Record-triggered, **after-save** | Runs after the record is written (pre-commit); may perform DML on other records, call actions and subflows; effects are visible as *other* state changes | **[V1:FL04–FL07, FL09, FL12–FL15]** |
| Record-triggered, **on delete** | Before-delete context; `$Record` exists but is going away | **[V2]** — worth a control, but the delete-fixture lifecycle (stage → delete → observe survivors) adds ceremony without adding a *reasoning* class beyond FL05's fan-out |
| Entry conditions + **"only when updated to meet the condition"** | ISCHANGED-style transition semantics on the trigger itself — the flow fires on *entering* a state, not on *being in* it | **[V1:FL04, FL09, FL14]** — the direct heir of VRB-V1's transition IR |
| **Scheduled path** on a record-triggered flow | The trigger enqueues a future execution (e.g. "2 days after…, if still…"); the effect happens outside any request window | **[V1:FL10]** — a designed evidence-limit control |
| **Asynchronous path** ("Run Asynchronously") | The effect happens after the transaction commits, seconds-to-minutes later, in a separate transaction | **[V1:FL11]** |
| **Scheduled flow** (standalone, cron-like) | Runs at a schedule over a queried record set; no triggering record at all | **[V2]** — the time-travel honesty question is already carried by FL10 with a cleaner anchor; a standalone schedule adds batch semantics (a V2 area of its own) |
| **Platform-event-triggered flow** | Fires on an event message, not a record save; eventually consistent; no `$Record__Prior` | **[V2]** — requires an event-publish surface in the harness *and* compounds the async-evidence question FL11 already isolates; two new mechanisms in one control is bad instrument design |
| **Screen flow** | User-interactive, UI-driven, session-scoped | **[V2, likely never in this family]** — there is no API-observable execution surface; testing screen flows is a UI-automation problem, a different instrument category |
| **Autolaunched flow** (no trigger) | Invoked by another flow, Apex, or REST | **[V1:FL12, implicit]** — exercised as the subflow target, its natural product role |

## Area B — Logic elements (what a flow decides)

| Capability | Runtime meaning | FB-V1 |
|---|---|---|
| **Decision** element, single outcome + default | Two-way branch | **[V1:FL02]** |
| **Decision** element, multi-outcome | N-way exclusive branch with ordered evaluation and a default outcome — outcome *order* matters when conditions overlap | **[V1:FL03]** (bands), **[V1:FL12]** (composed) |
| **Assignment** element | Variable / `$Record` field mutation | **[implicit — every flow]** |
| **Formula resource** | Computed value (arithmetic, date arithmetic, text functions) evaluated at element use | **[V1:FL08]** (date arithmetic), **[implicit in FL03, FL04]** |
| **Variables, constants, record variables, collections** | State inside a run | **[implicit]** |
| **Loop** element + collection processing | Iterate a record collection; aggregate, filter, transform | **[V1:FL07]** (aggregate), **[V1:FL12]** (iterate-and-update) |
| **Transform** element | Declarative collection mapping (newer authoring sugar over loop+assignment) | **[V2]** — same runtime class as Loop; a control would measure the *parser's* XML coverage, not a new reasoning capability. Worth adding to V2 precisely as a parser-coverage control |
| `$Record__Prior` | The pre-save image inside the flow's own conditions/logic | **[V1:FL09]** — prior-state reasoning *inside* the flow body, distinct from entry-condition transition semantics |

## Area C — Data elements (what a flow reads and writes)

| Capability | Runtime meaning | FB-V1 |
|---|---|---|
| **Get Records** | Query other records; behaviour becomes a function of pre-existing org data | **[V1:FL06]** (existence-dependent branch), **[V1:FL05, FL12]** (fan-out target sets) |
| **Create Records**, single | A new record appears that the test never posted | **[V1:FL04]** |
| **Create Records**, from collection | Bulk creation | **[V2]** — cardinality reasoning is carried by FL07; bulk-DML is a governor-domain concern |
| **Update Records** — the triggering record (after-save) | Same-record re-write after save (extra save cycle) | **[V2]** — deliberately excluded from V1: it re-enters the order of execution (recursion territory) and would contaminate every other control's isolation |
| **Update Records** — related / queried records | Cross-record fan-out | **[V1:FL05]** (children), **[V1:FL07]** (child→parent), **[V1:FL12]** (via subflow) |
| **Delete Records** | Records disappear | **[V2]** — same reasoning class as create/update evidence (absence assertion) but with teardown-confounding risk in V1's shared fixture |
| Cross-object field traversal (`$Record.Parent__r.Field`) | Read-through relationships in conditions | **[implicit in FL07]** |

## Area D — Interaction and action elements (what a flow does beyond DML)

| Capability | Runtime meaning | FB-V1 |
|---|---|---|
| **Subflow** invocation | Composition; behaviour lives in a different metadata artifact than the trigger | **[V1:FL12]** |
| **Email alert / Send Email** action | An effect with **no record trace** (no queryable artifact from the API's viewpoint) | **[V1:FL15]** — the designed evidence-limit control |
| **Submit for Approval** action | Bridges into the approval-process mechanism; observable via `ProcessInstance` and record lock | **[V1:FL14]** |
| **Custom notification** action | In-app notification; no practical API-observable trace | **[V2]** — same honesty class as FL15; one evidence-limit control of this kind is enough for V1 |
| **Apex invocable action** | Escapes declarative semantics entirely — behaviour lives in code | **[V2]** — V1 is deliberately Apex-free so the fixture stays fully declarative and the grounding question stays "can you read Flow metadata", not "can you read Apex" |
| **Outbound message / external callout** | Side effects outside the org | **[V2/never]** — unobservable *and* unsafe in a shared sandbox |

## Area E — Error and transaction semantics (what happens when a flow fails)

| Capability | Runtime meaning | FB-V1 |
|---|---|---|
| **Fault path / fault connector** | A designed alternative route when an element fails; converts an abort into a handled behaviour | **[V1:FL13]** (dedicated, externally reachable), **[V1:FL12]** (composed) |
| Unhandled flow fault | The save fails with a flow error — a *rejection* whose "message" is a flow fault, not a VR message | **[implicit]** — characterised during the program (it is what FL13 looks like with the fault connector removed), not a designed control |
| **Roll Back Records** element | Explicit rollback — screen flows only | **[V2/never per Area A screen-flow exclusion]** |
| Transactional boundary (before-save error rolls back everything; async path is a separate transaction) | Which effects survive a partial failure | **[implicit in FL11, FL13]** |
| Recursion / re-trigger control | A flow's update re-triggering itself or others | **[V2]** — deliberately excluded (see Update-triggering-record above) |
| Governor limits / bulkification | Behaviour at volume | **[V2]** — a different instrument category (performance), orthogonal to reasoning correctness |

## Area F — Execution context (who and as-what a flow runs)

| Capability | Runtime meaning | FB-V1 |
|---|---|---|
| System vs. user context (sharing) | Whether the flow sees/writes records the *triggering user* could not | **[V2]** — this is the USER differential dimension named at VRB-V1 freeze; it deserves its own fixture with permission variance, not a corner of V1 |
| `$User` / `$Permission` in conditions | Actor-dependent branching | **[V2]** — same deferral, same reason |
| Order of execution vs. other automations | Before-save flows precede validation rules; after-save flows follow them; two automations can share a trigger | **[V1:FL02]** (flow-repairs-before-VR, the adversarial control), **[V1:FL04+FL15]** (co-triggered pair) |

---

## Summary of the taxonomy → surface mapping

Fifteen controls cover: both save-cycle positions (before/after), transition
entry semantics, prior-state logic, multi-way decisions, formula computation,
loops/aggregation, Get/Create/Update data elements, cross-record fan-out in
both directions (parent→children, child→parent), data-dependent branching,
scheduled + asynchronous paths, subflow composition, fault handling, approval
invocation, email, and two deliberate order-of-execution interactions.

Deferred with recorded reasons: delete triggers, standalone scheduled flows,
platform events, screen flows, Transform element, bulk/collection DML,
same-record after-save update (recursion), Delete Records, custom
notifications, Apex actions, rollback, governor behaviour, and the entire
actor/sharing axis (the USER differential — a future benchmark family's
centrepiece, per the VRB-V1 architecture map).
