-- Migration 071: Step B — the ledger admits the fourth recommendation value
-- (LLD_STEP_B_STALENESS_PIN §b, ruling F1a; D-479 B).
--
--   release_decisions.recommendation CHECK widens from
--   ('go', 'conditional_go', 'no_go') to include 'cannot_determine' — the
--   engine's RECORDED refusal to grade a release whose staleness cannot be
--   resolved (no connected org bound to its evidence, or an org that never
--   synced). final_decision — the HUMAN's decision — stays three-valued:
--   a human does not "decide" CANNOT_DETERMINE.
--
-- Classification (D-476): constraint widening, no data write, no ORM change
-- (ReleaseDecision carries no CHECK) → ADDITIVE, dumpless, applied BEFORE
-- the deploy: the old code never writes the value; the new code needs the
-- column to accept it. No ORM window in either direction. Idempotent.

ALTER TABLE release_decisions
    DROP CONSTRAINT IF EXISTS release_decisions_recommendation_check;

ALTER TABLE release_decisions
    ADD CONSTRAINT release_decisions_recommendation_check
    CHECK (recommendation IN ('go', 'conditional_go', 'no_go', 'cannot_determine'));

COMMENT ON COLUMN release_decisions.recommendation IS
    'go | conditional_go | no_go | cannot_determine (Step B: the engine refused to grade — staleness unresolvable)';
