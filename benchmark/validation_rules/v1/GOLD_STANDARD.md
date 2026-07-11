# Plimsol Validation Benchmark V1 — Gold Standard

> **CONFIDENTIAL TO EVALUATION.** This document must NOT be provided to Plimsol
> as input. It is the scoring rubric for evaluating Plimsol's generated test
> cases after generation.

---

## VR01 — PLS_BM_VR01_Positive_Deal_Value

**Business condition**: Deal Value must be greater than zero when populated. Blank is allowed.

### Valid partitions (should save)

| # | Scenario | Deal Value |
|---|----------|------------|
| V1.1 | Blank (null) | _(empty)_ |
| V1.2 | Positive value | 100.00 |
| V1.3 | Large positive value | 10,000,000.00 |
| V1.4 | Small positive value | 0.01 |

### Invalid partitions (should block)

| # | Scenario | Deal Value |
|---|----------|------------|
| I1.1 | Zero | 0 |
| I1.2 | Negative value | -1.00 |
| I1.3 | Large negative value | -1,000,000.00 |

### Boundary cases

| # | Scenario | Deal Value | Expected |
|---|----------|------------|----------|
| B1.1 | Exactly zero | 0 | BLOCK |
| B1.2 | Smallest positive (0.01) | 0.01 | SAVE |
| B1.3 | Smallest negative (-0.01) | -0.01 | BLOCK |

### Minimum test cases a strong system should generate: 5
- Blank allowed (V1.1)
- Positive value saves (V1.2)
- Zero blocked (B1.1)
- Negative blocked (I1.2)
- Boundary at 0.01 (B1.2)

### Additional tests an excellent system might generate: 2
- Smallest negative (-0.01) boundary (B1.3)
- Large positive value to confirm no upper-bound issue (V1.3)

---

## VR02 — PLS_BM_VR02_Approval_Reason

**Business condition**: Approval Reason is mandatory when Discount exceeds 20%. Exactly 20% is allowed without a reason.

### Valid partitions (should save)

| # | Scenario | Discount | Approval Reason |
|---|----------|----------|-----------------|
| V2.1 | No discount, no reason | _(blank)_ | _(blank)_ |
| V2.2 | 10% discount, no reason | 10% | _(blank)_ |
| V2.3 | Exactly 20%, no reason | 20% | _(blank)_ |
| V2.4 | 25% discount with reason | 25% | "Volume discount" |
| V2.5 | 50% discount with reason | 50% | "Strategic deal" |

### Invalid partitions (should block)

| # | Scenario | Discount | Approval Reason |
|---|----------|----------|-----------------|
| I2.1 | 21% without reason | 21% | _(blank)_ |
| I2.2 | 50% without reason | 50% | _(blank)_ |
| I2.3 | 100% without reason | 100% | _(blank)_ |

### Boundary cases

| # | Scenario | Discount | Approval Reason | Expected |
|---|----------|----------|-----------------|----------|
| B2.1 | Exactly 20%, no reason | 20% | _(blank)_ | SAVE |
| B2.2 | 20.01%, no reason | 20.01% | _(blank)_ | BLOCK |
| B2.3 | 20.01%, with reason | 20.01% | "Justified" | SAVE |
| B2.4 | 19.99%, no reason | 19.99% | _(blank)_ | SAVE |

### Minimum test cases a strong system should generate: 5
- Below threshold without reason saves (V2.2)
- Exactly 20% without reason saves (B2.1)
- Just above 20% without reason blocks (B2.2)
- Above threshold with reason saves (V2.4)
- Well above threshold without reason blocks (I2.2)

### Additional tests an excellent system might generate: 3
- Blank discount with blank reason saves (V2.1)
- 20.01% with reason saves (B2.3)
- 19.99% boundary saves (B2.4)

---

## VR03 — PLS_BM_VR03_High_Value_Deal

**Business condition**: Compliance Approval required when Deal Value > 1,000,000 AND (Discount > 15% OR Risk Level is High or Critical).

### Valid partitions (should save)

| # | Scenario | Deal Value | Discount | Risk Level | Compliance Approved |
|---|----------|------------|----------|------------|---------------------|
| V3.1 | Below 1M, high discount, no compliance | 500,000 | 20% | Low | false |
| V3.2 | Above 1M, low discount, low risk, no compliance | 2,000,000 | 10% | Low | false |
| V3.3 | Above 1M, high discount, with compliance | 2,000,000 | 20% | Low | true |
| V3.4 | Above 1M, high risk, with compliance | 2,000,000 | 10% | High | true |
| V3.5 | Above 1M, critical risk, with compliance | 2,000,000 | 10% | Critical | true |
| V3.6 | Exactly 1M, high discount, no compliance | 1,000,000 | 20% | Low | false |
| V3.7 | Above 1M, exactly 15%, medium risk, no compliance | 2,000,000 | 15% | Medium | false |

### Invalid partitions (should block)

| # | Scenario | Deal Value | Discount | Risk Level | Compliance Approved |
|---|----------|------------|----------|------------|---------------------|
| I3.1 | Above 1M, high discount, no compliance | 2,000,000 | 20% | Low | false |
| I3.2 | Above 1M, high risk, no compliance | 2,000,000 | 10% | High | false |
| I3.3 | Above 1M, critical risk, no compliance | 2,000,000 | 10% | Critical | false |
| I3.4 | Above 1M, high discount + high risk, no compliance | 5,000,000 | 25% | Critical | false |

### Boundary cases

| # | Scenario | Deal Value | Discount | Risk Level | Compliance Approved | Expected |
|---|----------|------------|----------|------------|---------------------|----------|
| B3.1 | Exactly 1M, 16% discount, no compliance | 1,000,000 | 16% | Low | false | SAVE (value not > 1M) |
| B3.2 | 1,000,001, 16% discount, no compliance | 1,000,001 | 16% | Low | false | BLOCK |
| B3.3 | 1,000,001, exactly 15%, low risk | 1,000,001 | 15% | Low | false | SAVE (15% not > 15%) |
| B3.4 | 1,000,001, 15.01%, low risk | 1,000,001 | 15.01% | Low | false | BLOCK |
| B3.5 | Above 1M, medium risk, low discount | 2,000,000 | 10% | Medium | false | SAVE |

### Minimum test cases a strong system should generate: 7
- Below 1M not affected (V3.1)
- Above 1M, low discount, low risk saves without compliance (V3.2)
- Above 1M, high discount, no compliance blocks (I3.1)
- Above 1M, high risk, no compliance blocks (I3.2)
- Above 1M, high discount, with compliance saves (V3.3)
- Above 1M, high risk, with compliance saves (V3.4)
- Exactly 1M boundary (B3.1)

### Additional tests an excellent system might generate: 4
- Critical risk variant (I3.3/V3.5)
- 15% discount boundary (B3.3/B3.4)
- Both OR branches true + no compliance (I3.4)
- Medium risk not affected (B3.5)

---

## VR04 — PLS_BM_VR04_Contract_Number

**Business condition**: Contract Number mandatory when Stage is Contract Review or Approved.

### Valid partitions (should save)

| # | Scenario | Stage | Contract Number |
|---|----------|-------|-----------------|
| V4.1 | Draft, no contract number | Draft | _(blank)_ |
| V4.2 | Qualification, no contract number | Qualification | _(blank)_ |
| V4.3 | Proposal, no contract number | Proposal | _(blank)_ |
| V4.4 | Negotiation, no contract number | Negotiation | _(blank)_ |
| V4.5 | Contract Review with contract number | Contract Review | "CN-001" |
| V4.6 | Approved with contract number | Approved | "CN-002" |
| V4.7 | Rejected, no contract number | Rejected | _(blank)_ |

### Invalid partitions (should block)

| # | Scenario | Stage | Contract Number |
|---|----------|-------|-----------------|
| I4.1 | Contract Review without contract number | Contract Review | _(blank)_ |
| I4.2 | Approved without contract number | Approved | _(blank)_ |

### Minimum test cases a strong system should generate: 5
- Non-requiring stage without contract number saves (V4.1 or V4.4)
- Contract Review with contract number saves (V4.5)
- Contract Review without contract number blocks (I4.1)
- Approved with contract number saves (V4.6)
- Approved without contract number blocks (I4.2)

### Additional tests an excellent system might generate: 3
- Each non-requiring stage verified (V4.2, V4.3, V4.7)
- Rejected stage without contract number saves (V4.7)
- Draft with contract number provided (should save — the rule doesn't prohibit it)

---

## VR05 — PLS_BM_VR05_Approved_Lock

**Business condition**: Once a deal reaches Approved status, Deal Value cannot be changed. Must allow creation and unrelated field edits.

### Valid partitions (should save)

| # | Scenario | Prior Stage | Current Stage | Deal Value Change? | Other Changes? |
|---|----------|-------------|---------------|-------------------|----------------|
| V5.1 | Create with Approved stage | _(new)_ | Approved | _(initial set)_ | — |
| V5.2 | Edit non-value field on Approved deal | Approved | Approved | No | Yes (e.g., Override Reason) |
| V5.3 | Change Deal Value on Draft deal | Draft | Draft | Yes | — |
| V5.4 | Change Deal Value on Negotiation deal | Negotiation | Negotiation | Yes | — |
| V5.5 | Move Draft to Approved (with value set) | Draft | Approved | No | Stage changes |

### Invalid partitions (should block)

| # | Scenario | Prior Stage | Current Stage | Deal Value Change? |
|---|----------|-------------|---------------|-------------------|
| I5.1 | Change Deal Value on Approved deal | Approved | Approved | Yes (e.g., 100,000 → 200,000) |
| I5.2 | Change Deal Value while moving from Approved to Rejected | Approved | Rejected | Yes |

### State-transition cases

| # | From | To | Value Changed | Expected |
|---|------|----|---------------|----------|
| S5.1 | Draft | Approved | No | SAVE |
| S5.2 | Approved | Approved | Yes | BLOCK |
| S5.3 | Approved | Approved | No | SAVE |
| S5.4 | Approved | Rejected | Yes | BLOCK |
| S5.5 | Approved | Rejected | No | SAVE |
| S5.6 | Draft | Draft | Yes | SAVE |

### Minimum test cases a strong system should generate: 5
- Create with any value saves (V5.1)
- Edit value on non-Approved stage saves (V5.3)
- Edit value on Approved stage blocks (I5.1)
- Edit non-value field on Approved stage saves (V5.2)
- Transition to Approved without changing value saves (V5.5)

### Additional tests an excellent system might generate: 3
- Change value while leaving Approved (I5.2)
- Create directly as Approved (V5.1)
- Edit non-value field while Approved (V5.2 — explicit unrelated field)

---

## VR06 — PLS_BM_VR06_Contract_Start_Date

**Business condition**: When Stage is Approved, Contract Start Date is mandatory and cannot be earlier than TODAY(). Today's date is valid.

### Valid partitions (should save)

| # | Scenario | Stage | Contract Start Date |
|---|----------|-------|---------------------|
| V6.1 | Non-Approved stage, no date | Draft | _(blank)_ |
| V6.2 | Approved with today's date | Approved | TODAY |
| V6.3 | Approved with future date | Approved | TODAY + 30 |
| V6.4 | Non-Approved with past date | Draft | 2020-01-01 |

### Invalid partitions (should block)

| # | Scenario | Stage | Contract Start Date |
|---|----------|-------|---------------------|
| I6.1 | Approved with no date | Approved | _(blank)_ |
| I6.2 | Approved with past date | Approved | YESTERDAY |
| I6.3 | Approved with far-past date | Approved | 2020-01-01 |

### Boundary cases

| # | Scenario | Stage | Contract Start Date | Expected |
|---|----------|-------|---------------------|----------|
| B6.1 | Approved with today | Approved | TODAY | SAVE |
| B6.2 | Approved with yesterday | Approved | TODAY - 1 | BLOCK |
| B6.3 | Approved with tomorrow | Approved | TODAY + 1 | SAVE |

### Minimum test cases a strong system should generate: 5
- Non-Approved without date saves (V6.1)
- Approved without date blocks (I6.1)
- Approved with today saves (B6.1)
- Approved with yesterday blocks (B6.2)
- Approved with future date saves (V6.3)

### Additional tests an excellent system might generate: 2
- Non-Approved with past date saves (V6.4)
- Tomorrow boundary (B6.3)

---

## VR07 — PLS_BM_VR07_Critical_Risk

**Business condition**: Critical Risk deals require both Compliance Approved = true AND Override Reason not blank. Block if either is missing.

### Valid partitions (should save)

| # | Scenario | Risk Level | Compliance Approved | Override Reason |
|---|----------|------------|---------------------|-----------------|
| V7.1 | Non-Critical, neither field set | High | false | _(blank)_ |
| V7.2 | Non-Critical, no risk level | _(blank)_ | false | _(blank)_ |
| V7.3 | Critical with both set | Critical | true | "CEO override" |
| V7.4 | Low risk, neither set | Low | false | _(blank)_ |
| V7.5 | Medium risk, neither set | Medium | false | _(blank)_ |

### Invalid partitions (should block)

| # | Scenario | Risk Level | Compliance Approved | Override Reason |
|---|----------|------------|---------------------|-----------------|
| I7.1 | Critical, neither set | Critical | false | _(blank)_ |
| I7.2 | Critical, compliance but no reason | Critical | true | _(blank)_ |
| I7.3 | Critical, reason but no compliance | Critical | false | "Has reason" |

### Minimum test cases a strong system should generate: 5
- Non-Critical risk saves without either (V7.1)
- Critical with both saves (V7.3)
- Critical with neither blocks (I7.1)
- Critical with compliance only blocks (I7.2)
- Critical with reason only blocks (I7.3)

### Additional tests an excellent system might generate: 3
- Each non-Critical risk level (V7.4, V7.5)
- Blank risk level (V7.2)
- High risk without either (V7.1 — confirms "High" is not "Critical")

---

## VR08 — PLS_BM_VR08_Enterprise_Discount

**Business condition**: Enterprise record type only — Discount > 25% is prohibited. Exactly 25% is allowed. Standard record type is unaffected.

### Valid partitions (should save)

| # | Scenario | Record Type | Discount |
|---|----------|-------------|----------|
| V8.1 | Enterprise, 10% | Enterprise | 10% |
| V8.2 | Enterprise, 25% exactly | Enterprise | 25% |
| V8.3 | Standard, 30% | Standard | 30% |
| V8.4 | Standard, 50% | Standard | 50% |
| V8.5 | Enterprise, no discount | Enterprise | _(blank)_ |

### Invalid partitions (should block)

| # | Scenario | Record Type | Discount |
|---|----------|-------------|----------|
| I8.1 | Enterprise, 26% | Enterprise | 26% |
| I8.2 | Enterprise, 50% | Enterprise | 50% |

### Boundary cases

| # | Scenario | Record Type | Discount | Expected |
|---|----------|-------------|----------|----------|
| B8.1 | Enterprise, exactly 25% | Enterprise | 25% | SAVE |
| B8.2 | Enterprise, 25.01% | Enterprise | 25.01% | BLOCK |
| B8.3 | Enterprise, 24.99% | Enterprise | 24.99% | SAVE |
| B8.4 | Standard, 25.01% | Standard | 25.01% | SAVE |

### Minimum test cases a strong system should generate: 5
- Enterprise below cap saves (V8.1)
- Enterprise at exactly 25% saves (B8.1)
- Enterprise above 25% blocks (I8.1)
- Standard above 25% saves (V8.3)
- Standard well above 25% saves (V8.4)

### Additional tests an excellent system might generate: 3
- Enterprise 25.01% boundary blocks (B8.2)
- Enterprise 24.99% boundary saves (B8.3)
- Standard 25.01% saves (B8.4 — proves record-type isolation)

---

## VR09 — PLS_BM_VR09_External_Reference

**Business condition**: If populated, External Reference must match `EXT-` followed by exactly 8 digits. Blank is allowed.

### Valid partitions (should save)

| # | Scenario | External Reference |
|---|----------|--------------------|
| V9.1 | Blank | _(blank)_ |
| V9.2 | Correct format | EXT-12345678 |
| V9.3 | Correct format (all zeros) | EXT-00000000 |
| V9.4 | Correct format (all nines) | EXT-99999999 |

### Invalid partitions (should block)

| # | Scenario | External Reference | Why invalid |
|---|----------|--------------------|-------------|
| I9.1 | Too few digits | EXT-1234 | Only 4 digits |
| I9.2 | Too many digits | EXT-123456789 | 9 digits |
| I9.3 | Lowercase prefix | ext-12345678 | Wrong case |
| I9.4 | Letters instead of digits | EXT-ABCDEFGH | Non-numeric |
| I9.5 | Wrong prefix | ABC-12345678 | Not "EXT-" |
| I9.6 | Missing hyphen | EXT12345678 | No separator |
| I9.7 | Extra characters after | EXT-12345678X | Trailing char |
| I9.8 | Extra characters before | XEXT-12345678 | Leading char |
| I9.9 | Mixed alpha-numeric | EXT-1234ABCD | Partial alpha |
| I9.10 | Space in value | EXT- 2345678 | Space instead of digit |

### Minimum test cases a strong system should generate: 5
- Blank allowed (V9.1)
- Valid format saves (V9.2)
- Too few digits blocks (I9.1)
- Too many digits blocks (I9.2)
- Wrong prefix blocks (I9.5)

### Additional tests an excellent system might generate: 5
- Lowercase prefix blocks (I9.3)
- Letters instead of digits blocks (I9.4)
- Missing hyphen blocks (I9.6)
- Trailing character blocks (I9.7)
- All-zeros valid (V9.3)

---

## VR10 — PLS_BM_VR10_Enterprise_Approval

**Business condition**: When Deal Type = Enterprise AND Stage changes to Approved AND Deal Value > 2,000,000, block if ANY of these conditions is true:
- Discount > 20%
- Risk Level is High or Critical
- Compliance Approved is false
- Contract Number is blank
- Contract Start Date is blank
- Contract Start Date < TODAY()

Only fires on a stage transition to Approved. Editing unrelated fields on an already-Approved record must not trigger the rule.

### Valid partitions (should save)

| # | Scenario | Key fields |
|---|----------|------------|
| V10.1 | Enterprise, 3M, all conditions met, transition to Approved | Type=Enterprise, Value=3M, Discount=15%, Risk=Low, Compliance=true, Contract="CN-001", StartDate=TODAY, Stage Draft→Approved |
| V10.2 | SMB, 3M, conditions NOT met, transition to Approved | Type=SMB, Value=3M, Discount=30%, Risk=Critical, Stage Draft→Approved |
| V10.3 | Enterprise, 1.5M, conditions NOT met, transition to Approved | Type=Enterprise, Value=1.5M, Discount=30%, Stage Draft→Approved |
| V10.4 | Enterprise, 3M, already Approved, edit unrelated field | Type=Enterprise, Value=3M, Stage Approved→Approved (no stage change) |
| V10.5 | Enterprise, exactly 2M, transition to Approved | Type=Enterprise, Value=2,000,000, Stage Draft→Approved (value not > 2M) |
| V10.6 | Enterprise, 3M, exactly 20% discount, other conditions met | Type=Enterprise, Value=3M, Discount=20%, Risk=Low, Compliance=true, Contract="CN-001", StartDate=TODAY |

### Invalid partitions (should block — transition to Approved)

| # | Scenario | Failing condition |
|---|----------|-------------------|
| I10.1 | Discount 25% | Discount > 20% |
| I10.2 | Risk Level = High | Risk is High |
| I10.3 | Risk Level = Critical | Risk is Critical |
| I10.4 | Compliance Approved = false | Not compliance-approved |
| I10.5 | Contract Number blank | No contract number |
| I10.6 | Contract Start Date blank | No start date |
| I10.7 | Contract Start Date = yesterday | Past date |
| I10.8 | Multiple conditions failing | Discount 30% + Critical + no compliance |

All invalid cases assume: Type=Enterprise, Value=3,000,000, Stage transitioning from Draft to Approved, with all other conditions met except the one being tested.

### Boundary cases

| # | Scenario | Key boundary | Expected |
|---|----------|--------------|----------|
| B10.1 | Value = exactly 2,000,000 | Value boundary | SAVE (not > 2M) |
| B10.2 | Value = 2,000,001 | Value boundary | BLOCK (if any condition fails) |
| B10.3 | Discount = exactly 20% | Discount boundary | SAVE |
| B10.4 | Discount = 20.01% | Discount boundary | BLOCK |
| B10.5 | Contract Start Date = TODAY | Date boundary | SAVE |
| B10.6 | Contract Start Date = YESTERDAY | Date boundary | BLOCK |

### State-transition cases

| # | From Stage | To Stage | Stage Changed? | Expected |
|---|-----------|----------|----------------|----------|
| S10.1 | Draft | Approved | Yes | Rule evaluates |
| S10.2 | Negotiation | Approved | Yes | Rule evaluates |
| S10.3 | Approved | Approved | No (unrelated edit) | Rule does NOT fire |
| S10.4 | Draft | Contract Review | Yes but not to Approved | Rule does NOT fire |
| S10.5 | _(new record)_ | Approved | ISCHANGED=false on insert | Rule does NOT fire |

### Minimum test cases a strong system should generate: 8
- All conditions met, transition saves (V10.1)
- Non-Enterprise type bypasses rule (V10.2)
- Below 2M value bypasses rule (V10.3)
- Discount violation blocks (I10.1)
- Risk violation blocks (I10.2 or I10.3)
- Compliance violation blocks (I10.4)
- Contract Number missing blocks (I10.5)
- Contract Start Date missing or past blocks (I10.6 or I10.7)

### Additional tests an excellent system might generate: 6
- Edit on already-Approved does not trigger (V10.4/S10.3)
- Exactly 2M value boundary saves (B10.1)
- Exactly 20% discount boundary saves (B10.3)
- Today's date as Contract Start Date saves (B10.5)
- Multiple conditions failing simultaneously (I10.8)
- Non-Approved stage transition doesn't trigger (S10.4)

---

## Summary scoring guide

| VR | Minimum test cases | Excellent additional | Total possible |
|----|-------------------|---------------------|----------------|
| VR01 | 5 | 2 | 7 |
| VR02 | 5 | 3 | 8 |
| VR03 | 7 | 4 | 11 |
| VR04 | 5 | 3 | 8 |
| VR05 | 5 | 3 | 8 |
| VR06 | 5 | 2 | 7 |
| VR07 | 5 | 3 | 8 |
| VR08 | 5 | 3 | 8 |
| VR09 | 5 | 5 | 10 |
| VR10 | 8 | 6 | 14 |
| **Total** | **55** | **34** | **89** |

A strong system should generate at least the minimum set for each rule (55 tests). An excellent system would also surface boundary, isolation, and state-transition cases (up to 89 total).
