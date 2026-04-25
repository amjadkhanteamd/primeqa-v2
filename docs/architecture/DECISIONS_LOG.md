# PrimeQA Architecture Decisions Log

Append-only record of architectural decisions. Each decision has a monotonic ID.

**Format per entry:**

```
## D-NNN — <One-line decision>

**Date:** YYYY-MM-DD
**Substrates affected:** [S1, S2, ...]
**Status:** active | superseded-by-D-NNN | reversed

**Decision:** What we decided.

**Rationale:** Why. 2-4 sentences.

**Alternatives considered:**
- Alternative A — rejected because...
- Alternative B — rejected because...

**References:** Links to SPEC sections, external docs, prior decisions.
```

---

## D-001 — PrimeQA architecture is decomposed into 8 substrates

**Date:** 2026-04-24
**Substrates affected:** [all]
**Status:** active

**Decision:** The platform is architected as 8 substrates: Semantic Org Model, Test Representation, Generation Engine, Execution Engine, Knowledge System, Observation and Interpretation, Conversation and Control, Evolution Engine.

**Rationale:** Building toward a "Claude Code for QA" vision requires general capabilities (substrates) rather than a tool built around a specific feature. Substrates change slowly; features accumulate on top. This decomposition lets us build layer by layer without rewrites. The 8 substrates are documented in PLATFORM_VISION.md.

**Alternatives considered:**
- Feature-driven architecture (generator, executor, dashboard as top-level components) — rejected because it couples substrate-level capabilities to specific feature surfaces and forces rewrites when features expand.
- Monolithic design — rejected because a 5-year vision of this scope cannot be built without clean separation of concerns.

**References:** PLATFORM_VISION.md

---

## D-002 — Substrate 1 (Semantic Org Model) is designed first

**Date:** 2026-04-24
**Substrates affected:** [S1]
**Status:** active

**Decision:** Substrate 1 is designed before any other substrate gets a full spec. All other substrates depend on it directly or transitively.

**Rationale:** The Semantic Org Model is the foundation for everything else: generation reasons against it, execution interprets tests through it, evolution detects changes in it, interpretation explains failures via it. Designing any other substrate first would force assumptions about the org model that would either constrain S1 or require rework downstream.

**Alternatives considered:**
- Design Substrate 3 (Generation Engine) first — the team had momentum here. Rejected because S3 decisions would pre-constrain S1 in ways we can't predict.
- Design multiple substrates in parallel — rejected because they share S1 as a dependency. Until S1 is stable, parallel design creates conflicting assumptions.

**References:** PLATFORM_VISION.md §"Design Order"

---

## D-003 — Architecture 4 (tool-use test plan generation) is paused

**Date:** 2026-04-24
**Substrates affected:** [S3]
**Status:** active

**Decision:** The Architecture 4 spec (v1 through v4) is paused pending Substrate 1 design. A4's design assumptions about metadata access and test structure pre-date the substrate decomposition and need to be re-examined against a proper Semantic Org Model.

**Rationale:** A4 was designed as a standalone generation architecture before we realized the broader platform vision. On review (both Claude Code sanity check and external TA critique), A4 conflates validation with execution, is narrow to Archetype A (data behavior), and doesn't fit the multi-archetype product scope. A4's useful principles (scenario binding, state discipline, strict > convenient) will likely carry forward into the eventual Generation Engine design, but the spec as written is not implemented.

**Alternatives considered:**
- Ship A4 as planned — rejected because it optimizes a narrow slice and doesn't serve the platform vision.
- Ship A4-lite (no Salesforce execution at generation time) — rejected because this still precedes Substrate 1 design and makes commitments we may regret.

**References:** archive/ARCHITECTURE_4_NOTE.md

---

## D-004 — Documentation system lives in /docs/architecture, markdown-only, session-end commits

**Date:** 2026-04-24
**Substrates affected:** [all]
**Status:** active

**Decision:** Architecture documentation lives in the primeqa-v2 repository under `docs/architecture/`. Markdown only, with Mermaid diagrams embedded where useful. Every substrate design session ends with a git commit updating the relevant doc files.

**Rationale:** Without a persistent documentation system, multi-week design work loses context across sessions. The approach mirrors how high-quality architecture work is done in mature engineering orgs: docs are the source of truth, updated continuously, committed with the design work itself.

**Alternatives considered:**
- Documentation in Confluence or external wiki — rejected because it separates docs from code and creates a drift problem.
- Documentation produced once at end of design — rejected because multi-week work without continuous documentation loses context.
- Rich document formats (Notion, docs with embeds) — rejected as unnecessary overhead; markdown is sufficient and grep-friendly.

**References:** README.md

---

## D-005 — Hybrid authorship: Claude produces design docs, Claude Code produces implementation docs

**Date:** 2026-04-24
**Substrates affected:** [all]
**Status:** active

**Decision:** Design documents (SPEC, BACKGROUND, PLATFORM_VISION, EVOLUTION, GLOSSARY) are authored by Claude in design sessions with the user. Implementation documents (how the code actually works, deployment runbooks, code-level architecture docs) are authored by Claude Code after implementation. They coexist but do not overlap in authority.

**Rationale:** The two have complementary strengths. Claude engages in architectural reasoning and external perspective. Claude Code has codebase context and implementation realism. Having both author the same doc type creates authority confusion; having them author different doc types creates a triangulated system where design intent (Claude) meets code reality (Claude Code).

**Alternatives considered:**
- Single author for all docs — rejected because neither Claude nor Claude Code is equally strong at both design and implementation reality.
- User as sole author — rejected because it creates a bottleneck and doesn't leverage either AI's strengths.

**References:** README.md §"Who produces what"

---

## D-006 — Per-tenant authoritative semantic org model

**Date:** 2026-04-24
**Substrates affected:** [S1, S5]
**Status:** active

**Decision:** Each tenant has its own authoritative semantic org model. No tenant data crosses tenant boundaries within Substrate 1. Cross-tenant learning is a structurally separate layer (relevant to Substrate 5, Knowledge System) that consumes from many tenants but stores only abstractions, never tenant data.

**Rationale:** Per-tenant authoritative models are simpler to design, simpler to reason about for compliance, and aligned with how customers expect their org data to be handled. The trade-off — no "free" cross-tenant insights at startup for new tenants — is acceptable, mitigated by Domain Packs (Substrate 5) providing prescriptive knowledge that applies broadly. Cross-tenant pattern aggregation can be added later as a separate, opt-in system without disturbing the foundational design.

**Alternatives considered:**
- Shared model with tenant_id filtering — rejected because it makes tenant deletion fragile, complicates compliance, and tempts code paths that might cross boundaries.
- Cross-tenant learning baked into Substrate 1 — rejected because it conflates two distinct capabilities (authoritative org representation vs. learned patterns) and prevents either from being designed cleanly.

**References:** PLATFORM_VISION.md §"Substrate 1", substrate_1_semantic_org_model/SPEC.md

---

## D-007 — Versioning is event-sourced with logical checkpoints

**Date:** 2026-04-24
**Substrates affected:** [S1, S3, S4, S6, S8]
**Status:** active

**Decision:** Substrate 1 is versioned as an event-sourced model — an append-only change log captures every meaningful change. Logical version markers are placed at coarse-grained boundaries (deploys, sandbox refreshes, sync milestones, manual checkpoints). Test runs, generated test cases, and execution results all bind to a specific logical version. The model can reconstruct any historical version from the change log.

**Rationale:** "Snapshot every change" doesn't scale; "current mutable state only" destroys explainability. Event sourcing with logical checkpoints gives both: low storage cost (we store changes, not snapshots) and stable read views (a logical version is a fixed point everything can reference). Every consuming substrate that produces output (S3 generated tests, S4 run results, S6 explanations, S8 evolution proposals) needs to record which logical version it was produced against, so future analysis can ask "what did the org look like when this was generated?"

**Alternatives considered:**
- Snapshot-based versioning (full copy at each version) — rejected; storage cost prohibitive for large orgs over time.
- Single mutable model with "current state" only — rejected; loses ability to explain why something failed when org has since changed.
- Hybrid (snapshots at major events, deltas otherwise) — viable, treated as an implementation detail of event sourcing rather than a separate strategy.

**References:** substrate_1_semantic_org_model/SPEC.md §"Versioning"

---

## D-008 — Behavior graph with derived edges; edges are invariants, not features

**Date:** 2026-04-24
**Substrates affected:** [S1]
**Status:** active

**Decision:** The semantic org model is a behavior graph, not a metadata cache. It stores derived edges (computed relationships like `flow_modifies_field`, `validation_applies_under_condition`, `permission_set_grants_field_access`) alongside raw entities. Derived edges are computed at sync time, not on-demand by consumers.

**Mindset:** Edges represent invariants the system must reason about, not features the system supports. This framing prevents archetype bias — we don't enumerate edges based on what one consumer wants; we enumerate edges based on what relationships must always be true in this graph for any consumer.

**Rationale:** Storing only raw Salesforce metadata forces every consumer to recompute derived relationships, the cost amortizes badly, and edges drift from the data they describe. Computing edges at sync time means the model is "thicker" but the queries that matter (impact analysis, behavior reasoning, explainability) become first-class. The "edges are invariants" framing was the difference between an archetype-A-only graph and a multi-archetype graph in our design discussion.

**Alternatives considered:**
- Raw-metadata-only model with consumers computing derived facts — rejected; performance and consistency problems.
- Edges enumerated based on Substrate 3's needs — rejected; this is what produced the archetype-A bias we caught and corrected.

**References:** substrate_1_semantic_org_model/SPEC.md §"Derived Edges"

---

## D-009 — Sync strategy: background + on-demand; event-driven deferred

**Date:** 2026-04-24
**Substrates affected:** [S1]
**Status:** active

**Decision:** Substrate 1 sync runs in two modes: periodic background sync (default schedule TBD per substrate, candidates: hourly, nightly, configurable per tenant) keeps the model warm, and on-demand sync of specific slices runs before critical operations (e.g., test generation for a release). Event-driven sync (Salesforce Change Data Capture, Platform Events, deploy notifications) is explicitly deferred — possibly to v3 or v4.

**Rationale:** Event-driven sync sounds elegant but Salesforce CDC is incomplete (doesn't cover metadata changes), Platform Events require per-tenant setup, and the infrastructure overhead is enormous. Background-and-on-demand achieves 90% of the freshness benefit at 10% of the implementation cost. The complexity of true event-driven sync isn't warranted until tenants demand near-realtime test generation in response to org changes.

**Alternatives considered:**
- Pure on-demand sync — rejected; background sync prevents cold-start latency for every test generation.
- Event-driven only — rejected as above; complexity trap.
- All three (event + background + on-demand) — rejected for v1; pick the simpler two-mode approach now, add the third when warranted.

**References:** substrate_1_semantic_org_model/SPEC.md §"Sync Strategy"

---

## D-010 — Tiered modeling with explicit capability_level exposure

**Date:** 2026-04-24
**Substrates affected:** [S1, S3, S4, S6]
**Status:** active

**Decision:** The semantic org model evolves in tiers. Tier 1 covers structural facts (objects, fields, relationships, record types, layouts, profiles, permission sets, validation rule formula parsing). Tier 2 covers behavior interpretation (flow logic, permission inheritance, sharing rules). Tier 3 covers deep semantics (Apex analysis, complex sharing edge cases, lightning page composition).

The model exposes a `capability_level` (TIER_1 | TIER_2 | TIER_3) so consumers know what they can rely on. Consumers must check capability_level before assuming a Tier-2 or Tier-3 capability is available.

**Rationale:** Building the entire behavior graph at once is multi-month work. Tiering lets us ship a useful Substrate 1 progressively. Exposing capability_level prevents the failure mode of consumers blindly expecting capabilities the model hasn't loaded yet. Validation rule formula parsing was promoted to Tier 1 (not Tier 2) because without it, Substrate 3 generates tests that randomly fail validation — making Tier 1 too thin to be useful.

**Alternatives considered:**
- Build all tiers as one monolith — rejected; multi-month work blocks all consumers.
- Tier without capability exposure — rejected; consumers would silently break when expecting Tier-2 features at Tier-1.
- Validation parsing in Tier 2 — rejected; Tier 1 without it is a toy.

**References:** substrate_1_semantic_org_model/SPEC.md §"Tiered Capability Model"

---

## D-011 — Cross-tenant boundary three-tier policy

**Date:** 2026-04-24
**Substrates affected:** [S1, S5]
**Status:** active

**Decision:** Cross-tenant data sharing is governed by a three-tier policy:

- **Tier 1 (raw data) — STRICTLY PRIVATE.** Formulas, field values, org-specific configurations, names, descriptions, structure that could identify a tenant or reveal business logic. Never crosses tenant boundaries.
- **Tier 2 (derived patterns) — SAFE TO SHARE.** Abstract patterns observed across tenants, e.g., "When Stage = Closed Won, Amount is typically required." Patterns are abstractions, not redacted data.
- **Tier 3 (aggregated statistics) — SAFE TO SHARE.** Distributions, frequencies, e.g., "73% of orgs have a validation rule of this shape." Aggregate-only.

**Reconstructable tenant logic is forbidden — even when "anonymized."** Anonymized formula examples are NOT permitted because formulas leak business logic by their structure alone.

**Rationale:** "Anonymized" is a treacherous category — what looks anonymized often isn't, especially in Salesforce where formula structure encodes business rules. The bright line "patterns and statistics yes, examples no" is enforceable; "anonymized examples" requires per-case judgment that will eventually fail. Customers buying PrimeQA need to trust that their org's logic isn't being shared — that trust is foundational and worth more than any cross-tenant feature this restriction blocks.

**Alternatives considered:**
- Allow anonymized examples — rejected; anonymization is a leaky abstraction for Salesforce content.
- Per-tenant opt-in for sharing — rejected for v1; adds policy complexity before we've earned trust.
- No cross-tenant data sharing at all — rejected as too restrictive; patterns and statistics can be safely shared and improve the product.

**References:** PLATFORM_VISION.md §"Substrate 5", substrate_1_semantic_org_model/SPEC.md §"Cross-Tenant Boundary"

---

## D-012 — Diff engine is first-class in Substrate 1

**Date:** 2026-04-24
**Substrates affected:** [S1, S6, S8]
**Status:** active

**Decision:** Substrate 1 includes a diff engine as a first-class subsystem. The diff engine answers queries of the form "what changed between version A and version B that affects entity E?" — where E might be a test case, a flow, a profile, or any other entity in the model.

The diff engine is not a feature consumers cobble together by querying raw change logs. It is a designed subsystem of S1 with its own contract.

**Rationale:** Diff is the engine of explainability. Without it, "why did this test fail" devolves to "the org changed somehow." With it, we can say "between when this test was last green and now, validation rule X was added, and the test triggers it." Both Substrate 6 (Interpretation) and Substrate 8 (Evolution) depend on this capability — without making it first-class, both substrates would reinvent it incompatibly.

**Alternatives considered:**
- Expose raw change log, let consumers compute diffs — rejected; consumers would compute incompatibly, performance would suffer, and the most important use case (impact-aware diff) requires graph traversal that consumers shouldn't reimplement.
- Diff as a Substrate 6 capability — rejected; diff fundamentally operates on S1's data and belongs there. S6 consumes it.

**References:** substrate_1_semantic_org_model/SPEC.md §"Diff Engine"

---

## D-013 — Validation rule formula parsing is Tier 1

**Date:** 2026-04-24
**Substrates affected:** [S1, S3, S4, S6]
**Status:** active

**Decision:** Validation rule formula parsing — extracting the fields referenced and the conditions asserted — is a Tier 1 capability of the semantic org model. Not Tier 2.

**Rationale:** A Tier 1 model that knows validation rules exist but doesn't know what they check is too thin to be useful. Substrate 3 (Generation) at this thin Tier 1 would produce tests that randomly fail validation. Substrate 6 (Interpretation) would explain failures as "validation rule failed" without saying which fields or conditions were involved. The cost of formula parsing is real but not prohibitive — Salesforce formulas have a finite grammar, parsers exist, and the result enables the consuming substrates to be useful at the same tier as S1.

What stays in Tier 2: flow logic interpretation (entry conditions, record updates, decision branches), permission inheritance computation across profile + permission set chains, sharing rule modeling. These genuinely warrant deferral.

**Alternatives considered:**
- Validation parsing in Tier 2 (TA's initial proposal) — rejected; makes Tier 1 too thin to ship.
- Validation as a separate "Tier 1.5" — rejected; arbitrary granularity.

**References:** substrate_1_semantic_org_model/SPEC.md §"Tiered Capability Model"
