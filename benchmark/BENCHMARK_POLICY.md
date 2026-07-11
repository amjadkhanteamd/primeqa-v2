# Benchmark Policy

Benchmarks under `benchmark/` are measurement instruments, not test suites.
Their value is comparability over time: the same instrument, applied to
successive versions of Plimsol, yields scores that can honestly be compared.
That value is destroyed the moment an instrument changes silently.

## The rules

1. **A frozen benchmark is immutable.** Validation Rule Benchmark V1 is
   frozen. Its org metadata (`sandbox_fixtures/pls_benchmark_v1/`), its
   requirement text, its gold standard, and its pass criteria do not change —
   not to accommodate new architecture, not to "clean up", not to make a score
   look better.

2. **Architecture changes are measured AGAINST the benchmark, never the other
   way round.** When new Plimsol capability lands, rerun the frozen benchmark
   and report the new score against the recorded baseline. If the new
   capability cannot handle something the benchmark contains, that is a
   finding, not a defect in the benchmark.

3. **Do not change benchmark metadata after architecture work.** The org
   fixture was deployed once (2026-07-08) and characterised exhaustively
   during the V1 program. Editing a rule, a field, a picklist value, or a
   record type after the fact invalidates every recorded score. If the org
   has drifted, restore it from the SFDX source rather than re-baselining.

4. **If a benchmark must change, create the next version.** New rules, new
   Salesforce mechanisms (Flows, Approval Processes, permissions), corrected
   wording — all of it goes into `validation_rules/v2/` (or a new benchmark
   family) with its own fixture, requirement, gold standard, and baseline.
   V1 stays exactly as it is, still runnable, still comparable.

5. **Never silently edit a frozen benchmark.** Any commit that touches a
   frozen benchmark's directory must say so explicitly in its message and be
   limited to typo-level documentation fixes that cannot alter what the
   benchmark measures. When in doubt: don't — version instead.

6. **The gold standard stays confidential to evaluation.** It is the scoring
   rubric, not an input. It must never be provided to Plimsol as grounding
   material (it pre-disambiguates the very interpretation questions the
   benchmark exists to test).

## Why this exists

The V1 program's central finding was that *apparent* coverage and
*trustworthy* coverage are different numbers, and only a stable instrument
makes their convergence measurable. Between 2026-07-08 and 2026-07-10 the same
frozen fixture measured Plimsol from 1/10 correctly-exercised controls (with
7 of 8 generated tests broken) to 10/10 live-proven and attributed. That
trajectory is only meaningful because the instrument never moved.
