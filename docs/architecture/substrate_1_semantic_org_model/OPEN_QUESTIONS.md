# Substrate 1 — Semantic Org Model — Open Questions

Questions specific to this substrate's design. Cross-cutting questions live in the top-level OPEN_QUESTIONS.md.

When a question is answered, move it into DECISIONS_LOG.md as a formal decision and remove it here.

---

## Resolved in Phase 1 (2026-04-24)

- ~~S1-Q-008 — Storage-layer decision~~ → still open (Phase 2 work), tracked at top-level Q-002
- Cross-tenant policy question → resolved by D-011

---

## Open

### S1-Q-001 — Flow logic interpretation depth

Tier 2 commits to interpreting flow XML. The depth question remains:

- **Minimum (Tier 2 starter):** Extract entry conditions and record updates only. Don't simulate decision branches.
- **Medium (Tier 2 mature):** Interpret decision branches, loops, and assignments to track which records get created/updated under which conditions.
- **Full (Tier 3):** Simulate flow execution to predict outcomes given input conditions.

Decide when Phase 2 design begins for flow modeling. The minimum-Tier-2 starter is likely the right ambition.

### S1-Q-002 — Validation rule formula parser scope

We've committed validation rule formula parsing to Tier 1. The parser must handle:
- Field references (always)
- Comparison operators (always)
- Logical operators AND/OR/NOT (always)
- Functions (ISBLANK, ISCHANGED, ISNEW, PRIORVALUE, TEXT, VALUE, etc. — finite set)
- Cross-object references via relationship traversal (e.g., Account.Owner.Profile.Name)
- CASE statements
- Custom labels and custom metadata references

Unknown: do we attempt to interpret SaaS-specific functions like `RegEx()` or do we mark formulas containing them as "partially parsed"? Worth deciding before implementation.

### S1-Q-003 — Apex modeling approach

Apex behavior is opaque from metadata. Options for Tier 3:

- **Reference only:** Model knows apex classes/triggers exist, what objects they touch (from describe), but doesn't reason about behavior.
- **LLM-assisted interpretation:** Feed apex code to an LLM at sync time, extract structured summaries.
- **Static analysis:** Build an actual analyzer (high cost, uncertain return).

Likely answer: reference-only at Tier 3 entry, LLM-assisted summaries when value is shown to be high. Static analysis probably not worth it.

### S1-Q-004 — Granularity of change history events

When sync detects changes, what level of granularity do we record?

- Field-level: every field change is its own event
- Entity-level: "Object O was modified" with a diff payload
- Hybrid: entity-level by default, field-level for entity types where field-level matters (validation rules, formulas)

Affects storage cost and query performance of "what changed" queries. Decide in Phase 2.

### S1-Q-005 — RecordType + Profile + Layout three-way assignment representation

Salesforce's RecordType + Profile → PageLayout mapping is fundamental for Archetype B testing. The model must represent this correctly. Specifically:

- A Profile + RecordType combination maps to one PageLayout
- A PageLayout includes specific fields in specific sections
- A Profile can also override which fields are visible/required regardless of layout (FLS)

The data model must let us answer "for user U with profile P, on a record of type RT, can they see field F?" cleanly. This requires careful representation. Decide in Phase 2.

### S1-Q-006 — Managed package handling

Managed packages introduce namespaced entities with opaque internals. Options:

- First-class entities with namespace labels (treat them like any other entity, just namespaced)
- Opaque blobs (the package is "a thing" but its internals aren't represented)
- Hybrid: public API exposed, internals opaque

Affects testability of orgs relying on managed packages. Decide in Phase 2.

### S1-Q-007 — Initial Tier 1 entity coverage

The metadata API exposes hundreds of entity types. Tier 1 doesn't need them all. Starting list (subject to Phase 2 review):

**In Tier 1:**
- Object (sObject)
- Field
- Relationship
- RecordType
- Layout, Layout assignment
- ValidationRule (with formula parsing)
- Flow (existence + trigger object only)
- Profile, PermissionSet, PermissionSetAssignment
- User
- ChangeEvent (the change log itself)

**Deferred to later tiers:**
- ApprovalProcess
- SharingRule (Tier 2 — modeling enters at T2)
- ApexTrigger, ApexClass (Tier 3)
- CustomSetting, CustomMetadataType (TBD)
- OutboundMessage, PlatformEvent (Archetype E — Tier 3)

### S1-Q-008 — Default background sync schedule

The decision in D-009 commits to background + on-demand sync. The actual schedule defaults remain open:

- Hourly sync for active tenants?
- Nightly for inactive?
- Tenant-configurable?
- Different schedules per entity type (faster for flow changes, slower for layouts)?

Decide in Phase 3 (operational details).
