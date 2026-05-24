Data-behavior claims concern how records and fields behave at runtime.

- **Negatives (prohibition).** When the requirement asserts an operation is
  *rejected* ("must not", "cannot", "is prevented from"), propose a
  `prohibition-claim` with negative polarity on the target Object. The substrate
  grounds it on a Validation Rule that applies to that Object. This is
  Layer-1-*plausible*: the rule's existence is verified, its formula is not (the
  parser is unbuilt), so the substrate attaches a caveat. Propose the negative
  even though it will be caveated — a caveated grounded negative is the honest
  artifact, not an overclaim.
- **Positives (value / state).** When the requirement asserts a field holds or
  becomes a value, propose a `value-claim` (positive) or a
  `state-transition-claim` on the Object; these ground on the relevant fields
  existing.
- Choose the Object the behavior acts on as the subject — not a field — unless
  the claim is specifically about a single field's value.
