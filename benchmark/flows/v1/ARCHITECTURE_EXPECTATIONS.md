# FB-V1 — Architectural Expectations

Predictions, recorded **before** any implementation, of where Plimsol's
VRB-V1-era architecture should generalise and where FB-V1 should expose
genuinely new pressure. This is the document the completed program will be
scored against as a *forecast* — wrong predictions are findings too.

Method note: these predictions identify pressure points only. Per the
benchmark's charter, no implementations are proposed here.

Grounding for the "what exists" column: the VRB-V1
[architecture map](../../validation_rules/v1/ARCHITECTURE_MAP.md) (capabilities
D-342…D-360), plus one verified fact about the current substrate — S1 syncs
Flows at **definition level** (flow type, record-trigger type, target object,
active version; `primeqa/sync/detail_mappers.py::_map_flow_details`), with
element-level logic left to an enrichment slot (`parsed_logic`) that is not
today a grounding source the way parsed VR formulas are.

---

## Part A — VRB-V1 capabilities expected to generalise

| VRB-V1 capability | Should carry to | Confidence & caveat |
|---|---|---|
| **Quality hierarchy** (truthful → executable → isolated → evidentially strong → coverage) and the attribution ladder STRUCTURAL < OUTCOME < ATTRIBUTED | The entire benchmark — it is representation-independent by construction | High. The *concept* carries; the *mechanism* behind ATTRIBUTED does not (see B2). |
| **Transition IR** (`TransitionState(prior,next)`, satisfy/evaluate, legitimate-path composition, PRIOR_STATE differential) | FL04's "updated to meet" entry semantics; FL09's `$Record__Prior` logic; every transition-anchored trigger (FL05, FL08, FL10–FL15) | High — the VRB-V1 map explicitly names "Flow record-change triggers" as a generalisation target. Caveat: it must accept a **new front end** (flow entry-condition metadata rather than VR formula functions). |
| **DecisionBranchCoverage** (shape recognizer, per-branch isolation, gate-necessity controls) + contradiction elimination | FL03's multi-outcome decision; FL12's composed decisions; FL14's threshold gate | Medium-high. The machinery proved shape-driven (VR03→VR07 with zero rule-specific code); flow decisions add **outcome ordering** (first-match-wins) which the strict Boolean shape recognizer has never seen. |
| **Minimally violating witnesses / boundary derivation** (`minimal_increment`) | FL03's band edges; FL14's threshold | High, *conditional on* the boundary values being extractable from flow metadata at all (B1). |
| **Deterministic fixture completion** (`complete_accept_fixture`, UNSAT-refuses) | Reaching trigger states past FL02's VR and past sibling flows' entry conditions | High — the VRB-V1 map names "Flow entry conditions" as a target. New wrinkle: completion must now avoid *unintentionally arming other flows* (sibling isolation extends from rules-that-reject to flows-that-act). |
| **Temporal capability** (`RelativeDate(RUN_DATE, k)`, single materialisation boundary) | FL08's computed deadline; FL04's task due date | Medium — the symbol machinery carries, but it has only ever lived on the *staging* side; FL08 needs it on the *assertion* side (B3). Does **not** cover FL10/FL11 (future execution is a different problem than symbolic dates). |
| **ContextDifferential family** (RECORD, PRIOR_STATE; hold-scenario-vary-one-dimension) | FL06 (a new DATA-PRESENCE dimension), FL09 (PRIOR_STATE verbatim) | High for the *pattern*; the new dimension varies **surrounding org data** rather than a record attribute — the differential engine's "one mutated dimension" contract must stretch to environment state. |
| **Proposal contract** (prompts v23–v27: the model names the behaviour, the substrate supplies mechanics) | All fifteen — requirements about effects ("is recorded", "is created", "is escalated") instead of constraints | High for the pattern; guaranteed to need new vocabulary instances (effects, schedules, faults). VRB-V1 predicted exactly this: "the same contract governs how requirements about Flows … get proposed". |
| **Dedup / deprecate-then-regenerate; claims_written-based detection** | The FB-V1 program's own rerun loop | High — carries as-is. |
| **Correctness guards + typed value boundary** (grounding refusal; formula→semantic→transport) | Everywhere values cross the boundary | High — precondition, unchanged. |

## Part B — predicted genuinely new architecture (the pressure points)

Ordered by when the benchmark first applies the pressure.

### B1. Flow behaviour extraction (pressure from FL01; blocking for everything)

The foundational gap. VR reasoning consumes parsed formulas; a flow's
behaviour lives in an **element graph** (start conditions, decisions,
assignments, formulas, DML nodes, scheduled/async paths, fault connectors,
subflow references) that the semantic model currently records only at
definition level. Until flow internals are a grounding source, every control
in this benchmark is invisible: generation cannot know FL01 sets a default,
let alone FL03's band boundaries. **Constraint reasoning becomes effect
reasoning**: a VR is a predicate (world → allowed?); a flow is a function
(world → world′). The IR question is categorically new.

### B2. Effect evidence and causal attribution (FL01, FL04+)

Three sub-pressures, one root — *there is no error message*:

- **Transformation evidence** (FL01–FL03, FL08): the acceptance verdict's
  read-back currently asserts the **staged** value persisted; a before-save
  flow makes the correct persisted value one the probe never posted. The
  expected-value model must switch sources from payload to predicted effect.
- **Side-effect evidence** (FL04+): assertions on records the probe did not
  post — existence, field values, **cardinality** (exactly one task), and
  **set predicates** (zero open tasks remain), plus absence assertions on
  suppression arms.
- **Attribution without messages**: VRB-V1 attributed by matching a rule's
  own error text. An effect carries no signature; the fire/suppress
  **differential becomes the attribution mechanism**, and co-triggered
  automations (FL04/FL13/FL15 share one transition) force *per-effect*
  attribution rather than per-probe.

### B3. Order-of-execution / composition model (FL02)

Predicting a save's outcome stops being "conjunction of constraints" and
becomes "pipeline of automations then constraints then automations". FL02 is
built to falsify any system that evaluates the VR against the *posted*
payload instead of the *flow-transformed* one. Pressure: a save-pipeline
model with defined stage ordering — also the prerequisite for reasoning about
FL07 (child save writing the parent) and FL12 (cascading effects).

### B4. Environment-state fixtures and cross-record staging (FL05–FL07)

Fixture completion so far completes **one record's fields**. FL05 needs
pre-staged child sets; FL06 needs a *sibling record's existence* to be the
varied dimension (and its absence guaranteed in the control arm — inter-run
isolation becomes load-bearing); FL07 needs parent-child graphs staged in
cardinality partitions. Pressure: the fixture concept grows from "a record
satisfying constraints" to "an org neighbourhood in a defined state".

### B5. Iteration / cardinality reasoning (FL07, FL12)

Expected values become functions of collections (aggregates), and witness
axes gain a cardinality dimension (0 / 1 / N). No VRB-V1 capability touches
this; the nearest neighbour (DecisionBranchCoverage) partitions logic, not
set size.

### B6. Asynchronous evidence (FL10, FL11)

Two distinct classes the metadata distinguishes and the system must too:

- **Deferred-but-observable** (FL11, async path): evidence exists at t + ε.
  Pressure: a race-aware evidence protocol (bounded polling, observation
  delay recorded) — S4's evidence collection is currently synchronous with
  the save.
- **Out-of-window** (FL10, scheduled path, days later): honest
  classification, or genuinely new deferred-observation orchestration.
  The failure mode the control is designed to catch is fabrication
  ("save succeeded → behaviour verified").

### B7. Fault-path reasoning (FL13, FL12)

A new question shape: *which external input makes an internal element fail?*
Answering requires composing the flow's data mapping with the **target
object's** required-field metadata — two metadata sources, neither of which
VR reasoning ever combined. Plus a verdict vocabulary for handled faults
(save succeeded, primary effect absent, compensating effect present —
currently expressible as neither `prohibition_enforced` nor
`value_persisted`).

### B8. Cross-mechanism evidence (FL14)

Effects landing in another governance mechanism's objects (`ProcessInstance`,
record locks). Partially paved: the approval-action arc (D-333 era) already
reads approval state; the new pressure is flow-initiated approval as a
*side effect to attribute*, not an action the test itself performs.

### B9. Honest partial observability (FL15, FL10)

Coverage accounting must distinguish "behaviour represented, evidence
unobtainable" from both "covered" and "missing" — per effect, on a probe
whose *other* effects are fully observable. VRB-V1's three-number scoring
(apparent vs. trustworthy vs. correctly-exercised) anticipated this split;
FB-V1 makes it structural.

## Part C — per-flow pressure index

| Flow | Generalises (Part A) | New pressure (Part B) |
|---|---|---|
| FL01 | quality hierarchy, guards | **B1, B2** (first gap expected here) |
| FL02 | fixture completion, boundary witnesses | **B3** |
| FL03 | DecisionBranchCoverage, minimal_increment | B1 (decision extraction), outcome ordering |
| FL04 | transition IR | **B2** (side-effect + attribution) |
| FL05 | transition IR | **B4** (set staging, set assertions) |
| FL06 | ContextDifferential pattern | **B4** (DATA-PRESENCE dimension, inter-run isolation) |
| FL07 | — | **B5**, B3, B4 (child→parent) |
| FL08 | temporal symbols | **B3-adjacent**: symbolic *assertion* values |
| FL09 | transition IR + PRIOR_STATE differential (near-verbatim) | B2 only |
| FL10 | — | **B6** (out-of-window) + B9 |
| FL11 | — | **B6** (deferred-observable) |
| FL12 | DecisionBranchCoverage, transition IR | **B1** (subflow refs), B2, B5, B7 |
| FL13 | fixture completion | **B7** |
| FL14 | boundary witnesses, transition IR | **B8** |
| FL15 | — | **B9** + B2 (per-effect attribution) |

## The headline prediction

**The first architectural gap is expected at FL01 — the simplest control in
the benchmark — and it is expected before execution even starts.** Two walls,
in order: generation has no flow-element grounding source (B1), so it either
refuses or proposes ungrounded behaviour; and even handed a correct test, the
acceptance evidence model asserts staged-value persistence, which FL01's
transformation violates by design (B2). VRB-V1 began with 7 of 8 tests broken
for evidence-model reasons; FB-V1 is predicted to begin with a shape of the
same finding one representation up. That is the benchmark doing its job.
