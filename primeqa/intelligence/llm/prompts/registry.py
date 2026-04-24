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
)


_REGISTRY: Dict[str, object] = {
    "test_plan_generation":   test_plan_generation,
    "failure_summary":        failure_summary,
    "failure_analysis":       failure_analysis,
    "agent_fix":              agent_fix,
    "connection_test":        connection_test,
    # Migration 048 — BA-facing story-view summarisation, Haiku, best-effort
    "story_view_generation":  story_view,
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
