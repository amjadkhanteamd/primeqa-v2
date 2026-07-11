# VRB-V1 — Manifest

The canonical identity card of **Validation Rule Benchmark V1**. This document
answers one question — *what exactly is this benchmark?* — and points at the
documents that answer everything else. It duplicates none of them.

---

## 1. Identity

| Attribute | Value | Derivation |
|---|---|---|
| Name | Validation Rule Benchmark V1 (VRB-V1) | — |
| Version | 1 (spec `benchmark_version: 1.0.0`) | [`benchmark-v1.json`](benchmark-v1.json) |
| Status | **FROZEN** | [`../../BENCHMARK_POLICY.md`](../../BENCHMARK_POLICY.md) |
| Org fixture deployed | 2026-07-08 | [`ORG_FIXTURE.md`](ORG_FIXTURE.md), `benchmark-v1.json` `deployed` |
| Program completed (10/10) | 2026-07-10 | `docs/architecture/DECISIONS_LOG.md` D-360 |
| Architecture baseline | main commit **`4db8a75`** (2026-07-11) — the V1-completion merge; the design record is **D-342 … D-360** (D-350 retired) in `docs/architecture/DECISIONS_LOG.md` | `git log` |
| Frozen at | main commit **`b04edd1`** (2026-07-11) | `git log` |
| Salesforce fixture (SFDX source) | `sandbox_fixtures/pls_benchmark_v1/` (repo root) | — |
| Documentation | `benchmark/validation_rules/v1/` (this directory) | — |
| Original benchmark org | sandbox, alias `primeqa-sandbox` (Plimsol environment "env-59" during the V1 program); any sandbox restored from the SFDX source is equivalent | `benchmark-v1.json` |

## 2. Inventory

| Item | Location | Purpose |
|---|---|---|
| Requirement (verbatim input) | [`REQUIREMENT.md`](REQUIREMENT.md) | The single business-requirement input, byte-exact incl. the projection tail — the determinism anchor |
| Org fixture description | [`ORG_FIXTURE.md`](ORG_FIXTURE.md) | Human-readable org definition: object, fields, record types, rule inventory, deployment record, implementation decisions |
| Machine-readable spec | [`benchmark-v1.json`](benchmark-v1.json) | The same fixture as structured data (drift checks, tooling) |
| SFDX fixture package | `sandbox_fixtures/pls_benchmark_v1/` | The deployable source of the benchmark org — its restore point; the exact rule formulas live here |
| Rule catalogue | [`RULES.md`](RULES.md) | Per-rule: business purpose, Salesforce mechanism, capability tested, requirement path, experiment shape, evidence required |
| Gold standard | [`GOLD_STANDARD.md`](GOLD_STANDARD.md) | Partition-level scoring rubric — **confidential to evaluation, never an input** |
| Architecture map | [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) | Which capability each rule forced into existence; where each generalises |
| Execution guide | [`EXECUTION.md`](EXECUTION.md) | The rerun runbook: prerequisites → org → generation → execution → evidence → pass criteria → failure interpretation |
| Benchmark policy | [`../../BENCHMARK_POLICY.md`](../../BENCHMARK_POLICY.md) | Immutability governance for all benchmarks |
| Scope & assumptions | [`README.md`](README.md) | Purpose, scope, assumptions, recorded limitations, contents |
| Benchmark report | *(none as a separate artifact)* | The V1 program's results are recorded in the D-342…D-360 ledger entries; a standalone `BASELINE`/results file is a recommended future addition (outside this frozen directory) |

## 3. Fingerprint

Observable characteristics that define the instrument. Any rerun should
re-derive these and stop on a mismatch (drift check). All values below were
derived from repository artifacts at freeze time.

| Characteristic | Value | Derived from |
|---|---|---|
| Salesforce objects | **1** (`PLS_BM_Deal__c`) | SFDX `objects/` |
| Custom fields | **12** | SFDX `fields/*.xml` |
| Validation rules | **10** (VR01–VR10), **all active** | SFDX `validationRules/*.xml` (`<active>true</active>` × 10) |
| Record types | **2** (`PLS_BM_Enterprise`, `PLS_BM_Standard`) | SFDX `recordTypes/*.xml` |
| Permission sets | **1** (`PLS_BM_Deal_Access`) | SFDX `permissionsets/*.xml` |
| Requirements | **1** (manual; "Enterprise Deal Approval Requests") | [`REQUIREMENT.md`](REQUIREMENT.md) |
| Acceptance criteria | one prose block of **10 behavioural statements**; the numbered AC decomposition is LLM-derived at generation time and varies slightly per run (typically 10–12 labels) — the *statement count* is the stable figure, the decomposition is **not** a reliable fingerprint | [`REQUIREMENT.md`](REQUIREMENT.md) |
| Benchmark controls | **10** (one per validation rule) | [`RULES.md`](RULES.md) |
| Differential experiments | **2** — RECORD (VR08: record-type control arm) and PRIOR_STATE (VR05: transition-history control arm) | [`RULES.md`](RULES.md) |
| Decision experiments | **1 designed** (VR03, five arms); VR07 additionally receives the same decomposition because its shape matches — emergent, not part of the frozen design | [`RULES.md`](RULES.md) |
| Temporal experiments | **1** (VR06, four arms incl. the adjacent RUN_DATE−1 / RUN_DATE boundary) | [`RULES.md`](RULES.md) |
| Fixture package | `sandbox_fixtures/pls_benchmark_v1/` at commit `b04edd1` | `git log` |

Not reliably derivable (stated per policy): the per-run probe **count** (claim
composition depends on LLM proposal shape within the ≤3-pass protocol) and the
AC decomposition above. Neither is part of the fingerprint.

## 4. Success criteria (summary)

A successful rerun demonstrates — full definitions and the three-number
scoring in [`EXECUTION.md`](EXECUTION.md) §7–8:

- all **10 controls exercised for their intended reason**;
- every rejection **attributed** to its own rule's message (never "some rule
  rejected");
- **persisted-state verification** on acceptance arms (read-back of the staged
  or transitioned value) where the experiment shape requires it;
- both **differential experiments** completed with their single varied
  dimension intact;
- the **decision** and **temporal** experiments completed arm-by-arm;
- zero false tests, zero unattributed rejections;
- and the benchmark itself **unchanged** — fixture, requirement, gold
  standard, and this manifest byte-identical before and after the run.

## 5. Change policy (summary)

Full governance: [`../../BENCHMARK_POLICY.md`](../../BENCHMARK_POLICY.md).

**Allowed** — architecture improvements anywhere in Plimsol; rerunning this
benchmark against them; recording result and score history (in a results
location *outside* this frozen directory).

**Not allowed** — modifying the benchmark org metadata or the SFDX fixture;
changing the requirement text; editing the gold standard; altering pass
criteria or experiment definitions; any silent edit under this directory.

**If the benchmark must evolve, create Benchmark V2.** V1 stays exactly as it
is — still runnable, still comparable.

## 6. Related documents

- [`README.md`](README.md) — purpose, scope, assumptions, limitations
- [`REQUIREMENT.md`](REQUIREMENT.md) — the verbatim input
- [`ORG_FIXTURE.md`](ORG_FIXTURE.md) / [`benchmark-v1.json`](benchmark-v1.json) — the org
- [`RULES.md`](RULES.md) — the ten controls and their experiments
- [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) — what the benchmark bought
- [`EXECUTION.md`](EXECUTION.md) — how to rerun and score
- [`GOLD_STANDARD.md`](GOLD_STANDARD.md) — the rubric (confidential to evaluation)
- [`../../BENCHMARK_POLICY.md`](../../BENCHMARK_POLICY.md) — governance
