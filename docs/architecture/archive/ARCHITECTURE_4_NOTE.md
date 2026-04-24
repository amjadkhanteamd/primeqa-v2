# Architecture 4 — Archive Note

**Status:** Paused, not deleted. Reference material for future Substrate 3 (Generation Engine) design.

**Date paused:** 2026-04-24

**Referenced decision:** D-003 in DECISIONS_LOG.md

---

## What Architecture 4 was

A spec for a tool-use test plan generation system. Four revisions (v1-v4) produced during April 2026. The original Architecture 4 spec in its final v4 form lives at `docs/architecture/ARCHITECTURE_4_SPEC.md` in this repo (committed prior to the platform-vision pivot).

## Why it was paused

On review — both Claude Code sanity check and external TA critique — the design had three structural problems:

1. **It conflated validation with execution.** The spec proposed that tools would execute against real Salesforce during generation. The useful property (validation at generation time) doesn't require the expensive property (execution at generation time). Validating against metadata is sufficient; execution can defer to the existing execution pipeline.

2. **It addressed only Archetype A (data behavior tests).** Roughly 50% of actual customer test needs. A product competing with Provar and Copado needs to handle configuration, permissions, UI, and integration testing — which A4 had no framework for.

3. **It pre-dated the substrate decomposition.** Architecture 4 was designed as a standalone generation architecture before we understood that PrimeQA's real architecture is 8 substrates with Semantic Org Model as the foundation. A4's design assumptions (flat metadata context, test representation as steps array) should be revisited against proper Substrate 1 and Substrate 2 designs.

## What's worth carrying forward

A4's useful design thinking — principles worth preserving as we eventually design Substrate 3:

- **Scenario binds execution.** The declaration at the start of a test case (actors, relationships, expected outcome) should constrain what follows. Generation and execution both enforce against this declaration.
- **State is handed out, not invented.** State references (like `acc_1`, `case_1`) are returned by creation tools, not guessed by the LLM. This prevents a whole class of unresolved-reference bugs.
- **Strict validation over silent recovery.** Duplicate state refs error. Invalid field names error. Retries happen with narrowed context (just the failed tool call and error), not with resubmission of the full plan.
- **11 tools as minimal primitives.** The vocabulary debate concluded at 11 tools. If tool-use is the right model for generation (an open question now), this vocabulary is a good starting point.
- **Feature-flag-gated rollout with shadow mode.** Any architecture shift ships side-by-side with the current one, compared in shadow mode before cutover.
- **Multi-turn gateway with per-turn attribution.** If tool-use is retained, each LLM turn produces a logged row attributable to the parent generation batch. This pattern is worth keeping.

## What's NOT worth carrying forward

Explicit rejections:

- **Execution against Salesforce during generation.** Drop. Validate against metadata instead.
- **Cleanup queue for generation-phase records.** If we don't execute during generation, there's nothing to clean up.
- **Namespace stamping for generation-phase records.** Same reason.
- **Dual-format persistence.** We accepted v4-only `tool_invocations` as the right call. That stands if tool-use stays; if we move to JSON-schema-based generation, this question disappears.
- **Worker-only generation constraint.** Tied to execute-during-generation. If we don't execute, generation can run sync.

## Open question when Substrate 3 design begins

**Is tool-use actually the right generation model?**

A4 committed to multi-turn tool-use. An alternative worth considering: single-shot structured JSON generation with schema validation and targeted retry. Cheaper (1-2 LLM calls vs 8-15), simpler (no multi-turn loop), and potentially sufficient if the JSON schema is well-designed.

We don't know the answer yet. This is the first question Substrate 3 design should tackle.

## Where to find the A4 artifacts

- `docs/architecture/ARCHITECTURE_4_SPEC.md` — final v4 spec (if previously committed)
- Historical v1, v2, v3 specs exist only in chat transcripts and were superseded by v4 in the repo

If the A4 spec was never committed to the repo (design happened entirely in chat), that's fine — the useful content from A4 is preserved in this archive note and in OPEN_QUESTIONS.md.
