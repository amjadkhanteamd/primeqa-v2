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

from typing import Any, Optional

# D-313.1: the propose turn on a large multi-AC requirement (e.g. req-302, 10 ACs)
# needs ~4200 output tokens to emit acceptance_criteria + ~22 intent_descriptors; at
# 2048 the model was TRUNCATED mid-proposal (acceptance_criteria only) and Layer A
# refused it. This is a ceiling — normal-size requirements finish well under it. A
# per-complexity budget is the deferred [[model-efficiency-relook]] design task.
DEFAULT_MAX_TOKENS = 8192


def build_tool_turn_fn(*, tenant_id: int, api_key: str, model: str, task: str,
                       max_tokens: int = DEFAULT_MAX_TOKENS,
                       user_id: Optional[int] = None,
                       environment_id: Optional[int] = None,
                       request_id: Optional[Any] = None,
                       requirement_key: Optional[str] = None):
    """A ``gateway.tool_turn`` closure shaped as the runtime's ``ToolTurnFn``,
    pinning ``model`` via ``model_override`` (D-104.6 / D-106.2) and tagging the
    call with ``task`` for usage accounting. The returned ``ToolTurnResult``
    duck-types the turn the runtime expects (``content_blocks`` / ``input_tokens``
    / ``output_tokens`` / ``model`` / ``stop_reason`` / ``latency_ms``). The
    gateway is imported lazily.

    Cross-reference attribution: the gateway has always accepted ``user_id`` and
    ``context_for_log`` and forwarded both to ``llm_usage_log``; this binding
    simply never passed them, so every generation row carried cost and cache
    counters with NO way back to the run that spent them (``llm_usage_log`` has
    the cost, ``llm_calls`` has the outcome FK, and nothing joined the two).
    ``request_id`` is the S3 request uuid — the join key into
    ``generation_outcomes.request_id`` — and rides ``context`` because
    ``llm_usage_log`` has no column for it (adding one would be a migration).
    ``environment_id`` rides ``context`` for the same reason; it is the per-org
    attribution D-286 already scopes the batch's S1 reads by.

    Deliberately NOT threaded: ``run_id`` / ``test_case_id`` /
    ``generation_batch_id`` point at v1 product tables dropped in migration 053,
    and ``requirement_id`` is an integer FK to ``public.requirements`` that the
    substrate's text ``requirement_key`` does not reliably resolve to. The key
    itself is recorded in ``context`` instead."""
    from primeqa.intelligence.llm import gateway

    # Constant for the whole batch — built once, not per turn.
    context_for_log = {k: v for k, v in (
        ("s3_request_id", str(request_id) if request_id is not None else None),
        ("environment_id", environment_id),
        ("requirement_key", requirement_key),
    ) if v is not None}

    def fn(*, messages, tools, tool_choice, system):
        return gateway.tool_turn(
            task=task, tenant_id=tenant_id, api_key=api_key, max_tokens=max_tokens,
            model_override=model, messages=messages, tools=tools,
            tool_choice=tool_choice, system=system,
            user_id=user_id, context_for_log=context_for_log,
        )

    return fn
