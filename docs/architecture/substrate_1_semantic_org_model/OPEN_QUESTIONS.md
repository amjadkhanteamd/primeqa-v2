# Substrate 1 — Semantic Org Model — Open Questions

Questions specific to this substrate's design. Cross-cutting questions live in the top-level OPEN_QUESTIONS.md.

When a question is answered, move it into DECISIONS_LOG.md as a formal decision and remove it here.

---

## S1-Q-001 — What level of behavior modeling do we commit to for flows?

Flows are XML. Their behavior is knowable by parsing and interpreting the XML, but that's a significant effort. We have three options:

- **Reference only** — model stores "flow F exists, triggers on object O, activated yes/no." Doesn't know what the flow does.
- **Partial interpretation** — parse flow XML to extract entry criteria, record updates, and outputs. Don't simulate full flow logic.
- **Full simulation** — essentially rebuilding Flow's runtime in our model. Almost certainly out of scope.

Decision affects how useful S1 is for Substrate 3 (generation) and Substrate 6 (interpretation). Partial interpretation is likely the right answer, but the boundary ("partial" means what exactly?) needs definition.

## S1-Q-002 — How do we model validation rules?

Validation rules are formulas. Parsing formulas to understand what they assert is non-trivial. Options similar to flows:
- Reference only (rule exists, on object X, active/inactive)
- Partial interpretation (extract fields referenced, extract comparison operators)
- Full evaluation (essentially a formula engine)

Partial interpretation enables impact analysis ("which validation rules reference field Y") without building a formula engine.

## S1-Q-003 — Apex trigger and apex class bodies — in the model or not?

Apex behavior cannot be determined from metadata. Options:
- Exclude from model entirely (reference only)
- Include as opaque text (available for LLM reading but not reasoning)
- Static analysis (expensive, complex, uncertain value)

Most likely answer: reference only, with apex text available as a separate fetch when a consumer needs it.

## S1-Q-004 — Granularity of change history

When the model detects changes, what level of granularity do we record?
- Field-level: every field change logged independently
- Entity-level: "Object O was modified on date D" with a diff stored separately
- Snapshot-level: periodic full snapshots, diffs computed on-demand

Affects storage cost and query performance of "what changed" queries.

## S1-Q-005 — How do we represent the relationship between RecordType, Profile, and PageLayout?

Salesforce has a three-way assignment: a Profile + RecordType combination maps to a specific PageLayout. Modeling this correctly is fiddly but essential for Archetype B (configuration tests) that ask "does the Service Rep profile see the IsEscalated field on Case record type Escalation?"

## S1-Q-006 — How do we handle managed packages?

Managed packages introduce namespaced objects, fields, validation rules, and flows. Their internals are opaque. Options:
- Treat them as first-class entities with namespace labels
- Treat the package as an opaque blob referenced by its namespace
- Hybrid: expose the package's public API (global methods, exposed objects) but treat internals as opaque

Affects testability of orgs that rely on managed packages (most enterprise orgs).

## S1-Q-007 — What subset of metadata do we capture initially?

The Salesforce metadata API exposes hundreds of entity types. We don't need them all on day one. What's the minimum viable set?

Starting proposal: Object, Field, Relationship, RecordType, Layout, ValidationRule, Flow (reference only), Profile, PermissionSet, PermissionSetAssignment.

Deferred: ApprovalProcess, SharingRule, ApexTrigger, CustomSetting, CustomMetadataType, outbound messages, platform events.

Revisit when consuming substrates (S3, S4) tell us what they need.

## S1-Q-008 — Storage-layer decision (cross-reference top-level Q-002)

Top-level Q-002 covers this. Noting here because it's a substrate-level decision with substrate-level implications.
