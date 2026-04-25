# Substrate 1 — Semantic Org Model — Evolution Log

Append-only. One entry per session that made substantive changes to this substrate's docs.

**Format:**
```
## YYYY-MM-DD — Session topic

What changed in this substrate's docs this session. 2-5 sentences.
Reference the relevant sections of SPEC.md if specific.
```

---

## 2026-04-24 — Substrate skeleton created

Initial skeleton. No design decisions yet. Created SPEC.md with section placeholders, BACKGROUND.md explaining why this substrate exists and what's in/out of scope, GLOSSARY.md as an empty seed, OPEN_QUESTIONS.md with the initial set of questions.

No design content — just the container.

Decisions recorded in top-level DECISIONS_LOG.md: D-002 (S1 designed first).

---

## 2026-04-24 — Phase 1 design (conceptual shape complete)

Filled in SPEC.md sections 1-8 with substantive content. Decisions D-006 through D-013 recorded.

**Conceptual shape locked:**
- Per-tenant authoritative model (D-006)
- Event-sourced versioning with logical checkpoints (D-007)
- Behavior graph with derived edges as invariants (D-008)
- Background + on-demand sync, no event-driven for v1 (D-009)
- Tiered capability model with capability_level exposure (D-010)
- Cross-tenant three-tier policy: raw private, patterns and statistics shareable, anonymized examples forbidden (D-011)
- Diff engine first-class (D-012)
- Validation rule formula parsing in Tier 1, not Tier 2 (D-013)

**Edge taxonomy aligned to PLATFORM_VISION's 5 archetypes:**
- Caught and corrected an archetype-drift problem mid-session (TA had remapped Apex into Archetype D and UI into Archetype E; reverted to vision's definitions)

**Open for Phase 2:**
- Storage backend (top-level Q-002 still open)
- Concrete data model schemas
- Query interface choice
- Diff engine internals
- Tenant isolation mechanism specifics

OPEN_QUESTIONS.md retains S1-Q-001 through S1-Q-008 minus S1-Q-008 (which referenced the cross-tenant question now closed by D-011).
