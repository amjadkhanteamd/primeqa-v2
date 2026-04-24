---
id: case_escalation
title: Case Escalation Patterns in Service Cloud
keywords: [escalate, escalation, case, isescalated, sla, milestone, service level, case_sla, escalation_start_time]
objects: [Case, Case_SLA__c, Escalation__c, CaseHistory, CaseMilestone]
token_budget: 1200
version: v1
---

# Case Escalation in Service Cloud

## Object semantics

**Case.IsEscalated** is a Boolean. Critical behaviours:
- Does NOT auto-clear on status change, owner change, or case closure
- Must be explicitly set to false if a test needs to reset it
- Setting to true via API works immediately; Flow-triggered escalation is async
- CaseHistory captures the field change with Field='IsEscalated'

**Case.Escalation_Start_Time** (if present as custom field):
- May or may not be populated depending on org automation
- Do not assert exact timestamp; assert not-null if testing that escalation fired

**Case_SLA__c** (common custom object):
- Links Case to SLA milestone configuration
- Lookup from Case via custom field (name varies: `SLA__c`, `Active_SLA__c`, etc.)
- Do not assume field names; query metadata or reference from Jira context

**Escalation__c** (common custom object):
- Stores the escalation event, often with owner, timestamp, reason
- Triggered by Flow or Process Builder when IsEscalated flips true
- Lookup back to Case via custom field

## Common test patterns

### Pattern 1: verify manual escalation
1. Create a Case with IsEscalated=false (default)
2. Update Case SET IsEscalated=true
3. Verify Case.IsEscalated=true
4. Verify CaseHistory row exists with Field='IsEscalated', NewValue='true'

### Pattern 2: verify Flow-triggered escalation
Flow-triggered escalation runs asynchronously after a threshold condition.
1. Create a Case that meets the trigger condition (e.g. Priority='High', Status='New')
2. Wait for Flow to execute (or trigger manually in test)
3. Query Case with `IsEscalated=true`
4. If Escalation__c is used: verify related Escalation__c record exists
5. Do NOT assume exact timing of Escalation_Start_Time

### Pattern 3: verify escalation does NOT clear on close
1. Create Case with IsEscalated=true
2. Update Case SET Status='Closed'
3. Verify Case.IsEscalated is STILL true (important — does not auto-clear)
4. Verify Case.IsClosed=true (formula from Status)

### Pattern 4: negative validation (the rejection path must exist)
Escalation requirements almost always have validation rules that block
incorrect or partial escalation state. For any covered requirement,
**generate at least one negative_validation test** that proves the
rejection path fires. Examples:

- Attempt to create `Escalation__c` without its required parent Case
  lookup → expect Salesforce to REQUIRED_FIELD_MISSING.
- Attempt to set `Case.IsEscalated=true` without the SLA record in
  required state (per the org's validation rule) → expect validation
  rule error.
- Attempt to close a Case that is still in escalation without the
  required resolution field → expect validation rule error.

Emit these as `coverage_type: "negative_validation"` with
`expect_fail: true` on the step that Salesforce should reject. The
test passes when the step fails with the expected error, NOT when it
succeeds.

### Pattern 5: boundary (escalation threshold)
Case escalation requirements usually have a threshold (SLA duration,
priority level, minimum record count). Generate a
`coverage_type: "boundary"` test that:
1. Creates a Case that does NOT meet the threshold (e.g. Priority='Low'
   when threshold is 'High') and verifies Escalation__c is NOT created.
2. Creates a Case that exactly meets the threshold and verifies
   Escalation__c IS created.

## Common pitfalls

- **Don't assume auto-clear**: IsEscalated persists across status changes, owner changes, and closure. If the test expects it to clear, something explicitly must set it to false.
- **Async Flow timing**: Flow-triggered escalation is not immediate. Test needs to either trigger manually or poll with retry.
- **Custom field naming**: SLA and Escalation object fields vary by org. Reference metadata, don't hardcode.
- **IsClosed is formula**: IsClosed derives from Status. Never include IsClosed in create/update payload.
- **CaseMilestone vs Case_SLA__c**: CaseMilestone is standard Salesforce Entitlements object; Case_SLA__c is a common custom implementation. Don't confuse them.
- **Escalation via assignment rules**: Some orgs escalate via assignment rule reassignment to a queue, not IsEscalated flag. Check the Jira spec for which pattern applies.

## When this pack applies

Use this pack for test generation when the requirement involves:
- Manual or automated Case escalation
- SLA milestone verification
- Service Cloud workflows with escalation triggers
- Testing that escalation flags persist or propagate correctly

## Coverage expectations for escalation requirements

When generating a test plan that touches Case escalation, aim for the
full coverage mix — do NOT skip negative_validation just because the
happy path is well-understood. Minimum:

- **1 × positive**: the full end-to-end escalation succeeds with all
  side effects (CaseHistory, Escalation__c, Account field updates).
- **1 × negative_validation**: validation rule or missing required
  field blocks the escalation — see Pattern 4 above.
- **1 × boundary**: the threshold check (Priority / SLA duration /
  Status precondition) — see Pattern 5 above.
- **1 × edge_case OR regression**: escalation behaviour on
  already-escalated / already-closed / reassigned Cases — see
  Pattern 3 above.

If you emit fewer than 4 coverage types for an escalation requirement,
you're almost certainly missing one of the above.
