# Substrate 1 — Semantic Org Model — Glossary

Terms defined specifically for this substrate. Terms used across the platform live in a future top-level glossary; duplicate them here only if the substrate gives them a specialized meaning.

---

**Behavior graph.** The form of S1's data model: entities plus derived edges that represent relationships and dependencies, computed at sync time. Contrast with "metadata cache," which stores only raw entity attributes.

**Capability level.** The S1 model exposes a `capability_level` attribute (TIER_1, TIER_2, or TIER_3) so consumers know what they can rely on. Querying a Tier 2 capability against a TIER_1 model returns NotAvailableAtCurrentTier rather than silent absence.

**Change log.** The append-only event stream that is the foundation of S1's event-sourced model. Every meaningful change to the org produces an event; the current model is a materialized view computed from the event stream.

**Cross-tenant boundary policy.** The three-tier policy governing what can be shared across tenants: Tier 1 raw data is strictly private, Tier 2 derived patterns are safe to share, Tier 3 aggregated statistics are safe to share. Anonymized examples are explicitly forbidden because they leak structure.

**Derived edge.** A relationship in the graph that is computed at sync time, not directly present in Salesforce metadata. Example: `flow_modifies_field` is derived from parsing flow XML; Salesforce doesn't expose this relationship directly.

**Diff engine.** A first-class subsystem of S1 that answers "what changed between version A and version B that affects entity E?" The engine of explainability — Substrate 6 (Interpretation) and Substrate 8 (Evolution) both depend on it.

**Edge as invariant.** The mindset that derived edges represent relationships that must always be true in the graph, not features that consumers want. This framing prevents archetype bias when enumerating edges.

**Event-sourced model.** S1's storage model: an append-only change log captures every change. Historical states are reconstructable from the change log. Logical version markers identify meaningful checkpoints.

**Logical version.** A named, coarse-grained checkpoint in the change log corresponding to a meaningful event (deploy, sandbox refresh, manual checkpoint, scheduled milestone). Consumers reference logical versions by name; runs and tests bind to the version they were generated/executed against.

**On-demand sync.** A sync mode that refreshes a specific slice of the model on request, typically before a critical operation (e.g., test generation for a release). Contrast with background sync, which runs on a schedule.

**Per-tenant authoritative model.** Each tenant has its own complete model. No data crosses tenant boundaries within S1. Cross-tenant learning is a structurally separate layer (S5).

**Sync milestone.** A user-triggered or system-triggered checkpoint in the change log marking a meaningful state. One of the events that creates a logical version marker.

**Tiered capability model.** S1 evolves in tiers — Tier 1 (foundation), Tier 2 (behavior interpretation), Tier 3 (deep semantics). Tiering lets us ship a useful S1 progressively rather than waiting for full capability before any consumer can use it.
