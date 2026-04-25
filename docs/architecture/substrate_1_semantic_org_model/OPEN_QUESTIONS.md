# Substrate 1 — Semantic Org Model — Open Questions

Questions specific to this substrate's design. Cross-cutting questions live in the top-level OPEN_QUESTIONS.md.

---

## Resolved in Phase 1 (2026-04-24)

- Cross-tenant policy → resolved by D-011

## Resolved in Phase 2 (2026-04-25)

- ~~S1-Q-005 — RecordType + Profile + Layout three-way assignment~~ → resolved by D-019 (`ASSIGNED_TO_PROFILE_RECORDTYPE` edge with `record_type_entity_id` in properties)
- ~~S1-Q-007 — Initial Tier 1 entity coverage~~ → resolved by D-018 (10 entity types defined)
- Storage backend (top-level Q-002) → resolved by D-014 (Postgres with graph-friendly design)

---

## Open — to be addressed during Tier 2 / Tier 3 design

### S1-Q-001 — Flow logic interpretation depth

Tier 2 commits to interpreting flow XML. The depth question remains:
- Minimum (Tier 2 starter): Extract entry conditions and record updates only
- Medium (Tier 2 mature): Interpret decision branches, loops, assignments
- Full (Tier 3): Simulate flow execution

Decide when Phase 2 design begins for flow modeling. Likely answer: minimum-Tier-2 starter.

### S1-Q-002 — Validation rule formula parser scope

Validation rule formula parsing is committed to Tier 1 (D-013). The parser must handle:
- Field references (always)
- Comparison and logical operators (always)
- Standard functions (ISBLANK, ISCHANGED, ISNEW, PRIORVALUE, TEXT, VALUE, etc.)
- Cross-object references via relationship traversal
- CASE statements
- Custom labels and custom metadata references

Unknown: how do we handle SaaS-specific functions (RegEx, etc.) that may not parse cleanly? Likely: mark formulas as `formula_parse_status='partial'` and store best-effort parse plus raw text. Address during Tier 1 implementation.

### S1-Q-003 — Apex modeling approach (Tier 3)

Options for Tier 3:
- Reference only: model knows apex classes/triggers exist, what objects they touch
- LLM-assisted interpretation: feed apex code to LLM at sync time, extract structured summaries
- Static analysis: build an actual analyzer

Likely answer: reference-only at Tier 3 entry, LLM-assisted summaries when value is shown to be high.

### S1-Q-004 — Granularity of change history events

Decided in Phase 2 (D-021): granular change_type values plus changed_field_names array. Field-level granularity for entity attribute changes; entity-level for create/delete; edge-level for edge changes.

(Effectively resolved by D-016 and D-021. Removing from open questions.)

### S1-Q-006 — Managed package handling

Managed packages introduce namespaced entities with opaque internals. Options:
- First-class entities with namespace labels
- Opaque blobs
- Hybrid: public API exposed, internals opaque

Affects testability of orgs relying on managed packages. Decide during Tier 1 implementation when first managed-package-heavy tenant onboards.

---

## Open — Phase 3 deferred (operational details)

### S1-Q-008 — Default background sync schedule

D-009 commits to background + on-demand sync. Schedule defaults remain open:
- Hourly for active tenants?
- Nightly for inactive?
- Tenant-configurable?
- Different schedules per entity type (D-020 establishes entity-scoped sync — operational data more frequent than structural)

Decide in Phase 3.

### S1-Q-009 — change_log retention policy

`change_log` grows linearly with org activity. At what point do we purge?
- Never (keeps full audit history)
- After N years (compliance-friendly default)
- Tenant-configurable
- Based on storage pressure

Affects diff engine's "purged version" failure mode. Decide in Phase 3.

### S1-Q-010 — Materialized view refresh strategy

`effective_field_permissions` materialized view (D-020). Refresh approaches:
- After every sync run (simple, may over-refresh)
- After permission-related sync (specific, requires sync-event awareness)
- Triggered by edge changes (most precise, complex)

Decide in Phase 3 based on observed sync patterns.

### S1-Q-011 — Tenant onboarding sequence

When a tenant connects an org, what's the orchestration?
- How long does initial sync take?
- Is the model available for queries during initial sync?
- What's the failure-recovery path?
- How do we communicate progress to the user?

Decide in Phase 3 when implementation begins.

### S1-Q-012 — Schema migration parallelism

D-015 mentions sequential vs parallel migration. At what scale do we need parallel? What's the failure handling for partial migrations?

Decide in Phase 3 when migration tooling is built.
