-- Migration 039: Permission-Set data model + ownership + release state.
--
-- Foundation for the permission-set authorization architecture documented
-- in CLAUDE.md (## Permission Model). Adds the four new tables that hold
-- the permission-set registry + user assignments + shared dashboard links
-- + notification preferences, then extends environments / test_cases /
-- test_suites / pipeline_runs with the per-row columns that the two-layer
-- (user permissions × env run policies) check needs.
--
-- Column-type note: PrimeQA's existing PKs are INTEGER (SERIAL), not
-- BIGINT/BIGSERIAL. We stick with SERIAL/INTEGER for every new table + FK
-- so the new columns are directly comparable with existing PKs and we
-- don't introduce implicit cross-type casts on joins.
--
-- Name-conflict note: test_cases already has `owner_id` and pipeline_runs
-- already has `triggered_by`, and generation_batches already has
-- `created_by` — all three serve the ownership / triggered-by role the
-- spec introduces as owner_user_id / triggered_by_user_id. We keep the
-- existing names as canonical (documented in the model layer) rather
-- than adding redundant duplicate columns that would drift over time.
-- Only test_suites genuinely needs a new owner column.
--
-- Idempotent — every CREATE / ALTER uses IF NOT EXISTS.
-- Re-runnable — the per-tenant seed uses ON CONFLICT DO NOTHING against
-- (tenant_id, api_name) so a second apply is a no-op.

BEGIN;

-- ============================================================
-- 1. permission_sets
-- ============================================================
CREATE TABLE IF NOT EXISTS permission_sets (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    name            VARCHAR(100) NOT NULL,
    api_name        VARCHAR(100) NOT NULL,
    description     TEXT,
    is_system       BOOLEAN NOT NULL DEFAULT false,
    is_base         BOOLEAN NOT NULL DEFAULT false,
    permissions     JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, api_name)
);

CREATE INDEX IF NOT EXISTS idx_permission_sets_tenant
    ON permission_sets (tenant_id);

CREATE INDEX IF NOT EXISTS idx_permission_sets_tenant_base
    ON permission_sets (tenant_id) WHERE is_base = true;


-- ============================================================
-- 2. user_permission_sets
-- ============================================================
CREATE TABLE IF NOT EXISTS user_permission_sets (
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission_set_id   INTEGER NOT NULL REFERENCES permission_sets(id) ON DELETE CASCADE,
    assigned_by         INTEGER REFERENCES users(id),
    assigned_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, permission_set_id)
);

CREATE INDEX IF NOT EXISTS idx_user_permission_sets_user
    ON user_permission_sets (user_id);


-- ============================================================
-- 3. shared_dashboard_links
-- ============================================================
CREATE TABLE IF NOT EXISTS shared_dashboard_links (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    environment_id  INTEGER REFERENCES environments(id),
    token           VARCHAR(64) UNIQUE NOT NULL,
    created_by      INTEGER REFERENCES users(id),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shared_dashboard_links_tenant_active
    ON shared_dashboard_links (tenant_id)
    WHERE revoked_at IS NULL;


-- ============================================================
-- 4. notification_preferences
-- ============================================================
CREATE TABLE IF NOT EXISTS notification_preferences (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type  VARCHAR(50) NOT NULL,
    channel     VARCHAR(20) NOT NULL DEFAULT 'in_app',
    UNIQUE (user_id, event_type)
);


-- ============================================================
-- 5. environments: run policies + ownership
--    (spec table name "test_environments" -> actual table "environments")
-- ============================================================
ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS allow_single_run      BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS allow_bulk_run        BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS allow_scheduled_run   BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS is_production         BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS require_approval      BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS max_api_calls_per_run INTEGER;

ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS environment_type      VARCHAR(20) NOT NULL DEFAULT 'team';
ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS owner_user_id         INTEGER REFERENCES users(id);
ALTER TABLE environments
    ADD COLUMN IF NOT EXISTS parent_team_env_id    INTEGER REFERENCES environments(id);

-- Backfill is_production from existing env_type='production' rows.
UPDATE environments
    SET is_production = true
    WHERE env_type = 'production' AND is_production = false;

-- Personal-env visibility index: owner's own personal environments.
CREATE INDEX IF NOT EXISTS idx_environments_owner
    ON environments (owner_user_id)
    WHERE environment_type = 'personal';


-- ============================================================
-- 6. Resource ownership
--
-- test_cases already has `owner_id` (NOT NULL). pipeline_runs already
-- has `triggered_by` (NOT NULL). generation_batches already has
-- `created_by` (NOT NULL). These serve the role spec'd as
-- owner_user_id / triggered_by_user_id. See model layer for the
-- synonym that exposes the spec-compliant name.
--
-- Only test_suites genuinely needs a new ownership column.
-- ============================================================
ALTER TABLE test_suites
    ADD COLUMN IF NOT EXISTS owner_user_id INTEGER REFERENCES users(id);

-- Backfill test_suites.owner_user_id from created_by for historical rows.
UPDATE test_suites
    SET owner_user_id = created_by
    WHERE owner_user_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_test_suites_owner
    ON test_suites (owner_user_id)
    WHERE deleted_at IS NULL;


-- ============================================================
-- 7. pipeline_runs: release state
-- ============================================================
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS release_status   VARCHAR(20);
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS approved_by      INTEGER REFERENCES users(id);
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS approved_at      TIMESTAMPTZ;
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS override_reason  TEXT;

-- Constraint: release_status ∈ { NULL, 'PENDING', 'APPROVED', 'OVERRIDDEN' }
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pipeline_runs_release_status_check'
    ) THEN
        ALTER TABLE pipeline_runs
            ADD CONSTRAINT pipeline_runs_release_status_check
            CHECK (release_status IS NULL
                OR release_status IN ('PENDING', 'APPROVED', 'OVERRIDDEN'));
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_release_status
    ON pipeline_runs (tenant_id, release_status)
    WHERE release_status IS NOT NULL;


-- ============================================================
-- 8. users.role: mark deprecated (kept for back-compat)
-- ============================================================
COMMENT ON COLUMN users.role IS
    'DEPRECATED: use permission_sets instead. Remove after all routes migrated.';


-- ============================================================
-- 9. Seed system Permission Sets for every existing tenant.
--
-- The five Base sets + one granular set per unique permission string.
-- ON CONFLICT (tenant_id, api_name) DO NOTHING keeps this idempotent.
-- New tenants will be seeded via
--     primeqa.core.permissions.seed_permission_sets_for_tenant()
-- which must be called from the tenant-creation path.
-- ============================================================

-- Developer Base
INSERT INTO permission_sets (tenant_id, name, api_name, description, is_system, is_base, permissions)
SELECT id,
       'Developer Base',
       'developer_base',
       'Self-test individual Jira tickets against scratch org or team sandbox.',
       true, true,
       '["connect_personal_org","run_single_ticket","view_own_results","view_own_diagnosis","rerun_own_ticket"]'::jsonb
FROM tenants
ON CONFLICT (tenant_id, api_name) DO NOTHING;

-- Tester Base
INSERT INTO permission_sets (tenant_id, name, api_name, description, is_system, is_base, permissions)
SELECT id,
       'Tester Base',
       'tester_base',
       'Sprint-level testing, test case review, suite management.',
       true, true,
       '["connect_personal_org","run_single_ticket","view_own_results","view_own_diagnosis","rerun_own_ticket","run_sprint","run_suite","view_all_results","view_all_diagnosis","view_intelligence_report","review_test_cases","manage_test_suites","view_test_library","view_coverage_map","trigger_metadata_sync","view_knowledge_attribution"]'::jsonb
FROM tenants
ON CONFLICT (tenant_id, api_name) DO NOTHING;

-- Release Owner Base
INSERT INTO permission_sets (tenant_id, name, api_name, description, is_system, is_base, permissions)
SELECT id,
       'Release Owner Base',
       'release_owner_base',
       'Release readiness assessment and stakeholder communication.',
       true, true,
       '["view_dashboard","view_suite_quality_gates","view_all_results_summary","view_intelligence_summary","view_trends","share_dashboard","revoke_shared_links","approve_release"]'::jsonb
FROM tenants
ON CONFLICT (tenant_id, api_name) DO NOTHING;

-- Admin Base
INSERT INTO permission_sets (tenant_id, name, api_name, description, is_system, is_base, permissions)
SELECT id,
       'Admin Base',
       'admin_base',
       'Platform configuration, user management, knowledge curation. Includes all Tester and Release Owner permissions.',
       true, true,
       '["connect_personal_org","run_single_ticket","view_own_results","view_own_diagnosis","rerun_own_ticket","run_sprint","run_suite","view_all_results","view_all_diagnosis","view_intelligence_report","review_test_cases","manage_test_suites","view_test_library","view_coverage_map","trigger_metadata_sync","view_knowledge_attribution","view_dashboard","view_suite_quality_gates","view_all_results_summary","view_intelligence_summary","view_trends","share_dashboard","revoke_shared_links","approve_release","manage_environments","manage_jira_connections","manage_sf_connections","manage_ai_models","manage_users","manage_permission_sets","manage_knowledge","manage_skills","view_audit_log","view_api_usage","configure_scheduled_runs","manage_rate_limits","override_quality_gate","view_all_personal_environments","delete_any_personal_environment"]'::jsonb
FROM tenants
ON CONFLICT (tenant_id, api_name) DO NOTHING;

-- API Access
INSERT INTO permission_sets (tenant_id, name, api_name, description, is_system, is_base, permissions)
SELECT id,
       'API Access',
       'api_access',
       'CI/CD pipelines, headless execution. Token-based authentication.',
       true, true,
       '["api_authenticate","run_single_ticket","run_suite","view_all_results","trigger_metadata_sync","webhook_notifications"]'::jsonb
FROM tenants
ON CONFLICT (tenant_id, api_name) DO NOTHING;

-- Granular permission sets — one row per unique permission string across
-- all five base sets. Generated dynamically from the Admin Base (which is
-- the superset minus `api_authenticate` and `webhook_notifications`) UNION
-- the API Access set.
--
-- We inline the exact list here rather than computing it across joins to
-- keep the seed idempotent + diff-reviewable.
INSERT INTO permission_sets (tenant_id, name, api_name, description, is_system, is_base, permissions)
SELECT t.id, g.name, g.api_name, g.description, true, false, to_jsonb(array[g.api_name]::text[])
FROM tenants t
CROSS JOIN (VALUES
    ('connect_personal_org',             'Connect Personal Org',             'Connect a personal Salesforce org for individual testing.'),
    ('run_single_ticket',                'Run Single Ticket',                'Trigger a run scoped to a single Jira ticket.'),
    ('view_own_results',                 'View Own Results',                 'View pipeline-run results you triggered.'),
    ('view_own_diagnosis',               'View Own Diagnosis',               'View AI failure diagnosis on your own runs.'),
    ('rerun_own_ticket',                 'Rerun Own Ticket',                 'Rerun a previous single-ticket run you triggered.'),
    ('run_sprint',                       'Run Sprint',                       'Trigger a sprint-scoped test run.'),
    ('run_suite',                        'Run Suite',                        'Trigger a suite test run.'),
    ('view_all_results',                 'View All Results',                 'View pipeline-run results from any user.'),
    ('view_all_diagnosis',               'View All Diagnosis',               'View AI failure diagnosis across all runs.'),
    ('view_intelligence_report',         'View Intelligence Report',         'View detailed risk + coverage intelligence reports.'),
    ('review_test_cases',                'Review Test Cases',                'Review AI-generated test cases in the BA queue.'),
    ('manage_test_suites',               'Manage Test Suites',               'Create, edit, and delete test suites.'),
    ('view_test_library',                'View Test Library',                'Browse the shared test case library.'),
    ('view_coverage_map',                'View Coverage Map',                'View the coverage map across metadata objects.'),
    ('trigger_metadata_sync',            'Trigger Metadata Sync',            'Kick off a Salesforce metadata refresh.'),
    ('view_knowledge_attribution',       'View Knowledge Attribution',       'See which knowledge rules influenced an AI output.'),
    ('view_dashboard',                   'View Dashboard',                   'View the release intelligence dashboard.'),
    ('view_suite_quality_gates',         'View Suite Quality Gates',         'View suite-level GO/NO-GO quality gates.'),
    ('view_all_results_summary',         'View All Results Summary',         'View cross-run aggregate result summaries.'),
    ('view_intelligence_summary',        'View Intelligence Summary',        'View the top-line intelligence summary.'),
    ('view_trends',                      'View Trends',                      'View quality + cost trend charts over time.'),
    ('share_dashboard',                  'Share Dashboard',                  'Create tokenised shared dashboard links.'),
    ('revoke_shared_links',              'Revoke Shared Links',              'Revoke previously issued shared dashboard links.'),
    ('approve_release',                  'Approve Release',                  'Approve a release candidate (PENDING -> APPROVED).'),
    ('manage_environments',              'Manage Environments',              'Create, edit, delete test environments.'),
    ('manage_jira_connections',          'Manage Jira Connections',          'Create, edit, delete Jira connections.'),
    ('manage_sf_connections',            'Manage Salesforce Connections',    'Create, edit, delete Salesforce connections.'),
    ('manage_ai_models',                 'Manage AI Models',                 'Configure LLM routing + model overrides.'),
    ('manage_users',                     'Manage Users',                     'Create, edit, deactivate user accounts.'),
    ('manage_permission_sets',           'Manage Permission Sets',           'Create, edit, assign permission sets.'),
    ('manage_knowledge',                 'Manage Knowledge',                 'Curate the knowledge base used by AI prompts.'),
    ('manage_skills',                    'Manage Skills',                    'Curate the skills registry.'),
    ('view_audit_log',                   'View Audit Log',                   'View the tenant activity / audit log.'),
    ('view_api_usage',                   'View API Usage',                   'View API + LLM usage dashboards.'),
    ('configure_scheduled_runs',         'Configure Scheduled Runs',         'Create, edit, delete scheduled runs.'),
    ('manage_rate_limits',               'Manage Rate Limits',               'Edit tenant-level LLM rate limits + tiers.'),
    ('override_quality_gate',            'Override Quality Gate',            'Override a failing quality gate (OVERRIDDEN state).'),
    ('view_all_personal_environments',   'View All Personal Environments',   'View personal environments owned by any user.'),
    ('delete_any_personal_environment',  'Delete Any Personal Environment',  'Delete personal environments owned by any user.'),
    ('api_authenticate',                 'API Authenticate',                 'Authenticate via programmatic API token.'),
    ('webhook_notifications',            'Webhook Notifications',            'Receive webhook notifications.')
) AS g(api_name, name, description)
ON CONFLICT (tenant_id, api_name) DO NOTHING;


-- ============================================================
-- 10. Assign default Permission Sets to existing users.
--
--    admin    -> admin_base
--    superadmin -> admin_base (god-mode also gets admin set as fallback)
--    ba       -> tester_base
--    viewer   -> release_owner_base
--    tester   -> developer_base ("all others" per spec)
--
-- Idempotent via INSERT ... ON CONFLICT DO NOTHING on the
-- (user_id, permission_set_id) PK.
-- ============================================================

INSERT INTO user_permission_sets (user_id, permission_set_id, assigned_by, assigned_at)
SELECT u.id,
       ps.id,
       NULL,
       NOW()
FROM users u
JOIN permission_sets ps
    ON ps.tenant_id = u.tenant_id
   AND ps.api_name = CASE
        WHEN u.role IN ('admin', 'superadmin') THEN 'admin_base'
        WHEN u.role = 'ba'                     THEN 'tester_base'
        WHEN u.role = 'viewer'                 THEN 'release_owner_base'
        ELSE 'developer_base'
   END
ON CONFLICT (user_id, permission_set_id) DO NOTHING;


COMMIT;
