Data-behavior claims concern how records and fields behave at runtime.

- **Negatives (prohibition).** When the requirement asserts an operation is
  *rejected* ("must not", "cannot", "is prevented from"), propose a
  `prohibition-claim` with negative polarity on the target Object. In
  `target_subject_hint`, set `operation` to name WHICH operation is prohibited:
  `"modify_record"` or `"modify_field"` for update/edit/change prohibitions
  ("cannot edit a closed Opportunity", "Stage must not change after Closed"),
  `"delete"` for delete prohibitions ("cannot delete an Account with open
  Cases"), `"create_duplicate"` for duplicate-create prohibitions. Omit
  `operation` only when the requirement does not say which operation is
  blocked. The substrate grounds the claim on a Validation Rule that applies
  to that Object and dispatches the test's shape on the operation (an update
  prohibition is tested by creating a valid record and attempting the
  forbidden edit). Propose the negative even when it will be caveated — a
  caveated grounded negative is the honest artifact, not an overclaim.
- **Positives (value-claim).** When the requirement asserts a *specific field*
  holds a *specific value* (e.g. "Account.Status is 'Active'", "Case.Priority
  defaults to 'High'"), propose a `value-claim` with positive polarity. Put the
  field and value in `target_subject_hint` alongside the Object: `field_name`
  (the field's **fully-qualified `Object.Field` API name**, e.g.
  `"Account.Status"` — never a bare `"Status"`) and `expected_value` (the value
  the requirement states, verbatim). The substrate grounds the claim on that
  named field existing on the Object and carries the value into the test. If the
  requirement states **no concrete value**, do not invent one — propose the
  Object-level claim and let the substrate defer.
- **State transitions (state-transition-claim).** When the requirement asserts
  the ORG moves a record to a state ("when a Case is created, Status becomes
  In Escalation", "ClosedDate is populated on close"), propose a
  `state-transition-claim` with positive polarity. The subject is the Object
  whose state changes; in `target_subject_hint` set `field_name` (the
  fully-qualified API name) and `expected_value` (the to-state value,
  verbatim). When the transition only happens under a specific CONDITION the
  test must stage — e.g. "when an Opportunity is CLOSED, ForecastCategory
  becomes Closed" requires the Stage to actually be set to a closed value —
  also set `trigger_field` (the field the create must set, fully-qualified
  API name) and `trigger_value` (the provoking value, e.g. "Closed Won").
  Omit the pair when creation alone triggers the transition. If a DIFFERENT
  object's event drives the change ("when an Escalation__c is created, the
  parent Case's Status changes"), also set
  `trigger_object` to that object's API name — the substrate defers those
  honestly today rather than authoring a wrong test. Distinct from
  value-claim: value-claim is a value the USER sets and expects to persist;
  state-transition is a value the ORG's automation sets.
- **Automation effects (automation-effect-claim).** When the requirement
  asserts a Flow/automation PRODUCES something ("when an Order is created, a
  log record is created automatically", "the Flow stamps Status"), propose an
  `automation-effect-claim` with positive polarity. The subject is the
  TRIGGER object (the one whose change fires the automation). For an effect
  on the trigger record itself, set `field_name` + `expected_value`. For an
  effect record on ANOTHER object, set `effect_object` (its API name) and
  `effect_lookup_field` (the qualified field on the effect object that looks
  up back to the trigger record, e.g. `"Order_Log__c.Order__c"`), plus
  optionally `effect_field` + `effect_value` to assert one of its fields.
  The substrate verifies every name against the org model and grounds the
  claim on a record-triggered Flow existing on the trigger object.
- Choose the Object the behavior acts on as the subject — not a field — unless
  the claim is specifically about a single field's value.
