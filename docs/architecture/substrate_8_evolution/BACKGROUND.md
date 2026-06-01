# Substrate 8 — Evolution Engine — BACKGROUND

## Why this substrate exists

A Salesforce org is not static. Fields are renamed, validation rules are edited, picklist values are removed, automations are reordered. Every such change threatens the *truth* of the tests PrimeQA generated against the org's earlier shape: a test that was meaningful last week may now assert something the org no longer does — silently. Without a faculty that asks *"is this test still true?"*, the platform's trust loop decays: stale tests pass for the wrong reasons, or fail for reasons that have nothing to do with the requirement.

Substrate 8 is that faculty. It governs **grounding continuity** — whether a stable test meaning is still *grounded* in the evolved org. Crucially, S8 does **not** change what a test means. The platform vision frames S8 as "tests maintain themselves" — but that framing (references update, tests adjust, re-verified, flagged) is the *mechanics*. Underneath every such action is a prior *judgment*: is this artifact's grounding still true? S8's semantic core is that judgment; the mechanics are downstream of it.

## The keystone — why evolution is a grounding-axis event, never an identity-axis event

The existing substrates already separated two axes, and built one. **Identity** (what a test *means*) is org-independent *by construction*: a claim's `identity_hash` canonicalizes entity references to `{entity_id, entity_type}` only — `version_seq` and `external_id` are stripped (the C0/C1 invariants in S2). So a rename, a formula edit, an S1 re-sync **cannot** change a claim's identity; "is this the same claim?" is permanently stable. **Grounding** (whether the test is still *true* against the live org) is the axis org evolution actually moves. Therefore org evolution is *never* an identity-axis event — *always* a grounding-axis event. S8 operates on the grounding axis, under the constraint of identity preservation (the S2 constitution already mandates identity-preserving-versions-only). One sentence: **S8 re-asks generation's grounding questions against the current org.**

## What substrate-8 is for

- **Evaluate grounding validity** — a deterministic, on-demand pure function `(artifact, current org) → intact / drifted / broken`, two-level (claim-grounding + recipe-grounding, which drift independently). Its legs are *generation's own grounding checks, re-asked* against today's org — claim-grounding re-consumes S1 entity-resolution; recipe-grounding re-consumes the formula-evaluation primitive (the first leg built, D-113); admissibility re-consumes S3's derivability logic.
- **Re-ground under identity preservation** (the mechanics phase) — when grounding drifts and is repaired, the result is a *new version with the same `identity_hash`*, on the existing lineage spine, stamped `recipe_s8_rewrite` / `grounding_evolution`. Meaning is the invariant; grounding evolves. "Governed semantic evolution."

S8 is **parallel to S6, never consumed by it for the predicate** (the epistemic boundary: S6 judges post-run with the actual run evidence; S8 judges pre-run by derivation; both stand on the neutral `formula.evaluate` primitive). The evolution *mechanics* — the standing dependency manifest, the change→impact reverse index, the S1-sync trigger, re-grounding orchestration — are deliberately fenced; the semantic core (the predicate + the supersession law) is the opening.

Distinct from v1's `primeqa/intelligence` and from S6's `primeqa/interpretation`. Package `primeqa/evolution/`.
