Permission claims concern access grants. Layer-1-*complete*: reading the
configured grant IS the verification, so no caveat is attached.

- When the requirement asserts a Profile or Permission Set grants access to an
  object or field — e.g. "Profile P can edit Account", "Permission Set S grants
  read on Case.Status" — propose a `capability-claim`. In `target_subject_hint`
  give four keys: `grantee` (the granting Profile/PermissionSet, as
  `{entity_type, sf_api_name}`), `target` (the Object or Field granted on, as
  `{entity_type, sf_api_name}` — fully-qualified `Object.Field` for a field),
  `granted_capability` (`"read"` or `"edit"`), and `grant_type` (`"object"` or
  `"field"`). The substrate verifies the grant edge AND that its specific
  capability bit is set — a claim of *edit* does not ground on a read-only grant.
- v1 grounds **direct** grants only. A capability that would follow from sharing
  rules / org-wide defaults / role hierarchy has no direct grant edge — the
  substrate refuses rather than overstate. Propose the grant the text states and
  let the substrate decide whether the org supports it.
