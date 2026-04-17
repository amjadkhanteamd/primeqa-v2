-- PrimeQA Migration 012: Risk Scoring Engine
BEGIN;

CREATE TABLE test_case_risk_factors (
    id                   serial PRIMARY KEY,
    tenant_id            integer NOT NULL REFERENCES tenants(id),
    test_case_id         integer NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    business_priority    integer DEFAULT 50,
    last_passed_at       timestamptz,
    last_failed_at       timestamptz,
    flaky_score          float DEFAULT 0.0,
    reference_count      integer DEFAULT 0,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT test_case_risk_factors_unique UNIQUE (test_case_id)
);

CREATE INDEX idx_test_case_risk_factors_tenant ON test_case_risk_factors(tenant_id);

COMMIT;
