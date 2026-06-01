Data-behavior claims concern how records and fields behave at runtime.

- **Negatives (prohibition).** When the requirement asserts an operation is
  *rejected* ("must not", "cannot", "is prevented from"), propose a
  `prohibition-claim` with negative polarity on the target Object. The substrate
  grounds it on a Validation Rule that applies to that Object. This is
  Layer-1-*plausible*: the rule's existence is verified, its formula is not (the
  parser is unbuilt), so the substrate attaches a caveat. Propose the negative
  even though it will be caveated — a caveated grounded negative is the honest
  artifact, not an overclaim.
- **Positives (value-claim).** When the requirement asserts a *specific field*
  holds a *specific value* (e.g. "Account.Status is 'Active'", "Case.Priority
  defaults to 'High'"), propose a `value-claim` with positive polarity. Put the
  field and value in `target_subject_hint` alongside the Object: `field_name`
  (the field's **fully-qualified `Object.Field` API name**, e.g.
  `"Account.Status"` — never a bare `"Status"`) and `expected_value` (the value
  the requirement states, verbatim). The substrate grounds the claim on that
  named field existing on the Object and carries the value into the test. If the
  requirement states **no concrete value**, do not invent one — propose the
  Object-level claim and let the substrate defer. (A `state-transition-claim` is
  the when-X-becomes-Y variant; v1 grounds it on the field existing.)
- Choose the Object the behavior acts on as the subject — not a field — unless
  the claim is specifically about a single field's value.
