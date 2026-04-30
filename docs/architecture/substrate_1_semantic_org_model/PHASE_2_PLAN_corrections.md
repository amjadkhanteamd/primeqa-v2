# PHASE_2_PLAN.md corrections log

Corrections made during implementation that should fold back into a
future plan revision. These are surface-level errata, not architectural
changes.

## §3.3 sync_runs column type

**Date:** 2026-04-30
**Step:** 1C
**Source:** PHASE_2_PLAN.md §3.3 sync_runs CREATE TABLE block

Plan reads:
    logical_version_seq INT REFERENCES logical_versions(version_seq),

Should read:
    logical_version_seq BIGINT REFERENCES logical_versions(version_seq),

**Reason:** logical_versions.version_seq is BIGINT (Phase 0 foundation).
All other references to it in the schema (entities.valid_from_seq,
entities.valid_to_seq, edges.valid_from_seq, edges.valid_to_seq,
change_log.version_seq, logical_versions.parent_version_seq) are BIGINT.
INT would create an incompatible-type FK that Postgres rejects.

The 1C migration uses BIGINT (correct). The plan source still reads INT
(typo). Plan should be updated in a future revision pass; this file
tracks the discrepancy until then.
