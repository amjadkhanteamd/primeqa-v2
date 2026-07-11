# FB-V1 — Implementation Roadmap

Recommended order for building and running the benchmark. Each wave's flows
depend on capabilities the previous wave forced into existence, mirroring how
VRB-V1's program moved (baseline → differentials → decision/temporal). The
wave gates are **benchmark milestones**, not architecture milestones —
architecture work happens between waves, is measured against the wave, and is
recorded in the decisions ledger as usual.

## Wave 0 — instrument construction (no Plimsol involvement)

Build and seal the instrument before the system under test sees any of it:

1. Author the SFDX fixture (`sandbox_fixtures/pls_fb_benchmark_v1/`): the
   four objects, VR01, the approval process, the email alert, the fifteen
   flows + one subflow, the permission set. Declarative metadata only.
2. Deploy to the benchmark sandbox; characterise **org truth by hand** — for
   every flow, exercise fire and suppress arms via anonymous API calls and
   record the actual observed behaviour (this catches fixture bugs *and*
   produces the raw material for the gold standard).
3. Author `REQUIREMENT.md` (qualitative, no thresholds/bands/flow names,
   with the projection tail), `ORG_FIXTURE.md`, `benchmark-v1.json`, and the
   confidential `GOLD_STANDARD.md`. Seal the gold standard before the first
   generation run.

**Open design fork to settle in Wave 0** — one requirement or two. VRB-V1
used a single requirement for ten controls on one object. FB-V1 has fifteen
controls across an object graph, and its async/composition family (FL10–FL15)
is a different behavioural register from the synchronous core. *Lean: two
requirements* — REQ-A "order lifecycle automation" (FL01–FL09) and REQ-B
"order operations, escalation and notifications" (FL10–FL15) — keeping each
AC block within the density VRB-V1 proved workable, and letting Waves 1–3 run
before REQ-B's harder material is even generated. Cost: two generation
universes; selection interference across the whole fifteen is exercised less
than a single-requirement design would. The fork is recorded here so the
decision is explicit at Wave 0, not accidental.

## Wave 1 — the before-save family: FL01 → FL02 → FL03

- **FL01 first, alone.** It applies the two foundational pressures (flow
  grounding B1, transformation evidence B2) with zero other machinery in the
  frame. Expect the program's first finding here — see prediction below.
- **FL02 second**: adds the order-of-execution composition (B3) against the
  fixture's single VR.
- **FL03 third**: the DecisionBranchCoverage generalisation probe — the first
  score with real diagnostic weight for the VRB-V1 machinery.
- **Gate:** 3/3 correctly exercised (transformation evidence attributed via
  fire/suppress differentials; FL03's bands + boundaries complete).

## Wave 2 — after-save effects and org-data dependence: FL04 → FL05 → FL06 → FL07

- FL04 introduces side-effect evidence + entry-condition transition semantics
  in one control; FL05 widens it to set-valued effects; FL06 adds the
  DATA-PRESENCE differential; FL07 caps the wave with iteration + the
  child→parent direction.
- Settle in this wave: whether FL05's pre-staged tasks are created directly
  or via FL04's own behaviour (fixture-realism vs. isolation tradeoff —
  *lean: directly*, isolation wins; using FL04 as a staging mechanism couples
  two controls).
- **Gate:** 7/7 cumulative; every effect attributed to its flow by
  differential, sibling flows accounted for.

## Wave 3 — temporal computation and prior state: FL08 → FL09

- The two cheapest-if-the-architecture-generalises controls, run *after* the
  side-effect machinery exists so their failures are unambiguous (nothing
  about evidence plumbing is novel by now).
- FL09 doubles as the first legitimate-path composition in flow-land.
- **Gate:** 9/9 cumulative.

## Wave 4 — asynchrony: FL11 → FL10

- **FL11 before FL10**, deliberately: the deferred-but-observable class
  (bounded polling) is buildable and testable; the out-of-window class then
  gets classified against a system that already understands async — making an
  honest FL10 refusal a *positive statement* ("scheduled beyond window")
  rather than a shrug.
- **Gate:** FL11 correctly exercised with recorded observation delay; FL10
  resolved as either honest evidence-limit classification or genuine deferred
  observation. A fabricated FL10 pass fails the wave regardless of the other
  fourteen.

## Wave 5 — composition, failure, interaction: FL13 → FL14 → FL12 → FL15

- FL13 first (fault reasoning in a minimal frame), then FL14 (cross-mechanism
  evidence, building on the existing approval-arc reads), then the FL12
  capstone (which composes what FL05/FL07/FL13 built), and FL15 last —
  because its honesty question ("this save produced three effects; two are
  observable, attribute them correctly and declare the third out of reach")
  is only meaningful once FL04 and FL13 are green on the same transition.
- **Gate:** 15/15 per the headline metric (with FL10/FL15 scored on
  honest classification per the gold standard).

## Freeze

After the Wave 5 gate: record the completion state, write `EXECUTION.md`
(the rerun runbook) and `MANIFEST.md` (identity + fingerprint: object/field/
flow counts, arm inventories, differential inventory), add the FB-V1 row to
`benchmark/README.md`, and freeze the directory under
[`BENCHMARK_POLICY.md`](../../BENCHMARK_POLICY.md). From then on: reruns
only; changes mean V2.

## Where the first gap appears — the prediction, on record

**FL01, before any execution.** Rationale in
[`ARCHITECTURE_EXPECTATIONS.md`](ARCHITECTURE_EXPECTATIONS.md) (headline
prediction): flow internals are not yet a grounding source, so generation
cannot ground the simplest default-setting behaviour; and the acceptance
evidence model asserts staged-value persistence, which transformation
evidence contradicts by design. If FL01 unexpectedly passes clean, the
benchmark's next-most-likely first gaps are FL04 (side-effect evidence) and
FL10 (fabricated async pass) — in that order.

## Ordering rationale (summary)

The sequence is chosen so that every wave isolates at most **one** new
architectural pressure per flow, and each control's failure is diagnosable
without disentangling it from machinery that doesn't exist yet: evidence
model first (Wave 1), effect scope second (Wave 2), generalisation checks
third (Wave 3), time fourth (Wave 4), composition and honesty last (Wave 5).
