"""LLMGateway \u2014 single entry point for every Anthropic call.

Responsibilities (coherent, narrow):
  - Look up the prompt module for this task
  - Ask it for the complexity bucket
  - Ask the router for the model chain
  - Build the spec (prompt module takes care of caching blocks)
  - Invoke the provider (backoff + usage extraction handled there)
  - If the prompt says to escalate AND the chain has a fallback AND
    we haven't already escalated, retry ONCE with the fallback model
  - Record one row in llm_usage_log (always, success or fail)
  - Return LLMResponse with parsed content

Anything NOT in this list (prompt text, model pricing, how to split a
prompt into cache blocks, etc.) is someone else's concern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from primeqa.intelligence.llm import pricing, usage
from primeqa.intelligence.llm.provider import (
    invoke as provider_invoke,
    ProviderError,
    ProviderResponse,
)
from primeqa.intelligence.llm.prompts import get_prompt
from primeqa.intelligence.llm.router import select_chain, TenantPolicy

log = logging.getLogger(__name__)


# ---- Public types ---------------------------------------------------------

@dataclass
class LLMResponse:
    """What call sites actually use."""
    parsed_content: Any
    raw_text: str
    model: str
    prompt_version: str
    task: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    latency_ms: int
    escalated: bool
    status: str
    request_id: Optional[str] = None
    complexity: Optional[str] = None
    # Row id of the llm_usage_log entry this call produced. Callers that
    # only know their generation_batch_id / run_id AFTER the LLM call
    # use this to back-link via usage.attach_batch(...) so the cost
    # dashboard attributes the spend correctly.
    #
    # `usage_log_id` is the LAST (final) attempt's row id \u2014 i.e. the one
    # whose response this LLMResponse carries. On chain traversal with
    # escalation, each attempt's usage row is logged; `usage_log_ids`
    # holds them ALL so the caller can back-link every billable attempt
    # to the batch (not just the final successful one \u2014 a primary that
    # was escalated past STILL costs money and must be attributed).
    usage_log_id: Optional[int] = None
    usage_log_ids: List[int] = field(default_factory=list)


class LLMError(Exception):
    """Raised when the gateway can't satisfy the caller.

    Status codes map 1:1 to ProviderError.status so the UI can pick a
    friendly flash message ("quota_exceeded" \u2192 "Top up credits", etc.)
    """

    def __init__(self, status: str, message: str,
                 request_id: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.request_id = request_id


# ---- Main entry point -----------------------------------------------------

def llm_call(
    *,
    task: str,
    tenant_id: int,
    api_key: str,
    context: Dict[str, Any],
    user_id: Optional[int] = None,
    complexity: Optional[str] = None,
    tenant_policy: Optional[TenantPolicy] = None,
    recent_misses: Optional[List[Dict[str, Any]]] = None,
    # Cross-reference fields (forwarded to llm_usage_log):
    run_id: Optional[int] = None,
    requirement_id: Optional[int] = None,
    test_case_id: Optional[int] = None,
    generation_batch_id: Optional[int] = None,
    model_override: Optional[str] = None,
) -> LLMResponse:
    """Execute a registered LLM task end-to-end.

    Returns a parsed, usage-tracked LLMResponse. Raises LLMError on
    terminal provider failures (auth, content, quota exhaustion after
    retries). Never silently swallows errors.
    """
    prompt = get_prompt(task)
    prompt_version = getattr(prompt, "VERSION", task)

    # ---- Per-tenant rate limits (migration 032) ---------------------------
    # Shared with tool_turn via _enforce_rate_limit so the two entry points
    # can't drift on the rate-limit guarantee (chokepoint-as-shared-internals,
    # D-095.2). A blocked call writes a zero-token llm_usage_log row and
    # raises LLMError("rate_limited").
    tenant_policy = _enforce_rate_limit(
        tenant_id=tenant_id, user_id=user_id, task=task,
        prompt_version=prompt_version, complexity=(complexity or "default"),
        run_id=run_id, requirement_id=requirement_id,
        test_case_id=test_case_id, generation_batch_id=generation_batch_id,
        tenant_policy=tenant_policy,
    )

    # Complexity: caller may override; otherwise the prompt module
    # detects from context. Falls through to "default" for tasks that
    # don't route by complexity.
    if complexity is None and hasattr(prompt, "detect_complexity"):
        complexity = prompt.detect_complexity(context) or "default"
    elif complexity is None:
        complexity = "default"

    # Model chain selection. If caller forced a model, use only that.
    if model_override:
        chain = [model_override]
    else:
        chain = select_chain(task, complexity=complexity,
                             tenant_policy=tenant_policy)

    if not chain:
        raise LLMError("content_error",
                       f"no model available for task={task} complexity={complexity}")

    # Auto-load feedback signals for tasks that benefit. Caller override
    # still wins (pass recent_misses=... to inject specific ones).
    #
    # Phase 7: `recent_misses` was a raw list of signal dicts that the
    # prompt had to format itself. We now aggregate signals into a
    # pre-rendered "Common mistakes to avoid" rules block (see
    # feedback_rules.build_rules_block) and pass that string through.
    # Callers passing a list still work via the prompt's back-compat path.
    if recent_misses is None and task == "test_plan_generation":
        from primeqa.intelligence.llm import feedback_rules
        recent_misses = feedback_rules.build_rules_block(tenant_id) or None

    # Build the prompt spec once — escalation reuses the same spec
    # (same prompt, different model) to keep comparisons clean.
    spec = prompt.build(context, tenant_id=tenant_id,
                        recent_misses=recent_misses)

    escalated = False
    last_error: Optional[LLMError] = None
    # Every attempt's usage_log row id \u2014 success AND error \u2014 collected so
    # the caller can back-attribute all billable attempts to their batch
    # via usage.attach_batch(). Without this, a primary-that-got-escalated-
    # past was invisible in the per-run cost panel.
    attempt_log_ids: List[int] = []

    # Phase 5: Redact obvious PII from outbound prompts before the
    # provider sees them. Safe + fast \u2014 regex-only, preserves structure.
    from primeqa.intelligence.llm import redact
    safe_messages = redact.redact_messages(spec.messages)
    safe_system = (
        redact.redact_messages(
            [{"role": "system", "content": spec.system}]
        )[0]["content"] if spec.system else None
    )

    # Tool use plumbing \u2014 if the prompt declares tools, build the
    # tool_choice parameter to force Anthropic to call it.
    tools_param = spec.tools
    tool_choice_param = None
    if spec.tools and spec.force_tool_name:
        tool_choice_param = {"type": "tool", "name": spec.force_tool_name}

    for attempt, model in enumerate(chain):
        # One provider call + cost + usage row, via the shared internal both
        # llm_call and tool_turn use (D-095.2). Never raises for ProviderError
        # — the result carries .error so this loop owns escalation control.
        inv = _invoke_and_record(
            api_key=api_key, model=model,
            messages=safe_messages, system=safe_system,
            max_tokens=spec.max_tokens, tools=tools_param, tool_choice=tool_choice_param,
            task=task, tenant_id=tenant_id, user_id=user_id,
            prompt_version=prompt_version, complexity=complexity,
            escalated=(attempt > 0),
            run_id=run_id, requirement_id=requirement_id,
            test_case_id=test_case_id, generation_batch_id=generation_batch_id,
            context_for_log=spec.context_for_log,
        )
        if inv.usage_log_id:
            attempt_log_ids.append(inv.usage_log_id)

        if inv.error is not None:
            pe = inv.error
            # For non-retryable terminal statuses, bubble up now.
            if pe.status in ("auth_error", "content_error", "quota_exceeded"):
                raise LLMError(pe.status, pe.message, pe.request_id) from pe
            # Transient: the provider already exhausted its backoff budget;
            # try the next model if we have one.
            last_error = LLMError(pe.status, pe.message, pe.request_id)
            continue

        provider_resp = inv.response

        # Parse the response content
        parsed = spec.parse(provider_resp) if spec.parse else provider_resp.raw_text

        # Decide whether to escalate to the next model in the chain.
        has_more = attempt + 1 < len(chain)
        should_retry = (
            has_more
            and not escalated
            and getattr(prompt, "SUPPORTS_ESCALATION", False)
            and prompt.should_escalate(parsed, provider_resp)
        )
        if should_retry:
            escalated = True
            continue

        return LLMResponse(
            parsed_content=parsed,
            raw_text=provider_resp.raw_text,
            model=provider_resp.model,
            prompt_version=prompt_version,
            task=task,
            cost_usd=inv.cost_usd,
            input_tokens=provider_resp.input_tokens,
            output_tokens=provider_resp.output_tokens,
            cached_input_tokens=provider_resp.cached_input_tokens,
            cache_write_tokens=provider_resp.cache_write_tokens,
            latency_ms=provider_resp.latency_ms,
            escalated=(attempt > 0),
            status="ok",
            request_id=provider_resp.request_id,
            complexity=complexity,
            usage_log_id=inv.usage_log_id,
            usage_log_ids=list(attempt_log_ids),
        )

    # Fell off the end of the chain on transient errors.
    if last_error:
        raise last_error
    raise LLMError("provider_error", "model chain exhausted without a valid response")


def _log_usage_error(**kwargs):
    """Wrapper so every transient-error path writes a usage row without
    duplicating the field list. Returns the inserted row id so callers
    can include it in batch-attribution loops (errors still consume
    input tokens and need to be cost-attributed).
    """
    kwargs.setdefault("status", "provider_error")
    return usage.record(**kwargs)


# ---- Shared cross-cutting internals (chokepoint, D-095.2) -----------------
# llm_call (prompt-module, single-shot-with-escalation) and tool_turn
# (transport-thin, one turn) both go through these helpers, so the two entry
# points cannot drift on rate-limiting, routing, redaction, or cost-logging.

def _enforce_rate_limit(
    *, tenant_id: int, user_id: Optional[int], task: str, prompt_version: str,
    complexity: str, run_id, requirement_id, test_case_id, generation_batch_id,
    tenant_policy: Optional[TenantPolicy],
) -> TenantPolicy:
    """Per-tenant rate-limit gate. Loads tenant config (honoring an explicitly
    passed policy), checks the minute/hour/daily-spend windows, and on block
    writes a zero-token llm_usage_log row + raises LLMError("rate_limited").
    Returns the resolved TenantPolicy for downstream routing."""
    from primeqa.intelligence.llm import limits as _limits
    if tenant_policy is None:
        tenant_limits, tenant_policy = _limits.load_tenant_config(tenant_id)
    else:
        tenant_limits, _ = _limits.load_tenant_config(tenant_id)

    rc = _limits.check(tenant_id, tenant_limits)
    if not rc.allowed:
        usage.record(
            tenant_id=tenant_id, user_id=user_id, task=task,
            model="(blocked)", prompt_version=prompt_version,
            input_tokens=0, output_tokens=0, cost_usd=0.0, latency_ms=0,
            status="rate_limited", complexity=complexity, escalated=False,
            run_id=run_id, requirement_id=requirement_id,
            test_case_id=test_case_id, generation_batch_id=generation_batch_id,
            context={"reason": rc.reason},
        )
        raise LLMError("rate_limited", rc.message or "Tenant rate limit hit")
    return tenant_policy


@dataclass
class _InvokeResult:
    """One provider call's normalized result. .error is set (no raise) on a
    ProviderError so the caller owns the control flow."""
    response: Optional[ProviderResponse]
    cost_usd: float
    usage_log_id: Optional[int]
    error: Optional[ProviderError]


def _invoke_and_record(
    *, api_key: str, model: str, messages, system, max_tokens: int,
    tools, tool_choice, task: str, tenant_id: int, user_id: Optional[int],
    prompt_version: str, complexity: str, escalated: bool,
    run_id, requirement_id, test_case_id, generation_batch_id, context_for_log,
) -> _InvokeResult:
    """One provider call + cost computation + usage.record (the ok row, or the
    error row on ProviderError). Routes by model id. Never raises for
    ProviderError — returns it in .error so llm_call can escalate and tool_turn
    can one-shot. Shared by both (D-095.2)."""
    from primeqa.intelligence.llm.providers import get_provider_for_model
    try:
        provider = get_provider_for_model(model)
        resp = provider.invoke(
            api_key=api_key, model=model, messages=messages, system=system,
            max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
        )
    except ProviderError as pe:
        err_log_id = _log_usage_error(
            tenant_id=tenant_id, user_id=user_id, task=task, model=model,
            prompt_version=prompt_version, complexity=complexity, escalated=escalated,
            status=pe.status, latency_ms=pe.latency_ms, request_id=pe.request_id,
            run_id=run_id, requirement_id=requirement_id,
            test_case_id=test_case_id, generation_batch_id=generation_batch_id,
            context=context_for_log,
        )
        return _InvokeResult(response=None, cost_usd=0.0, usage_log_id=err_log_id, error=pe)

    cost = pricing.compute_cost_usd(
        model=resp.model, input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens, cached_input_tokens=resp.cached_input_tokens,
        cache_write_tokens=resp.cache_write_tokens,
    )
    usage_log_id = usage.record(
        tenant_id=tenant_id, user_id=user_id, task=task, model=resp.model,
        prompt_version=prompt_version, input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens, cached_input_tokens=resp.cached_input_tokens,
        cache_write_tokens=resp.cache_write_tokens, cost_usd=cost,
        latency_ms=resp.latency_ms, status="ok", complexity=complexity,
        escalated=escalated, request_id=resp.request_id,
        run_id=run_id, requirement_id=requirement_id, test_case_id=test_case_id,
        generation_batch_id=generation_batch_id, context=context_for_log,
    )
    return _InvokeResult(response=resp, cost_usd=cost, usage_log_id=usage_log_id, error=None)


# ---- tool_turn — transport-thin single tool-use turn (D-095.2) ------------

@dataclass
class ToolTurnResult:
    """One transport turn's raw result. The S3 spine reads tool_use
    id/name/input from ``content_blocks`` and builds the next tool_result; the
    spine owns the loop. No parsing, no escalation here."""
    content_blocks: List[Any]
    stop_reason: Optional[str]
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    cost_usd: float
    latency_ms: int
    request_id: Optional[str]
    usage_log_id: Optional[int]


def tool_turn(
    *, task: str, tenant_id: int, api_key: str, messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]], max_tokens: int,
    tool_choice: Optional[Dict[str, Any]] = None,
    system: Optional[Any] = None, user_id: Optional[int] = None,
    complexity: Optional[str] = None, tenant_policy: Optional[TenantPolicy] = None,
    model_override: Optional[str] = None,
    run_id: Optional[int] = None, requirement_id: Optional[int] = None,
    test_case_id: Optional[int] = None, generation_batch_id: Optional[int] = None,
    context_for_log: Optional[Dict[str, Any]] = None,
) -> ToolTurnResult:
    """Perform exactly ONE tool-use turn over caller-supplied messages / tools /
    tool_choice (D-095.2). Shares llm_call's cross-cutting internals — rate-limit
    (_enforce_rate_limit), routing (select_chain), PII redaction, and
    cost-logging (_invoke_and_record -> llm_usage_log). NO orchestration: no
    message-history management, no tool_result construction, no loop, no
    escalation. The S3 runtime owns all of that."""
    complexity = complexity or "default"
    tenant_policy = _enforce_rate_limit(
        tenant_id=tenant_id, user_id=user_id, task=task, prompt_version=task,
        complexity=complexity, run_id=run_id, requirement_id=requirement_id,
        test_case_id=test_case_id, generation_batch_id=generation_batch_id,
        tenant_policy=tenant_policy,
    )

    if model_override:
        model = model_override
    else:
        chain = select_chain(task, complexity=complexity, tenant_policy=tenant_policy)
        if not chain:
            raise LLMError("content_error", f"no model available for task={task}")
        model = chain[0]  # one turn — no escalation chain

    from primeqa.intelligence.llm import redact
    safe_messages = redact.redact_messages(messages)
    safe_system = (
        redact.redact_messages([{"role": "system", "content": system}])[0]["content"]
        if system else None
    )

    inv = _invoke_and_record(
        api_key=api_key, model=model, messages=safe_messages, system=safe_system,
        max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
        task=task, tenant_id=tenant_id, user_id=user_id, prompt_version=task,
        complexity=complexity, escalated=False,
        run_id=run_id, requirement_id=requirement_id, test_case_id=test_case_id,
        generation_batch_id=generation_batch_id, context_for_log=context_for_log or {},
    )
    if inv.error is not None:
        raise LLMError(inv.error.status, inv.error.message, inv.error.request_id)

    r = inv.response
    return ToolTurnResult(
        content_blocks=list(r.content or []), stop_reason=r.stop_reason, model=r.model,
        input_tokens=r.input_tokens, output_tokens=r.output_tokens,
        cached_input_tokens=r.cached_input_tokens, cache_write_tokens=r.cache_write_tokens,
        cost_usd=inv.cost_usd, latency_ms=r.latency_ms, request_id=r.request_id,
        usage_log_id=inv.usage_log_id,
    )
