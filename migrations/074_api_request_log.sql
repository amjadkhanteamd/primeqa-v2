-- Migration 074: the API request log (round 4, part D — AUD-034, the decision
-- memo's ruling: log requests on the /api surface, kept 30 days; prune no route
-- on this evidence yet).
--
--   api_request_log  one row per /api request: when, tenant/user/role (from the
--                    token the gate read), the caller kind (anonymous / cookie /
--                    bearer / webhook), method, the url_map RULE (never the path),
--                    endpoint, status, duration, the rule's declared minimum tier.
--                    NEVER a body, a query string, a header or a token.
--
-- Written by primeqa/shared/request_log.py (a queue flushed every 2 s, one
-- multi-row INSERT); pruned daily by the scheduler (rows older than 30 days).
--
-- CLASSIFICATION (D-476 / D-285): ADDITIVE — a new table, no data write, no
-- constraint on an existing row — dumpless. Idempotent (016+ convention).

CREATE TABLE IF NOT EXISTS api_request_log (
    id           bigserial PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    tenant_id    integer,
    user_id      integer,
    role         varchar(20),
    caller_kind  varchar(12) NOT NULL,
    method       varchar(8)  NOT NULL,
    rule         text        NOT NULL,
    endpoint     text        NOT NULL,
    status       integer     NOT NULL,
    duration_ms  integer     NOT NULL DEFAULT 0,
    min_tier     varchar(12)
);

-- the two reads the memo's question needs: by rule over a window, and the prune
CREATE INDEX IF NOT EXISTS idx_api_request_log_at ON api_request_log (at);
CREATE INDEX IF NOT EXISTS idx_api_request_log_rule_at ON api_request_log (rule, method, at);

COMMENT ON TABLE api_request_log IS
  'AUD-034 (round 4, D-502): every /api request — rule, tier, caller kind, count — kept 30 days so the orphan-route question is answered from evidence, not absence. No bodies, no tokens.';
