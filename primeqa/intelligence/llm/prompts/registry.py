"""Central registry \u2014 `get(task)` returns the module for that task.

Keeps import cost explicit: adding a prompt is adding a file plus one
line below. No auto-scanning; makes breakage visible at import time.
"""

from __future__ import annotations

from typing import Dict, List

from primeqa.intelligence.llm.prompts import (
    test_plan_generation,
    failure_summary,
    failure_analysis,
    agent_fix,
    connection_test,
    story_view,
    interpretation_phrasing,
    entity_summary_flow,
    entity_summary_validation_rule,
    grounded_answer,
    repair_proposal,
    readable_body_phrasing,
    readable_run_phrasing,
)


_REGISTRY: Dict[str, object] = {
    "test_plan_generation":   test_plan_generation,
    "failure_summary":        failure_summary,
    "failure_analysis":       failure_analysis,
    "agent_fix":              agent_fix,
    "connection_test":        connection_test,
    # Migration 048 — BA-facing story-view summarisation, Haiku, best-effort
    "story_view_generation":  story_view,
    # D-117 — S6 interpretation phrasing (deterministic Interpretation -> QA
    # prose), Haiku, best-effort, invent-nothing. interpretation/ stays LLM-free.
    "interpretation_phrasing_generation":  interpretation_phrasing,
    # §23 enrichment worker — per-entity-type plain-English summaries,
    # Haiku, best-effort. Summary scope is Flow + ValidationRule for v1.
    "entity_summary_flow":             entity_summary_flow,
    "entity_summary_validation_rule":  entity_summary_validation_rule,
    # D-163.3 — S7 grounded answering: phrase a bounded substrate-evidence block,
    # Haiku, invent-nothing. conversation/ stays LLM-free (invoked via the bridge).
    "grounded_answer_generation":      grounded_answer,
    # D-236 (theme #6) — the auto-fix agent's LLM layer: propose a recipe_edit
    # for the recipe-owner failure classes. tool_use, Sonnet->Opus, best-effort.
    "repair_proposal":                 repair_proposal,
    # Readable-body phrasing — restate the deterministic Stage-1 readable-body
    # skeleton into BA/QA prose. Haiku, best-effort, invent-nothing; a
    # grounding validator rejects any ungrounded output → Stage-1 fallback.
    "readable_body_phrasing_generation":  readable_body_phrasing,
    # Readable-run phrasing — restate the deterministic readable-run skeleton
    # (what one completed run did + its recorded result) into QA prose. Haiku,
    # best-effort, invent-nothing, never re-judges the recorded outcome; the
    # same grounding validator rejects ungrounded output → deterministic fallback.
    "readable_run_phrasing_generation":   readable_run_phrasing,
}


def get(task: str):
    """Return the prompt module for this task. Raises KeyError on unknown."""
    module = _REGISTRY.get(task)
    if module is None:
        raise KeyError(
            f"unknown task '{task}'. Registered: "
            + ", ".join(sorted(_REGISTRY.keys()))
        )
    return module


def all_tasks() -> List[str]:
    return sorted(_REGISTRY.keys())
