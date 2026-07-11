# Salesforce Flow Benchmark V1 (FB-V1) — DESIGN

**Status: DESIGN — NOT FROZEN, NOT DEPLOYED.** This directory contains the
*proposed* design of the second Plimsol benchmark family. Nothing here is an
instrument yet: there is no org fixture, no requirement text, no gold
standard, no baseline. Per [`../../BENCHMARK_POLICY.md`](../../BENCHMARK_POLICY.md),
freezing happens once — after the fixture is deployed, the program is run to
completion, and the results are recorded. Until then these documents may be
revised freely.

FB-V1 follows the philosophy of the frozen
[Validation Rule Benchmark V1](../../validation_rules/v1/README.md):
**the benchmark exists to discover architectural limitations in Plimsol, not
to demonstrate success.** It is deliberately adversarial, it is independent of
Plimsol's implementation, and an honest refusal is a valid — sometimes the
*correct* — outcome.

## Purpose

Measure whether Plimsol can turn a qualitative business requirement plus a
Salesforce org's own **Flow** metadata into tests that are truthful,
executable, isolated, and evidentially strong — in that order, with raw
coverage last.

Flows are a fundamentally harder instrument than validation rules, and the
benchmark is designed around the three ways they are harder:

1. **Flows act; rules refuse.** VRB-V1's headline evidence was the *attributed
   rejection* — an error message that names its rule. A flow's typical
   evidence is a **state change**: a field transformed before save, a record
   created somewhere else, an update fanned out to children, an email sent.
   There is no error message to attribute. The evidence model itself is under
   test.
2. **Flows participate in the order of execution.** A before-save flow runs
   *before* custom validation rules, so the org's observable behaviour is a
   **composition** of automations, not a conjunction of constraints. The
   benchmark contains a deliberate control where reasoning from the validation
   rule alone predicts the wrong outcome.
3. **Flows escape the request window.** Scheduled paths and asynchronous paths
   produce effects after the save returns. A system that only observes
   synchronous responses must either grow deferred-evidence machinery or say
   honestly that it cannot observe the effect. Both honesty controls are in
   the benchmark by design.

The headline metric is **correctly-exercised controls (n/15)**: a flow counts
only when live evidence demonstrates its behaviour *for the intended reason*,
with the effect attributed to that flow (not merely "something changed"), and
with sibling automations accounted for. For the two designed evidence-limit
controls (FL10 scheduled path, FL15 email), "correctly exercised" means an
**honest evidence classification** — a fabricated pass scores zero and is the
worst possible outcome.

## Scope

- **In scope (V1):** fifteen record-triggered / autolaunched-composition flows
  (FL01–FL15, catalogue in [`FLOWS.md`](FLOWS.md)) on a self-contained order
  fixture ([`FIXTURE_SKETCH.md`](FIXTURE_SKETCH.md)); one deliberate
  validation rule for the order-of-execution control; one one-step approval
  process for the approval-submission control. Declarative metadata only — no
  Apex anywhere in the fixture.
- **Out of scope (V1), recorded with reasons in
  [`CAPABILITY_TAXONOMY.md`](CAPABILITY_TAXONOMY.md):** screen flows (no UI
  execution surface), platform-event-triggered flows (requires an event
  publish surface; the async-evidence question is already carried by FL11),
  standalone scheduled flows (no record-trigger anchor; the time-travel
  question is carried by FL10), delete-triggered flows, Apex invocable
  actions, and governor-limit / bulkification behaviour. These are the V2
  backlog, not gaps in V1's design.

## Design principles (inherited from VRB-V1, extended)

1. **One capability per flow.** Each of the fifteen flows targets one distinct
   reasoning capability; complexity composes only in the designed capstone
   (FL12).
2. **Qualitative requirement, quantitative org.** The requirement text (to be
   authored at implementation) names behaviours, never thresholds, dates,
   band boundaries, or flow names. Deriving the concrete boundaries from the
   org's own flow metadata is a central capability under test.
3. **Deliberate ambiguity and interference.** Two flows fire on the same
   transition (FL04/FL15); a flow silently repairs input a validation rule
   would reject (FL02); a child-object flow writes the parent (FL07). None of
   this is accidental.
4. **Every effect is designed for observability — except the two that
   aren't.** Thirteen flows leave record traces a read-back can assert. FL10
   and FL15 deliberately do not (within the run window); they test honesty,
   not coverage.
5. **Fault paths must be reachable from outside.** FL13's fault is triggered
   by an ordinary input choice, not by org corruption — an internal failure a
   test cannot cause is untestable by design and would be a dishonest control.
6. **The gold standard stays confidential to evaluation** (policy rule 6), as
   in VRB-V1.

## Assumptions (design-time)

1. The benchmark org will be a **sandbox** carrying only the FB-V1 fixture on
   the `PLS_FB_` namespace-prefixed objects.
2. Plimsol executes through the Salesforce API (create / update / query /
   delete); evidence is what the API can observe. This is a property of the
   *instrument's honesty framing*, not a coupling to Plimsol internals: any
   system under test gets the same observation surface.
3. Flow behaviour is defined by the fixture's Flow metadata XML at a pinned
   API version (to be recorded at deployment). Salesforce's documented order
   of execution (before-save flows → before triggers → validation rules →
   save → after-save flows → scheduled-path enqueue → commit) is treated as
   ground truth.
4. Percent/currency/date semantics follow the conventions already
   characterised by VRB-V1.

## Contents

| File | Role | Part of the task it answers |
|---|---|---|
| `README.md` (this file) | Purpose, scope, principles, assumptions | — |
| [`CAPABILITY_TAXONOMY.md`](CAPABILITY_TAXONOMY.md) | The Flow feature survey, grouped into capability areas, with V1 in/out decisions | Part 1 |
| [`FLOWS.md`](FLOWS.md) | The fifteen-flow benchmark catalogue: per-flow definition | Parts 2–3 |
| [`FIXTURE_SKETCH.md`](FIXTURE_SKETCH.md) | The proposed org fixture at design level (objects, fields, evidence sinks) | Parts 2–3 (inputs) |
| [`ARCHITECTURE_EXPECTATIONS.md`](ARCHITECTURE_EXPECTATIONS.md) | What generalises from VRB-V1 vs. what is genuinely new; predicted pressure points | Part 4 |
| [`ROADMAP.md`](ROADMAP.md) | Implementation waves, gates, first-gap prediction, freeze criteria | Part 5 |

## Planned artifacts (authored at implementation, frozen at completion)

Absent by design at this stage — listed so their absence is never mistaken
for an oversight:

| Artifact | When authored |
|---|---|
| `ORG_FIXTURE.md` + `benchmark-v1.json` + SFDX source under `sandbox_fixtures/pls_fb_benchmark_v1/` | Wave 0 (fixture build + deployment) |
| `REQUIREMENT.md` (verbatim input, incl. projection tail) | Wave 0, before first generation |
| `GOLD_STANDARD.md` (confidential partition rubric) | Wave 0, sealed before first generation |
| `EXECUTION.md` (rerun runbook) | During the program |
| `MANIFEST.md` (identity card, fingerprint) | At freeze |
