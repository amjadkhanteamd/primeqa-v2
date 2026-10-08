-- Migration 075: forced tool choice as a RECORDED per-model capability (D-504).
--
-- The Claude 5.5 models and Fable 5.1 reject tool_choice {"type":"tool"} /
-- {"type":"any"} with a 400 ("tool_choice: type "tool" and "any" are not
-- supported for this model."). The gateway forces one tool per turn (D-085),
-- so a tenant pinned to such a model failed every generation at the API
-- (2026-10-08, llm_usage_log 1136/1137). The capability is a per-model FACT:
--
--   forced_tool_choice           TRUE  = the model accepts a forced tool choice;
--                                FALSE = it rejects it (the gateway sends
--                                        tool_choice auto + one call per turn +
--                                        a trailing role:system instruction
--                                        naming the tool instead);
--                                NULL  = not yet probed (the gateway refuses
--                                        pre-spend; the picker refuses the pick).
--   forced_tool_choice_probed_at when the fact was recorded.
--
-- Written by primeqa/intelligence/llm/catalog.py: probe_forced_tool_choice —
-- a FREE count_tokens call with a forced tool_choice (200 -> TRUE, the exact
-- 400 text -> FALSE, anything else -> unknown) run at enable time and, for
-- NULL active rows, at refresh time. The built-in CODE models carry their
-- fact in router.FORCED_TOOL_CHOICE_CODE (no row needed).
--
-- ADDITIVE (dumpless). Idempotent.
ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS forced_tool_choice BOOLEAN;
ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS forced_tool_choice_probed_at TIMESTAMPTZ;

COMMENT ON COLUMN llm_models.forced_tool_choice IS
  'D-504: TRUE accepts tool_choice tool/any; FALSE rejects it (gateway shapes auto + trailing system instruction); NULL not yet probed (refused pre-spend). Probed by a free count_tokens call (catalog.probe_forced_tool_choice).';
COMMENT ON COLUMN llm_models.forced_tool_choice_probed_at IS
  'D-504: when forced_tool_choice was recorded.';

-- Seed the four catalog rows that exist at the time of this migration with
-- the fact proven by probe on 2026-10-08 (docs/design/LLD_TOOL_CHOICE_CAPABILITY.md
-- section 2) — only rows that exist, only where the fact is still unknown, so a
-- tenant already pinned to Sonnet 5.5 is driven by instruction from the first
-- deploy instead of failing loud until a Refresh. A later probe may overwrite.
UPDATE llm_models AS m
   SET forced_tool_choice = v.ok,
       forced_tool_choice_probed_at = NOW()
  FROM (VALUES
          ('claude-fable-5',   TRUE),
          ('claude-opus-5',    TRUE),
          ('claude-opus-5-5',  FALSE),
          ('claude-sonnet-5-5', FALSE)
       ) AS v(model_id, ok)
 WHERE m.model_id = v.model_id
   AND m.forced_tool_choice IS NULL;
