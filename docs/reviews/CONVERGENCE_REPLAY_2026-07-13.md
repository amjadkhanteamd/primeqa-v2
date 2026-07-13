# Generation Convergence V1 — Replay Analysis & Root-Cause Ranking

**Date:** 2026-07-13 · **Branch:** `phase-7-substrate-3-b01-label-affinity`
**Instrument:** `scripts/convergence_replay.py` + `primeqa/generation/convergence.py`
(committed @b95a529) — deterministic, no LLM, no writes; replays every
persisted propose-turn intent through the production seam entry points
(`check_refs_exist` → `resolve_intent`) at the current pin (seq 130, env-59).

## The pipeline being measured

```
model propose turn (persisted verbatim in llm_calls.raw_parameters)
   │  per intent_descriptor:
   ▼
Layer A — lexical ref resolution ── miss → B0 subject offers ──▶ retry/hop
   ▼
Layer 1 — admission (capability check: _field_has_verifiable_producer)
   ▼
resolution tail — grounding, witnesses, VR-conflict, transitions, N-arm
   ▼
emission bundle ──▶ persisted claim ──▶ (approval) ──▶ executed claim
```

Every historical proposal is replayed **as-proposed**; every failure is
probed with bounded counterfactual variants (follow-the-offer, value-drop,
kind-swap) so the taxonomy measures *distance from convergence* rather than
guessing root cause. Corpus: **req-320 = 1,804 intents** (v27: 590, v28:
181, v29: 1,033) + **req-315 = 89 intents**. Replay errors: **0**.

## Headline numbers (replayed against CURRENT code)

| Corpus | as-proposed intent convergence | notes |
|---|---|---|
| req-320 @v29 (n=1033) | **11%** | first-proposal quality; the live loop's Layer-A retry + recovery hop sit on top |
| req-320 @v27/v28 | 12% / 9% | prompt era changes proposal *shape*, not this rate |
| req-315 (n=89) | **61%** | per-AC coverage is 10/10 live — per-intent ≠ per-AC |

**Per-intent convergence is the wrong success metric alone** — an AC
converges if ANY of its intents does. The per-AC table below is the
operative view.

## Per-AC-concept convergence, req-320 @v29, current code

| concept | ACs | converged | rate | dominant failure classes |
|---|---|---|---|---|
| priority-default | 34 | 31 | **91%** | (solved) |
| reference-canonical | 32 | 28 | **88%** | (solved) |
| reference-format | 32 | 15 | 47% | LEXICAL_FIELD (offers exist; live loop recoverable) |
| priority-respected | 30 | 7 | 23% | **EMISSION_GAP** (value-claim emission unbuilt) |
| sla | 32 | 7 | 22% | **KIND_MISFRAME** (state-transition framing) |
| cancel | 26 | 3 | 12% | mixed; largely FL05 honest limits |
| tier | 63 | 5 | 8% | **ADMISSION_DISMISSAL (no feedback)** + lexical |
| reopen | 61 | 4 | 7% | lexical + KIND_MISFRAME + audit-trail honest limit |
| fulfilment-task | 67 | 0 | 0% | honest (FL04 unbuilt) |
| rollup | 37 | 0 | 0% | honest (FL07 unbuilt) + dismissal black hole |

The two solved classes (91%, 88%) prove the loop converges **when the
refusal feedback is actionable**. The broken classes fail for *named,
mechanical* reasons:

## Failure taxonomy — measured distribution (req-320 @v29, 1,033 intents)

| class | n | % | side | reading |
|---|---|---|---|---|
| LEXICAL_SUBJECT | 290 | 28% | model | first-pass name guesses; Layer-A retry + B0/B0.1 already recover these live (self-healing, costs hops) |
| CAPABILITY_LIMIT_CROSS_OBJECT | 110 | 11% | honest | FL04/05/07 families — refusals are correct by design |
| MODEL_SELF_REFUSAL | 110 | 11% | model | no_admissible_test declarations (mostly correct for unbuilt families) |
| CONVERGED | 117 | 11% | — | |
| LEXICAL_FIELD | 160 | 15% | model | wrong field names; **B0.2 offers resolve the miss ~100% where attached** |
| ADMISSION_DISMISSAL | 81 | 8% | substrate | **113/144 whole-corpus are `automation_name == field_name`** (the calculated-field idiom) with a near-miss field → dismissed with **no detail, no offer** |
| KIND_MISFRAME | 63 | 6% | model | **100% state-transition-claim** on automation-written fields; kind-swap variant rescues 63% (221/352 probed) |
| EMISSION_GAP | 43 | 4% | substrate | value-claim emission sub-shape unbuilt (AC2 class) |
| GROUNDING_OTHER | 59 | 6% | mixed | decomposes below |
| ambiguity/witness | 28 | 3% | honest | correct refusals |

`GROUNDING_OTHER` decomposition (full corpus): 35× the state-transition
"needs a verifiable to-state" site (compound kind+lexical; **the refusal
site carries no offers**), 30× VR negative-underivable (req-315 negative
controls — honest), 19× post-C3b "no transform/relative-date/classification
producer" (honest NO_PRODUCER wording), 4× `update_trigger_fields did not
ground` (**no offers at that site either**).

`VALUE_SHAPE` measured **0** — the ingress placeholder scrub + C3b
value-less enumeration already eliminated the invented-value class.

## Recovery measurements

- **Precision** (the offer resolves the missed reference): field offers
  ~100% (shakeout 6/6); overall 40–73% by era — understated for subject
  offers whose follow-up then fails on a *different* (field) layer;
  measurement refinement noted.
- **Yield** (offer → full convergence): 18–22% — the gap between precision
  and yield is downstream honest limits + the compound misses.
- **offer+value_drop composition** rescues 28/160 — compositions matter;
  the live loop only gets them if each refusal layer hands the model an
  actionable next step.

## As-run funnel (persisted outcomes — behavioural, not code-corrected)

req-320 @v29 (32 outcomes): AC coverage 17%; recovery hop requested 421
ACs, newly covered 13%, **abandoned 71%**. Abandonment by concept: tier
**89%**, rollup 81%, sla 81%, reopen 70% — vs priority-default **12%**
(with 15 recoveries). req-315: 90–100% coverage, 0% abandonment @v29,
stdev 0.5 — converged and stable.

**Causal reading:** abandonment tracks feedback hopelessness, not model
whim. Where the refusal carries a path (offers, capability-accurate
detail), the model re-proposes and converges (priority-default,
reference-canonical, and req-315 across the board). Where the refusal is a
bare dismissal or a dead-end message, the model silently triages the AC.
The one-hop budget (D-247) then caps total repair at one round.

## Phase 4 — Root-cause ranking (frequency × severity × effort × measured gain)

| rank | root cause | freq @v29 | severity | effort | measured gain basis |
|---|---|---|---|---|---|
| **1** | **Admission-dismissal black hole** — automation-effect Layer-1 dismissal carries no detail/offer (the `auto==field` + near-miss-field shape) | 81 intents; tier + rollup ACs | AC-blocking | **S** (feedback-only: attach B0.2 field offers + capability detail at the dismissal) | field-offer precision ~100%; tier converges deterministically once the field is right (C3b replay) |
| **2** | **State-transition misframe dead-end** — the to-state refusal neither offers field candidates nor names the verifiable framing | 63 KIND_MISFRAME + 35 to-state OTHERs | AC-blocking (sla, reopen) | **S** (feedback-only: capability descriptor names the automation-effect framing; field offers at the site) | kind-swap rescues 63% deterministically |
| 3 | `update_trigger_fields` grounding refusal lacks offers | 4+ | minor | XS | same helper |
| 4 | Recovery-hop abandonment (71%) | outcome-level | multiplies 1–2 | 0 direct | expected to fall as 1–2 land (feedback actionability); re-measure live; a second hop is a **product decision** (cost) — recommend only |
| 5 | EMISSION_GAP: value-claim sub-shape unbuilt | 43; priority-respected AC | 1 concept | M–L | **out of this mission's scope** (new emission capability, not reachability) — recommend for capability roadmap |
| 6 | LEXICAL_SUBJECT first-pass noise | 290 | self-healing live | — | monitor only; B0/B0.1 already deployed |

Ranks 1–3 are one architectural change: **capability-aware refusal
feedback** — every field-referencing refusal site speaks the same
vocabulary (ranked near-miss offers + what the substrate CAN verify on the
resolved field), built on the existing `_field_recovery_tail` + a new
field-capability descriptor shared by admission and grounding. Benchmark-
independent, deterministic, conservative (feedback-only — no admission or
grounding semantics change), no prompt changes, no benchmark specifics.

## Measurement caveats (honesty)

- Replay measures the SUBSTRATE's response to historical proposals; it
  cannot measure how the live model would respond to *improved feedback* —
  that requires live before/after runs (planned gate: ≥3 live req-320
  runs + req-315 regression at 10/10 after each improvement).
- Per-intent lexical classes over-count relative to end-of-run failures
  (the live loop already retries Layer-A rejections).
- Recovery precision for subject offers is understated (compound misses).
- AC-concept grouping uses measurement-only keyword maps over the model's
  own declared AC labels (analysis scripts only, never product).
