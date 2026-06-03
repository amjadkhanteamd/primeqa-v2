Configuration claims concern the org's metadata structure itself. All are
Layer-1-*complete*: reading the metadata IS the verification, so no caveat is
attached.

- **Existence.** When the requirement assumes a metadata entity simply *exists*
  — e.g. "the Account object has an Industry field", "a Case escalation flow
  exists" — propose an `existence-claim`. Put the subject in
  `target_subject_hint` as a flat entity reference: `entity_type` (e.g.
  `"Field"`, `"Object"`) and `sf_api_name` (the fully-qualified API name, e.g.
  `"Account.Industry"` for a field, `"Account"` for an object). The substrate
  grounds it by resolving that entity in the pinned org version.
- **Property.** When the requirement asserts a metadata entity *has a specific
  attribute value* — e.g. "the Industry field is required", "Name has length
  80" — propose a `property-claim`. In `target_subject_hint` give the flat
  subject (`entity_type` + `sf_api_name`) plus `property_name` (the attribute,
  e.g. `"is_required"`) and `expected_value` (the value the requirement states,
  verbatim). The substrate reads the value from the org and grounds only if it
  matches — it never takes the asserted value on faith. If the requirement names
  no concrete value, propose `existence-claim` instead.
- **Metadata-relationship.** When the requirement assumes a structural
  relationship between two metadata entities — e.g. "Validation Rule R applies
  to Account", "Field F belongs to Case" — propose a
  `metadata-relationship-claim` with the `edge_type` and the two endpoints
  (`source`, `target`). The substrate verifies the edge exists in the org.
- Name every entity, edge type, and endpoint precisely; the substrate binds the
  edge type to its Tier-1 relationship vocabulary and resolves entities against
  the pinned org version.
