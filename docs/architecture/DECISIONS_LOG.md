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

**Decision:** The Architecture 4 spec (v1 through v4, stored in archive/ARCHITECTURE_4_NOTE.md) is paused pending Substrate 1 design. A4's design assumptions about metadata access and test structure pre-date the substrate decomposition and need to be re-examined against a proper Semantic Org Model.

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
