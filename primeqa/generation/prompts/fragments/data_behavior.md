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
  forbidden edit).
  - **Name the business STATE under which the rejection applies.** Two
    prohibitions on the same Object that differ only by *which* rule is
    violated ("reject when Loan Amount is blank" vs "reject when Stage is
    Closed") are DIFFERENT assertions, not one realisation of the same one. So
    in `target_subject_hint` set `rejection_conditions`: a list of clauses,
    each `{"field": "Object.Field", "predicate": <p>, "value": <v>}`, naming
    the field state that holds when the operation must be rejected. `predicate`
    is one of `"equals"`, `"not_equals"`, `"in_set"` (value is a list), or
    `"matches_pattern"` — all of which carry a `value` — or `"is_null"` /
    `"is_not_null"`, which take NO `value` (omit it). Each `field` must be a
    real, fully-qualified `Object.Field` on the subject. Give the state
    whenever the requirement implies one; omit `rejection_conditions` only for a
    genuinely unconditional rule. The state becomes part of the claim's
    identity, so distinct states are distinct claims (they no longer collapse
    into one generic prohibition); the specific violating value the test sends
    stays in the recipe.
  - **The substrate refuses an incomplete behaviour instance — it does not
    degrade.** A prohibition is authored ONLY when a real reject test is
    derivable (a violating input the substrate can construct from the
    validation rule, today numeric comparisons). When it is not — a
    mandatory-field / `ISBLANK` or picklist gate, a cross-field comparison, or
    a delete/share prohibition no validation rule fires on — the substrate
    REFUSES that intent honestly rather than emitting a weak "a rule exists"
    metadata check. Propose the negative regardless; an honest refusal is the
    correct artifact, never an overclaim.
- **Positives (value-claim).** When the requirement asserts a *specific field*
  holds a *specific value* (e.g. "Account.Status is 'Active'", "Case.Priority
  defaults to 'High'"), propose a `value-claim` with positive polarity. Put the
  field and value in `target_subject_hint` alongside the Object: `field_name`
  (the field's **fully-qualified `Object.Field` API name**, e.g.
  `"Account.Status"` — never a bare `"Status"`) and `expected_value` (the value
  the requirement states, verbatim). The substrate grounds the claim on that
  named field existing on the Object and carries the value into the test. A
  requirement that a user-entered field is "stored", "retained", or "saved as
  entered" with a named value is a value-claim — propose it with that field and
  value. If the requirement states **no concrete value**, do not invent one —
  propose the Object-level claim and let the substrate defer.
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
  parent Case's Status changes"), set `trigger_object` to that object's API
  name AND `trigger_lookup_field` to the qualified field ON THE TRIGGER
  OBJECT that looks up to the subject (e.g. `"Escalation__c.Case__c"`) —
  the test creates the subject, creates the trigger record linked to it,
  and verifies the subject's new state. Both names are verified against
  the org model; without a verifiable lookup the claim is deferred rather
  than authored wrong. Distinct from
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
  When the automation instead STAMPS a record the trigger record points to
  ("creating an Escalation stamps the linked Account's
  Last_Escalation_Date__c"), set `effect_object` (the stamped object) and
  `effect_via_lookup_field` (the qualified lookup ON THE TRIGGER OBJECT
  pointing at it, e.g. `"Escalation__c.Account__c"`) and `effect_field` (the
  stamped field, REQUIRED); set `effect_value` only when the requirement
  names a stable literal — omit it for stamps like "today"/"now" (the test
  then asserts the field was set at all).
  When several Flows fire on the trigger object, set `automation_name` to the
  API name of the specific Flow the requirement is about — otherwise the claim
  cannot tell which automation to assert.
  A Flow usually fires only when its ENTRY CONDITION is met, and the record
  must first pass every validation rule to be created at all. So set
  `trigger_fields` — an array of `{"field_name": "<Object.Field>", "value": <v>}`
  (fully-qualified names) — naming every field the create must set for the Flow
  to fire: the entry-gate field(s) AND any field a validation rule requires
  present. Example: to fire a risk-rating Flow gated on
  `StageName='Credit Assessment'` where that stage's VR also demands KYC and a
  credit score, set all three — `[{"field_name":"Opportunity.StageName","value":"Credit Assessment"},{"field_name":"Opportunity.KYC_Complete__c","value":true},{"field_name":"Opportunity.Credit_Score__c","value":700}]`.
  Do NOT list the effect field itself in `trigger_fields` — the org must
  produce it. Omit `trigger_fields` when the Flow fires on bare creation.
  A CALCULATED (formula) field is also an automation: when the requirement
  asserts a computed field's value ("Loan-to-Value is calculated as loan
  divided by property value", "the formula computes 62.5%"), propose an
  `automation-effect-claim` whose `automation_name` is the FORMULA FIELD's
  fully-qualified API name (e.g. `"Opportunity.Loan_to_Value__c"`), with
  `field_name` set to the same field, `expected_value` to the computed result
  the requirement states, and `trigger_fields` naming the INPUT fields and
  values the computation reads (plus any field a validation rule requires for
  the record to save). Never list the computed field itself in
  `trigger_fields` — the org must produce it.
  The substrate verifies every name against the org model and grounds the
  claim on a record-triggered Flow existing on the trigger object — or, for a
  formula, on the named field being calculated; it silently drops any
  `trigger_fields` entry it cannot verify (it never guesses).
- Choose the Object the behavior acts on as the subject — not a field — unless
  the claim is specifically about a single field's value.
