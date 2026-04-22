-- Migration 047: user_recent_tickets.
--
-- Powers the /run "Tickets" picker's "Recent tickets" list. A write
-- happens whenever a user views a requirement detail page, runs a
-- single ticket, or selects a ticket in any picker.
--
-- Scoped to (user_id, environment_id) — a user on a different env
-- shouldn't see tickets from another Jira. Last 20 per
-- (user, environment) are kept; the write path prunes older rows.
--
-- Idempotent. No FK on jira_key (it lives upstream in Jira and we
-- don't require a local requirement row to exist — e.g. the user
-- might paste a key before importing the requirement).

BEGIN;

CREATE TABLE IF NOT EXISTS user_recent_tickets (
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    environment_id   INTEGER NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    jira_key         VARCHAR(50) NOT NULL,
    jira_summary     TEXT,
    viewed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, environment_id, jira_key)
);

CREATE INDEX IF NOT EXISTS idx_recent_tickets_viewed
    ON user_recent_tickets (user_id, environment_id, viewed_at DESC);

COMMIT;
