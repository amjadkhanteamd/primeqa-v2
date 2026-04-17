-- PrimeQA Migration 022: Agent fix-and-rerun audit table.
--
-- Every triage / proposed-fix / auto-apply / rerun / user-decision lands
-- here as one row. Forms the training corpus for the next-gen agent and
-- is the ledger for the Agent fixes tab on the run detail page.

BEGIN;

CREATE TABLE IF NOT EXISTS agent_fix_attempts (
    id                   SERIAL       PRIMARY KEY,
    run_id               INTEGER      NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    test_case_id         INTEGER      NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    run_test_result_id   INTEGER      REFERENCES run_test_results(id) ON DELETE SET NULL,
    run_step_result_id   INTEGER      REFERENCES run_step_results(id) ON DELETE SET NULL,
    failure_class        VARCHAR(40),
    pattern_id           INTEGER      REFERENCES failure_patterns(id) ON DELETE SET NULL,
    root_cause_summary   TEXT,
    confidence           NUMERIC(4,3),
    trust_band           VARCHAR(10),          -- 'high' | 'medium' | 'low'
    proposed_fix_type    VARCHAR(40),          -- 'edit_step' | 'regenerate_test' | 'update_template' | 'retry' | 'quarantine' | 'review'
    before_state         JSONB,                -- full snapshot for revert (Q8)
    after_state          JSONB,                -- for diff display
    auto_applied         BOOLEAN      NOT NULL DEFAULT false,
    rerun_run_id         INTEGER      REFERENCES pipeline_runs(id) ON DELETE SET NULL,
    rerun_outcome        VARCHAR(20),          -- 'passed' | 'failed' | 'pending'
    user_decision        VARCHAR(20),          -- 'accepted' | 'reverted' | 'edited' | NULL
    decided_at           TIMESTAMPTZ,
    decided_by           INTEGER      REFERENCES users(id),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT agent_fix_attempts_trust_band_ck
      CHECK (trust_band IS NULL OR trust_band IN ('high','medium','low')),
    CONSTRAINT agent_fix_attempts_user_decision_ck
      CHECK (user_decision IS NULL OR user_decision IN ('accepted','reverted','edited'))
);

CREATE INDEX IF NOT EXISTS idx_afa_run ON agent_fix_attempts(run_id);
CREATE INDEX IF NOT EXISTS idx_afa_tc  ON agent_fix_attempts(test_case_id);
CREATE INDEX IF NOT EXISTS idx_afa_created ON agent_fix_attempts(created_at DESC);

COMMIT;
