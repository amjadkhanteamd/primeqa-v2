# Substrate 5 — Knowledge System — Deferred Items

What S5's opening (Phase 4, D-134) deliberately does **not** build. Each is designed-or-noted, not done; the binding scope was "ratify + consolidate, docs-led" — formalize the realized surface, defer all new wiring.

## 1. Forward consumers (the seams)

- **The S3-substrate generation forward-seam.** The dependency graph has S5 feeding S3, and `primeqa/generation/protocol.py` already carries an attribution stub (*"Domain packs invoked (the S5 knowledge channel), if any"*). But the substrate generation **emits semantic claims** (existence/property/capability/…), a different artifact from v1's test cases — and the existing knowledge (domain packs about Case-escalation test patterns; system rules like "formula fields are read-only") is **v1-test-case-calibrated**. Wiring the S5 port into the S3-substrate prompt therefore needs a **semantic-fit design first**: what knowledge does a *claim-emitter* consume, and in what prompt block? Deferred until that's defined (and until the v1-vs-substrate generation direction settles at the cutover). — **D-134**
- **Serving S6 interpretation.** S6 (failure interpretation) consumes no knowledge today (its taxonomy is hardcoded regex). S5 could later serve a **failure-pattern / known-issue catalog** to interpretation (the vision's "feeds interpretation"). No port exists on the S6 side yet. — **D-134** (cf. PLATFORM_VISION dependency graph)

## 2. The unbuilt vision pieces

PLATFORM_VISION §"Substrate 5" lists knowledge S5 *includes* that is **not yet built** beyond today's feedback-signal rules:

- **"Learned facts specific to a tenant's org."** Today the only learned channel is `feedback_rules` (aggregated *quality signals* — what the generator got wrong). A richer **per-tenant learned-knowledge provider** (durable org-specific facts that accumulate, "gets smarter the more it's used" beyond mistake-avoidance) is a new capability — a new `KnowledgeProvider` + a per-tenant store. — **D-134**
- **"Cross-tenant patterns that stay tenant-isolated."** Patterns learned across tenants, surfaced without leaking tenant data — its own privacy-preserving aggregation design. — **D-134**
- **Org-curated rules (`source="curated"`).** The `Rule.source` taxonomy + the assembler precedence (`learned > curated > system`) already reserve a **curated** tier (human-admin org rules), but **no `CuratedRulesProvider` exists** — the slot is defined, unimplemented. — **D-134**

## 3. Substrate-package hygiene (the relocation)

- **Physical relocation `primeqa/intelligence/knowledge/` → a top-level `primeqa/knowledge/` substrate package.** Every other substrate has its own top-level package (S1 `semantic/`, S2 `test_representation/`, S3 `generation/`, S4 `execution_engine/`, S6 `interpretation/`); S5 living under the v1 `intelligence/` tree is anomalous. Deferred to the **Phase-7 greenfield cutover**, which reorganizes packages anyway — relocating now would churn the live v1 import graph (`test_plan_generation.py`, `generation.py`, the gateway, tests) for no functional gain. — **D-134**
- Feedback aggregation (`llm/feedback_rules.py`) likewise stays put; it is wrapped into the S5 port via `LearnedRulesProvider`. Whether it physically moves under S5 is part of the same cutover relocation. — **D-134**

## 4. Channel-shape evolutions (noted, not urgent)

- **Domain-pack object-match scoring.** The selector's object-score path (`+2·matched_objects`) is dormant — v1 callers pass `referenced_objects=None`; it activates only once the requirements pipeline extracts objects up front (v1.1). — **D-049 (v1) / D-134**
- **A unified channel facade.** The three channels are assembled at scattered call sites today (the assembler in `test_plan_generation.py`, the feedback auto-load in `gateway.py`, the pack resolve in `generation.py`). A single S5 entry point (`assemble_knowledge_for_generation(...)` returning all three blocks) would give one consumption seam — useful, but a refactor of live call sites; deferred behind a concrete second consumer. — **D-134**

## References

- PLATFORM_VISION.md §"Substrate 5 — Knowledge System" (the vision source).
- SPEC.md (this substrate) — the ratified realized surface + boundary.
- DECISIONS_LOG D-134 (ratification) / D-135 (Phase-4 close).
