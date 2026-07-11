# Plimsol Validation Benchmark V1

## Purpose

Controlled benchmark fixture for evaluating Plimsol's ability to generate QA
test cases from a business requirement and Salesforce org metadata. The fixture
creates a custom object with 10 validation rules of progressively increasing
complexity, each targeting a distinct reasoning capability.

## Deployment

| Item | Value |
|------|-------|
| Date | 2026-07-08 |
| Org alias | `primeqa-sandbox` |
| Username | `amjad.khan@teamd.co.in.primeqa` |
| Org type | Sandbox |
| Status | **Deployed and verified** |
| SFDX source | `sandbox_fixtures/pls_benchmark_v1/` |

## What was created

### Custom object

- **API Name**: `PLS_BM_Deal__c`
- **Label**: Plimsol Benchmark Deal
- **Name field**: Auto Number (`DEAL-{00000}`)

### Custom fields (12)

| # | API Name | Type | Notes |
|---|----------|------|-------|
| 1 | `PLS_BM_Deal_Type__c` | Picklist | Enterprise, SMB, Partner |
| 2 | `PLS_BM_Stage__c` | Picklist | Draft, Qualification, Proposal, Negotiation, Contract Review, Approved, Rejected |
| 3 | `PLS_BM_Deal_Value__c` | Currency(16,2) | |
| 4 | `PLS_BM_Discount__c` | Percent(5,2) | |
| 5 | `PLS_BM_Approval_Reason__c` | Long Text Area | |
| 6 | `PLS_BM_Contract_Number__c` | Text(50) | |
| 7 | `PLS_BM_Contract_Start_Date__c` | Date | |
| 8 | `PLS_BM_Risk_Level__c` | Picklist | Low, Medium, High, Critical |
| 9 | `PLS_BM_Compliance_Approved__c` | Checkbox | Default: false |
| 10 | `PLS_BM_Override_Reason__c` | Text Area | |
| 11 | `PLS_BM_External_Reference__c` | Text(30) | |
| 12 | `PLS_BM_Expected_Close_Date__c` | Date | |

### Record types (2)

| Developer Name | Label |
|----------------|-------|
| `PLS_BM_Enterprise` | Enterprise Benchmark Deal |
| `PLS_BM_Standard` | Standard Benchmark Deal |

### Permission set

- **API Name**: `PLS_BM_Deal_Access`
- Full CRUD on the object + read/edit FLS on all 12 fields + record type visibility

## Validation rule inventory

| # | API Name | Capability tested | Active |
|---|----------|-------------------|--------|
| 1 | `PLS_BM_VR01_Positive_Deal_Value` | Simple boundary (positive number, blank allowed) | Yes |
| 2 | `PLS_BM_VR02_Approval_Reason` | Conditional required field (threshold with exact boundary) | Yes |
| 3 | `PLS_BM_VR03_High_Value_Deal` | Compound boolean (AND + OR across field types) | Yes |
| 4 | `PLS_BM_VR04_Contract_Number` | Picklist-driven required field | Yes |
| 5 | `PLS_BM_VR05_Approved_Lock` | Change detection (ISCHANGED + PRIORVALUE) | Yes |
| 6 | `PLS_BM_VR06_Contract_Start_Date` | Date boundary (mandatory + not-in-past) | Yes |
| 7 | `PLS_BM_VR07_Critical_Risk` | Multiple failure conditions (both must pass) | Yes |
| 8 | `PLS_BM_VR08_Enterprise_Discount` | Record type context (rule applies to one RT only) | Yes |
| 9 | `PLS_BM_VR09_External_Reference` | Text pattern (REGEX) | Yes |
| 10 | `PLS_BM_VR10_Enterprise_Approval` | Composite business logic (multi-field stage-transition gate) | Yes |

## Implementation decisions

1. **VR05 (Approved Lock)**: Uses `ISPICKVAL(PRIORVALUE(PLS_BM_Stage__c), "Approved")` combined with `ISCHANGED(PLS_BM_Deal_Value__c)`. This blocks Deal Value changes on records that are already Approved while allowing creation (PRIORVALUE returns null on insert, so ISPICKVAL is false) and unrelated field edits (ISCHANGED is false when Deal Value doesn't change).

2. **VR08 (Enterprise Discount)**: Uses `RecordType.DeveloperName` rather than a hardcoded RecordTypeId for org-portability.

3. **VR10 (Enterprise Approval)**: Uses `ISCHANGED(PLS_BM_Stage__c)` so the rule only fires when Stage is transitioning to Approved, not when editing unrelated fields on an already-Approved record. On insert, ISCHANGED returns false, so the rule does not fire on create (the spec says "changing to Approved" which implies an update).

4. **Picklist restriction**: All picklists use `<restricted>true</restricted>` to prevent freeform values, ensuring validation rules can rely on the defined value set.

5. **Percent storage**: Salesforce stores percent fields as decimals (20% = 0.20 internally). All VR formulas compare against decimal values (e.g., `> 0.20` for the 20% boundary).

6. **Auto Number format**: `DEAL-{00000}` provides a clean, identifiable name without requiring manual entry.

## Deviations from specification

None. All requirements implemented as specified.
