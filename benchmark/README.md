# Plimsol Benchmarks

Permanent, reproducible benchmarks that successive versions of Plimsol are
measured against. Each benchmark is a **frozen instrument**: a controlled
Salesforce fixture + a verbatim requirement + a gold standard + an execution
runbook, versioned together so a rerun six months from now measures the same
thing the original run measured.

| Benchmark | Version | Status | Location | Identity |
|---|---|---|---|---|
| Validation Rule Benchmark | V1 | **FROZEN** — complete 2026-07-10 at 10/10 | [`validation_rules/v1/`](validation_rules/v1/) | [`MANIFEST.md`](validation_rules/v1/MANIFEST.md) |

Governance: [`BENCHMARK_POLICY.md`](BENCHMARK_POLICY.md) — read it before
touching anything under this directory. The one-sentence version: **a frozen
benchmark is never edited; a change means a new version.**
