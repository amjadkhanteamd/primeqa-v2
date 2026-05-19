# Substrate 3 — Generation Engine — BACKGROUND

## Why this substrate exists

PrimeQA's product is generating tests that meaningfully assert what should be true about a customer's Salesforce org. Without a deliberately-designed generation substrate, the platform either ships LLM-mediated generation as a black-box pipeline (the v2 status quo, with limitations forensically catalogued in the April 2026 codebase report) or it reaches into the substrate-2 representation layer with prompts that don't honor substrate-2's structural commitments. Both regimes erode the platform's trust loop.

Substrate-3 makes generation explicit: a typed pipeline that interprets requirements through substrate-1's ontology and topology, produces substrate-2-compliant records, and refuses informatively when it cannot do either with verification-bar confidence.

## What substrate-3 replaces

PrimeQA v2's generation pipeline (`primeqa/generation.py` + `worker.py` + `generation_jobs.py`) is a single-shot LLM call with prompt-shaped metadata context, validator post-processing, and a feedback loop for error correction. Three limitations of that pipeline motivate substrate-3:

1. **No formal separation between interpretation, claim emission, and recipe emission.** The LLM is asked to produce everything in one pass against a flat metadata context. There is no ontology or topology layer; the LLM cannot reason about Profile inheritance, Flow triggering, or constraint relationships explicitly.

2. **Output is structurally validated against v2.2's collapsed test_case representation.** That representation flattens across archetypes and lacks the semantic richness of substrate-2's claim/recipe split. Generation cannot honor structural commitments substrate-2 makes.

3. **Refusals are not first-class.** When generation produces weak output, the validator post-processes; when validation cannot repair, generation reports a generic failure. Iterative regeneration starts cold each time; the pipeline has no memory of prior attempts at the same requirement.

Substrate-3 supersedes this pipeline with an architecture that addresses each limitation explicitly.

## What substrate-3 is for

Substrate-3 owns:

- **Requirement interpretation** through S1's ontology and topology.
- **Claim and recipe generation** as typed substrate-2 records.
- **Admissible grounding** against S1's actual constraint structure.
- **Refusal infrastructure** as first-class product surface.
- **Generation ledger** for iterative regeneration, refusal continuity, and evaluation.
- **Quality envelope** measurable on two dimensions (emission, refusal).

It does not own:

- The semantic org model itself (S1).
- The test representation schema (S2).
- Test execution and evidence capture (S4).
- Knowledge artifacts — Domain Packs, system rules, learned facts (S5).
- Failure attribution or explanation (S6).
- Conversational interfaces (S7).
- Autonomous test evolution (S8).

## How substrate-3 relates to other substrates

- **Reads from S1.** S3 queries S1's bitemporal graph for ontology and topology during interpretation. S1's query interface bounds what S3 can ground claims against.
- **Writes to S2.** S3 produces typed Pydantic body instances matching substrate-2's registry, routed through the Semantic Transaction Coordinator. The Coordinator's authority enforcement constrains S3's actor scope.
- **Consumes from S5.** Domain Packs and system rules shape S3's interpretation layer and prompt design.
- **Produces for S4.** Recipes are static artifacts S3 emits; S4 executes them when it ships.
- **Eventually informed by S6.** When S6 ships, attribution feedback can close the gap between generation-time and execution-time domain-truth checking; S3's interpretation layer is the consumption point.
- **Eventually hands off to S8.** Recipe evolution responsibility migrates to S8 when it ships. S3's design bar of "S8-evolvable recipes from day one" makes the handoff incremental, not a forklift.
- **Future surface from S7.** Conversational generation interfaces eventually wrap S3's request pipeline.

## Scope of Phase 1 design

Phase 1 designs S3's architectural shape: substrate boundaries, generation request shape, per-archetype strategies, grounded-negative discipline, LLM integration architecture, prompt management, quality envelope. Seven themes, each producing one or more D-entries. SPEC §2 onward fills section by section as themes converge.

Phase 1 does not implement S3. Implementation lands in Phase 2 (Substrate 3) of the product roadmap, scheduled after Phase 1 design completes.

Phase 1 deliverables, per the substrate-2 precedent:

- This `BACKGROUND.md`
- `PRECONDITIONS.md` (already shipped; baseline at S3 design start)
- `SPEC.md` — fills section by section through Theme convergence
- `GLOSSARY.md` — S3-specific terms accumulate as introduced
- `OPEN_QUESTIONS.md` — design surfaces under deliberation
- `EVOLUTION.md` — session-by-session change log
