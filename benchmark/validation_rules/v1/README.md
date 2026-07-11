# Validation Rule Benchmark V1 (VRB-V1) — FROZEN

**Status: FROZEN.** Completed 2026-07-10 at **10/10 correctly-exercised
controls, all with attributed evidence**, against live env-59. This directory
is immutable under [`../../BENCHMARK_POLICY.md`](../../BENCHMARK_POLICY.md);
improvements rerun this benchmark, they never modify it.

## Purpose

Measure whether Plimsol can turn one qualitative business requirement plus a
Salesforce org's own metadata into tests that are **truthful, executable,
isolated, and evidentially strong** — in that order, with raw coverage last.
The instrument is deliberately adversarial: ten validation rules of increasing
logical complexity on one object, each targeting a distinct reasoning
capability, several of them deliberately ambiguous against the requirement
wording (e.g. "Enterprise" names both a business picklist value and a record
type).

The headline metric is **correctly-exercised controls**: a rule counts only
when a live run demonstrates it firing (or admitting) *for the intended
reason*, with the rejection **attributed** to that rule's own error message —
never merely "some rule rejected the save".

## Scope

- **In scope:** the ten validation rules VR01–VR10 on `PLS_BM_Deal__c`
  (inventory in [`ORG_FIXTURE.md`](ORG_FIXTURE.md)); the single requirement in
  [`REQUIREMENT.md`](REQUIREMENT.md); generation, execution, and evidence
  collection against the benchmark org.
- **Out of scope:** Flows, Approval Processes, Apex triggers, permissions,
  sharing, duplicate rules, multi-object graphs. Those are future benchmark
  families (the V1 architecture map records which V1 capabilities they will
  inherit).

## Assumptions

1. The benchmark org (a Salesforce **sandbox**) carries the fixture exactly as
   defined in `sandbox_fixtures/pls_benchmark_v1/` and
   [`benchmark-v1.json`](benchmark-v1.json) — nothing more on the object, all
   ten rules active.
2. The requirement is provided to Plimsol **verbatim** as recorded in
   [`REQUIREMENT.md`](REQUIREMENT.md) (including its field/rule-name
   projection tail). The gold standard is **not** provided as input.
3. The org has been synced into Plimsol's semantic model (S1) before
   generation, and the integration user holds the `PLS_BM_Deal_Access`
   permission set.
4. Runs create and delete records on the benchmark object; the org is a
   sandbox where that is acceptable. Teardown is automatic but best-effort.
5. Percent fields follow Salesforce semantics: formulas see fractions
   (20% = `0.20`), the API sees display numbers (`20`).

## Limitations (honest, recorded at freeze time)

- **Attribution is message-based.** A rejection is attributed by matching the
  rule's error message; two rules sharing identical messages would be
  indistinguishable (none do in this fixture).
- **Temporal runs have a midnight window.** A run that straddles the org's
  midnight between payload materialisation and save can misgrade the VR06
  boundary arms (a sub-minute exposure; rerun if it happens).
- **Generation is LLM-fed and run-to-run variant.** The benchmark measures
  whether the *system* reliably reaches each control; occasional
  proposal-shape variance means a control can require a second generation
  pass (this is itself signal — record it).
- **Evidence records the test-design value.** For symbolic temporal values the
  persisted evidence shows the `$relative_date` symbol; the wire carried the
  materialised ISO date. Recording both is a documented future improvement,
  not a V1 change.
- **One object, one requirement, validation rules only.** By design; see
  Scope.

## Contents

| File | Role |
|---|---|
| [`MANIFEST.md`](MANIFEST.md) | The benchmark's identity card: identity, inventory, fingerprint, success criteria, change policy |
| [`REQUIREMENT.md`](REQUIREMENT.md) | The verbatim input requirement (the determinism anchor) |
| [`ORG_FIXTURE.md`](ORG_FIXTURE.md) | The benchmark org: object, fields, record types, rules, deployment record |
| [`benchmark-v1.json`](benchmark-v1.json) | Machine-readable fixture spec |
| [`RULES.md`](RULES.md) | Per-rule documentation: purpose, mechanism, capability, experiment shape, evidence |
| [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) | Which architectural capability each rule forced into existence, and where it generalises |
| [`GOLD_STANDARD.md`](GOLD_STANDARD.md) | **Confidential to evaluation** — the partition-level scoring rubric; never an input |
| [`EXECUTION.md`](EXECUTION.md) | The rerun runbook: prerequisites → generation → execution → scoring |
| `sandbox_fixtures/pls_benchmark_v1/` (repo root) | The deployable SFDX source of the org fixture |
