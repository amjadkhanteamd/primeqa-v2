# FB-V1 — Requirement A (verbatim input candidate)

**Status: authored Wave 0 — NOT yet loaded into Plimsol, NOT yet frozen.**
When this requirement is created in Plimsol (Wave 1), the stored acceptance
criteria must match the block in §Acceptance criteria **byte-for-byte**;
Plimsol's requirement sync then appends its object/field projection tail,
which will be recorded here at that point (VRB-V1 precedent). The text is
deliberately qualitative: it names behaviours and business states, never
thresholds, offsets, formats, band boundaries, or automation artifacts —
deriving those from the org's own metadata is the capability under test.

---

## Summary

Order Lifecycle Automation

## Description

As a Sales Operations Manager, I want PLS FB Order records to be maintained
automatically as they move through the order lifecycle, so that orders stay
complete, correctly classified and consistent without the team doing manual
housekeeping.

Users raise orders, enter the commercial details and line items, and progress
each order through Draft, Submitted, Confirmed, Fulfilled, or mark it as
Cancelled.

## Acceptance criteria (the exact generation input)

```text
As a Sales Operations Manager, I want PLS FB Order records to be maintained automatically as they move through the order lifecycle, so that orders stay complete, correctly classified and consistent without the team doing manual housekeeping.

Users raise orders, enter the commercial details and line items, and progress each order through Draft, Submitted, Confirmed, Fulfilled, or mark it as Cancelled.

An order raised without a stated priority shows a priority of Standard once saved. A priority chosen by the user is always respected.

External references may be typed in any casing and with stray spaces, but are always stored in the company's canonical uppercase form. References that do not follow the company's required reference format are not accepted.

Orders are classified into commercial tiers according to their value, with higher-value orders receiving higher tiers, and the classification stays current when the value changes.

A newly raised order that appears to duplicate an existing open order is flagged for review.

When an order is submitted, the service-level deadline the team must work to is recorded on the order, based on the submission date.

When an order is confirmed, a fulfilment task appears for the operations team, linked to the order, with an appropriate due date.

Cancelling an order cancels its outstanding fulfilment work; work already completed stays as it is.

An order always shows the up-to-date total value and count of its line items as lines are added or amended.

An order that leaves its fulfilled state is marked as reopened, and the event is recorded in the order's audit trail.

At every point in the lifecycle, an order should accurately reflect the work outstanding against it.

Generate comprehensive functional test cases covering positive scenarios, negative scenarios, boundary conditions, relevant record state transitions, observable side effects on related records, and combinations of business conditions.
```
