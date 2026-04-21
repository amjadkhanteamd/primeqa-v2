-- Migration 040: users.preferred_landing_page.
--
-- Lets a user override the computed post-login landing page (see
-- primeqa.core.navigation.get_landing_page). When NULL (the default)
-- the computed page wins. When set, the preference is honoured IF the
-- user still has permission for it — otherwise the computed default is
-- used as a safety net.
--
-- Idempotent.

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS preferred_landing_page VARCHAR(50);

COMMENT ON COLUMN users.preferred_landing_page IS
    'Optional override for post-login redirect. NULL -> computed by permissions. '
    'Honoured only if the user still has permission for the target page.';

COMMIT;
