-- PrimeQA Migration 008: Add Jira and LLM connection references to environments

BEGIN;

ALTER TABLE environments ADD COLUMN jira_connection_id integer REFERENCES connections(id);
ALTER TABLE environments ADD COLUMN llm_connection_id integer REFERENCES connections(id);

COMMIT;
