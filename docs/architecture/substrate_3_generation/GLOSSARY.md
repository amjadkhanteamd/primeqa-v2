# Substrate 3 — Generation Engine — Glossary

Terms specific to substrate-3. Cross-cutting terms live in the top-level glossary (when one exists) or in the relevant substrate's glossary.

---

## Theme 1 terms

**Admissibly grounded.** A claim is admissibly grounded in S1 when S1's actual constraint structure (at the current capability tier) supports the claim's assertion — not merely that referenced entities exist. Stronger than reference-existence; weaker than execution-verified.

**Constrained interpretation engine.** S3's architectural framing. The substrate interprets requirements through S1's ontology and topology and emits typed substrate-2 records, operating within a semantic search space bounded by S1's ontology and substrate-2's locked taxonomy. Opposes "freewheeling LLM authorship" or "translation pipeline."

**Generation ledger.** S3-owned record of generation history per requirement and per `test_id`: actor, prompt version, retrieval set, refusal categories, partial-outcome map, prior attempt linkage. V1-only; retires into substrate-2 provenance when substrate-2's reserved `get_provenance` / `get_recipe_provenance` interfaces ship.

**`GenerationOutcome`.** Typed union representing the result of a generation cycle: draft claims and recipes, refusals, or partial outcomes mixing both. Protocol shape resolved in Theme 2.

**Interpretation layer.** The S3 substrate layer that bridges natural-language requirements to S1-grounded semantic context. Structured, deterministic-where-possible inference over S1's ontology and topology. Precedes claim generation.

**Refusal (typed).** A first-class generation output where S3 declines to emit a claim or recipe with a typed `RefusalKind` discriminator and structured actionable feedback. Refusals carry product value (actionable to the engineer) parallel to drafts; they are not failures.

**`RefusalKind`.** Typed discriminator over refusal categories. Theme 1 names five (`underspecified-requirement`, `no-relevant-context`, `ambiguous-reference`, `ungrounded-claim`, `structural-validation-failure`); Theme 4 anticipates a sixth (`no-admissible-negative-scenario-found`); taxonomy extends through Theme 2 design.

**S3 Guardrail 1 — Semantic search space bounded.** The LLM's semantic search space is bounded by S1's ontology × substrate-2's locked taxonomy. The LLM cannot invent semantic content outside this bounded space, and operates conservatively within it (ambiguity refuses or surfaces disambiguation rather than guesses).

**Scoped semantic neighborhood.** The bounded subgraph of S1 entities and edges within which the LLM reasons during claim generation. Produced by S3's interpretation layer; constrains the LLM's referenceable space.

**Verification bar.** S3's calibration target for output quality. Output is at the verification bar when a competent reviewer can affirm or reject it in bounded time without co-authoring. Output below the verification bar refuses rather than emits.
