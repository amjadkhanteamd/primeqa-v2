-- PrimeQA Migration 013: BA Review step comments
BEGIN;

ALTER TABLE ba_reviews ADD COLUMN step_comments jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMIT;
