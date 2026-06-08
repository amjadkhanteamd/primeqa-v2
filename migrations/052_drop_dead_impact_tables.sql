-- 052_drop_dead_impact_tables.sql
-- Cutover Step 5a.4 (D-195.3) — the metadata cutover's one irreversible act,
-- scoped down to the two FULLY CODE-DEAD tables.
--
-- The metadata-impact subsystem (the MetadataImpact / ReleaseImpact models +
-- MetadataImpactRepository + the RiskEngine impact-scoring + the /impacts UI +
-- the GO/NO-GO "high-risk impacts" criterion) was removed in code at Step 5a.2
-- (D-195.2). Both tables' WRITERS were retired earlier (D-193: run_impact_analysis
-- + add_impact have zero callers). After 5a.2 no code references either table.
--
-- NOT in scope here (5b-coupled, deferred): meta_versions / meta_objects /
-- meta_fields / meta_validation_rules / meta_flows / meta_triggers /
-- meta_record_types / meta_sync_status. meta_versions is the target of a LIVE
-- NOT-NULL FK (test_case_versions.metadata_version_id, runtime-populated) +
-- environments.current_meta_version_id, so it retires with test_case_versions in
-- Step 5b (D-195.3).
--
-- IRREVERSIBLE. Archive-first (pg_dump release_impacts + metadata_impacts) BEFORE
-- applying. Apply only after the 5a.2 (impact-free) code is deployed.
--
-- Drop order: release_impacts FIRST — it FKs metadata_impacts(id) (009:49) — then
-- metadata_impacts. The tables they REFERENCE (meta_versions / test_cases /
-- releases / users) are UNTOUCHED; only the dead referencing tables drop.
-- Idempotent (IF EXISTS), per the migrations/ 016+ convention.

BEGIN;

DROP TABLE IF EXISTS release_impacts;
DROP TABLE IF EXISTS metadata_impacts;

COMMIT;
