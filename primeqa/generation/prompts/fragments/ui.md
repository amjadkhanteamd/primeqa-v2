UI claims concern how fields are presented to users. Layer-1-*complete*: reading
the configured layout IS the verification, so no caveat is attached.

- **Layout placement.** When the requirement asserts a field *appears on* a page
  layout — e.g. "the Industry field is on the Account sales layout", "AnnualRevenue
  shows on the Account layout" — propose a `layout-claim`. In `target_subject_hint`
  give two keys: `layout` (the PageLayout, as `{entity_type, sf_api_name}` with
  `entity_type: "Layout"`) and `field` (the placed Field, as
  `{entity_type, sf_api_name}` — fully-qualified `Object.Field`). The substrate
  verifies the layout's field-placement edge to that field; absent placement
  refuses.
- This is a placement (metadata) fact — that the field is *configured onto* the
  layout. It is not a runtime rendering claim (whether the field visibly
  enables/renders for a given user is a different, not-yet-supported surface). If
  the requirement is about runtime visibility/enablement rather than static
  placement, do not force a `layout-claim` — let the substrate refuse.
