# VRB-V1 — Architecture Map

What the benchmark bought. Each entry: the problem a rule (or the fixture as a
whole) exposed, the capability added to Plimsol because of it, and where that
capability generalises beyond validation rules. The canonical design record is
`docs/architecture/DECISIONS_LOG.md` (D-342 … D-360); this map is the index.

The single organising discovery, before the per-rule entries: **the quality
hierarchy is truthful → executable → isolated → evidentially strong → then
coverage**, and its four permanent failure classes (claim fidelity, fixture
satisfiability, evidence strength, value semantics) are what every capability
below serves.

---

## The fixture as a whole → the correctness guards + the Constraint IR

- **Problem discovered:** of the first 8 generated tests, 7 were broken —
  invented values crossing the execution boundary, phantom automations,
  existence reads counted as behavioural coverage, formula-domain values sent
  to the API.
- **Capability added (D-342, D-345, D-346):** the grounding refusal guards; the
  claim↔evidence **attribution** contract (STRUCTURAL < OUTCOME < ATTRIBUTED);
  the typed value boundary (formula → semantic → transport).
- **Generalises to:** every feature family — attribution and the value
  boundary are preconditions for trusting any evidence, on any mechanism.

## VR01/VR02 (thresholds) → minimally violating witnesses

- **Problem discovered:** `±1` witnesses (125% for a 25% cap) are logically
  sufficient but exposed to field precision, sibling controls, and domain
  plausibility.
- **Capability added (D-352):** `minimal_increment` — the nearest representable
  value past a boundary, derived from field precision in the correct value
  domain; decimal thresholds admitted exactly where scale metadata cures the
  old unsoundness.
- **Generalises to:** currency limits, quantity caps, age/score thresholds,
  date boundaries — any ordered constraint.

## VR02/VR04 (sibling interference) → deterministic fixture completion

- **Problem discovered:** an accept probe's staged values armed a *sibling*
  rule (Enterprise + 25% arms VR02) — the probe was rejected by a control not
  under test.
- **Capability added (D-354, wiring the D-347 satisfaction spike):**
  `complete_accept_fixture` — protected target dimensions preserved exactly;
  only provably-firing siblings demand fills, on free dimensions, with typed
  values and per-value provenance (target witness / target activation /
  context / sibling isolation); UNSAT refuses rather than modifies.
- **Generalises to:** Flow entry conditions, approval entry criteria, required
  fields, permissions — any fixture that must reach a state past unrelated
  guards.

## VR08 (record type) → context as a first-class dimension + ContextDifferential

- **Problem discovered:** `RecordType.DeveloperName` is invisible to
  field-overlap selection, inexpressible to the LLM, and orthogonal to the
  same-named business field — plus the deeper discovery that **control
  relevance (nomination) and formula entailment (proof) are different
  operations**: a qualitative requirement cannot entail a formula, so
  relevance nominates and formula analysis supplies the numbers.
- **Capability added (D-348, D-349, D-355):** RecordType context grounding
  from stable metadata identity; control-relevance nomination
  (`ContextGateMatch ∧ SubjectGovernanceMatch ∧ BehaviouralRoleMatch` over a
  bounded role grammar); the DeveloperName→Id transport consumer; the
  **RECORD ContextDifferential** (hold the scenario, vary one classification,
  attribute the delta).
- **Generalises to:** the differential's other dimensions — permission
  (user with/without), profile (visible/hidden), currency, channel (UI vs
  API) — and to any platform classification mechanism.

## VR05/VR10 (org-state functions) → the transition IR

- **Problem discovered:** `ISCHANGED`/`PRIORVALUE` are unknowable to
  single-phase evaluation; "reach Approved" is gated by the org's own
  transition control, so a direct create *bypasses* the very rules under test.
- **Capability added (D-356, D-357, D-358):** `TransitionState(prior, next)` —
  org-state functions as decidable predicates over a two-phase world, with
  satisfy/evaluate operations; the exactly-one-branch violation discipline;
  the legitimate-path composition (a gated prior state entered via the gating
  control's own acceptance fixture); the S4 entry-update recipe shape; the
  **PRIOR_STATE ContextDifferential**; the prior-state-lock selection
  tie-break.
- **Generalises to:** approval-process lifecycles, Flow record-change
  triggers, any before/after semantics; the TRANSITION differential dimension.

## VR06 (dates) → the temporal capability

- **Problem discovered:** calendar literals are not replay-stable, and
  `TODAY()` is undecidable at authoring; blank-vs-past are *separate* branches
  of the same rule.
- **Capability added (D-359):** `RelativeDate(RUN_DATE, k)` — a bounded
  symbolic value the recipe persists and one S4 boundary materialises against
  one explicit `TemporalReference` (org-default timezone); the IR decides
  RelativeDate-vs-TODAY by offset sign; the D-338 refinement (a recipe-staged
  value survives the asserted-blank strip — staged absence beats fillers,
  staged values beat the strip).
- **Generalises to:** every time-relative test value (SLAs, expiry, renewal
  windows), and the EXECUTION-time differential dimension.

## VR03 (compound Boolean) → DecisionBranchCoverage + contradiction elimination

- **Problem discovered:** a lumped witness for `A AND (B OR C) AND D` proves
  almost nothing — which branch fired, and whether each gate is necessary, are
  separate questions; and selection ties between structurally similar rules
  (VR03/VR07) defeat both overlap and entailment.
- **Capability added (D-360):** the bounded decision-shape recognizer +
  five-arm decomposition (isolated firing per branch group; necessity control
  per gate; the OR-gate control); **contradiction elimination** — sound Kleene
  narrowing that removes any tied rule provably False in a claim-pinned world
  (the arc's most general addition: it strengthens *every* selection tie);
  the decision-shape tie-break. VR07 received a five-arm experiment with zero
  rule-specific code — the generalization proof.
- **Generalises to:** Flow decision elements, approval entry criteria,
  compound Apex guards — any branching business logic.

## The requirement's qualitativeness → the proposal contract (prompts v23–v27)

- **Problem discovered (five instances of one class):** the LLM has no
  vocabulary for thresholds, mutations, or platform classifications, and
  under pressure it either self-dismisses ("no named threshold → untestable"),
  contorts (`Deal_Value exceeds Deal_Value`; `exceeds compared_to=null`), or
  over-leans (dropping expressible state along with the inexpressible number).
- **Capability added:** the division-of-responsibility contract, one narrow
  paragraph per instance — *the model names the behaviour; the substrate
  supplies the mechanics* (v23 missing values, v24 feasibility on every axis,
  v25 lock state not mutation, v26 numeric thresholds, v27 lean ≠ minimal).
- **Generalises to:** every future feature family — the same contract governs
  how requirements about Flows, approvals, or permissions get proposed.

## The rerun loop itself → dedup and detection honesty

- **Problem discovered:** deprecated claims silently captured same-hash
  regenerations forever (stale recipes immortal); ad-hoc detectors
  false-negatived against dedup.
- **Capability added (D-353):** the persister's dedup excludes deprecated
  claims — deprecate-then-regenerate mints fresh *by mechanism*; the
  operational lesson (recorded in `EXECUTION.md`): always read the outcome's
  `claims_written`/`equivalent_existing` ids, never time-window heuristics.
- **Generalises to:** every benchmark rerun, including this one.
