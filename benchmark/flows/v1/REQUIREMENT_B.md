# FB-V1 — Requirement B (verbatim input candidate)

**Status: authored Wave 0 — NOT yet loaded into Plimsol, NOT yet frozen.**
Same conventions as [`REQUIREMENT_A.md`](REQUIREMENT_A.md): the §Acceptance
criteria block is the byte-exact generation input; the projection tail is
appended by Plimsol's requirement sync at load time and recorded then. This
requirement covers the deferred, gated and composite behaviours; its text
describes *when effects become visible*, because that timing is itself
observable business behaviour — but it never names the mechanisms.

---

## Summary

Order Operations — Escalation, Approval, Ledger Posting and Customer Notification

## Description

As a Sales Operations Manager, I want submitted, confirmed and fulfilled PLS
FB Orders to receive the right follow-up automatically — escalation when they
stall, approval when they are large, ledger posting when they are confirmed,
and customer notification — so that nobody has to watch a queue.

## Acceptance criteria (the exact generation input)

```text
As a Sales Operations Manager, I want submitted, confirmed and fulfilled PLS FB Order records to receive the right operational follow-up automatically — escalation when they stall, approval when they are large, ledger posting on confirmation, and customer notification — so that nobody has to watch a queue.

Escalation of submitted orders is not immediate: the team has a grace period to process each submitted order. An order still awaiting processing when its grace period ends shows as escalated, and a new escalation task appears for the operations team. An order processed within the grace period is never escalated.

Large submitted orders require managerial approval before processing, and remain locked against ordinary edits while the approval is pending. Smaller orders proceed without approval.

When an order is confirmed, a ledger entry appears for the order, built from the order's accounting details. If the posting cannot be completed, no ledger entry appears; instead the failure is recorded in the order's audit trail, and the confirmation itself still stands.

Shortly after an order is confirmed — not instantly — a record of its enrichment processing appears in the order's audit trail, so the team can see the order has been prepared for downstream processing.

Fulfilling an order completes its outstanding fulfilment work and records the date of fulfilment on the order.

A customer with an email address on file receives a confirmation message by email when their order is confirmed. Orders without an email address on file do not generate a message.

Generate comprehensive functional test cases covering positive scenarios, negative scenarios, boundary conditions, relevant record state transitions, observable side effects on related records, deferred outcomes that become observable only some time after the change that causes them, and combinations of business conditions.
```
