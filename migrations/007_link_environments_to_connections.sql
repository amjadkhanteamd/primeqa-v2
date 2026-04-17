-- PrimeQA Migration 007: Link environments to connections
-- Environments now reference a Salesforce connection instead of storing credentials independently.

BEGIN;

ALTER TABLE environments ADD COLUMN connection_id integer REFERENCES connections(id);

-- Create index for looking up environments by connection
CREATE INDEX idx_environments_connection ON environments(connection_id) WHERE connection_id IS NOT NULL;

COMMIT;
