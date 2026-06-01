# Substrate 8 — Evolution Engine — Glossary

Terms specific to substrate-8.

---

**Grounding-validity predicate.** S8's semantic core: a deterministic, on-demand, stateless pure function `(artifact, current org) → intact / drifted / broken`. Two-level (claim-grounding + recipe-grounding). Its legs re-ask generation's own grounding checks against today's org.

**intact / drifted / broken.** The three verdicts. **intact** — every grounding assumption still holds (the subject resolves; the payload still violates; admissibility unchanged). **drifted** — an assumption changed in a way that *may* alter truth; re-evaluation / re-grounding may be warranted. **broken** — an assumption no longer holds at all; the grounding is definitively stale.

**The keystone.** Identity is org-independent by construction (`identity_hash` strips `version_seq` + `external_id`, the C0/C1 invariants), so org evolution is *never* an identity-axis event — *always* a grounding-axis event. S8 governs grounding continuity under identity preservation. One sentence: *S8 re-asks generation's grounding questions against the current org.*

**Recipe-grounding leg.** The first built leg (D-113): does a behavioral-negative recipe's stored payload still violate the current VR formula? **Object-level** (the recipe pins no VR): **intact** = ≥1 active VR's current formula evaluates `True`; **drifted** = none triggered but ≥1 active evaluable VR exists; **broken** (`no_active_vr` / `formula_non_evaluable`).

**`formula.evaluate` primitive.** The neutral Kleene-three-valued evaluator (`primeqa/semantic/formula/eval.py`) — `evaluate(formula_ast, payload) → True | False | NonEvaluable`. The evaluation counterpart of S3's `derive` (`derive` *solves* formula → a violating assignment; `evaluate` *computes* formula + payload → fires?). Consumed by S8 (pre-run) + S6 (post-run) — the parallel-siblings.

**Dependency law (parallel-siblings).** `S8 → {S1 resolution, S3 derive, S3 admissibility}`, **parallel to S6, never S6 → S8**. The decisive reason is epistemic: S6 judges post-run with the actual run evidence; S8 judges pre-run by derivation; the stronger-evidence faculty must not route through the weaker.

**Supersession law.** Re-grounding a drifted artifact is an *identity-preserving supersession*: a new version with the **same** `identity_hash`, on the existing lineage spine, stamped `recipe_s8_rewrite` / `grounding_evolution`. Meaning is the invariant; grounding evolves.

**Object-level / VR-pin.** The recipe-grounding leg is object-level — the recipe pins no specific VR, so an `intact` can mask the loss of one *specific* VR when a different active VR catches the payload. A generation-side **VR-pin** (recording the grounding VR on the recipe at emission) is the deferred sharpening, and the dominant object-level fix.

**Evolution mechanics (the fence).** The deferred impact/trigger machinery — standing dependency manifest, change→impact reverse index, S1-sync trigger, standing recorded verdict, re-grounding orchestration — explicitly out of the semantic-core opening (`DEFERRED_ITEMS.md`).
