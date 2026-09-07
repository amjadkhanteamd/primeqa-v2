-- Migration 072: Step 1 — the requirement ROW carries the identity it
-- decorates (LLD_STEP_1_PROVENANCE §a; Fork 1 ratified).
--
--   requirements.external_key — the identity key, verbatim. Backfilled to
--   COALESCE(jira_key, 'req-' || id): EXACTLY the key
--   s3_enqueue._requirement_to_ref derives today, so NO ROW CHANGES KEY
--   (TA invariant 6). requirements.id stays a row id and is never
--   displayed; jira_key stays Jira's own reference for re-sync and is no
--   longer an identity.
--
-- Uniqueness within tenant scope (TA invariant 2): a partial UNIQUE index
-- over LIVE rows — a soft-deleted row keeps its key without blocking a
-- re-decoration. This also closes the manual-create hole: a typed key
-- colliding with a live row is refused by the database, not merely by the
-- Jira-import path's find_by_jira_key check.
--
-- The CHECK forbids a typed key from spoofing another row's derived
-- namespace ('req-<n>' belongs to row n alone).
--
-- CLASSIFICATION (D-476 / D-285): the UPDATE is a DATA WRITE and the
-- index/CHECK CAN REJECT rows → potentially destructive → DUMP-FIRST,
-- ruled by AK 2026-09-07 regardless of the zero-collision dry-run.
-- Applied BEFORE the deploy: the column is nullable and the old code
-- neither reads nor writes it (no ORM window in either direction); the
-- new code needs it present. Idempotent (016+ convention).

ALTER TABLE requirements
    ADD COLUMN IF NOT EXISTS external_key text;

UPDATE requirements
    SET external_key = COALESCE(jira_key, 'req-' || id)
    WHERE external_key IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_requirements_tenant_external_key
    ON requirements (tenant_id, external_key)
    WHERE deleted_at IS NULL;

ALTER TABLE requirements
    DROP CONSTRAINT IF EXISTS requirements_external_key_namespace;
ALTER TABLE requirements
    ADD CONSTRAINT requirements_external_key_namespace
    CHECK (external_key IS NULL
           OR external_key !~ '^req-[0-9]+$'
           OR external_key = 'req-' || id);

COMMENT ON COLUMN requirements.external_key IS
    'Step 1: the requirement IDENTITY this row decorates (the link key, '
    'verbatim). Backfilled from COALESCE(jira_key, req-<id>) — no row was '
    're-keyed. Never updated after creation; id is a row id, not identity.';
