-- PrimeQA Migration 009: Release Intelligence Model
-- Releases become first-class entities tying Jira tickets, metadata impacts,
-- test plans, executions, and decisions together.

BEGIN;

-- ============================================================
-- releases — the central entity
-- ============================================================
CREATE TABLE releases (
    id                 serial PRIMARY KEY,
    tenant_id          integer      NOT NULL REFERENCES tenants(id),
    name               varchar(255) NOT NULL,
    version_tag        varchar(100),
    description        text,
    status             varchar(30)  NOT NULL DEFAULT 'planning'
                         CHECK (status IN ('planning', 'in_progress', 'ready', 'decided', 'shipped', 'cancelled')),
    target_date        date,
    decision_criteria  jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_by         integer      NOT NULL REFERENCES users(id),
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT releases_tenant_name_unique UNIQUE (tenant_id, name)
);

CREATE INDEX idx_releases_tenant_status ON releases(tenant_id, status);
CREATE INDEX idx_releases_target_date ON releases(target_date) WHERE status IN ('planning', 'in_progress', 'ready');

-- ============================================================
-- release_requirements — junction: requirements ↔ releases
-- ============================================================
CREATE TABLE release_requirements (
    id             serial PRIMARY KEY,
    release_id     integer     NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    requirement_id integer     NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
    added_by       integer     NOT NULL REFERENCES users(id),
    added_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT release_requirements_unique UNIQUE (release_id, requirement_id)
);

CREATE INDEX idx_release_requirements_release ON release_requirements(release_id);

-- ============================================================
-- release_impacts — which metadata impacts apply to a release
-- ============================================================
CREATE TABLE release_impacts (
    id                    serial PRIMARY KEY,
    release_id            integer      NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    metadata_impact_id    integer      NOT NULL REFERENCES metadata_impacts(id) ON DELETE CASCADE,
    risk_score            integer,
    risk_level            varchar(20)  CHECK (risk_level IS NULL OR risk_level IN ('low', 'medium', 'high', 'critical')),
    risk_reasoning        jsonb,
    created_at            timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT release_impacts_unique UNIQUE (release_id, metadata_impact_id)
);

CREATE INDEX idx_release_impacts_release_risk ON release_impacts(release_id, risk_score DESC);

-- ============================================================
-- release_test_plan_items — ranked test plan entries per release
-- ============================================================
CREATE TABLE release_test_plan_items (
    id                 serial PRIMARY KEY,
    release_id         integer      NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    test_case_id       integer      NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    priority           varchar(20)  NOT NULL DEFAULT 'medium'
                         CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    position           integer      NOT NULL DEFAULT 0,
    risk_score         integer,
    inclusion_reason   varchar(50),
    created_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT release_test_plan_items_unique UNIQUE (release_id, test_case_id)
);

CREATE INDEX idx_release_test_plan_items_release ON release_test_plan_items(release_id, position);

-- ============================================================
-- release_runs — which pipeline_runs were executed for a release
-- ============================================================
CREATE TABLE release_runs (
    id              serial PRIMARY KEY,
    release_id      integer     NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    pipeline_run_id integer     NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    triggered_by    integer     NOT NULL REFERENCES users(id),
    triggered_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT release_runs_unique UNIQUE (release_id, pipeline_run_id)
);

CREATE INDEX idx_release_runs_release ON release_runs(release_id);

-- ============================================================
-- release_decisions — GO/NO-GO recommendations and final decisions
-- ============================================================
CREATE TABLE release_decisions (
    id                serial PRIMARY KEY,
    release_id        integer      NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    recommendation    varchar(20)  NOT NULL
                        CHECK (recommendation IN ('go', 'conditional_go', 'no_go')),
    confidence        float,
    reasoning         jsonb,
    criteria_met      jsonb,
    recommended_by    varchar(20)  NOT NULL DEFAULT 'ai'
                        CHECK (recommended_by IN ('ai', 'human')),
    final_decision    varchar(20)
                        CHECK (final_decision IS NULL OR final_decision IN ('go', 'conditional_go', 'no_go')),
    decided_by        integer      REFERENCES users(id),
    decided_at        timestamptz,
    override_reason   text,
    created_at        timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX idx_release_decisions_release ON release_decisions(release_id, created_at DESC);

COMMIT;
