-- Migration 042: ba_reviews.review_reason.
--
-- Captures why a test case version was flagged for human review so the
-- BA can prioritise the queue. Values the pipeline sets today:
--   'new_generation'         — freshly generated, never reviewed
--   'regenerated_after_fail' — replaced a previous version that failed
--   'regenerated_knowledge'  — knowledge rules changed; regenerated
--   'linter_modified'        — linter applied fixes and flagged for review
--   'low_confidence'         — generator confidence below threshold
--
-- Nullable because historical reviews pre-date this column. The UI
-- falls back to a sensible default ("New generation") when null.
--
-- Idempotent.

BEGIN;

ALTER TABLE ba_reviews
    ADD COLUMN IF NOT EXISTS review_reason VARCHAR(40);

COMMENT ON COLUMN ba_reviews.review_reason IS
    'Why this version was flagged for review. See migration 042 header '
    'for values.';

COMMIT;
