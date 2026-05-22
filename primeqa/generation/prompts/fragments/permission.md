Permission claims concern access grants.

- When the requirement asserts a Profile or Permission Set grants (or denies)
  access to an object or field — e.g. "Profile P can edit Account", "Permission
  Set S grants read on Case.Status" — propose a `capability-claim` naming the
  granting subject and the target. The substrate grounds it on the relevant
  access-grant relationship (`GRANTS_OBJECT_ACCESS` / `GRANTS_FIELD_ACCESS`).
- Permission grounding is narrower at v1; if the grant is not modeled the
  substrate refuses rather than guess. Propose the grant the text states, and
  let the substrate decide whether the org supports it.
