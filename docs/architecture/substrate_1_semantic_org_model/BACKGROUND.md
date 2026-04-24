# Substrate 1 — Semantic Org Model — Background

## What this substrate is

A rich, queryable representation of a Salesforce org's structure, configuration, behavior, and change history. The system's "understanding" of an org.

Not a metadata dump. Not a schema diagram. A model the other substrates can reason against: query the model to answer "what would be affected if this field changed," "which profiles can edit this object," "what flows trigger on this record type."

## Why it's foundational

Without a semantic org model:

- **Generation (S3) can't reason about impact.** It generates tests for a single requirement without understanding what else in the org touches the same entities. Test coverage is accidental, not intentional.
- **Execution (S4) can't understand context.** A test that updates a Case field doesn't know what flows might fire in response, what validation rules might apply, what permission checks the user will hit.
- **Interpretation (S6) can't explain failures.** When a test fails, the system can report the error but can't place it in org context: "this failed because the flow was deactivated Tuesday by user Y."
- **Evolution (S8) can't detect what to update.** When a field is renamed, the system can't find the tests that reference it without a model that knows about references.

Every substrate above S1 depends on it directly or transitively. The semantic model is the shared language the platform thinks in.

## What we have today

PrimeQA today has "metadata context" — a flat per-generation structure passed into prompts that lists objects, fields, relationships, layouts, and basic rules. It's useful for prompt-shaping but has properties that limit it:

- Regenerated per requirement, not persistent
- Flat structure, not relational/graph
- Captures surface metadata, not behavior (doesn't know what flows do, what validation rules assert)
- No change history — doesn't know what was different yesterday
- No inference — doesn't connect related facts (e.g., "this profile inherits these perm sets which grant these permissions")

The semantic org model replaces flat metadata context with something substantively richer.

## What this substrate is NOT

Scope boundaries to prevent scope creep:

- **NOT a replacement for Salesforce's own metadata API.** We query Salesforce for metadata. The semantic model stores, structures, and enriches it. Salesforce remains the source of truth for what's actually in the org.
- **NOT a deployment tool.** We don't push changes to Salesforce from the model. Deployment is out of scope.
- **NOT a documentation generator for humans.** Though it could feed one. The primary consumer is other PrimeQA substrates.
- **NOT a full org history replay system.** We capture change history at a useful granularity (daily, per-deploy, per-sync) but we're not building a time-travel database.

## What makes this hard

Some reasons this substrate is genuinely challenging to design well:

- **Salesforce orgs are large.** Enterprise orgs have thousands of objects, tens of thousands of fields, hundreds of flows and validation rules. The model must scale without becoming unusable.
- **Behavior isn't all metadata.** Flow behavior requires interpreting the flow XML. Validation rule behavior requires parsing formulas. Apex trigger behavior isn't knowable from metadata at all. We need to decide how much behavior to model vs. defer.
- **Orgs change continuously.** Keeping the model current is a non-trivial infrastructure problem (see Q-003 in OPEN_QUESTIONS.md).
- **Different consumers need different views.** Generation wants "what can I operate on." Execution wants "what will happen when I do this." Evolution wants "what changed and what references it." One model, many projections.
- **Tenant isolation.** Each tenant's org model is private to them, but some patterns may be learned across tenants (see Q-001).

## Design priorities (from the vision)

When trade-offs arise during design, prefer:

1. **Queryability over completeness.** A smaller model that answers real questions beats a complete model nobody can query efficiently.
2. **Correctness over speed.** Better to return "I don't know" than a wrong answer.
3. **Incremental refresh over batch reload.** The model should support partial updates as the org changes.
4. **Explicit over inferred.** When we can represent a fact directly, do so. Inference is a capability built on top, not the primary representation.
5. **Human-readable over compact.** The model will be debugged, inspected, and audited. Readability matters.
