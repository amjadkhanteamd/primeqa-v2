-- Migration 041: users.preferred_environment_id.
--
-- Lets a Developer (or any user with access to multiple environments) pin
-- their active org so the Run button + ticket list always target the
-- right place. Updated from the "Active Org" switcher on /tickets.
--
-- The FK uses ON DELETE SET NULL so deleting / disconnecting the env
-- doesn't orphan the user — they just fall back to the computed default
-- (most recent personal env, else team env).
--
-- Idempotent.

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS preferred_environment_id INTEGER
        REFERENCES environments(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_users_preferred_environment
    ON users (preferred_environment_id)
    WHERE preferred_environment_id IS NOT NULL;

COMMENT ON COLUMN users.preferred_environment_id IS
    'Last env the user selected in the Active Org switcher. NULL -> '
    'resolver picks most recent personal env, else a team env.';

COMMIT;
