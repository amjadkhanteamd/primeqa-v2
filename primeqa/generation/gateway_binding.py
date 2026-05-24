"""Shared gateway -> runtime transport binding (D-104.6 / D-106.1).

One place that builds the ``gateway.tool_turn`` closure the
:class:`GenerationRuntime` consumes as its ``ToolTurnFn``. Two callers (D-106.1):

  - the **live eval** (``eval/live.py``) PINS a model — a Sonnet-class gate;
  - the **production runner** (``run.py``) ROUTES a model per batch
    (``route_model``, D-106.2).

Both pass the resolved model as ``model_override`` (D-104.6 / D-106.2): the
gateway keeps its cross-cutting internals (rate-limit, PII redaction,
cost-logging to ``llm_usage_log``) but substrate-3 owns orchestration — no
escalation chain, one model per turn. The gateway is imported lazily so importing
this module carries no hard v2-platform dependency (the eval package stays
importable without the gateway present).
"""
from __future__ import annotations

DEFAULT_MAX_TOKENS = 2048


def build_tool_turn_fn(*, tenant_id: int, api_key: str, model: str, task: str,
                       max_tokens: int = DEFAULT_MAX_TOKENS):
    """A ``gateway.tool_turn`` closure shaped as the runtime's ``ToolTurnFn``,
    pinning ``model`` via ``model_override`` (D-104.6 / D-106.2) and tagging the
    call with ``task`` for usage accounting. The returned ``ToolTurnResult``
    duck-types the turn the runtime expects (``content_blocks`` / ``input_tokens``
    / ``output_tokens`` / ``model`` / ``stop_reason`` / ``latency_ms``). The
    gateway is imported lazily."""
    from primeqa.intelligence.llm import gateway

    def fn(*, messages, tools, tool_choice, system):
        return gateway.tool_turn(
            task=task, tenant_id=tenant_id, api_key=api_key, max_tokens=max_tokens,
            model_override=model, messages=messages, tools=tools,
            tool_choice=tool_choice, system=system,
        )

    return fn
