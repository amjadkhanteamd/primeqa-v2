# Constraint-aware fixture solver — design + spike (req-315 Phase-3)

Status: SPIKE (`primeqa/generation/fixture.py`, tests `test_fixture.py`). Not wired
into emission. Completes the Constraint IR's second operation and scopes the
production build.

## The operation

The Constraint IR (`CONSTRAINT_IR_SPIKE.md`) has two operations. D-343 built
**entailment** ("which VR does the claim NECESSARILY fire?" → selection). This is
**satisfaction**: find a field assignment that FIRES a target rule while every
SIBLING active rule stays FALSE — a negative that trips ONLY the rule under test,
or an acceptance that reaches a gated state past every guard — or report **UNSAT**.

Worked case (req-315): `solve_fixture(VR06, [VR01..VR05, VR07..VR09])` →
`{Stage: "Approved", Contract_Start_Date: blank, Contract_Number: "PQA"}` — fires
VR06, and sets Contract_Number so VR04 (Approved + blank contract number) does NOT
also fire. Zero siblings firing.

## Two findings the spike surfaced (the design payoff)

1. **Off-target falsification is mandatory (the k16 discipline).** The naive
   "assign the target's firing + every sibling's non-firing, then merge" FAILS:
   `verified_negative._satisfy(sibling, want_true=False)` greedily picks the FIRST
   non-firing disjunct — e.g. for VR04 it picks *"Stage ≠ Approved"*, which
   conflicts with the target's `Stage = Approved`. A sibling must be silenced by
   padding a value IT owns, never by disturbing a value under test (D-119's k16).
   `_falsify_off_target` tries each conjunct and takes the first whose fields avoid
   the target's; a sibling silenceable ONLY on a target field → **UNSAT**.

2. **Satisfaction needs run-time semantics (absent = BLANK), NOT selection's
   (absent = unknown).** A fixture that leaves a field absent will have it default
   to **blank** at run time, so a sibling that fires under blank-defaults WILL fire.
   Selection (entailment) correctly treats an absent field as UNKNOWN (`_fires`,
   Kleene) — a rule is only "necessarily" selected from the claim's OWN asserted
   values. Isolation is the opposite: it must assume the run-time default, so it
   uses **`evaluate`** (absent = blank). This is the crisp entailment-vs-satisfaction
   distinction — the two IR operations use two different evaluation modes.

## How it composes with what exists

Today isolation is operational: **`vr_conflict` (D-337)** REFUSES at authoring when
the staged state provably fires a DIFFERENT rule, and **R1 padding (D-119,
`execution_engine/world.py`)** fills sibling-satisfying values at RUN time. The
solver lifts this to an authoring-time SOLVE. Its unique additions:

- **UNSAT detection** — a target that can't be isolated (a firing sibling silenceable
  only on a value under test) is refused at authoring, instead of emitting a test R1
  can never make clean (the AmbiguousRejection / setup_rejection class).
- **Determinism** — the isolating assignment is computed, not runtime-padded, so the
  recipe is self-describing and reproducible.
- **Generalization** — the same IR + solve covers Flow entry criteria, Approval
  Process entry criteria, required fields, and record types, not just VRs.

## Bounded (what the spike is NOT)

Single-pass, no backtracking, no inter-sibling conflict resolution (silencing sibling
A may re-arm sibling B); the certainty floor is `_satisfy`'s (org-state / cross-object
/ non-anchored shapes are skipped, never guessed). A production build adds a fixpoint
loop + backtracking and the record-type / cross-object cases (VR08's gate, via the
D-346 typed value boundary's RecordType DeveloperName→Id).

## Trigger for the production build

Wire it into emission when **refuse + pad becomes insufficient** — an org where a
negative genuinely can't be isolated by run-time padding (UNSAT the solver would
catch at authoring), or an acceptance whose satisfying state the model can't
construct (a deep multi-guard gate). req-315 does not hit that wall (`vr_conflict` +
R1 suffice), so the spike stays a spike; the design is banked. See D-337 (authoring
conflict refuse), D-119 (R1 run-time pad), D-343 (the entailment operation), D-346
(the value boundary VR08 needs), CONSTRAINT_IR_SPIKE.md (the shared representation).
