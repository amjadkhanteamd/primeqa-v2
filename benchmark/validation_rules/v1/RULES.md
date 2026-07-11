# VRB-V1 — The Ten Controls

One entry per validation rule: what the rule is for, which Salesforce
mechanism it exercises, which Plimsol capability it tests, which acceptance
criteria reached it, the final experiment shape the V1 program settled on, and
the evidence a rerun must produce. The rule inventory and field definitions
live in [`ORG_FIXTURE.md`](ORG_FIXTURE.md); the exact error-condition formulas
live in the deployable SFDX source
(`sandbox_fixtures/pls_benchmark_v1/.../validationRules/`); partition-level
expected values live in [`GOLD_STANDARD.md`](GOLD_STANDARD.md) (confidential
to evaluation).

Evidence vocabulary used throughout:

- **attributed rejection** — the save is rejected AND the error matches this
  rule's own message (verdict `prohibition_enforced`); a rejection by any
  other rule scores zero.
- **acceptance + read-back** — the save (or transition) succeeds AND the
  probe reads the record back asserting the staged value persisted (verdict
  `value_persisted`).
- **isolated** — every sibling rule provably silent over the staged state, so
  the outcome is attributable to exactly one cause.

---

## VR01 — `PLS_BM_VR01_Positive_Deal_Value`

- **Business purpose:** a deal's value, when entered, must be positive; blank
  is allowed.
- **Salesforce mechanism:** simple numeric comparison guarded by
  `NOT(ISBLANK(...))`.
- **Capability tested:** the baseline — minimally violating numeric witness
  derivation with a blank-allowed guard.
- **Requirement path:** AC "Deal values must be commercially valid".
- **Experiment shape:** single isolated negative (create-rejected at a
  non-positive value).
- **Evidence required:** attributed rejection.

## VR02 — `PLS_BM_VR02_Approval_Reason`

- **Business purpose:** discounts above 20% need a written justification;
  exactly 20% does not.
- **Salesforce mechanism:** numeric threshold AND `ISBLANK` on a companion
  field (conditional requiredness).
- **Capability tested:** threshold + requiredness coupling; the exact-boundary
  semantics ("exactly 20% is allowed").
- **Requirement path:** AC "Higher discounts may require additional
  justification".
- **Experiment shape:** isolated negative (above-threshold discount with the
  reason blank).
- **Evidence required:** attributed rejection.

## VR03 — `PLS_BM_VR03_High_Value_Deal`

- **Business purpose:** high-value deals that are risky or heavily discounted
  require compliance approval before they can be saved without it.
- **Salesforce mechanism:** compound Boolean — `A AND (B OR C1 OR C2) AND D`
  across currency, percent, picklist, and checkbox types.
- **Capability tested:** **DecisionBranchCoverage** — logical branch coverage
  over the decision structure, not one lumped witness.
- **Requirement path:** AC "High-value deals with significant discounts or
  risk must receive compliance approval".
- **Experiment shape:** **five-arm Boolean decomposition** under one claim,
  strict-AND: the Discount branch fired in isolation (15.01% — minimally
  violating, deliberately below VR02's 20% gate), the Risk branch fired in
  isolation (Risk High, Discount held at exactly 15%), plus three necessity
  controls — the OR-gate control (every branch false), the Deal-Value gate
  control (exactly 1,000,000 — "greater than" false AT the boundary), and the
  Compliance gate control (approved = true).
- **Evidence required:** two isolated attributed rejections (one per branch)
  + three acceptance controls proving each gate necessary.

## VR04 — `PLS_BM_VR04_Contract_Number`

- **Business purpose:** a contract number must exist once a deal reaches the
  contracting stages.
- **Salesforce mechanism:** picklist-state gate (`ISPICKVAL` disjunction) AND
  `ISBLANK` requiredness.
- **Capability tested:** stage-conditional requiredness; also the workhorse
  *sibling* — nearly every other rule's fixture must satisfy VR04 to stay
  isolated.
- **Requirement path:** AC "Contract details must be captured when the deal
  reaches the appropriate contracting stages".
- **Experiment shape:** isolated negative (contracting stage with the number
  blank).
- **Evidence required:** attributed rejection.

## VR05 — `PLS_BM_VR05_Approved_Lock`

- **Business purpose:** once a deal has been approved, its commercial value is
  locked against later modification.
- **Salesforce mechanism:** org-state functions — `PRIORVALUE` (the prior
  stage) AND `ISCHANGED` (the value mutation): fires only on an update of an
  already-approved record.
- **Capability tested:** **PRIOR_STATE ContextDifferential** on the transition
  IR — same mutation, varied transition history — plus the legitimate-path
  discipline (the Approved prior state is established through the org's own
  VR10-gated transition, never a direct create that would bypass it).
- **Requirement path:** AC "Once a deal has been approved, its approved
  commercial value must be protected from later modification".
- **Experiment shape:** two-arm differential. Reject arm: create (all
  approval conditions met) → the org's own Stage→Approved update (accepted) →
  Deal-Value mutation by the minimal increment → rejected. Control arm: the
  byte-identical base and the identical mutation with the entry transition
  omitted → accepted.
- **Evidence required:** attributed rejection on the approved arm +
  acceptance with Deal-Value read-back on the non-approved arm; the two arms
  differing in exactly one dimension (transition history).

## VR06 — `PLS_BM_VR06_Contract_Start_Date`

- **Business purpose:** an approved deal's contract must start today or later;
  a blank or past start date blocks approval.
- **Salesforce mechanism:** stage gate AND a two-branch disjunction —
  `ISBLANK(date)` OR `date < TODAY()` (two *distinct* violation branches).
- **Capability tested:** the **TEMPORAL capability** — replay-stable
  `RelativeDate(RUN_DATE, k)` values materialised once at the execution
  boundary against a single run temporal reference (the org-default
  timezone), with the adjacent boundary decided by the org's own `TODAY()`.
- **Requirement path:** ACs "Contract details must be captured…" and "Before a
  large Enterprise deal can move to Approved…" (the date leg).
- **Experiment shape:** **four temporal arms** under one claim, all reaching
  Approved via the transition: blank → reject (the ISBLANK branch, staged by
  absence), RUN_DATE−1 → reject (the past branch), RUN_DATE → accept (the
  adjacent boundary), RUN_DATE+1 → accept.
- **Evidence required:** two attributed rejections (one per violation branch,
  demonstrably distinct — the blank arm posts no date key, the past arm posts
  the −1 value) + two acceptances with Approved read-back; the
  yesterday-vs-today pair is the boundary proof.

## VR07 — `PLS_BM_VR07_Critical_Risk`

- **Business purpose:** critical-risk deals need both compliance approval and
  a written override justification.
- **Salesforce mechanism:** picklist gate AND a two-branch disjunction
  (`NOT(checkbox)` OR `ISBLANK(text)`).
- **Capability tested:** originally conditional requiredness; at freeze time
  it ALSO receives the full DecisionBranchCoverage treatment automatically
  (its shape matches `AND(gate, OR(branches))`) — the generalization proof
  that VR03's machinery is shape-driven, not rule-specific.
- **Requirement path:** AC "Critical-risk deals require additional approval
  and justification".
- **Experiment shape:** isolated negative (historic baseline) / five-arm
  decision decomposition (current machinery).
- **Evidence required:** attributed rejection (baseline); branch isolation +
  gate controls under the decision shape.

## VR08 — `PLS_BM_VR08_Enterprise_Discount`

- **Business purpose:** deals of the Enterprise *record type* cannot exceed a
  25% discount; exactly 25% is allowed.
- **Salesforce mechanism:** `RecordType.DeveloperName` — a cross-object
  platform-classification gate — AND a percent threshold. Deliberately
  ambiguous against `PLS_BM_Deal_Type__c = "Enterprise"` (the business field
  VR10 gates on); both record types expose Deal Type "Enterprise", so the two
  "Enterprise" readings are fully orthogonal.
- **Capability tested:** **RecordType context** — the classification as a
  first-class context dimension (deterministic DeveloperName grounding,
  control-relevance nomination, DeveloperName→RecordTypeId at the transport
  boundary) — plus the **RECORD ContextDifferential**.
- **Requirement path:** AC "Enterprise deals are subject to stricter discount
  controls than standard deals".
- **Experiment shape:** **three arms**: the boundary pair (Enterprise at
  exactly 25.00% → accept; Enterprise at 25.01% — the minimally violating
  witness — → reject) + the record-type control (Standard at the same 25.01%
  → accept), the control derived by reference from the firing arm with
  exactly one mutated dimension (RecordTypeId).
- **Evidence required:** attributed rejection on the in-context above-boundary
  arm + two acceptances (the exact boundary; the out-of-context control at
  the identical value) — proving the threshold and the context gate
  independently.

## VR09 — `PLS_BM_VR09_External_Reference`

- **Business purpose:** external references, when provided, must follow the
  company format `EXT-` + 8 digits.
- **Salesforce mechanism:** `REGEX` over an optional text field (fires only
  when populated AND non-matching).
- **Capability tested:** structural regex partitions — a certain NON-matching
  witness derived from the anchored pattern (never a generic complement
  synthesizer).
- **Requirement path:** AC "External references, when provided, must follow
  the company's required reference format".
- **Experiment shape:** isolated negative (a populated, verified-non-matching
  value).
- **Evidence required:** attributed rejection.

## VR10 — `PLS_BM_VR10_Enterprise_Approval`

- **Business purpose:** a large Enterprise deal may move to Approved only when
  every commercial, compliance, risk, and contract condition is satisfied.
- **Salesforce mechanism:** the org's most complex rule — business-field gate
  (`ISPICKVAL(Deal_Type)`), stage gate, `ISCHANGED(Stage)` (fires only on the
  transition, never on create), a currency threshold, and a seven-branch
  violation disjunction.
- **Capability tested:** the **transition IR** — `TransitionState(prior,
  next)` with `satisfy`/`evaluate` operations: the isolated negative violates
  exactly ONE approval condition with every other branch and sibling
  satisfied; the positive (the original benchmark's "T3" false alarm)
  constructs a record state satisfying all ten rules simultaneously and
  drives it through the transition.
- **Requirement path:** AC "Before a large Enterprise deal can move to
  Approved, all required commercial, compliance, risk and contract conditions
  must be satisfied".
- **Experiment shape:** the transition pair. Negative: create the
  all-but-one-condition fixture (Discount 20.01% the single violated branch,
  minimal) → update Stage→Approved → rejected. Positive: the inverse
  experiment (every branch false, Discount at exactly 20%) → the same update
  → accepted, with Stage read back as Approved.
- **Evidence required:** attributed rejection on the single-violation arm +
  acceptance with Approved read-back on the all-conditions-met arm.
