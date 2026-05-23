"""Live eval binding (D-089 slice 2 / D-104) — the real prompt gate.

Binds ``gateway.tool_turn`` (the production path: routing, rate-limit, PII
redaction, cost-logging) as the runtime's ``tool_turn_fn``, pinned to a model
(``model_override``, D-104.6), with ``state.system`` = the active frozen prompt
(the runtime resolves it from the request). The real LLM proposes from each
live-eligible probe's ``requirement_text``; results are adjudicated against the
per-probe semantic envelope — the ontology-coherence gate (D-104).

Periodic, never PR-gating (D-104): needs ``ANTHROPIC_API_KEY`` + the v2-platform
tenant / ``llm_usage_log`` infra the gateway writes to (D-104.6). The gateway
import is lazy so the eval package has no hard v2-platform dependency.
"""
from __future__ import annotations

import os
from typing import Optional

# Default pinned model for the gate (Sonnet-class, D-104.6 — Opus is an optional
# periodic sweep, not the gate). Override via env to the deployed model id.
DEFAULT_LIVE_MODEL = os.environ.get("LIVE_EVAL_MODEL", "claude-sonnet-4-5")
LIVE_TASK = "generation_live_eval"


def api_key_from_env() -> Optional[str]:
    """The live layer's API key (None -> the live suite skips, D-104)."""
    return os.environ.get("ANTHROPIC_API_KEY")


def build_tool_turn_fn(*, tenant_id: int, api_key: str, model: str,
                       task: str = LIVE_TASK, max_tokens: int = 2048):
    """A ``gateway.tool_turn`` closure shaped as the runtime's ``ToolTurnFn``.
    Pins the model (``model_override``, D-104.6). ``ToolTurnResult`` duck-types
    the runtime's expected turn (content_blocks / *_tokens / model / stop_reason
    / latency_ms). The gateway is imported lazily."""
    from primeqa.intelligence.llm import gateway

    def fn(*, messages, tools, tool_choice, system):
        return gateway.tool_turn(
            task=task, tenant_id=tenant_id, api_key=api_key, max_tokens=max_tokens,
            model_override=model, messages=messages, tools=tools,
            tool_choice=tool_choice, system=system,
        )

    return fn


def run_live_corpus(harness, cases, *, tenant_id: int, api_key: str,
                    model: str = DEFAULT_LIVE_MODEL, prompt_version: Optional[str] = None):
    """Run every live-eligible probe through the live binding; returns the list
    of ``scorer.LiveResult``. The gate is: no ``invariant_ok=False`` results
    (auto-fail); drift rows are for human adjudication (D-104.4)."""
    fn = build_tool_turn_fn(tenant_id=tenant_id, api_key=api_key, model=model)
    results = []
    for case in cases:
        if not case.live_eligible:
            continue
        results.append(harness.run_case_live(
            case, tool_turn_fn=fn, prompt_version=prompt_version, model=model))
    return results
