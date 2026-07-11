# VRB-V1 — The Requirement (verbatim)

**This is the benchmark's single input requirement, frozen verbatim.** During
the V1 program it lived as requirement **#315** (source: manual) in the
Plimsol database; it is recorded here because a database row is not a
reproducible artifact. A rerun MUST provide this text unchanged — the
acceptance-criteria block below is exactly what generation consumes, including
its object/field/rule-name projection tail (Plimsol's requirement sync appends
that projection; if recreating the requirement through the UI, verify the
stored acceptance criteria match this block byte-for-byte before running).

The requirement is deliberately **qualitative**: it names no thresholds, no
dates, no record types. Deriving the concrete boundaries from the org's own
rules — rather than from the requirement — is one of the central capabilities
under test. It is also deliberately **ambiguous** in places ("Enterprise
deals" plausibly names both the `PLS_BM_Deal_Type__c` picklist value and the
`PLS_BM_Enterprise` record type); honest handling of that ambiguity is part of
the instrument.

---

## Summary

Enterprise Deal Approval Requests

## Description

As a Sales Manager, I want to create and progress Enterprise Deal Approval Requests through the deal lifecycle so that high-value, discounted, and high-risk deals are properly controlled before approval.

Users should be able to create a deal, enter the commercial details, progress it through Draft, Qualification, Proposal, Negotiation, Contract Review and Approved stages, or mark it as Rejected.

Deal values must be commercially valid. Higher discounts may require additional justification, and high-value deals with significant discounts or risk must receive compliance approval.

Contract details must be captured when the deal reaches the appropriate contracting stages.

Critical-risk deals require additional approval and justification.

Enterprise deals are subject to stricter discount controls than standard deals.

External references, when provided, must follow the company's required reference format.

Once a deal has been approved, its approved commercial value must be protected from later modification.

Before a large Enterprise deal can move to Approved, all required commercial, compliance, risk and contract conditions must be satisfied.

Generate comprehensive functional test cases covering positive scenarios, negative scenarios, boundary conditions, relevant record state transitions, and combinations of business conditions.

## Acceptance criteria (the exact generation input)

```text
As a Sales Manager, I want to create and progress Plimsol Benchmark Deal records (Salesforce object API name: PLS_BM_Deal__c) through the deal lifecycle so that high-value, discounted, and high-risk deals are properly controlled before approval.

Users should be able to create a Plimsol Benchmark Deal, enter the commercial details, progress it through Draft, Qualification, Proposal, Negotiation, Contract Review and Approved stages, or mark it as Rejected.

Deal values must be commercially valid. Higher discounts may require additional justification, and high-value deals with significant discounts or risk must receive compliance approval.

Contract details must be captured when the deal reaches the appropriate contracting stages.

Critical-risk deals require additional approval and justification.

Enterprise deals are subject to stricter discount controls than standard deals.

External references, when provided, must follow the company's required reference format.

Once a deal has been approved, its approved commercial value must be protected from later modification.

Before a large Enterprise deal can move to Approved, all required commercial, compliance, risk and contract conditions must be satisfied.

Generate comprehensive functional test cases covering positive scenarios, negative scenarios, boundary conditions, relevant record state transitions, and combinations of business conditions.


PLS_BM_Deal__c
Plimsol Benchmark Deal

Fields (22)
PLS_BM_Deal__c.CreatedById
PLS_BM_Deal__c.CreatedDate
PLS_BM_Deal__c.Id
PLS_BM_Deal__c.IsDeleted
PLS_BM_Deal__c.LastModifiedById
PLS_BM_Deal__c.LastModifiedDate
PLS_BM_Deal__c.Name
PLS_BM_Deal__c.OwnerId
PLS_BM_Deal__c.PLS_BM_Approval_Reason__c
PLS_BM_Deal__c.PLS_BM_Compliance_Approved__c
PLS_BM_Deal__c.PLS_BM_Contract_Number__c
PLS_BM_Deal__c.PLS_BM_Contract_Start_Date__c
PLS_BM_Deal__c.PLS_BM_Deal_Type__c
PLS_BM_Deal__c.PLS_BM_Deal_Value__c
PLS_BM_Deal__c.PLS_BM_Discount__c
PLS_BM_Deal__c.PLS_BM_Expected_Close_Date__c
PLS_BM_Deal__c.PLS_BM_External_Reference__c
PLS_BM_Deal__c.PLS_BM_Override_Reason__c
PLS_BM_Deal__c.PLS_BM_Risk_Level__c
PLS_BM_Deal__c.PLS_BM_Stage__c
PLS_BM_Deal__c.RecordTypeId
PLS_BM_Deal__c.SystemModstamp
Validation rules (10)
PLS_BM_Deal__c.PLS_BM_VR01_Positive_Deal_Value
PLS_BM_Deal__c.PLS_BM_VR02_Approval_Reason
PLS_BM_Deal__c.PLS_BM_VR03_High_Value_Deal
PLS_BM_Deal__c.PLS_BM_VR04_Contract_Number
PLS_BM_Deal__c.PLS_BM_VR05_Approved_Lock
PLS_BM_Deal__c.PLS_BM_VR06_Contract_Start_Date
PLS_BM_Deal__c.PLS_BM_VR07_Critical_Risk
PLS_BM_Deal__c.PLS_BM_VR08_Enterprise_Discount
PLS_BM_Deal__c.PLS_BM_VR09_External_Reference
PLS_BM_Deal__c.PLS_BM_VR10_Enterprise_Approval
```
