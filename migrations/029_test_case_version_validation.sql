-- PrimeQA Migration 029: validation report on test_case_versions.
--
-- Multi-TC generation made it clear that AI sometimes hallucinates:
-- non-existent field names, wrong object, unresolved $vars, etc. The
-- cost is "generate \u2192 run \u2192 fail with cryptic SF error \u2192 debug".
--
-- This migration stores a structured validation report per version so
-- the UI can surface issues inline right after generation (and again
-- at pre-execution). Report shape:
--
--   {
--     "status": "ok" | "warnings" | "critical",
--     "issues": [
--       {"step_order": 1, "severity": "critical",
--        "rule": "field_not_found",
--        "object": "Account", "field": "Last_Escalation_Date__c",
--        "message": "Field does not exist on Account",
--        "suggestions": ["LastActivityDate", "LastModifiedDate"]}
--     ],
--     "summary": {"critical": 2, "warning": 0, "info": 0}
--   }
--
-- Recomputed by the TestCaseValidator whenever a new version is created
-- or the user clicks Revalidate. Keyed to the meta version used so we
-- can flag "validated against v8 metadata; current is v11 \u2014 revalidate"
-- when metadata drifts.
--
-- Idempotent.

BEGIN;

ALTER TABLE test_case_versions
    ADD COLUMN IF NOT EXISTS validation_report JSONB,
    ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS validated_against_meta_version_id INT
        REFERENCES meta_versions(id) ON DELETE SET NULL;

-- Fast lookup for "show me all TC versions with critical issues in this
-- tenant" style ops queries, once we add a library-wide validation view.
CREATE INDEX IF NOT EXISTS idx_tcv_validation_status
    ON test_case_versions ((validation_report->>'status'))
    WHERE validation_report IS NOT NULL
      AND validation_report->>'status' <> 'ok';

COMMIT;
