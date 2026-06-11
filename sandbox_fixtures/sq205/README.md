# SQ-205 sandbox fixture — Case escalation

Deployable metadata for the SQ-205 requirement (Case escalation chain), built
2026-06-11 as the live fixture for the automation-effect / state-transition
test vertical (D-210) and to make SQ-205 itself generate tests.

## What it contains
- `Case_SLA__c` — master-detail to Case (cascade delete keeps the org clean);
  `SLA_Start__c` (DateTime), `Status__c` (Active/Met/Breached, default Active).
- `Escalation__c` — master-detail to Case + lookup to Account;
  `Status__c` (Open/Closed, default Open), `Priority__c` (High/Medium/Low).
- `Account.Last_Escalation_Date__c` (Date).
- Flow `SQ205_Create_Case_SLA` (Active): Case AFTER CREATE → creates the
  Case_SLA__c clock record.
- Flow `SQ205_Escalation_Effects` (Active): Escalation__c AFTER CREATE →
  parent Case Status = `Escalated`, Account.Last_Escalation_Date__c = today.

## Deliberate deviations from the requirement's letter (verify + accept/reject)
1. **Flow A has no `Priority = High` entry condition** — the test engine
   creates trigger records with required-field padding only and cannot yet
   stage trigger-state fields; an entry condition would make every generated
   test miss the flow. Tighten later when trigger-state staging lands.
2. **`Escalated` instead of `In Escalation`** — the org's standard Case
   Status already contains `Escalated`; reusing it avoids editing the
   restricted standard picklist.
3. **The time-based SLA-breach path (AC2) is NOT built** — a scheduled-path
   flow cannot be observed synchronously by tests; out of fixture scope v1.

## Deploy / re-deploy
    sf project deploy start -o primeqa-sandbox -d sandbox_fixtures/sq205/force-app
