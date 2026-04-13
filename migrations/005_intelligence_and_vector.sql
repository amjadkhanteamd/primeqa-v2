-- PrimeQA Migration 005: Intelligence Layer + Vector Store
-- Covers spec sections 5.1 through 5.5 and 6.1.
-- Tables: entity_dependencies, explanation_requests, failure_patterns,
--         behaviour_facts, step_causal_links, embeddings

BEGIN;

-- pgvector must exist before we declare the embedding column.
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 5.1 entity_dependencies
-- ============================================================
CREATE TABLE entity_dependencies (
    id                serial PRIMARY KEY,
    meta_version_id   integer           NOT NULL REFERENCES meta_versions(id) ON DELETE CASCADE,
    source_entity     varchar(255)      NOT NULL,
    source_type       varchar(30)       NOT NULL
                        CHECK (source_type IN ('flow', 'trigger', 'validation_rule', 'process_builder', 'workflow_rule')),
    target_entity     varchar(255)      NOT NULL,
    dependency_type   varchar(20)       NOT NULL
                        CHECK (dependency_type IN ('creates', 'updates', 'reads', 'deletes', 'validates')),
    discovery_source  varchar(20)       NOT NULL DEFAULT 'metadata_parse'
                        CHECK (discovery_source IN ('metadata_parse', 'execution_trace', 'inferred', 'manual')),
    confidence        double precision  NOT NULL DEFAULT 1.0
                        CHECK (confidence BETWEEN 0.0 AND 1.0)
);

CREATE INDEX idx_entity_deps_version_source
    ON entity_dependencies(meta_version_id, source_entity);
CREATE INDEX idx_entity_deps_version_target
    ON entity_dependencies(meta_version_id, target_entity);
CREATE INDEX idx_entity_deps_type
    ON entity_dependencies(meta_version_id, dependency_type);

-- ============================================================
-- 5.2 explanation_requests
-- ============================================================
CREATE TABLE explanation_requests (
    id                   serial PRIMARY KEY,
    run_test_result_id   integer      NOT NULL REFERENCES run_test_results(id) ON DELETE CASCADE,
    run_step_result_id   integer      REFERENCES run_step_results(id) ON DELETE CASCADE,
    explanation_type     varchar(30)  NOT NULL
                           CHECK (explanation_type IN ('failure_analysis', 'root_cause', 'impact_assessment', 'anomaly_detection')),
    structured_input     jsonb        NOT NULL,
    llm_response         jsonb,
    parsed_explanation   jsonb,
    model_used           varchar(50),
    prompt_tokens        integer,
    completion_tokens    integer,
    requested_at         timestamptz  NOT NULL DEFAULT now(),
    completed_at         timestamptz
);

CREATE INDEX idx_explanation_requests_test ON explanation_requests(run_test_result_id);
CREATE INDEX idx_explanation_requests_step ON explanation_requests(run_step_result_id);
CREATE INDEX idx_explanation_requests_type
    ON explanation_requests(explanation_type, requested_at DESC);

-- ============================================================
-- 5.3 failure_patterns
-- ============================================================
CREATE TABLE failure_patterns (
    id                        serial PRIMARY KEY,
    tenant_id                 integer           NOT NULL REFERENCES tenants(id),
    environment_id            integer           REFERENCES environments(id),
    pattern_signature         varchar(64)       NOT NULL,
    failure_type              varchar(30)       NOT NULL,
    root_entity               varchar(255),
    description               text,
    occurrence_count          integer           NOT NULL DEFAULT 1,
    confidence                double precision  NOT NULL DEFAULT 1.0
                                CHECK (confidence BETWEEN 0.0 AND 1.0),
    affected_test_case_ids    jsonb             NOT NULL DEFAULT '[]'::jsonb,
    status                    varchar(20)       NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'decayed', 'resolved')),
    first_seen                timestamptz       NOT NULL DEFAULT now(),
    last_seen                 timestamptz       NOT NULL DEFAULT now(),
    last_validated_at         timestamptz       NOT NULL DEFAULT now(),
    CONSTRAINT failure_patterns_tenant_signature_unique
        UNIQUE (tenant_id, environment_id, pattern_signature)
);

CREATE INDEX idx_failure_patterns_tenant_active
    ON failure_patterns(tenant_id) WHERE status = 'active';
CREATE INDEX idx_failure_patterns_lookup
    ON failure_patterns(pattern_signature) WHERE status = 'active';
CREATE INDEX idx_failure_patterns_decay
    ON failure_patterns(last_validated_at) WHERE status = 'active';

-- ============================================================
-- 5.4 behaviour_facts
-- ============================================================
CREATE TABLE behaviour_facts (
    id                 serial PRIMARY KEY,
    tenant_id          integer           NOT NULL REFERENCES tenants(id),
    environment_id     integer           NOT NULL REFERENCES environments(id),
    entity_ref         varchar(255)      NOT NULL,
    fact_type          varchar(30)       NOT NULL
                         CHECK (fact_type IN ('constraint', 'default', 'side_effect', 'sequence', 'dependency')),
    fact_description   text              NOT NULL,
    source             varchar(20)       NOT NULL
                         CHECK (source IN ('seeded', 'learned', 'ba_feedback', 'execution_trace')),
    confidence         double precision  NOT NULL DEFAULT 1.0
                         CHECK (confidence BETWEEN 0.0 AND 1.0),
    is_active          boolean           NOT NULL DEFAULT true,
    learned_at         timestamptz       NOT NULL DEFAULT now()
);

CREATE INDEX idx_behaviour_facts_entity
    ON behaviour_facts(tenant_id, environment_id, entity_ref)
    WHERE is_active = true;
CREATE INDEX idx_behaviour_facts_type
    ON behaviour_facts(tenant_id, environment_id, fact_type)
    WHERE is_active = true;

-- ============================================================
-- 5.5 step_causal_links
-- ============================================================
CREATE TABLE step_causal_links (
    id                   serial PRIMARY KEY,
    run_test_result_id   integer           NOT NULL REFERENCES run_test_results(id) ON DELETE CASCADE,
    from_step_result_id  integer           NOT NULL REFERENCES run_step_results(id) ON DELETE CASCADE,
    to_step_result_id    integer           NOT NULL REFERENCES run_step_results(id) ON DELETE CASCADE,
    link_type            varchar(30)       NOT NULL
                           CHECK (link_type IN ('data_dependency', 'trigger_cascade', 'validation_block', 'state_mutation', 'cleanup_dependency')),
    reason               text,
    confidence           double precision  NOT NULL DEFAULT 1.0
                           CHECK (confidence BETWEEN 0.0 AND 1.0),
    discovery_source     varchar(20)       NOT NULL
                           CHECK (discovery_source IN ('execution_trace', 'metadata_analysis', 'llm_inferred')),
    created_at           timestamptz       NOT NULL DEFAULT now(),
    CONSTRAINT step_causal_links_no_self CHECK (from_step_result_id <> to_step_result_id)
);

CREATE INDEX idx_step_causal_links_test ON step_causal_links(run_test_result_id);
CREATE INDEX idx_step_causal_links_from ON step_causal_links(from_step_result_id);
CREATE INDEX idx_step_causal_links_to ON step_causal_links(to_step_result_id);

-- ============================================================
-- 6.1 embeddings (vector store)
-- ============================================================
CREATE TABLE embeddings (
    id              serial PRIMARY KEY,
    tenant_id       integer       NOT NULL REFERENCES tenants(id),
    environment_id  integer       REFERENCES environments(id),
    content_type    varchar(30)   NOT NULL
                      CHECK (content_type IN ('jira_description', 'jira_comment', 'confluence_doc', 'bug_report', 'ba_feedback')),
    source_id       varchar(255)  NOT NULL,
    content_text    text          NOT NULL,
    embedding       vector(1024)  NOT NULL,
    created_at      timestamptz   NOT NULL DEFAULT now()
);

-- Tenant+environment filter index (every similarity search must scope by these).
CREATE INDEX idx_embeddings_tenant_env ON embeddings(tenant_id, environment_id);
CREATE INDEX idx_embeddings_source ON embeddings(tenant_id, content_type, source_id);

-- pgvector IVFFlat index for cosine similarity search.
CREATE INDEX idx_embeddings_vector
    ON embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

COMMIT;
