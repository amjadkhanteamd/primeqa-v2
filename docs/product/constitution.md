# PrimeQA — Product & Architecture Constitution

> **Status:** Evergreen. These principles govern all architectural reasoning, code generation, schema design, and product decisions for PrimeQA. Changes to this document are deliberate and reviewed.
>
> **Naming:** *PrimeQA* is the internal / engineering name (repo `primeqa-v2`); *Plimsol* is the commercial name. This document uses PrimeQA throughout.
>
> **How to use:** Cite any principle by number in design decisions, PRs, CC prompts, and TA reviews — e.g. *"leaks S4 → S6, violating §3.4"* or *"binds identity to a UI path, violating §4.7."*

PrimeQA is a semantic QA and test-intelligence platform for Salesforce-focused enterprise systems. It is **not** a generic AI testing tool, low-code automation builder, or Selenium wrapper. Its core purpose is to **model, generate, execute, and explain** structured system truths ("claims") about enterprise behavior.

All architectural reasoning, code generation, schema design, and product decisions must preserve the following principles.

---

## 1. What PrimeQA Is

**1.1** A semantic QA and test-intelligence platform for Salesforce-focused enterprise systems.

**1.2** NOT a generic AI testing tool, low-code automation builder, or Selenium wrapper.

**1.3** Core purpose: model, generate, execute, and explain structured system truths ("claims") about enterprise behavior.

## 2. Core Product Philosophy

**2.1** PrimeQA exists to help QA teams understand failures quickly and accurately.

**2.2** The primary value is NOT automated clicking or raw test generation. The primary value is:
- semantic understanding
- failure attribution
- structured behavioral claims
- execution intelligence
- impact understanding

**2.3** When making product or architecture decisions:
- optimize for correctness and explainability over flashy automation
- prefer deterministic reasoning over speculative AI behavior
- fail loud instead of hallucinating
- preserve human reviewability at all times

## 3. Architectural Philosophy

**3.1** PrimeQA is built as a multi-substrate system.

**3.2** Key substrates include:
- semantic org model (S1)
- test representation substrate (S2)
- generation substrate (S3)
- execution substrate (S4)
- intelligence / attribution substrate (S6)
- evolution substrate (S8)

**3.3** Do NOT collapse substrate responsibilities together.

**3.4** Maintain clean boundaries:
- S1 models the org
- S2 models asserted system truths
- S3 generates claims and recipes
- S4 executes recipes and captures truth
- S6 interprets failures
- S8 evolves artifacts over time

> **Note:** §3.2 names the *key* substrates. The running system also includes **S5 (Knowledge)** and **S7 (Conversation)**; these sit outside this constitution's key-substrate scope and are documented in the architecture reference pages.

## 4. Test Representation (S2)

**4.1** A PrimeQA test is NOT fundamentally a procedural script.

**4.2** A PrimeQA test is: a structured asserted system truth, under semantic conditions, realized through one or more executable recipes.

**4.3** Claims are identity-bearing.

**4.4** Recipes are replaceable operationalizations.

**4.5** Execution context is NOT identity-bearing.

**4.6** Coverage is derived, not canonical. Provenance is historical, not semantic identity.

**4.7** Do NOT model tests as linear step blobs. Do NOT bind semantic identity to UI execution paths.

## 5. Claim Modeling

**5.1** Claim-kinds represent semantic assertion forms, NOT Salesforce implementation primitives.

**5.2** Prefer fewer top-level claim kinds with richer sub-discriminators. Avoid taxonomy explosion.

**5.3** Claims should remain: typed, queryable, human-reviewable, structurally constrained.

**5.4** PrimeQA is NOT building a general-purpose logic language. Avoid over-generalization.

## 6. Semantic Discipline

**6.1** If a value is referenced in the asserted claim, it is semantic.

**6.2** If it is incidental setup or execution machinery, it is operational.

**6.3** Preserve this distinction consistently.

## 7. Execution Philosophy

**7.1** Execution captures truth. Intelligence interprets truth. Do NOT conflate execution with explanation.

**7.2** Execution should capture: structured traces, errors, metadata references, contextual signals.

**7.3** The intelligence layer converts those into: QA-readable explanations, clustered failures, semantic attribution.

**7.4** Prefer deterministic attribution before LLM interpretation. LLMs assist phrasing and contextualization; they do NOT invent root causes.

## 8. Salesforce Modeling Philosophy

**8.1** PrimeQA is NOT a raw Salesforce metadata mirror. It is a semantic extraction engine.

**8.2** Model meaning, relationships, assertions, and dependencies — NOT just API payloads.

**8.3** Salesforce retrieval strategies are entity-specific. Do NOT force artificial API uniformity.

**8.4** Prefer thin transport wrappers, normalization layers, and semantic derivation downstream. Do NOT over-intellectualize transport clients.

## 9. Database Philosophy

**9.1** Use Postgres as a graph-friendly semantic store.

**9.2** Preserve: normalized canonical entities, explicit edges, typed relationships, recursive traversal capability.

**9.3** Do NOT devolve into: giant JSONB blobs, app-layer graph traversal, denormalized metadata dumping.

**9.4** Edges model semantic relationships. Entity tables model identity and attributes.

## 10. AI Usage Philosophy

**10.1** AI is an enrichment layer, not a source of truth.

**10.2** AI-generated artifacts must remain reviewable, editable, and versionable.

**10.3** Prefer deterministic extraction, structured intermediate forms, and typed semantic representations before LLM summarization or phrasing.

**10.4** Never hallucinate metadata, Salesforce behavior, or architectural assumptions.

**10.5** If uncertain: explicitly surface ambiguity, propose pressure points, and HOLD before implementation.

## 11. Engineering Expectations

**11.1 Always:** reason from first principles; surface architectural pressure points; distinguish conceptual design from implementation detail; identify future scaling risks; preserve long-term maintainability.

**11.2 Avoid:** premature abstraction; fake generalization; unnecessary frameworks; hidden magic; silent fallbacks.

**11.3 Prefer:** explicitness; observability; deterministic behavior; layered architecture; evolvable schemas.

## 12. Interaction Expectations

**12.1** When discussing architecture: think as a systems architect, not just an implementer; challenge weak assumptions; identify ontology drift; identify semantic leakage between substrates; protect long-term coherence.

**12.2** When uncertain: pause; enumerate options; explain tradeoffs; state your lean explicitly; HOLD before locking irreversible decisions.

**12.3** Never optimize for short-term implementation convenience at the expense of substrate integrity.

---

*End of constitution.*
