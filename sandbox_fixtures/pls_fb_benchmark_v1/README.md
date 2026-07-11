# PLS FB Benchmark V1 — SFDX Fixture

Deployable source for the **Flow Benchmark V1 (FB-V1)** org fixture. Design
and documentation live in `benchmark/flows/v1/` at the repo root; this
package is the org's restore point. Entirely declarative — no Apex.

## Contents

- 5 custom objects (`PLS_FB_Order__c`, `PLS_FB_Order_Line__c`,
  `PLS_FB_Fulfilment_Task__c`, `PLS_FB_Audit_Log__c`,
  `PLS_FB_Ledger_Entry__c`) with 29 custom fields
- 1 validation rule (`PLS_FB_VR01_External_Ref_Format`) — the deliberate
  order-of-execution counterpart to flow FL02
- 16 flows (FL01–FL15 + subflow SF01), all Active
- 1 approval process (`PLS_FB_Large_Order_Approval`, active, ad-hoc approver)
- 1 permission set (`PLS_FB_Access`)

## Deployment

```bash
cd sandbox_fixtures/pls_fb_benchmark_v1

# validate without changing the org
sf project deploy start --dry-run -o <sandbox-alias> --wait 15

# deploy
sf project deploy start -o <sandbox-alias> --wait 20

# grant the integration user access
sf org assign permset -n PLS_FB_Access -o <sandbox-alias>
```

Target must be a **sandbox** (runs create and delete records). First deployed
2026-07-11 to `primeqa-sandbox` (53/53 components, 0 errors); the same org
hosts the frozen VRB-V1 fixture — the two share nothing (disjoint objects,
no cross-references).

## Notes

- Flows deploy **Active** (sandboxes don't require flow test coverage).
- The five before-save Order flows carry explicit `triggerOrder` values
  (FL01=10, FL02=20, FL03=30, FL06=40, FL08=50) — FL06's duplicate match must
  see FL02's normalized value; the rest are pinned for determinism.
- Sandbox email deliverability ("System email only" by default) may suppress
  FL15's outbound mail; the flow still executes. Irrelevant to the benchmark:
  FL15 is the evidence-limit control and its effect is unobservable by design.
- No test data ships with the fixture: benchmark runs stage their own records
  and tear them down. (One long-lived sentinel order may exist pre-freeze for
  the FL10 two-day scheduled-path drill — see `benchmark/flows/v1/ORG_FIXTURE.md`.)
