# Generation Convergence — Architecture V1

**Status:** V1 shipped 2026-07-13 (branch `phase-7-substrate-3-b01-label-affinity`).
Companion evidence: `docs/reviews/CONVERGENCE_REPLAY_2026-07-13.md`.

## The problem this subsystem owns

The substrate reasons correctly (C1–C6 live-proven; the VR benchmark
converged at 10/10) but the model reaches those capabilities
intermittently. Convergence — *the probability that the model's proposals
allow the substrate to exercise its verified capabilities* — is therefore
an engineering surface of its own, with its own instrument, taxonomy, and
improvement discipline. It is **not** prompt tuning: the levers are
deterministic substrate feedback and measurement.

## Pipeline (the funnel every intent traverses)

```
model propose turn ──────────────── persisted VERBATIM (llm_calls.raw_parameters)
  │ per intent_descriptor
  ▼
Layer A · lexical resolution ── miss ─▶ B0/B0.1 ranked offers ─▶ correction turn
  ▼
Layer 1 · admission  ── _field_has_verifiable_producer (shares the tail's
  │                      capability reach; dismissals carry offers + capability
  │                      detail since Phase 6a)
  ▼
resolution tail · grounding ── producers (literal/ladder, transform, temporal,
  │                            transition) · witnesses · VR-conflict · N-arm
  │                            — refusals carry offers + capability lines
  ▼
emission bundle ─▶ persisted claim (identity-stable) ─▶ approval ─▶ execution
        │
        └──── every stage's outcome is RECONSTRUCTIBLE read-only from
              llm_calls + attempted_interpretation (+ claims/recipes/runs):
              Phase-1 instrumentation with ZERO product change
```

The recovery loop (D-247/D-340) sits across the funnel: one Layer-A
correction turn per structural failure + ONE coverage re-prompt hop, whose
per-AC feedback lines carry the refusal details + offers above.

## The measurement instrument

- `primeqa/generation/convergence.py` — pure: intent snapshots, the
  failure taxonomy, counterfactual variant generators, the deterministic
  classifier, the outcome-funnel reducer.
- `scripts/convergence_replay.py` — replays every persisted proposal (and
  bounded variants of every failure) through the **production** seam entry
  points at the current pin. Deterministic, no LLM, no writes. The same
  corpus re-run after a change is the before/after comparison.

**The counterfactual-variant principle:** a failed proposal is probed with
the smallest transformations that could converge it — follow the
substrate's own top offer (measures *recovery precision*), drop the value
(the org-defines-the-value class), swap the claim kind. Classification is
therefore *measured distance from convergence*, never a guess.

**Telemetry-only discipline (the D-361 pattern):** nothing in the product
reads these classifications. The promotion boundary is explicit — the
classifier ranks engineering work; it never steers a live generation.

## Failure taxonomy (V1)

14 codes, partitioned (see `TAXONOMY` in `convergence.py`):

- **model-side**: LEXICAL_SUBJECT, LEXICAL_FIELD, VALUE_SHAPE (measured
  ~0 — already closed by the ingress scrub + C3b), KIND_MISFRAME,
  MODEL_SELF_REFUSAL, MODEL_ABANDONMENT (outcome-level).
- **substrate-side**: ADMISSION_DISMISSAL, GROUNDING_WITNESS, EMISSION_GAP.
- **honest limits**: CAPABILITY_LIMIT_CROSS_OBJECT, NO_PRODUCER,
  GROUNDING_AMBIGUITY, NEGATIVE_UNDERIVABLE.
- CONVERGED / GROUNDING_OTHER (audited residue).

## Duplication inventory → the abstractions (Phase 5)

| duplicated logic | where it lived | the abstraction |
|---|---|---|
| producer enumeration | admission twin, tail dispatch chain, C3b disclosure | `_flows_producing_by_projection` (consolidated earlier) + **`_field_capability_summary`** — ONE structured descriptor of what the substrate can verify on a field, shared by admission and grounding |
| field near-miss offers | 4 of 8 field-referencing refusal sites | **`_field_recovery_tail` at every site** (admission dismissal, to-state, update_trigger_fields joined acceptance/prohibition/automation-effect/value-claim) |
| capability wording in refusals | ad-hoc per site | **`_capability_line`** — one renderer: "field X is verifiably WRITTEN by automation (flows) as <shapes> — frame as automation-effect; OMIT expected_value where the org derives it" |
| proposal/recovery vocabulary | prompt (v29 naming contract) vs refusal texts | both now speak *offers + capability shapes*; the prompt stays contract-level (no per-benchmark vocabulary) |

**The shared-vocabulary law:** admission admits exactly what the tail can
ground (`_field_has_verifiable_producer`, the reachability fix), and every
refusal that involves a field reference speaks the same two-part language —
*here is the nearest grounded reference* (ranked offers, fields only —
automation names are never offered, the D-318/B0 law) + *here is what IS
verifiable there* (the capability line). A refusal without a next step is
treated as a defect.

## Guardrails (what this subsystem must never do)

- Never auto-redispatch a claim kind (a kind-swap changes MEANING — the
  AC2 lesson: a persistence value-claim swapped to automation-effect
  grounds the default-writing flow; true claim, wrong meaning). The
  substrate *names* the framing; the model chooses.
- Never offer automation names (D-318).
- Never relax admission/grounding to improve convergence numbers.
- Never add benchmark-specific heuristics; concept keyword maps live in
  analysis scripts only.

## Known gaps & recommendations (not built here)

1. **value-claim emission sub-shape** (EMISSION_GAP, 43 intents @v29;
   blocks the priority-respected AC concept): a real capability gap —
   belongs to the capability roadmap, not convergence.
2. **Second recovery hop** (conditional: only when hop 1 made progress and
   actionable offers remain): abandonment is 71% under a one-hop budget;
   with actionable feedback everywhere the expected need drops — measure
   first, then decide. **Cost is a product decision — recommend, don't
   build.**
3. Recovery-precision measurement for subject offers understates compound
   misses (subject fixed → field then misses) — refine the metric when it
   matters.
4. Future-proofing: a compact per-intent funnel stamp at finalize time
   would remove the path-index reconstruction in the extractor — only
   worth it if the replay harness's derivation ever breaks.
