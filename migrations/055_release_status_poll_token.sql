-- Migration 055: per-release opaque polling token for the public
-- /api/releases/<id>/status endpoint.
--
-- Why: that endpoint is intentionally public (CI/CD polls it without an
-- interactive login) but it had NO tenant scoping and NO capability check —
-- a bare integer release_id was the only "credential", so anyone could
-- enumerate ids and read any tenant's release name / pass-fail / GO-NO-GO.
--
-- Fix mirrors the shared-dashboard link idiom (shared_dashboard_links.token):
-- a SHA-256 hash of a random token is stored here; the raw token is handed to
-- CI once at mint time and never persisted. /status now requires ?token= and
-- matches by hash, which both authenticates the poll and proves the tenant
-- (the row's tenant scopes the response). NULL = no token minted yet → the
-- endpoint refuses (404) until a Release Owner mints one.
--
-- Idempotent (016+ convention): ADD COLUMN IF NOT EXISTS.

ALTER TABLE releases
    ADD COLUMN IF NOT EXISTS status_poll_token_hash VARCHAR(64);

COMMENT ON COLUMN releases.status_poll_token_hash IS
    'SHA-256 hex of the public /status polling token. NULL until minted. '
    'Raw token never stored; revoke by setting back to NULL.';
