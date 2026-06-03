# Substrate 5 — Knowledge System — EVOLUTION

The append-only build arc for S5. Each entry: what changed, why, what was verified.

## 2026-06-03 — Phase 4: S5 opened + ratified (docs-led, D-134 → D-135)

S5 is the only core-path substrate that was **built before it was documented** — its machinery shipped in the v1 intelligence stack (≈ Apr 2026: the `KnowledgeProvider` port + assembler, the system-rules JSON channel, the feedback/learned channel, the Domain Packs channel) and has fed v1 test-case generation in production since. Phase 4 **opens** it as a formal substrate: a doc set + a DECISIONS_LOG ratification + a unified public API + a contract drift-guard. **No new runtime behavior** — a documentation-led formalization of already-deployed code (the binding scope decision: "ratify + consolidate, docs-led").

**Why now (and why docs-led).** The substrate spine (S1→S4) is built; the PLATFORM_VISION says S5 "can be designed after S1–S4 have initial designs" and "extends/formalizes the current Domain Packs and System Rules infrastructure." The machinery is real and works; what it lacked was a *substrate boundary* — a documented contract future consumers (the S3-substrate generation; the Phase-7 cutover; S6) can build against. Relocating the code or wiring new consumers now would churn the live v1 product for no functional gain, so those are designed-and-deferred (see DEFERRED).

**Slice 1 (D-134) — open + ratify.** The doc set (`SPEC.md` realized-surface + boundary + scoping model; `DEFERRED_ITEMS.md`) + the DECISIONS_LOG D-134 ratification (mirroring S2's D-121 readiness-ratification): S5's three channels (provider-port rules, Domain Packs, feedback rules) ratified as the realized surface; the consumer (v1 generation today) + the provider-port contract (Rule/QueryContext + the assembler's dedup/precedence/cap/**deterministic render**) named as the substrate boundary.

**Slice 2 — consolidate the API + pin the contract.** `primeqa/intelligence/knowledge/__init__.py` extended (additively) to export the full S5 surface — the provider port + both rule providers **and** the Domain Packs channel (`DomainPackProvider`/`DomainPack`/`DomainPackLibrary`/`DomainPackSelector`) — so S5 has one coherent public API. A new `tests/test_s5_knowledge_contract.py` drift-guard pins the invariants (Rule/QueryContext shape; assembler precedence/dedup/determinism/token-cap; domain-pack selection; the `__all__` surface). No call-site change, no migration, no v1-runtime behavior change.

**Verified.** The contract drift-guard green; the existing `test_knowledge_architecture` / `test_domain_packs` / `test_generation_quality_gate` suites unchanged + green (the `__init__` change is purely additive). DECISIONS_LOG D-134 / D-135. On `phase-12-substrate-5-knowledge`.
